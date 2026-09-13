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
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_CARRIER)],
                annotations=[pb.Annotation(text="N/M", type=pb.ANNOTATION_TYPE_GENOTYPE)],
            ),
            pb.Individual(
                generation=2,
                index=1,
                gender=pb.GENDER_MAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
                proband=True,
                annotations=[pb.Annotation(text="M/M", type=pb.ANNOTATION_TYPE_GENOTYPE)],
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
        conditions=[
            pb.Condition(
                name="cystic fibrosis",
                status=pb.CONDITION_STATUS_CARRIER,
                inheritance=pb.INHERITANCE_AUTOSOMAL_RECESSIVE,
                onset_age="40s",
            )
        ],
    )
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
    ],
)
def test_invalid_messages_rejected(msg: message.Message) -> None:
    with pytest.raises(protovalidate.ValidationError):
        protovalidate.validate(msg)
