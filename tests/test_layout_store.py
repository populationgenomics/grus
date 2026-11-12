"""Stored layouts (``grus.render._store``, docs/design/layout-store.md).

* **Lossless** — ``Layout`` -> ``PedigreeLayout`` -> ``Layout`` is the identity for every golden and for the synthetic
  cells no golden has (a ghost).
* **Order-independent** — shuffling a pedigree's individuals and matings leaves the stored bytes unchanged, because
  cells are keyed by identity and the digest is canonical.
* **Exact** — drawing from a stored layout is byte-identical to a fresh render.
* **Layout only** — the digest covers every field layout reads and no other: an edit to a cleared field leaves the
  stored layout unchanged and draws from it as a fresh render does, an edit to a hashed scalar moves the digest.
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
from typing import cast

import protovalidate
import pytest
import test_layout2
import test_render
import test_svg_output
from google.protobuf import descriptor

from grus import ir, render
from grus.ir import _migrate
from grus.models import layout_pb2 as lpb
from grus.models import pedigree_pb2 as pb
from grus.render import _store

_GEOM = render.DEFAULT_GEOMETRY


_Defs = tuple[pb.ConditionDef, ...]


def _fixtures() -> dict[str, tuple[pb.Pedigree, _Defs]]:
    """Every drawable golden, plus the shapes with synthetic cells no golden has; each with its declarations."""
    out = {name: test_render._golden(name) for name in test_layout2._DRAWABLE}
    out["ghost"] = (test_svg_output._avuncular(), ())
    out["two_ghosts"] = (test_svg_output._avuncular(second_niece=True), ())
    out.update({name: (p, ()) for name, p in test_layout2.GHOST_PEDIGREES.items()})
    return out


_FIXTURES = _fixtures()


def _record(p: pb.Pedigree, defs: _Defs = ()) -> lpb.PedigreeLayout:
    return _store.to_proto(p, render.layout(p, _GEOM, conditions=defs), _GEOM, defs)


def test_fixtures_cover_every_kind_of_cell() -> None:
    layouts = [render.layout(p, conditions=defs) for p, defs in _FIXTURES.values()]
    assert any(lay.ghost_of for lay in layouts)
    assert any(lay.phantom for lay in layouts)
    assert any(lay.passthrough for lay in layouts)
    assert any(lay.founder_sibships for lay in layouts)


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_round_trip_is_lossless(name: str) -> None:
    p, defs = _FIXTURES[name]
    lay = render.layout(p, _GEOM, conditions=defs)
    record = _store.to_proto(p, lay, _GEOM, defs)
    protovalidate.validate(record)
    back = lpb.PedigreeLayout.FromString(record.SerializeToString())
    assert _store.from_proto(p, back.placement, _GEOM, defs) == lay


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_record_is_independent_of_input_order(name: str) -> None:
    p, defs = _FIXTURES[name]
    ref = _record(p, defs).SerializeToString(deterministic=True)
    for seed in range(4):
        q = test_layout2._shuffled(p, seed)
        assert _record(q, defs).SerializeToString(deterministic=True) == ref


@pytest.mark.parametrize("name", sorted(test_layout2.GHOST_PEDIGREES))
def test_ghost_records_are_independent_of_mating_order(name: str) -> None:
    # A record of one mating order draws every other order exactly as a fresh render of it does.
    p, defs = test_layout2.GHOST_PEDIGREES[name], ()
    ref = _record(p, defs)
    for perm in itertools.permutations(p.matings):
        q = pb.Pedigree()
        q.CopyFrom(p)
        del q.matings[:]
        q.matings.extend(perm)
        assert _record(q, defs).SerializeToString(deterministic=True) == ref.SerializeToString(deterministic=True)
        assert render.render_svg(q, stored_layout=ref, conditions=defs) == render.render_svg(q, conditions=defs)


def test_repeated_avuncular_marriages_store_distinct_ghosts() -> None:
    record = render.store_layout(test_layout2.GHOST_PEDIGREES["uncle_niece_twice"])
    ghosts = [c.ghost for row in record.placement.rows for c in row.cells if c.HasField("ghost")]
    assert sorted((g.first_child.index, g.occurrence) for g in ghosts) == [(1, 0), (2, 0)]


def test_record_reads_back_into_a_shuffled_pedigree() -> None:
    # The record names identities, so it maps onto any input order of the same pedigree.
    p, defs = _FIXTURES["two_ghosts"]
    record = _record(p, defs)
    q = test_layout2._shuffled(p, 1)
    assert _store.from_proto(q, record.placement, _GEOM, defs) == render.layout(q, _GEOM, conditions=defs)


def test_digest_ignores_order_but_not_content() -> None:
    p, defs = _FIXTURES["three_generation"]
    assert _store.pedigree_digest(test_layout2._shuffled(p, 0), defs) == _store.pedigree_digest(p, defs)
    q = pb.Pedigree()
    q.CopyFrom(p)
    q.individuals[0].annotations.add(text="d.72", type=pb.ANNOTATION_TYPE_AGE_AT_DEATH)
    assert _store.pedigree_digest(q, defs) != _store.pedigree_digest(p, defs)


def test_digest_keeps_birth_order() -> None:
    p, defs = _FIXTURES["sibship"]
    q = pb.Pedigree()
    q.CopyFrom(p)
    offspring = list(q.matings[0].offspring)
    del q.matings[0].offspring[:]
    q.matings[0].offspring.extend(reversed(offspring))
    assert _store.pedigree_digest(q, defs) != _store.pedigree_digest(p, defs)


def test_a_cell_the_pedigree_does_not_lay_out_is_refused() -> None:
    p, defs = _FIXTURES["sibship"]
    record = _record(p, defs)
    record.placement.rows[0].cells[0].individual.index = 99
    with pytest.raises(_store.StaleLayoutError, match="does not lay out"):
        _store.from_proto(p, record.placement, _GEOM, defs)


def test_a_missing_cell_is_refused() -> None:
    p, defs = _FIXTURES["sibship"]
    record = _record(p, defs)
    del record.placement.rows[-1].cells[-1]
    with pytest.raises(_store.StaleLayoutError, match="missing"):
        _store.from_proto(p, record.placement, _GEOM, defs)


def test_a_column_outside_its_row_is_refused() -> None:
    p, defs = _FIXTURES["sibship"]
    record = _record(p, defs)
    record.placement.rows[1].cells[0].parent_column = 7
    with pytest.raises(_store.StaleLayoutError, match="parent column 7"):
        _store.from_proto(p, record.placement, _GEOM, defs)


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
        p, defs = test_render._golden(name)
        record = render.store_layout(p, geom, conditions=defs)
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
    p, defs = _FIXTURES[name]
    stored = lpb.PedigreeLayout.FromString(render.store_layout(p, conditions=defs).SerializeToString())
    assert render.render_svg(p, stored_layout=stored, id_prefix="f-", conditions=defs) == render.render_svg(
        p, id_prefix="f-", conditions=defs
    )


def test_drawing_only_geometry_may_vary() -> None:
    p, defs = _FIXTURES["carrier_inheritance"]
    stored = render.store_layout(p, conditions=defs)
    # Row pitch, margins, stubs and the vertical label rhythm move nothing horizontally. symbol_size and carrier_style
    # did, once count marks and labels beside a drop entered the spacing; they are in the key now.
    geom = dataclasses.replace(
        _GEOM, gen_height=120.0, margin=10.0, sib_stub=30.0, elbow_gap=10.0, label_line_gap=4.0, label_box_height=3.0
    )
    assert render.render_svg(p, geom, stored_layout=stored, conditions=defs) == render.render_svg(
        p, geom, conditions=defs
    )


def test_a_stored_layout_draws_a_shuffled_pedigree() -> None:
    p, defs = _FIXTURES["two_ghosts"]
    q = test_layout2._shuffled(p, 2)
    assert render.render_svg(q, stored_layout=render.store_layout(p, conditions=defs)) == render.render_svg(
        q, conditions=defs
    )


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
    p, defs = _FIXTURES["three_generation"]
    stored = render.store_layout(p, conditions=defs)
    enums = {"x_solver": render.XSolver.HIGHS, "carrier_style": render.CarrierStyle.INHERITANCE_GLYPH}
    changed = enums[field] if field in enums else getattr(_GEOM, field) + 1.0
    geom = dataclasses.replace(_GEOM, **{field: changed})
    with pytest.raises(render.StaleLayoutError, match=field):
        render.render_svg(p, geom, stored_layout=stored, conditions=defs)


def test_a_layout_of_other_pedigree_content_is_stale() -> None:
    p, defs = _FIXTURES["three_generation"]
    stored = render.store_layout(p, conditions=defs)
    q = pb.Pedigree()
    q.CopyFrom(p)
    q.individuals[0].deceased = not q.individuals[0].deceased
    with pytest.raises(render.StaleLayoutError, match="digest"):
        render.render_svg(q, stored_layout=stored, conditions=defs)


def test_a_layout_from_another_algorithm_version_is_stale() -> None:
    p, defs = _FIXTURES["three_generation"]
    stored = render.store_layout(p, conditions=defs)
    stored.key.algorithm_version = render.LAYOUT_VERSION + 1
    with pytest.raises(render.StaleLayoutError, match="version"):
        render.render_svg(p, stored_layout=stored, conditions=defs)


def test_a_malformed_record_is_refused() -> None:
    p, defs = _FIXTURES["three_generation"]
    stored = render.store_layout(p, conditions=defs)
    stored.key.ClearField("geometry")
    with pytest.raises(ir.ValidationError):
        render.render_svg(p, stored_layout=stored, conditions=defs)


def test_a_deferral_is_stored_and_replayed() -> None:
    p = _deferred_pedigree()
    with pytest.raises(render.DeferredFeatureError) as fresh:
        render.layout(p)
    stored = render.store_layout(p)
    assert stored.deferred == str(fresh.value)
    with pytest.raises(render.DeferredFeatureError, match="more than one mating"):
        render.render_svg(p, stored_layout=stored)
    ps = test_render._set_of("trio")
    ps.pedigrees.append(p)
    stored_set = [render.store_layout(ped, conditions=ps.conditions) for ped in ps.pedigrees]
    assert render.render_set_svg(ps, stored_layouts=stored_set) == render.render_set_svg(ps)
    assert render.render_svgs(ps, stored_layouts=stored_set) == render.render_svgs(ps)


def test_a_set_needs_one_stored_layout_per_pedigree() -> None:
    ps = test_render._set_of("trio", "sibship")
    with pytest.raises(render.StaleLayoutError, match="1 stored layouts for a set of 2"):
        render.render_set_svg(ps, stored_layouts=[render.store_layout(ps.pedigrees[0], conditions=ps.conditions)])


# --- an internally inconsistent record (a serializer or version bug) ------------------------------------------------


def _edited(name: str, edit: Callable[[lpb.Placement], None]) -> lpb.PedigreeLayout:
    """``name``'s stored layout with ``edit`` applied to its placement."""
    p, defs = _FIXTURES[name]
    record = render.store_layout(p, conditions=defs)
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
    p, defs = _FIXTURES[name]
    with pytest.raises(render.StaleLayoutError, match=why):
        render.render_svg(p, stored_layout=_edited(name, edit), conditions=defs)


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
    p, defs = test_render._golden("trio")
    stored = render.store_layout(p, conditions=defs)
    cited = pb.Pedigree()
    cited.CopyFrom(p)
    cited.evidence.append("panel-a")
    cited.supports.add(citations=["cap"], field="proband", individuals=[pb.Position(generation=2, index=1)])
    assert _store.pedigree_digest(cited, defs) == _store.pedigree_digest(p, defs)
    assert render.render_svg(cited, stored_layout=stored, conditions=defs) == render.render_svg(p, conditions=defs)


# --- the digest covers what layout reads, and only that -----------------------------------------------------------

# The encoding the digest serializes; Message.DESCRIPTOR is typed as the C (upb) or Python descriptor.
_DIGESTED = cast(descriptor.Descriptor, _migrate.legacy_pedigree(pb.Pedigree(), ()).DESCRIPTOR)


def _reachable(
    desc: descriptor.Descriptor, seen: dict[str, descriptor.Descriptor] | None = None
) -> dict[str, descriptor.Descriptor]:
    """Every message descriptor the digest walks into from ``desc``, through LAYOUT_READS message fields."""
    seen = {} if seen is None else seen
    seen[desc.name] = desc
    for field in desc.fields:
        read = field.name in _store.LAYOUT_READS.get(desc.name, ())
        if read and field.message_type is not None and field.message_type.name not in seen:
            _reachable(field.message_type, seen)
    return seen


def _classified() -> dict[str, descriptor.Descriptor]:
    """Every classified message: those the digest walks into, and the declarations their entries are resolved by."""
    return _reachable(_DIGESTED) | {"ConditionDef": cast(descriptor.Descriptor, pb.ConditionDef.DESCRIPTOR)}


def test_every_digested_field_is_classified() -> None:
    reached = _classified()
    assert set(reached) == set(_store.LAYOUT_READS) == set(_store.LAYOUT_IGNORES)
    for name, desc in reached.items():
        read, ignored = _store.LAYOUT_READS[name], _store.LAYOUT_IGNORES[name]
        assert not read & ignored, f"{name}: {sorted(read & ignored)} classified twice"
        assert {f.name for f in desc.fields} == read | ignored, f"{name}: unclassified fields"


# The sample's one locus, which its first person is genotyped at.
_LOCI = [pb.Locus(id="l1", alleles=[pb.Allele(symbol="N", reference=True), pb.Allele(symbol="M")])]


def _sample() -> tuple[pb.Pedigree, list[pb.ConditionDef]]:
    """A golden with a condition on every person, plus a mating, an annotation, a genotype and a count to edit.

    The genotype is at ``_LOCI``, which every render of the sample passes.

    A fifth condition puts it in sixths, where any one fill has borders, so the count sits beside its symbol.
    """
    p, defs = test_render._golden("condition_fills")
    p.matings.add(
        partner_a=pb.Position(generation=1, index=1),
        partner_b=pb.Position(generation=1, index=2),
        offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
    )
    p.individuals[0].annotations.add(text="N/M", type=pb.ANNOTATION_TYPE_OTHER)
    p.individuals[0].genotypes.add(locus_id="l1", alleles=["N", "M"])
    p.individuals[2].count = 3
    p.individuals[3].conditions.add(condition_id="k5", status=pb.CONDITION_STATUS_AFFECTED)
    return p, [*defs, pb.ConditionDef(id="k5", name="E")]


def _reverse_phenotype_labels(p: pb.Pedigree, defs: list[pb.ConditionDef]) -> None:
    labels = [label for label in p.labels if label.kind == pb.LABEL_KIND_PHENOTYPE]
    for label, text in zip(labels, reversed([label.text for label in labels]), strict=True):
        label.text = text


def _rename_declaration(p: pb.Pedigree, defs: list[pb.ConditionDef]) -> None:
    old, defs[0].id = defs[0].id, "renamed"
    for ind in p.individuals:
        for c in ind.conditions:
            if c.condition_id == old:
                c.condition_id = "renamed"


# One edit per LAYOUT_IGNORES field, made to _sample(): the digest and the stored layout must not move.
_IGNORED_EDITS: dict[tuple[str, str], Callable[[pb.Pedigree, list[pb.ConditionDef]], object]] = {
    ("Pedigree", "id"): lambda p, defs: setattr(p, "id", "another-id"),
    ("Pedigree", "provenance"): lambda p, defs: setattr(p.provenance, "doi", "10.1/x"),
    ("Pedigree", "evidence"): lambda p, defs: p.evidence.append("c1"),
    ("Pedigree", "supports"): lambda p, defs: p.supports.add(citations=["c1"], field="labels"),
    ("Pedigree", "labels"): _reverse_phenotype_labels,  # reorders the legend
    ("Individual", "gender"): lambda p, defs: setattr(p.individuals[2], "gender", pb.GENDER_MAN),
    ("Individual", "proband"): lambda p, defs: setattr(p.individuals[1], "proband", True),  # B becomes primary
    ("Individual", "consultand"): lambda p, defs: setattr(p.individuals[0], "consultand", True),
    ("Individual", "external_id"): lambda p, defs: setattr(p.individuals[0], "external_id", "S-17"),
    ("Individual", "sex_assigned_at_birth"): lambda p, defs: setattr(
        p.individuals[0], "sex_assigned_at_birth", pb.SEX_ASSIGNED_AT_BIRTH_FEMALE
    ),
    ("Individual", "reproductive_outcome"): lambda p, defs: setattr(
        p.individuals[-1],
        "reproductive_outcome",
        pb.REPRODUCTIVE_OUTCOME_STILLBIRTH,  # not a mating partner
    ),
    ("Individual", "reproductive_role"): lambda p, defs: setattr(
        p.individuals[0], "reproductive_role", pb.REPRODUCTIVE_ROLE_GAMETE_DONOR
    ),
    ("Individual", "documented_evaluation"): lambda p, defs: setattr(p.individuals[0], "documented_evaluation", True),
    ("ConditionDef", "id"): _rename_declaration,
    ("Condition", "onset_age"): lambda p, defs: setattr(p.individuals[0].conditions[0], "onset_age", "40s"),
    ("Annotation", "type"): lambda p, defs: setattr(
        p.individuals[0].annotations[0], "type", pb.ANNOTATION_TYPE_GENOTYPE
    ),
    ("Annotation", "loci"): lambda p, defs: (
        p.individuals[0].annotations[0].MergeFrom(pb.Annotation(type=pb.ANNOTATION_TYPE_GENOTYPE, loci=["l1"]))
    ),
    ("Individual", "genotypes"): lambda p, defs: p.individuals[0].genotypes[0].alleles.__setitem__(0, "M"),
    ("Mating", "status"): lambda p, defs: setattr(p.matings[-1], "status", pb.RELATIONSHIP_STATUS_DIVORCED),
    ("Mating", "annotations"): lambda p, defs: p.matings[-1].annotations.add(
        text="vasectomy", type=pb.ANNOTATION_TYPE_OTHER
    ),
    ("Offspring", "parentage"): lambda p, defs: setattr(p.matings[-1].offspring[0], "parentage", pb.PARENTAGE_ADOPTIVE),
    ("Offspring", "adoption"): lambda p, defs: setattr(p.matings[-1].offspring[0], "adoption", pb.ADOPTION_IN),
}


def test_every_ignored_field_has_an_edit() -> None:
    assert set(_IGNORED_EDITS) == {(m, f) for m, fs in _store.LAYOUT_IGNORES.items() for f in fs}


@pytest.mark.parametrize("key", sorted(_IGNORED_EDITS))
def test_an_edit_to_an_ignored_field_leaves_the_stored_layout(key: tuple[str, str]) -> None:
    # The stored record is byte-identical, and the edited pedigree drawn from the unedited one's record is its fresh
    # render, alone and in a set: a field layout comes to read fails here until it moves to LAYOUT_READS.
    base, base_defs = _sample()
    edited, defs = _sample()
    _IGNORED_EDITS[key](edited, defs)
    stored = render.store_layout(base, conditions=base_defs, loci=_LOCI)
    assert render.store_layout(edited, conditions=defs, loci=_LOCI) == stored
    fresh = render.render_svg(edited, conditions=defs, loci=_LOCI)
    assert render.render_svg(edited, stored_layout=stored, conditions=defs, loci=_LOCI) == fresh
    edited_set = pb.PedigreeSet(pedigrees=[edited], conditions=defs, loci=_LOCI)
    assert render.render_set_svg(edited_set, stored_layouts=[stored]) == render.render_set_svg(edited_set)


def _declared_name(p: pb.Pedigree, defs: list[pb.ConditionDef]) -> None:
    defs[0].name = "Z"


def _declared_inheritance(p: pb.Pedigree, defs: list[pb.ConditionDef]) -> None:
    defs[0].inheritance = pb.INHERITANCE_X_LINKED_RECESSIVE


# One edit per LAYOUT_READS scalar field, made to _sample(): the digest must move. A declaration's name and
# inheritance reach the digest only as an entry's (pre-1.0 Condition), so each is keyed both ways.
_READ_EDITS: dict[tuple[str, str], Callable[[pb.Pedigree, list[pb.ConditionDef]], object]] = {
    ("Individual", "generation"): lambda p, defs: setattr(p.individuals[-1], "generation", 9),
    ("Individual", "index"): lambda p, defs: setattr(p.individuals[-1], "index", 99),
    ("Individual", "deceased"): lambda p, defs: setattr(p.individuals[0], "deceased", True),
    ("Individual", "count"): lambda p, defs: setattr(p.individuals[0], "count", 3),
    ("Individual", "count_unspecified"): lambda p, defs: setattr(p.individuals[0], "count_unspecified", True),
    ("Condition", "name"): _declared_name,
    ("Condition", "status"): lambda p, defs: setattr(
        p.individuals[0].conditions[0], "status", pb.CONDITION_STATUS_UNKNOWN
    ),
    ("Condition", "inheritance"): _declared_inheritance,
    ("ConditionDef", "name"): _declared_name,
    ("ConditionDef", "inheritance"): _declared_inheritance,
    ("Annotation", "text"): lambda p, defs: setattr(p.individuals[0].annotations[0], "text", "a much wider label line"),
    ("Mating", "consanguineous"): lambda p, defs: setattr(p.matings[-1], "consanguineous", True),
    ("Mating", "childlessness"): lambda p, defs: setattr(p.matings[-1], "childlessness", pb.CHILDLESSNESS_BY_CHOICE),
    ("Mating", "partner_a"): lambda p, defs: p.matings[-1].ClearField("partner_a"),
    ("Mating", "partner_b"): lambda p, defs: p.matings[-1].ClearField("partner_b"),
    ("Offspring", "child"): lambda p, defs: (
        p.matings[-1].offspring[0].child.CopyFrom(pb.Position(generation=2, index=2))
    ),
    ("Offspring", "twin_group"): lambda p, defs: setattr(p.matings[-1].offspring[0], "twin_group", 1),
    ("Offspring", "twin_type"): lambda p, defs: setattr(
        p.matings[-1].offspring[0], "twin_type", pb.ZYGOSITY_TYPE_MONOZYGOTIC
    ),
    ("Position", "generation"): lambda p, defs: setattr(p.matings[-1].partner_a, "generation", 3),
    ("Position", "index"): lambda p, defs: setattr(p.matings[-1].partner_a, "index", 7),
}


def test_every_read_scalar_has_an_edit() -> None:
    scalars = {
        (m, f.name)
        for m, desc in _classified().items()
        for f in desc.fields
        if f.name in _store.LAYOUT_READS[m] and f.message_type is None
    }
    assert scalars <= set(_READ_EDITS), f"no edit for {sorted(scalars - set(_READ_EDITS))}"


@pytest.mark.parametrize("key", sorted(_READ_EDITS))
def test_an_edit_to_a_read_scalar_moves_the_digest(key: tuple[str, str]) -> None:
    edited, defs = _sample()
    _READ_EDITS[key](edited, defs)
    assert _store.pedigree_digest(edited, defs) != _store.pedigree_digest(*_sample())
