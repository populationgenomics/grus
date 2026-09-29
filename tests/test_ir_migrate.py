"""Migrating a pre-1.0 record: conditions named on each entry -> declared once, by id (``grus.ir.migrate_*``).

* **Grouping** — one declaration per distinct name, ids ``k1``, ``k2``, … by first appearance across the set; every
  entry and support points at its declaration.
* **Declaration supports** — a ``condition.name`` / ``condition.inheritance`` support that scoped the people it
  described now describes the declaration, so it scopes the whole pedigree.
* **Loud** — entries of one name that state different inheritance, two entries of one name on one person, and a
  support naming a condition no one has all fail.
* **Continuity** — a migrated record keeps its stored-layout digest: the digest reads entries through their
  declarations in the pre-1.0 encoding, so a declaration's id never enters it.
"""

from __future__ import annotations

import json

import pytest
import test_render
from google.protobuf import text_format

from grus import ir
from grus.models import pedigree_pb2 as pb
from grus.render import _store

_OLD_SET = """
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


def _entries(ps: pb.PedigreeSet) -> list[list[tuple[str, int]]]:
    return [[(c.condition_id, c.status) for ind in p.individuals for c in ind.conditions] for p in ps.pedigrees]


def test_one_declaration_per_name_by_first_appearance() -> None:
    ps = ir.migrate_pbtxt(_OLD_SET)
    assert [(d.id, d.name, d.HasField("inheritance") and d.inheritance) for d in ps.conditions] == [
        ("k1", "CF", pb.INHERITANCE_AUTOSOMAL_RECESSIVE),  # the first entry states none; the second's holds for all
        ("k2", "DMD", pb.INHERITANCE_X_LINKED_RECESSIVE),
        ("k3", "", False),
    ]
    a, c = pb.CONDITION_STATUS_AFFECTED, pb.CONDITION_STATUS_CARRIER
    assert _entries(ps) == [[("k1", c), ("k1", c), ("k2", c), ("k1", a)], [("k3", a), ("k2", a)]]
    assert ps.pedigrees[0].individuals[2].conditions[0].onset_age == "P2Y"
    assert [sup.condition_id for sup in ps.pedigrees[0].supports] == ["k1", "k2"]
    name, status = ps.pedigrees[0].supports
    assert not name.individuals, "a declaration field's support loses its people"
    assert [(i.generation, i.index) for i in status.individuals] == [(1, 2)], "an entry field's keeps them"
    assert ps.citations[0].id == "cap", "everything else carries over"
    assert ir.load_set_pbtxt(ir.dump_set_pbtxt(ps)) == ps


def test_json_and_a_bare_pedigree_migrate() -> None:
    old = """{"individuals": [{"generation": 1, "index": 1, "gender": "GENDER_MAN",
               "conditions": [{"name": "CF", "status": "CONDITION_STATUS_AFFECTED"}]}]}"""
    ps = ir.migrate_json(old)
    assert len(ps.pedigrees) == 1 and [(d.id, d.name) for d in ps.conditions] == [("k1", "CF")]
    assert json.loads(ir.dump_set_json(ps))["pedigrees"][0]["individuals"][0]["conditions"] == [
        {"conditionId": "k1", "status": "CONDITION_STATUS_AFFECTED"}
    ]


def test_a_record_without_conditions_migrates_to_itself() -> None:
    old = 'individuals { generation: 1 index: 1 gender: GENDER_MAN external_id: "x" }'
    ps = ir.migrate_pbtxt(old)
    assert not ps.conditions and ps.pedigrees[0] == text_format.Parse(old, pb.Pedigree())


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
def test_what_has_no_1_0_form_fails_loud(old: str, new: str, match: str) -> None:
    assert old in _OLD_SET
    with pytest.raises(ir.MigrationError, match=match):
        ir.migrate_pbtxt(_OLD_SET.replace(old, new, 1))


def test_a_migrated_record_keeps_its_layout_digest() -> None:
    # The layout pins hold the pre-1.0 digests; every golden migrated by grouping kept its pin (see layout_pins.json).
    # Here: the digest reads each entry through its declaration, so ids are the record's own business.
    p, defs = test_render._golden("compound_carrier")
    renamed = pb.Pedigree()
    renamed.CopyFrom(p)
    new_id = {d.id: f"x-{d.id}" for d in defs}
    for ind in renamed.individuals:
        for c in ind.conditions:
            c.condition_id = new_id[c.condition_id]
    renamed_defs = [pb.ConditionDef(id=new_id[d.id], name=d.name) for d in defs]
    assert _store.pedigree_digest(renamed, renamed_defs) == _store.pedigree_digest(p, defs)
    edited = [pb.ConditionDef(id=d.id, name=d.name + " (edited)") for d in defs]
    assert _store.pedigree_digest(p, edited) != _store.pedigree_digest(p, defs), "a declared name is content"
    stated = [pb.ConditionDef(id=d.id, name=d.name, inheritance=pb.INHERITANCE_AUTOSOMAL_RECESSIVE) for d in defs]
    assert _store.pedigree_digest(p, stated) != _store.pedigree_digest(p, defs), "so is a declared inheritance"


# Pedigree digests pinned before 1.0 (layout_pins.json, LAYOUT_VERSION 6): the migrated goldens still have them.
_PRE_1_0_DIGESTS = {
    "trio": "c9ff32d81e2c4fb568c8acfcf43bff2bbf3319cccb5faaeb73fc5eb8408a24fe",
    "six_conditions": "7e5309774d590a3809fd9b6ec7829b5294a09d1a908b7f96b31157a599c0d83f",
}


@pytest.mark.parametrize("name", sorted(_PRE_1_0_DIGESTS))
def test_the_digest_is_the_pre_1_0_one(name: str) -> None:
    p, defs = test_render._golden(name)
    assert _store.pedigree_digest(p, defs).hex() == _PRE_1_0_DIGESTS[name]
