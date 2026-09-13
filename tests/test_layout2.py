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
    """Run the layout front end (validate, ranks, alignment) to get the graph the crossing counter reads."""
    ir.validate(p)
    g = _layout_mod._derive(p)
    _layout_mod._duplicate_cross_generation(g)
    cross = _layout_mod._cross_matings(g)
    _layout_mod._detect_loops(g, cross)
    depth = _layout_mod._kindepth(g)
    _layout_mod._align_couples(g, depth, cross)
    base = min(depth) if depth else 0
    g.level = [d - base for d in depth]
    return g


def _crossings(p: pb.Pedigree, ranks: list[list[int]]) -> int:
    return _ordering_mod.count_crossings(_prepared(p), ranks)


def _ident_rows(p: pb.Pedigree, ranks: list[list[int]]) -> list[list[tuple[int, int]]]:
    """Per-rank stable identities ``(generation, index)`` — the arrangement independent of row indexing."""
    return [[(p.individuals[i].generation, p.individuals[i].index) for i in row] for row in ranks]


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


@pytest.mark.parametrize("name", [*_TIER1, "childless", "founder_sibship_marry_in", *_CROSS_JOINS])
def test_order_is_shuffle_invariant(name: str) -> None:
    # The strongest determinism test: the drawn arrangement (by stable identity) must not depend on the input
    # individuals'/matings' order — only on the tie-break. Shuffling changes row indices but not the drawing.
    p = (
        test_render._load(name)
        if name in _TIER1 or name in ("childless", "founder_sibship_marry_in")
        else _load_pair(name)
    )
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


def _couple_gaps(lay: object) -> list[float]:
    """Every drawn couple's x-distance under a layout (any mating flagged ``spouse``, childless included)."""
    xof = {c: lay.pos[level][k] for level, row in enumerate(lay.nid) for k, c in enumerate(row)}  # type: ignore[attr-defined]
    return [abs(xof[a] - xof[b]) for a, b in _layout2_mod._couples(lay)]  # type: ignore[arg-type]


@pytest.mark.parametrize("name", [*_DRAWABLE, "c05"])
def test_drawn_couples_stay_tight(name: str) -> None:
    # Couples-stay-tight invariant: no drawn couple stretches past sib_gap (a small multiple of couple_gap).
    # A hinge couple spreads to at most sib_gap to centre over two sibships (half_sibs), which is the boundary —
    # anything wider is a torn contiguity block.
    lay = render.layout(test_render._load(name) if name in _DRAWABLE else _load_pair(name))
    for gap in _couple_gaps(lay):
        assert gap <= render.DEFAULT_GEOMETRY.sib_gap + _EPS, f"{name}: a drawn couple is torn ({gap:.3f} units apart)"


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
