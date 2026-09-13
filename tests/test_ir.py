"""Tests for grus.ir: load/dump round-trips, graph-invariant validation, and the structural diff.

Field-local protovalidate rules are covered by tests/test_pedigree_proto.py; here we exercise the
hand-written layer — the pbtxt/JSON surfaces, the cross-message invariants the loader enforces, and
the layout-invariant semantic diff (docs/design/eval.md).
"""

from __future__ import annotations

import pytest

from grus import ir
from grus.models import pedigree_pb2 as pb


def _copy(p: pb.Pedigree) -> pb.Pedigree:
    q = pb.Pedigree()
    q.CopyFrom(p)
    return q


# --- battery of valid, hand-written IRs (one per structural shape) --------------------------------


def _ad() -> pb.Pedigree:
    """Autosomal-dominant single condition: two founders, two affected children, a proband."""
    return pb.Pedigree(
        id="ad",
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(
                generation=1,
                index=2,
                gender=pb.GENDER_WOMAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_CARRIER)],
                annotations=[pb.Annotation(text="N/M", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
            pb.Individual(
                generation=2,
                index=1,
                gender=pb.GENDER_MAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
                proband=True,
            ),
            pb.Individual(
                generation=2,
                index=2,
                gender=pb.GENDER_WOMAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
            ),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1)),
                    pb.Offspring(child=pb.Position(generation=2, index=2)),
                ],
            )
        ],
    )


def _consang() -> pb.Pedigree:
    """A consanguineous mating (the flag is explicit, not inferred)."""
    return pb.Pedigree(
        id="consang",
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
                consanguineous=True,
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            )
        ],
    )


def _multi_mate() -> pb.Pedigree:
    """One individual (I-1) partnered in two matings."""
    return pb.Pedigree(
        id="multi",
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_WOMAN),
            pb.Individual(
                generation=2,
                index=1,
                gender=pb.GENDER_MAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
            ),
            pb.Individual(generation=2, index=2, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=3),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=2))],
            ),
        ],
    )


def _single_parent() -> pb.Pedigree:
    """A single-parent sibship: one drawn parent, ``partner_b`` omitted (no phantom stand-in)."""
    return pb.Pedigree(
        id="single",
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_WOMAN),
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
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            )
        ],
    )


def _twins() -> pb.Pedigree:
    """Monozygotic twins (a twin group within one mating's sibship)."""
    return pb.Pedigree(
        id="twins",
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=2, index=2, gender=pb.GENDER_MAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(
                        child=pb.Position(generation=2, index=1), twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC
                    ),
                    pb.Offspring(
                        child=pb.Position(generation=2, index=2), twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC
                    ),
                ],
            )
        ],
    )


def _founder_sibship() -> pb.Pedigree:
    """A founder sibship: three siblings grouped by a partnerless mating (their parent couple is undrawn)."""
    return pb.Pedigree(
        id="founder-sibship",
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
        ],
        matings=[
            pb.Mating(
                offspring=[
                    pb.Offspring(child=pb.Position(generation=1, index=1)),
                    pb.Offspring(child=pb.Position(generation=1, index=2)),
                    pb.Offspring(child=pb.Position(generation=1, index=3)),
                ],
            )
        ],
    )


_BATTERY = [_ad, _consang, _multi_mate, _single_parent, _twins, _founder_sibship]


# --- round-trips ----------------------------------------------------------------------------------


@pytest.mark.parametrize("build", _BATTERY, ids=lambda b: b.__name__)
def test_pbtxt_round_trip(build) -> None:
    p = build()
    assert ir.load_pbtxt(ir.dump_pbtxt(p)) == p


@pytest.mark.parametrize("build", _BATTERY, ids=lambda b: b.__name__)
def test_json_round_trip(build) -> None:
    p = build()
    assert ir.load_json(ir.dump_json(p)) == p


# --- validate: graph invariants (protovalidate passes; the loader must reject) --------------------


def test_validate_accepts_battery() -> None:
    for build in _BATTERY:
        ir.validate(build())  # does not raise


def test_validate_rejects_dangling_reference() -> None:
    p = pb.Pedigree(
        individuals=[pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN)],
        matings=[pb.Mating(partner_a=pb.Position(generation=1, index=1), partner_b=pb.Position(generation=1, index=9))],
    )
    with pytest.raises(ir.IntegrityError):
        ir.validate(p)


def test_validate_rejects_dangling_offspring() -> None:
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            )
        ],
    )
    with pytest.raises(ir.IntegrityError):
        ir.validate(p)


def test_validate_rejects_duplicate_position() -> None:
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=1, gender=pb.GENDER_WOMAN),
        ]
    )
    with pytest.raises(ir.IntegrityError):
        ir.validate(p)


def test_validate_rejects_nondistinct_partners() -> None:
    p = pb.Pedigree(
        individuals=[pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN)],
        matings=[pb.Mating(partner_a=pb.Position(generation=1, index=1), partner_b=pb.Position(generation=1, index=1))],
    )
    with pytest.raises(ir.IntegrityError):
        ir.validate(p)


def test_validate_rejects_singleton_founder_sibship() -> None:
    # A partnerless mating with a single offspring is just an isolated founder, not a sibship.
    p = pb.Pedigree(
        individuals=[pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN)],
        matings=[pb.Mating(offspring=[pb.Offspring(child=pb.Position(generation=1, index=1))])],
    )
    with pytest.raises(ir.IntegrityError, match=r"founder.*sibship"):
        ir.validate(p)


def test_validate_rejects_stray_partner_b() -> None:
    # partner_b without partner_a is malformed: a lone drawn parent is always partner_a.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),
        ],
        matings=[
            pb.Mating(
                partner_b=pb.Position(generation=1, index=1),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            )
        ],
    )
    with pytest.raises(ir.IntegrityError, match="partner_b without partner_a"):
        ir.validate(p)


def test_validate_rejects_self_ancestry_cycle() -> None:
    # A is listed as an offspring of its own mating -> A is its own ancestor.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=1, index=1))],
            )
        ],
    )
    with pytest.raises(ir.IntegrityError):
        ir.validate(p)


def test_validate_rejects_longer_cycle() -> None:
    # A parents B, B parents A: a two-generation ancestry cycle.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),  # A
            pb.Individual(generation=2, index=1, gender=pb.GENDER_WOMAN),  # B
            pb.Individual(generation=1, index=2, gender=pb.GENDER_UNKNOWN),  # X
            pb.Individual(generation=2, index=2, gender=pb.GENDER_UNKNOWN),  # Y
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),  # A x X -> B
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=2, index=1),  # B x Y -> A
                partner_b=pb.Position(generation=2, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=1, index=1))],
            ),
        ],
    )
    with pytest.raises(ir.IntegrityError):
        ir.validate(p)


def test_validate_accepts_several_probands() -> None:
    # A family ascertained through two members has two probands (Bennett); figures draw both arrows.
    p = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN, proband=True),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN, proband=True),
        ]
    )
    ir.validate(p)


def test_validate_rejects_reproductive_loss_as_partner() -> None:
    # A pregnancy/loss node never reproduces, so it may not be a mating partner. A miscarriage drawn as a
    # leaf under its parents is fine; the same individual appearing as partner_a of a mating is rejected.
    leaf_ok = pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(
                generation=2,
                index=1,
                gender=pb.GENDER_UNKNOWN,
                reproductive_outcome=pb.REPRODUCTIVE_OUTCOME_MISCARRIAGE,
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
    ir.validate(leaf_ok)  # a loss as a terminal child is valid
    bad = _copy(leaf_ok)
    bad.individuals.add(generation=3, index=1, gender=pb.GENDER_UNKNOWN)  # a real child of the loss node
    bad.matings.add(
        partner_a=pb.Position(generation=2, index=1),  # the miscarriage now reproduces — illegal
        offspring=[pb.Offspring(child=pb.Position(generation=3, index=1))],
    )
    with pytest.raises(ir.IntegrityError, match="pregnancy/loss"):
        ir.validate(bad)


def test_validate_surfaces_field_local_violation() -> None:
    # gender left as the GENDER_UNSPECIFIED sentinel is a protovalidate (field-local) failure.
    with pytest.raises(ir.ValidationError):
        ir.validate(pb.Pedigree(individuals=[pb.Individual(generation=1, index=1)]))


def test_load_pbtxt_validates() -> None:
    bad = pb.Pedigree(
        individuals=[pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN)],
        matings=[pb.Mating(partner_a=pb.Position(generation=1, index=1), partner_b=pb.Position(generation=1, index=9))],
    )
    with pytest.raises(ir.IntegrityError):
        ir.load_pbtxt(ir.dump_pbtxt(bad))  # dump does not validate; load must


def test_load_json_validates() -> None:
    bad = pb.Pedigree(
        individuals=[pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN)],
        matings=[pb.Mating(partner_a=pb.Position(generation=1, index=1), partner_b=pb.Position(generation=1, index=9))],
    )
    with pytest.raises(ir.IntegrityError):
        ir.load_json(ir.dump_json(bad))


# --- diff: layout-invariant structural semantic diff ----------------------------------------------


@pytest.mark.parametrize("build", _BATTERY, ids=lambda b: b.__name__)
def test_diff_identical_is_empty(build) -> None:
    p = build()
    d = ir.diff(p, _copy(p))
    assert d.mismatches == []
    assert d.only_in_a == []
    assert d.only_in_b == []
    assert d.matched == sorted(f"{ind.generation}-{ind.index}" for ind in p.individuals)
    assert d.precision() == 1.0
    assert d.recall() == 1.0


def test_diff_flipped_gender_is_localized() -> None:
    a = _ad()
    b = _copy(a)
    (ii1,) = (ind for ind in b.individuals if (ind.generation, ind.index) == (2, 1))
    ii1.gender = pb.GENDER_WOMAN  # was GENDER_MAN
    d = ir.diff(a, b)
    assert d.only_in_a == [] and d.only_in_b == []
    assert d.precision() == 1.0 and d.recall() == 1.0  # still matched by position
    assert len(d.mismatches) == 1
    (mm,) = d.mismatches
    assert (mm.subject, mm.field, mm.a_value, mm.b_value) == ("2-1", "gender", "GENDER_MAN", "GENDER_WOMAN")


def test_diff_annotation_text_mismatch_is_localized() -> None:
    # The same annotation kind (genotype) with different verbatim text on the matched individual is one
    # localized mismatch under field annotation:genotype, not a lost/gained individual.
    a = _ad()
    b = _copy(a)
    (a_ii1,) = (ind for ind in a.individuals if (ind.generation, ind.index) == (2, 1))
    (b_ii1,) = (ind for ind in b.individuals if (ind.generation, ind.index) == (2, 1))
    a_ii1.annotations.append(pb.Annotation(text="M/M", type=pb.ANNOTATION_TYPE_GENOTYPE))
    b_ii1.annotations.append(pb.Annotation(text="N/M", type=pb.ANNOTATION_TYPE_GENOTYPE))
    d = ir.diff(a, b)
    assert d.precision() == 1.0 and d.recall() == 1.0  # still matched by position
    ann = [m for m in d.mismatches if m.field == "annotation:genotype"]
    assert len(ann) == 1
    assert (ann[0].subject, ann[0].a_value, ann[0].b_value) == ("2-1", "M/M", "N/M")


def test_diff_annotation_present_on_one_side_is_localized() -> None:
    # An annotation kind present only on the reference is reported (b_value "absent"); it is not confused
    # with the identical genotype both sides already carry on I-2.
    a = _ad()
    b = _copy(a)
    (a_i1,) = (ind for ind in a.individuals if (ind.generation, ind.index) == (1, 1))
    a_i1.annotations.append(pb.Annotation(text="175", type=pb.ANNOTATION_TYPE_MEASUREMENT))
    d = ir.diff(a, b)
    meas = [m for m in d.mismatches if m.field == "annotation:measurement"]
    assert len(meas) == 1
    assert (meas[0].subject, meas[0].a_value, meas[0].b_value) == ("1-1", "175", "absent")
    assert not [m for m in d.mismatches if m.field == "annotation:genotype"], "identical genotype is not a mismatch"


def test_diff_dropped_consanguinity_is_localized() -> None:
    a = _consang()
    b = _copy(a)
    b.matings[0].consanguineous = False
    d = ir.diff(a, b)
    assert d.precision() == 1.0 and d.recall() == 1.0
    cons = [m for m in d.mismatches if m.field == "consanguineous"]
    assert len(cons) == 1
    assert cons[0].subject.startswith("mating(")
    assert (cons[0].a_value, cons[0].b_value) == ("True", "False")


def test_diff_missing_founder_sibship_grouping_is_localized() -> None:
    # A has a founder sibship (partnerless mating); B extracted the same three individuals but left them
    # ungrouped (no mating). Every individual still matches, but the sibship relationship is reported absent.
    a = _founder_sibship()
    b = _copy(a)
    del b.matings[:]
    d = ir.diff(a, b)
    assert d.precision() == 1.0 and d.recall() == 1.0  # all individuals recovered
    sib = [m for m in d.mismatches if m.field == "sibship"]
    assert len(sib) == 1
    assert sib[0].subject.startswith("sibship(")
    assert (sib[0].a_value, sib[0].b_value) == ("present", "absent")


def test_diff_matched_founder_sibship_is_empty() -> None:
    # Two founder sibships grouping the same offspring set diff clean even at disjoint positions.
    a = _founder_sibship()
    b = _copy(a)
    d = ir.diff(a, b)
    assert d.mismatches == []


def test_diff_missing_individual_shows_in_only_in_a() -> None:
    a = _ad()
    b = _copy(a)
    (idx,) = (i for i, ind in enumerate(b.individuals) if (ind.generation, ind.index) == (2, 2))
    del b.individuals[idx]
    (oidx,) = (i for i, off in enumerate(b.matings[0].offspring) if (off.child.generation, off.child.index) == (2, 2))
    del b.matings[0].offspring[oidx]
    d = ir.diff(a, b)
    assert d.only_in_a == ["2-2"]
    assert d.only_in_b == []
    assert d.precision() == 1.0
    assert d.recall() == pytest.approx(3 / 4)


def _chain(off: int) -> pb.Pedigree:
    """A labelless nuclear family with indices shifted by ``off`` per call.

    Positions are disjoint across calls, so matching must be structural (generation/index never enter the
    fingerprint), not by exact position.
    """
    return pb.Pedigree(
        individuals=[
            pb.Individual(generation=1, index=1 + off, gender=pb.GENDER_MAN),
            pb.Individual(
                generation=1,
                index=2 + off,
                gender=pb.GENDER_WOMAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
            ),
            pb.Individual(
                generation=2,
                index=1 + off,
                gender=pb.GENDER_MAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
                proband=True,
            ),
            pb.Individual(generation=2, index=2 + off, gender=pb.GENDER_WOMAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1 + off),
                partner_b=pb.Position(generation=1, index=2 + off),
                offspring=[
                    pb.Offspring(child=pb.Position(generation=2, index=1 + off)),
                    pb.Offspring(child=pb.Position(generation=2, index=2 + off)),
                ],
            )
        ],
    )


def test_match_individuals_is_structural_across_disjoint_positions() -> None:
    a, b = _chain(0), _chain(10)
    mapping = ir.match_individuals(a, b)
    assert len(mapping) == 4  # full bijection with no shared positions and no labels
    assert mapping[(2, 1)] == (2, 11)  # II-1 -> its structural twin
    assert mapping[(1, 1)] == (1, 11)  # I-1 -> its structural twin


def test_diff_structural_match_is_empty_across_disjoint_positions() -> None:
    d = ir.diff(_chain(0), _chain(10))
    assert d.mismatches == []
    assert d.only_in_a == []
    assert d.only_in_b == []
    assert d.precision() == 1.0
    assert d.recall() == 1.0
