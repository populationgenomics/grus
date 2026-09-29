"""The minimiser shrinks to a small pedigree that still fails, whatever the batch size."""

import pathlib
from collections.abc import Callable, Sequence

import pytest

from grus import ir
from grus.models import pedigree_pb2 as pb
from tools.fuzz import gen, minimize, trees, workers

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
    p.individuals[0].conditions.add(condition_id="k1", status=pb.CONDITION_STATUS_AFFECTED)
    defs = pb.PedigreeSet(conditions=[pb.ConditionDef(id="k1", name="CF")]).SerializeToString()
    assert probe.outcome_of(p.SerializeToString(), False).startswith("invalid: ")
    assert not probe.outcome_of(p.SerializeToString(), False, defs).startswith(("invalid", "defer"))
    outs = probe.shuffle_outcomes_of(p.SerializeToString(), 2, False, defs)
    assert len(outs) == 3 and len(set(outs)) == 1
