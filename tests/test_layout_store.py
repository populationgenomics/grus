"""Stored layouts (``grus.render._store``, docs/design/layout-store.md).

* **Lossless** — ``Layout`` -> ``PedigreeLayout`` -> ``Layout`` is the identity for every golden and for the synthetic
  cells no golden has (a ghost, a routed mating).
* **Order-independent** — shuffling a pedigree's individuals and matings leaves the stored bytes unchanged, because
  cells are keyed by identity and the digest is canonical.
"""

from __future__ import annotations

import protovalidate
import pytest
import test_layout2
import test_render
import test_svg_output

from grus import render
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
