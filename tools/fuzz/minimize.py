"""Greedy minimiser: shrink a pedigree while a failure predicate still holds.

Each step tries every one-move shrink of the current pedigree and takes the first, in a fixed order, that still fails:

- drop a leaf child (a child who is nobody's partner) from its sibship;
- drop a mating together with all its descendants.

Each move is followed by pruning what it orphaned (a mating left with neither children nor two partners, a founder
sibship left with fewer than two children, an individual in no mating). It stops when no move keeps the failure, then
renumbers each generation 1..n in the original order, so the result reads as a figure would.

Predicates run in worker pools on the trees they name, so a pedigree can be minimised against a baseline and a branch
at once. A candidate that is not valid IR never counts as failing.
"""

from __future__ import annotations

import multiprocessing.pool
from collections.abc import Callable, Iterator, Sequence

from grus.models import pedigree_pb2 as pb
from tools.fuzz import probe

Key = tuple[int, int]
# Given candidates, the index of the first that fails, or None.
Search = Callable[[Sequence[pb.Pedigree]], int | None]

PREDICATES = {
    "defers": "the pedigree defers on TREE",
    "regress": "draws on TREE, defers on TREE_B",
    "bytes": "draws on both trees with different SVG bytes",
    "shuffle": "the drawing on TREE varies under shuffles of individuals and matings",
}


def _key(p: pb.Position | pb.Individual) -> Key:
    return (p.generation, p.index)


def _partners(m: pb.Mating) -> set[Key]:
    return {_key(x) for x in (m.partner_a, m.partner_b) if x.generation}


def prune(p: pb.Pedigree) -> pb.Pedigree:
    """``p`` without matings or individuals left purposeless, repeated to a fixed point."""
    q = pb.Pedigree()
    q.CopyFrom(p)
    changed = True
    while changed:
        changed = False
        keep = []
        for m in q.matings:
            couple = m.HasField("partner_a") and m.HasField("partner_b")
            if (not m.offspring and not couple) or (not m.HasField("partner_a") and len(m.offspring) < 2):
                changed = True
                continue
            keep.append(m)
        del q.matings[:]
        q.matings.extend(keep)
        used = {k for m in q.matings for k in _partners(m)} | {_key(o.child) for m in q.matings for o in m.offspring}
        inds = [i for i in q.individuals if _key(i) in used]
        if len(inds) != len(q.individuals):
            changed = True
            del q.individuals[:]
            q.individuals.extend(inds)
    return q


def candidates(p: pb.Pedigree) -> Iterator[pb.Pedigree]:
    """Every one-move shrink of ``p``, pruned: leaf children first, then matings with their descendants."""
    partners = {k for m in p.matings for k in _partners(m)}
    for mi, m in enumerate(p.matings):
        for oi, o in enumerate(m.offspring):
            if _key(o.child) in partners:
                continue
            q = pb.Pedigree()
            q.CopyFrom(p)
            del q.matings[mi].offspring[oi]
            q.individuals.remove(next(i for i in q.individuals if _key(i) == _key(o.child)))
            yield prune(q)
    children_of = {mi: [_key(o.child) for o in m.offspring] for mi, m in enumerate(p.matings)}
    for mi in range(len(p.matings)):
        drop: set[Key] = set()
        stack = list(children_of[mi])
        while stack:
            c = stack.pop()
            if c in drop:
                continue
            drop.add(c)
            for mj, m in enumerate(p.matings):
                if c in _partners(m):
                    stack += children_of[mj]
        q = pb.Pedigree()
        q.CopyFrom(p)
        q.matings.pop(mi)
        keep = [m for m in q.matings if not (_partners(m) & drop)]
        del q.matings[:]
        q.matings.extend(keep)
        for m in q.matings:
            kids = [o for o in m.offspring if _key(o.child) not in drop]
            del m.offspring[:]
            m.offspring.extend(kids)
        inds = [i for i in q.individuals if _key(i) not in drop]
        del q.individuals[:]
        q.individuals.extend(inds)
        yield prune(q)


def renumber(p: pb.Pedigree) -> pb.Pedigree:
    """``p`` with each generation's indices renumbered 1..n in their original order."""
    by_gen: dict[int, list[pb.Individual]] = {}
    for i in p.individuals:
        by_gen.setdefault(i.generation, []).append(i)
    new = {_key(i): k + 1 for g in by_gen.values() for k, i in enumerate(sorted(g, key=lambda i: i.index))}
    q = pb.Pedigree()
    q.CopyFrom(p)
    for i in q.individuals:
        i.index = new[_key(i)]
    for m in q.matings:
        for x in (m.partner_a, m.partner_b):
            if x.generation:
                x.index = new[_key(x)]
        for o in m.offspring:
            o.child.index = new[_key(o.child)]
    return q


def minimize(p: pb.Pedigree, search: Search, batch: int = 1) -> tuple[pb.Pedigree, bool]:
    """Shrink ``p`` while ``search`` finds it failing, trying candidates ``batch`` at a time.

    The result does not depend on ``batch``: within a batch the first failing candidate is taken.

    Returns:
        The minimised pedigree, and whether it is renumbered: it is not when renumbering loses the failure (a
        predicate sensitive to indices), which is then returned as found.

    Raises:
        ValueError: ``p`` does not fail.
    """
    if search([p]) is None:
        raise ValueError("the input does not fail the predicate")
    while True:
        cands = [q for q in candidates(p) if len(q.individuals) < len(p.individuals)]
        found = None
        for lo in range(0, len(cands), batch):
            j = search(cands[lo : lo + batch])
            if j is not None:
                found = cands[lo + j]
                break
        if found is None:
            break
        p = found
    r = renumber(p)
    if search([r]) is None:
        return p, False
    return r, True


def tree_search(
    predicate: str, pools: Sequence[multiprocessing.pool.Pool], reason: str, shuffles: int, highs: bool
) -> Search:
    """A ``Search`` for one of ``PREDICATES``, evaluated in ``pools`` (one per tree it names).

    ``reason``, when not empty, narrows a deferral to one whose message contains it.

    Raises:
        ValueError: an unknown predicate, or the wrong number of trees for it.
    """
    two = predicate in ("regress", "bytes")
    if predicate not in PREDICATES:
        raise ValueError(f"unknown predicate {predicate!r}; one of {', '.join(PREDICATES)}")
    if len(pools) != (2 if two else 1):
        raise ValueError(f"predicate {predicate!r} takes {2 if two else 1} tree(s)")

    def defers(o: str) -> bool:
        return o.startswith("defer: ") and reason in o

    def draws(o: str) -> bool:
        return not o.startswith(("defer: ", "invalid: "))

    def search(ps: Sequence[pb.Pedigree]) -> int | None:
        data = [q.SerializeToString() for q in ps]
        if predicate == "shuffle":
            outs = pools[0].starmap(probe.shuffle_outcomes_of, [(d, shuffles, highs) for d in data])
            hits = [len(set(o)) > 1 for o in outs]
        else:
            args = [(d, highs) for d in data]
            a = pools[0].starmap(probe.outcome_of, args)
            if predicate == "defers":
                hits = [defers(o) for o in a]
            else:
                b = pools[1].starmap(probe.outcome_of, args)
                if predicate == "regress":
                    hits = [draws(x) and defers(y) for x, y in zip(a, b, strict=True)]
                else:
                    hits = [draws(x) and draws(y) and x != y for x, y in zip(a, b, strict=True)]
        return next((i for i, h in enumerate(hits) if h), None)

    return search
