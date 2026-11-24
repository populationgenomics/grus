"""Foundational contract test for the pedigree IR (schema/proto/grus/models/pedigree.proto).

Guards the two things the IR must always do: round-trip losslessly through the human-facing text
surfaces (pbtxt + proto3-JSON), and reject field-local / single-message invalid states via
protovalidate. Graph-level integrity (referential, acyclic) is the grus.ir loader's job (slice 02),
tested there.
"""

from __future__ import annotations

import protovalidate
import pytest
from google.protobuf import json_format, message, text_format

from grus.models import pedigree_pb2 as pb


def _sample() -> pb.Pedigree:
    return pb.Pedigree(
        id="demo",
        labels=[pb.Label(text="AD, single condition", kind=pb.LABEL_KIND_OTHER)],
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(
                generation=1,
                index=2,
                gender=pb.GENDER_WOMAN,
                marks=[pb.Mark(key_id="k1", status=pb.CONDITION_STATUS_CARRIER)],
                annotations=[pb.Annotation(text="N/M", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
            pb.Individual(
                generation=2,
                index=1,
                gender=pb.GENDER_MAN,
                marks=[pb.Mark(key_id="k1", status=pb.CONDITION_STATUS_AFFECTED)],
                proband=True,
                annotations=[pb.Annotation(text="M/M", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
            pb.Individual(
                generation=2,
                index=2,
                gender=pb.GENDER_WOMAN,
                marks=[pb.Mark(key_id="k1", status=pb.CONDITION_STATUS_AFFECTED)],
            ),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[
                    pb.Offspring(
                        child=pb.Position(generation=2, index=1), twin_group=1, twin_type=pb.ZYGOSITY_TYPE_DIZYGOTIC
                    ),
                    pb.Offspring(
                        child=pb.Position(generation=2, index=2), twin_group=1, twin_type=pb.ZYGOSITY_TYPE_DIZYGOTIC
                    ),
                ],
            ),
        ],
    )


def test_pbtxt_round_trip() -> None:
    p = _sample()
    assert text_format.Parse(text_format.MessageToString(p), pb.Pedigree()) == p


def test_json_round_trip() -> None:
    p = _sample()
    assert json_format.Parse(json_format.MessageToJson(p), pb.Pedigree()) == p


def test_valid_sample_passes_protovalidate() -> None:
    protovalidate.validate(_sample())  # recurses into Individual + Offspring CEL rules


def test_prior_art_audit_fields_round_trip_and_validate() -> None:
    # Additive fields/enum values from the prior-art coverage audit: external id, documented-evaluation,
    # per-condition onset age, trizygotic zygosity, adopted-by-relative. Round-trip and pass protovalidate.
    ind = pb.Individual(
        generation=1,
        index=1,
        gender=pb.GENDER_WOMAN,
        external_id="SAMPLE-42",
        documented_evaluation=True,
        marks=[
            pb.Mark(key_id="cf", status=pb.CONDITION_STATUS_CARRIER, onset_age="40s"),
        ],
    )
    cf = pb.KeyEntry(
        id="cf", text="cystic fibrosis", phenotype=pb.Phenotype(inheritance=pb.INHERITANCE_AUTOSOMAL_RECESSIVE)
    )
    protovalidate.validate(cf)
    assert text_format.Parse(text_format.MessageToString(cf), pb.KeyEntry()) == cf
    off = pb.Offspring(
        child=pb.Position(generation=2, index=1),
        twin_group=1,
        twin_type=pb.ZYGOSITY_TYPE_TRIZYGOTIC,
        parentage=pb.PARENTAGE_ADOPTIVE,
        adoption=pb.ADOPTION_BY_RELATIVE,
    )
    protovalidate.validate(ind)
    protovalidate.validate(off)
    assert text_format.Parse(text_format.MessageToString(ind), pb.Individual()) == ind
    assert text_format.Parse(text_format.MessageToString(off), pb.Offspring()) == off


@pytest.mark.parametrize(
    "msg",
    [
        pb.Individual(generation=1, index=1),  # gender == GENDER_UNSPECIFIED sentinel
        pb.Individual(gender=pb.GENDER_MAN),  # generation/index unset (< 1)
        pb.Offspring(child=pb.Position(generation=2, index=1), twin_group=1),  # twin_group set without twin_type
        pb.Annotation(text="N/M"),  # type == ANNOTATION_TYPE_UNSPECIFIED sentinel
        pb.Annotation(type=pb.ANNOTATION_TYPE_GENOTYPE),  # empty text
        pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN, count=0),  # a group of no one
        pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN, count=-2),
        pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN, count=3, count_unspecified=True),  # known + unknown
        pb.Mark(status=pb.CONDITION_STATUS_AFFECTED),  # no key_id
        pb.Mark(key_id="k1", status=pb.CONDITION_STATUS_UNSPECIFIED),  # a status set to the sentinel
        pb.Mark(key_id="k1", grade=""),  # an empty grade
        pb.KeyEntry(text="cystic fibrosis", phenotype=pb.Phenotype()),  # no id
        pb.KeyEntry(id="k1", text="cystic fibrosis"),  # no kind
        pb.KeyEntry(id="k1", text="CF", phenotype=pb.Phenotype(grades=[""])),  # an empty grade
        pb.GenotypePattern(locus_id="l1", alleles=["M", "M"], copies_unstated=True),  # carries M, twice
        pb.GenotypePattern(locus_id="l1"),  # no alleles
        pb.Support(citations=["cap"], field="mark.status", key_id=""),  # a present, empty key_id
    ],
)
def test_invalid_messages_rejected(msg: message.Message) -> None:
    with pytest.raises(protovalidate.ValidationError):
        protovalidate.validate(msg)


@pytest.mark.parametrize(
    "ind",
    [
        pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN, count=1),  # one person, the same as absent
        pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN, count=3),
        pb.Individual(generation=1, index=1, gender=pb.GENDER_UNKNOWN, count_unspecified=True),
    ],
)
def test_counts_accepted(ind: pb.Individual) -> None:
    protovalidate.validate(ind)
