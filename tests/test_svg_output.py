"""Tests for the SVG document structure (docs/design/svg-output.md).

The drawing itself is pinned byte-for-byte by the goldens (test_render.py); these tests pin the *contract*
a consumer builds on: every drawn thing is a group naming its IR fact, parts are layered in a fixed order,
ids are unique and prefixable, and the minimum label box is a floor the layout honours.
"""

from __future__ import annotations

import collections
import dataclasses
import itertools
import json
import math
import pathlib
import re
import xml.etree.ElementTree as ET

import pytest

from grus import ir, render
from grus.models import pedigree_pb2 as pb
from grus.render import _draw

_GOLDENS = pathlib.Path(__file__).parent / "goldens"
_NAMES = sorted(path.stem for path in _GOLDENS.glob("*.pbtxt"))
_SVG = "{http://www.w3.org/2000/svg}"


def _load(name: str) -> pb.Pedigree:
    return ir.load_pbtxt((_GOLDENS / f"{name}.pbtxt").read_text())


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _classes(el: ET.Element) -> list[str]:
    return el.get("class", "").split()


def _groups(root: ET.Element, cls: str) -> list[ET.Element]:
    return [g for g in root.iter(f"{_SVG}g") if cls in _classes(g)]


def _parts(group: ET.Element) -> list[str]:
    """The first class of each direct child part, in document order (``clipPath`` has none)."""
    return [_classes(child)[0] for child in group if _classes(child)]


def _position(ind: pb.Individual) -> str:
    return f"{_draw._roman(ind.generation)}-{ind.index}"  # the drawer's own id rule


# --- goldens: the contract holds on every committed pedigree ------------------------------------------


@pytest.mark.parametrize("name", _NAMES)
def test_golden_parses_and_every_individual_has_one_group(name: str) -> None:
    p = _load(name)
    root = _parse((_GOLDENS / f"{name}.svg").read_text())
    assert "pedigree" in _classes(root), "the root carries the pedigree group's attributes"
    real = [g for g in _groups(root, "individual") if "ghost" not in _classes(g)]
    assert sorted(g.get("data-position", "") for g in real) == sorted(_position(i) for i in p.individuals)
    for g in real:
        ind = next(i for i in p.individuals if _position(i) == g.get("data-position"))
        assert g.get("id") == f"ind-{_position(ind)}"
        assert g.get("data-generation") == str(ind.generation) and g.get("data-index") == str(ind.index)
        assert g.get("data-gender") == pb.Gender.Name(ind.gender).removeprefix("GENDER_").lower()


@pytest.mark.parametrize("name", _NAMES)
def test_golden_ids_are_unique_and_references_resolve(name: str) -> None:
    svg = (_GOLDENS / f"{name}.svg").read_text()
    ids = re.findall(r'\bid="([^"]+)"', svg)
    assert len(ids) == len(set(ids)), "ids are unique within the document"
    for ref in re.findall(r"url\(#([^)]+)\)", svg):
        assert ref in ids, f"reference #{ref} has no target"


@pytest.mark.parametrize("name", _NAMES)
def test_golden_individual_parts_are_layered(name: str) -> None:
    order = ["backing", "fill", "divider", "symbol", "mark", "label", "hit"]
    for g in _groups(_parse((_GOLDENS / f"{name}.svg").read_text()), "individual"):
        parts = _parts(g)
        assert parts.count("backing") == 1 and parts.count("symbol") == 1 and parts.count("hit") == 1
        assert parts[0] == "backing" and parts[-1] == "hit"
        ranks = [order.index(part) for part in parts]
        assert ranks == sorted(ranks), f"parts out of layer order: {parts}"


@pytest.mark.parametrize("name", _NAMES)
def test_golden_hit_rect_covers_symbol_and_label_band(name: str) -> None:
    geom = render.DEFAULT_GEOMETRY
    for g in _groups(_parse((_GOLDENS / f"{name}.svg").read_text()), "individual"):
        hit = next(c for c in g if "hit" in _classes(c))
        x, y, w, h = (float(hit.get(a, "0")) for a in ("x", "y", "width", "height"))
        assert w >= geom.symbol_size and h > geom.symbol_size
        assert hit.get("fill") == "none" and hit.get("pointer-events") == "all"
        # every label line's anchor lies inside the hit rect
        for label in (c for c in g if "label" in _classes(c)):
            lx, ly = float(label.get("x", "0")), float(label.get("y", "0"))
            assert x <= lx <= x + w and y <= ly <= y + h


# --- the pedigree group and the condition legend ---------------------------------------------------------


def test_pedigree_group_carries_title_and_condition_legend() -> None:
    p = _load("carrier_inheritance")
    root = _parse(render.render_svg(p))
    assert root.get("data-title") == _draw._display_title(p)
    legend = json.loads(root.get("data-conditions", "null"))
    names = {c.name for i in p.individuals for c in i.conditions}
    assert set(legend) == names, "every condition an individual has appears in the legend, unnamed as an empty string"
    for g in _groups(root, "individual"):
        ind = next(i for i in p.individuals if _position(i) == g.get("data-position"))
        for c in ind.conditions:
            status = pb.ConditionStatus.Name(c.status).removeprefix("CONDITION_STATUS_").lower()
            assert g.get(f"data-condition-{legend.index(c.name)}") == status


def test_fill_part_names_the_condition_it_paints() -> None:
    def carrier(i: int, name: str) -> pb.Individual:
        ind = pb.Individual(generation=1, index=i, gender=pb.GENDER_MAN)
        ind.conditions.add(name=name, status=pb.CONDITION_STATUS_CARRIER)
        return ind

    root = _parse(render.render_svg(pb.Pedigree(individuals=[carrier(1, "varA"), carrier(2, "varB")])))
    legend = json.loads(root.get("data-conditions", "null"))
    assert legend == ["varA", "varB"]
    for g in _groups(root, "individual"):
        fills = [c for c in g if "fill" in _classes(c)]
        assert len(fills) == 1
        (idx,) = [k.removeprefix("data-condition-") for k in g.attrib if k.startswith("data-condition-")]
        assert fills[0].get("data-condition") == idx


def test_state_classes_mirror_the_ir() -> None:
    ind = pb.Individual(generation=1, index=1, gender=pb.GENDER_WOMAN, deceased=True, proband=True)
    ind.conditions.add(name="x", status=pb.CONDITION_STATUS_CARRIER)
    root = _parse(render.render_svg(pb.Pedigree(individuals=[ind])))
    (g,) = _groups(root, "individual")
    assert set(_classes(g)) == {"individual", "carrier", "deceased", "proband"}
    marks = [" ".join(_classes(c)) for c in g if "mark" in _classes(c)]
    assert marks == ["mark deceased", "mark proband"]


def _centre(shape: ET.Element) -> tuple[float, float]:
    """The centre of a symbol outline: a circle's, a square's, or a diamond's (top point's x, right point's y)."""
    if shape.tag == f"{_SVG}circle":
        return float(shape.get("cx", "nan")), float(shape.get("cy", "nan"))
    if shape.tag == f"{_SVG}rect":
        half = float(shape.get("width", "nan")) / 2
        return float(shape.get("x", "nan")) + half, float(shape.get("y", "nan")) + half
    top, right = shape.get("points", "").split()[:2]
    return float(top.split(",")[0]), float(right.split(",")[1])


def test_count_is_a_data_attribute_and_a_mark_inside_the_symbol() -> None:
    # A count-collapsed symbol draws its number, or n for an unknown number, centred inside it (Bennett); one
    # person (count absent or 1) draws none. The value rides on the group, so [data-count] selects every group.
    root = _parse(render.render_svg(_load("counts")))
    drawn: dict[str, tuple[str | None, str | None, str | None]] = {}
    for g in _groups(root, "individual"):
        marks = [c for c in g if _classes(c) == ["mark", "count"]]
        assert len(marks) <= 1
        if marks:
            mark_at = (float(marks[0].get("x", "nan")), float(marks[0].get("y", "nan")))
            assert mark_at == _centre(next(c for c in g if "symbol" in _classes(c)))
        text = marks[0].text if marks else None
        drawn[g.get("data-position", "")] = (g.get("data-count"), text, marks[0].get("fill") if marks else None)
    assert drawn == {
        "I-1": (None, None, None),
        "I-2": (None, None, None),
        "II-1": ("3", "3", "#000000"),
        "II-2": ("12", "12", "#ffffff"),  # white on the solid fill of an affected group
        "II-3": (None, None, None),  # count 1 is one person
        "II-4": ("n", "n", "#000000"),
    }


def test_count_never_overprints_another_mark() -> None:
    # The count sat on top of a '?', an X-linked carrier's dot, the presymptomatic line and the deceased slash, and a
    # three-digit count overflowed a diamond. With a mark through the centre (a section line included) it now sits
    # beside the upper right, clear of the symbol; otherwise it is centred and shrunk to fit the shape. A halo keeps it
    # legible over a fill.
    size = render.DEFAULT_GEOMETRY.symbol_size
    fit = {"man": 0.8, "woman": 0.7, "unknown": 0.5}
    root = _parse(render.render_svg(_load("count_marks")))
    seen = collections.Counter()
    for g in _groups(root, "individual"):
        (mark,) = [c for c in g if _classes(c)[:2] == ["mark", "count"]]
        symbol = next(c for c in g if "symbol" in _classes(c))
        cx, cy = _centre(symbol)
        x, y, font = (float(mark.get(a, "nan")) for a in ("x", "y", "font-size"))
        centre_mark = bool({"deceased", "presymptomatic"} & set(_classes(g))) or any(
            _classes(c)[:2] in (["mark", "unknown"], ["fill", "dot"]) or _classes(c)[:1] == ["divider"] for c in g
        )
        assert mark.get("paint-order") == "stroke" and mark.get("stroke") != mark.get("fill"), "a contrasting halo"
        if centre_mark:
            assert "outside" in _classes(mark) and mark.get("text-anchor") == "start"
            assert x > cx + size / 2 and y < cy, "beside the upper right, off the symbol"
        else:
            assert "outside" not in _classes(mark) and (x, y) == (cx, cy)
            assert 0.6 * font * len(mark.text or "") <= fit[g.get("data-gender", "")] * size + 1e-9
        seen[centre_mark] += 1
    assert seen[True] and seen[False]


# --- connectors ------------------------------------------------------------------------------------------


def test_mating_and_sibship_groups_name_their_people() -> None:
    root = _parse(render.render_svg(_load("trio")))
    (mating,) = _groups(root, "mating")
    assert mating.get("data-partners") == "I-1 I-2"
    assert any("hit" in _classes(c) and c.get("pointer-events") == "stroke" for c in mating)
    (sibship,) = _groups(root, "sibship")
    assert sibship.get("data-parents") == "I-1 I-2" and sibship.get("data-children") == "II-1"


def test_consanguineous_and_childless_ride_on_the_mating_group() -> None:
    root = _parse(render.render_svg(_load("consanguineous")))
    assert any("consanguineous" in _classes(g) for g in _groups(root, "mating"))
    root = _parse(render.render_svg(_load("childless")))
    kinds = {c for g in _groups(root, "mating") for c in _classes(g) if c.startswith("childless-")}
    assert kinds == {"childless-by-choice", "childless-infertility"}, "each glyph's kind is a class on its mating"


def test_founder_sibship_group_has_children_but_no_parents() -> None:
    root = _parse(render.render_svg(_load("founder_sibship")))
    founders = [g for g in _groups(root, "sibship") if "founder" in _classes(g)]
    assert founders and all(g.get("data-parents") is None and g.get("data-children") for g in founders)


def test_generation_markers_are_groups_naming_their_row() -> None:
    root = _parse(render.render_svg(_load("three_generation")))
    assert [g.get("data-generation") for g in _groups(root, "generation")] == ["1", "2", "3"]


# --- ids and the namespace argument -------------------------------------------------------------------------


def test_id_prefix_namespaces_every_id_and_reference() -> None:
    p = _load("carrier_inheritance")  # has clip-path references
    svg = render.render_svg(p, id_prefix="fig2-")
    ids = re.findall(r'\bid="([^"]+)"', svg)
    assert ids and all(i.startswith("fig2-") for i in ids)
    assert all(ref in ids for ref in re.findall(r"url\(#([^)]+)\)", svg))
    assert svg.replace("fig2-", "") == render.render_svg(p), "the prefix is the only difference"


def test_set_render_composes_tile_prefix_under_the_caller_prefix() -> None:
    ps = pb.PedigreeSet(pedigrees=[_load("trio"), _load("carrier_inheritance")])
    svg = render.render_set_svg(ps, id_prefix="fig-")
    ids = re.findall(r'\bid="([^"]+)"', svg)
    assert any(i.startswith("fig-p0-ind-") for i in ids) and any(i.startswith("fig-p1-ind-") for i in ids)
    assert len(ids) == len(set(ids))
    root = _parse(svg)
    tiles = [t for t in root.iter(f"{_SVG}svg") if t is not root]
    assert all("pedigree" in _classes(t) and t.get("data-conditions") for t in tiles)
    for title, doc in render.render_svgs(ps, id_prefix="z-"):
        assert _parse(doc).get("data-title") == title
        assert all(i.startswith("z-") for i in re.findall(r'\bid="([^"]+)"', doc))


def test_deferred_placeholder_is_a_pedigree_deferred_tile() -> None:
    # Two matings sharing a child is a shape the layout defers (docs/design/renderer.md, Deferred).
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=g, index=i, gender=pb.GENDER_MAN) for g, i in ((1, 1), (1, 2), (1, 3), (2, 1))
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=2),
                partner_b=pb.Position(generation=1, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
        ],
    )
    root = _parse(render.render_set_svg(pb.PedigreeSet(pedigrees=[p])))
    tiles = [t for t in root.iter(f"{_SVG}svg") if t is not root]
    assert tiles and "deferred" in _classes(tiles[0])


# --- the minimum label box ------------------------------------------------------------------------------------


def test_label_box_smaller_than_content_changes_nothing() -> None:
    p = _load("trio")
    geom = dataclasses.replace(render.DEFAULT_GEOMETRY, label_box_width=1.0, label_box_height=1.0)
    assert render.render_svg(p, geom) == render.render_svg(p)


def test_label_box_widens_reserved_space() -> None:
    p = _load("trio")
    for ind in p.individuals:
        del ind.conditions[:]  # no key: a wider canvas wraps the key's entries less, which can shorten it
    geom = render.DEFAULT_GEOMETRY
    wide = dataclasses.replace(geom, label_box_width=20.0, label_box_height=3.0)
    # The box is a floor under each label's width, and label widths separate neighbours in the solve: every pair
    # on a row sits at least a box plus the label gap apart.
    floor = (20.0 * geom.label_size + geom.label_size) / geom.x_unit
    for row in render.layout(p, wide).pos:
        assert all(b - a >= floor - 1e-6 for a, b in itertools.pairwise(row))
    base, boxed = _parse(render.render_svg(p, geom)), _parse(render.render_svg(p, wide))
    assert float(boxed.get("width", "0")) > float(base.get("width", "0"))
    assert float(boxed.get("height", "0")) > float(base.get("height", "0"))
    for g in _groups(boxed, "individual"):
        hit = next(c for c in g if "hit" in _classes(c))
        if "proband" in _classes(g) or "consultand" in _classes(g):
            continue  # the arrow widens the hit beyond the box; see test_hit_rect_covers_the_proband_arrow
        assert float(hit.get("width", "0")) == 20.0 * geom.label_size
        assert float(hit.get("height", "0")) == geom.symbol_size + 3.0 * geom.label_size


# --- ghosts, escaping, validation --------------------------------------------------------------------------


def _ind(g: int, i: int) -> pb.Individual:
    return pb.Individual(generation=g, index=i, gender=pb.GENDER_MAN if i % 2 else pb.GENDER_WOMAN)


def _mating(a: tuple[int, int], b: tuple[int, int], *kids: tuple[int, int], consang: bool = False) -> pb.Mating:
    return pb.Mating(
        partner_a=pb.Position(generation=a[0], index=a[1]),
        partner_b=pb.Position(generation=b[0], index=b[1]),
        consanguineous=consang,
        offspring=[pb.Offspring(child=pb.Position(generation=g, index=i)) for g, i in kids],
    )


def _avuncular(second_niece: bool = False) -> pb.Pedigree:
    """Uncle II-1 marries his niece III-1 (and, optionally, a second niece III-2): one or two ghosts of II-1."""
    kids = [(3, 1), (3, 2)] if second_niece else [(3, 1)]
    people = [_ind(1, 1), _ind(1, 2), _ind(2, 1), _ind(2, 2), _ind(2, 3), _ind(4, 1)] + [_ind(g, i) for g, i in kids]
    matings = [
        _mating((1, 1), (1, 2), (2, 1), (2, 2)),
        _mating((2, 2), (2, 3), *kids),
        _mating((2, 1), (3, 1), (4, 1), consang=True),
    ]
    if second_niece:
        people.append(_ind(4, 2))
        matings.append(_mating((2, 1), (3, 2), (4, 2), consang=True))
    return pb.Pedigree(individuals=people, matings=matings)


def test_ghost_carries_its_real_cells_identity() -> None:
    root = _parse(render.render_svg(_avuncular()))
    ghosts = [g for g in _groups(root, "individual") if "ghost" in _classes(g)]
    assert [g.get("id") for g in ghosts] == ["ghost-II-1"]
    (ghost,) = ghosts
    real = next(g for g in _groups(root, "individual") if g.get("id") == "ind-II-1")
    assert ghost.get("data-position") == real.get("data-position") == "II-1"
    assert set(_classes(ghost)) - {"ghost"} == set(_classes(real)), "same state classes as the real cell"
    assert [g.get("data-position") for g in _groups(root, "ghost-link")] == ["II-1"]
    partners = {g.get("data-partners") for g in _groups(root, "mating")}
    assert any(p and set(p.split()) == {"II-1", "III-1"} for p in partners), "the mating names the real, not the ghost"


def test_two_ghosts_of_one_individual_get_distinct_ids() -> None:
    svg = render.render_svg(_avuncular(second_niece=True))
    ids = re.findall(r'\bid="([^"]+)"', svg)
    assert len(ids) == len(set(ids)), "ids are unique even when one individual is ghosted twice"
    assert {"ghost-II-1", "ghost-II-1-2"} <= set(ids)


def test_text_and_attributes_are_escaped() -> None:
    ind = pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN, external_id='ext "1" & <2>')
    ind.conditions.add(name='A & "B" <C>', status=pb.CONDITION_STATUS_AFFECTED)
    ind.annotations.add(text="c.1<2>&", type=pb.ANNOTATION_TYPE_VARIANT)
    p = pb.Pedigree(individuals=[ind], labels=[pb.Label(text='Fam "Q"\t<&>\nline 2', kind=pb.LABEL_KIND_FAMILY)])
    root = _parse(render.render_svg(p))  # well-formed, or this raises
    assert root.get("data-title") == 'Fam "Q"\t<&>\nline 2', "tabs and newlines survive attribute normalisation"
    assert json.loads(root.get("data-conditions", "null")) == ['A & "B" <C>']
    (g,) = _groups(root, "individual")
    assert g.get("data-external-id") == 'ext "1" & <2>'
    assert [t.text for t in g if "label" in _classes(t)] == ["I-1", "c.1<2>&"]


def test_deferred_document_still_carries_the_legend() -> None:
    # A child of two matings is a shape the layout defers (docs/design/renderer.md, Deferred).
    p = pb.Pedigree(
        individuals=[_ind(1, 1), _ind(1, 2), _ind(1, 3), _ind(2, 1)],
        matings=[_mating((1, 1), (1, 2), (2, 1)), _mating((1, 2), (1, 3), (2, 1))],
    )
    p.individuals[3].conditions.add(name="cond", status=pb.CONDITION_STATUS_AFFECTED)
    ((_title, doc),) = render.render_svgs(pb.PedigreeSet(pedigrees=[p]))
    root = _parse(doc)
    assert "deferred" in _classes(root) and json.loads(root.get("data-conditions", "null")) == ["cond"]


def test_hit_rect_covers_the_proband_arrow() -> None:
    p = _load("trio")  # II-1 is the proband
    root = _parse(render.render_svg(p))
    g = next(g for g in _groups(root, "individual") if "proband" in _classes(g))
    hit = next(c for c in g if "hit" in _classes(c))
    x, y, w, h = (float(hit.get(a, "0")) for a in ("x", "y", "width", "height"))
    arrow = next(c for c in g if "proband" in _classes(c))
    for el in arrow.iter():
        for ax, ay in (("x1", "y1"), ("x2", "y2"), ("x", "y")):
            if el.get(ax) is not None:
                assert x <= float(el.get(ax, "0")) <= x + w and y <= float(el.get(ay, "0")) <= y + h


@pytest.mark.parametrize("bad", ['fig"', "fig 1-", "1fig-", "a)b", "fig-\n"])
def test_id_prefix_must_be_an_id_safe_token(bad: str) -> None:
    p = _load("trio")
    with pytest.raises(ValueError, match="id_prefix"):
        render.render_svg(p, id_prefix=bad)
    with pytest.raises(ValueError, match="id_prefix"):
        render.render_set_svg(pb.PedigreeSet(pedigrees=[p]), id_prefix=bad)
    with pytest.raises(ValueError, match="id_prefix"):
        render.render_svgs(pb.PedigreeSet(pedigrees=[p]), id_prefix=bad)


def test_same_named_conditions_share_a_slot_with_joined_statuses() -> None:
    ind = pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN)
    ind.conditions.add(status=pb.CONDITION_STATUS_CARRIER)
    ind.conditions.add(status=pb.CONDITION_STATUS_PRESYMPTOMATIC)
    (g,) = _groups(_parse(render.render_svg(pb.Pedigree(individuals=[ind]))), "individual")
    assert g.get("data-condition-0") == "carrier presymptomatic"


def test_ghost_hit_rect_has_no_arrow_extension_and_links_follow_layout_order() -> None:
    p = _avuncular(second_niece=True)
    p.individuals[2].proband = True  # II-1, the ghosted uncle
    root = _parse(render.render_svg(p))
    for g in _groups(root, "individual"):
        if "ghost" not in _classes(g):
            continue
        hit = next(c for c in g if "hit" in _classes(c))
        symbol = next(c for c in g if "symbol" in _classes(c))
        assert float(hit.get("x", "0")) == float(symbol.get("x", "0")), "a ghost draws no arrow, so its hit is the box"
    svg = render.render_svg(p)
    for _ in range(3):
        shuffled = pb.Pedigree()
        shuffled.CopyFrom(p)
        del shuffled.individuals[:]
        shuffled.individuals.extend(reversed(list(p.individuals)))
        assert render.render_svg(shuffled) == svg, "ghost links and ordinals follow layout order, not input order"


def _segments(group: ET.Element) -> list[tuple[float, float, float, float]]:
    return [
        tuple(float(e.get(a)) for a in ("x1", "y1", "x2", "y2"))  # type: ignore[misc]
        for e in group.iter(f"{_SVG}line")
        if "hit" not in (e.get("class") or "")
    ]


def _touch(p: tuple[float, float], s: tuple[float, float, float, float], eps: float = 1e-6) -> bool:
    """Whether point ``p`` lies on segment ``s``."""
    (px, py), (x1, y1, x2, y2) = p, s
    cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
    within = min(x1, x2) - eps <= px <= max(x1, x2) + eps and min(y1, y2) - eps <= py <= max(y1, y2) + eps
    return abs(cross) <= eps * max(1.0, abs(x2 - x1) + abs(y2 - y1)) and within


def _pieces(segs: list[tuple[float, float, float, float]]) -> int:
    """How many separate pieces ``segs`` draw: segments join where an endpoint of one lies on the other."""
    parent = list(range(len(segs)))

    def find(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    for i, a in enumerate(segs):
        for j, b in enumerate(segs):
            if i != j and (_touch((a[0], a[1]), b) or _touch((a[2], a[3]), b)):
                parent[find(i)] = find(j)
    return len({find(i) for i in range(len(segs))})


@pytest.mark.parametrize("name", _NAMES)
def test_every_sibship_is_one_connected_drawing(name: str) -> None:
    # A descent is one piece of ink: the drop from the parents, the sib bar and the child stubs all touch. A drop
    # landing beside the bar (a hinge's couple that cannot centre over its children) reads as a line to nowhere.
    root = ET.fromstring(render.render_svg(_load(name)))
    for g in root.iter(f"{_SVG}g"):
        if "sibship" not in (g.get("class") or "").split():
            continue
        segs = _segments(g)
        assert _pieces(segs) == 1, f"{name}: {g.get('data-parents')} descent is in pieces"


# --- clinical status: named fills, sections and the key (docs/design/renderer.md, Clinical status) ----------------


def _person(i: int, *conditions: tuple[str, int], gender: pb.Gender = pb.GENDER_MAN) -> pb.Individual:
    ind = pb.Individual(generation=1, index=i, gender=gender)
    for name, status in conditions:
        ind.conditions.add(name=name, status=status)
    return ind


_AFF, _CAR = pb.CONDITION_STATUS_AFFECTED, pb.CONDITION_STATUS_CARRIER


def _fills(g: ET.Element) -> list[ET.Element]:
    return [c for c in g if "fill" in _classes(c)]


def _patterns(root: ET.Element) -> dict[str, ET.Element]:
    return {pat.get("id", ""): pat for pat in root.iter(f"{_SVG}pattern")}


def test_patterns_are_emitted_only_for_the_fills_drawn() -> None:
    p = pb.Pedigree(individuals=[_person(1, ("A", _AFF)), _person(2, ("B", _CAR)), _person(3, ("C", 0))])
    p.individuals[2].conditions[0].status = pb.CONDITION_STATUS_PRESYMPTOMATIC  # C is in the legend, never filled
    root = _parse(render.render_svg(p, id_prefix="f-"))
    pats = _patterns(root)
    assert sorted(pats) == ["f-fill-affected-0", "f-fill-carrier-1"]
    for pid, pat in pats.items():
        assert "fill-pattern" in _classes(pat)
        assert pid == f"f-fill-{pat.get('data-status')}-{pat.get('data-condition')}"
        assert "style" not in pat.attrib and all("style" not in el.attrib for el in pat.iter())
    for g in _groups(root, "individual"):
        for f in _fills(g):
            assert f.get("fill") == f"url(#f-fill-{f.get('data-status')}-{f.get('data-condition')})"


class _Ray:
    """One ray of a ``divider`` border path (centre to outline), read like a line element."""

    def __init__(self, path: ET.Element, start: tuple[float, float], end: tuple[float, float]) -> None:
        self._attrs = {"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]}
        self._path = path

    def get(self, key: str, default: str = "") -> str:
        if key in self._attrs:
            return f"{self._attrs[key]:g}"
        return self._path.get(key, default)


def _dividers(g: ET.Element) -> list[_Ray]:
    """The rays of a group's ``divider`` border paths: each distinct centre-to-outline segment once."""
    rays: list[_Ray] = []
    for path in (c for c in g if "divider" in _classes(c)):
        assert path.tag == f"{_SVG}path" and path.get("stroke-linejoin") == "round", "one path, clean joins"
        pts = [(float(a), float(b)) for a, b in re.findall(r"([-0-9.]+),([-0-9.]+)", path.get("d", ""))]
        centre = pts[1]
        assert all(pt == centre for pt in pts[1::2]), "out along each ray and back through the centre"
        rays += [_Ray(path, centre, end) for end in pts[0::2]]
    return rays


def test_a_carrier_of_two_conditions_is_two_hatched_sections_of_different_tones() -> None:
    root = _parse(render.render_svg(pb.Pedigree(individuals=[_person(1, ("HEXA", _CAR), ("CFTR", _CAR))])))
    (g,) = _groups(root, "individual")
    fills = _fills(g)
    assert [(f.get("data-condition"), f.get("data-status")) for f in fills] == [("0", "carrier"), ("1", "carrier")]
    assert all(f.get("clip-path") for f in fills), "sections are clipped to the shape, never the solid shape"
    assert float(fills[0].get("x", "0")) < float(fills[1].get("x", "0")), "index 0 left, index 1 right"
    pats = _patterns(root)
    tones = [pats[f"fill-carrier-{i}"].find(f"{_SVG}rect").get("fill") for i in (0, 1)]  # type: ignore[union-attr]
    hatches = [pats[f"fill-carrier-{i}"].find(f"{_SVG}path") for i in (0, 1)]
    assert tones[0] != tones[1], "each condition has its own tone"
    assert [h.get("stroke") for h in hatches] == ["#ffffff", "#ffffff"], "white lines on the dark tones"  # type: ignore[union-attr]
    assert hatches[0].get("d") != hatches[1].get("d"), "neighbouring indices hatch in opposite directions"  # type: ignore[union-attr]


def test_carrier_is_the_affected_tone_with_a_contrasting_hatch() -> None:
    legend = [pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in "ABCD"]
    people = [_person(i + 1, (n, s)) for i, (n, s) in enumerate((n, s) for n in "ABCD" for s in (_AFF, _CAR))]
    pats = _patterns(_parse(render.render_svg(pb.Pedigree(labels=legend, individuals=people))))
    for i, line in enumerate(("#ffffff", "#ffffff", "#000000", "#000000")):
        affected, carrier = pats[f"fill-affected-{i}"], pats[f"fill-carrier-{i}"]
        tone = affected.find(f"{_SVG}rect").get("fill")  # type: ignore[union-attr]
        hatch = carrier.find(f"{_SVG}path")
        assert carrier.find(f"{_SVG}rect").get("fill") == tone  # type: ignore[union-attr]
        assert hatch is not None and hatch.get("stroke") == line and "hatch" in _classes(hatch)
        (x1, y1), (x2, y2) = _segments_of(hatch.get("d", ""))[0]
        rising = (x2 - x1) * (y2 - y1) < 0  # "/" in SVG's y-down frame
        assert rising == (i % 2 == 0), "/ for even indices, \\ for odd"
        assert affected.find(f"{_SVG}path") is None


def _vertical(line: ET.Element | _Ray) -> bool:
    return line.get("x1") == line.get("x2")


def test_one_condition_draws_only_a_filled_sections_inner_edge() -> None:
    # A lone carrier's half has a visible edge inside the symbol: the thin centre line, as the two rays that bound
    # its section. The rest of its boundary is the outline, drawn once; a wholly filled or empty symbol has none.
    p = pb.Pedigree(individuals=[_person(1, ("A", _AFF)), _person(2, ("A", _CAR)), _person(3)])
    affected, carrier, plain = _groups(_parse(render.render_svg(p)), "individual")
    (whole,) = _fills(affected)
    assert whole.tag == f"{_SVG}rect" and whole.get("clip-path") is None and whole.get("width") == "36"
    assert not _dividers(affected) and not _dividers(plain)
    edges = _dividers(carrier)
    assert len(edges) == 2 and all(_vertical(e) and e.get("stroke-width") == "1" for e in edges)


def test_two_conditions_border_only_filled_halves() -> None:
    # Only a filled region is bordered: no lines in an empty symbol, none inside a wholly filled one.
    p = pb.Pedigree(
        individuals=[_person(1, ("A", _AFF)), _person(2, ("B", _CAR)), _person(3), _person(4, ("A", _AFF), ("B", _CAR))]
    )
    whole, carrier, plain, both = _groups(_parse(render.render_svg(p)), "individual")
    assert [f.get("width") for f in _fills(whole)] == ["36"] and not _dividers(whole)
    assert not _dividers(plain)
    for g in (carrier, both):
        edges = _dividers(g)
        assert len(edges) == 2 and all(_vertical(e) and e.get("stroke-width") == "1" for e in edges)


def test_three_or_four_conditions_divide_into_quadrants() -> None:
    legend = [pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in "ABC"]
    p = pb.Pedigree(labels=legend, individuals=[_person(1, ("A", _AFF), ("C", _CAR)), _person(2, ("B", _AFF))])
    two, one = _groups(_parse(render.render_svg(p)), "individual")
    # Top-left and bottom-left filled: the rays at 12, 6 and 9 o'clock bound them; the one at 3 o'clock does not.
    assert len(_dividers(two)) == 3 and not _dividers(one)
    a, c = _fills(two)
    assert a.get("width") == c.get("width") == "18" and a.get("height") == "18"
    assert float(c.get("y", "0")) > float(a.get("y", "0")), "index 2 is the bottom-left quadrant"


def test_a_seventh_filled_condition_defers() -> None:
    p = pb.Pedigree(individuals=[_person(i + 1, (name, _AFF)) for i, name in enumerate("ABCDEFG")])
    with pytest.raises(render.DeferredFeatureError, match="legend index 6; at most 6 conditions can be drawn"):
        render.render_svg(p)
    p.individuals[6].conditions[0].status = pb.CONDITION_STATUS_UNKNOWN  # in the legend, but drawn with no fill
    render.render_svg(p)


def test_key_has_one_entry_per_fill_drawn_with_its_label() -> None:
    p = pb.Pedigree(
        individuals=[
            _person(1, ("A", _AFF)),
            _person(2, ("A", _CAR), ("B", _CAR)),
            _person(3, ("", _AFF)),
            _person(4, ("", _CAR), gender=pb.GENDER_WOMAN),
        ]
    )
    root = _parse(render.render_svg(p, id_prefix="k-"))
    (key,) = _groups(root, "key")
    assert key.get("id") == "k-key"
    entries = [(e.get("id"), e.get("data-condition"), e.get("data-status")) for e in _groups(key, "key-entry")]
    assert entries == [
        ("k-key-affected-0", "0", "affected"),
        ("k-key-carrier-0", "0", "carrier"),
        ("k-key-carrier-1", "1", "carrier"),
        ("k-key-affected-2", "2", "affected"),
        ("k-key-carrier-2", "2", "carrier"),
    ]
    labels = [next(t.text for t in e if "key-label" in _classes(t)) for e in _groups(key, "key-entry")]
    assert labels == ["A", "Carrier: A", "Carrier: B", "Affected", "Carrier"]
    for e in _groups(key, "key-entry"):
        swatch = next(c for c in e if "swatch" in _classes(c))
        assert swatch.get("fill") == f"url(#k-fill-{e.get('data-status')}-{e.get('data-condition')})"


def test_no_fill_draws_no_key_and_no_defs() -> None:
    p = _load("trio")
    for ind in p.individuals:
        del ind.conditions[:]
    svg = render.render_svg(p)
    assert 'class="key"' not in svg and "<defs>" not in svg and "<pattern" not in svg


def _translate(el: ET.Element) -> tuple[float, float]:
    """The offset of an element drawn in local coordinates (``transform="translate(x y)"``)."""
    m = re.fullmatch(r"translate\(([-0-9.]+) ([-0-9.]+)\)", el.get("transform", ""))
    assert m, el.attrib
    return float(m.group(1)), float(m.group(2))


def _bottom(el: ET.Element) -> float:
    return float(el.get("y", "0")) + float(el.get("height", "0"))


@pytest.mark.parametrize("name", _NAMES)
def test_key_sits_below_every_individual(name: str) -> None:
    root = _parse((_GOLDENS / f"{name}.svg").read_text())
    keys = _groups(root, "key")
    if not keys:
        assert not _patterns(root)
        return
    (key,) = keys
    swatches = [
        (tx + float(sw.get("x", "0")), ty + float(sw.get("y", "0")), float(sw.get("height", "0")))
        for sw in key.iter(f"{_SVG}rect")
        if sw.get("transform")  # not a key symbol's clip rectangle
        for tx, ty in [_translate(sw)]
    ]
    top = min(y for _, y, _ in swatches)
    # Each hit rect covers its symbol, the reserved label box and any arrow.
    lowest = max(_bottom(next(c for c in g if "hit" in _classes(c))) for g in _groups(root, "individual"))
    assert top >= lowest + render.DEFAULT_GEOMETRY.key_gap - 1e-6
    bottom = max(y + h for _, y, h in swatches)
    assert bottom <= float(root.get("height", "0")) - render.DEFAULT_GEOMETRY.margin + 1e-6
    for e in _groups(key, "key-entry"):
        label = next(t for t in e if "key-label" in _classes(t))
        right = float(label.get("x", "0")) + 0.6 * render.DEFAULT_GEOMETRY.label_size * len(label.text or "")
        assert right <= float(root.get("width", "0")) - render.DEFAULT_GEOMETRY.margin + 1e-6


def test_set_render_keys_each_tile() -> None:
    ps = pb.PedigreeSet(pedigrees=[_load("trio"), _load("compound_carrier")])
    svg = render.render_set_svg(ps)
    root = _parse(svg)
    tiles = [t for t in root.iter(f"{_SVG}svg") if t is not root]
    assert [[k.get("id") for k in _groups(t, "key")] for t in tiles] == [["p0-key"], ["p1-key"]]
    ids = re.findall(r'\bid="([^"]+)"', svg)
    assert len(ids) == len(set(ids)) and all(ref in ids for ref in re.findall(r"url\(#([^)]+)\)", svg))


def test_a_count_moves_beside_a_symbol_only_when_it_has_borders() -> None:
    # A border crosses the centre, where the count would sit, so the count moves outside; a symbol without one keeps
    # its count inside, in any pedigree.
    def count_classes(*conditions: tuple[str, int]) -> list[str]:
        ind = _person(1, *conditions)
        ind.count = 3
        other = _person(2, ("A", _AFF), ("B", _CAR))
        (g, _) = _groups(_parse(render.render_svg(pb.Pedigree(individuals=[ind, other]))), "individual")
        (count,) = [c for c in g if "count" in _classes(c)]
        return _classes(count)

    assert count_classes() == ["mark", "count"]
    assert count_classes(("A", _AFF)) == ["mark", "count"], "wholly filled: no border"
    assert count_classes(("B", _CAR)) == ["mark", "count", "outside"]
    one = _person(1, ("A", _AFF))
    one.count = 3
    (g,) = _groups(_parse(render.render_svg(pb.Pedigree(individuals=[one]))), "individual")
    (count,) = [c for c in g if "count" in _classes(c)]
    assert _classes(count) == ["mark", "count"] and count.get("fill") == "#ffffff", "white on the black tone"


def test_presymptomatic_line_is_outline_weight_from_outline_to_outline() -> None:
    # A filled half's inner border and the presymptomatic line share a place; the weight tells them apart. The line
    # ends on the outline, so it does not run on into a descent line above.
    ind = _person(1, ("A", pb.CONDITION_STATUS_PRESYMPTOMATIC), ("B", _CAR))
    (g,) = _groups(_parse(render.render_svg(pb.Pedigree(individuals=[ind]))), "individual")
    (line,) = [c for c in g if "presymptomatic" in _classes(c)]
    borders = _dividers(g)
    symbol = next(c for c in g if "symbol" in _classes(c))
    assert borders and all(
        float(line.get("stroke-width", "0")) == 2 * float(b.get("stroke-width", "0")) for b in borders
    )
    top, bottom = float(symbol.get("y", "0")), float(symbol.get("y", "0")) + float(symbol.get("height", "0"))
    assert float(line.get("y1", "0")) == top and float(line.get("y2", "0")) == bottom


def test_the_key_names_each_presymptomatic_condition() -> None:
    # The vertical line does not say which condition; the key does, one entry per condition, named or not.
    legend = [pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in "AB"]
    people = [_person(1, ("A", pb.CONDITION_STATUS_PRESYMPTOMATIC)), _person(2, ("B", _CAR))]
    (key,) = _groups(_parse(render.render_svg(pb.Pedigree(labels=legend, individuals=people))), "key")
    entry = next(e for e in _groups(key, "key-entry") if e.get("data-status") == "presymptomatic")
    assert entry.get("id") == "key-presymptomatic-0" and entry.get("data-condition") == "0"
    assert [t.text for t in entry if "key-label" in _classes(t)] == ["Presymptomatic: A"]
    assert [c for c in entry if "presymptomatic" in _classes(c)], "a swatch with the vertical line"
    unnamed = pb.Pedigree(individuals=[_person(1, ("", pb.CONDITION_STATUS_PRESYMPTOMATIC))])
    (key,) = _groups(_parse(render.render_svg(unnamed)), "key")
    (entry,) = _groups(key, "key-entry")
    assert [t.text for t in entry if "key-label" in _classes(t)] == ["Presymptomatic"]


def test_an_unfilled_legend_entry_still_holds_its_section() -> None:
    # Sections are keyed by legend index, not by which conditions happen to be filled, so a phenotype label nobody
    # is shaded for still takes index 0 and a carrier of the second condition fills the right half.
    legend = [pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in "AB"]
    root = _parse(render.render_svg(pb.Pedigree(labels=legend, individuals=[_person(1, ("B", _CAR))])))
    (g,) = _groups(root, "individual")
    (section,) = _fills(g)
    assert section.get("data-condition") == "1" and section.get("x") == "0", "the right half, about the centre"


def _segments_of(d: str) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The straight segments of a path of ``M`` / ``L`` commands."""
    out = []
    for run in re.findall(r"M[^M]+", d):
        pts = [tuple(map(float, xy.split(","))) for xy in re.findall(r"[-0-9.]+,[-0-9.]+", run)]
        out += list(itertools.pairwise(pts))
    return out  # type: ignore[return-value]


def _hatch_gaps(svg: str) -> list[tuple[str, float]]:
    """For every carrier-hatched fill part and key swatch, the clear gap from its strokes to the edges they run along.

    Each hatch segment, repeated over the pattern's tiles, is an infinite line in the part's local frame. Against a
    straight edge (a section rectangle's side, a square's or diamond's outline edge) only a parallel line counts: its
    gap is the distance between them less half the stroke. Against a circle's outline, a line inside it (or within
    half a stroke outside, so still painted) counts as a chord whose gap is the radius less its distance from the
    centre less half the stroke. Strokes crossing an edge have no gap to report.
    """
    root = _parse(svg)
    hatches: dict[str, tuple[float, float, list[tuple[tuple[float, float], tuple[float, float]]]]] = {}
    for pat in root.iter(f"{_SVG}pattern"):
        if pat.get("data-status") != "carrier":
            continue
        path = pat.find(f"{_SVG}path")
        assert path is not None
        assert not pat.get("patternTransform"), "a hatch's phase comes from the part's frame alone"
        hatches[pat.get("id", "")] = (
            float(pat.get("width", "0")),
            float(path.get("stroke-width", "0")),
            _segments_of(path.get("d", "")),
        )
    out: list[tuple[str, float]] = []
    for group in root.iter(f"{_SVG}g"):
        # A symbol's outline; a divided pedigree's key symbol has a swatch outline instead.
        symbol = next((c for c in group if {"symbol", "swatch-outline"} & set(_classes(c))), None)
        for part in group:
            ref = re.fullmatch(r"url\(#(.+)\)", part.get("fill", ""))
            if not ref or ref.group(1) not in hatches:
                continue
            tile, thick, segments = hatches[ref.group(1)]
            tx, ty = _translate(part)
            if part.tag == f"{_SVG}polygon":  # a sixth's wedge, whose far edges lie outside the shape
                corners = [(float(a), float(b)) for a, b in (xy.split(",") for xy in part.get("points", "").split())]
                xs, ys = [c[0] for c in corners], [c[1] for c in corners]
                x, y, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
            else:
                x, y = float(part.get("x", "0")), float(part.get("y", "0"))
                w, h = float(part.get("width", "0")), float(part.get("height", "0"))
                corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]  # the section or swatch, local frame
            circle = None
            local = symbol is not None and "swatch-outline" in _classes(symbol)  # drawn in the part's own frame
            if symbol is not None and symbol.tag == f"{_SVG}polygon":
                pts = [tuple(map(float, xy.split(","))) for xy in symbol.get("points", "").split()]
                outline = [(px - tx, py - ty) for px, py in pts]
                edges = list(itertools.pairwise([*outline, outline[0]]))
            elif symbol is not None and symbol.tag == f"{_SVG}circle":
                circle = (
                    float(symbol.get("cx", "0")) - tx,
                    float(symbol.get("cy", "0")) - ty,
                    float(symbol.get("r", "0")),
                )
                edges = []
            else:
                sx, sy = (
                    (
                        float(symbol.get("x", "0")) - (0 if local else tx),
                        float(symbol.get("y", "0")) - (0 if local else ty),
                    )
                    if symbol is not None
                    else (x, y)
                )
                sw = float(symbol.get("width", "0")) if symbol is not None else w
                edges = list(itertools.pairwise([(sx, sy), (sx + sw, sy), (sx + sw, sy + sw), (sx, sy + sw), (sx, sy)]))
            shape = [e[0] for e in edges] or [(circle[0] - circle[2], circle[1]), (circle[0] + circle[2], circle[1])]  # type: ignore[index]
            scx = sum(px for px, _ in shape) / len(shape) if circle is None else circle[0]
            scy = sum(py for _, py in shape) / len(shape) if circle is None else circle[1]
            bound = max(math.hypot(px - scx, py - scy) for px, py in shape) + 1.0
            # A section edge counts only where it passes through the shape; one wholly outside is clipped away.
            for a, b in itertools.pairwise([*corners, corners[0]]):
                elen = math.hypot(b[0] - a[0], b[1] - a[1])
                if abs((b[0] - a[0]) * (a[1] - scy) - (b[1] - a[1]) * (a[0] - scx)) / elen < bound:
                    edges.append((a, b))
            what = f"{ref.group(1)} {group.get('id')}"
            for (x1, y1), (x2, y2) in segments:
                length = math.hypot(x2 - x1, y2 - y1)
                n = (-(y2 - y1) / length, (x2 - x1) / length)
                offsets = [n[0] * (x1 + i * tile) + n[1] * (y1 + j * tile) for i in range(-8, 9) for j in range(-8, 9)]
                for (ex1, ey1), (ex2, ey2) in edges:
                    elen = math.hypot(ex2 - ex1, ey2 - ey1)
                    m = (-(ey2 - ey1) / elen, (ex2 - ex1) / elen)
                    if abs(m[0] * n[1] - m[1] * n[0]) > 1e-6:
                        continue  # crosses the edge
                    b = n[0] * ex1 + n[1] * ey1
                    out.append((f"{what} edge", min(abs(o - b) for o in offsets) - thick / 2))
                if circle is not None:
                    cx, cy, r = circle
                    c = n[0] * cx + n[1] * cy
                    for o in offsets:
                        if abs(o - c) < r + thick / 2:
                            out.append((f"{what} chord", r - abs(o - c) - thick / 2))
    return out


def _all_hatches(names: str = "ABCD") -> pb.Pedigree:
    """Every carrier hatch in its section on each shape, singly and all together: quadrants for four, sixths for six."""
    genders = (pb.GENDER_MAN, pb.GENDER_WOMAN, pb.GENDER_NONBINARY)
    people = [
        _person(i + 1, (name, _CAR), gender=gender)
        for i, (gender, name) in enumerate(itertools.product(genders, names))
    ]
    people += [
        _person(len(people) + i + 1, *((n, _CAR) for n in names), gender=gender) for i, gender in enumerate(genders)
    ]
    return pb.Pedigree(labels=[pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in names], individuals=people)


@pytest.mark.parametrize("name", [*_NAMES, "all_hatches", "sixths", "halves", "undivided"])
def test_no_hatch_line_runs_within_a_stroke_width_of_a_region_edge(name: str) -> None:
    # Every carrier stroke is diagonal and each fill part is drawn in its symbol's own frame (a swatch from its
    # corner), so each hatch has one phase per shape: no stroke runs along a section edge, a divider, a square's or
    # diamond's outline edge, or close inside a circle's outline, where it would read as a thicker edge or a mark.
    if name == "all_hatches":
        svg = render.render_svg(_all_hatches())
    elif name == "sixths":
        svg = render.render_svg(_all_hatches("ABCDEF"))
    elif name == "halves":
        genders = (pb.GENDER_MAN, pb.GENDER_WOMAN, pb.GENDER_NONBINARY)
        svg = render.render_svg(
            pb.Pedigree(individuals=[_person(i + 1, ("A", _CAR), ("B", _CAR), gender=g) for i, g in enumerate(genders)])
        )
    elif name == "undivided":
        genders = (pb.GENDER_MAN, pb.GENDER_WOMAN, pb.GENDER_NONBINARY)
        svg = render.render_svg(
            pb.Pedigree(individuals=[_person(i + 1, ("A", _CAR), gender=g) for i, g in enumerate(genders)])
        )
    else:
        svg = (_GOLDENS / f"{name}.svg").read_text()
    gaps = _hatch_gaps(svg)
    stroke = 2.0  # the outline's stroke width
    assert all(gap >= stroke - 1e-6 for _, gap in gaps), sorted(g for g in gaps if g[1] < stroke - 1e-6)[:5]
    if name in ("all_hatches", "sixths", "halves", "undivided"):
        kinds = {what.split()[0] for what, _ in gaps}
        want = {
            f"fill-carrier-{i}"
            for i in {"all_hatches": range(4), "sixths": range(6), "halves": range(2), "undivided": range(1)}[name]
        }
        assert kinds == want, "the check saw every hatch run alongside some edge"


# --- sixths: five or six conditions -----------------------------------------------------------------------------


def _sixths(*people: pb.Individual, names: str = "ABCDEF") -> pb.Pedigree:
    return pb.Pedigree(labels=[pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in names], individuals=people)


@pytest.mark.parametrize("names", ["ABCDE", "ABCDEF"])
def test_five_or_six_conditions_border_filled_wedges_only(names: str) -> None:
    genders = (pb.GENDER_MAN, pb.GENDER_WOMAN, pb.GENDER_NONBINARY)
    every = [(n, _CAR) for n in names]
    people = [_person(i + 1, *every, gender=g) for i, g in enumerate(genders)] + [_person(9)]
    *filled, plain = _groups(_parse(render.render_svg(_sixths(*people, names=names))), "individual")
    assert not _dividers(plain), "an empty symbol is its outline alone"
    for g in filled:
        rays = _dividers(g)
        assert len(rays) == 6, "every boundary bounds a filled wedge"
        assert not any(_vertical(r) for r in rays), "none vertical, so none lies on the presymptomatic line"
        assert all(r.get("stroke-width") == "1" for r in rays), "half the outline's width"


@pytest.mark.parametrize("gender", [pb.GENDER_MAN, pb.GENDER_WOMAN, pb.GENDER_NONBINARY])
def test_a_sixth_is_a_wedge_toward_its_clock_position(gender: pb.Gender) -> None:
    # Index order is the reading order of halves and quadrants: 10, 12 and 2 o'clock, then 8, 6 and 4 o'clock. The
    # wedge's point is the symbol's centre on every shape, a circle's offset fill frame included.
    people = [_person(i + 1, (n, _AFF), gender=gender) for i, n in enumerate("ABCDEF")]
    groups = _groups(_parse(render.render_svg(_sixths(*people))), "individual")
    for g, clock in zip(groups, (10, 12, 2, 8, 6, 4), strict=True):
        (wedge,) = _fills(g)
        assert wedge.tag == f"{_SVG}polygon", "in sixths one affected condition keeps its section"
        tx, ty = _translate(wedge)
        (c, _, mid, _) = [tuple(map(float, xy.split(","))) for xy in wedge.get("points", "").split()]
        assert (c[0] + tx, c[1] + ty) == _centre(next(e for e in g if "symbol" in _classes(e)))
        angle = math.degrees(math.atan2(mid[0] - c[0], -(mid[1] - c[1]))) % 360  # clockwise from 12
        assert abs(angle - clock * 30 % 360) < 1e-2, "3-decimal coordinates"


def test_a_presymptomatic_line_reads_over_sixth_borders() -> None:
    ind = _person(1, ("A", pb.CONDITION_STATUS_PRESYMPTOMATIC), ("B", _CAR))
    (g, *_) = _groups(_parse(render.render_svg(_sixths(ind, _person(2, ("E", _AFF))))), "individual")
    (line,) = [c for c in g if "presymptomatic" in _classes(c)]
    assert _vertical(line) and line.get("stroke-width") == "2"
    assert _dividers(g) and not any(_vertical(d) for d in _dividers(g))


def test_a_divided_pedigrees_key_shows_each_entrys_section() -> None:
    # The key draws a small square per entry, filled in that entry's section only and bordered by that section's
    # two boundaries, so it shows where a condition sits as well as its fill; an undivided pedigree keeps plain
    # swatches.
    root = _parse(render.render_svg(_sixths(_person(1, ("C", _CAR)), _person(2, ("E", _AFF)))))
    (key,) = _groups(root, "key")
    for e in _groups(key, "key-entry"):
        (swatch,) = [c for c in e if "swatch" in _classes(c)]
        assert swatch.tag == f"{_SVG}polygon" and swatch.get("clip-path")
        assert len(_dividers(e)) == 2 and [c for c in e if "swatch-outline" in _classes(c)]
    undivided = _parse(render.render_svg(pb.Pedigree(individuals=[_person(1, ("A", _CAR))])))
    (key,) = _groups(undivided, "key")
    assert not any(_dividers(e) for e in _groups(key, "key-entry"))


def test_conditions_4_and_5_have_their_own_tones_and_hatches() -> None:
    people = [_person(i + 1, (n, s)) for i, (n, s) in enumerate((n, s) for n in "ABCDEF" for s in (_AFF, _CAR))]
    pats = _patterns(_parse(render.render_svg(_sixths(*people))))
    tones = [pats[f"fill-affected-{i}"].find(f"{_SVG}rect").get("fill") for i in range(6)]  # type: ignore[union-attr]
    assert len(set(tones)) == 6
    for i in (4, 5):
        carrier = pats[f"fill-carrier-{i}"]
        assert carrier.find(f"{_SVG}rect").get("fill") == tones[i]  # type: ignore[union-attr]
        (x1, y1), (x2, y2) = _segments_of(carrier.find(f"{_SVG}path").get("d", ""))[0]  # type: ignore[union-attr]
        assert ((x2 - x1) * (y2 - y1) < 0) == (i % 2 == 0), "/ for even indices, \\ for odd"


# --- the primary condition takes index 0 ----------------------------------------------------------------------------


def _legend(p: pb.Pedigree) -> list[str]:
    return json.loads(_parse(render.render_svg(p)).get("data-conditions", "null"))


def test_the_probands_affected_condition_takes_index_0() -> None:
    proband = _person(3, ("C", _AFF))
    proband.proband = True
    labels = [pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in "ABC"]
    p = pb.Pedigree(labels=labels, individuals=[_person(1, ("A", _AFF)), _person(2, ("A", _AFF)), proband])
    assert _legend(p) == ["C", "A", "B"], "the proband's condition first, even when another has more affected"


def test_else_the_most_affected_condition_takes_index_0() -> None:
    # Review-set c03: the proband is unaffected, and Polyneuropathy has the most affected individuals.
    proband = _person(4)
    proband.proband = True
    labels = [pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in ("Bone Cancer", "Polyneuropathy", "Polio")]
    people = [
        _person(1, ("Bone Cancer", _AFF)),
        _person(2, ("Polyneuropathy", _AFF)),
        _person(3, ("Polyneuropathy", _AFF)),
    ]
    p = pb.Pedigree(labels=labels, individuals=[*people, proband, _person(5, ("Polio", _CAR))])
    assert _legend(p) == ["Polyneuropathy", "Bone Cancer", "Polio"]


def test_an_unnamed_affected_condition_can_be_primary() -> None:
    # The usual figure: a plain "affected" status (unnamed) beside two named carrier-only conditions. The affected
    # condition is the one to draw black, whole-shape.
    people = [
        _person(1, ("", _AFF)),
        _person(2, ("", _AFF)),
        _person(3, ("HEXA", _CAR)),
        _person(4, ("CFTR", _CAR)),
    ]
    root = _parse(render.render_svg(pb.Pedigree(individuals=people)))
    assert json.loads(root.get("data-conditions", "null")) == ["", "HEXA", "CFTR"]
    affected = _groups(root, "individual")[0]
    (whole,) = _fills(affected)
    assert whole.get("data-condition") == "0" and whole.get("width") == "36", "whole shape, in index 0's fill"
    tone = _patterns(root)["fill-affected-0"].find(f"{_SVG}rect")
    assert tone is not None and tone.get("fill") == "#000000"


def test_a_tie_or_no_affected_keeps_the_base_order() -> None:
    labels = [pb.Label(text=t, kind=pb.LABEL_KIND_PHENOTYPE) for t in "ABC"]
    tied = pb.Pedigree(labels=labels, individuals=[_person(1, ("C", _AFF)), _person(2, ("B", _AFF))])
    assert _legend(tied) == ["B", "A", "C"], "B and C tie; B comes first in the base order"
    carriers = pb.Pedigree(labels=labels, individuals=[_person(1, ("C", _CAR))])
    assert _legend(carriers) == ["A", "B", "C"]


# --- colour mode ----------------------------------------------------------------------------------------------------

_COLOUR = dataclasses.replace(render.DEFAULT_GEOMETRY, palette=render.Palette.COLOUR)


def test_colour_mode_changes_only_the_tones() -> None:
    # The same patterns, ids, sections and hatches; only each pattern's tone (and so its contrasting hatch) changes.
    people = [_person(i + 1, (n, s)) for i, (n, s) in enumerate((n, s) for n in "ABCDEF" for s in (_AFF, _CAR))]
    grey, colour = (_parse(render.render_svg(_sixths(*people), g)) for g in (render.DEFAULT_GEOMETRY, _COLOUR))
    assert sorted(_patterns(grey)) == sorted(_patterns(colour))
    tones = [_patterns(colour)[f"fill-affected-{i}"].find(f"{_SVG}rect").get("fill") for i in range(6)]  # type: ignore[union-attr]
    assert tones[0] == "#000000" and len(set(tones)) == 6, "black for the primary condition, then six colours"
    assert all(t.lower() != "#f0e442" for t in tones if t), "no Okabe-Ito yellow: too light on white"
    for i in range(6):
        carrier = _patterns(colour)[f"fill-carrier-{i}"]
        assert carrier.find(f"{_SVG}rect").get("fill") == tones[i]  # type: ignore[union-attr]
        hatch = carrier.find(f"{_SVG}path")
        assert hatch is not None and hatch.get("stroke") in ("#ffffff", "#000000")
    strip = re.compile(r'fill="#[0-9a-f]{6}"|stroke="#[0-9a-f]{6}"')
    assert strip.sub("", render.render_svg(_sixths(*people))) == strip.sub(
        "", render.render_svg(_sixths(*people), _COLOUR)
    ), "nothing but paint differs"


@pytest.mark.parametrize("name", ["six_conditions", "condition_fills", "compound_carrier"])
def test_colour_mode_keeps_the_hatch_clear_of_edges(name: str) -> None:
    p = ir.load_pbtxt((_GOLDENS / f"{name}.pbtxt").read_text())
    gaps = _hatch_gaps(render.render_svg(p, _COLOUR))
    assert gaps and all(gap >= 2.0 - 1e-6 for _, gap in gaps)
