"""Tests for grus.render: golden SVGs, layout invariants, and tier-1 deferral.

Two complementary checks (docs/plans/03-renderer-tier1.md):

* **Golden SVGs** — each hand-authored IR in ``tests/goldens/*.pbtxt`` renders to a committed
  ``*.svg``; we assert byte-equality so any layout or glyph change is a deliberate, reviewable diff.
  Regenerate them after an intended change with ``GRUS_REGEN_GOLDENS=1 uv run pytest tests/test_render.py``.
* **Structural invariants** — cheaper than goldens and robust to spacing tweaks: generations are
  monotonic rows, no two symbols overlap, children sit within their parents' sib-bar span, couples
  are adjacent, and no two label lines on a shared baseline overlap (the node pitch widens for label
  width). These hold for every golden regardless of the exact constants.

Consanguinity loops and other non-tier-1 topologies must raise ``DeferredFeatureError`` (detect and
defer), never be mislaid out.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import pathlib
import re

import pytest

from grus import ir, render
from grus.models import pedigree_pb2 as pb
from grus.render import _draw, _labels

_GOLDENS = pathlib.Path(__file__).parent / "goldens"
_NAMES = sorted(path.stem for path in _GOLDENS.glob("*.pbtxt"))
_REGEN = os.environ.get("GRUS_REGEN_GOLDENS") == "1"
_EPS = 1e-9


def _load(name: str) -> pb.Pedigree:
    return ir.load_pbtxt((_GOLDENS / f"{name}.pbtxt").read_text())


def _position_id(pos: pb.Position) -> str:
    """The drawn position id of ``pos`` (``"III-2"``), as the SVG's data attributes name it."""
    return f"{_labels.roman(pos.generation)}-{pos.index}"


def _coords(lay: render.Layout) -> dict[int, tuple[int, int, float]]:
    """Individual index -> (level, column, x)."""
    out: dict[int, tuple[int, int, float]] = {}
    for level, row in enumerate(lay.nid):
        for col, idx in enumerate(row):
            out[idx] = (level, col, lay.pos[level][col])
    return out


def _pos(pos: pb.Position) -> tuple[int, int]:
    return (pos.generation, pos.index)


def _index(p: pb.Pedigree) -> dict[tuple[int, int], int]:
    return {(ind.generation, ind.index): i for i, ind in enumerate(p.individuals)}


def _ind(g: int, i: int) -> pb.Individual:
    """A gender-unknown individual at (g, i) — for loop fixtures where only the topology matters."""
    return pb.Individual(generation=g, index=i, gender=pb.GENDER_UNKNOWN)


_TEXT_RE = re.compile(
    r'<text (?:class="[^"]*" )?x="([-0-9.]+)" y="([-0-9.]+)" font-family="[^"]*" '
    r'font-size="([-0-9.]+)"[^>]*>([^<]*)</text>'
)
# The symbol outline (one per drawn individual); the backing, fill and hit parts share its geometry.
_SYMBOL_CIRCLE_RE = re.compile(r'<circle class="symbol" cx="([-0-9.]+)" cy="([-0-9.]+)" r="([-0-9.]+)"')
_SYMBOL_RECT_RE = re.compile(r'<rect class="symbol" x="([-0-9.]+)" y="([-0-9.]+)"')
# Drawn (non-hit) elements: the invisible pointer targets carry class="hit" and are not glyphs.
_GLYPH_LINE_RE = re.compile(r'<line (?!class="hit")(?:class="[^"]*" )?x1="([-0-9.]+)" y1="[-0-9.]+" x2="([-0-9.]+)"')
_GLYPH_RECT_RE = re.compile(r'<rect (?!class="hit")(?:class="[^"]*" )?(?:data-condition="\d+" )?x="([-0-9.]+)"')
_GLYPH_CIRCLE_RE = re.compile(r'<circle (?:class="[^"]*" )?cx="([-0-9.]+)" cy="[-0-9.]+" r="([-0-9.]+)"')
_GLYPH_POLYGON_RE = re.compile(r'<polygon (?:class="[^"]*" )?points="([^"]+)"')
_WIDTH_RE = re.compile(r'<svg [^>]*\bwidth="([-0-9.]+)"')


def _texts(svg: str) -> list[tuple[float, float, float, str]]:
    """Every ``<text>`` in the SVG as ``(x, y, font_size, content)``."""
    return [(float(x), float(y), float(sz), s) for x, y, sz, s in _TEXT_RE.findall(svg)]


def _label_lines(svg: str) -> list[tuple[float, float, str]]:
    """Just the label-stack lines (font size == label_size): ``(x, y, content)``."""
    size = render.DEFAULT_GEOMETRY.label_size
    return [(x, y, s) for x, y, sz, s in _texts(svg) if sz == size]


_LABEL_RE = re.compile(
    r'<text class="label" x="([-0-9.]+)" y="([-0-9.]+)" [^>]*text-anchor="(middle|start|end)"[^>]*>([^<]*)</text>'
)


def _label_spans(svg: str) -> list[tuple[float, float, float, str]]:
    """Each label line's estimated horizontal extent: ``(left, right, y, content)``, by its text anchor."""
    size = render.DEFAULT_GEOMETRY.label_size
    out: list[tuple[float, float, float, str]] = []
    for x, y, anchor, s in _LABEL_RE.findall(svg):
        w = 0.6 * size * len(s)
        left = {"start": float(x), "end": float(x) - w}.get(anchor, float(x) - w / 2)
        out.append((left, left + w, float(y), s))
    return out


_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")


def _markers(svg: str) -> list[tuple[float, float, float, str]]:
    """Generation-marker texts (a bare Roman numeral, no id hyphen): ``(x, y, font_size, content)``."""
    return [(x, y, sz, s) for x, y, sz, s in _texts(svg) if _ROMAN_RE.match(s)]


def _symbol_center_ys(svg: str) -> set[float]:
    """Distinct y of each symbol's centre (circle ``cy``; rect top + half)."""
    half = render.DEFAULT_GEOMETRY.symbol_size / 2
    ys = {round(float(cy), 3) for _cx, cy, _r in _SYMBOL_CIRCLE_RE.findall(svg)}
    ys |= {round(float(y) + half, 3) for _x, y in _SYMBOL_RECT_RE.findall(svg)}
    return ys


def _min_glyph_x(svg: str) -> float:
    """Leftmost x touched by any symbol / connector / arrow (every non-``<text>`` element)."""
    xs: list[float] = []
    for x1, x2 in _GLYPH_LINE_RE.findall(svg):
        xs += [float(x1), float(x2)]
    xs += [float(x) for x in _GLYPH_RECT_RE.findall(svg)]
    for cx, r in _GLYPH_CIRCLE_RE.findall(svg):
        xs.append(float(cx) - float(r))
    for pts in _GLYPH_POLYGON_RE.findall(svg):
        xs += [float(p.split(",")[0]) for p in pts.split()]
    return min(xs)


# --- golden SVGs ----------------------------------------------------------------------------------


def test_golden_set_is_non_empty() -> None:
    assert _NAMES, "no golden IRs found under tests/goldens/"


@pytest.mark.parametrize("name", _NAMES)
def test_golden_svg(name: str) -> None:
    svg = render.render_svg(_load(name))
    path = _GOLDENS / f"{name}.svg"
    if _REGEN:
        path.write_text(svg)
    assert path.exists(), f"missing golden {path.name}; regenerate with GRUS_REGEN_GOLDENS=1"
    assert svg == path.read_text()


@pytest.mark.parametrize("name", _NAMES)
def test_render_is_well_formed_svg(name: str) -> None:
    svg = render.render_svg(_load(name))
    assert svg.startswith("<svg ")
    assert svg.rstrip().endswith("</svg>")
    assert "viewBox" in svg


@pytest.mark.parametrize("name", _NAMES)
def test_render_is_deterministic(name: str) -> None:
    p = _load(name)
    assert render.render_svg(p) == render.render_svg(p)
    assert render.layout(p) == render.layout(p)


# --- structural invariants (robust to spacing) ----------------------------------------------------


@pytest.mark.parametrize("name", _NAMES)
def test_rows_are_ir_generations(name: str) -> None:
    # The row is the IR generation (the drawn row), offset so the first generation is level 0 — not a depth
    # computed from the matings, so a detached branch and a child drawn rows below its parents keep theirs.
    p = _load(name)
    lay = render.layout(p)
    at = _coords(lay)
    for i, ind in enumerate(p.individuals):
        assert at[i][0] == ind.generation - lay.first_generation


@pytest.mark.parametrize("name", _NAMES)
def test_no_two_symbols_overlap(name: str) -> None:
    lay = render.layout(_load(name))
    for row in lay.pos:
        for a, b in itertools.pairwise(sorted(row)):
            assert b - a >= render.DEFAULT_GEOMETRY.couple_gap - _EPS


@pytest.mark.parametrize("name", _NAMES)
def test_couples_are_adjacent(name: str) -> None:
    p = _load(name)
    at = _coords(render.layout(p))
    idx = _index(p)
    for m in p.matings:
        if not m.HasField("partner_b"):
            continue  # a lone single parent is not a couple
        pa, pb_ = at[idx[_pos(m.partner_a)]], at[idx[_pos(m.partner_b)]]
        assert pa[0] == pb_[0], "partners must share a row"
        assert abs(pa[1] - pb_[1]) == 1, "partners must be adjacent columns"


@pytest.mark.parametrize("name", _NAMES)
def test_children_lie_within_parent_span(name: str) -> None:
    p = _load(name)
    lay = render.layout(p)
    svg = render.render_svg(p)
    groups = {
        frozenset(kids.split()): body
        for kids, body in re.findall(r'<g class="sibship"[^>]*data-children="([^"]*)">(.*?)</g>', svg, re.S)
    }
    at = _coords(lay)
    idx = _index(p)
    for m in p.matings:
        if not m.offspring or not m.HasField("partner_a"):
            continue  # a founder sibship centres under an implied hanger, not drawn parents
        parent_x = [at[idx[_pos(m.partner_a)]][2]]
        if m.HasField("partner_b"):
            parent_x.append(at[idx[_pos(m.partner_b)]][2])
        elif lay.descends_from_one(*at[idx[_pos(m.offspring[0].child)]][:2]):
            pass  # a count-collapsed (or sideless) lone parent's descent drops from its own centre
        else:  # a lone parent's descent drops from its line to the omitted partner, the phantom beside it
            level, col, _ = at[idx[_pos(m.partner_a)]]
            mate = [c for c in (col - 1, col + 1) if 0 <= c < lay.n[level] and lay.nid[level][c] in lay.phantom]
            assert mate, f"{name}: lone parent {_pos(m.partner_a)} has no phantom partner"
            partner_of = {lay.nid[level][c]: c for c in mate}
            kid_level, kid_col, _ = at[idx[_pos(m.offspring[0].child)]]
            pc = lay.fam[kid_level][kid_col]
            phantom_col = pc + 1 if pc == col else pc
            assert lay.nid[level][phantom_col] in partner_of
            parent_x.append(lay.pos[level][phantom_col])
        midpoint = sum(parent_x) / len(parent_x)
        child_x = [at[idx[_pos(o.child)]][2] for o in m.offspring]
        meet = _draw._MEET_EPS  # the drawer's tolerance: a midpoint of two rounded positions may sit a quantum off
        if min(child_x) - meet <= midpoint <= max(child_x) + meet:
            continue
        # Off its bar only where the order forces it (a crossing descent), and then drawn with an elbow, never
        # along another bar.
        children = frozenset(_position_id(o.child) for o in m.offspring)
        assert '<path d="M' in groups[children], f"{name}: the descent to {sorted(children)} misses its bar, no elbow"


@pytest.mark.parametrize("name", _NAMES)
def test_label_lines_do_not_overlap(name: str) -> None:
    # The node pitch widens for label width, so no two label lines sharing a baseline collide. Estimate
    # each line's drawn width with the renderer's own heuristic (0.6*label_size px per character) and
    # require the boxes of consecutive lines on a row to clear each other.
    rows: dict[float, list[tuple[float, float, str]]] = {}
    svg = render.render_svg(_load(name))
    for left, right, y, s in _label_spans(svg):
        rows.setdefault(round(y, 3), []).append((left, right, s))
    assert sum(len(row) for row in rows.values()) == len(_label_lines(svg))
    for row in rows.values():
        row.sort()
        for (_, right_of_left, s1), (left_of_right, _, s2) in itertools.pairwise(row):
            assert left_of_right - right_of_left >= -_EPS, f"labels {s1!r} and {s2!r} overlap in {name}"


def test_wide_label_widens_only_adjacent_columns() -> None:
    # Label-driven widening is local: a wide annotation on one node opens only the gaps whose labels
    # would collide, not the whole figure's column pitch. Four founder siblings, all short-labelled but
    # the second carrying a long annotation -> the gaps beside it widen, the far C-D gap keeps the
    # geometric sib pitch.
    g = render.DEFAULT_GEOMETRY
    floor = g.sib_gap * g.x_unit
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(
                generation=1,
                index=2,
                gender=pb.GENDER_MAN,
                annotations=[pb.Annotation(text="X" * 40, type=pb.ANNOTATION_TYPE_OTHER)],
            ),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_MAN),
        ],
        matings=[pb.Mating(offspring=[pb.Offspring(child=pb.Position(generation=1, index=i)) for i in (1, 2, 3, 4)])],
    )
    svg = render.render_svg(p)
    xs = sorted(float(x) for x, _y in _SYMBOL_RECT_RE.findall(svg))  # men -> squares, left to right
    a, b, c, d = xs
    assert abs((d - c) - floor) < _EPS, "columns away from the wide label keep the geometric sib pitch"
    assert b - a > floor + _EPS and c - b > floor + _EPS, "the gaps beside the wide label widen to clear it"


def test_wide_edge_label_is_not_clipped() -> None:
    # An outermost label wider than its symbol sets the canvas edge, not the symbol: the drawing shifts
    # and the width grows so the label stays inside the viewBox (a centred label can overhang the symbol
    # far past the margin).
    p = pb.Pedigree(
        individuals=[
            pb.Individual(
                generation=1,
                index=1,
                gender=pb.GENDER_WOMAN,
                annotations=[pb.Annotation(text="X" * 50, type=pb.ANNOTATION_TYPE_OTHER)],
            ),
        ],
    )
    svg = render.render_svg(p)
    m = _WIDTH_RE.search(svg)
    assert m is not None
    width = float(m.group(1))
    size = render.DEFAULT_GEOMETRY.label_size
    for x, _y, s in _label_lines(svg):
        half_w = 0.6 * size * len(s) / 2
        assert x - half_w >= -_EPS, f"label {s!r} clips off the left edge"
        assert x + half_w <= width + _EPS, f"label {s!r} clips off the right edge (width {width})"


def test_childless_couple_draws_the_bennett_glyph() -> None:
    # A childless mating (no offspring) hangs a stub + bar below the couple: one bar for by-choice, two
    # for infertility. The layout records the value on the couple's left column; drawing emits the bars.
    p = _load("childless")
    lay = render.layout(p)
    drawn = [v for row in lay.childless for v in row if v]
    assert sorted(drawn) == [
        int(pb.CHILDLESSNESS_BY_CHOICE),
        int(pb.CHILDLESSNESS_INFERTILITY),
    ], "both childless couples are recorded on their left column"
    # A fertile couple records nothing.
    fertile = render.layout(_load("trio")).childless
    assert not any(v for row in fertile for v in row), "a couple with children draws no childless glyph"


def test_carrier_glyph_depends_on_inheritance() -> None:
    # The carrier glyph is chosen by the condition's inheritance: a half-filled symbol (clipped to the shape)
    # for autosomal recessive, a central dot for X-linked; an unspecified carrier defaults to the AR half-fill.
    ar = render.render_svg(
        _one(pb.Condition(status=pb.CONDITION_STATUS_CARRIER, inheritance=pb.INHERITANCE_AUTOSOMAL_RECESSIVE))
    )
    assert "<clipPath" in ar and 'clip-path="url(#' in ar, "an AR carrier is a shape-clipped half-fill"
    xl = render.render_svg(
        _one(pb.Condition(status=pb.CONDITION_STATUS_CARRIER, inheritance=pb.INHERITANCE_X_LINKED_RECESSIVE))
    )
    assert "<clipPath" not in xl and 'r="' in xl, "an X-linked carrier is a central dot, not a half-fill"
    default = render.render_svg(_one(pb.Condition(status=pb.CONDITION_STATUS_CARRIER)))
    assert "<clipPath" in default, "an unspecified-inheritance carrier defaults to the AR half-fill"


def _one(cond: pb.Condition) -> pb.Pedigree:
    return pb.Pedigree(individuals=[pb.Individual(generation=1, index=1, gender=pb.GENDER_WOMAN, conditions=[cond])])


def test_carrier_style_toggle_controls_the_x_linked_glyph() -> None:
    # The carrier convention is a render option, not IR meaning. An X-linked carrier is a central dot under
    # the default INHERITANCE_GLYPH style, but a shape-clipped region fill (dot retired) under PARTITION_FILL.
    import dataclasses

    xl = _one(pb.Condition(status=pb.CONDITION_STATUS_CARRIER, inheritance=pb.INHERITANCE_X_LINKED_RECESSIVE))
    default = render.render_svg(xl)  # DEFAULT_GEOMETRY -> INHERITANCE_GLYPH
    assert "<clipPath" not in default, "default (existing-literature) X-linked carrier is a central dot"
    partition = render.render_svg(
        xl, dataclasses.replace(render.DEFAULT_GEOMETRY, carrier_style=render.CarrierStyle.PARTITION_FILL)
    )
    assert "<clipPath" in partition, "under 2022 PARTITION_FILL the X-linked carrier is a region fill, not a dot"


def test_carrier_fill_keyed_to_named_variant() -> None:
    # The filled half is keyed to WHICH condition the carrier carries: two carriers of different named
    # variants fill opposite halves (a compound het reads as opposite sides), two of the same variant share
    # a side. So the second carrier's fill shifts right by a half-symbol when its variant differs.
    def two(name_a: str, name_b: str) -> pb.Pedigree:
        def c(i: int, name: str) -> pb.Individual:
            return pb.Individual(
                generation=1,
                index=i,
                gender=pb.GENDER_WOMAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_CARRIER, name=name)],
            )

        return pb.Pedigree(individuals=[c(1, name_a), c(2, name_b)])

    def rect_xs(svg: str) -> list[float]:
        return sorted(float(x) for x in re.findall(r'<rect class="fill" data-condition="\d+" x="([-0-9.]+)"', svg))

    different = rect_xs(render.render_svg(two("varA", "varB")))
    same = rect_xs(render.render_svg(two("varA", "varA")))
    assert max(different) - max(same) == render.DEFAULT_GEOMETRY.symbol_size / 2, (
        "a carrier of a different variant fills the opposite half"
    )


# --- deferral: detect, do not mislay out ----------------------------------------------------------


def _cousin_marriage_loop() -> pb.Pedigree:
    """Two children of one founder couple each have a child; those cousins marry -> a loop."""
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=2, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=3, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=3, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=4, index=1, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1)),
                    pb.Offspring(child=pb.Position(generation=2, index=3)),
                ],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=3),
                partner_b=pb.Position(generation=2, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=3, index=1),
                partner_b=pb.Position(generation=3, index=2),
                consanguineous=True,
                offspring=[pb.Offspring(child=pb.Position(generation=4, index=1))],
            ),
        ],
    )


def _avuncular_loop() -> pb.Pedigree:
    """Uncle II-1 marries his niece III-1 (child of his sib II-2) — a loop whose join spans two rows."""
    return pb.Pedigree(
        individuals=[_ind(1, 1), _ind(1, 2), _ind(2, 1), _ind(2, 2), _ind(2, 3), _ind(3, 1), _ind(4, 1)],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1)),
                    pb.Offspring(child=pb.Position(generation=2, index=2)),
                ],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=2),
                partner_b=pb.Position(generation=2, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=3, index=1),
                consanguineous=True,
                offspring=[pb.Offspring(child=pb.Position(generation=4, index=1))],
            ),
        ],
    )


def _double_cousin_loop() -> pb.Pedigree:
    """Two founder couples cross-marry both ways, then those children marry; the join defers.

    II-1xII-3 and II-2xII-4, then III-1 x III-2 — two joins the second pass can't make adjacent, so it defers
    (partners not adjacent).
    """
    return pb.Pedigree(
        individuals=[_ind(1, i) for i in (1, 2, 3, 4)]
        + [_ind(2, i) for i in (1, 2, 3, 4)]
        + [_ind(3, 1), _ind(3, 2), _ind(4, 1)],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1)),
                    pb.Offspring(child=pb.Position(generation=2, index=2)),
                ],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=3)),
                    pb.Offspring(child=pb.Position(generation=2, index=4)),
                ],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=2),
                partner_b=pb.Position(generation=2, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=3, index=1),
                partner_b=pb.Position(generation=3, index=2),
                consanguineous=True,
                offspring=[pb.Offspring(child=pb.Position(generation=4, index=1))],
            ),
        ],
    )


def test_cousin_marriage_loop_lays_out() -> None:
    """A first-cousin marriage lays out as an adjacent, doubled couple with their child below.

    The loop is closed solely by that cross-mating. Harder loops defer.
    """
    p = _cousin_marriage_loop()
    lay = render.layout(p)  # must not raise: the ordering draws the loop's cross-mating as an adjacent couple
    assert render.render_svg(p).startswith("<svg ")
    at, idx = _coords(lay), _index(p)
    c1, c2 = at[idx[(3, 1)]], at[idx[(3, 2)]]  # the marrying first cousins
    assert c1[0] == c2[0] and abs(c1[1] - c2[1]) == 1  # adjacent couple on one row
    assert lay.spouse[c1[0]][min(c1[1], c2[1])] == 2  # drawn consanguineous (doubled mating line)
    child = at[idx[(4, 1)]]
    assert child[0] == c1[0] + 1 and min(c1[2], c2[2]) < child[2] < max(c1[2], c2[2])  # child hangs between


def test_avuncular_loop_duplicates_the_cross_generation_partner() -> None:
    """An uncle-niece marriage lays out by duplicating the shallower partner down to the niece's row.

    The partners are on different IR generations: the uncle is drawn on his own row and again as a ghost beside
    the niece, the two joined by a dashed 'same individual' link, with their child below the ghost couple.
    """
    p = _avuncular_loop()
    lay = render.layout(p)  # was a deferral; now drawn via duplication
    assert len(lay.ghost_of) == 1
    ghost, real = next(iter(lay.ghost_of.items()))
    assert real == _index(p)[(2, 1)]  # the uncle (II-1) is the duplicated (shallower) partner
    at = _coords(lay)
    niece = at[_index(p)[(3, 1)]]
    gcell = at[ghost]
    assert gcell[0] == niece[0] and abs(gcell[1] - niece[1]) == 1  # ghost sits beside the niece, same row
    assert at[real][0] == niece[0] - 1  # the real uncle stays one generation up
    child = at[_index(p)[(4, 1)]]
    assert child[0] == niece[0] + 1  # the couple's child hangs one row below
    svg = render.render_svg(p)
    assert svg.startswith("<svg ") and svg.count("stroke-dasharray") >= 1  # a dashed same-individual link


def test_double_cousin_loop_still_defers() -> None:
    """Double first cousins still defer to a placeholder, never a crossing or crash.

    An interlocking loop the ordering cannot open as adjacent couples without tearing a sibship; the
    torn-sibship backstop fires.
    """
    with pytest.raises(render.DeferredFeatureError, match="sib bars would overlap"):
        render.render_svg(_double_cousin_loop())


def _same_generation_split_join() -> pb.Pedigree:
    """The c16 shape: same-generation cousins whose lineages have unequal length.

    Both are gen III, so ``kindepth`` puts them on different computed levels and packing leaves them far apart.
    III-1 descends from a founder couple II-3 x II-4 drawn at generation II (a short lineage), III-2 from the
    longer I-1 -> II-2 -> III-2 line and heads a sibship {III-2, III-3, III-4}. Generation-aware alignment
    pulls III-1's lineage down to row III; relocation then slides III-1's separable birth block over so the
    cousins sit as an adjacent couple with their child IV-1 below.
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),  # marry-in
            pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),  # born-in (I-1 x I-2)
            pb.Individual(generation=2, index=3, gender=pb.GENDER_MAN),  # founder (short lineage)
            pb.Individual(generation=2, index=4, gender=pb.GENDER_WOMAN),  # founder
            pb.Individual(generation=3, index=1, gender=pb.GENDER_MAN),  # cross partner (short lineage)
            pb.Individual(generation=3, index=2, gender=pb.GENDER_WOMAN),  # cross partner (heads a sibship)
            pb.Individual(generation=3, index=3, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=3, index=4, gender=pb.GENDER_MAN),
            pb.Individual(generation=4, index=1, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=i)) for i in (2, 3, 4)],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=3),
                partner_b=pb.Position(generation=2, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(  # the same-generation cross join whose partners pack non-adjacent
                partner_a=pb.Position(generation=3, index=1),
                partner_b=pb.Position(generation=3, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=4, index=1))],
            ),
        ],
    )


def test_same_generation_split_join_relocates() -> None:
    """A same-generation cross join split by kindepth aligns to one row and relocates (the c16 shape).

    The cousins end as an adjacent couple, their child below — drawn once, no duplicate.
    """
    p = _same_generation_split_join()
    lay = render.layout(p)  # must not raise
    assert render.render_svg(p).startswith("<svg ")
    at, idx = _coords(lay), _index(p)
    c1, c2 = at[idx[(3, 1)]], at[idx[(3, 2)]]
    assert c1[0] == c2[0] and abs(c1[1] - c2[1]) == 1  # same row, adjacent columns
    assert c1[0] == at[idx[(3, 3)]][0] == at[idx[(2, 2)]][0] + 1  # cousins on row III, one below gen II
    child = at[idx[(4, 1)]]
    assert child[0] == c1[0] + 1 and min(c1[2], c2[2]) - _EPS <= child[2] <= max(c1[2], c2[2]) + _EPS


def test_more_than_two_matings_is_deferred() -> None:
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),  # A: shared across three matings
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),  # W1
            pb.Individual(generation=1, index=3, gender=pb.GENDER_WOMAN),  # W2
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),  # W3
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),  # C1
            pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),  # C2
            pb.Individual(generation=2, index=3, gender=pb.GENDER_MAN),  # C3
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=3))],
            ),
        ],
    )
    # A >2-mate individual routes its overflow mating (design), but routing a mating that HAS offspring is not
    # yet drawn — its child's descent hangs from a non-adjacent parent pair, which _build cannot express — so
    # this shape (every mating bears a child) still defers rather than mislay the descent out.
    with pytest.raises(render.DeferredFeatureError, match="not an adjacent couple"):
        render.layout(p)


def test_child_of_two_matings_is_deferred() -> None:
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),  # A
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),  # B
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),  # C
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),  # D
            pb.Individual(generation=2, index=1, gender=pb.GENDER_UNKNOWN),  # X: child of two matings
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
        ],
    )
    with pytest.raises(render.DeferredFeatureError, match="more than one mating"):
        render.layout(p)


def _two_parented_marriage() -> pb.Pedigree:
    """Two unrelated founder couples each have a child; the two children marry.

    A non-consanguineous join of two drawn lineages. Both partners of the II-1 x II-2 mating are born-in — each
    already anchored by its own founder subtree — so the single-mate recursion has no marry-in to hang the couple
    on. This is the exact shape that crashed ``render_svg`` with a bare ``KeyError`` (in ``_centroid``, on the
    child the second lineage could no longer place) on real extracted figures; it must now defer cleanly.
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=3, index=1, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
        ],
    )


def _two_parented_marriage_with_extra_mate() -> pb.Pedigree:
    """The two-lineage join where a born-in partner (II-2) also has a second, marry-in mate (II-4).

    The born-in x born-in mating (II-2 x II-3) is refused before the extra mate matters — the same
    KeyError shape, reached via the spouse-hinge redirect rather than a plain sibship walk.
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=2, gender=pb.GENDER_MAN),
            pb.Individual(generation=2, index=3, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=4, gender=pb.GENDER_MAN),
            pb.Individual(generation=3, index=1, gender=pb.GENDER_UNKNOWN),
            pb.Individual(generation=3, index=2, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=3))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=2),
                partner_b=pb.Position(generation=2, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=3),
                partner_b=pb.Position(generation=2, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=2))],
            ),
        ],
    )


def _two_lineage_join_not_adjacent() -> pb.Pedigree:
    """Three families A/B/C; A's child (II-1) marries C's child (II-3).

    In birth order B's child (II-2) falls between them; the v2 ordering reorders the families so the two
    born-in partners sit adjacent (v1 could not and deferred).
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=5, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=6, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_WOMAN),  # family A child
            pb.Individual(generation=2, index=2, gender=pb.GENDER_MAN),  # family B child (packs between)
            pb.Individual(generation=2, index=3, gender=pb.GENDER_MAN),  # family C child
            pb.Individual(generation=3, index=1, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=5),
                partner_b=pb.Position(generation=1, index=6),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=3))],
            ),
            pb.Mating(  # A's child x C's child — a join whose partners straddle family B
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
        ],
    )


def _child_marries_multi_mate_founder() -> pb.Pedigree:
    """A born-in child marries a marry-in founder who has a second marriage to another marry-in.

    II-1 x II-2, then II-2 x II-3. Only one partner of each mating is born-in, so the marry-in demotes onto the
    child's row and the single-mate recursion places the couple. A look-alike of the crashing shape (same
    spouse-hinge redirect) that must keep rendering — a guard against the two-lineage rule over-deferring
    supported half-sib pedigrees.
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_WOMAN),  # born-in child
            pb.Individual(generation=2, index=2, gender=pb.GENDER_MAN),  # marry-in with a second mating
            pb.Individual(generation=2, index=3, gender=pb.GENDER_WOMAN),  # the second marry-in
            pb.Individual(generation=3, index=1, gender=pb.GENDER_UNKNOWN),
            pb.Individual(generation=3, index=2, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=2),
                partner_b=pb.Position(generation=2, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=2))],
            ),
        ],
    )


def test_two_parented_marriage_lays_out() -> None:
    """A two-lineage join — a born-in child from each of two families marries — lays out.

    The ordering brings the two born-in partners together as an adjacent couple and their child hangs below.
    """
    p = _two_parented_marriage()
    lay = render.layout(p)  # must not raise: the ordering brings the join's partners adjacent
    assert render.render_svg(p).startswith("<svg ")
    at, idx = _coords(lay), _index(p)
    a, b, child = at[idx[(2, 1)]], at[idx[(2, 2)]], at[idx[(3, 1)]]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1  # the marrying pair: same row, adjacent columns
    assert child[0] == a[0] + 1  # the child is one row below
    assert min(a[2], b[2]) < child[2] < max(a[2], b[2])  # and hangs between the partners


def test_two_parented_marriage_with_extra_mate_lays_out() -> None:
    """The join partner (II-3) also has a marry-in second mate (II-4); both matings lay out.

    The two-lineage join and the half-sib mating coexist — the join pair is an adjacent couple and both children
    are placed.
    """
    p = _two_parented_marriage_with_extra_mate()
    lay = render.layout(p)
    assert render.render_svg(p).startswith("<svg ")
    at, idx = _coords(lay), _index(p)
    a, b = at[idx[(2, 2)]], at[idx[(2, 3)]]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1  # the two-lineage join is an adjacent couple
    assert idx[(3, 1)] in at and idx[(3, 2)] in at  # both children placed, no collision


def test_two_lineage_join_not_adjacent_reorders() -> None:
    """A two-lineage join whose partners packed non-adjacent under v1 now lays out.

    A third family fell between them; the ordering brings A's child and C's child together as an adjacent
    couple, their child below. A's own descent centering yields to the couple pull (a soft-constraint tradeoff
    v1 could not make, so it deferred); the layout is topologically sound — no overlaps, couple adjacent, join
    child within span.
    """
    p = _two_lineage_join_not_adjacent()
    lay = render.layout(p)
    assert render.render_svg(p).startswith("<svg ")
    for row in lay.pos:  # no two symbols overlap
        for x, y in itertools.pairwise(sorted(row)):
            assert y - x >= render.DEFAULT_GEOMETRY.couple_gap - _EPS
    at, idx = _coords(lay), _index(p)
    a, b, child = at[idx[(2, 1)]], at[idx[(2, 3)]], at[idx[(3, 1)]]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1  # A's child x C's child: adjacent couple
    assert child[0] == a[0] + 1 and min(a[2], b[2]) - _EPS <= child[2] <= max(a[2], b[2]) + _EPS


def test_child_of_multi_mate_founder_still_renders() -> None:
    p = _child_marries_multi_mate_founder()
    lay = render.layout(p)  # must not raise: one born-in partner per mating is within tier 1
    assert render.render_svg(p).startswith("<svg ")
    at, idx = _coords(lay), _index(p)
    # the born-in child II-1 and its two-mate spouse II-2 are placed as an adjacent couple
    assert abs(at[idx[(2, 1)]][1] - at[idx[(2, 2)]][1]) == 1


def _cross_join_child_collision() -> pb.Pedigree:
    """A two-lineage join whose children, centred on the couple, would overlap a neighbouring sibship.

    II-1 (born-in) marries a marry-in and heads a three-child sibship on gen III; its sibling II-2 marries
    into the right family (II-2 x II-3, a cross-lineage join) and that couple has three children. Centred
    under the couple midpoint the join's children land on top of II-1's sibship; the second pass slides the
    join's whole child subtree right until it clears (slice 23). No pull is needed — the couple already sits
    at the left edge of the slid sibship.
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_WOMAN),  # born-in, heads a sibship
            pb.Individual(generation=2, index=2, gender=pb.GENDER_MAN),  # born-in, cross partner
            pb.Individual(generation=2, index=3, gender=pb.GENDER_WOMAN),  # born-in (right family), cross partner
            pb.Individual(generation=2, index=4, gender=pb.GENDER_MAN),  # marry-in for II-1
            pb.Individual(generation=3, index=1, gender=pb.GENDER_UNKNOWN),  # II-1 x II-4 sibship (neighbour)
            pb.Individual(generation=3, index=2, gender=pb.GENDER_UNKNOWN),
            pb.Individual(generation=3, index=3, gender=pb.GENDER_UNKNOWN),
            pb.Individual(generation=3, index=4, gender=pb.GENDER_UNKNOWN),  # II-2 x II-3 join children
            pb.Individual(generation=3, index=5, gender=pb.GENDER_UNKNOWN),
            pb.Individual(generation=3, index=6, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1)),
                    pb.Offspring(child=pb.Position(generation=2, index=2)),
                ],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=3))],
            ),
            pb.Mating(  # neighbour sibship the join's children would collide with
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=i)) for i in (1, 2, 3)],
            ),
            pb.Mating(  # the cross-lineage join
                partner_a=pb.Position(generation=2, index=2),
                partner_b=pb.Position(generation=2, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=i)) for i in (4, 5, 6)],
            ),
        ],
    )


def _cross_join_chain(third_family: bool) -> pb.Pedigree:
    """The c07-A shape (gen I-IV): a consanguineous cousin marriage beside a wide sibship.

    III-3 x III-4 sits next to its cousins' sibship, so the join's children must slide right past IV-1..IV-5
    and the couple must be *pulled* right to keep its descent connected. Because III-4 is the single child of
    II-4 x II-3, the pull drags that couple too (the cascade slice 23 must handle).

    With ``third_family`` an unrelated family C is added whose gen-III member packed immediately beside the
    cross couple, which boxed v1's pull in and made it defer; the v2 ordering finds room and lays it out.
    """
    inds = [
        pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
        pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
        pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),  # marry-in
        pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),  # born-in
        pb.Individual(generation=2, index=3, gender=pb.GENDER_WOMAN),  # born-in
        pb.Individual(generation=2, index=4, gender=pb.GENDER_MAN),  # marry-in
        pb.Individual(generation=3, index=1, gender=pb.GENDER_MAN),  # marry-in
        pb.Individual(generation=3, index=2, gender=pb.GENDER_WOMAN),  # born-in
        pb.Individual(generation=3, index=3, gender=pb.GENDER_MAN),  # born-in, cross partner
        pb.Individual(generation=3, index=4, gender=pb.GENDER_WOMAN),  # single child, cross partner
        *(pb.Individual(generation=4, index=i, gender=pb.GENDER_UNKNOWN) for i in range(1, 14)),
    ]
    matings = [
        pb.Mating(
            partner_a=pb.Position(generation=1, index=1),
            partner_b=pb.Position(generation=1, index=2),
            offspring=[
                pb.Offspring(child=pb.Position(generation=2, index=2)),
                pb.Offspring(child=pb.Position(generation=2, index=3)),
            ],
        ),
        pb.Mating(
            partner_a=pb.Position(generation=2, index=1),
            partner_b=pb.Position(generation=2, index=2),
            offspring=[
                pb.Offspring(child=pb.Position(generation=3, index=2)),
                pb.Offspring(child=pb.Position(generation=3, index=3)),
            ],
        ),
        pb.Mating(  # III-4 is a single child -> the pull drags this couple
            partner_a=pb.Position(generation=2, index=4),
            partner_b=pb.Position(generation=2, index=3),
            offspring=[pb.Offspring(child=pb.Position(generation=3, index=4))],
        ),
        pb.Mating(  # neighbour sibship IV-1..IV-5
            partner_a=pb.Position(generation=3, index=1),
            partner_b=pb.Position(generation=3, index=2),
            offspring=[pb.Offspring(child=pb.Position(generation=4, index=i)) for i in range(1, 6)],
        ),
        pb.Mating(  # the cross-lineage cousin join, wide sibship IV-6..IV-13
            partner_a=pb.Position(generation=3, index=3),
            partner_b=pb.Position(generation=3, index=4),
            consanguineous=True,
            offspring=[pb.Offspring(child=pb.Position(generation=4, index=i)) for i in range(6, 14)],
        ),
    ]
    if third_family:
        inds += [
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=5, gender=pb.GENDER_MAN),
            pb.Individual(generation=2, index=6, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=3, index=5, gender=pb.GENDER_UNKNOWN),  # the blocker on gen III
        ]
        matings += [
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=5))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=5),
                partner_b=pb.Position(generation=2, index=6),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=5))],
            ),
        ]
    return pb.Pedigree(individuals=inds, matings=matings)


def _boundary_bridge_join() -> pb.Pedigree:
    """Two families joined at their sibship boundary (the c03 shape).

    Family 1's last child (II-2) marries family 2's first child (II-3); their children III-1/III-2 must hang
    between the families — but family 2's next child II-4 already heads a sibship (III-3/III-4) right there.
    The couple can't be pulled (its right member II-3 is anchored in family 2's sibship), so the second pass
    spreads family 2 rightward to open room.
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),  # family 1 child
            pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),  # family 1 child, bridge partner
            pb.Individual(generation=2, index=3, gender=pb.GENDER_MAN),  # family 2 child, bridge partner
            pb.Individual(generation=2, index=4, gender=pb.GENDER_WOMAN),  # family 2 child, heads neighbour sibship
            pb.Individual(generation=2, index=5, gender=pb.GENDER_MAN),  # family 2 child
            pb.Individual(generation=2, index=6, gender=pb.GENDER_MAN),  # marry-in for II-4
            pb.Individual(generation=3, index=1, gender=pb.GENDER_UNKNOWN),  # bridge children
            pb.Individual(generation=3, index=2, gender=pb.GENDER_UNKNOWN),
            pb.Individual(generation=3, index=3, gender=pb.GENDER_UNKNOWN),  # neighbour sibship
            pb.Individual(generation=3, index=4, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=i)) for i in (1, 2)],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=i)) for i in (3, 4, 5)],
            ),
            pb.Mating(  # the bridge (cross-lineage join at the sibship boundary), consanguineous
                partner_a=pb.Position(generation=2, index=2),
                partner_b=pb.Position(generation=2, index=3),
                consanguineous=True,
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=i)) for i in (1, 2)],
            ),
            pb.Mating(  # family 2's neighbour sibship, right where the bridge children want to go
                partner_a=pb.Position(generation=2, index=4),
                partner_b=pb.Position(generation=2, index=6),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=i)) for i in (3, 4)],
            ),
        ],
    )


def test_boundary_bridge_join_spreads_the_neighbour() -> None:
    """The c03 shape lays out.

    The bridge couple's children hang between the families and the neighbouring sibship is spread right to
    make room (rather than the join deferring as it did before slice 29).
    """
    p = _boundary_bridge_join()
    _assert_join_laid_out(p)  # no overlaps, couples adjacent, every descent midpoint within its sibship span
    at, idx = _coords(render.layout(p)), _index(p)
    a, b = at[idx[(2, 2)]], at[idx[(2, 3)]]
    bridge_kids = [at[idx[(3, i)]] for i in (1, 2)]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1  # bridge couple adjacent
    assert min(a[2], b[2]) - _EPS <= sum(k[2] for k in bridge_kids) / 2 <= max(a[2], b[2]) + _EPS
    neighbour = [at[idx[(3, i)]] for i in (3, 4)]
    assert min(n[2] for n in neighbour) > max(k[2] for k in bridge_kids)  # neighbour spread clear to the right


def _assert_join_laid_out(p: pb.Pedigree) -> None:
    """A cross-join pedigree lays out with the drawing's load-bearing invariants intact."""
    lay = render.layout(p)
    at, idx = _coords(lay), _index(p)
    for row in lay.pos:  # no two symbols overlap
        for a, b in itertools.pairwise(sorted(row)):
            assert b - a >= render.DEFAULT_GEOMETRY.couple_gap - _EPS
    for m in p.matings:  # couples adjacent; every descent midpoint stays within its sibship span
        parents = [at[idx[_pos(m.partner_a)]]]
        if m.HasField("partner_b"):
            parents.append(at[idx[_pos(m.partner_b)]])
            assert parents[0][0] == parents[1][0] and abs(parents[0][1] - parents[1][1]) == 1
        if m.offspring:
            mid = sum(pt[2] for pt in parents) / len(parents)
            child_x = [at[idx[_pos(o.child)]][2] for o in m.offspring]
            assert min(child_x) - _EPS <= mid <= max(child_x) + _EPS
    assert render.render_svg(p).startswith("<svg ")


def test_cross_join_child_collision_slides_clear() -> None:
    """A join whose centred children collide with a neighbour slides them clear and still lays out."""
    p = _cross_join_child_collision()
    _assert_join_laid_out(p)
    at, idx = _coords(render.layout(p)), _index(p)
    kids = [at[idx[(3, i)]] for i in (4, 5, 6)]
    neighbour = [at[idx[(3, i)]] for i in (1, 2, 3)]
    assert min(k[2] for k in kids) > max(n[2] for n in neighbour)  # join children moved clear to the right


def test_cross_join_chain_pulls_couple_and_ancestry() -> None:
    """The c07-A chain lays out.

    The cousin couple is pulled right over its slid sibship and the single-child parent couple is dragged with
    it, keeping both descents connected.
    """
    p = _cross_join_chain(third_family=False)
    _assert_join_laid_out(p)
    at, idx = _coords(render.layout(p)), _index(p)
    # III-4 (single child of II-4 x II-3) stays exactly under its parents' midpoint after the drag
    mid = (at[idx[(2, 4)]][2] + at[idx[(2, 3)]][2]) / 2
    assert abs(at[idx[(3, 4)]][2] - mid) < _EPS


def test_cross_join_chain_with_blocker_lays_out() -> None:
    """The c07-A chain with an extra family packed beside the cross couple lays out.

    That family boxed v1's pull in and made it defer; the ordering places the cousin marriage and its wide
    sibship with no special pull/spread pass, invariants intact.
    """
    _assert_join_laid_out(_cross_join_chain(third_family=True))


def test_cousin_marriage_with_siblings_lays_out() -> None:
    # c05's shape: a first-cousin marriage (3-3 x 3-4, sharing grandparents I-1 x I-2) where BOTH partners
    # have siblings, plus a wide cross-sibship (7 children). v1 had to pull the couple over its kids, tearing
    # each cousin from its sibship (overlapping bars) and deferred. v2's ordering pulls each cousin to its
    # sibship end, so the loop marriage is an ordinary adjacent consanguineous couple — no sibship is torn.
    def pos(g: int, i: int) -> pb.Position:
        return pb.Position(generation=g, index=i)

    def mating(a: tuple[int, int], b: tuple[int, int], kids: list[tuple[int, int]], cons: bool = False) -> pb.Mating:
        return pb.Mating(
            partner_a=pos(*a),
            partner_b=pos(*b),
            consanguineous=cons,
            offspring=[pb.Offspring(child=pos(*k)) for k in kids],
        )

    ids = [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
        (2, 3),
        (2, 4),
        (2, 5),
        (2, 6),
        (3, 1),
        (3, 2),
        (3, 3),
        (3, 4),
        (3, 5),
        (3, 6),
        (3, 7),
        (3, 8),
        (4, 20),
        (4, 21),
    ]
    ids += [(4, i) for i in range(1, 8)]  # the 7 children of the cousin marriage
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=g, index=i, gender=pb.GENDER_MAN if i % 2 else pb.GENDER_WOMAN) for g, i in ids
        ],
        matings=[
            mating((1, 1), (1, 2), [(2, 1), (2, 3), (2, 5)]),
            mating((2, 1), (2, 2), [(3, 1), (3, 2), (3, 3)]),  # 3-3's sibship
            mating((2, 3), (2, 4), [(3, 4), (3, 5), (3, 6)]),  # 3-4's sibship
            mating((2, 5), (2, 6), [(3, 7)]),
            mating((3, 7), (3, 8), [(4, 20), (4, 21)]),  # neighbour descendants the cross-sibship slides past
            mating((3, 3), (3, 4), [(4, i) for i in range(1, 8)], cons=True),  # the cousin marriage
        ],
    )
    _assert_join_laid_out(p)  # no overlaps, couples adjacent, every descent midpoint within its sibship span
    at, idx = _coords(lay := render.layout(p)), _index(p)
    a, b = at[idx[(3, 3)]], at[idx[(3, 4)]]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1, "the marrying cousins are an adjacent couple"
    assert lay.spouse[a[0]][min(a[1], b[1])] == 2, "drawn consanguineous (doubled mating line)"


@pytest.mark.parametrize(
    "p",
    [
        _two_parented_marriage(),
        _two_parented_marriage_with_extra_mate(),
        _two_lineage_join_not_adjacent(),
        _cross_join_child_collision(),
        _cross_join_chain(third_family=False),
        _cross_join_chain(third_family=True),
        _boundary_bridge_join(),
        _same_generation_split_join(),
        _cousin_marriage_loop(),
        _avuncular_loop(),
        _double_cousin_loop(),
        _child_marries_multi_mate_founder(),
        _load("trio"),
        _load("half_sibs"),
    ],
    ids=[
        "two_parented",
        "two_parented_extra_mate",
        "two_lineage_not_adjacent",
        "cross_join_collision",
        "cross_join_chain",
        "cross_join_boxed_in",
        "boundary_bridge_join",
        "same_generation_split_join",
        "cousin_loop",
        "avuncular_loop",
        "double_cousin_loop",
        "child_x_multimate_founder",
        "trio",
        "half_sibs",
    ],
)
def test_renderer_is_total(p: pb.Pedigree) -> None:
    # The renderer's totality contract: for any IR that passes grus.ir.validate, render_svg either
    # returns an SVG document or raises DeferredFeatureError — never a bare KeyError/IndexError. Only
    # DeferredFeatureError is caught here, so any other exception propagates and fails the test.
    try:
        svg = render.render_svg(p)
    except render.DeferredFeatureError:
        return
    assert svg.startswith("<svg ") and svg.rstrip().endswith("</svg>")


# --- odds and ends --------------------------------------------------------------------------------


def _twin_pedigree(twin_type: pb.ZygosityType) -> pb.Pedigree:
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1), twin_group=7, twin_type=twin_type),
                    pb.Offspring(child=pb.Position(generation=2, index=2), twin_group=7, twin_type=twin_type),
                ],
            )
        ],
    )


@pytest.mark.parametrize("twin_type", [pb.ZYGOSITY_TYPE_DIZYGOTIC, pb.ZYGOSITY_TYPE_UNKNOWN])
def test_twin_zygosity_is_marked_and_drawn(twin_type: pb.ZygosityType) -> None:
    lay = render.layout(_twin_pedigree(twin_type))
    assert lay.twin_groups == [render.TwinGroup(level=1, columns=(0, 1), zygosity=int(twin_type))]
    svg = render.render_svg(_twin_pedigree(twin_type))
    assert svg.startswith("<svg ")
    if twin_type == pb.ZYGOSITY_TYPE_UNKNOWN:
        assert ">?<" in svg  # unknown zygosity marks the convergence with a "?"


def test_founder_sibship_lays_out_with_bar_and_no_parents() -> None:
    # A partnerless mating (founder sibship): three siblings, no drawn parents. They lay out packed in birth
    # order at the top row, carry no fam pointer, and are recorded as a founder sibship for drawing.
    p = _load("founder_sibship")
    lay = render.layout(p)
    assert lay.founder_sibships == [(0, (0, 1, 2))]
    assert [f for row in lay.fam for f in row] == [-1, -1, -1], "a founder sibship draws no parent cell"
    at, idx = _coords(lay), _index(p)
    xs = [at[idx[(1, i)]][2] for i in (1, 2, 3)]
    assert xs == sorted(xs), "siblings placed left to right in birth order"
    svg = render.render_svg(p)
    assert svg.startswith("<svg ")
    # The implied hanger is a short vertical stub rising above the sib bar to a point with no symbol at its top.
    verticals = [
        (float(x1), float(y1), float(y2))
        for x1, y1, x2, y2 in re.findall(r'<line x1="([-0-9.]+)" y1="([-0-9.]+)" x2="([-0-9.]+)" y2="([-0-9.]+)"', svg)
        if x1 == x2
    ]
    hanger_top = min(min(y1, y2) for _x, y1, y2 in verticals)
    assert hanger_top < min(_symbol_center_ys(svg)), "the hanger rises above every symbol"


def test_founder_sibship_marriage_lays_out() -> None:
    # The c19 gen-I shape: two founder sibships whose members marry (I-2 x I-3), with a descending generation.
    p = _load("founder_sibship_marry_in")
    lay = render.layout(p)
    assert len(lay.founder_sibships) == 2, "both parentless sib rows are recorded"
    at, idx = _coords(lay), _index(p)
    a, b = at[idx[(1, 2)]], at[idx[(1, 3)]]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1, "the marrying founder-sibs form an adjacent couple"
    kids = [at[idx[(2, i)]] for i in (1, 2)]
    assert all(k[0] == a[0] + 1 for k in kids), "gen II hangs one row below the joined couple"
    assert min(a[2], b[2]) - _EPS <= sum(k[2] for k in kids) / 2 <= max(a[2], b[2]) + _EPS
    assert render.render_svg(p).startswith("<svg ")


def test_founder_sibship_marriage_children_head_marry_in_families() -> None:
    # c19's full shape: the founder-sib join's children each head a marry-in family (a spouse the founder
    # loops would otherwise root, placing the child before the cross pass could hang it as a sibship).
    # Regression: this used to raise "placed by another subtree" — the child's spouse is now deferred to
    # the cross pass, which claims the join's offspring.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_MAN),  # marries into sib B
            pb.Individual(generation=1, index=3, gender=pb.GENDER_WOMAN),  # marries into sib A
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_WOMAN),  # cross child, heads a family
            pb.Individual(generation=2, index=2, gender=pb.GENDER_MAN),  # cross child, heads a family
            pb.Individual(generation=2, index=10, gender=pb.GENDER_MAN),  # marry-in spouse of II-1
            pb.Individual(generation=2, index=11, gender=pb.GENDER_WOMAN),  # marry-in spouse of II-2
            pb.Individual(generation=3, index=1, gender=pb.GENDER_UNKNOWN),
            pb.Individual(generation=3, index=2, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                offspring=[
                    pb.Offspring(child=pb.Position(generation=1, index=1)),
                    pb.Offspring(child=pb.Position(generation=1, index=2)),
                ]
            ),
            pb.Mating(
                offspring=[
                    pb.Offspring(child=pb.Position(generation=1, index=3)),
                    pb.Offspring(child=pb.Position(generation=1, index=4)),
                ]
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=2),
                partner_b=pb.Position(generation=1, index=3),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1)),
                    pb.Offspring(child=pb.Position(generation=2, index=2)),
                ],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=10),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=2),
                partner_b=pb.Position(generation=2, index=11),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=2))],
            ),
        ],
    )
    lay = render.layout(p)
    assert len(lay.founder_sibships) == 2, "both parentless gen-I sib rows are recorded"
    at, idx = _coords(lay), _index(p)
    a, b = at[idx[(1, 2)]], at[idx[(1, 3)]]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1, "the marrying founder-sibs form an adjacent couple"
    for child, spouse in (((2, 1), (2, 10)), ((2, 2), (2, 11))):
        c, s = at[idx[child]], at[idx[spouse]]
        assert c[0] == s[0] and abs(c[1] - s[1]) == 1, "each cross child sits beside its marry-in spouse"
    assert render.render_svg(p).startswith("<svg ")


def test_founder_sib_marrying_a_founder_lays_out() -> None:
    # A founder-sib (I-1) marrying a plain drawn founder (I-3, no siblings of its own): v1's single-mate
    # recursion could not keep I-1 in its sibship while drawing the couple, so it deferred. v2's ordering keeps
    # the founder sibship {I-1, I-2} contiguous with I-1 at the end abutting its spouse I-3 — it lays out.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_WOMAN),  # plain marry-in founder
            pb.Individual(generation=2, index=1, gender=pb.GENDER_UNKNOWN),
        ],
        matings=[
            pb.Mating(
                offspring=[
                    pb.Offspring(child=pb.Position(generation=1, index=1)),
                    pb.Offspring(child=pb.Position(generation=1, index=2)),
                ]
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
        ],
    )
    lay = render.layout(p)  # was a deferral; now drawn
    assert lay.founder_sibships, "the parentless gen-I sibship {I-1, I-2} is recorded"
    at, idx = _coords(lay), _index(p)
    a, b = at[idx[(1, 1)]], at[idx[(1, 3)]]
    assert a[0] == b[0] and abs(a[1] - b[1]) == 1, "the founder-sib I-1 abuts its spouse I-3"
    assert at[idx[(2, 1)]][0] == a[0] + 1, "their child hangs one row below"
    assert render.render_svg(p).startswith("<svg ")


def test_label_stack_draws_id_then_annotation() -> None:
    # A node with a position id and a drawable annotation draws a two-line stack: the reconstructed id
    # ("II-1", from generation + index) first, the annotation (here the genotype) centred beneath it. trio
    # annotates every individual with a genotype; the bare arabic index rides in the id line, never as its
    # own "1" label line.
    lines = _label_lines(render.render_svg(_load("trio")))
    by_text = {s: (x, y) for x, y, s in lines}
    assert {"I-1", "I-2", "II-1"} <= set(by_text), "the position id is line 1 of every stack"
    assert {"N/N", "N/M", "M/M"} <= set(by_text), "the annotation line is drawn under the id"
    assert "1" not in by_text, "the bare index is part of the id line (II-1), never a standalone label"
    (id_x, id_y), (ann_x, ann_y) = by_text["I-1"], by_text["N/N"]
    assert ann_y > id_y, "annotation stacks below the id"
    assert id_x == ann_x, "the stack is a single centred column"


def test_label_stack_drops_empty_annotation() -> None:
    # The position id is always line 1; an individual with no annotations adds no further line. This DZ-twin
    # fixture annotates no one, so each of its four individuals draws exactly its id line and nothing
    # else (no genotype, no zygosity glyph).
    lines = _label_lines(render.render_svg(_twin_pedigree(pb.ZYGOSITY_TYPE_DIZYGOTIC)))
    assert sorted(s for *_, s in lines) == ["I-1", "I-2", "II-1", "II-2"]


def test_individual_number_annotation_not_drawn() -> None:
    # The bare arabic individual number is now the `index`, carried by the reconstructed position id line
    # ("II-2") — ANNOTATION_TYPE_INDIVIDUAL_NUMBER was retired, so there is no separate number annotation.
    # The id line encoding the index is drawn, a co-located genotype is drawn, and no standalone "2" appears.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(
                generation=2,
                index=2,
                gender=pb.GENDER_WOMAN,
                annotations=[pb.Annotation(text="N/M", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
        ],
    )
    texts = [s for *_, s in _label_lines(render.render_svg(p))]
    assert "II-2" in texts, "the position id (encoding the index) is drawn"
    assert "N/M" in texts, "a typed annotation is drawn"
    assert "2" not in texts, "the bare index is not drawn as a standalone label line"


def test_label_stack_dedups_id_equal_annotation() -> None:
    # An extractor may emit an annotation whose text repeats the position id; the stack must draw the id
    # once (not "I-1 / I-1"), while a genuinely distinct annotation still adds its own line.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(
                generation=1,
                index=1,
                gender=pb.GENDER_MAN,
                annotations=[pb.Annotation(text="I-1", type=pb.ANNOTATION_TYPE_OTHER)],
            ),
            pb.Individual(
                generation=1,
                index=2,
                gender=pb.GENDER_WOMAN,
                annotations=[pb.Annotation(text="N/M", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
        ],
    )
    texts = [s for *_, s in _label_lines(render.render_svg(p))]
    assert texts.count("I-1") == 1, "id-equals-annotation collapses to a single line"
    assert texts.count("I-2") == 1 and texts.count("N/M") == 1, "a distinct annotation is still a second line"


def test_wide_labels_widen_node_pitch() -> None:
    # Annotations can be wider than the symbol; the horizontal heuristic raises the column pitch so wide
    # text still clears. A long annotation therefore yields a wider canvas than the short-annotation figure.
    p = _load("trio")
    narrow = float(_WIDTH_RE.search(render.render_svg(p)).group(1))  # type: ignore[union-attr]
    for ind in p.individuals:
        ind.annotations.append(pb.Annotation(text="GENOTYPE-XXL", type=pb.ANNOTATION_TYPE_OTHER))  # 12 chars, wide
    wide = float(_WIDTH_RE.search(render.render_svg(p)).group(1))  # type: ignore[union-attr]
    assert wide > narrow


def test_render_validates_input() -> None:
    # A malformed IR (dangling offspring reference) is rejected before any layout or drawing.
    bad = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],  # dangling: no such individual
            )
        ],
    )
    with pytest.raises(ir.IntegrityError):
        render.render_svg(bad)


def test_unconnected_individuals_render() -> None:
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
        ]
    )
    lay = render.layout(p)
    assert lay.n == [2]
    assert render.render_svg(p).startswith("<svg ")


def test_geometry_spacing_changes_layout() -> None:
    p = _load("sibship")
    assert render.layout(p, render.Geometry(sib_gap=3.0)) != render.layout(p)


def test_consanguineous_founder_couple_draws_double_line() -> None:
    # The double-line marker follows the explicit Mating.consanguineous flag (never inferred), founders
    # included: the founder couple's spouse cell is 2 and _matings emits two parallel mating lines.
    p = _load("consanguineous")  # I-1 x I-2 founders, consanguineous: true
    assert render.layout(p).spouse[0][0] == 2, "the flagged founder couple is marked as a double-line mating"
    horiz = sorted(
        (float(y1), float(x1), float(x2))
        for x1, y1, x2, y2 in re.findall(
            r'<line x1="([-0-9.]+)" y1="([-0-9.]+)" x2="([-0-9.]+)" y2="([-0-9.]+)"', render.render_svg(p)
        )
        if y1 == y2
    )
    assert len(horiz) == 2, "a consanguineous mating draws two parallel lines"
    (y_top, x1a, x2a), (y_bot, x1b, x2b) = horiz
    assert (x1a, x2a) == (x1b, x2b), "the two lines span the same x-range"
    assert abs(y_bot - y_top) == render.DEFAULT_GEOMETRY.double_line_offset


def test_generation_markers_sit_in_left_gutter() -> None:
    # A three-generation pedigree gets one Roman-numeral marker per row (I, II, III), each centred in
    # the reserved left gutter and on its row's symbols, clear of every symbol and the proband arrow.
    svg = render.render_svg(_load("three_generation"))
    gutter = render.DEFAULT_GEOMETRY.gen_marker_gutter
    markers = _markers(svg)
    assert {s for *_, s in markers} == {"I", "II", "III"}
    assert [s for *_, s in sorted(markers, key=lambda t: t[1])] == ["I", "II", "III"], "top-to-bottom"
    assert {round(y, 3) for _x, y, _sz, _s in markers} == _symbol_center_ys(svg), "one per row, row-centred"
    for x, _y, sz, s in markers:  # each numeral lies fully inside the gutter [0, gutter]
        half_w = 0.6 * sz * len(s) / 2
        assert x - half_w >= -_EPS and x + half_w <= gutter + _EPS
    assert _min_glyph_x(svg) >= gutter - _EPS, "no symbol / connector / arrow reaches into the gutter"


def test_offset_single_child_descent_leaves_parents_midpoint() -> None:
    # v2 can pull a lone child off its parents' mating midpoint (a cousin who must sit beside its mate, the
    # LTBP3 shape). The descent must still originate from the parents' mating line — not float as a bare
    # vertical at the child's x (the old single-child shortcut assumed the child was centred).
    from grus.render._draw import _Draw

    def c(g: int, i: int, gender: pb.Gender) -> pb.Individual:
        return pb.Individual(generation=g, index=i, gender=gender)

    p = pb.Pedigree(
        individuals=[
            c(1, 1, pb.GENDER_MAN),
            c(1, 2, pb.GENDER_WOMAN),
            c(2, 1, pb.GENDER_MAN),
            c(2, 2, pb.GENDER_WOMAN),
            c(2, 3, pb.GENDER_WOMAN),
            c(2, 4, pb.GENDER_MAN),
            c(3, 1, pb.GENDER_WOMAN),
            c(3, 2, pb.GENDER_MAN),
            c(4, 1, pb.GENDER_MAN),
            c(4, 2, pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1)),
                    pb.Offspring(child=pb.Position(generation=2, index=3)),
                ],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),
                partner_b=pb.Position(generation=2, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=3),
                partner_b=pb.Position(generation=2, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=3, index=2))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=3, index=1),
                partner_b=pb.Position(generation=3, index=2),
                consanguineous=True,
                offspring=[
                    pb.Offspring(child=pb.Position(generation=4, index=1)),
                    pb.Offspring(child=pb.Position(generation=4, index=2)),
                ],
            ),
        ],
    )
    # The exact x-solve centres this shape, so offset it by hand: rows III and IV one unit right of where they
    # were placed, which leaves III-1 off II-1 x II-2's midpoint with every row's order and spacing intact.
    placed = render.layout(p)
    lay = dataclasses.replace(
        placed, pos=[[x + (1.0 if level >= 2 else 0.0) for x in row] for level, row in enumerate(placed.pos)]
    )
    draw = _Draw(p, lay, render.DEFAULT_GEOMETRY)
    at, idx = _coords(lay), _index(p)
    (lvl, k, _cx) = at[idx[(3, 1)]]  # III-1: only child of II-1 x II-2
    ii1, ii2 = at[idx[(2, 1)]], at[idx[(2, 2)]]
    mid_px = (draw.px(ii1[2]) + draw.px(ii2[2])) / 2
    assert abs(lay.pos[lvl][k] - (ii1[2] + ii2[2]) / 2) > 0.1, (
        "precondition: III-1 is offset from its parents' midpoint"
    )
    svg = draw.svg()
    lines = re.findall(r'<line x1="([-0-9.]+)" y1="([-0-9.]+)" x2="([-0-9.]+)" y2="([-0-9.]+)"', svg)
    starts = [(float(x), float(y)) for x, y in re.findall(r'<path d="M([-0-9.]+),([-0-9.]+)', svg)]  # elbows
    parent_y = draw.py(1)
    # a descent leg leaves the mating midpoint at the parents' row y (the bug emitted a leg only at the child x)
    assert any(
        abs(float(x1) - mid_px) < 0.5 and abs(float(x2) - mid_px) < 0.5 and min(float(y1), float(y2)) <= parent_y + 0.5
        for x1, y1, x2, y2 in lines
    ) or any(abs(x - mid_px) < 0.5 and y <= parent_y + 0.5 for x, y in starts), (
        "the offset single-child descent must originate from the parents' mating midpoint"
    )
