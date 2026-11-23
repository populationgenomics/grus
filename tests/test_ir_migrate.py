"""Migrating a record written before the key (``grus.ir.migrate_*``; docs/design/ir.md, "The key", Migration).

* **0.4** — each condition declaration becomes a phenotype key entry with its name as the text, each person's entry a
  mark, and each support citing a condition cites the entry under the renamed path.
* **Pre-0.4** — conditions named on each entry are first declared once per distinct name, ids ``k1``, ``k2``, … by
  first appearance across the set; a ``condition.name`` / ``condition.inheritance`` support that scoped the people it
  described now describes the entry, so it scopes the whole pedigree.
* **Current** — a record already in the current shape migrates to itself.
* **Loud** — entries of one name that state different inheritance, two entries of one name on one person, and a
  support naming a condition no one has all fail; so does text in no known schema.
* **Ids are the record's own** — the stored-layout digest reads what people show through the key in the pre-0.4
  encoding, so an entry's id never enters it; its text and inheritance do.
"""

from __future__ import annotations

import json

import pytest
import test_render
from google.protobuf import text_format

from grus import ir
from grus.models import pedigree_pb2 as pb
from grus.render import _store

_PRE_0_4 = """
citations { id: "cap" quote: "Figure 1. Two families with CF and DMD." }
pedigrees {
  individuals { generation: 1 index: 1 gender: GENDER_MAN
                conditions { name: "CF" status: CONDITION_STATUS_CARRIER } }
  individuals { generation: 1 index: 2 gender: GENDER_WOMAN
                conditions { name: "CF" status: CONDITION_STATUS_CARRIER inheritance: INHERITANCE_AUTOSOMAL_RECESSIVE }
                conditions { name: "DMD" status: CONDITION_STATUS_CARRIER
                             inheritance: INHERITANCE_X_LINKED_RECESSIVE } }
  individuals { generation: 2 index: 1 gender: GENDER_MAN
                conditions { name: "CF" status: CONDITION_STATUS_AFFECTED onset_age: "P2Y" } }
  matings { partner_a { generation: 1 index: 1 } partner_b { generation: 1 index: 2 }
            offspring { child { generation: 2 index: 1 } } }
  supports { citations: "cap" field: "condition.name" condition: "CF" individuals { generation: 2 index: 1 } }
  supports { citations: "cap" field: "condition.status" condition: "DMD" individuals { generation: 1 index: 2 } }
}
pedigrees {
  individuals { generation: 1 index: 1 gender: GENDER_WOMAN conditions { status: CONDITION_STATUS_AFFECTED } }
  individuals { generation: 1 index: 2 gender: GENDER_MAN conditions { name: "DMD" status: CONDITION_STATUS_AFFECTED } }
}
"""

# The same record as 0.4 wrote it: conditions declared once and referenced by id.
_V0_4 = """
citations { id: "cap" quote: "Figure 1. Two families with CF and DMD." }
conditions { id: "k1" name: "CF" inheritance: INHERITANCE_AUTOSOMAL_RECESSIVE }
conditions { id: "k2" name: "DMD" inheritance: INHERITANCE_X_LINKED_RECESSIVE }
conditions { id: "k3" }
pedigrees {
  individuals { generation: 1 index: 1 gender: GENDER_MAN
                conditions { condition_id: "k1" status: CONDITION_STATUS_CARRIER } }
  individuals { generation: 1 index: 2 gender: GENDER_WOMAN
                conditions { condition_id: "k1" status: CONDITION_STATUS_CARRIER }
                conditions { condition_id: "k2" status: CONDITION_STATUS_CARRIER } }
  individuals { generation: 2 index: 1 gender: GENDER_MAN
                conditions { condition_id: "k1" status: CONDITION_STATUS_AFFECTED onset_age: "P2Y" } }
  matings { partner_a { generation: 1 index: 1 } partner_b { generation: 1 index: 2 }
            offspring { child { generation: 2 index: 1 } } }
  supports { citations: "cap" field: "condition.name" condition_id: "k1" }
  supports { citations: "cap" field: "condition.status" condition_id: "k2" individuals { generation: 1 index: 2 } }
}
pedigrees {
  individuals { generation: 1 index: 1 gender: GENDER_WOMAN
                conditions { condition_id: "k3" status: CONDITION_STATUS_AFFECTED } }
  individuals { generation: 1 index: 2 gender: GENDER_MAN
                conditions { condition_id: "k2" status: CONDITION_STATUS_AFFECTED } }
}
"""


def _marks(ps: pb.PedigreeSet) -> list[list[tuple[str, int]]]:
    return [[(m.key_id, m.status) for ind in p.individuals for m in ind.marks] for p in ps.pedigrees]


@pytest.mark.parametrize("old", [_V0_4, _PRE_0_4], ids=["0.4", "pre-0.4"])
def test_conditions_become_phenotype_entries_and_marks(old: str) -> None:
    ps = ir.migrate_pbtxt(old)
    assert [(e.id, e.text, e.WhichOneof("denotes"), e.phenotype.inheritance) for e in ps.key_entries] == [
        ("k1", "CF", "phenotype", pb.INHERITANCE_AUTOSOMAL_RECESSIVE),  # pre-0.4: one entry stated it, for all
        ("k2", "DMD", "phenotype", pb.INHERITANCE_X_LINKED_RECESSIVE),
        ("k3", "", "phenotype", pb.INHERITANCE_UNSPECIFIED),
    ]
    assert not ps.key_entries[2].phenotype.HasField("inheritance")
    a, c = pb.CONDITION_STATUS_AFFECTED, pb.CONDITION_STATUS_CARRIER
    assert _marks(ps) == [[("k1", c), ("k1", c), ("k2", c), ("k1", a)], [("k3", a), ("k2", a)]]
    assert ps.pedigrees[0].individuals[2].marks[0].onset_age == "P2Y"
    text, status = ps.pedigrees[0].supports
    assert (text.field, text.key_id, list(text.individuals)) == ("key.text", "k1", [])
    assert (status.field, status.key_id) == ("mark.status", "k2")
    assert [(i.generation, i.index) for i in status.individuals] == [(1, 2)], "a mark field's support keeps its people"
    assert ps.citations[0].id == "cap", "everything else carries over"
    assert ir.load_set_pbtxt(ir.dump_set_pbtxt(ps)) == ps


def test_pre_0_4_and_0_4_migrate_alike() -> None:
    assert ir.migrate_pbtxt(_PRE_0_4) == ir.migrate_pbtxt(_V0_4)


def test_json_and_a_bare_pedigree_migrate() -> None:
    old = """{"individuals": [{"generation": 1, "index": 1, "gender": "GENDER_MAN",
               "conditions": [{"name": "CF", "status": "CONDITION_STATUS_AFFECTED"}]}]}"""
    ps = ir.migrate_json(old)
    assert len(ps.pedigrees) == 1 and [(e.id, e.text) for e in ps.key_entries] == [("k1", "CF")]
    assert json.loads(ir.dump_set_json(ps))["pedigrees"][0]["individuals"][0]["marks"] == [
        {"keyId": "k1", "status": "CONDITION_STATUS_AFFECTED"}
    ]


@pytest.mark.parametrize(
    "old",
    [
        'individuals { generation: 1 index: 1 gender: GENDER_MAN external_id: "x" }',  # no conditions at all
        test_render._GOLDENS.joinpath("carrier_inheritance.pbtxt").read_text(),  # already the current shape
    ],
    ids=["no conditions", "current"],
)
def test_a_record_with_nothing_to_migrate_migrates_to_itself(old: str) -> None:
    try:
        expected = text_format.Parse(old, pb.PedigreeSet())
    except text_format.ParseError:
        expected = pb.PedigreeSet(pedigrees=[text_format.Parse(old, pb.Pedigree())])
    assert ir.migrate_pbtxt(old) == expected


def test_text_in_no_known_schema_fails_loud() -> None:
    with pytest.raises(ir.MigrationError, match="not a pedigree set or pedigree"):
        ir.migrate_pbtxt('individuals { generation: 1 index: 1 gender: GENDER_MAN favourite_colour: "blue" }')


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            'name: "CF" status: CONDITION_STATUS_CARRIER }',
            'name: "CF" status: CONDITION_STATUS_CARRIER inheritance: INHERITANCE_AUTOSOMAL_DOMINANT }',
            r"entries of condition 'CF' disagree on inheritance: "
            r"\['INHERITANCE_AUTOSOMAL_DOMINANT', 'INHERITANCE_AUTOSOMAL_RECESSIVE'\]",
        ),
        (
            'conditions { name: "CF" status: CONDITION_STATUS_AFFECTED onset_age: "P2Y" }',
            'conditions { name: "CF" status: CONDITION_STATUS_AFFECTED } conditions { name: "CF" status: '
            "CONDITION_STATUS_CARRIER }",
            r"individual \(2, 1\) has several entries of condition\(s\) \['CF'\]",
        ),
        ('condition: "DMD"', 'condition: "SMA"', r"supports\[1\] names condition 'SMA', which no entry has"),
    ],
)
def test_what_has_no_current_form_fails_loud(old: str, new: str, match: str) -> None:
    assert old in _PRE_0_4
    with pytest.raises(ir.MigrationError, match=match):
        ir.migrate_pbtxt(_PRE_0_4.replace(old, new, 1))


def test_an_entry_id_never_enters_the_layout_digest() -> None:
    # The digest reads what each person shows through the key, so ids are the record's own business.
    p, defs = test_render._golden("compound_carrier")
    renamed = pb.Pedigree()
    renamed.CopyFrom(p)
    new_id = {e.id: f"x-{e.id}" for e in defs}
    for ind in renamed.individuals:
        for m in ind.marks:
            m.key_id = new_id[m.key_id]
    renamed_defs = [pb.KeyEntry(id=new_id[e.id], text=e.text, phenotype=e.phenotype) for e in defs]
    assert _store.pedigree_digest(renamed, renamed_defs) == _store.pedigree_digest(p, defs)
    edited = [pb.KeyEntry(id=e.id, text=e.text + " (edited)", phenotype=e.phenotype) for e in defs]
    assert _store.pedigree_digest(p, edited) != _store.pedigree_digest(p, defs), "an entry's text is content"
    stated = [
        pb.KeyEntry(id=e.id, text=e.text, phenotype=pb.Phenotype(inheritance=pb.INHERITANCE_AUTOSOMAL_RECESSIVE))
        for e in defs
    ]
    assert _store.pedigree_digest(p, stated) != _store.pedigree_digest(p, defs), "so is a declared inheritance"
