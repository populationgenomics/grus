"""Stored layouts (``grus.render._store``, docs/design/layout-store.md).

* **Lossless** — ``Layout`` -> ``PedigreeLayout`` -> ``Layout`` is the identity for every golden and for the synthetic
  cells no golden has (a ghost, a routed mating).
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
    """Every drawable golden, plus the shapes with synthetic cells or routes no golden has."""
    out = {name: test_render._load(name) for name in test_layout2._DRAWABLE}
    out["ghost"] = test_svg_output._avuncular()
    out["two_ghosts"] = test_svg_output._avuncular(second_niece=True)
    out["routed"] = test_layout2._overflow_pedigree()
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
    assert any(lay.routed for lay in layouts)
    assert any(lay.founder_sibships for lay in layouts)


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_round_trip_is_lossless(name: str) -> None:
    p = _FIXTURES[name]
    lay = render.layout(p, _GEOM)
    record = _store.to_proto(p, lay, _GEOM)
    protovalidate.validate(record)
    back = lpb.PedigreeLayout.FromString(record.SerializeToString())
    assert _store.from_proto(p, back.placement) == lay


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
    assert _store.from_proto(q, record.placement) == render.layout(q, _GEOM)


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
        _store.from_proto(p, record.placement)


def test_a_missing_cell_is_refused() -> None:
    p = _FIXTURES["sibship"]
    record = _record(p)
    del record.placement.rows[-1].cells[-1]
    with pytest.raises(_store.StaleLayoutError, match="missing"):
        _store.from_proto(p, record.placement)


def test_a_column_outside_its_row_is_refused() -> None:
    p = _FIXTURES["sibship"]
    record = _record(p)
    record.placement.rows[1].cells[0].parent_column = 7
    with pytest.raises(_store.StaleLayoutError, match="parent column 7"):
        _store.from_proto(p, record.placement)


# --- drawing from a stored layout ---------------------------------------------------------------------------------

# sha256 over every golden's stored layout (key cleared), keyed by the layout-algorithm version that produced it.
_PINNED_GOLDEN_LAYOUTS = {1: "33719c940f8a49273c7d8f00b0520f68c3d41c156e396872e3827d5d27bb7560"}


def test_layout_version_is_bumped_when_a_golden_layout_changes() -> None:
    digest = hashlib.sha256()
    for name in test_render._NAMES:
        record = render.store_layout(test_render._load(name))
        record.ClearField("key")
        digest.update(record.SerializeToString(deterministic=True))
    assert _PINNED_GOLDEN_LAYOUTS.get(render.LAYOUT_VERSION) == digest.hexdigest(), (
        "a golden's layout changed: bump LAYOUT_VERSION (stored layouts from the old algorithm must read as stale) "
        "and pin the new digest under it; a golden IR edit needs only the re-pin"
    )


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_drawing_from_a_stored_layout_is_byte_identical(name: str) -> None:
    p = _FIXTURES[name]
    stored = lpb.PedigreeLayout.FromString(render.store_layout(p).SerializeToString())
    assert render.render_svg(p, stored_layout=stored, id_prefix="f-") == render.render_svg(p, id_prefix="f-")


def test_drawing_only_geometry_may_vary() -> None:
    p = _FIXTURES["carrier_inheritance"]
    stored = render.store_layout(p)
    geom = dataclasses.replace(
        _GEOM, gen_height=120.0, symbol_size=30.0, margin=10.0, carrier_style=render.CarrierStyle.PARTITION_FILL
    )
    assert render.render_svg(p, geom, stored_layout=stored) == render.render_svg(p, geom)


def test_a_stored_layout_draws_a_shuffled_pedigree() -> None:
    p = _FIXTURES["two_ghosts"]
    q = test_layout2._shuffled(p, 2)
    assert render.render_svg(q, stored_layout=render.store_layout(p)) == render.render_svg(q)


@pytest.mark.parametrize("field", ["couple_gap", "sib_gap", "label_size", "label_box_width", "x_unit", "x_solver"])
def test_a_layout_under_other_layout_geometry_is_stale(field: str) -> None:
    p = _FIXTURES["three_generation"]
    stored = render.store_layout(p)
    changed = render.XSolver.HIGHS if field == "x_solver" else getattr(_GEOM, field) + 1.0
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
