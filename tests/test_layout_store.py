"""Stored layouts (``grus.render._store``, docs/design/layout-store.md).

* **Lossless** — ``Layout`` -> ``PedigreeLayout`` -> ``Layout`` is the identity for every golden and for the synthetic
  cells no golden has (a ghost).
* **Order-independent** — shuffling a pedigree's individuals and matings leaves the stored bytes unchanged, because
  cells are keyed by identity and the digest is canonical.
* **Exact** — drawing from a stored layout is byte-identical to a fresh render.
* **Never stale** — a stored layout of other pedigree content, under other layout geometry, or from another layout
  algorithm version is refused, and a golden whose layout changes without a version bump fails.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import os
import pathlib
from collections.abc import Callable

import protovalidate
import pytest
import test_layout2
import test_render
import test_svg_output

from grus import ir, render
from grus.models import layout_pb2 as lpb
from grus.models import pedigree_pb2 as pb
from grus.render import _store

_GEOM = render.DEFAULT_GEOMETRY


def _fixtures() -> dict[str, pb.Pedigree]:
    """Every drawable golden, plus the shapes with synthetic cells no golden has."""
    out = {name: test_render._load(name) for name in test_layout2._DRAWABLE}
    out["ghost"] = test_svg_output._avuncular()
    out["two_ghosts"] = test_svg_output._avuncular(second_niece=True)
    out.update(test_layout2.GHOST_PEDIGREES)
    return out


_FIXTURES = _fixtures()


def _record(p: pb.Pedigree) -> lpb.PedigreeLayout:
    return _store.to_proto(p, render.layout(p, _GEOM), _GEOM)


def test_fixtures_cover_every_kind_of_cell() -> None:
    layouts = [render.layout(p) for p in _FIXTURES.values()]
    assert any(lay.ghost_of for lay in layouts)
    assert any(lay.phantom for lay in layouts)
    assert any(lay.passthrough for lay in layouts)
    assert any(lay.founder_sibships for lay in layouts)


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_round_trip_is_lossless(name: str) -> None:
    p = _FIXTURES[name]
    lay = render.layout(p, _GEOM)
    record = _store.to_proto(p, lay, _GEOM)
    protovalidate.validate(record)
    back = lpb.PedigreeLayout.FromString(record.SerializeToString())
    assert _store.from_proto(p, back.placement, _GEOM) == lay


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_record_is_independent_of_input_order(name: str) -> None:
    p = _FIXTURES[name]
    ref = _record(p).SerializeToString(deterministic=True)
    for seed in range(4):
        q = test_layout2._shuffled(p, seed)
        assert _record(q).SerializeToString(deterministic=True) == ref


@pytest.mark.parametrize("name", sorted(test_layout2.GHOST_PEDIGREES))
def test_ghost_records_are_independent_of_mating_order(name: str) -> None:
    # A record of one mating order draws every other order exactly as a fresh render of it does.
    p = test_layout2.GHOST_PEDIGREES[name]
    ref = _record(p)
    for perm in itertools.permutations(p.matings):
        q = pb.Pedigree()
        q.CopyFrom(p)
        del q.matings[:]
        q.matings.extend(perm)
        assert _record(q).SerializeToString(deterministic=True) == ref.SerializeToString(deterministic=True)
        assert render.render_svg(q, stored_layout=ref) == render.render_svg(q)


def test_repeated_avuncular_marriages_store_distinct_ghosts() -> None:
    record = render.store_layout(test_layout2.GHOST_PEDIGREES["uncle_niece_twice"])
    ghosts = [c.ghost for row in record.placement.rows for c in row.cells if c.HasField("ghost")]
    assert sorted((g.first_child.index, g.occurrence) for g in ghosts) == [(1, 0), (2, 0)]


def test_record_reads_back_into_a_shuffled_pedigree() -> None:
    # The record names identities, so it maps onto any input order of the same pedigree.
    p = _FIXTURES["two_ghosts"]
    record = _record(p)
    q = test_layout2._shuffled(p, 1)
    assert _store.from_proto(q, record.placement, _GEOM) == render.layout(q, _GEOM)


def test_digest_ignores_order_but_not_content() -> None:
    p = _FIXTURES["three_generation"]
    assert _store.pedigree_digest(test_layout2._shuffled(p, 0)) == _store.pedigree_digest(p)
    q = pb.Pedigree()
    q.CopyFrom(p)
    q.individuals[0].annotations.add(text="d.72", type=pb.ANNOTATION_TYPE_AGE_AT_DEATH)
    assert _store.pedigree_digest(q) != _store.pedigree_digest(p)


def test_digest_keeps_birth_order() -> None:
    p = _FIXTURES["sibship"]
    q = pb.Pedigree()
    q.CopyFrom(p)
    offspring = list(q.matings[0].offspring)
    del q.matings[0].offspring[:]
    q.matings[0].offspring.extend(reversed(offspring))
    assert _store.pedigree_digest(q) != _store.pedigree_digest(p)


def test_a_cell_the_pedigree_does_not_lay_out_is_refused() -> None:
    p = _FIXTURES["sibship"]
    record = _record(p)
    record.placement.rows[0].cells[0].individual.index = 99
    with pytest.raises(_store.StaleLayoutError, match="does not lay out"):
        _store.from_proto(p, record.placement, _GEOM)


def test_a_missing_cell_is_refused() -> None:
    p = _FIXTURES["sibship"]
    record = _record(p)
    del record.placement.rows[-1].cells[-1]
    with pytest.raises(_store.StaleLayoutError, match="missing"):
        _store.from_proto(p, record.placement, _GEOM)


def test_a_column_outside_its_row_is_refused() -> None:
    p = _FIXTURES["sibship"]
    record = _record(p)
    record.placement.rows[1].cells[0].parent_column = 7
    with pytest.raises(_store.StaleLayoutError, match="parent column 7"):
        _store.from_proto(p, record.placement, _GEOM)


# --- drawing from a stored layout ---------------------------------------------------------------------------------

# The layout-version pins: for each LAYOUT_VERSION, solver and golden, the golden's pedigree digest and the SHA-256 of
# its stored placement. A pin's placement may change only together with its pedigree digest (the golden's IR was
# edited); a changed placement under an unchanged digest is a layout change, which takes a LAYOUT_VERSION bump (old
# stored layouts must read as stale) and pins under the new version. A pin whose golden is gone fails until removed.
# GRUS_REPIN_LAYOUTS=1 writes missing pins and pins whose pedigree digest changed, and nothing else. A deliberate edit
# of both a golden and its pin is out of reach of this test.
_PINS = pathlib.Path(__file__).parent / "layout_pins.json"
_REPIN = os.environ.get("GRUS_REPIN_LAYOUTS") == "1"
_SOLVERS = {"z3": render.XSolver.Z3, "highs": render.XSolver.HIGHS}


@pytest.mark.parametrize("solver", sorted(_SOLVERS))
def test_layout_version_is_bumped_when_a_golden_layout_changes(solver: str) -> None:
    if solver == "highs":
        pytest.importorskip("highspy")
    geom = dataclasses.replace(_GEOM, x_solver=_SOLVERS[solver])
    pins = json.loads(_PINS.read_text())
    pinned: dict[str, dict[str, str]] = pins.setdefault(str(render.LAYOUT_VERSION), {}).setdefault(solver, {})
    problems = [f"{name}: its golden is gone; remove the pin" for name in sorted(set(pinned) - set(test_render._NAMES))]
    for name in test_render._NAMES:
        record = render.store_layout(test_render._load(name), geom)
        now = {
            "pedigree": record.key.pedigree_digest.hex(),
            "placement": hashlib.sha256(record.placement.SerializeToString(deterministic=True)).hexdigest(),
        }
        pin = pinned.get(name)
        if pin == now:
            continue
        if pin is not None and pin["pedigree"] == now["pedigree"]:
            problems.append(f"{name}: layout changed under an unchanged pedigree; bump LAYOUT_VERSION")
        elif _REPIN:
            pinned[name] = now
        else:
            problems.append(f"{name}: {'not pinned' if pin is None else 'IR edited'}; repin with GRUS_REPIN_LAYOUTS=1")
    if _REPIN:
        _PINS.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n")
    assert not problems, f"LAYOUT_VERSION {render.LAYOUT_VERSION}, {solver}: {problems}"


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_drawing_from_a_stored_layout_is_byte_identical(name: str) -> None:
    p = _FIXTURES[name]
    stored = lpb.PedigreeLayout.FromString(render.store_layout(p).SerializeToString())
    assert render.render_svg(p, stored_layout=stored, id_prefix="f-") == render.render_svg(p, id_prefix="f-")


def test_drawing_only_geometry_may_vary() -> None:
    p = _FIXTURES["carrier_inheritance"]
    stored = render.store_layout(p)
    # Row pitch, margins, stubs and the vertical label rhythm move nothing horizontally. symbol_size and carrier_style
    # did, once count marks and labels beside a drop entered the spacing; they are in the key now.
    geom = dataclasses.replace(
        _GEOM, gen_height=120.0, margin=10.0, sib_stub=30.0, elbow_gap=10.0, label_line_gap=4.0, label_box_height=3.0
    )
    assert render.render_svg(p, geom, stored_layout=stored) == render.render_svg(p, geom)


def test_a_stored_layout_draws_a_shuffled_pedigree() -> None:
    p = _FIXTURES["two_ghosts"]
    q = test_layout2._shuffled(p, 2)
    assert render.render_svg(q, stored_layout=render.store_layout(p)) == render.render_svg(q)


_KEY_FIELDS = (
    "couple_gap",
    "sib_gap",
    "label_size",
    "label_box_width",
    "x_unit",
    "x_solver",
    "label_gap",
    "symbol_size",
    "carrier_style",
)


@pytest.mark.parametrize("field", _KEY_FIELDS)
def test_a_layout_under_other_layout_geometry_is_stale(field: str) -> None:
    p = _FIXTURES["three_generation"]
    stored = render.store_layout(p)
    enums = {"x_solver": render.XSolver.HIGHS, "carrier_style": render.CarrierStyle.INHERITANCE_GLYPH}
    changed = enums[field] if field in enums else getattr(_GEOM, field) + 1.0
    geom = dataclasses.replace(_GEOM, **{field: changed})
    with pytest.raises(render.StaleLayoutError, match=field):
        render.render_svg(p, geom, stored_layout=stored)


def test_a_layout_of_other_pedigree_content_is_stale() -> None:
    p = _FIXTURES["three_generation"]
    stored = render.store_layout(p)
    q = pb.Pedigree()
    q.CopyFrom(p)
    q.individuals[0].deceased = not q.individuals[0].deceased
    with pytest.raises(render.StaleLayoutError, match="digest"):
        render.render_svg(q, stored_layout=stored)


def test_a_layout_from_another_algorithm_version_is_stale() -> None:
    p = _FIXTURES["three_generation"]
    stored = render.store_layout(p)
    stored.key.algorithm_version = render.LAYOUT_VERSION + 1
    with pytest.raises(render.StaleLayoutError, match="version"):
        render.render_svg(p, stored_layout=stored)


def test_a_malformed_record_is_refused() -> None:
    p = _FIXTURES["three_generation"]
    stored = render.store_layout(p)
    stored.key.ClearField("geometry")
    with pytest.raises(ir.ValidationError):
        render.render_svg(p, stored_layout=stored)


def test_a_deferral_is_stored_and_replayed() -> None:
    p = _deferred_pedigree()
    with pytest.raises(render.DeferredFeatureError) as fresh:
        render.layout(p)
    stored = render.store_layout(p)
    assert stored.deferred == str(fresh.value)
    with pytest.raises(render.DeferredFeatureError, match="more than one mating"):
        render.render_svg(p, stored_layout=stored)
    ps = pb.PedigreeSet(pedigrees=[_FIXTURES["trio"], p])
    stored_set = [render.store_layout(ped) for ped in ps.pedigrees]
    assert render.render_set_svg(ps, stored_layouts=stored_set) == render.render_set_svg(ps)
    assert render.render_svgs(ps, stored_layouts=stored_set) == render.render_svgs(ps)


def test_a_set_needs_one_stored_layout_per_pedigree() -> None:
    ps = pb.PedigreeSet(pedigrees=[_FIXTURES["trio"], _FIXTURES["sibship"]])
    with pytest.raises(render.StaleLayoutError, match="1 stored layouts for a set of 2"):
        render.render_set_svg(ps, stored_layouts=[render.store_layout(ps.pedigrees[0])])


# --- an internally inconsistent record (a serializer or version bug) ------------------------------------------------


def _edited(name: str, edit: Callable[[lpb.Placement], None]) -> lpb.PedigreeLayout:
    """``name``'s stored layout with ``edit`` applied to its placement."""
    record = render.store_layout(_FIXTURES[name])
    edit(record.placement)
    return record


def _swap_identities(row: lpb.Row, a: int, b: int) -> None:
    first, second = lpb.Cell(), lpb.Cell()
    first.CopyFrom(row.cells[a])
    second.CopyFrom(row.cells[b])
    row.cells[a].individual.CopyFrom(second.individual)
    row.cells[b].individual.CopyFrom(first.individual)


def _bump_first_generation(pl: lpb.Placement) -> None:
    pl.first_generation += 1


def _move_left_child_past_its_sibling(pl: lpb.Placement) -> None:
    pl.rows[2].cells[0].x += 7


def _couple_two_siblings(pl: lpb.Placement) -> None:
    pl.rows[2].cells[0].couple_right = lpb.COUPLE_LINE_DOUBLE


def _orphan_a_child(pl: lpb.Placement) -> None:
    pl.rows[2].cells[1].ClearField("parent_column")


def _flip_lone(pl: lpb.Placement) -> None:
    pl.rows[2].cells[0].lone = True


def _crowd_a_couple(pl: lpb.Placement) -> None:
    pl.rows[0].cells[1].x = 0.5


def _route_a_couple(pl: lpb.Placement) -> None:
    pl.routed.add(a=lpb.CellRef(row=0, column=0), b=lpb.CellRef(row=0, column=1))


def _mark_twins_by_adjacency(pl: lpb.Placement) -> None:
    pl.rows[1].cells[0].twin_right = pb.ZYGOSITY_TYPE_MONOZYGOTIC  # layout version 1's form, replaced by twin_groups


_EDITS: dict[str, tuple[str, Callable[[lpb.Placement], None], str]] = {
    "first_generation": ("three_generation", _bump_first_generation, "first_generation"),
    "swapped_couple": ("three_generation", lambda pl: _swap_identities(pl.rows[1], 0, 1), "fam"),
    "moved_x": ("three_generation", _move_left_child_past_its_sibling, "increasing x"),
    "added_couple": ("three_generation", _couple_two_siblings, "spouse"),
    "dropped_parent": ("three_generation", _orphan_a_child, "fam"),
    "flipped_lone": ("three_generation", _flip_lone, "lone"),
    "crowded": ("three_generation", _crowd_a_couple, "need"),
    "dropped_founder_sibship": ("founder_sibship", lambda pl: pl.ClearField("founder_sibships"), "founder_sibships"),
    "dropped_twin_group": ("twins", lambda pl: pl.ClearField("twin_groups"), "twin_groups"),
    "twin_right": ("twins", _mark_twins_by_adjacency, "twin_right"),
    "routed": ("three_generation", _route_a_couple, "routes a mating"),
    "couple_apart": ("childless", lambda pl: _swap_identities(pl.rows[1], 1, 2), "not adjacent"),
}


@pytest.mark.parametrize("case", sorted(_EDITS))
def test_an_inconsistent_record_is_refused(case: str) -> None:
    name, edit, why = _EDITS[case]
    with pytest.raises(render.StaleLayoutError, match=why):
        render.render_svg(_FIXTURES[name], stored_layout=_edited(name, edit))


def _deferred_pedigree() -> pb.Pedigree:
    """A child of two matings: a topology the layout defers."""
    people = [pb.Individual(generation=1, index=i, gender=pb.GENDER_UNKNOWN) for i in range(1, 5)]
    people.append(pb.Individual(generation=2, index=1, gender=pb.GENDER_UNKNOWN))
    child = pb.Offspring(child=pb.Position(generation=2, index=1))
    matings = [
        pb.Mating(
            partner_a=pb.Position(generation=1, index=a),
            partner_b=pb.Position(generation=1, index=b),
            offspring=[child],
        )
        for a, b in ((1, 2), (3, 4))
    ]
    return pb.Pedigree(individuals=people, matings=matings)


def test_every_geometry_field_the_layout_reads_is_in_the_key() -> None:
    # The key names the Geometry fields positions depend on, by hand; the layout path grew three (label_gap,
    # symbol_size, carrier_style, with count marks and labels beside a drop) before the key did. Every `geom.<field>`
    # the layout modules read must be in the key or be drawing-only by construction (none are, today).
    import re

    from grus.render import _labels, _layout, _layout2, _ordering, _xsolve

    read: set[str] = set()
    for module in (_layout, _layout2, _labels, _ordering, _xsolve):
        assert module.__file__ is not None
        read |= set(re.findall(r"\bgeom\.([a-z_]+)", pathlib.Path(module.__file__).read_text()))
    key = {f.name for f in lpb.LayoutGeometry.DESCRIPTOR.fields}
    assert set(_KEY_FIELDS) == key, "the staleness test must cover every key field"
    assert read <= key, f"layout reads geometry not in the stored-layout key: {sorted(read - key)}"


def test_citation_supports_leave_a_stored_layout_valid() -> None:
    # Evidence and supports are provenance the layout never reads: adding them keeps the digest, so a layout stored
    # before they were added draws without a relayout.
    p = test_render._load("trio")
    stored = render.store_layout(p)
    cited = pb.Pedigree()
    cited.CopyFrom(p)
    cited.evidence.append("panel-a")
    cited.supports.add(citations=["cap"], field="proband", individuals=[pb.Position(generation=2, index=1)])
    assert _store.pedigree_digest(cited) == _store.pedigree_digest(p)
    assert render.render_svg(cited, stored_layout=stored) == render.render_svg(p)


# --- the digest covers what layout and drawing read, and only that ------------------------------------------------


def _reachable(desc, seen=None):  # type: ignore[no-untyped-def]
    """Every message descriptor the digest walks into from ``desc``, through RENDERED message fields."""
    seen = {} if seen is None else seen
    seen[desc.name] = desc
    for field in desc.fields:
        rendered = field.name in _store.RENDERED.get(desc.name, ())
        if rendered and field.message_type is not None and field.message_type.name not in seen:
            _reachable(field.message_type, seen)
    return seen


def test_every_digested_field_is_classified() -> None:
    # A new IR field fails here until it is marked rendered (hashed) or not (cleared), so a provenance-like field
    # cannot silently mark layouts stale and a drawn one cannot silently be ignored.
    reached = _reachable(pb.Pedigree.DESCRIPTOR)
    assert set(reached) == set(_store.RENDERED) == set(_store.NOT_RENDERED)
    for name, desc in reached.items():
        rendered, not_rendered = _store.RENDERED[name], _store.NOT_RENDERED[name]
        assert not rendered & not_rendered, f"{name}: {sorted(rendered & not_rendered)} classified twice"
        assert {f.name for f in desc.fields} == rendered | not_rendered, f"{name}: unclassified fields"


def _sample() -> pb.Pedigree:
    return test_render._load("condition_fills")


def _mating_with_children(p: pb.Pedigree) -> pb.Mating:
    p.matings.add(
        partner_a=pb.Position(generation=1, index=1),
        partner_b=pb.Position(generation=1, index=2),
        offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
    )
    return p.matings[-1]


# One edit per NOT_RENDERED field: the digest must not move.
_UNRENDERED_EDITS: dict[tuple[str, str], Callable[[pb.Pedigree], object]] = {
    ("Pedigree", "id"): lambda p: setattr(p, "id", "another-id"),
    ("Pedigree", "provenance"): lambda p: setattr(p.provenance, "doi", "10.1/x"),
    ("Pedigree", "evidence"): lambda p: p.evidence.append("c1"),
    ("Pedigree", "supports"): lambda p: p.supports.add(citations=["c1"], field="labels"),
    ("Individual", "sex_assigned_at_birth"): lambda p: setattr(
        p.individuals[0], "sex_assigned_at_birth", pb.SEX_ASSIGNED_AT_BIRTH_FEMALE
    ),
    ("Individual", "reproductive_outcome"): lambda p: setattr(
        p.individuals[0], "reproductive_outcome", pb.REPRODUCTIVE_OUTCOME_STILLBIRTH
    ),
    ("Individual", "reproductive_role"): lambda p: setattr(
        p.individuals[0], "reproductive_role", pb.REPRODUCTIVE_ROLE_GAMETE_DONOR
    ),
    ("Individual", "documented_evaluation"): lambda p: setattr(p.individuals[0], "documented_evaluation", True),
    ("Condition", "onset_age"): lambda p: setattr(p.individuals[0].conditions[0], "onset_age", "40s"),
    ("Annotation", "type"): lambda p: p.individuals[0].annotations.add(text="N/M", type=pb.ANNOTATION_TYPE_GENOTYPE),
    ("Mating", "status"): lambda p: setattr(_mating_with_children(p), "status", pb.RELATIONSHIP_STATUS_DIVORCED),
    ("Mating", "annotations"): lambda p: _mating_with_children(p).annotations.add(
        text="vasectomy", type=pb.ANNOTATION_TYPE_OTHER
    ),
    ("Offspring", "parentage"): lambda p: setattr(
        _mating_with_children(p).offspring[0], "parentage", pb.PARENTAGE_ADOPTIVE
    ),
    ("Offspring", "adoption"): lambda p: setattr(_mating_with_children(p).offspring[0], "adoption", pb.ADOPTION_IN),
}


def test_edits_to_unrendered_fields_leave_the_digest() -> None:
    assert set(_UNRENDERED_EDITS) == {(m, f) for m, fs in _store.NOT_RENDERED.items() for f in fs}
    for (message, field), edit in _UNRENDERED_EDITS.items():
        base = _sample()
        if message in ("Mating", "Offspring"):
            _mating_with_children(base)  # the edit's mating, unedited
        edited = pb.Pedigree()
        edited.CopyFrom(_sample())
        edit(edited)
        if message == "Annotation":
            base.individuals[0].annotations.add(text="N/M")  # the same line, typed or not
        assert _store.pedigree_digest(edited) == _store.pedigree_digest(base), f"{message}.{field} moved the digest"


# One edit per RENDERED scalar field (message-typed ones are covered by their contents): the digest must move.
_RENDERED_EDITS: dict[tuple[str, str], Callable[[pb.Pedigree], object]] = {
    ("Label", "text"): lambda p: setattr(p.labels[0], "text", p.labels[0].text + "!"),
    ("Label", "kind"): lambda p: setattr(p.labels[0], "kind", pb.LABEL_KIND_PANEL),
    ("Individual", "generation"): lambda p: setattr(p.individuals[-1], "generation", 9),
    ("Individual", "index"): lambda p: setattr(p.individuals[-1], "index", 99),
    ("Individual", "gender"): lambda p: setattr(p.individuals[0], "gender", pb.GENDER_UNKNOWN),
    ("Individual", "deceased"): lambda p: setattr(p.individuals[0], "deceased", True),
    ("Individual", "proband"): lambda p: setattr(p.individuals[0], "proband", True),
    ("Individual", "consultand"): lambda p: setattr(p.individuals[0], "consultand", True),
    ("Individual", "count"): lambda p: setattr(p.individuals[0], "count", 3),
    ("Individual", "count_unspecified"): lambda p: setattr(p.individuals[0], "count_unspecified", True),
    ("Individual", "external_id"): lambda p: setattr(p.individuals[0], "external_id", "S-17"),
    ("Condition", "name"): lambda p: setattr(p.individuals[0].conditions[0], "name", "Z"),
    ("Condition", "status"): lambda p: setattr(p.individuals[0].conditions[0], "status", pb.CONDITION_STATUS_UNKNOWN),
    ("Condition", "inheritance"): lambda p: setattr(
        p.individuals[0].conditions[0], "inheritance", pb.INHERITANCE_X_LINKED_RECESSIVE
    ),
    ("Annotation", "text"): lambda p: p.individuals[0].annotations.add(text="N/M"),
    ("Mating", "consanguineous"): lambda p: setattr(_mating_with_children(p), "consanguineous", True),
    ("Mating", "childlessness"): lambda p: setattr(
        p.matings.add(partner_a=pb.Position(generation=1, index=1), partner_b=pb.Position(generation=1, index=2)),
        "childlessness",
        pb.CHILDLESSNESS_BY_CHOICE,
    ),
    ("Mating", "partner_a"): lambda p: _mating_with_children(p),
    ("Mating", "partner_b"): lambda p: _mating_with_children(p).ClearField("partner_b"),
    ("Offspring", "child"): lambda p: (
        _mating_with_children(p).offspring[0].child.CopyFrom(pb.Position(generation=2, index=2))
    ),
    ("Offspring", "twin_group"): lambda p: setattr(_mating_with_children(p).offspring[0], "twin_group", 1),
    ("Offspring", "twin_type"): lambda p: setattr(
        _mating_with_children(p).offspring[0], "twin_type", pb.ZYGOSITY_TYPE_MONOZYGOTIC
    ),
    ("Position", "generation"): lambda p: setattr(_mating_with_children(p).partner_a, "generation", 3),
    ("Position", "index"): lambda p: setattr(_mating_with_children(p).partner_a, "index", 7),
}


def test_edits_to_rendered_fields_move_the_digest() -> None:
    scalars = {
        (m, f.name)
        for m, desc in _reachable(pb.Pedigree.DESCRIPTOR).items()
        for f in desc.fields
        if f.name in _store.RENDERED[m] and f.message_type is None
    }
    assert scalars <= set(_RENDERED_EDITS), f"no edit for {sorted(scalars - set(_RENDERED_EDITS))}"
    base = _store.pedigree_digest(_sample())
    for (message, field), edit in _RENDERED_EDITS.items():
        edited = _sample()
        edit(edited)
        assert _store.pedigree_digest(edited) != base, f"{message}.{field} did not move the digest"
