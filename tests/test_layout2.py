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


def _ident_rows(p: pb.Pedigree, ranks: list[list[int]]) -> list[list[tuple[int, ...]]]:
    """Per-rank stable identities (``_Graph.ident``) — the arrangement independent of row indexing.

    Read from the prepared graph, so a pass-through cell has its synthetic identity (keyed on the sibship it
    leads to, not on the input order).
    """
    graph = _layout2_mod._prepare(p).graph
    return [[graph.ident(i) for i in row] for row in ranks]


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


_SHUFFLED_GOLDENS = (
    "childless",
    "founder_sibship_marry_in",
    "descent_across_rows",
    "detached_branch",
    "cousins_across_family",
    "cousins_across_middle_sibling",
    "crossing_descents",
    "lone_parent_sibships",
    "count_collapsed_parents",
    "twins_marry_chain_stretches",
    "twins_marry_cousins_apart",
)


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
    """Every rigid drawn couple's x-distance (any mating flagged ``spouse``, childless included).

    The couples the layout leaves free are excluded: a hinge's (either partner mates more than once), whose spread
    centres each of its families and can exceed ``sib_gap`` (``hinge_offcentre``), and a couple with a partner
    bonded to a co-twin, which stretches so each drop in the twins' chain reaches its own children
    (``twins_marry_chain_stretches``). Their properties are centring and each drop on its own bar, tested directly.
    """
    g = render.DEFAULT_GEOMETRY
    seps = _layout2_mod._row_seps(lay, g.couple_gap, g.sib_gap)
    blocks = _layout2_mod._blocks(lay, seps, _layout2_mod._relations(lay))
    bonded = {(lay.nid[level][a], lay.nid[level][b]) for level, a, b in _layout2_mod._block_pairs(blocks)}
    xof = {c: lay.pos[level][k] for level, row in enumerate(lay.nid) for k, c in enumerate(row)}
    return [abs(xof[a] - xof[b]) for a, b in _layout2_mod._couples(lay) if (a, b) in bonded]


@pytest.mark.parametrize("name", [*_DRAWABLE, "c05"])
def test_drawn_couples_stay_tight(name: str) -> None:
    # Couples-stay-tight invariant: no ordinary drawn couple stretches past sib_gap — anything wider is a torn
    # contiguity block. (The couples the layout leaves free stretch by design; see _couple_gaps.)
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


def test_cousins_across_a_family_move_it_aside() -> None:
    # II-1's and II-3's children marry; II-2's family stands between them. The crossing-only ordering reached an
    # order that put III-2 inside III-3/III-4's sibship (one crossing) and the layout deferred it as torn. Sibship
    # contiguity is strong and crossings weak, so the ordering now ranks torn sibships first and moves II-2's
    # family to the end instead.
    lay = render.layout(test_render._load("cousins_across_family"))
    assert lay.routed == []


def test_a_twin_chain_reverses_for_a_cousin_marriage_below() -> None:
    # Twins II-1 and II-2 both marry, a spouse-twin-twin-spouse chain that starts in birth order. II-1's daughter
    # marries II-3's son, so II-1 must stand next to II-3 and the chain reversed. Local moves cannot reverse it and
    # re-sort row III together, so the search kept a torn order and the pedigree deferred; the retry from reversed
    # atoms finds the clean one.
    p = test_render._load("twins_marry_cousins_apart")
    lay = render.layout(p)
    assert [f"{p.individuals[i].generation}-{p.individuals[i].index}" for i in lay.nid[1]] == [
        "2-4",
        "2-2",
        "2-1",
        "2-5",
        "2-3",
        "2-6",
    ]


def _pos(g: int, i: int) -> pb.Position:
    return pb.Position(generation=g, index=i)


def _lone_parent_pedigree(with_mate: bool) -> pb.Pedigree:
    """II-1 heads two sibships: III-1/III-2 alone (or with II-2), and III-3 alone."""
    man, woman = pb.GENDER_MAN, pb.GENDER_WOMAN
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=man),
            pb.Individual(generation=1, index=2, gender=woman),
            pb.Individual(generation=2, index=1, gender=woman),
            *([pb.Individual(generation=2, index=2, gender=man)] if with_mate else []),
            *(pb.Individual(generation=3, index=k, gender=man) for k in (1, 2, 3)),
        ]
    )
    p.matings.add(partner_a=_pos(1, 1), partner_b=_pos(1, 2)).offspring.add(child=_pos(2, 1))
    first = p.matings.add(partner_a=_pos(2, 1))
    if with_mate:
        first.partner_b.CopyFrom(_pos(2, 2))
    first.offspring.add(child=_pos(3, 1))
    first.offspring.add(child=_pos(3, 2))
    p.matings.add(partner_a=_pos(2, 1)).offspring.add(child=_pos(3, 3))
    return p


@pytest.mark.parametrize(
    ("with_mate", "sibships"),
    [
        (False, {("II-1", "III-1 III-2"), ("II-1", "III-3")}),
        (True, {("II-1 II-2", "III-1 III-2"), ("II-1", "III-3")}),
    ],
)
def test_a_lone_parents_sibships_draw_apart(with_mate: bool, sibships: set[tuple[str, str]]) -> None:
    # The layout recorded a child's parent by column, so II-1's lone-parent sibship joined its other one: three full
    # siblings where the IR has two half-sibships (review-set figure c09), or III-3 drawn as II-1 and II-2's. Each
    # lone-parent mating now has a phantom partner, drawn as a marriage line to an omitted partner as the literature
    # draws it, so each sibship drops from its own line.
    svg = render.render_svg(_lone_parent_pedigree(with_mate))
    drawn = set(re.findall(r'<g class="sibship" data-parents="([^"]*)" data-children="([^"]*)"', svg))
    assert {s for s in drawn if s[1].startswith("III")} == sibships
    omitted = re.findall(r'<g class="mating partner-omitted" data-partners="([^"]*)"', svg)
    assert omitted == (["II-1"] if with_mate else ["II-1", "II-1"])
    assert "ind-III-4" not in svg and svg.count('class="individual') == len(
        _lone_parent_pedigree(with_mate).individuals
    )


def test_a_count_collapsed_parent_drops_from_its_own_centre() -> None:
    # A phantom partner drew a group symbol ("3" sisters) as one person with children by an omitted partner. The
    # literature drops a straight line from the group symbol to its offspring (review-set figure c05): no phantom,
    # the drop leaves the symbol's centre, and the label stack moves beside the line. An ordinary lone parent keeps
    # its line to the omitted partner.
    p = test_render._load("count_collapsed_parents")
    lay = render.layout(p)
    svg = render.render_svg(p)
    at = {
        (i.generation, i.index): (lv, k)
        for lv, row in enumerate(lay.nid)
        for k, c in enumerate(row)
        if c < len(p.individuals)
        for i in [p.individuals[c]]
    }
    assert len(lay.phantom) == 1
    assert re.findall(r'<g class="mating partner-omitted" data-partners="([^"]*)"', svg) == ["III-3"]
    for parent, child in (((3, 1), (4, 1)), ((3, 2), (4, 2)), ((3, 4), (4, 5)), ((3, 5), (4, 6))):
        (plv, pk), (clv, ck) = at[parent], at[child]
        assert lay.fam[clv][ck] == pk and lay.descends_from_one(clv, ck)
        assert lay.drops_from_centre(plv, pk)
        assert lay.pos[plv][pk] == lay.pos[clv][ck], "the drop runs straight down from the group to its offspring"
    assert not lay.drops_from_centre(*at[(3, 3)])
    starts = set(re.findall(r'<text class="label" [^>]*text-anchor="start"[^>]*>([^<]*)</text>', svg))
    assert starts == {"III-1", "III-2", "III-4", "III-5"}, "only a label with a line through its centre moves"


def test_a_group_with_two_lone_sibships_keeps_a_line_for_each() -> None:
    # A group parent's one lone sibship drops from its centre, but two cannot share that one drop: they deferred as a
    # torn sibship. With more than one, each keeps its own line to an omitted partner, as for any lone parent.
    man, woman, unknown = pb.GENDER_MAN, pb.GENDER_WOMAN, pb.GENDER_UNKNOWN
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=man),
            pb.Individual(generation=1, index=2, gender=woman),
            pb.Individual(generation=2, index=1, gender=man, count=3),
            pb.Individual(generation=2, index=2, gender=woman),
            pb.Individual(generation=3, index=1, gender=unknown, count_unspecified=True),
            pb.Individual(generation=3, index=2, gender=unknown, count_unspecified=True),
            pb.Individual(generation=3, index=3, gender=man),
        ]
    )
    top = p.matings.add(partner_a=_pos(1, 1), partner_b=_pos(1, 2))
    top.offspring.add(child=_pos(2, 1))
    top.offspring.add(child=_pos(2, 2))
    p.matings.add(partner_a=_pos(2, 1)).offspring.add(child=_pos(3, 1))
    second = p.matings.add(partner_a=_pos(2, 1))
    second.offspring.add(child=_pos(3, 2))
    second.offspring.add(child=_pos(3, 3))
    svg = render.render_svg(p)
    assert re.findall(r'<g class="mating partner-omitted" data-partners="([^"]*)"', svg) == ["II-1", "II-1"]
    drawn = set(re.findall(r'<g class="sibship" data-parents="([^"]*)" data-children="([^"]*)"', svg))
    assert {s for s in drawn if s[1].startswith("III")} == {("II-1", "III-1"), ("II-1", "III-2 III-3")}


def test_a_label_beside_the_drop_takes_the_side_no_couple_leaves() -> None:
    # II-1 (a group of 3) has a lone sibship, dropped from its centre, and a drawn partner II-3 on its right, whose
    # couple's drop leaves that side. The label stack moved beside the drop always went right, so the couple's drop
    # ran through it; it now goes left, with its reach reserved on that side.
    man, woman, unknown = pb.GENDER_MAN, pb.GENDER_WOMAN, pb.GENDER_UNKNOWN
    note = pb.Annotation(text="a long annotation here", type=pb.ANNOTATION_TYPE_OTHER)
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=man),
            pb.Individual(generation=1, index=2, gender=woman),
            pb.Individual(generation=2, index=1, gender=man, count=3, annotations=[note]),
            pb.Individual(generation=2, index=2, gender=woman),
            pb.Individual(generation=2, index=3, gender=woman),
            pb.Individual(generation=3, index=1, gender=unknown, count_unspecified=True),
            pb.Individual(generation=3, index=2, gender=man),
            pb.Individual(generation=3, index=3, gender=woman),
        ]
    )
    top = p.matings.add(partner_a=_pos(1, 1), partner_b=_pos(1, 2))
    top.offspring.add(child=_pos(2, 1))
    top.offspring.add(child=_pos(2, 2))
    p.matings.add(partner_a=_pos(2, 1)).offspring.add(child=_pos(3, 1))
    couple = p.matings.add(partner_a=_pos(2, 1), partner_b=_pos(2, 3))
    couple.offspring.add(child=_pos(3, 2))
    couple.offspring.add(child=_pos(3, 3))
    lay = render.layout(p)
    (level, k), (_, mate) = [
        (lv, row.index(i))
        for lv, row in enumerate(lay.nid)
        for i, ind in enumerate(p.individuals)
        if i in row and (ind.generation, ind.index) in ((2, 1), (2, 3))
    ]
    assert mate == k + 1 and lay.spouse[level][k], "the partner is on the right"
    assert lay.label_side(level, k) == -1
    svg = render.render_svg(p)
    g = re.search(r'<g id="ind-II-1" class="individual.*?</g>', svg, re.S)
    assert g is not None
    anchors = re.findall(r'<text class="label" [^>]*text-anchor="(\w+)"', g.group(0))
    assert anchors == ["end", "end"]
    drop = re.search(r'<g class="sibship" data-parents="II-1 II-3"[^>]*>\s*(?:<line x1="|<path d="M)([-0-9.]+)', svg)
    assert drop is not None
    (right,) = [r for _l, r, _y, s in test_render._label_spans(svg) if s == note.text]
    assert right < float(drop.group(1)), "the label stays clear of the couple's drop"


def test_crossing_descents_turn_on_their_own_tracks() -> None:
    # A fuzzed pedigree, minimised. The order puts II-2 x II-6 left of II-3 x II-4 but its twins right of their
    # children, so the descents cross; the drops used to run along each other's bars at bar height and the row read
    # as one family. Each now turns on its own elbow track above the bars, and II-2 x II-6's drop, which stands over
    # II-3 x II-4's landing leg, turns on the higher track, so the two never run along one line.
    svg = render.render_svg(test_render._load("crossing_descents"))
    elbows = re.findall(r'<path d="M([-0-9.]+),[-0-9.]+L[-0-9.]+,([-0-9.]+)Q', svg)
    assert len(elbows) == 2
    (_, y_left), (_, y_right) = sorted((float(x), float(y)) for x, y in elbows)
    assert y_left < y_right, "the drop standing over the other's landing leg turns higher"
    # And it stands clear of III-1, the other family's child it would otherwise sit exactly above.
    p = test_render._load("crossing_descents")
    lay = render.layout(p)
    x = {
        (i.generation, i.index): lay.pos[lv][k]
        for lv, row in enumerate(lay.nid)
        for k, c in enumerate(row)
        if c < len(p.individuals)
        for i in [p.individuals[c]]
    }
    assert abs((x[(2, 2)] + x[(2, 6)]) / 2 - x[(3, 1)]) >= _layout2_mod._APART_CLEAR - _EPS


def _twin_parent_pedigree(married: bool) -> pb.Pedigree:
    """Twins II-1, II-2; II-1 has III-1 with II-3 (if ``married``, else alone) and a lone sibship III-2."""
    man, woman = pb.GENDER_MAN, pb.GENDER_WOMAN
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=man),
            pb.Individual(generation=1, index=2, gender=woman),
            pb.Individual(generation=2, index=1, gender=woman),
            pb.Individual(generation=2, index=2, gender=woman),
            *([pb.Individual(generation=2, index=3, gender=man)] if married else []),
            pb.Individual(generation=3, index=1, gender=man),
            pb.Individual(generation=3, index=2, gender=man),
        ]
    )
    top = p.matings.add(partner_a=_pos(1, 1), partner_b=_pos(1, 2))
    for k in (1, 2):
        top.offspring.add(child=_pos(2, k), twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC)
    first = p.matings.add(partner_a=_pos(2, 1))
    if married:
        first.partner_b.CopyFrom(_pos(2, 3))
    first.offspring.add(child=_pos(3, 1))
    p.matings.add(partner_a=_pos(2, 1)).offspring.add(child=_pos(3, 2))
    return p


@pytest.mark.parametrize(
    ("married", "sibships"),
    [(True, {("II-1 II-3", "III-1"), ("II-1", "III-2")}), (False, {("II-1", "III-1"), ("II-1", "III-2")})],
)
def test_a_twins_lone_sibship_draws_without_a_free_side(married: bool, sibships: set[tuple[str, str]]) -> None:
    # A phantom partner takes one of the parent's two sides. A twin with a spouse has none free (co-twin, spouse),
    # and a phantom there overflowed and deferred a shape that drew before. The phantom now goes only where a side
    # is free; the other lone sibship drops from the parent's own centre, marked so drawing does not read it as the
    # couple's.
    svg = render.render_svg(_twin_parent_pedigree(married))
    drawn = {
        (" ".join(sorted(parents.split())), kids)
        for parents, kids in re.findall(r'<g class="sibship" data-parents="([^"]*)" data-children="([^"]*)"', svg)
    }
    assert {s for s in drawn if s[1].startswith("III")} == sibships


def test_a_drop_on_its_bar_by_rounding_draws_no_elbow() -> None:
    # Label clearance gives separations whose midpoints land a fraction of the position quantum off the child, and
    # a pixel tolerance read that as a miss: a zero-length elbow path that also opened the row pitch.
    man, woman = pb.GENDER_MAN, pb.GENDER_WOMAN
    p = pb.Pedigree(
        individuals=[
            pb.Individual(
                generation=1,
                index=1,
                gender=man,
                annotations=[pb.Annotation(text="xxxxx", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
            pb.Individual(
                generation=1,
                index=2,
                gender=woman,
                annotations=[pb.Annotation(text="yyyyyyyyy", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
            pb.Individual(generation=2, index=1, gender=man),
        ]
    )
    p.matings.add(partner_a=_pos(1, 1), partner_b=_pos(1, 2)).offspring.add(child=_pos(2, 1))
    svg = render.render_svg(p)
    assert "<path d=" not in svg
    assert render.render_svg(p) == svg


def test_cousins_across_a_middle_sibling_start_at_facing_ends() -> None:
    # IV-1's son and IV-3's daughter marry; IV-2's family stands between them in birth order. Local moves reached
    # an order with a torn sibship and the pedigree deferred. A run seeded with the two lines adjacent and the
    # cousins at their facing ends finds the clean order: IV-2's family moves aside and V-1 = V-4 face each other.
    p = test_render._load("cousins_across_middle_sibling")
    lay = render.layout(p)
    row = [f"{p.individuals[i].generation}-{p.individuals[i].index}" for i in lay.nid[4] if i < len(p.individuals)]
    assert abs(row.index("5-1") - row.index("5-4")) == 1


def test_facing_seeds_do_not_depend_on_input_order() -> None:
    # The seeds were built in input mating order, and ties between split matings went to the first in input order,
    # while the six-seed cap and the stop at the first tear-free seed made that order decide draw versus defer (a
    # fuzzed pedigree drew in one input order and deferred in two others). Seeds and ties now follow identity.
    from grus.render import _layout2 as l2
    from grus.render import _ordering

    def seeds(p: pb.Pedigree) -> list[dict[tuple[tuple[int, ...], ...], list[tuple[int, ...]]]]:
        prep = l2._prepare(p)
        model = _ordering._Model(prep.graph)
        by_index = {mr.index: mr for mr in prep.graph.matings}
        return [
            {model.mating_key(by_index[mi])[0]: [model.ident(k) for k in kids] for mi, kids in seed.items()}
            for seed in _ordering._facing_seeds(model)
        ]

    p = test_render._load("cousins_across_middle_sibling")
    ref = seeds(p)
    assert ref
    for seed in range(4):
        assert seeds(_shuffled(p, seed)) == ref


def test_which_overflow_mating_routes_does_not_depend_on_input_order() -> None:
    # I-1 has three childless partners, one more than a row has sides for, so one mating routes. The tie between
    # equally weighted matings broke on input position, so shuffling Pedigree.matings changed which partner stood
    # apart; it now breaks on the matings' identity.
    man, woman = pb.GENDER_MAN, pb.GENDER_WOMAN
    p = pb.Pedigree(individuals=[pb.Individual(generation=1, index=1, gender=man)])
    for k in (2, 3, 4):
        p.individuals.add(generation=1, index=k, gender=woman)
        p.matings.add(partner_a=_pos(1, 1), partner_b=_pos(1, k))

    def routed(q: pb.Pedigree) -> set[tuple[int, int]]:
        lay = render.layout(q)
        cells = {(lv, k): q.individuals[i] for lv, row in enumerate(lay.nid) for k, i in enumerate(row)}
        return {
            (cells[end].generation, cells[end].index)
            for rm in lay.routed
            for end in (rm.a, rm.b)
            if cells[end].index != 1
        }

    ref = routed(p)
    assert len(ref) == 1
    for seed in range(8):
        assert routed(_shuffled(p, seed)) == ref


def test_founder_sibships_and_routes_are_listed_in_layout_order() -> None:
    # Both lists are drawn in order, so an order taken from the input's matings would reorder the SVG under a shuffle.
    for p in (test_render._load("founder_sibship_marry_in"), _overflow_pedigree()):
        lay = render.layout(p)
        assert lay.founder_sibships == sorted(lay.founder_sibships)
        assert lay.routed == sorted(lay.routed, key=lambda rm: (rm.a, rm.b))
        for seed in range(4):
            shuffled = render.layout(_shuffled(p, seed))
            assert shuffled.founder_sibships == lay.founder_sibships
            assert [(rm.a, rm.b) for rm in shuffled.routed] == [(rm.a, rm.b) for rm in lay.routed]


def _ghost_ind(g: int, i: int, gender: pb.Gender = pb.GENDER_MAN) -> pb.Individual:
    return pb.Individual(generation=g, index=i, gender=gender)


def _ghost_mating(a: tuple[int, int], b: tuple[int, int], *kids: tuple[int, int], consang: bool = False) -> pb.Mating:
    return pb.Mating(
        partner_a=pb.Position(generation=a[0], index=a[1]),
        partner_b=pb.Position(generation=b[0], index=b[1]),
        consanguineous=consang,
        offspring=[pb.Offspring(child=pb.Position(generation=g, index=i)) for g, i in kids],
    )


def _niece_two_uncles(with_children: bool) -> pb.Pedigree:
    """Niece III-1 marries both her uncles II-1 and II-4: two ghosts on row III, one per uncle."""
    woman = pb.GENDER_WOMAN
    people = [_ghost_ind(1, 1), _ghost_ind(1, 2, woman), _ghost_ind(2, 1), _ghost_ind(2, 2), _ghost_ind(2, 3, woman)]
    people += [_ghost_ind(2, 4), _ghost_ind(3, 1, woman)]
    kids: tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]] = ((), ())
    if with_children:
        people += [_ghost_ind(4, 1), _ghost_ind(4, 2, woman)]
        kids = (((4, 1),), ((4, 2),))
    matings = [
        _ghost_mating((1, 1), (1, 2), (2, 1), (2, 2), (2, 4)),
        _ghost_mating((2, 2), (2, 3), (3, 1)),
        _ghost_mating((2, 1), (3, 1), *kids[0], consang=True),
        _ghost_mating((2, 4), (3, 1), *kids[1], consang=True),
    ]
    return pb.Pedigree(individuals=people, matings=matings)


def _uncle_niece_twice() -> pb.Pedigree:
    """Uncle II-1 and niece III-1 with two matings (children IV-1, IV-2): two ghosts of II-1 marrying III-1."""
    woman = pb.GENDER_WOMAN
    people = [_ghost_ind(1, 1), _ghost_ind(1, 2, woman), _ghost_ind(2, 1), _ghost_ind(2, 2), _ghost_ind(2, 3, woman)]
    people += [_ghost_ind(3, 1, woman), _ghost_ind(4, 1), _ghost_ind(4, 2)]
    matings = [
        _ghost_mating((1, 1), (1, 2), (2, 1), (2, 2)),
        _ghost_mating((2, 2), (2, 3), (3, 1)),
        _ghost_mating((2, 1), (3, 1), (4, 1), consang=True),
        _ghost_mating((2, 1), (3, 1), (4, 2), consang=True),
    ]
    return pb.Pedigree(individuals=people, matings=matings)


def _uncle_niece_repeated(*children: tuple[tuple[int, int], ...], childless: tuple[int, ...] = ()) -> pb.Pedigree:
    """Uncle II-1 and niece III-1 with one mating per entry of ``children`` (``()`` for a childless one).

    ``childless`` gives each childless mating's ``Childlessness``; the second of them lists the niece as ``partner_a``,
    so only the partner order and the childlessness tell the repeats apart.
    """
    woman = pb.GENDER_WOMAN
    people = [_ghost_ind(1, 1), _ghost_ind(1, 2, woman), _ghost_ind(2, 1), _ghost_ind(2, 2), _ghost_ind(2, 3, woman)]
    people += [_ghost_ind(3, 1, woman)] + [_ghost_ind(g, i) for kids in children for g, i in kids]
    matings = [_ghost_mating((1, 1), (1, 2), (2, 1), (2, 2)), _ghost_mating((2, 2), (2, 3), (3, 1))]
    reasons = iter(childless)
    for kids in children:
        m = _ghost_mating((2, 1), (3, 1), *kids, consang=True)
        if not kids and (reason := next(reasons, None)) is not None:
            m.childlessness = reason  # type: ignore[assignment]
            if reason == pb.CHILDLESSNESS_INFERTILITY:
                m.partner_a.CopyFrom(pb.Position(generation=3, index=1))
                m.partner_b.CopyFrom(pb.Position(generation=2, index=1))
        matings.append(m)
    return pb.Pedigree(individuals=people, matings=matings)


GHOST_PEDIGREES = {
    "niece_two_uncles": _niece_two_uncles(with_children=False),
    "niece_two_uncles_children": _niece_two_uncles(with_children=True),
    "uncle_niece_twice": _uncle_niece_twice(),
    "uncle_niece_with_and_without_children": _uncle_niece_repeated(((4, 1),), ()),
    "uncle_niece_three_times": _uncle_niece_repeated(((4, 1),), ((4, 2),), ()),
    "uncle_niece_childless_twice": _uncle_niece_repeated(
        (), (), childless=(pb.CHILDLESSNESS_BY_CHOICE, pb.CHILDLESSNESS_INFERTILITY)
    ),
}


@pytest.mark.parametrize("name", sorted(GHOST_PEDIGREES))
def test_ghost_drawing_is_independent_of_mating_order(name: str) -> None:
    # A ghost's identity feeds the ordering's tie-breaks, so it must come from the join it is drawn for, never from
    # its mating's input position: every permutation of the matings draws the same bytes.
    p = GHOST_PEDIGREES[name]
    assert render.layout(p).ghost_of
    svgs = set()
    for perm in itertools.permutations(p.matings):
        q = pb.Pedigree()
        q.CopyFrom(p)
        del q.matings[:]
        q.matings.extend(perm)
        svgs.add(render.render_svg(q))
    assert len(svgs) == 1


@pytest.mark.parametrize("name", sorted(GHOST_PEDIGREES))
def test_ghost_drawing_is_independent_of_partner_order(name: str) -> None:
    # Which partner a mating lists first is not meaning: every combination of swapped partners draws the same bytes.
    p = GHOST_PEDIGREES[name]
    couples = [k for k, m in enumerate(p.matings) if m.HasField("partner_b")]
    svgs = set()
    for flips in itertools.product((False, True), repeat=len(couples)):
        q = pb.Pedigree()
        q.CopyFrom(p)
        for k, flip in zip(couples, flips, strict=True):
            if flip:
                a = pb.Position()
                a.CopyFrom(q.matings[k].partner_a)
                q.matings[k].partner_a.CopyFrom(q.matings[k].partner_b)
                q.matings[k].partner_b.CopyFrom(a)
        svgs.add(render.render_svg(q))
    assert len(svgs) == 1


def _two_condition_carriers() -> pb.Pedigree:
    """Two carriers of different named conditions, listed so input order and Position order disagree."""

    def carrier(i: int, name: str) -> pb.Individual:
        ind = _ghost_ind(1, i)
        ind.conditions.add(name=name, status=pb.CONDITION_STATUS_CARRIER)
        return ind

    return pb.Pedigree(individuals=[carrier(2, "beta"), carrier(1, "alpha")])


@pytest.mark.parametrize("name", [*_DRAWABLE, *sorted(GHOST_PEDIGREES), "two_condition_carriers"])
def test_render_is_shuffle_invariant(name: str) -> None:
    # Everything drawn depends on the pedigree, not on how the IR lists it: the whole SVG, including the condition
    # legend that keys each carrier's fill region, is byte-identical under a shuffle.
    if name in GHOST_PEDIGREES:
        p = GHOST_PEDIGREES[name]
    elif name == "two_condition_carriers":
        p = _two_condition_carriers()
    else:
        p = test_render._load(name)
    ref = render.render_svg(p)
    for seed in range(4):
        assert render.render_svg(_shuffled(p, seed)) == ref


def _two_nieces(offset: int) -> pb.Pedigree:
    """Uncle II-1 marries nieces III-1 and III-2 (two ghosts on row III); ``offset`` is added to row III's indexes."""
    woman = pb.GENDER_WOMAN
    niece = [(3, 1 + offset), (3, 2 + offset)]
    people = [_ghost_ind(1, 1), _ghost_ind(1, 2, woman), _ghost_ind(2, 1), _ghost_ind(2, 2), _ghost_ind(2, 3, woman)]
    people += [_ghost_ind(2, 4), _ghost_ind(*niece[0], woman), _ghost_ind(*niece[1], woman)]
    people += [_ghost_ind(4, 1), _ghost_ind(4, 2)]
    matings = [
        _ghost_mating((1, 1), (1, 2), (2, 1), (2, 2), (2, 4)),
        _ghost_mating((2, 2), (2, 3), *niece),
        _ghost_mating((2, 1), niece[0], (4, 1), consang=True),
        _ghost_mating((2, 1), niece[1], (4, 2), consang=True),
    ]
    return pb.Pedigree(individuals=people, matings=matings)


def test_no_valid_index_reorders_a_ghost() -> None:
    # A ghost's identity is tagged, not a large number, so a real index anywhere in the valid range (up to 2**31 - 1)
    # neither collides with it nor changes where it stands.
    def arrangement(p: pb.Pedigree) -> list[list[str]]:
        lay = render.layout(p)
        return [["ghost" if i in lay.ghost_of else f"{p.individuals[i].generation}" for i in row] for row in lay.nid]

    ref = arrangement(_two_nieces(0))
    for offset in (999_999_999, 1_000_000_000, 2**31 - 3):
        assert arrangement(_two_nieces(offset)) == ref
