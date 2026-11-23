"""The key: phenotype, genotype and other entries, and what a person shows (docs/design/ir.md, "The key").

* **Kinds** — a phenotype mark has a standard status or one of its entry's grades; an other mark is shown and nothing
  more; a genotype entry is never marked, and a person shows it when their genotype matches its pattern.
* **Checks** — the loader fails loud on each broken invariant, naming the entry or the person.
* **Consumers** — the diff compares marks by what their entries state and leaves genotype entries to the genotypes;
  the renderer draws other and genotype entries as flat fills labelled with their text, and defers a grade; no one but
  an affected phenotype mark counts as affected.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Callable

import pytest
from google.protobuf import text_format

from grus import ir, render
from grus.models import pedigree_pb2 as pb
from grus.render import _labels

# A recessive figure whose key maps one fill to the disease, one to a variant, one to controls, and grades the
# disease: the proband is affected; her mother carries the variant (copies not stated) and is suspected; her father is
# a control.
_SET = """
citations { id: "legend" quote: "■ hearing loss; ▨ c.1234A>G; ● control; grey: suspected, not examined" }
loci {
  id: "l1" name: "c.1234A>G" gene: "SLC26A4"
  alleles { symbol: "+" reference: true }
  alleles { symbol: "M" name: "c.1234A>G" }
}
key_entries { id: "k1" text: "hearing loss" phenotype { grades: "suspected, not examined" } }
key_entries { id: "k2" text: "c.1234A>G" genotype { locus_id: "l1" alleles: "M" copies_unstated: true } }
key_entries { id: "k3" text: "control" other {} }
pedigrees {
  individuals { generation: 1 index: 1 gender: GENDER_MAN marks { key_id: "k3" } }
  individuals {
    generation: 1 index: 2 gender: GENDER_WOMAN
    marks { key_id: "k1" grade: "suspected, not examined" }
    genotypes { locus_id: "l1" alleles: "M" copies_unstated: true }
  }
  individuals {
    generation: 2 index: 1 gender: GENDER_WOMAN proband: true
    marks { key_id: "k1" status: CONDITION_STATUS_AFFECTED }
    genotypes { locus_id: "l1" alleles: "M" alleles: "M" }
  }
  matings {
    partner_a { generation: 1 index: 1 }
    partner_b { generation: 1 index: 2 }
    offspring { child { generation: 2 index: 1 } }
  }
  supports { citations: "legend" field: "key.grades" key_id: "k1" }
  supports { citations: "legend" field: "key.text" key_id: "k2" }
  supports { citations: "legend" field: "mark.status" key_id: "k3" individuals { generation: 1 index: 1 } }
}
"""


def _set() -> pb.PedigreeSet:
    return text_format.Parse(_SET, pb.PedigreeSet())


def _ind(ps: pb.PedigreeSet, generation: int, index: int) -> pb.Individual:
    (ind,) = (i for i in ps.pedigrees[0].individuals if (i.generation, i.index) == (generation, index))
    return ind


def _ungraded() -> pb.PedigreeSet:
    """The sample with the mother's grade replaced by a status, so it draws."""
    ps = _set()
    _ind(ps, 1, 2).marks[0].status = pb.CONDITION_STATUS_UNAFFECTED
    return ps


def test_the_sample_is_valid_and_round_trips() -> None:
    ir.validate_set(_set())
    assert ir.load_set_pbtxt(ir.dump_set_pbtxt(_set())) == _set()
    assert ir.load_set_json(ir.dump_set_json(_set())) == _set()


def test_a_person_shows_marks_then_matched_genotype_entries() -> None:
    ps = _set()
    key = ir.Key(ps.key_entries)
    shown = {(i.generation, i.index): [(s.key_id, s.kind) for s in key.shown(i)] for i in ps.pedigrees[0].individuals}
    assert shown == {
        (1, 1): [("k3", "other")],
        (1, 2): [("k1", "phenotype"), ("k2", "genotype")],
        (2, 1): [("k1", "phenotype"), ("k2", "genotype")],
    }
    assert [s.grade for s in key.marked(_ind(ps, 1, 2))] == ["suspected, not examined"]


@pytest.mark.parametrize(
    ("pattern", "genotype", "shows"),
    [
        # "carries M": any genotype carrying M, copies stated or not
        (pb.GenotypePattern(alleles=["M"], copies_unstated=True), pb.Genotype(alleles=["M", "+"]), True),
        (pb.GenotypePattern(alleles=["M"], copies_unstated=True), pb.Genotype(alleles=["M"]), True),
        (pb.GenotypePattern(alleles=["M"], copies_unstated=True), pb.Genotype(alleles=["+", "+"]), False),
        # exact copies: only the same copies, in any order, and never a genotype whose copies are not stated
        (pb.GenotypePattern(alleles=["M", "M"]), pb.Genotype(alleles=["M", "M"]), True),
        (pb.GenotypePattern(alleles=["M", "+"]), pb.Genotype(alleles=["+", "M"]), True),
        (pb.GenotypePattern(alleles=["M", "M"]), pb.Genotype(alleles=["M", "+"]), False),
        (pb.GenotypePattern(alleles=["M", "+"]), pb.Genotype(alleles=["M"], copies_unstated=True), False),
    ],
)
def test_a_genotype_entry_is_shown_when_the_genotype_matches(
    pattern: pb.GenotypePattern, genotype: pb.Genotype, shows: bool
) -> None:
    pattern.locus_id = genotype.locus_id = "l1"
    assert ir.pattern_matches(pattern, genotype) is shows
    genotype.locus_id = "l2"
    assert not ir.pattern_matches(pattern, genotype), "another locus never matches"


def test_a_key_that_grades_zygosity_is_two_entries_at_one_locus() -> None:
    ps = _ungraded()
    del ps.key_entries[1]
    ps.key_entries.add(id="hom", text="homozygous", genotype=pb.GenotypePattern(locus_id="l1", alleles=["M", "M"]))
    ps.key_entries.add(id="het", text="heterozygous", genotype=pb.GenotypePattern(locus_id="l1", alleles=["M", "+"]))
    _ind(ps, 1, 2).genotypes[0].CopyFrom(pb.Genotype(locus_id="l1", alleles=["M", "+"]))
    del ps.pedigrees[0].supports[1]
    ir.validate_set(ps)
    key = ir.Key(ps.key_entries)
    assert [s.key_id for s in key.shown(_ind(ps, 1, 2)) if s.kind == "genotype"] == ["het"]
    assert [s.key_id for s in key.shown(_ind(ps, 2, 1)) if s.kind == "genotype"] == ["hom"]


def _edit(path: Callable[[pb.PedigreeSet], object], name: str, value: object) -> Callable[[pb.PedigreeSet], None]:
    return lambda ps: setattr(path(ps), name, value)


# One edit per loader invariant, each breaking a valid set: (edit, the message the failure names).
_BROKEN: dict[str, tuple[Callable[[pb.PedigreeSet], object], str]] = {
    "marking a genotype entry": (
        lambda ps: _ind(ps, 1, 1).marks.add(key_id="k2"),
        r"marks genotype entry 'k2'; a person shows a genotype entry through their genotype",
    ),
    "an other mark with a status": (
        _edit(lambda ps: _ind(ps, 1, 1).marks[0], "status", pb.CONDITION_STATUS_AFFECTED),
        r"other entry 'k3' \('control'\) is shown, nothing more",
    ),
    "an other mark with an onset age": (
        _edit(lambda ps: _ind(ps, 1, 1).marks[0], "onset_age", "40s"),
        r"other entry 'k3' \('control'\) is shown, nothing more",
    ),
    "a phenotype mark with no status or grade": (
        lambda ps: _ind(ps, 2, 1).marks[0].ClearField("status"),
        r"phenotype mark 'k1' needs a status or a grade",
    ),
    "an undeclared grade": (
        _edit(lambda ps: _ind(ps, 1, 2).marks[0], "grade", "probable"),
        r"grade 'probable' is not one of 'k1''s grades",
    ),
    "a grade declared twice": (
        lambda ps: ps.key_entries[0].phenotype.grades.append("suspected, not examined"),
        r"a grade is declared twice",
    ),
    "an other entry with empty text": (
        _edit(lambda ps: ps.key_entries[2], "text", ""),
        r"only a phenotype may have empty text",
    ),
    "one text across kinds": (
        _edit(lambda ps: ps.key_entries[2], "text", "hearing loss"),
        r"key entry 'hearing loss' is already declared as 'k1'",
    ),
    "a pattern at an undeclared locus": (
        _edit(lambda ps: ps.key_entries[1].genotype, "locus_id", "l9"),
        r"key_entries\[1\] \('k2'\): locus_id 'l9' is not declared",
    ),
    "a pattern allele not a symbol": (
        lambda ps: ps.key_entries[1].genotype.alleles.__setitem__(0, "X"),
        r"allele 'X' at locus 'l1' is not one of its symbols",
    ),
    "one pattern keyed twice": (
        lambda ps: ps.key_entries.add(id="k4", text="variant", genotype=ps.key_entries[1].genotype),
        r"the same genotype is already keyed as 'k2'",
    ),
    "a genotype entry no one shows": (
        lambda ps: ps.key_entries.add(
            id="k4", text="homozygous", genotype=pb.GenotypePattern(locus_id="l1", alleles=["+", "+"])
        ),
        r"key_entries\[3\] \('k4', 'homozygous'\) is declared but no one shows it",
    ),
    "a key entry id that is a locus id": (
        _edit(lambda ps: ps.loci[0], "id", "k3"),
        r"locus id 'k3' is also a citation or key entry id",
    ),
    "a mark support for an entry no one marks": (
        _edit(lambda ps: ps.pedigrees[0].supports[2], "key_id", "k2"),
        r"no one in scope marks key entry 'k2'",
    ),
    "a mark support whose person no longer marks the entry": (
        lambda ps: _ind(ps, 1, 1).marks.pop(),
        r"no one in scope marks key entry 'k3'",
    ),
    "a key support for a genotype entry no one shows": (
        lambda ps: [
            g.CopyFrom(pb.Genotype(locus_id="l1", alleles=["+", "+"]))
            for g in (_ind(ps, 1, 2).genotypes[0], _ind(ps, 2, 1).genotypes[0])
        ],
        r"no one in scope shows key entry 'k2'",
    ),
}


@pytest.mark.parametrize("case", sorted(_BROKEN))
def test_the_loader_rejects(case: str) -> None:
    edit, match = _BROKEN[case]
    ps = _set()
    edit(ps)
    with pytest.raises(ir.IntegrityError, match=match):
        ir.validate_set(ps)


def test_a_key_support_may_cite_a_genotype_entry() -> None:
    # A genotype entry is shown, not marked: key.* names it, mark.* cannot.
    ps = _set()
    ps.pedigrees[0].supports.add(citations=["legend"], field="mark.status", key_id="k2")
    with pytest.raises(ir.IntegrityError, match=r"no one in scope marks key entry 'k2'"):
        ir.validate_set(ps)


# --- consumers --------------------------------------------------------------------------------------------------


def _mark_mismatches(a: pb.PedigreeSet, b: pb.PedigreeSet) -> dict[str, tuple[str, str]]:
    (pair,) = ir.diff_set(a, b).matched
    return {m.subject: (m.a_value, m.b_value) for m in pair.diff.mismatches if m.field == "marks"}


def test_diff_compares_marks_by_kind_and_text() -> None:
    other_as_phenotype = _set()
    other_as_phenotype.key_entries[2].ClearField("other")
    other_as_phenotype.key_entries[2].phenotype.SetInParent()
    _ind(other_as_phenotype, 1, 1).marks[0].status = pb.CONDITION_STATUS_AFFECTED
    assert _mark_mismatches(_set(), other_as_phenotype) == {
        "1-1": ("control (other)", "control:CONDITION_STATUS_AFFECTED")
    }


def test_diff_compares_a_grade() -> None:
    assert _mark_mismatches(_set(), _ungraded()) == {
        "1-2": ("hearing loss:'suspected, not examined'", "hearing loss:CONDITION_STATUS_UNAFFECTED")
    }


def test_diff_leaves_genotype_entries_to_the_genotypes() -> None:
    # Whether a reading keyed a variant as a fill is how the figure draws it; the genotypes carry the meaning.
    unkeyed = _set()
    del unkeyed.key_entries[1]
    del unkeyed.pedigrees[0].supports[1]
    ir.validate_set(unkeyed)
    (pair,) = ir.diff_set(_set(), unkeyed).matched
    assert pair.diff.mismatches == []


def test_only_an_affected_phenotype_is_affected() -> None:
    # A control, a keyed variant's carrier and a suspected case are none of them affected.
    ps = _set()
    key = ir.Key(ps.key_entries)
    affected = {
        (i.generation, i.index)
        for i in ps.pedigrees[0].individuals
        for s in key.shown(i)
        if s.status == pb.CONDITION_STATUS_AFFECTED
    }
    assert affected == {(2, 1)}


def test_the_renderer_defers_a_grade() -> None:
    (svg,) = render.render_svgs(_set())
    assert "deferred" in svg[1] and "a grade the key defines is not drawn yet" in svg[1]
    with pytest.raises(render.DeferredFeatureError, match=r"I-2 is graded 'suspected, not examined'"):
        render.layout(_set().pedigrees[0], key_entries=_set().key_entries, loci=_set().loci)


def test_other_and_genotype_entries_draw_as_flat_fills_keyed_by_text() -> None:
    ps = _ungraded()
    p = ps.pedigrees[0]
    svg = render.render_svg(p, key_entries=ps.key_entries, loci=ps.loci)
    root = ET.fromstring(svg)
    legend = json.loads(root.get("data-conditions", "null"))
    assert sorted(legend) == ["c.1234A>G", "control", "hearing loss"]
    ns = {"s": "http://www.w3.org/2000/svg"}
    by_pos = {g.get("data-position"): g for g in root.iterfind(".//s:g[@data-position]", ns)}
    assert by_pos["I-1"].get(f"data-condition-{legend.index('control')}") == "shown"
    assert by_pos["II-1"].get(f"data-condition-{legend.index('c.1234A>G')}") == "shown"
    assert by_pos["II-1"].get(f"data-condition-{legend.index('hearing loss')}") == "affected"
    labels = [t.text for t in root.iterfind(".//s:g[@class='key']//s:text", ns)]
    assert "control" in labels and "c.1234A>G" in labels and "hearing loss" in labels
    assert "Affected" not in labels, "a shown entry is labelled with its text, never as affected"


def test_a_genotype_that_stops_matching_changes_the_drawing_and_the_digest() -> None:
    ps = _ungraded()
    p = ps.pedigrees[0]
    before = render.render_svg(p, key_entries=ps.key_entries, loci=ps.loci)
    digest = render.store_layout(p, key_entries=ps.key_entries, loci=ps.loci).key.pedigree_digest
    _ind(ps, 1, 2).genotypes[0].CopyFrom(pb.Genotype(locus_id="l1", alleles=["+", "+"]))
    assert render.render_svg(p, key_entries=ps.key_entries, loci=ps.loci) != before
    assert render.store_layout(p, key_entries=ps.key_entries, loci=ps.loci).key.pedigree_digest != digest


def test_a_shown_entry_is_painted_flat_and_a_grade_not_at_all() -> None:
    shown = ir.Shown("k3", "other", "control")
    assert _labels.paint(shown) == _labels.SHOWN and _labels.fills(shown)
    carrier = ir.Shown("k1", "phenotype", "CF", status=pb.CONDITION_STATUS_CARRIER)
    assert not _labels.fills(carrier)
    with pytest.raises(ValueError, match="has no drawing"):
        _labels.paint(ir.Shown("k1", "phenotype", "CF", grade="suspected"))
