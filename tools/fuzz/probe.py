"""Lay out and draw one pedigree in a worker, and measure what a renderer change could move.

Runs in a worker from ``tools.fuzz.workers``, so ``grus`` here is the tree under test, while this module is the
current tooling. Declarations reach the tree's ``grus`` only when a pedigree has some — ``conditions`` when it has
condition entries, ``loci`` when it has genotypes — so a tree from before either, whose calls do not take it, still
probes every pedigree without them (every generated one); it cannot read an IR file that has them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import random
import time
from typing import Any

from grus import ir, render
from grus.models import pedigree_pb2 as pb
from tools.fuzz import corpus

_EPS = 1e-6


@dataclasses.dataclass(frozen=True)
class Drawn:
    """Measures of a drawn pedigree.

    Attributes:
        svg_sha1: digest of the SVG bytes.
        width: layout x extent, in layout units.
        centring_error: the largest distance between a couple's midpoint and the mean x of its children.
        off_bar: descents whose parents' midpoint lies outside the children's x-span, so the drop misses the bar.
        off_bar_meeting: those of ``off_bar`` whose drop-to-bar span overlaps another sibship's on the same row.
    """

    svg_sha1: str
    width: float
    centring_error: float
    off_bar: int
    off_bar_meeting: int


@dataclasses.dataclass(frozen=True)
class Result:
    """One pedigree's outcome in one tree: exactly one of ``drawn`` and ``deferred`` is set.

    Attributes:
        key: the case key (``tools.fuzz.corpus``).
        seconds: wall time of the layout, not the drawing.
        deferred: the ``DeferredFeatureError`` message.
    """

    key: str
    seconds: float
    drawn: Drawn | None
    deferred: str | None


def geometry(highs: bool) -> render.Geometry:
    if highs:
        return dataclasses.replace(render.DEFAULT_GEOMETRY, x_solver=render.XSolver.HIGHS)
    return render.DEFAULT_GEOMETRY


# The tree's grus calls, passing declarations only when there are some (see the module doc).


def _passed(declarations: pb.PedigreeSet) -> dict[str, Any]:
    """The declaration keywords to pass the tree's ``grus``: each kind only when there are some."""
    out: dict[str, Any] = {}
    if declarations.conditions:
        out["conditions"] = list(declarations.conditions)
    if declarations.loci:
        out["loci"] = list(declarations.loci)
    return out


def _validate(p: pb.Pedigree, declarations: pb.PedigreeSet) -> None:
    ir.validate(p, **_passed(declarations))


def _layout(p: pb.Pedigree, geom: render.Geometry, declarations: pb.PedigreeSet) -> render.Layout:
    return render.layout(p, geom, **_passed(declarations))


def _svg(p: pb.Pedigree, geom: render.Geometry, declarations: pb.PedigreeSet) -> str:
    return render.render_svg(p, geom, **_passed(declarations))


def measure(case: corpus.Case, highs: bool) -> Result:
    """Lay out and draw ``case``'s pedigree (a validation failure propagates).

    The drawing lays out again rather than drawing from a stored layout, so the result does not rest on the layout
    store, and so a tree from before it can be measured.
    """
    p, declarations = case.load()
    geom = geometry(highs)
    t = time.perf_counter()
    try:
        lay = _layout(p, geom, declarations)
    except render.DeferredFeatureError as e:
        return Result(case.key, time.perf_counter() - t, None, str(e))
    seconds = time.perf_counter() - t
    return Result(case.key, seconds, _drawn(p, lay, _svg(p, geom, declarations)), None)


def outcome_of(data: bytes, highs: bool, declarations: bytes = b"") -> str:
    """The serialized pedigree's SVG digest if it draws, else ``defer: <reason>``, or ``invalid: <error>``.

    ``declarations`` is a serialized ``PedigreeSet`` carrying only the declarations (``conditions``, ``loci``) the
    pedigree references; empty when it references none.
    """
    p = pb.Pedigree.FromString(data)
    defs = pb.PedigreeSet.FromString(declarations)
    try:
        _validate(p, defs)
    except (ir.ValidationError, ir.IntegrityError) as e:
        return f"invalid: {e}"
    try:
        return hashlib.sha1(_svg(p, geometry(highs), defs).encode()).hexdigest()
    except render.DeferredFeatureError as e:
        return f"defer: {e}"


def shuffled(p: pb.Pedigree, seed: int) -> pb.Pedigree:
    """``p`` with its individuals and matings in a seeded random order; offspring keep theirs, which is birth order."""
    rng = random.Random(seed)
    inds = list(p.individuals)
    mats = list(p.matings)
    rng.shuffle(inds)
    rng.shuffle(mats)
    q = pb.Pedigree()
    q.CopyFrom(p)
    del q.individuals[:]
    del q.matings[:]
    q.individuals.extend(inds)
    q.matings.extend(mats)
    return q


def shuffle_outcomes(
    p: pb.Pedigree, shuffles: int, highs: bool, declarations: pb.PedigreeSet | None = None
) -> list[str]:
    """The SVG digest, or ``defer``, of ``p`` and of ``shuffled(p, s)`` for each ``s`` below ``shuffles``."""
    geom = geometry(highs)
    out = []
    for q in [p] + [shuffled(p, s) for s in range(shuffles)]:
        try:
            out.append(hashlib.sha1(_svg(q, geom, declarations or pb.PedigreeSet()).encode()).hexdigest())
        except render.DeferredFeatureError:
            out.append("defer")
    return out


def shuffle_outcomes_of(data: bytes, shuffles: int, highs: bool, declarations: bytes = b"") -> list[str]:
    """``shuffle_outcomes`` of a serialized pedigree; ``["invalid"]`` if it is not valid IR.

    ``declarations`` is as for ``outcome_of``.
    """
    p = pb.Pedigree.FromString(data)
    defs = pb.PedigreeSet.FromString(declarations)
    try:
        _validate(p, defs)
    except (ir.ValidationError, ir.IntegrityError):
        return ["invalid"]
    return shuffle_outcomes(p, shuffles, highs, defs)


def shuffle_case(case: corpus.Case, shuffles: int, highs: bool) -> tuple[str, list[str]]:
    """``shuffle_outcomes`` of ``case``'s pedigree, with its key."""
    p, declarations = case.load()
    return case.key, shuffle_outcomes(p, shuffles, highs, declarations)


def _drawn(p: pb.Pedigree, lay: render.Layout, svg: str) -> Drawn:
    at = {i: lay.pos[lv][k] for lv, row in enumerate(lay.nid) for k, i in enumerate(row)}
    idx = {(i.generation, i.index): j for j, i in enumerate(p.individuals)}

    def x(pos: pb.Position) -> float:
        return at[idx[(pos.generation, pos.index)]]

    err = 0.0
    spans: list[tuple[int, float, float, bool]] = []  # (children's generation, lo, hi, off the bar)
    for m in p.matings:
        if not m.offspring or not m.HasField("partner_a"):
            continue
        parents = [m.partner_a] + ([m.partner_b] if m.HasField("partner_b") else [])
        mid = sum(x(q) for q in parents) / len(parents)
        kids = [x(o.child) for o in m.offspring]
        err = max(err, abs(mid - sum(kids) / len(kids)))
        off = not (min(kids) - _EPS <= mid <= max(kids) + _EPS)
        spans.append((m.offspring[0].child.generation, min([*kids, mid]), max([*kids, mid]), off))
    off_bar = sum(s[3] for s in spans)
    meeting = sum(1 for i, a in enumerate(spans) if a[3] and any(_overlap(a, b) for b in spans[:i] + spans[i + 1 :]))
    xs = [v for row in lay.pos for v in row]
    return Drawn(
        svg_sha1=hashlib.sha1(svg.encode()).hexdigest(),
        width=round(max(xs) - min(xs), 6) if xs else 0.0,
        centring_error=round(err, 6),
        off_bar=off_bar,
        off_bar_meeting=meeting,
    )


def _overlap(a: tuple[int, float, float, bool], b: tuple[int, float, float, bool]) -> bool:
    return a[0] == b[0] and b[1] <= a[2] + _EPS and a[1] <= b[2] + _EPS
