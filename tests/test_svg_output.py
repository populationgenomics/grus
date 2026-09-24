"""Tests for the SVG document structure (docs/design/svg-output.md).

The drawing itself is pinned byte-for-byte by the goldens (test_render.py); these tests pin the *contract*
a consumer builds on: every drawn thing is a group naming its IR fact, parts are layered in a fixed order,
ids are unique and prefixable, and the minimum label box is a floor the layout honours.
"""

from __future__ import annotations

import dataclasses
import itertools
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
