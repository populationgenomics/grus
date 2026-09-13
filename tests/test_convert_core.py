"""The shared import core: parent pairs -> matings, kindepth+alignment -> generation, source order -> index."""

from __future__ import annotations

import pytest

from grus.convert._core import Extras, PedigreeImportError, Person, build_pedigree, build_set
from grus.models import pedigree_pb2 as pb

M, W = pb.GENDER_MAN, pb.GENDER_WOMAN


def _pos(ped: pb.Pedigree) -> dict[str, tuple[int, int]]:
    return {i.external_id: (i.generation, i.index) for i in ped.individuals}


def test_trio_positions_and_single_mating() -> None:
    ped = build_pedigree([Person("dad", M), Person("mum", W), Person("kid", W, father="dad", mother="mum")], Extras())
    assert _pos(ped) == {"dad": (1, 1), "mum": (1, 2), "kid": (2, 1)}
    (m,) = ped.matings
    assert (m.partner_a.generation, m.partner_a.index) == (1, 1)
    assert (m.partner_b.generation, m.partner_b.index) == (1, 2)
    assert [(o.child.generation, o.child.index) for o in m.offspring] == [(2, 1)]


def test_marry_in_is_aligned_to_spouse_row() -> None:
    # gp x gm -> p ; p x spouse (a founder) -> c. kindepth alone puts spouse on row 1; alignment pulls it to row 2.
    people = [
        Person("gp", M),
        Person("gm", W),
        Person("p", M, father="gp", mother="gm"),
        Person("spouse", W),
        Person("c", M, father="p", mother="spouse"),
    ]
    pos = _pos(build_pedigree(people, Extras()))
    assert pos["spouse"][0] == 2 and pos["p"][0] == 2 and pos["c"][0] == 3


def test_single_parent_mating_has_one_partner_and_no_phantom() -> None:
    ped = build_pedigree([Person("mum", W), Person("kid", M, mother="mum")], Extras())
    (m,) = ped.matings
    assert m.HasField("partner_a") and not m.HasField("partner_b")  # a lone parent is partner_a
    assert (m.partner_a.generation, m.partner_a.index) == (1, 1)
    assert len(ped.individuals) == 2


def test_two_sibships_two_matings_offspring_in_source_order() -> None:
    people = [
        Person("a", M),
        Person("b", W),
        Person("c", W),
        Person("k1", M, father="a", mother="b"),
        Person("k2", W, father="a", mother="c"),
        Person("k3", W, father="a", mother="b"),
    ]
    ped = build_pedigree(people, Extras())
    kids = {
        tuple(sorted(x.index for x in (m.partner_a, m.partner_b))): [o.child.index for o in m.offspring]
        for m in ped.matings
    }
    assert kids == {(1, 2): [1, 3], (1, 3): [2]}


def test_extras_consanguineous_and_childless_couple() -> None:
    people = [Person("a", M), Person("b", W), Person("k", M, father="a", mother="b"), Person("x", M), Person("y", W)]
    extras = Extras(
        consanguineous={frozenset({"a", "b"})},
        childless={frozenset({"x", "y"}): pb.CHILDLESSNESS_INFERTILITY},
    )
    ped = build_pedigree(people, extras)
    by_kids = {len(m.offspring): m for m in ped.matings}
    assert by_kids[1].consanguineous is True
    assert by_kids[0].childlessness == pb.CHILDLESSNESS_INFERTILITY
    assert by_kids[0].partner_a.index == _pos(ped)["x"][1]  # man in the father slot


def test_twins_and_adoption_ride_the_offspring_edge() -> None:
    people = [
        Person("a", M),
        Person("b", W),
        Person("t1", M, father="a", mother="b", twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC),
        Person("t2", M, father="a", mother="b", twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC),
        Person("ad", W, father="a", mother="b", adoption=pb.ADOPTION_IN, parentage=pb.PARENTAGE_ADOPTIVE),
    ]
    (m,) = build_pedigree(people, Extras()).matings
    assert [o.twin_group for o in m.offspring[:2]] == [1, 1]
    assert m.offspring[2].adoption == pb.ADOPTION_IN and m.offspring[2].parentage == pb.PARENTAGE_ADOPTIVE


def test_build_set_groups_by_family_and_labels() -> None:
    people = [Person("a", M, family="F1"), Person("b", W, family="F2")]
    ps = build_set(people)
    assert [p.id for p in ps.pedigrees] == ["F1", "F2"]
    assert ps.pedigrees[0].labels[0].kind == pb.LABEL_KIND_FAMILY


def test_unknown_parent_and_duplicate_id_fail_loud() -> None:
    with pytest.raises(PedigreeImportError, match="not in family"):
        build_pedigree([Person("k", M, father="ghost")], Extras())
    with pytest.raises(PedigreeImportError, match="duplicate"):
        build_pedigree([Person("k", M), Person("k", W)], Extras())


def test_cycle_fails_loud() -> None:
    with pytest.raises(PedigreeImportError, match="cycle"):
        build_pedigree([Person("a", M, father="b"), Person("b", M, father="a")], Extras())
