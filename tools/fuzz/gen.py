"""Seeded random pedigree generator for renderer fuzz differentials.

``gen(seed, maxgen)`` grows a pedigree top-down from one founder couple: each mating has 0-5 child slots (12% chance
per slot of a twin pair, which takes two slots and may overrun the last), each born-in individual marries with
probability 0.7, 30% of those marry a cousin in their generation rather than a married-in spouse, and 15% take a
second spouse (half-siblings). It makes no lone parents, counts, ghosts or annotations.

The output is a pure function of ``(seed, maxgen)`` and is pinned by tests: a change here changes every corpus, so the
same seed no longer names the same pedigree across revisions.
"""

from __future__ import annotations

import random

from grus.models import pedigree_pb2 as pb

Pos = tuple[int, int]
_GENDERS = [pb.GENDER_MAN, pb.GENDER_WOMAN]


def gen(seed: int, maxgen: int = 4) -> pb.Pedigree:
    """The fuzz pedigree for ``seed``, at most ``maxgen`` generations deep, id ``f{seed}``."""
    rng = random.Random(seed)
    counters: dict[int, int] = {}
    inds: dict[Pos, pb.Gender] = {}
    born: dict[Pos, int] = {}  # position -> index of the parents' mating

    def new(level: int, gender: pb.Gender | None = None) -> Pos:
        counters[level] = counters.get(level, 0) + 1
        pos = (level, counters[level])
        inds[pos] = gender or rng.choice(_GENDERS)
        return pos

    def opposite(g: pb.Gender) -> pb.Gender:
        return pb.GENDER_WOMAN if g == pb.GENDER_MAN else pb.GENDER_MAN

    # (partner a, partner b, children as (position, twin group or None), consanguineous)
    matings: list[tuple[Pos, Pos, list[tuple[Pos, int | None]], bool]] = []
    a = new(1, pb.GENDER_MAN)
    b = new(1, pb.GENDER_WOMAN)
    matings.append((a, b, [], False))
    frontier = [0]
    for level in range(1, maxgen):
        nextgen_people: list[Pos] = []
        for mi in frontier:
            kids = matings[mi][2]
            nk = rng.choice([0, 1, 1, 2, 2, 3, 3, 4, 5])
            tg = 0
            k = 0
            while k < nk:
                if rng.random() < 0.12:
                    tg += 1
                    t = new(level + 1)
                    u = new(level + 1)
                    kids += [(t, tg), (u, tg)]
                    nextgen_people += [t, u]
                    born[t] = mi
                    born[u] = mi
                    k += 2
                else:
                    c = new(level + 1)
                    kids.append((c, None))
                    nextgen_people.append(c)
                    born[c] = mi
                    k += 1
        frontier = []
        if level + 1 >= maxgen:
            break
        rng.shuffle(nextgen_people)
        unmarried = set(nextgen_people)
        for p in nextgen_people:
            if p not in unmarried or rng.random() < 0.3:
                continue
            unmarried.discard(p)
            cands = [q for q in unmarried if born.get(q) != born.get(p) and inds[q] != inds[p]]
            if cands and rng.random() < 0.3:
                q = rng.choice(sorted(cands))
                unmarried.discard(q)
                cons = True
            else:
                q = new(level + 1, opposite(inds[p]))
                cons = False
            matings.append((p, q, [], cons))
            frontier.append(len(matings) - 1)
            if rng.random() < 0.15:  # a second spouse: half-siblings
                q2 = new(level + 1, opposite(inds[p]))
                matings.append((p, q2, [], False))
                frontier.append(len(matings) - 1)
    ped = pb.Pedigree(id=f"f{seed}")
    for (g, i), gd in sorted(inds.items()):
        ped.individuals.add(generation=g, index=i, gender=gd)
    for a, b, kids, cons in matings:
        m = ped.matings.add(consanguineous=cons)
        m.partner_a.generation, m.partner_a.index = a
        m.partner_b.generation, m.partner_b.index = b
        for c, t in kids:
            o = m.offspring.add()
            o.child.generation, o.child.index = c
            if t is not None:
                o.twin_group = t
                o.twin_type = pb.ZYGOSITY_TYPE_DIZYGOTIC
    return ped
