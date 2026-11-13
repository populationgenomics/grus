"""Tests for loci, alleles and genotypes: the loader's invariants, supports, and what the renderer makes of them.

docs/design/ir.md, "Genotypes".
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from google.protobuf import text_format

from grus import ir, render
from grus.models import pedigree_pb2 as pb

# A compound-heterozygous child of two carrier parents, as a figure with a legend ("M1 c.1234A>G, M2 c.2000del")
# records it: two variant loci, the child's genotypes in trans in one phase set, each drawn label linked to the loci
# it shows, the symbols the figure prints for the alleles, and the legend cited for the alleles.
_SET = """
citations { id: "legend" quote: "M1, c.1234A>G; M2, c.2000del" }
loci {
  id: "l1" name: "c.1234A>G" gene: "SLC26A4"
  alleles { id: "wt" reference: true }
  alleles { id: "m1" name: "c.1234A>G" identifiers { scheme: IDENTIFIER_SCHEME_HGVS value: "c.1234A>G" } }
}
loci {
  id: "l2" name: "c.2000del" gene: "SLC26A4"
  alleles { id: "wt" reference: true }
  alleles { id: "m2" name: "c.2000del" }
}
pedigrees {
  allele_symbols { locus_id: "l1" allele_id: "wt" symbol: "+" }
  allele_symbols { locus_id: "l1" allele_id: "m1" symbol: "M1" }
  allele_symbols { locus_id: "l2" allele_id: "wt" symbol: "+" }
  allele_symbols { locus_id: "l2" allele_id: "m2" symbol: "M2" }
  individuals {
    generation: 1 index: 1 gender: GENDER_MAN
    annotations { text: "M1/+" type: ANNOTATION_TYPE_GENOTYPE loci: "l1" }
    genotypes { locus_id: "l1" allele_ids: "m1" allele_ids: "wt" }
  }
  individuals {
    generation: 1 index: 2 gender: GENDER_WOMAN
    annotations { text: "M2/+" type: ANNOTATION_TYPE_GENOTYPE loci: "l2" }
    genotypes { locus_id: "l2" allele_ids: "m2" allele_ids: "wt" }
  }
  individuals {
    generation: 2 index: 1 gender: GENDER_MAN proband: true
    annotations { text: "M1/M2" type: ANNOTATION_TYPE_GENOTYPE loci: "l1" loci: "l2" }
    genotypes { locus_id: "l1" allele_ids: "m1" allele_ids: "wt" phase_set: "a" }
    genotypes { locus_id: "l2" allele_ids: "wt" allele_ids: "m2" phase_set: "a" }
  }
  matings {
    partner_a { generation: 1 index: 1 }
    partner_b { generation: 1 index: 2 }
    offspring { child { generation: 2 index: 1 } }
  }
  supports { citations: "legend" field: "locus.alleles" loci: "l1" loci: "l2" }
  supports { citations: "legend" field: "genotype.allele_ids" loci: "l2" individuals { generation: 2 index: 1 } }
}
"""


def _set() -> pb.PedigreeSet:
    return text_format.Parse(_SET, pb.PedigreeSet())


def _child(ps: pb.PedigreeSet) -> pb.Individual:
    return ps.pedigrees[0].individuals[2]


def test_the_sample_is_valid() -> None:
    ir.validate_set(_set())
    assert ir.load_set_pbtxt(ir.dump_set_pbtxt(_set())) == _set()
    assert ir.load_set_json(ir.dump_set_json(_set())) == _set()


def test_hemizygous_and_homozygous_genotypes_are_valid() -> None:
    ps = _set()
    _child(ps).genotypes[0].ClearField("phase_set")
    _child(ps).genotypes[1].ClearField("phase_set")
    del _child(ps).genotypes[0].allele_ids[1:]  # [M1]: one copy, on a single-copy locus
    _child(ps).genotypes[1].allele_ids[:] = ["m2", "m2"]
    ir.validate_set(ps)


def _set_field(path: Callable[[pb.PedigreeSet], object], name: str, value: object) -> Callable[[pb.PedigreeSet], None]:
    return lambda ps: setattr(path(ps), name, value)


def _unnamed_loci(ps: pb.PedigreeSet) -> None:
    """Both loci unnamed, in one gene, each with the reference and an unnamed allele: a legend printing only symbols."""
    for locus in ps.loci:
        locus.ClearField("name")
        locus.alleles[1].ClearField("name")


def _same_unnamed_loci(ps: pb.PedigreeSet) -> None:
    """Both loci unnamed, in one gene, with the same named alleles: they state one locus."""
    for locus in ps.loci:
        locus.ClearField("name")
        locus.alleles[1].name = "c.1234A>G"


# One edit per loader invariant, each breaking a valid set: (edit, the message the failure names).
_BROKEN: dict[str, tuple[Callable[[pb.PedigreeSet], object], str]] = {
    "undeclared locus": (_set_field(lambda ps: _child(ps).genotypes[0], "locus_id", "l9"), r"'l9' is not declared"),
    "undeclared allele": (
        lambda ps: _child(ps).genotypes[0].allele_ids.__setitem__(0, "m2"),
        r"allele id 'm2' is not declared at locus 'l1'",
    ),
    "two genotypes at one locus": (
        lambda ps: _child(ps).genotypes.add(locus_id="l1", allele_ids=["wt", "wt"]),
        r"two genotypes at locus 'l1'",
    ),
    "three alleles": (lambda ps: _child(ps).genotypes[0].allele_ids.append("wt"), r"not a valid pedigree"),
    "no alleles": (lambda ps: _child(ps).genotypes[0].ClearField("allele_ids"), r"not a valid pedigree"),
    "phase set of unequal ploidy": (
        lambda ps: _child(ps).genotypes[1].allele_ids.pop(),
        r"phase set 'a' mixes genotypes of 2 and 1 alleles",
    ),
    "locus declared twice": (lambda ps: ps.loci.append(ps.loci[0]), r"locus id 'l1' is declared twice"),
    "locus name declared twice": (
        lambda ps: setattr(ps.loci[1], "name", "c.1234A>G"),
        r"is already declared as 'l1'",
    ),
    "unnamed loci with the same alleles": (
        _same_unnamed_loci,
        r"locus \(gene='SLC26A4', alleles=\['c.1234A>G', 'reference'\]\) is already declared as 'l1'",
    ),
    "unnamed allele with no symbol": (
        lambda ps: (_unnamed_loci(ps), ps.pedigrees[0].allele_symbols.pop(1)),
        r"allele 'm1' at locus 'l1' is unnamed and the pedigree prints no symbol for it",
    ),
    "symbol row twice": (
        lambda ps: ps.pedigrees[0].allele_symbols.append(ps.pedigrees[0].allele_symbols[1]),
        r"allele 'm1' at locus 'l1' is listed twice",
    ),
    "locus id is a citation id": (
        lambda ps: ps.citations.add(id="l2", quote="x"),
        r"locus id 'l2' is also a citation or condition id",
    ),
    "allele id twice": (lambda ps: ps.loci[0].alleles.add(id="m1"), r"allele id\(s\) \['m1'\] are declared twice"),
    "allele name twice": (
        lambda ps: ps.loci[0].alleles.add(id="m9", name="c.1234A>G"),
        r"allele name\(s\) \['c.1234A>G'\] are declared twice",
    ),
    "allele without an id": (lambda ps: ps.loci[0].alleles.add(name="c.1300T>C"), r"loci\[0\] \('l1'\) is malformed"),
    "symbol at an undeclared locus": (
        lambda ps: ps.pedigrees[0].allele_symbols.add(locus_id="l9", allele_id="wt", symbol="+"),
        r"allele_symbols\[4\] \('\+'\): locus_id 'l9' is not declared",
    ),
    "symbol of an undeclared allele": (
        lambda ps: ps.pedigrees[0].allele_symbols.add(locus_id="l1", allele_id="m2", symbol="M2"),
        r"allele id 'm2' is not declared at locus 'l1'",
    ),
    "one symbol for two alleles in a pedigree": (
        lambda ps: ps.pedigrees[0].allele_symbols.add(locus_id="l1", allele_id="m1", symbol="+"),
        r"symbol '\+' at locus 'l1' already names allele 'wt'",
    ),
    "two symbols for one allele in a pedigree": (
        lambda ps: ps.pedigrees[0].allele_symbols.add(locus_id="l1", allele_id="m1", symbol="Mut"),
        r"allele 'm1' at locus 'l1' already has symbol 'M1'",
    ),
    "empty symbol": (_set_field(lambda ps: ps.pedigrees[0].allele_symbols[1], "symbol", ""), r"not a valid pedigree"),
    "two reference alleles": (
        lambda ps: setattr(ps.loci[0].alleles[1], "reference", True),
        r"more than one allele is the reference",
    ),
    "support names a locus twice": (
        lambda ps: ps.pedigrees[0].supports[1].loci.append("l2"),
        r"names a locus twice",
    ),
    "support names an undeclared locus": (
        lambda ps: ps.pedigrees[0].supports[0].loci.append("l9"),
        r"locus 'l9' is not declared",
    ),
    "empty gene": (lambda ps: setattr(ps.loci[0], "gene", ""), r"loci\[0\] \('l1'\) is malformed"),
    "locus with no alleles": (lambda ps: ps.loci[0].ClearField("alleles"), r"loci\[0\] \('l1'\) is malformed"),
    "unreferenced locus": (
        lambda ps: ps.loci.add(id="l3", name="D12S1640", alleles=[pb.Allele(id="a1", name="1")]),
        r"loci\[2\] \('l3'\) is declared but no genotype references it",
    ),
    "identifier without a scheme": (
        lambda ps: setattr(ps.loci[0].alleles[1].identifiers[0], "scheme", pb.IDENTIFIER_SCHEME_UNSPECIFIED),
        r"loci\[0\] \('l1'\) is malformed",
    ),
    "annotation names an ungenotyped locus": (
        lambda ps: ps.pedigrees[0].individuals[0].annotations[0].loci.append("l2"),
        r"names locus 'l2', which this person has no genotype at",
    ),
    "annotation names a locus twice": (
        lambda ps: _child(ps).annotations[0].loci.append("l1"),
        r"names a locus twice",
    ),
    "non-genotype annotation names a locus": (
        _set_field(lambda ps: _child(ps).annotations[0], "type", pb.ANNOTATION_TYPE_OTHER),
        r"names loci but is ANNOTATION_TYPE_OTHER",
    ),
    "mating annotation names a locus": (
        lambda ps: ps.pedigrees[0].matings[0].annotations.add(text="M1", type=pb.ANNOTATION_TYPE_GENOTYPE, loci=["l1"]),
        r"a couple has no genotype",
    ),
    "genotype support without a locus": (
        lambda ps: ps.pedigrees[0].supports[1].ClearField("loci"),
        r"names loci iff the field is genotype\.\* or locus\.\*",
    ),
    "locus id on another field": (
        _set_field(lambda ps: ps.pedigrees[0].supports[1], "field", "proband"),
        r"names loci iff",
    ),
    "locus support scoped to people": (
        lambda ps: ps.pedigrees[0].supports[0].individuals.add(generation=2, index=1),
        r"describes the declaration, so it scopes the whole pedigree",
    ),
    "genotype support where no one in scope has one": (
        lambda ps: ps.pedigrees[0].supports[1].individuals[0].MergeFrom(pb.Position(generation=1, index=1)),
        r"no one in scope has a genotype at locus 'l2'",
    ),
}


@pytest.mark.parametrize("case", sorted(_BROKEN))
def test_the_loader_rejects(case: str) -> None:
    edit, match = _BROKEN[case]
    ps = _set()
    edit(ps)
    with pytest.raises(ir.IntegrityError, match=match):
        ir.validate_set(ps)


def test_a_bare_pedigree_with_genotypes_needs_its_loci() -> None:
    ps = _set()
    p = ps.pedigrees[0]
    del p.supports[:]
    with pytest.raises(ir.IntegrityError, match=r"locus_id 'l1' is not declared"):
        ir.validate(p)
    ir.validate(p, loci=ps.loci)
    assert ir.load_pbtxt(ir.dump_pbtxt(p), loci=ps.loci) == p
    with pytest.raises(ir.IntegrityError, match=r"is not declared"):
        render.render_svg(p)
    render.render_svg(p, loci=ps.loci)


def test_genotypes_do_not_change_the_drawing() -> None:
    # Genotypes are meaning only: the drawn label is the annotation's text, so a set draws the same without them.
    ps = _set()
    bare = _set()
    for ind in bare.pedigrees[0].individuals:
        del ind.genotypes[:]
        for a in ind.annotations:
            del a.loci[:]
    del bare.loci[:]
    del bare.pedigrees[0].supports[:]
    del bare.pedigrees[0].allele_symbols[:]
    assert render.render_set_svg(ps) == render.render_set_svg(bare)
    p = ps.pedigrees[0]
    stored = render.store_layout(bare.pedigrees[0])
    assert render.render_svg(p, loci=ps.loci, stored_layout=stored) == render.render_svg(bare.pedigrees[0])


def _genotype_mismatches(a: pb.PedigreeSet, b: pb.PedigreeSet) -> list[ir.Mismatch]:
    (pair,) = ir.diff_set(a, b).matched
    return [m for m in pair.diff.mismatches if m.field == "genotypes"]


def test_diff_ignores_ids_phase_set_names_and_unphased_order() -> None:
    b = _set()
    ids = {"l1": "x", "l2": "y"}
    alleles = {"wt": "ref", "m1": "alt1", "m2": "alt2"}
    for locus in b.loci:
        locus.id = ids[locus.id]
        for allele in locus.alleles:
            allele.id = alleles[allele.id]
    b.loci.reverse()
    for ind in b.pedigrees[0].individuals:
        for g in ind.genotypes:
            g.locus_id = ids[g.locus_id]
            g.allele_ids[:] = [alleles[x] for x in g.allele_ids]
            if g.HasField("phase_set"):
                g.phase_set = "other"
                g.allele_ids.reverse()  # every slot of the phase set swapped at once: the same phase
        for a in ind.annotations:
            a.loci[:] = [ids[x] for x in a.loci]
    b.pedigrees[0].individuals[0].genotypes[0].allele_ids.reverse()  # unphased: a multiset
    for s in b.pedigrees[0].supports:
        s.loci[:] = [ids[x] for x in s.loci]
    for row in b.pedigrees[0].allele_symbols:
        row.locus_id, row.allele_id = ids[row.locus_id], alleles[row.allele_id]
    b.pedigrees[0].allele_symbols.reverse()
    ir.validate_set(b)
    assert _genotype_mismatches(_set(), b) == []


_CHILD = "SLC26A4 c.1234A>G: M1 (c.1234A>G)|+ (reference) [a]; SLC26A4 c.2000del: + (reference)|M2 (c.2000del) [a]"


@pytest.mark.parametrize(
    ("edit", "b_value"),
    [
        (  # in cis, not trans: one genotype's slots swapped
            lambda ps: _child(ps).genotypes[1].allele_ids.reverse(),
            "SLC26A4 c.1234A>G: M1 (c.1234A>G)|+ (reference) [a]; SLC26A4 c.2000del: M2 (c.2000del)|+ (reference) [a]",
        ),
        (  # phase not stated
            lambda ps: _child(ps).genotypes[1].ClearField("phase_set"),
            "SLC26A4 c.1234A>G: M1 (c.1234A>G)|+ (reference) [a]; SLC26A4 c.2000del: + (reference)/M2 (c.2000del)",
        ),
        (  # an allele's stated meaning
            lambda ps: setattr(ps.loci[1].alleles[1], "name", "c.2001del"),
            "SLC26A4 c.1234A>G: M1 (c.1234A>G)|+ (reference) [a]; SLC26A4 c.2000del: + (reference)|M2 (c.2001del) [a]",
        ),
        (  # which allele is the reference
            lambda ps: setattr(ps.loci[0].alleles[0], "reference", False),
            "SLC26A4 c.1234A>G: M1 (c.1234A>G)|+ (unnamed) [a]; SLC26A4 c.2000del: + (reference)|M2 (c.2000del) [a]",
        ),
        (  # the symbol the pedigree prints for an allele
            _set_field(lambda ps: ps.pedigrees[0].allele_symbols[1], "symbol", "M3"),
            "SLC26A4 c.1234A>G: M3 (c.1234A>G)|+ (reference) [a]; SLC26A4 c.2000del: + (reference)|M2 (c.2000del) [a]",
        ),
        (  # no symbols recorded
            lambda ps: ps.pedigrees[0].ClearField("allele_symbols"),
            "SLC26A4 c.1234A>G: c.1234A>G|reference [a]; SLC26A4 c.2000del: reference|c.2000del [a]",
        ),
        (lambda ps: _child(ps).ClearField("genotypes"), "none"),  # untested
    ],
)
def test_diff_reports_a_different_genotype(edit: Callable[[pb.PedigreeSet], object], b_value: str) -> None:
    b = _set()
    edit(b)
    (m,) = (m for m in _genotype_mismatches(_set(), b) if m.subject == "2-1")
    assert (m.a_value, m.b_value) == (_CHILD, b_value)


def _child_genotypes(*genotypes: pb.Genotype) -> pb.PedigreeSet:
    """The sample with the child's genotypes replaced, no annotation naming loci, and no supports."""
    ps = _set()
    del _child(ps).genotypes[:]
    _child(ps).genotypes.extend(genotypes)
    for a in _child(ps).annotations:
        del a.loci[:]
    del ps.pedigrees[0].supports[:]
    ir.validate_set(ps)
    return ps


def test_diff_keeps_copies_in_a_phase_set() -> None:
    # Two copies of one haplotype are not one copy of it: homozygous at both loci vs hemizygous at both.
    diploid = _child_genotypes(
        pb.Genotype(locus_id="l1", allele_ids=["m1", "m1"], phase_set="s"),
        pb.Genotype(locus_id="l2", allele_ids=["m2", "m2"], phase_set="s"),
    )
    haploid = _child_genotypes(
        pb.Genotype(locus_id="l1", allele_ids=["m1"], phase_set="s"),
        pb.Genotype(locus_id="l2", allele_ids=["m2"], phase_set="s"),
    )
    assert [m.subject for m in _genotype_mismatches(diploid, haploid)] == ["2-1"]


def test_diff_reads_a_phase_set_of_one_genotype_as_unphased() -> None:
    alone = _child_genotypes(pb.Genotype(locus_id="l1", allele_ids=["m1", "wt"], phase_set="s"))
    unphased = _child_genotypes(pb.Genotype(locus_id="l1", allele_ids=["wt", "m1"]))
    assert _genotype_mismatches(alone, unphased) == []


def test_diff_reads_homozygous_members_of_a_phase_set_as_unphased() -> None:
    # Phase relates heterozygous genotypes: a homozygous member states none, and leaves one heterozygous genotype.
    phased = _child_genotypes(
        pb.Genotype(locus_id="l1", allele_ids=["m1", "m1"], phase_set="s"),
        pb.Genotype(locus_id="l2", allele_ids=["m2", "wt"], phase_set="s"),
    )
    unphased = _child_genotypes(
        pb.Genotype(locus_id="l1", allele_ids=["m1", "m1"]),
        pb.Genotype(locus_id="l2", allele_ids=["wt", "m2"]),
    )
    assert _genotype_mismatches(phased, unphased) == []


def test_unnamed_loci_may_share_a_gene() -> None:
    # The legend case, one variant per locus, with only the alleles named.
    ps = _set()
    for locus in ps.loci:
        locus.name = ""
    ir.validate_set(ps)
    other = _set()
    for locus in other.loci:
        locus.name = ""
    other.loci[1].alleles[1].name = "c.2001del"
    (m,) = (m for m in _genotype_mismatches(ps, other) if m.subject == "2-1")
    assert "c.2001del" in m.b_value


@pytest.mark.parametrize("unnamed", [False, True])
def test_loci_of_one_identity_are_one_locus_across_sets(unnamed: bool) -> None:
    # The promise a caller merging sets relies on: equal identities are one locus — diff_set matches them whatever
    # their ids — and one set may not declare it twice.
    a, b = _set(), _set()
    for ps in (a, b):
        for locus in ps.loci:
            locus.name = "" if unnamed else locus.name
    b.loci[0].id = "renamed"
    for ind in b.pedigrees[0].individuals:
        for g in ind.genotypes:
            g.locus_id = "renamed" if g.locus_id == "l1" else g.locus_id
        for ann in ind.annotations:
            ann.loci[:] = ["renamed" if x == "l1" else x for x in ann.loci]
    for s in b.pedigrees[0].supports:
        s.loci[:] = ["renamed" if x == "l1" else x for x in s.loci]
    for row in b.pedigrees[0].allele_symbols:
        row.locus_id = "renamed" if row.locus_id == "l1" else row.locus_id
    ir.validate_set(b)
    assert ir.locus_identity(a.loci[0]) == ir.locus_identity(b.loci[0])
    assert ir.locus_identity(a.loci[0]) != ir.locus_identity(a.loci[1])
    assert _genotype_mismatches(a, b) == []
    a.loci.add().CopyFrom(b.loci[0])
    with pytest.raises(ir.IntegrityError, match="is already declared as 'l1'"):
        ir.validate_set(a)


def test_an_allele_named_reference_is_not_the_reference() -> None:
    # Display text can coincide ("reference" both ways); what is stated does not.
    named = pb.Allele(id="wt", name="reference")
    flagged = pb.Allele(id="wt", reference=True)
    assert ir.allele_text(named) == ir.allele_text(flagged)
    assert ir.allele_identity(named) != ir.allele_identity(flagged)
    a, b = _set(), _set()
    for ps, plus in ((a, named), (b, flagged)):
        for locus in ps.loci:
            locus.name = ""
            locus.alleles[0].CopyFrom(plus)
    assert ir.locus_identity(a.loci[0]) != ir.locus_identity(b.loci[0])
    assert [m.subject for m in _genotype_mismatches(a, b)] == ["1-1", "1-2", "2-1"]


def _two_figures() -> pb.PedigreeSet:
    """The sample and a second family, whose legend prints ``WT`` for the reference and ``M1`` for c.1300T>C."""
    ps = _set()
    ps.loci[0].alleles.add(id="m3", name="c.1300T>C")
    ps.pedigrees[0].labels.add(text="Family 1", kind=pb.LABEL_KIND_FAMILY)
    p = ps.pedigrees.add()
    p.CopyFrom(ps.pedigrees[0])
    p.labels[0].text = "Family 2"
    del p.supports[:]
    for ind in p.individuals:
        for g in ind.genotypes:
            g.allele_ids[:] = ["m3" if x == "m1" else x for x in g.allele_ids]
    for row in p.allele_symbols:
        row.allele_id = "m3" if row.allele_id == "m1" else row.allele_id
        row.symbol = "WT" if row.symbol == "+" else row.symbol
    return ps


def test_symbols_are_scoped_to_their_pedigree() -> None:
    # "M1" is c.1234A>G in one family and c.1300T>C in the other, and the reference is "+" in one and "WT" in the
    # other: one locus in one set, each family printing it as its legend does.
    ir.validate_set(_two_figures())


def test_unnamed_loci_told_apart_by_their_symbols_are_valid() -> None:
    # "M1/+" and "M2/+" beside one person, no legend: two loci, phase not stated. One locus [M1, M2] would state trans.
    ps = _set()
    _unnamed_loci(ps)
    ir.validate_set(ps)
    assert not any(ir.locus_is_matchable(locus) for locus in ps.loci)


def _one_locus_two_unnamed(child: list[str]) -> pb.PedigreeSet:
    """One unnamed locus, the reference and two unnamed alleles printed ``M1`` and ``M2``; one person at ``child``."""
    ps = text_format.Parse(
        """
        loci { id: "l1" gene: "SLC26A4" alleles { id: "wt" reference: true } alleles { id: "m1" } alleles { id: "m2" } }
        pedigrees {
          allele_symbols { locus_id: "l1" allele_id: "m1" symbol: "M1" }
          allele_symbols { locus_id: "l1" allele_id: "m2" symbol: "M2" }
          individuals { generation: 1 index: 1 gender: GENDER_MAN genotypes { locus_id: "l1" } }
        }
        """,
        pb.PedigreeSet(),
    )
    ps.pedigrees[0].individuals[0].genotypes[0].allele_ids[:] = child
    ir.validate_set(ps)
    return ps


def test_unnamed_alleles_of_one_locus_are_told_apart_by_their_symbols() -> None:
    heterozygous, homozygous = _one_locus_two_unnamed(["m1", "m2"]), _one_locus_two_unnamed(["m2", "m2"])
    assert [m.subject for m in _genotype_mismatches(heterozygous, homozygous)] == ["1-1"]


def test_the_reference_is_one_allele_whatever_each_record_names_it() -> None:
    # One legend says "+ = wild type", another prints "WT" and names nothing: a caller merging them has one reference.
    named = pb.Allele(id="a", name="wild type", reference=True)
    unnamed = pb.Allele(id="b", reference=True)
    assert ir.allele_identity(named) == ir.allele_identity(unnamed)
    assert ir.allele_identity(named) != ir.allele_identity(pb.Allele(id="c", name="wild type"))


def test_a_pedigree_may_print_a_symbol_for_an_allele_no_one_in_it_carries() -> None:
    # A legend shared by a figure's panels names every allele, whichever panel carries it.
    ps = _set()
    ps.loci[0].alleles.add(id="m3", name="c.1300T>C")
    ps.pedigrees[0].allele_symbols.add(locus_id="l1", allele_id="m3", symbol="M3")
    ir.validate_set(ps)


def test_symbols_are_optional() -> None:
    # An imported record prints no figure, so it records no symbols.
    ps = _set()
    ps.pedigrees[0].ClearField("allele_symbols")
    ir.validate_set(ps)


def test_diff_reads_a_copy_through_its_own_pedigree_s_symbols() -> None:
    a, b = _two_figures(), _two_figures()
    assert all(not pair.diff.mismatches for pair in ir.diff_set(a, b).matched)
    b.pedigrees[1].allele_symbols[0].symbol = "+"  # the second family's reference, printed as the first prints it
    ir.validate_set(b)
    (pair,) = (pair for pair in ir.diff_set(a, b).matched if pair.diff.mismatches)
    assert pair.a_index == 1
    assert {m.field for m in pair.diff.mismatches} == {"genotypes"}
