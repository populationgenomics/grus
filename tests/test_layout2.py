"""Tests for the v2 constraint layout (``grus.render.layout``, implemented in ``_layout2`` + ``_ordering``).

These cover the properties specific to the v2 model, beyond the golden bytes and generic invariants in
``test_render`` (which now exercise ``layout`` directly, v2 being the only engine):

* **Ordering** — tier-1 goldens and the boundary-bridge cross-joins order with zero weighted crossings.
* **Determinism** — bit-identical run-to-run, and **shuffle-invariant**: the drawn arrangement (by stable
  ``(generation, index)`` identity) does not depend on the input individuals'/matings' order.
* **Routing** — a >2-mate individual routes its overflow mating as an orthogonal edge clear of every symbol.
* **Cohesion / blocks** — ordinary couples stay tight (never torn past ``sib_gap``) while a half-sib hinge
  spreads to centre over both sibships; contiguity blocks never split (a corpus width bound + the c19
  founder-sibship regression lock).
* **No deferral** — the whole review set draws (some avuncular joins via the ghost).
* **Quantisation** — the final ``pos`` lands on a fixed decimal grid, so the byte-compared goldens are stable
  across arch/compiler (docs/plans/28 Stage D).
"""

from __future__ import annotations

import itertools
import pathlib
import random
import re

import pytest
import test_render

from grus import ir, render
from grus.models import pedigree_pb2 as pb
from grus.render import _layout as _layout_mod
from grus.render import _layout2 as _layout2_mod
from grus.render import _ordering as _ordering_mod

_EPS = 1e-9
_POS_QUANTUM = _layout2_mod._POS_QUANTUM


def _drawable() -> list[str]:
    """Golden names the layout draws (all of them under v2; kept as a filter so a future deferral is skipped)."""
    names: list[str] = []
    for name in test_render._NAMES:
        try:
            render.layout(test_render._load(name))
        except render.DeferredFeatureError:
            continue
        names.append(name)
    return names


_DRAWABLE = _drawable()

# The 20-figure review set is CPG-internal (deploy/ is not in the public tree); its regression cases skip
# when it is absent so the public renderer tests stay green on the goldens alone.
_PAIRS = pathlib.Path(__file__).parent.parent / "deploy" / "review-app" / "review-set" / "pairs"
_HAS_REVIEW_SET = _PAIRS.is_dir()
_REVIEW_SET = sorted(d.name for d in _PAIRS.iterdir() if d.is_dir()) if _HAS_REVIEW_SET else []

# Tier-1 goldens the slice names: V2 ordering must lay them out with zero crossings.
_TIER1 = ["sibship", "three_generation", "half_sibs", "twins", "consanguineous", "founder_sibship"]
# The two boundary-bridge cross-joins the ordering must lay out natively (no v1 placement pass).
_CROSS_JOINS = ["c03", "c16"]


def test_some_goldens_are_drawable() -> None:
    assert _DRAWABLE, "no drawable goldens to exercise the layout against"


def _load_pair(name: str) -> pb.Pedigree:
    """Load a review-set figure's first pedigree (the pairs are ``PedigreeSet``s, unlike the bare goldens)."""
    if not _HAS_REVIEW_SET:
        pytest.skip("review set (CPG-internal) not present")
    return ir.load_set_pbtxt((_PAIRS / name / "ir.pbtxt").read_text()).pedigrees[0]


def _prepared(p: pb.Pedigree) -> _layout_mod._Graph:
    """Run the layout front end (the graph the crossing counter reads)."""
    return _layout2_mod._prepare(p).graph


def _crossings(p: pb.Pedigree, ranks: list[list[int]]) -> int:
    return _ordering_mod.count_crossings(_prepared(p), ranks)


def _ident_rows(p: pb.Pedigree, ranks: list[list[int]]) -> list[list[tuple[int, int]]]:
    """Per-rank stable identities ``(generation, index)`` — the arrangement independent of row indexing.

    Read from the prepared graph, so a pass-through cell has its synthetic identity (keyed on the sibship it
    leads to, not on the input order).
    """
    individuals = _layout2_mod._prepare(p).graph.individuals
    return [[(individuals[i].generation, individuals[i].index) for i in row] for row in ranks]


def _shuffled(p: pb.Pedigree, seed: int) -> pb.Pedigree:
    """A copy with ``individuals`` and ``matings`` permuted — same pedigree, different row indices."""
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


# --- ordering (crossing minimization) ---------------------------------------------------------------------


@pytest.mark.parametrize("name", _TIER1)
def test_tier1_has_zero_crossings(name: str) -> None:
    p = test_render._load(name)
    assert _crossings(p, render.layout(p).nid) == 0


@pytest.mark.parametrize("name", _CROSS_JOINS)
def test_cross_join_orders_with_zero_crossings(name: str) -> None:
    p = _load_pair(name)
    assert _crossings(p, render.layout(p).nid) == 0


@pytest.mark.parametrize("name", [*_TIER1, *_CROSS_JOINS])
def test_couples_adjacent_and_children_span(name: str) -> None:
    p = test_render._load(name) if name in _TIER1 else _load_pair(name)
    at, idx = test_render._coords(render.layout(p)), test_render._index(p)
    for m in p.matings:
        if m.HasField("partner_b"):
            a, b = at[idx[test_render._pos(m.partner_a)]], at[idx[test_render._pos(m.partner_b)]]
            assert a[0] == b[0] and abs(a[1] - b[1]) == 1
        if m.offspring and m.HasField("partner_a"):
            parent_x = [at[idx[test_render._pos(m.partner_a)]][2]]
            if m.HasField("partner_b"):
                parent_x.append(at[idx[test_render._pos(m.partner_b)]][2])
            mid = sum(parent_x) / len(parent_x)
            child_x = [at[idx[test_render._pos(o.child)]][2] for o in m.offspring]
            assert min(child_x) - _EPS <= mid <= max(child_x) + _EPS


def test_twins_stay_adjacent() -> None:
    p = test_render._load("twins")
    at, idx = test_render._coords(render.layout(p)), test_render._index(p)
    for m in p.matings:
        co = [o for o in m.offspring if o.HasField("twin_group")]
        for x, y in itertools.pairwise(co):
            assert abs(at[idx[test_render._pos(x.child)]][1] - at[idx[test_render._pos(y.child)]][1]) == 1


# --- determinism + shuffle invariance ---------------------------------------------------------------------


@pytest.mark.parametrize("name", [*_TIER1, *_CROSS_JOINS])
def test_order_is_run_to_run_deterministic(name: str) -> None:
    p = test_render._load(name) if name in _TIER1 else _load_pair(name)
    assert render.layout(p) == render.layout(p)


_SHUFFLED_GOLDENS = ("childless", "founder_sibship_marry_in", "descent_across_rows", "detached_branch")


@pytest.mark.parametrize("name", [*_TIER1, *_SHUFFLED_GOLDENS, *_CROSS_JOINS])
def test_order_is_shuffle_invariant(name: str) -> None:
    # The strongest determinism test: the drawn arrangement (by stable identity) must not depend on the input
    # individuals'/matings' order — only on the tie-break. Shuffling changes row indices but not the drawing.
    p = test_render._load(name) if name in _TIER1 or name in _SHUFFLED_GOLDENS else _load_pair(name)
    ref = _ident_rows(p, render.layout(p).nid)
    for seed in range(4):
        q = _shuffled(p, seed)
        assert _ident_rows(q, render.layout(q).nid) == ref


# --- quantisation (byte-stable goldens) -------------------------------------------------------------------


@pytest.mark.parametrize("name", _DRAWABLE)
def test_pos_is_quantised(name: str) -> None:
    # The x-solve converges iteratively, so its residual is float-noisy; the final pos is rounded to a fixed
    # decimal grid so the byte-compared goldens cannot tip a rounding boundary across arch/compiler.
    for row in render.layout(test_render._load(name)).pos:
        for x in row:
            assert x == round(x, _POS_QUANTUM), f"pos {x!r} is not on the 1e-{_POS_QUANTUM} grid"


# --- routing (>2-mate overflow) ---------------------------------------------------------------------------

# All axis-aligned symbol / connector geometry needed to check a routed edge never crosses a symbol.
_RECT_RE = re.compile(r'<rect x="([-0-9.]+)" y="([-0-9.]+)" width="([-0-9.]+)" height="([-0-9.]+)"')
_CIRCLE_RE = re.compile(r'<circle cx="([-0-9.]+)" cy="([-0-9.]+)" r="([-0-9.]+)"')
_POLYGON_RE = re.compile(r'<polygon points="([^"]+)"')
_POLYLINE_RE = re.compile(r'<polyline points="([^"]+)"')


def _symbol_boxes(svg: str) -> list[tuple[float, float, float, float]]:
    """Every symbol's axis-aligned bounding box ``(x0, y0, x1, y1)`` — square, circle, or diamond."""
    boxes: list[tuple[float, float, float, float]] = []
    for x, y, w, h in _RECT_RE.findall(svg):
        boxes.append((float(x), float(y), float(x) + float(w), float(y) + float(h)))
    for cx, cy, r in _CIRCLE_RE.findall(svg):
        boxes.append((float(cx) - float(r), float(cy) - float(r), float(cx) + float(r), float(cy) + float(r)))
    for pts in _POLYGON_RE.findall(svg):
        xs = [float(p.split(",")[0]) for p in pts.split()]
        ys = [float(p.split(",")[1]) for p in pts.split()]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes


def _routed_segments(svg: str) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Each straight segment of every routed ``<polyline>`` in the SVG."""
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for poly in _POLYLINE_RE.findall(svg):
        pts = [(float(p.split(",")[0]), float(p.split(",")[1])) for p in poly.split()]
        segs += list(itertools.pairwise(pts))
    return segs


def _overflow_pedigree() -> pb.Pedigree:
    """A hinge individual (I-2) married three times: I-1 (2 kids), I-3 (1 kid), I-4 (childless)."""
    inds = [pb.Individual(generation=1, index=i, gender=pb.GENDER_UNKNOWN) for i in range(1, 5)]
    inds += [pb.Individual(generation=2, index=i, gender=pb.GENDER_UNKNOWN) for i in range(1, 4)]
    p = pb.Pedigree(id="triple", individuals=inds)

    def mate(a: int, b: int, kids: list[int]) -> pb.Mating:
        m = pb.Mating(partner_a=pb.Position(generation=1, index=a), partner_b=pb.Position(generation=1, index=b))
        m.offspring.extend(pb.Offspring(child=pb.Position(generation=2, index=k)) for k in kids)
        return m

    p.matings.extend([mate(1, 2, [1, 2]), mate(2, 3, [3]), mate(2, 4, [])])  # I-2 marries three
    return p


def _routed_idents(p: pb.Pedigree, lay: object) -> list[tuple[bool, frozenset[tuple[int, int]]]]:
    """Each routed mating as ``(consanguineous, {partner identities})`` — independent of row indexing."""
    out: list[tuple[bool, frozenset[tuple[int, int]]]] = []
    for rm in lay.routed:  # type: ignore[attr-defined]
        (la, ka), (lb, kb) = rm.a, rm.b
        ends = {
            (p.individuals[lay.nid[la][ka]].generation, p.individuals[lay.nid[la][ka]].index),  # type: ignore[attr-defined]
            (p.individuals[lay.nid[lb][kb]].generation, p.individuals[lay.nid[lb][kb]].index),  # type: ignore[attr-defined]
        }
        out.append((rm.consanguineous, frozenset(ends)))
    return sorted(out, key=repr)


def test_overflow_routes_the_third_mating() -> None:
    # A >2-mate individual keeps its two heaviest adjacencies; the third (lightest, childless) mating becomes a
    # routed edge on the Layout seam — no multi-mate DeferredFeatureError (v1 deferred every >2-mate shape).
    p = _overflow_pedigree()
    g = _prepared(p)
    routed = _ordering_mod.order(g, _layout_mod._cross_matings(g)).routed
    assert len(routed) == 1  # the single lightest (childless) mating routes; two heavier stay adjacent
    lay = render.layout(p)  # draws — no raise
    assert len(lay.routed) == 1
    rm = lay.routed[0]
    # the routed partners (I-2 hinge and I-4) are non-adjacent columns on the same rank
    assert rm.a[0] == rm.b[0] and abs(rm.a[1] - rm.b[1]) >= 2
    assert rm.children == ()  # childless
    # every non-routed mating still draws as an adjacent couple
    at, idx = test_render._coords(lay), test_render._index(p)
    for m in p.matings[:2]:
        a, b = at[idx[test_render._pos(m.partner_a)]], at[idx[test_render._pos(m.partner_b)]]
        assert a[0] == b[0] and abs(a[1] - b[1]) == 1


def test_overflow_renders_a_routed_polyline_clear_of_symbols() -> None:
    svg = render.render_svg(_overflow_pedigree())
    segs = _routed_segments(svg)
    assert segs, "the routed third mating must draw as a polyline"
    boxes = _symbol_boxes(svg)
    for (x1, y1), (x2, y2) in segs:
        lo_x, hi_x, lo_y, hi_y = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
        for bx0, by0, bx1, by1 in boxes:
            ox = min(hi_x, bx1) - max(lo_x, bx0)
            oy = min(hi_y, by1) - max(lo_y, by0)
            assert not (ox > _EPS and oy > _EPS), "a routed segment passes through a symbol"


def test_overflow_is_deterministic_and_shuffle_invariant() -> None:
    p = _overflow_pedigree()
    assert render.layout(p) == render.layout(p)  # run-to-run identical
    ref_rows = _ident_rows(p, render.layout(p).nid)
    ref_routed = _routed_idents(p, render.layout(p))
    for seed in range(4):
        q = _shuffled(p, seed)
        lay = render.layout(q)
        assert _ident_rows(q, lay.nid) == ref_rows
        assert _routed_idents(q, lay) == ref_routed  # the routed mating is the same by identity, whatever the order


def test_cousin_marriage_draws_as_adjacent_consanguineous_couple() -> None:
    # c05: a cousin marriage where both partners have siblings. The ordering pulls each cousin to its sibship
    # end, so the loop mating is an ordinary ADJACENT consanguineous couple — no routing, no deferral.
    p = _load_pair("c05")
    lay = render.layout(p)
    assert lay.routed == []  # drawn straight, not routed
    at, idx = test_render._coords(lay), test_render._index(p)
    consang = [m for m in p.matings if m.consanguineous]
    assert consang, "c05 has consanguineous matings"
    for m in consang:
        a, b = at[idx[test_render._pos(m.partner_a)]], at[idx[test_render._pos(m.partner_b)]]
        assert a[0] == b[0] and abs(a[1] - b[1]) == 1  # adjacent columns
        col = min(a[1], b[1])
        assert lay.spouse[a[0]][col] == 2  # flagged consanguineous (double line)
    render.render_svg(p)  # renders without raising


def test_avuncular_marriage_draws_via_the_ghost() -> None:
    # An avuncular (cross-generation) marriage draws via the ghost duplication (a same-row couple on the deeper
    # partner's rank), not a routed cross-rank edge. It must draw, with the ghost and no routed edge.
    p = _load_pair("c18")
    lay = render.layout(p)
    assert lay.ghost_of, "avuncular join is drawn via the ghost duplication"
    assert lay.routed == []  # not (yet) a routed cross-rank edge
    render.render_svg(p)  # renders without raising


@pytest.mark.skipif(not _HAS_REVIEW_SET, reason="review set (CPG-internal) not present")
def test_review_set_deferral_count_is_zero() -> None:
    # The whole review set draws (some avuncular joins via the ghost); nothing defers.
    deferred = []
    for name in _REVIEW_SET:
        try:
            render.layout(_load_pair(name))
        except render.DeferredFeatureError:
            deferred.append(name)
    assert deferred == [], f"expected no deferrals, got {deferred}"


# --- cohesion + contiguity blocks -------------------------------------------------------------------------


def _couple_gaps(lay: render.Layout) -> list[float]:
    """Every drawn couple's x-distance (any mating flagged ``spouse``, childless included).

    Except a couple heading a descent across rows, and any other couple of the same individual: the couple
    centres over its pass-through, whose column the rows it crosses decide, and a hinge sits centred between
    its matings, so their widths are set there (``test_couple_over_a_passthrough_centres_on_it``).
    """
    xof = {c: lay.pos[level][k] for level, row in enumerate(lay.nid) for k, c in enumerate(row)}
    heads = {
        (lay.nid[level - 1][lay.fam[level][k]], lay.nid[level - 1][lay.fam[level][k] + 1])
        for level in range(1, len(lay.nid))
        for k, c in enumerate(lay.nid[level])
        if c in lay.passthrough and lay.spouse[level - 1][lay.fam[level][k]]
    }
    members = {c for couple in heads for c in couple}
    return [abs(xof[a] - xof[b]) for a, b in _layout2_mod._couples(lay) if a not in members and b not in members]


@pytest.mark.parametrize("name", [*_DRAWABLE, "c05"])
def test_drawn_couples_stay_tight(name: str) -> None:
    # Couples-stay-tight invariant: no drawn couple stretches past sib_gap (a small multiple of couple_gap).
    # A hinge couple spreads to at most sib_gap to centre over two sibships (half_sibs), which is the boundary —
    # anything wider is a torn contiguity block.
    lay = render.layout(test_render._load(name) if name in _DRAWABLE else _load_pair(name))
    for gap in _couple_gaps(lay):
        assert gap <= render.DEFAULT_GEOMETRY.sib_gap + _EPS, f"{name}: a drawn couple is torn ({gap:.3f} units apart)"


def test_couple_over_a_passthrough_centres_on_it() -> None:
    # I-1 x I-3's child is drawn on row III: the couple's midpoint sits over the pass-through on row II, and the
    # pass-through over the child, so the descent is one straight line.
    lay = render.layout(test_render._load("descent_across_rows"))
    ((cell,),) = [[c for c in row if c in lay.passthrough] for row in lay.nid if any(c in lay.passthrough for c in row)]
    level, k = next((lv, row.index(cell)) for lv, row in enumerate(lay.nid) if cell in row)
    pc = lay.fam[level][k]
    assert lay.spouse[level - 1][pc], "the pass-through descends from a couple"
    mid = (lay.pos[level - 1][pc] + lay.pos[level - 1][pc + 1]) / 2
    assert abs(lay.pos[level][k] - mid) < _EPS
    (child_k,) = [j for j, f in enumerate(lay.fam[level + 1]) if f == k]
    assert abs(lay.pos[level + 1][child_k] - lay.pos[level][k]) < _EPS


def test_c05_couples_are_at_couple_gap() -> None:
    # Regression lock for the torn-couple fix: every c05 couple — the born-in<->marry-in pairs 2-5x2-6 and
    # 3-7x3-8 that Stage A used to stretch — sits at exactly couple_gap, no longer pulled apart by its subtree.
    gaps = _couple_gaps(render.layout(_load_pair("c05")))
    assert gaps, "c05 has drawn couples"
    for gap in gaps:
        assert abs(gap - render.DEFAULT_GEOMETRY.couple_gap) < _EPS


def test_half_sibs_hinge_centres_over_both_sibships() -> None:
    # The hinge case cohesion must NOT reel tight: the individual with two matings sits between its two
    # spouses so each mating's midpoint centres on that mating's children. Assert both sibships are centred.
    p = test_render._load("half_sibs")
    at, idx = test_render._coords(render.layout(p)), test_render._index(p)
    heads: dict[tuple[int, int], int] = {}
    centred = 0
    for m in p.matings:
        if not m.offspring or not m.HasField("partner_a"):
            continue
        parent_x = [at[idx[test_render._pos(m.partner_a)]][2]]
        if m.HasField("partner_b"):
            parent_x.append(at[idx[test_render._pos(m.partner_b)]][2])
        mid = sum(parent_x) / len(parent_x)
        child_x = [at[idx[test_render._pos(o.child)]][2] for o in m.offspring]
        assert abs(mid - sum(child_x) / len(child_x)) < _EPS, "a half_sibs mating is not centred over its kids"
        for parent in (m.partner_a, m.partner_b) if m.HasField("partner_b") else (m.partner_a,):
            heads[test_render._pos(parent)] = heads.get(test_render._pos(parent), 0) + 1
        centred += 1
    assert centred >= 2, "half_sibs must have two sibships to centre"
    assert max(heads.values()) >= 2, "half_sibs must have a hinge individual heading two matings"


def _width(lay: object) -> float:
    """The figure's x-extent in layout units (max minus min over every cell)."""
    xs = [x for row in lay.pos for x in row]  # type: ignore[attr-defined]
    return (max(xs) - min(xs)) if xs else 0.0


@pytest.mark.parametrize("name", _REVIEW_SET)
def test_review_set_blocks_hold(name: str) -> None:
    # Every review-set figure: no contiguity block splits. A couple never stretches past sib_gap, and the
    # figure stays no wider than packing every cell in one row at sib_gap — a torn block (c19's founder
    # sibship pre-fix) blows a figure many times past that bound.
    lay = render.layout(_load_pair(name))
    for gap in _couple_gaps(lay):
        assert gap <= render.DEFAULT_GEOMETRY.sib_gap + _EPS, f"{name}: a drawn couple is torn ({gap:.3f} units apart)"
    packed = sum(lay.n) * render.DEFAULT_GEOMETRY.sib_gap  # a sound upper bound on a healthy figure's width
    assert _width(lay) <= packed + _EPS, f"{name}: width {_width(lay):.1f} exceeds one-row packing bound {packed:.1f}"


def test_c19_founder_sibship_stays_contiguous() -> None:
    # Regression lock for the founder-sibship tear: the leaf founder-sib 1-1 (count 9, no descent) used to sit
    # at x~0 while its sibling 1-10 (heading the descent) was pulled to x~402, blowing the figure ~35x wider.
    # As a block the sibship rides together — 1-1 sits exactly sib_gap left of 1-10 — and the width stays sane.
    p = _load_pair("c19")
    lay = render.layout(p)
    packed = sum(lay.n) * render.DEFAULT_GEOMETRY.sib_gap
    assert _width(lay) <= packed + _EPS, f"c19 width {_width(lay):.1f} exceeds one-row packing bound {packed:.1f}"
    at, idx = test_render._coords(lay), test_render._index(p)
    x_leaf = at[idx[(1, 1)]][2]
    x_head = at[idx[(1, 10)]][2]
    assert at[idx[(1, 1)]][0] == at[idx[(1, 10)]][0], "1-1 and 1-10 share a row (the founder sibship)"
    assert abs(abs(x_leaf - x_head) - render.DEFAULT_GEOMETRY.sib_gap) < _EPS, "1-1 must ride sib_gap from its sibling"


# --- rows are IR generations; descents across rows ---------------------------------------------------------


def _couple_and_child(child_generation: int, *, top: int = 1, twins: bool = False) -> pb.Pedigree:
    """A generation-``top`` couple whose children (one, or MZ twins) are drawn on ``child_generation``."""
    kids = [pb.Position(generation=child_generation, index=i) for i in (1, 2)[: 2 if twins else 1]]
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=top, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=top, index=2, gender=pb.GENDER_WOMAN),
            *(pb.Individual(generation=k.generation, index=k.index, gender=pb.GENDER_WOMAN) for k in kids),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=top, index=1),
                partner_b=pb.Position(generation=top, index=2),
                offspring=[
                    pb.Offspring(child=k, twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC)
                    if twins
                    else pb.Offspring(child=k)
                    for k in kids
                ],
            )
        ],
    )


def test_first_drawn_generation_numbers_the_gutter() -> None:
    # A pedigree whose top row is generation II draws II and III — not I and II — and says so in the markup.
    svg = render.render_svg(_couple_and_child(3, top=2))
    assert render.layout(_couple_and_child(3, top=2)).first_generation == 2
    assert re.findall(r'class="generation" data-generation="(\d+)"', svg) == ["2", "3"]
    assert [t for *_, t in test_render._markers(svg)] == ["II", "III"]


def test_descent_across_rows_passes_through_each_crossed_row() -> None:
    # Children drawn three rows below their parents: one pass-through on each of the two rows between, and
    # the children hang from the last one, twin grouping intact.
    p = _couple_and_child(4, twins=True)
    lay = render.layout(p)
    assert [sum(c in lay.passthrough for c in row) for row in lay.nid] == [0, 1, 1, 0]
    assert lay.twins[3][0] == pb.ZYGOSITY_TYPE_MONOZYGOTIC
    assert [lay.nid[2][f] in lay.passthrough for f in lay.fam[3]] == [True, True]


def test_descent_across_rows_is_one_sibship_group_and_no_symbol() -> None:
    svg = render.render_svg(_couple_and_child(4, twins=True))
    assert svg.count('class="individual') == 4  # the two parents and the twins; pass-throughs draw no symbol
    groups = re.findall(r'<g class="sibship" data-parents="([^"]*)" data-children="([^"]*)">', svg)
    assert groups == [("I-1 I-2", "IV-1 IV-2")]


def test_couple_across_generations_defers() -> None:
    # A marry-in on a different generation from their partner has no drawn form (only an avuncular join of two
    # born-in partners is drawn across generations, via the ghost): deferred, not moved onto the partner's row.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=3, index=1, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=2, index=1),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            )
        ],
    )
    with pytest.raises(render.DeferredFeatureError, match="spans generations 1 and 2"):
        render.layout(p)


def test_deferral_names_the_descent_not_a_passthrough() -> None:
    # I-1's third mating overflows (routed), and its child is drawn a row lower: the deferral names the real
    # child, not the synthetic pass-through carrying its descent.
    i = test_render._ind
    p = pb.Pedigree(
        individuals=[i(1, 1), i(1, 2), i(1, 3), i(1, 4), i(2, 1), i(2, 2), i(3, 1)],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=k),
                offspring=[pb.Offspring(child=child)],
            )
            for k, child in (
                (2, pb.Position(generation=2, index=1)),
                (3, pb.Position(generation=2, index=2)),
                (4, pb.Position(generation=3, index=1)),
            )
        ],
    )
    with pytest.raises(render.DeferredFeatureError, match="'descent to 3-1'"):
        render.layout(p)


def _married_twin(older: bool) -> pb.Pedigree:
    """``twin_married`` with either twin marrying II-4."""
    p = test_render._load("twin_married")
    p.matings[1].partner_a.index = 1 if older else 2
    return p


@pytest.mark.parametrize(
    ("older", "expected"), [(True, ["2-4", "2-1", "2-2", "2-3"]), (False, ["2-1", "2-2", "2-4", "2-3"])]
)
def test_married_twin_keeps_birth_order(older: bool, expected: list[str]) -> None:
    # A twin pair joined to one twin's spouse is a three-member adjacency chain. It used to be oriented by identity
    # alone and never reversed, so the older twin's marriage drew the twins younger-first with the spouse between
    # siblings, although a zero-crossing order in birth order exists. Birth order is weak, but it only yields to
    # what a mating forces, and this marriage forces nothing.
    p = _married_twin(older)
    lay = render.layout(p)
    assert [f"{p.individuals[i].generation}-{p.individuals[i].index}" for i in lay.nid[1]] == expected
