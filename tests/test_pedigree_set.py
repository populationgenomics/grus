"""Tests for the PedigreeSet layer of grus.ir + the set serialization surfaces (docs/plans/11).

A figure maps to 0..N pedigrees (docs/design/ir.md, "Figure scope"). Each ``Pedigree`` is validated
independently, so distinct families reusing the same ``local_id``s never collide; an empty set is valid;
a bad reference inside one pedigree fails loud and names that pedigree.
"""

from __future__ import annotations

import pytest

from grus.ir import (
    IntegrityError,
    diff,
    diff_set,
    dump_set_json,
    dump_set_pbtxt,
    load_set_json,
    load_set_pbtxt,
    validate_set,
)
from grus.models import pedigree_pb2 as pb


def _trio(title: str) -> pb.Pedigree:
    """A minimal valid family (I-1 x I-2 -> II-1) labelled ``title``; positions repeat across trios by design."""
    labels = [pb.Label(text=title, kind=pb.LABEL_KIND_FAMILY)] if title else []
    return pb.Pedigree(
        labels=labels,
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(
                generation=2,
                index=1,
                gender=pb.GENDER_MAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
            ),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            )
        ],
    )


def _broken() -> pb.Pedigree:
    """A pedigree whose offspring references a position that does not exist (a graph-invariant failure)."""
    return pb.Pedigree(
        labels=[pb.Label(text="broken", kind=pb.LABEL_KIND_FAMILY)],
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=9))],  # "ghost": no such individual
            )
        ],
    )


# --- validate_set -----------------------------------------------------------------------------------


def test_validate_set_accepts_multiple_families_with_colliding_ids() -> None:
    # Two families both using I-1/I-2/II-1: local_ids are scoped per pedigree, so the set is valid.
    validate_set(pb.PedigreeSet(pedigrees=[_trio("Family 1"), _trio("Family 2")]))


def test_validate_set_accepts_empty_set() -> None:
    validate_set(pb.PedigreeSet())  # K=0: a figure with no pedigree is a valid result


def test_validate_set_names_the_failing_pedigree() -> None:
    bad = pb.PedigreeSet(pedigrees=[_trio("ok"), _broken()])
    with pytest.raises(IntegrityError, match=r"pedigrees\[1\]") as excinfo:
        validate_set(bad)
    message = str(excinfo.value)
    assert "broken" in message  # the offending pedigree's label
    assert "(2, 9)" in message  # the chained inner error naming the dangling position


# --- serialization round-trips ----------------------------------------------------------------------


def test_set_pbtxt_round_trip() -> None:
    ps = pb.PedigreeSet(pedigrees=[_trio("F1"), _trio("F2")])
    assert load_set_pbtxt(dump_set_pbtxt(ps)) == ps


def test_set_json_round_trip() -> None:
    ps = pb.PedigreeSet(pedigrees=[_trio("F1"), _trio("F2")])
    assert load_set_json(dump_set_json(ps)) == ps


def test_empty_set_round_trips_both_surfaces() -> None:
    empty = pb.PedigreeSet()
    assert load_set_pbtxt(dump_set_pbtxt(empty)) == empty
    assert load_set_json(dump_set_json(empty)) == empty


def test_load_set_validates_not_just_parses() -> None:
    text = dump_set_json(pb.PedigreeSet(pedigrees=[_broken()]))
    with pytest.raises(IntegrityError, match=r"pedigrees\[0\]"):
        load_set_json(text)


# --- diff_set (set-matching round-trip) -------------------------------------------------------------


def _couple(title: str) -> pb.Pedigree:
    """A childless couple (2 individuals, no offspring) — structurally distinct from a trio."""
    labels = [pb.Label(text=title, kind=pb.LABEL_KIND_FAMILY)] if title else []
    return pb.Pedigree(
        labels=labels,
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
        ],
        matings=[pb.Mating(partner_a=pb.Position(generation=1, index=1), partner_b=pb.Position(generation=1, index=2))],
    )


def _set_of(*pedigrees: pb.Pedigree) -> pb.PedigreeSet:
    return pb.PedigreeSet(pedigrees=list(pedigrees))


def test_diff_set_identical_sets_all_matched() -> None:
    s = _set_of(_trio("F1"), _trio("F2"))
    d = diff_set(s, s)
    assert len(d.matched) == 2
    assert not d.only_in_a and not d.only_in_b
    assert all(not pair.diff.mismatches for pair in d.matched)
    assert d.family_precision() == d.family_recall() == 1.0
    assert d.individual_precision() == d.individual_recall() == 1.0


def test_diff_set_matches_by_title_despite_reordering() -> None:
    # Two structurally identical trios separable only by their labels; the candidate swaps their order.
    ref = _set_of(_trio("Family 1"), _trio("Family 2"))
    cand = _set_of(_trio("Family 2"), _trio("Family 1"))
    d = diff_set(ref, cand)
    assert {(p.a_index, p.b_index) for p in d.matched} == {(0, 1), (1, 0)}  # matched by title, not position
    assert all(not p.diff.mismatches for p in d.matched)


def test_diff_set_matches_untitled_families_structurally() -> None:
    # No titles: a trio and a couple, reordered in the candidate, must match by structure.
    ref = _set_of(_trio(""), _couple(""))
    cand = _set_of(_couple(""), _trio(""))
    d = diff_set(ref, cand)
    assert {(p.a_index, p.b_index) for p in d.matched} == {(0, 1), (1, 0)}
    assert d.family_recall() == 1.0


def test_diff_set_reports_missing_family() -> None:
    d = diff_set(_set_of(_trio("F1"), _trio("F2")), _set_of(_trio("F1")))
    assert [p.a_index for p in d.matched] == [0]
    assert d.only_in_a == [1] and d.only_in_b == []
    assert d.family_recall() == 0.5 and d.family_precision() == 1.0


def test_diff_set_reports_spurious_family() -> None:
    d = diff_set(_set_of(_trio("F1")), _set_of(_trio("F1"), _trio("F2")))
    assert d.only_in_b == [1] and d.only_in_a == []
    assert d.family_precision() == 0.5 and d.family_recall() == 1.0


def test_diff_set_surfaces_per_pedigree_mismatch() -> None:
    flipped = _trio("F1")
    flipped.individuals[2].gender = pb.GENDER_WOMAN  # II-1 man -> woman inside the one matched family
    d = diff_set(_set_of(_trio("F1")), _set_of(flipped))
    assert len(d.matched) == 1
    assert "gender" in {m.field for m in d.matched[0].diff.mismatches}


def test_diff_set_empty_vs_empty() -> None:
    d = diff_set(pb.PedigreeSet(), pb.PedigreeSet())
    assert d.family_precision() == d.family_recall() == 1.0
    assert d.individual_precision() == d.individual_recall() == 1.0


def test_diff_set_reference_vs_empty_candidate() -> None:
    d = diff_set(_set_of(_trio("F1")), pb.PedigreeSet())
    assert d.only_in_a == [0] and not d.matched
    assert d.family_recall() == 0.0 and d.individual_recall() == 0.0
    assert d.family_precision() == 1.0  # no candidate families -> vacuously precise


def test_diff_set_generalizes_single_pedigree_diff() -> None:
    a = _trio("F1")
    b = _trio("F1")
    b.individuals[2].ClearField("conditions")  # II-1 affected -> unaffected: a per-pedigree discrepancy
    d = diff_set(_set_of(a), _set_of(b))
    assert len(d.matched) == 1
    assert d.matched[0].diff == diff(a, b)  # the pair's diff is exactly the single-pedigree diff
