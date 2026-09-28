"""Citations: optional provenance in the IR (docs/design/ir.md, "Citations: where the record came from").

* **Round trip** — a set with quote and region citations, figure and panel evidence, and supports survives pbtxt and
  proto3-JSON.
* **Provenance, not meaning** — ``diff_set`` and the renderer ignore citations, evidence and supports.
* **Loud** — a malformed citation, a duplicate or dangling id, an unknown field, a scope not in the pedigree, and a
  condition no one in scope has all fail to load, naming the pedigree and the item.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from google.protobuf import text_format

from grus import ir, render
from grus.ir import _validate
from grus.models import pedigree_pb2 as pb

_GOLDENS = pathlib.Path(__file__).parent / "goldens"

# The ir.md example: "II-1, the proband, and her affected sibs II-2 and II-3" backs `proband` for II-1 and
# `condition.status` for II-1..II-3 through one shared citation; the caption names the condition for everyone.
_CITED = """
citations { id: "fig" region { page: 3 x0: 72 y0: 144 x1: 540 y1: 420 } }
citations { id: "cap" quote: "Figure 2. Pedigree of family A with cystic fibrosis." }
citations { id: "panel-a" region { page: 3 x0: 72 y0: 144 x1: 300 y1: 420 } }
citations { id: "s1" quote: "II-1, the proband, and her affected sibs II-2 and II-3" }
evidence: "fig"
evidence: "cap"
pedigrees {
  labels { text: "Family A" kind: LABEL_KIND_FAMILY }
  individuals { generation: 1 index: 1 gender: GENDER_MAN }
  individuals { generation: 1 index: 2 gender: GENDER_WOMAN }
  individuals { generation: 2 index: 1 gender: GENDER_WOMAN proband: true
                conditions { name: "cystic fibrosis" status: CONDITION_STATUS_AFFECTED } }
  individuals { generation: 2 index: 2 gender: GENDER_MAN
                conditions { name: "cystic fibrosis" status: CONDITION_STATUS_AFFECTED } }
  individuals { generation: 2 index: 3 gender: GENDER_WOMAN
                conditions { name: "cystic fibrosis" status: CONDITION_STATUS_AFFECTED } }
  matings {
    partner_a { generation: 1 index: 1 } partner_b { generation: 1 index: 2 } consanguineous: true
    offspring { child { generation: 2 index: 1 } }
    offspring { child { generation: 2 index: 2 } twin_group: 1 twin_type: ZYGOSITY_TYPE_DIZYGOTIC }
    offspring { child { generation: 2 index: 3 } twin_group: 1 twin_type: ZYGOSITY_TYPE_DIZYGOTIC }
  }
  evidence: "panel-a"
  supports { citations: "cap" field: "condition.name" condition: "cystic fibrosis" }
  supports { citations: "s1" field: "condition.status" condition: "cystic fibrosis"
             individuals { generation: 2 index: 1 } individuals { generation: 2 index: 2 }
             individuals { generation: 2 index: 3 } }
  supports { citations: "s1" field: "proband" individuals { generation: 2 index: 1 } }
  supports { citations: "cap" field: "consanguineous"
             matings { partner_a { generation: 1 index: 2 } partner_b { generation: 1 index: 1 } } }
  supports { citations: "cap" field: "twin_type" individuals { generation: 2 index: 2 } }
  supports { citations: "cap" field: "labels" }
  supports { citations: "cap" field: "documented_evaluation" individuals { generation: 2 index: 1 } }
  supports { citations: "cap" field: "count_unspecified" }
  supports { citations: "cap" field: "annotations" individuals { generation: 2 index: 3 } }
  supports { citations: "cap" field: "annotations"
             matings { partner_a { generation: 1 index: 1 } partner_b { generation: 1 index: 2 } } }
}
"""


def _cited() -> pb.PedigreeSet:
    return ir.load_set_pbtxt(_CITED)


def _uncited() -> pb.PedigreeSet:
    ps = pb.PedigreeSet()
    ps.CopyFrom(_cited())
    ps.ClearField("citations")
    ps.ClearField("evidence")
    for p in ps.pedigrees:
        p.ClearField("evidence")
        p.ClearField("supports")
    return ps


# --- round trip and ignored ---------------------------------------------------------------------------------------


def test_citations_round_trip_through_pbtxt_and_json() -> None:
    ps = _cited()
    assert ir.load_set_pbtxt(ir.dump_set_pbtxt(ps)) == ps
    assert ir.load_set_json(ir.dump_set_json(ps)) == ps
    assert ps.citations[0].WhichOneof("anchor") == "region" and ps.citations[1].WhichOneof("anchor") == "quote"
    assert ps.citations[0].document_id == "", "never model-written: the harness stamps it"


def test_diff_and_render_ignore_citations() -> None:
    cited, uncited = _cited(), _uncited()
    d = ir.diff_set(cited, uncited)
    assert d == ir.diff_set(uncited, uncited), "a record with and without citations diffs the same"
    assert len(d.matched) == 1 and not d.only_in_a and not d.only_in_b and not d.matched[0].diff.mismatches
    assert render.render_set_svg(cited) == render.render_set_svg(uncited)
    assert render.render_svg(cited.pedigrees[0]) == render.render_svg(uncited.pedigrees[0])


def test_support_fields_name_real_schema_fields() -> None:
    # The closed path list cannot drift from the schema: every path is a field of the message it scopes.
    messages = {
        "individual": pb.Individual,
        "condition": pb.Condition,
        "mating": pb.Mating,
        "offspring": pb.Offspring,
        "pedigree": pb.Pedigree,
    }
    for path, kinds in _validate.SUPPORT_FIELDS.items():
        for kind in kinds:
            name = path.removeprefix("condition.") if kind == "condition" else path
            assert (kind == "condition") == path.startswith("condition.")
            assert name in messages[kind].DESCRIPTOR.fields_by_name, f"{path} is not a {kind} field"
    assert _validate.SUPPORT_FIELDS["annotations"] == {"individual", "mating"}


# --- loud ---------------------------------------------------------------------------------------------------------


def _broken(edit: str, old: str) -> str:
    assert old in _CITED, old
    return _CITED.replace(old, edit, 1)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ('id: "cap"', 'id: "fig"', r"citation id 'fig' is used twice"),
        ('evidence: "cap"\n', 'evidence: "nope"\n', r"set evidence cites unknown citation 'nope'"),
        (
            'evidence: "panel-a"',
            'evidence: "nope"',
            r"pedigrees\[0\] \(labels=\['Family A'\]\): evidence cites unknown",
        ),
        (
            'supports { citations: "s1" field: "proband"',
            'supports { citations: "x" field: "proband"',
            r"supports\[2\] \(proband\) cites unknown citation 'x'",
        ),
        ('field: "proband"', 'field: "documented"', r"supports\[2\] \('documented'\): unknown field"),
        (
            'field: "proband" individuals { generation: 2 index: 1 }',
            'field: "proband" individuals { generation: 5 index: 1 }',
            r"individual \(5, 1\) is not in the pedigree",
        ),
        (
            "matings { partner_a { generation: 1 index: 2 } partner_b { generation: 1 index: 1 } }",
            "matings { partner_a { generation: 2 index: 2 } partner_b { generation: 1 index: 1 } }",
            r"no mating of \[\(1, 1\), \(2, 2\)\]",
        ),
        (
            'field: "condition.name" condition: "cystic fibrosis"',
            'field: "condition.name" condition: "sickle cell"',
            r"no one in scope has condition 'sickle cell'",
        ),
        ('field: "condition.name" condition: "cystic fibrosis"', 'field: "condition.name"', r"names a condition iff"),
        (
            'field: "proband" individuals',
            'field: "proband" condition: "cystic fibrosis" individuals',
            r"names a condition iff",
        ),
        (
            'field: "twin_type" individuals { generation: 2 index: 2 }',
            'field: "twin_type" individuals { generation: 1 index: 1 }',
            r"\(1, 1\) is no one's child; an offspring field scopes the child",
        ),
        (
            'field: "labels" }',
            'field: "labels" individuals { generation: 1 index: 1 } }',
            r"labels is a pedigree field",
        ),
        (
            'field: "twin_type" individuals { generation: 2 index: 2 }',
            'field: "twin_type" matings { partner_a { generation: 1 index: 1 } partner_b { generation: 1 index: 2 } }',
            r"scopes matings, but twin_type is not a mating field",
        ),
    ],
)
def test_a_bad_citation_or_support_fails_loud(old: str, new: str, match: str) -> None:
    with pytest.raises(ir.IntegrityError, match=match):
        ir.load_set_pbtxt(_broken(new, old))


def test_a_support_scopes_people_or_couples_not_both() -> None:
    bad = _broken(
        'field: "annotations" individuals { generation: 2 index: 3 } '
        "matings { partner_a { generation: 1 index: 1 } partner_b { generation: 1 index: 2 } } }",
        'field: "annotations" individuals { generation: 2 index: 3 } }',
    )
    with pytest.raises(ir.IntegrityError, match=r"scopes both individuals and matings"):
        ir.load_set_pbtxt(bad)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("x1: 540", "x1: 50"),  # x1 <= x0
        ('y1: 420 } }\ncitations { id: "cap"', 'y1: 100 } }\ncitations { id: "cap"'),  # y1 <= y0
        ("page: 3 x0: 72 y0: 144 x1: 540", "page: -1 x0: 72 y0: 144 x1: 540"),
        ('citations { id: "cap" quote:', 'citations { id: "" quote:'),
        (
            'citations { id: "cap" quote: "Figure 2. Pedigree of family A with cystic fibrosis." }',
            'citations { id: "cap" }',
        ),
    ],
)
def test_a_malformed_citation_fails_loud(old: str, new: str) -> None:
    with pytest.raises(ir.IntegrityError, match=r"citations\[\d\] .* is malformed"):
        ir.load_set_pbtxt(_broken(new, old))


def test_a_support_with_no_citation_is_malformed() -> None:
    bad = _broken('supports { field: "labels" }', 'supports { citations: "cap" field: "labels" }')
    with pytest.raises(ir.IntegrityError, match=r"not a valid pedigree"):
        ir.load_set_pbtxt(bad)


def test_goldens_are_unchanged_by_the_schema() -> None:
    # No golden carries citations, and parsing one back emits none.
    for path in _GOLDENS.glob("*.pbtxt"):
        p = ir.load_pbtxt(path.read_text())
        assert not p.evidence and not p.supports
        assert not re.search(r"\b(citations|evidence|supports)\b", text_format.MessageToString(p))
