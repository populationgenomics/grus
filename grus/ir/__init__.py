"""grus.ir — the hand-written layer over the generated pedigree protobuf stubs.

Load/dump the meaning-only pedigree IR in both text surfaces (pbtxt goldens + proto3-JSON, the LLM
emission surface), enforce the cross-message graph invariants protovalidate cannot express, and diff
two IRs structurally (layout-invariant, role-based individual matching).

Design: docs/design/ir.md (the IR + validation split), docs/design/eval.md (the structural diff).

Errors: ``ValidationError`` (re-exported from protovalidate) is a field-local / single-message rule
failure; ``IntegrityError`` is a graph-invariant failure. Both are raised by ``load_*`` / ``validate``.
"""

from __future__ import annotations

from protovalidate import ValidationError

from grus.ir._diff import Diff, Mismatch, PedigreePair, SetDiff, diff, diff_set, match_individuals
from grus.ir._serialize import (
    dump_json,
    dump_pbtxt,
    dump_set_json,
    dump_set_pbtxt,
    load_json,
    load_pbtxt,
    load_set_json,
    load_set_pbtxt,
)
from grus.ir._validate import IntegrityError, validate, validate_set

__all__ = [
    "Diff",
    "IntegrityError",
    "Mismatch",
    "PedigreePair",
    "SetDiff",
    "ValidationError",
    "diff",
    "diff_set",
    "dump_json",
    "dump_pbtxt",
    "dump_set_json",
    "dump_set_pbtxt",
    "load_json",
    "load_pbtxt",
    "load_set_json",
    "load_set_pbtxt",
    "match_individuals",
    "validate",
    "validate_set",
]
