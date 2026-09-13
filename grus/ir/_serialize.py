"""Read/write the IR in its two human-readable text surfaces (docs/design/ir.md, "Serialization").

pbtxt is the canonical golden/human surface (comment-able, diffable); proto3-JSON is the LLM emission
surface (structured output -> parse -> validate). Both ``load_*`` parse and then ``validate`` (graph
invariants included), so a caller never holds a structurally invalid ``Pedigree``. ``dump_*`` serialize
as-is (no validation) so an in-progress or deliberately-broken IR can still be written for inspection.
"""

from __future__ import annotations

from google.protobuf import json_format, text_format

from grus.ir._validate import validate, validate_set
from grus.models import pedigree_pb2 as pb


def load_pbtxt(text: str) -> pb.Pedigree:
    """Parse canonical pbtxt into a validated ``Pedigree`` (raises on parse or invariant failure)."""
    p = text_format.Parse(text, pb.Pedigree())
    validate(p)
    return p


def load_json(text: str) -> pb.Pedigree:
    """Parse proto3-JSON (the LLM emission surface) into a validated ``Pedigree``."""
    p = json_format.Parse(text, pb.Pedigree())
    validate(p)
    return p


def dump_pbtxt(p: pb.Pedigree) -> str:
    """Serialize to canonical pbtxt. Field order is proto field number, so goldens are stable."""
    return text_format.MessageToString(p)


def dump_json(p: pb.Pedigree) -> str:
    """Serialize to proto3-JSON."""
    return json_format.MessageToJson(p)


def load_set_pbtxt(text: str) -> pb.PedigreeSet:
    """Parse pbtxt into a validated ``PedigreeSet`` — the figure-level unit (0..N pedigrees, each valid)."""
    ps = text_format.Parse(text, pb.PedigreeSet())
    validate_set(ps)
    return ps


def load_set_json(text: str) -> pb.PedigreeSet:
    """Parse proto3-JSON (the LLM emission surface) into a validated ``PedigreeSet``."""
    ps = json_format.Parse(text, pb.PedigreeSet())
    validate_set(ps)
    return ps


def dump_set_pbtxt(ps: pb.PedigreeSet) -> str:
    """Serialize a ``PedigreeSet`` to canonical pbtxt."""
    return text_format.MessageToString(ps)


def dump_set_json(ps: pb.PedigreeSet) -> str:
    """Serialize a ``PedigreeSet`` to proto3-JSON."""
    return json_format.MessageToJson(ps)
