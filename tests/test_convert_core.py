"""The shared import core: parent pairs -> matings, kindepth+alignment -> generation, source order -> index."""

from __future__ import annotations

import pytest

from grus.convert import _core
from grus.models import pedigree_pb2 as pb

M, W = pb.GENDER_MAN, pb.GENDER_WOMAN


def _pos(ped: pb.Pedigree) -> dict[str, tuple[int, int]]:
    return {i.external_id: (i.generation, i.index) for i in ped.individuals}


def test_trio_positions_and_single_mating() -> None:
    ped = _core.build_pedigree(
        [_core.Person("dad", M), _core.Person("mum", W), _core.Person("kid", W, father="dad", mother="mum")],
        _core.Extras(),
    )
    assert _pos(ped) == {"dad": (1, 1), "mum": (1, 2), "kid": (2, 1)}
    (m,) = ped.matings
    assert (m.partner_a.generation, m.partner_a.index) == (1, 1)
    assert (m.partner_b.generation, m.partner_b.index) == (1, 2)
    assert [(o.child.generation, o.child.index) for o in m.offspring] == [(2, 1)]


def test_marry_in_is_aligned_to_spouse_row() -> None:
    # gp x gm -> p ; p x spouse (a founder) -> c. kindepth alone puts spouse on row 1; alignment pulls it to row 2.
    people = [
        _core.Person("gp", M),
        _core.Person("gm", W),
        _core.Person("p", M, father="gp", mother="gm"),
        _core.Person("spouse", W),
        _core.Person("c", M, father="p", mother="spouse"),
    ]
    pos = _pos(_core.build_pedigree(people, _core.Extras()))
    assert pos["spouse"][0] == 2 and pos["p"][0] == 2 and pos["c"][0] == 3


def test_single_parent_mating_has_one_partner_and_no_phantom() -> None:
    ped = _core.build_pedigree([_core.Person("mum", W), _core.Person("kid", M, mother="mum")], _core.Extras())
    (m,) = ped.matings
    assert m.HasField("partner_a") and not m.HasField("partner_b")  # a lone parent is partner_a
    assert (m.partner_a.generation, m.partner_a.index) == (1, 1)
    assert len(ped.individuals) == 2


def test_two_sibships_two_matings_offspring_in_source_order() -> None:
    people = [
        _core.Person("a", M),
        _core.Person("b", W),
        _core.Person("c", W),
        _core.Person("k1", M, father="a", mother="b"),
        _core.Person("k2", W, father="a", mother="c"),
        _core.Person("k3", W, father="a", mother="b"),
    ]
    ped = _core.build_pedigree(people, _core.Extras())
    kids = {
        tuple(sorted(x.index for x in (m.partner_a, m.partner_b))): [o.child.index for o in m.offspring]
        for m in ped.matings
    }
    assert kids == {(1, 2): [1, 3], (1, 3): [2]}


def test_extras_consanguineous_and_childless_couple() -> None:
    people = [
        _core.Person("a", M),
        _core.Person("b", W),
        _core.Person("k", M, father="a", mother="b"),
        _core.Person("x", M),
        _core.Person("y", W),
    ]
    extras = _core.Extras(
        consanguineous={frozenset({"a", "b"})},
        childless={frozenset({"x", "y"}): pb.CHILDLESSNESS_INFERTILITY},
    )
    ped = _core.build_pedigree(people, extras)
    by_kids = {len(m.offspring): m for m in ped.matings}
    assert by_kids[1].consanguineous is True
    assert by_kids[0].childlessness == pb.CHILDLESSNESS_INFERTILITY
    assert by_kids[0].partner_a.index == _pos(ped)["x"][1]  # man in the father slot


def test_twins_and_adoption_ride_the_offspring_edge() -> None:
    people = [
        _core.Person("a", M),
        _core.Person("b", W),
        _core.Person("t1", M, father="a", mother="b", twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC),
        _core.Person("t2", M, father="a", mother="b", twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC),
        _core.Person("ad", W, father="a", mother="b", adoption=pb.ADOPTION_IN, parentage=pb.PARENTAGE_ADOPTIVE),
    ]
    (m,) = _core.build_pedigree(people, _core.Extras()).matings
    assert [o.twin_group for o in m.offspring[:2]] == [1, 1]
    assert m.offspring[2].adoption == pb.ADOPTION_IN and m.offspring[2].parentage == pb.PARENTAGE_ADOPTIVE


def test_build_set_groups_by_family_and_labels() -> None:
    people = [_core.Person("a", M, family="F1"), _core.Person("b", W, family="F2")]
    ps = _core.build_set(people)
    assert [p.id for p in ps.pedigrees] == ["F1", "F2"]
    assert ps.pedigrees[0].labels[0].kind == pb.LABEL_KIND_FAMILY


def test_unknown_parent_and_duplicate_id_fail_loud() -> None:
    with pytest.raises(_core.PedigreeImportError, match="not in family"):
        _core.build_pedigree([_core.Person("k", M, father="ghost")], _core.Extras())
    with pytest.raises(_core.PedigreeImportError, match="duplicate"):
        _core.build_pedigree([_core.Person("k", M), _core.Person("k", W)], _core.Extras())


def test_cycle_fails_loud() -> None:
    with pytest.raises(_core.PedigreeImportError, match="cycle"):
        _core.build_pedigree([_core.Person("a", M, father="b"), _core.Person("b", M, father="a")], _core.Extras())
