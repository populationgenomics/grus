"""The minimiser shrinks to a small pedigree that still fails, whatever the batch size."""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Sequence

import pytest

from grus import ir
from grus.models import pedigree_pb2 as pb
from tools.fuzz import corpus, gen, minimize, trees, workers

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _first(pred: Callable[[pb.Pedigree], bool]) -> minimize.Search:
    def search(ps: Sequence[pb.Pedigree]) -> int | None:
        return next((i for i, p in enumerate(ps) if pred(p)), None)

    return search


def _consanguineous(p: pb.Pedigree) -> bool:
    return any(m.consanguineous for m in p.matings)


@pytest.mark.parametrize("batch", [1, 7])
def test_shrinks_a_cousin_marriage_to_its_minimal_family(batch: int) -> None:
    p = gen.gen(3, 4)
    assert len(p.individuals) == 19 and _consanguineous(p)
    q, renumbered = minimize.minimize(p, _first(_consanguineous), batch=batch)
    ir.validate(q)
    assert renumbered
    # Founders, two children each marrying in, and their two children marrying each other: first cousins.
    assert [(i.generation, i.index) for i in q.individuals] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
        (2, 3),
        (2, 4),
        (3, 1),
        (3, 2),
    ]
    assert [m.consanguineous for m in q.matings] == [False, False, False, True]


def test_an_input_that_does_not_fail_is_refused() -> None:
    with pytest.raises(ValueError, match="does not fail"):
        minimize.minimize(gen.gen(0, 4), _first(_consanguineous))


def test_a_tree_against_itself_never_differs_in_bytes() -> None:
    with (
        trees.materialised([str(_REPO), str(_REPO)]) as (ta, tb),
        workers.pool(ta, 1) as pa,
        workers.pool(tb, 1) as pb_,
    ):
        search = minimize.tree_search("bytes", [pa, pb_], reason="", shuffles=0, highs=False)
        assert search([gen.gen(0, 4)]) is None


def test_a_probe_reads_entries_through_the_declarations_it_is_given() -> None:
    from tools.fuzz import probe

    p = gen.gen(3, 4)
    p.individuals[0].marks.add(key_id="k1", status=pb.CONDITION_STATUS_AFFECTED)
    defs = pb.PedigreeSet(key_entries=[pb.KeyEntry(id="k1", text="CF", phenotype=pb.Phenotype())]).SerializeToString()
    assert probe.outcome_of(p.SerializeToString(), False).startswith("invalid: ")
    assert not probe.outcome_of(p.SerializeToString(), False, defs).startswith(("invalid", "defer"))
    outs = probe.shuffle_outcomes_of(p.SerializeToString(), 2, False, defs)
    assert len(outs) == 3 and len(set(outs)) == 1


def test_a_probe_reads_genotypes_through_the_loci_it_is_given(tmp_path: pathlib.Path) -> None:
    from tools.fuzz import corpus, probe

    p = gen.gen(3, 4)
    p.individuals[0].genotypes.add(locus_id="l1", alleles=["A", "A"])
    locus = pb.Locus(id="l1", name="rs1", alleles=[pb.Allele(symbol="A")])
    assert probe.outcome_of(p.SerializeToString(), False).startswith("invalid: ")
    defs = pb.PedigreeSet(loci=[locus]).SerializeToString()
    assert not probe.outcome_of(p.SerializeToString(), False, defs).startswith(("invalid", "defer"))
    path = tmp_path / "set.pbtxt"
    path.write_text(ir.dump_set_pbtxt(pb.PedigreeSet(pedigrees=[p], loci=[locus])))
    probe.measure(corpus.FileCase(str(path), 0), False)


def test_a_member_carries_only_the_key_entries_it_shows() -> None:
    # A genotype entry at a locus the member is genotyped at, which only another member's genotype matches, would make
    # the member's one-pedigree set invalid: a set may not declare an entry no one shows.
    locus = pb.Locus(id="l1", alleles=[pb.Allele(symbol="M"), pb.Allele(symbol="+", reference=True)])
    hom = pb.KeyEntry(id="hom", text="homozygous M", genotype=pb.GenotypePattern(locus_id="l1", alleles=["M", "M"]))
    het, homozygous = (
        pb.Pedigree(
            individuals=[
                pb.Individual(
                    generation=1, index=1, gender=pb.GENDER_MAN, genotypes=[pb.Genotype(locus_id="l1", alleles=alleles)]
                )
            ]
        )
        for alleles in (["M", "+"], ["M", "M"])
    )
    ps = pb.PedigreeSet(loci=[locus], key_entries=[hom], pedigrees=[het, homozygous])
    ir.validate_set(ps)
    for member, shown in ((het, []), (homozygous, ["hom"])):
        out = corpus.referenced_declarations(member, ps)
        assert [e.id for e in out.key_entries] == shown
        out.pedigrees.append(member)
        ir.validate_set(out)
