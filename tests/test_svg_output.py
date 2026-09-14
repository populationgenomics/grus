"""Tests for the SVG document structure (docs/design/svg-output.md).

The drawing itself is pinned byte-for-byte by the goldens (test_render.py); these tests pin the *contract*
a consumer builds on: every drawn thing is a group naming its IR fact, parts are layered in a fixed order,
ids are unique and prefixable, and the minimum label box is a floor the layout honours.
"""

from __future__ import annotations

import dataclasses
import json
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
    order = ["backing", "fill", "symbol", "mark", "label", "hit"]
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
    assert kinds, "the childless glyph's kind is a class on its mating group"


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


def test_label_box_widens_reserved_space_without_moving_the_layout() -> None:
    p = _load("trio")
    geom = render.DEFAULT_GEOMETRY
    wide = dataclasses.replace(geom, label_box_width=20.0, label_box_height=3.0)
    assert render.layout(p, wide).pos == render.layout(p, geom).pos, "the box is a drawing floor, not a layout input"
    base, boxed = _parse(render.render_svg(p, geom)), _parse(render.render_svg(p, wide))
    assert float(boxed.get("width", "0")) > float(base.get("width", "0"))
    assert float(boxed.get("height", "0")) > float(base.get("height", "0"))
    for g in _groups(boxed, "individual"):
        hit = next(c for c in g if "hit" in _classes(c))
        assert float(hit.get("width", "0")) == 20.0 * geom.label_size
        assert float(hit.get("height", "0")) == geom.symbol_size + 3.0 * geom.label_size
