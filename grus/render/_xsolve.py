"""The x-solve: every cell's x from one lexicographic linear program (docs/design/layout-v2.md).

Given the ordering, x is chosen by five objectives in strict priority, each minimised among the optima of the
ones before it:

0. **descent lands on its bar** — for every sibship, how far the parents' midpoint lies outside the children's
   span (a lone child's span is its own x). Zero whenever the order allows: a drop beside its children meets its sib
   bar only by extending it, and the extension (or a lone child's jog at bar height) can run into the neighbouring
   family's bar and read as one sibship with two sets of parents. Minimised, not required: a cross-lineage partner
   who must stand beside its mate pays the least the order leaves.
1. **centring** — the sum over drawn sibships of ``|parents' midpoint - children's centroid|`` (a lone parent's
   position stands for the midpoint), plus a stretch cost on each couple that is not a rigid block (a half-sibling
   hinge's): cheap within ``sib_gap`` of the couple's minimum gap, steep beyond it. A hinge therefore spreads up to
   ``sib_gap`` to centre its families and no further; past that, a descent drops off-centre to an extended sib bar
   rather than a couple line stretching across the figure. The slopes bracket the rate at which spreading buys
   centring (moving one partner by ``d`` moves the midpoint by ``d / 2``): below it within the allowance, above it
   beyond, so the trade is decided by the allowance, not by a weight;
2. **balance** — the largest excess gap (a gap's width beyond its minimum separation), so slack spreads evenly
   instead of piling onto one couple or sibship;
3. **compactness** — the total excess gap;
4. **tie-break** — a fixed weighted sum of positions, pulling cells left, so the optimum is a single point. The
   optimum after level 3 can be a face, and the tie-break settles it only if no edge direction of that face is
   orthogonal to the weights. Those directions are small integer combinations of cells (``(+1, -1, -1, +1)`` on
   four cells, say), so weights with any linear structure fail on some of them: equal steps, and equally residues
   ``k * a mod p``, whose steps take only two values, so ``w[k+1] - w[k] == w[k+3] - w[k+2]`` for most ``k``
   (seen as a Z3/HiGHS disagreement on a 26-person pedigree). The weights are hashes of the cell's rank, 32-bit
   and independent, so a given small direction is orthogonal to them with probability about ``2**-32``.

Subject to the row separations (each adjacent pair at least its gap apart; the order never changes), the rigid
blocks (each consecutive pair in a block at exactly its separation), and every cell at ``x >= 0``. The objectives
before the tie-break depend only on differences of positions; the tie-break's pull left against ``x >= 0`` then fixes
the translation of every part of the drawing, including a part with no descent link to the rest (families on
disjoint generations), which a single pinned cell would leave unbounded.

Why an exact program, not relaxation sweeps: centring alone leaves directions it cannot see. A half-sibling hinge's
married-in spouse and the family descended through that couple can slide together (spouse ``2t``, family ``t``)
without moving any child relative to its parents, and iterative sweeps drifted along that direction until their
pass cap. Stating compactness as an objective below centring removes the freedom by construction, and the result no
longer depends on an iteration budget.

Two backends solve the same model. ``Z3`` (the default) optimises over exact rationals, so positions are bit-identical
on every platform — what the byte-compared goldens rest on. ``HIGHS`` (the optional ``highs`` extra) solves the five
objectives as successive floating-point linear programs, each earlier optimum held as a constraint; it agrees with
Z3 up to solver tolerance.

Both backends solve the levels one at a time, each earlier optimum held as a constraint on the next. For Z3 this is
several times faster than its built-in lexicographic mode on the same model (about 3 s against 20 s for a 267-person
pedigree), and gives the same optimum: a later level is minimised over every layout optimal at the earlier ones.
"""

from __future__ import annotations

import dataclasses
import enum
import fractions
import hashlib
from collections.abc import Sequence


class XSolver(enum.Enum):
    """Which linear-programming backend solves the x model."""

    Z3 = "z3"  # exact rational arithmetic; bit-identical positions on every platform (default)
    HIGHS = "highs"  # floating point; needs the optional ``highs`` extra (highspy)


@dataclasses.dataclass(frozen=True)
class Model:
    """The x model over cells ``0..n-1`` (layout cell indices; unused indices are free and ignored).

    Attributes:
        cells: the drawn cells, row-major left to right — also the order the tie-break weights follow.
        gaps: ``(left, right, sep)`` for every adjacent pair on a row: ``x[right] - x[left] >= sep``.
        rigid: ``(left, right, d)`` pairs fixed at ``x[right] - x[left] == d`` (inside a contiguity block).
        sibships: ``(parents, children)`` per drawn descent group.
        couples: ``(left, right, gap, allowance)`` per non-rigid couple: its minimum ``gap`` and the stretch
            beyond it that is cheap (``sib_gap - couple_gap``).
    """

    cells: tuple[int, ...]
    gaps: tuple[tuple[int, int, float], ...]
    rigid: tuple[tuple[int, int, float], ...]
    sibships: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]
    couples: tuple[tuple[int, int, float, float], ...] = ()


# Couple stretch cost per unit: within the allowance, and the extra beyond it (total slope 1). Spreading moves the
# midpoint at half the rate, so centring pays 1/2 per unit of stretch: 1/5 < 1/2 < 1 brackets it.
_STRETCH_SLOPE = fractions.Fraction(1, 5)
_OVERSTRETCH_EXTRA = fractions.Fraction(4, 5)


def solve(model: Model, backend: XSolver) -> dict[int, float]:
    """Each cell's x under ``model``; raises ``RuntimeError`` if the backend reports no optimum."""
    if not model.cells:
        return {}
    if backend is XSolver.Z3:
        return _solve_z3(model)
    if backend is XSolver.HIGHS:
        return _solve_highs(model)
    raise ValueError(f"unknown x solver {backend!r}")


_TIE_SCALE = 2**32  # the weights lie in [_TIE_SCALE, 2 * _TIE_SCALE)


def _tie_weights(model: Model) -> list[int]:
    """Distinct-with-high-probability positive integer weights along ``cells``, with no linear structure.

    Each is a hash of the cell's rank in ``cells`` (see the module docstring), so the weights are fixed per model
    shape and identical on every platform.
    """
    return [
        _TIE_SCALE + int.from_bytes(hashlib.blake2b(k.to_bytes(8, "little"), digest_size=4).digest(), "little")
        for k in range(len(model.cells))
    ]


def _q(v: float) -> fractions.Fraction:
    return fractions.Fraction(v)  # exact: a separation is a binary float, and converts without rounding


def _solve_z3(model: Model) -> dict[int, float]:
    import z3

    # A private context per solve: z3's default context is process-global and not thread-safe, so two layouts
    # solving at once in one process would otherwise mix their terms ("context mismatch").
    ctx = z3.Context()
    x = {c: z3.Real(f"x{c}", ctx) for c in model.cells}

    def val(f: fractions.Fraction | int) -> z3.ArithRef:
        f = fractions.Fraction(f)
        return z3.RealVal(f"{f.numerator}/{f.denominator}", ctx)

    cons = [x[c] >= 0 for c in model.cells]
    cons += [x[right] - x[left] >= val(_q(sep)) for left, right, sep in model.gaps]
    cons += [x[right] - x[left] == val(_q(d)) for left, right, d in model.rigid]
    outside, devs = [], []
    for s_, (parents, children) in enumerate(model.sibships):
        mid = z3.Sum([x[p] for p in parents]) / len(parents)
        centroid = z3.Sum([x[c] for c in children]) / len(children)
        o, dev = z3.Real(f"out{s_}", ctx), z3.Real(f"dev{s_}", ctx)
        cons += [o >= 0, o >= x[children[0]] - mid, o >= mid - x[children[-1]]]
        cons += [dev >= mid - centroid, dev >= centroid - mid]
        outside.append(o)
        devs.append(dev)
    stretch = []
    for q, (left, right, gap, allowance) in enumerate(model.couples):
        over = z3.Real(f"over{q}", ctx)
        spread = x[right] - x[left] - val(_q(gap))
        cons += [over >= 0, over >= spread - val(_q(allowance))]
        stretch.append(z3.Sum([val(_STRETCH_SLOPE) * spread, val(_OVERSTRETCH_EXTRA) * over]))
    excess = [x[right] - x[left] - val(_q(sep)) for left, right, sep in model.gaps]
    worst = z3.Real("worst", ctx)
    cons += [worst >= e for e in excess]
    zero = z3.RealVal(0, ctx)
    objectives = [
        z3.Sum(outside) if outside else zero,
        z3.Sum(devs + stretch) if devs or stretch else zero,
        worst if excess else zero,
        z3.Sum(excess) if excess else zero,
        z3.Sum([val(w) * x[c] for w, c in zip(_tie_weights(model), model.cells, strict=True)]),
    ]
    held = []
    m: z3.ModelRef | None = None
    for k, objective in enumerate(objectives):
        opt = z3.Optimize(ctx=ctx)
        opt.add(*cons, *held)
        handle = opt.minimize(objective)
        if opt.check() != z3.sat:
            raise RuntimeError(f"x model objective {k} has no optimum (z3: {opt.check()})")
        best = opt.lower(handle)
        if not (z3.is_rational_value(best) or z3.is_int_value(best)):  # else infinite (unbounded)
            raise RuntimeError(f"x model objective {k} is unbounded ({best})")
        held.append(objective == best)
        m = opt.model()
    assert m is not None  # objectives is never empty
    out: dict[int, float] = {}
    for c in model.cells:
        r = m.eval(x[c], model_completion=True)
        out[c] = float(fractions.Fraction(r.numerator_as_long(), r.denominator_as_long()))  # type: ignore[attr-defined]
    return out


def _solve_highs(model: Model) -> dict[int, float]:
    try:
        import highspy
        import numpy as np
    except ImportError as e:  # pragma: no cover - depends on the optional extra
        raise RuntimeError("XSolver.HIGHS needs the optional 'highs' extra: pip install 'grus[highs]'") from e

    inf = highspy.kHighsInf
    col = {c: k for k, c in enumerate(model.cells)}
    n = len(model.cells)
    ndev = len(model.sibships)
    nover = len(model.couples)
    spans = list(range(len(model.sibships)))
    nout = len(spans)
    worst = n + ndev + nover + nout
    ncols = worst + 1
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    for option in ("primal_feasibility_tolerance", "dual_feasibility_tolerance"):
        h.setOptionValue(option, 1e-10)
    lower = np.full(ncols, -inf)
    upper = np.full(ncols, inf)
    lower[:] = 0.0  # positions (the tie-break's pull left settles every part against 0), deviations, excesses
    h.addVars(ncols, lower, upper)

    def row(lo: float, hi: float, terms: Sequence[tuple[int, float]]) -> None:
        idx = np.array([k for k, _ in terms], dtype=np.int32)
        coef = np.array([v for _, v in terms], dtype=np.float64)
        h.addRow(lo, hi, len(terms), idx, coef)

    for left, right, sep in model.gaps:
        row(sep, inf, [(col[right], 1.0), (col[left], -1.0)])
        row(-sep, inf, [(worst, 1.0), (col[right], -1.0), (col[left], 1.0)])  # worst >= x_r - x_l - sep
    for left, right, d in model.rigid:
        row(d, d, [(col[right], 1.0), (col[left], -1.0)])
    for s, (parents, children) in enumerate(model.sibships):
        dev = n + s
        terms: dict[int, float] = {}
        for p in parents:
            terms[col[p]] = terms.get(col[p], 0.0) + 1.0 / len(parents)
        for c in children:
            terms[col[c]] = terms.get(col[c], 0.0) - 1.0 / len(children)
        diff = list(terms.items())  # mid - centroid
        row(0.0, inf, [(dev, 1.0), *[(k, -v) for k, v in diff]])  # dev >= mid - centroid
        row(0.0, inf, [(dev, 1.0), *[(k, v) for k, v in diff]])  # dev >= centroid - mid

    out_cost: dict[int, float] = {}
    for q, s_ in enumerate(spans):
        parents, children = model.sibships[s_]
        o = n + ndev + nover + q
        mid = [(col[p], 1.0 / len(parents)) for p in parents]
        row(0.0, inf, [(o, 1.0), (col[children[0]], -1.0), *mid])  # o >= x_first - mid
        row(0.0, inf, [(o, 1.0), (col[children[-1]], 1.0), *[(k, -v) for k, v in mid]])  # o >= mid - x_last
        out_cost[o] = 1.0
    stretch_cost: dict[int, float] = {}
    for q, (left, right, gap, allowance) in enumerate(model.couples):
        over = n + ndev + q
        row(-gap - allowance, inf, [(over, 1.0), (col[right], -1.0), (col[left], 1.0)])  # over >= x_r - x_l - gap - a
        stretch_cost[col[right]] = stretch_cost.get(col[right], 0.0) + float(_STRETCH_SLOPE)
        stretch_cost[col[left]] = stretch_cost.get(col[left], 0.0) - float(_STRETCH_SLOPE)
        stretch_cost[over] = float(_OVERSTRETCH_EXTRA)
    gap_cost: dict[int, float] = {}
    for left, right, _sep in model.gaps:
        gap_cost[col[right]] = gap_cost.get(col[right], 0.0) + 1.0
        gap_cost[col[left]] = gap_cost.get(col[left], 0.0) - 1.0
    objectives: list[dict[int, float]] = [
        out_cost,
        {**{n + s: 1.0 for s in range(ndev)}, **stretch_cost},  # (the stretch's constant -gap is dropped)
        {worst: 1.0},
        gap_cost,  # sum of gaps; the separations are constant, so this orders like the total excess
        {col[c]: w / _TIE_SCALE for w, c in zip(_tie_weights(model), model.cells, strict=True)},
    ]
    for k, obj in enumerate(objectives):
        cost = np.zeros(ncols)
        for i, v in obj.items():
            cost[i] = v
        h.changeColsCost(ncols, np.arange(ncols, dtype=np.int32), cost)
        h.run()
        if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
            raise RuntimeError(f"x model objective {k} not optimal ({h.modelStatusToString(h.getModelStatus())})")
        if k + 1 < len(objectives) and obj:
            best = h.getInfo().objective_function_value
            row(-inf, best + 1e-10 * (1.0 + abs(best)), list(obj.items()))  # hold this optimum for the next
    values = h.getSolution().col_value
    return {c: float(values[col[c]]) for c in model.cells}
