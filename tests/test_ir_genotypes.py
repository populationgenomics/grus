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
# it shows, and the legend cited for the alleles.
_SET = """
citations { id: "legend" quote: "M1, c.1234A>G; M2, c.2000del" }
loci {
  id: "l1" name: "c.1234A>G" gene: "SLC26A4"
  alleles { symbol: "+" reference: true }
  alleles { symbol: "M1" name: "c.1234A>G" identifiers { scheme: IDENTIFIER_SCHEME_HGVS value: "c.1234A>G" } }
}
loci {
  id: "l2" name: "c.2000del" gene: "SLC26A4"
  alleles { symbol: "+" reference: true }
  alleles { symbol: "M2" name: "c.2000del" }
}
pedigrees {
  individuals {
    generation: 1 index: 1 gender: GENDER_MAN
    annotations { text: "M1/+" type: ANNOTATION_TYPE_GENOTYPE loci: "l1" }
    genotypes { locus_id: "l1" alleles: "M1" alleles: "+" }
  }
  individuals {
    generation: 1 index: 2 gender: GENDER_WOMAN
    annotations { text: "M2/+" type: ANNOTATION_TYPE_GENOTYPE loci: "l2" }
    genotypes { locus_id: "l2" alleles: "M2" alleles: "+" }
  }
  individuals {
    generation: 2 index: 1 gender: GENDER_MAN proband: true
    annotations { text: "M1/M2" type: ANNOTATION_TYPE_GENOTYPE loci: "l1" loci: "l2" }
    genotypes { locus_id: "l1" alleles: "M1" alleles: "+" phase_set: "a" }
    genotypes { locus_id: "l2" alleles: "+" alleles: "M2" phase_set: "a" }
  }
  matings {
    partner_a { generation: 1 index: 1 }
    partner_b { generation: 1 index: 2 }
    offspring { child { generation: 2 index: 1 } }
  }
  supports { citations: "legend" field: "locus.alleles" loci: "l1" loci: "l2" }
  supports { citations: "legend" field: "genotype.alleles" loci: "l2" individuals { generation: 2 index: 1 } }
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
    del _child(ps).genotypes[0].alleles[1:]  # [M1]: one copy, on a single-copy locus
    _child(ps).genotypes[1].alleles[:] = ["M2", "M2"]
    ir.validate_set(ps)


def _set_field(path: Callable[[pb.PedigreeSet], object], name: str, value: object) -> Callable[[pb.PedigreeSet], None]:
    return lambda ps: setattr(path(ps), name, value)


# One edit per loader invariant, each breaking a valid set: (edit, the message the failure names).
_BROKEN: dict[str, tuple[Callable[[pb.PedigreeSet], object], str]] = {
    "undeclared locus": (_set_field(lambda ps: _child(ps).genotypes[0], "locus_id", "l9"), r"'l9' is not declared"),
    "allele not a symbol": (
        lambda ps: _child(ps).genotypes[0].alleles.__setitem__(0, "M2"),
        r"allele 'M2' at locus 'l1' is not one of its symbols",
    ),
    "two genotypes at one locus": (
        lambda ps: _child(ps).genotypes.add(locus_id="l1", alleles=["+", "+"]),
        r"two genotypes at locus 'l1'",
    ),
    "three alleles": (lambda ps: _child(ps).genotypes[0].alleles.append("+"), r"not a valid pedigree"),
    "no alleles": (lambda ps: _child(ps).genotypes[0].ClearField("alleles"), r"not a valid pedigree"),
    "phase set of unequal ploidy": (
        lambda ps: _child(ps).genotypes[1].alleles.pop(),
        r"phase set 'a' mixes genotypes of 2 and 1 alleles",
    ),
    "locus declared twice": (lambda ps: ps.loci.append(ps.loci[0]), r"locus id 'l1' is declared twice"),
    "locus name declared twice": (
        lambda ps: setattr(ps.loci[1], "name", "c.1234A>G"),
        r"is already declared as 'l1'",
    ),
    "locus id is a citation id": (
        lambda ps: ps.citations.add(id="l2", quote="x"),
        r"locus id 'l2' is also a citation or condition id",
    ),
    "allele symbol twice": (lambda ps: ps.loci[0].alleles.add(symbol="M1"), r"allele symbol\(s\) \['M1'\]"),
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
        lambda ps: ps.loci.add(id="l3", name="D12S1640", alleles=[pb.Allele(symbol="1")]),
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
    for locus in b.loci:
        locus.id = ids[locus.id]
    b.loci.reverse()
    for ind in b.pedigrees[0].individuals:
        for g in ind.genotypes:
            g.locus_id = ids[g.locus_id]
            if g.HasField("phase_set"):
                g.phase_set = "other"
                g.alleles.reverse()  # every slot of the phase set swapped at once: the same phase
        for a in ind.annotations:
            a.loci[:] = [ids[x] for x in a.loci]
    b.pedigrees[0].individuals[0].genotypes[0].alleles.reverse()  # unphased: a multiset
    for s in b.pedigrees[0].supports:
        s.loci[:] = [ids[x] for x in s.loci]
    ir.validate_set(b)
    assert _genotype_mismatches(_set(), b) == []


_CHILD = "SLC26A4 c.1234A>G: M1 (c.1234A>G)|+ (reference) [a]; SLC26A4 c.2000del: + (reference)|M2 (c.2000del) [a]"


@pytest.mark.parametrize(
    ("edit", "b_value"),
    [
        (  # in cis, not trans: one genotype's slots swapped
            lambda ps: _child(ps).genotypes[1].alleles.reverse(),
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
            "SLC26A4 c.1234A>G: M1 (c.1234A>G)|+ [a]; SLC26A4 c.2000del: + (reference)|M2 (c.2000del) [a]",
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
        pb.Genotype(locus_id="l1", alleles=["M1", "M1"], phase_set="s"),
        pb.Genotype(locus_id="l2", alleles=["M2", "M2"], phase_set="s"),
    )
    haploid = _child_genotypes(
        pb.Genotype(locus_id="l1", alleles=["M1"], phase_set="s"),
        pb.Genotype(locus_id="l2", alleles=["M2"], phase_set="s"),
    )
    assert [m.subject for m in _genotype_mismatches(diploid, haploid)] == ["2-1"]


def test_diff_reads_a_phase_set_of_one_genotype_as_unphased() -> None:
    alone = _child_genotypes(pb.Genotype(locus_id="l1", alleles=["M1", "+"], phase_set="s"))
    unphased = _child_genotypes(pb.Genotype(locus_id="l1", alleles=["+", "M1"]))
    assert _genotype_mismatches(alone, unphased) == []


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
