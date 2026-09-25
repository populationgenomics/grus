"""Shuffle invariance: the reordering keeps offspring (birth) order, and the renderer does not vary."""

import pathlib

from grus.models import pedigree_pb2 as pb
from tools.fuzz import corpus, gen, probe, shuffle, trees

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _key(pos: pb.Position | pb.Individual) -> tuple[int, int]:
    return (pos.generation, pos.index)


def test_shuffled_reorders_individuals_and_matings_but_not_offspring() -> None:
    p = gen.gen(7, 4)
    q = probe.shuffled(p, 0)
    assert [_key(i) for i in q.individuals] != [_key(i) for i in p.individuals]
    assert sorted(_key(i) for i in q.individuals) == sorted(_key(i) for i in p.individuals)
    sibships = {_key(m.partner_a) + _key(m.partner_b): [_key(o.child) for o in m.offspring] for m in p.matings}
    assert {_key(m.partner_a) + _key(m.partner_b): [_key(o.child) for o in m.offspring] for m in q.matings} == sibships


def test_the_renderer_does_not_vary_on_a_tiny_corpus() -> None:
    with trees.materialised([str(_REPO)]) as (t,):
        results = shuffle.run(t, corpus.build(["4:0-3"], []), shuffles=2, jobs=2, highs=False)
    assert all(len(outs) == 3 for outs in results.values())
    assert shuffle.varying(results) == []
