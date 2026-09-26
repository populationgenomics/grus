"""Layout v2, Stage B: within-rank ordering (crossing minimization).

Stage A borrowed v1's within-row column order and only recomputed x. Stage B replaces that borrow with a
native deterministic ordering: it builds a **bipartite half-rank graph** from v1's ``_Graph`` (individuals on
integer ranks, one mating/sibship connector node per drawn mating on the half-rank between its partners and
its children — the standard Sugiyama hyperedge move that makes "a family" a first-class orderable object),
then minimises weighted edge crossings with dot's ``mincross`` (``init_order`` birth-order DFS, ``wmedian``
with dot's ``median_value``, strict-improvement ``transpose``) counting crossings with the Barth-Jünger-Mutzel
accumulator bilayer counter, and a tiny exact search on small ranks. Output is one left->right individual
order per generation rank; Stage A's x-solver turns it into coordinates.

Couple adjacency and twin grouping are STRUCTURE, not soft penalties: each is an **adjacency atom** (a path of
individuals kept contiguous on their rank, orientation free; a partner of either twin may stand between two co-twins,
``_Model._twin_chains``). A same-generation cross-marriage (a
boundary-bridge join) is an ordinary couple atom whose two members belong to different families, so ordering
alone brings the two sibships' ends together — no special placement pass. Sibship contiguity mostly emerges (the
children of one mating share a connector, so crossing-minimisation clusters them), and the best order the mincross
loop visits is chosen with torn sibships ranked first, so an untorn order it reaches is never lost to one with fewer
crossings (the exact search ranks crossings first; ``_exact_ranks`` says why). The moves themselves are priced by
crossings: pricing them by tears first traded crossings without limit (a tear-free order with dozens of crossings)
and cut searches short on shapes that were already clean.
An individual needing more than two 1-D neighbours (>2 matings, or a twin whose sides are taken) overflows: the
highest-weight matings stay adjacent, the rest are **routed** (recorded in ``Ordering.routed``; the layout defers a
pedigree with any). Determinism is bit-exact: no RNG, sorted integer/identity iteration, a fixed pass count, ``best``
replaced only on strict improvement, and a four-level IR-derived tie-break whose ultimate backstop is the
individuals' stable ``(generation, index)`` identity (NOT their post-shuffle row index — so the order is
invariant to a shuffle of the input, the strongest determinism guarantee).

Design: docs/design/layout-v2.md; plan: docs/plans/29 (Stage B).
"""

from __future__ import annotations

import dataclasses
import itertools
from collections import defaultdict

from grus.render import _layout

_MAX_ITERS = 24  # dot's mincross pass budget; the contraction settles far inside this on drawable pedigrees
_EXACT_ATOM_CAP = 7  # a rank with <= this many atoms is ordered by exact enumeration, not the median heuristic


@dataclasses.dataclass(frozen=True)
class Ordering:
    """The Stage-B result: per integer rank the left->right individual indices, plus the routed matings.

    Attributes:
        ranks: ``ranks[L]`` — individual indices on generation rank ``L``, left to right.
        routed: mating indices whose partners cannot stand side by side (an individual's overflow matings). Empty
            for every shape the layout draws; ``_layout2.layout`` defers on any.
    """

    ranks: list[list[int]]
    routed: frozenset[int]


# --- node identity -----------------------------------------------------------------------------------------
# A layered node is an int: an individual is its own row index (>= 0); a mating connector is ``mat_base + mi``.
# Ranks are doubled so both integer generation rows (even) and half-rank connector rows (odd) are ints: an
# individual sits at ``2 * level``; a partnered mating's connector at ``2 * partner_level + 1`` (partners on
# ``2L``, children on ``2L + 2``); a founder sibship's connector at ``2 * min_child_level - 1`` (children only).


def order(g: _layout._Graph, cross: set[int]) -> Ordering:
    """Compute the deterministic within-rank ordering for the prepared graph ``g`` (ranks + level already set).

    ``cross`` is v1's cross-lineage mating set (unused as a special case — a cross mating is an ordinary couple
    atom — but its members are known born-in). Overflow matings route; everything else orders by half-rank
    mincross.
    """
    model = _Model(g)
    routed = model.routed
    init = _init_order(model)
    order_map = _mincross(model, init)
    if _total_overlaps(model, order_map):
        # A torn order has two remedies, each a run from another start; keep whichever scores best, since a run that
        # only removes the tear can buy it with crossings another start avoids.
        # Local moves cannot reverse an atom and re-sort the rank below it together, so an atom that starts in the
        # wrong orientation (birth order, where a marriage below needs the reverse) can leave a tear the search never
        # repairs: retry once from every atom reversed.
        retry = _mincross(model, _reversed_atoms(model, init))
        if _objective(model, retry) < _objective(model, order_map):
            order_map = retry
        # A marriage between relatives whose families start apart (first cousins across a middle sibling's family)
        # needs the two lines brought to facing ends, several ranks at once. Seed a run per such marriage with the
        # two lines adjacent at the mating where they split, each line's child toward the marriage at the facing
        # end. The seeded runs skip the exact search, which is most of a run's cost on a large rank; only the best
        # seeded order is settled by it. They stop at the first tear-free seed that beats every order so far.
        best_seed: dict[int, list[int]] | None = None
        for seed in _facing_seeds(model)[:_MAX_FACING_SEEDS]:
            trial = _mincross(model, _init_order(model, seed), settle=False)
            if best_seed is None or _objective(model, trial) < _objective(model, best_seed):
                best_seed = trial
            if not _total_overlaps(model, trial) and _objective(model, trial) < _objective(model, order_map):
                break
        if best_seed is not None and _objective(model, best_seed) < _objective(model, order_map):
            settled = _settle(model, best_seed)
            if _objective(model, settled) < _objective(model, order_map):
                order_map = settled
    ranks = [order_map.get(2 * lvl, []) for lvl in range(model.nlevels)]
    return Ordering(ranks=[list(r) for r in ranks], routed=frozenset(routed))


class _Model:
    """The half-rank layered graph derived from ``g``: nodes, ranks, adjacency, atoms, and routed overflow."""

    def __init__(self, g: _layout._Graph) -> None:
        self.g = g
        self.mat_base = g.n
        self.nlevels = (max(g.level) + 1) if g.level else 0
        self.routed: set[int] = set()
        self.birth = _birth_of(g)
        self._resolve_overflow()
        self._build_atoms()  # may route more matings (a twin's partners past its sides), so before the layers
        self._build_layers()
        # Each drawn sibship's children, grouped by the rank they sit on: what sibship contiguity is measured over.
        self.sibships_on: dict[int, list[tuple[int, ...]]] = defaultdict(list)
        for mr in g.matings:
            if self._drawn(mr) and mr.kids:
                self.sibships_on[self.rank[mr.kids[0]]].append(mr.kids)

    # -- node helpers --
    def mnode(self, mi: int) -> int:
        return self.mat_base + mi

    def is_mating(self, node: int) -> bool:
        return node >= self.mat_base

    def mating_key(self, mr: _layout._Mating) -> tuple[tuple[_layout.Ident, ...], tuple[_layout.Ident, ...]]:
        """Stable IR identity of a mating: its partners' then its children's identities, never its input position.

        Every tie between matings breaks on this, so a shuffle of ``Pedigree.matings`` cannot change the order.
        """
        return (tuple(sorted(self.ident(p) for p in mr.partners)), tuple(sorted(self.ident(k) for k in mr.kids)))

    def ident(self, i: int) -> _layout.Ident:
        """Stable identity of a cell (``_Graph.ident``) — used for every tie-break so the order is shuffle-invariant."""
        return self.g.ident(i)

    # -- overflow: an individual with >2 matings keeps its two heaviest; the rest route --
    def _resolve_overflow(self) -> None:
        for i in range(self.g.n):
            ms = [mr for mr in self.g.matings_of[i] if mr.b is not None and mr.index not in self.routed]
            if len(ms) <= 2:
                continue
            # keep the two heaviest adjacencies (most offspring); route the rest, tie-broken by mating index.
            ms.sort(key=lambda mr: (-len(mr.kids), self.mating_key(mr)))
            for mr in ms[2:]:
                self.routed.add(mr.index)

    def _drawn(self, mr: _layout._Mating) -> bool:
        return mr.index not in self.routed

    # -- layers: rank of every node, and up/down adjacency across one half-rank --
    def _build_layers(self) -> None:
        g = self.g
        self.rank: dict[int, int] = {}
        self.up: dict[int, list[int]] = defaultdict(list)
        self.down: dict[int, list[int]] = defaultdict(list)
        for i in range(g.n):
            self.rank[i] = 2 * g.level[i]
        for mr in g.matings:
            if not self._drawn(mr):
                continue
            m = self.mnode(mr.index)
            if mr.partners:
                r = 2 * g.level[mr.partners[0]] + 1
            elif mr.kids:
                r = 2 * min(g.level[k] for k in mr.kids) - 1
            else:
                continue  # a partnerless childless mating draws nothing and orders nothing
            self.rank[m] = r
            for pk in mr.partners:
                self.down[pk].append(m)
                self.up[m].append(pk)
            for k in mr.kids:
                self.down[m].append(k)
                self.up[k].append(m)
        self.max_rank = max(self.rank.values(), default=-1)
        self.connectors_on: dict[int, list[int]] = defaultdict(list)
        for node, rr in sorted(self.rank.items()):
            if self.is_mating(node):
                self.connectors_on[rr].append(node)
        self.min_rank = min(self.rank.values(), default=0)  # a founder-sibship connector sits at rank -1

    # -- atoms: per even rank, the must-be-adjacent chains (couples + twin groups) --
    def _build_atoms(self) -> None:
        """Each rank's adjacency atoms: connected couples and twin groups, each laid out as one path.

        A component that is already a simple path is that path. One that is not — a twin whose partners outnumber its
        free sides, or a cycle — is arranged by ``_twin_chains``, which may stand a partner between co-twins and
        routes the matings it cannot keep adjacent.
        """
        g = self.g
        adj: dict[int, set[int]] = defaultdict(set)
        couples: dict[frozenset[int], list[_layout._Mating]] = defaultdict(list)
        groups: list[tuple[int, ...]] = []

        def link(a: int, b: int) -> None:
            adj[a].add(b)
            adj[b].add(a)

        for mr in g.matings:
            if not self._drawn(mr):
                continue
            if mr.b is not None and g.level[mr.a] == g.level[mr.b]:
                link(mr.a, mr.b)
                couples[frozenset(mr.partners)].append(mr)
            by_group: dict[int, list[int]] = defaultdict(list)
            for o in mr.offspring:
                if o.twin_group is not None:
                    by_group[o.twin_group].append(o.child)
            for kids in by_group.values():
                if len(kids) > 1:
                    groups.append(tuple(kids))
                for a, b in itertools.pairwise(kids):
                    link(a, b)

        self.atom_of: dict[int, tuple[int, ...]] = {}
        for lvl in range(self.nlevels):
            members = [i for i in range(g.n) if g.level[i] == lvl]
            seen: set[int] = set()
            for start in sorted(members, key=self.ident):
                if start in seen:
                    continue
                comp = self._component(start, adj)
                seen |= comp
                if _is_path(comp, adj):
                    atoms = [self._orient(comp, adj)]
                else:
                    atoms = self._twin_chains(comp, couples, [grp for grp in groups if grp[0] in comp])
                for atom in atoms:
                    for i in atom:
                        self.atom_of[i] = atom

    def _twin_chains(
        self,
        comp: set[int],
        couples: dict[frozenset[int], list[_layout._Mating]],
        groups: list[tuple[int, ...]],
    ) -> list[tuple[int, ...]]:
        """Lay out a component that is not a simple path as paths, routing the matings none can keep adjacent.

        layout-v2.md, Twins with partners: co-twins stay adjacent, except that one partner of either twin may stand
        between two co-twins consecutive in the group — a partner with no drawn parents, not itself a twin, not a
        phantom or a ghost. Only a gap beside a twin with more partners than free sides (an end twin has one, a middle
        twin none) is offered one. Every choice of gap partners is tried; each keeps the heaviest matings first
        (``_arrange``), and the one kept is the one routing the fewest matings with offspring, then the fewest
        matings, then with the fewest partners between twins, then the smallest identity sequence. The routed
        matings join ``self.routed``.
        """
        g = self.g
        pairs = [pair for pair in couples if pair <= comp]
        partners_of: dict[int, set[int]] = defaultdict(set)
        for pair in pairs:
            a, b = tuple(pair)
            partners_of[a].add(b)
            partners_of[b].add(a)
        in_group = {i for grp in groups for i in grp}

        def eligible(p: int) -> bool:
            return not (_layout._born_in(g, p) or p in in_group or p in g.phantom_of or p in g.ghost_key)

        def overfull(grp: tuple[int, ...], k: int) -> bool:
            free = 1 if k in (0, len(grp) - 1) else 0
            return len(partners_of[grp[k]]) > free

        gaps = [(grp, k) for grp in groups for k in range(len(grp) - 1)]
        options: list[list[int | None]] = []
        for grp, k in gaps:
            if overfull(grp, k) or overfull(grp, k + 1):
                cands = {p for t in grp[k : k + 2] for p in partners_of[t] if eligible(p)}
                options.append([None, *sorted(cands, key=self.ident)])
            else:
                options.append([None])
        best: tuple[tuple[object, ...], list[tuple[int, ...]], set[int]] | None = None
        for choice in itertools.product(*options):
            placed = [p for p in choice if p is not None]
            if len(set(placed)) != len(placed):
                continue  # one partner cannot stand in two gaps
            trial = self._arrange(comp, couples, pairs, gaps, choice)
            if best is None or trial[0] < best[0]:
                best = trial
        assert best is not None  # the all-adjacent choice is always offered
        _, atoms, routed = best
        self.routed |= routed
        return atoms

    def _arrange(
        self,
        comp: set[int],
        couples: dict[frozenset[int], list[_layout._Mating]],
        pairs: list[frozenset[int]],
        gaps: list[tuple[tuple[int, ...], int]],
        choice: tuple[int | None, ...],
    ) -> tuple[tuple[object, ...], list[tuple[int, ...]], set[int]]:
        """``comp``'s paths with ``choice[j]`` standing in gap ``j`` (``None``: the co-twins side by side).

        The twins' sequence is kept first; then each couple, heaviest first (most offspring, then identity), while
        neither partner already has two neighbours or stands in a gap and it closes no cycle. Returns the ranking key
        (``_twin_chains``), the oriented paths, and the matings routed.
        """
        edges: set[frozenset[int]] = set()
        in_gap: set[int] = set()
        for (grp, k), p in zip(gaps, choice, strict=True):
            a, b = grp[k], grp[k + 1]
            if p is None:
                edges.add(frozenset((a, b)))
            else:
                edges |= {frozenset((a, p)), frozenset((p, b))}
                in_gap.add(p)
        degree: dict[int, int] = defaultdict(int)
        root = {i: i for i in comp}

        def find(i: int) -> int:
            while root[i] != i:
                root[i] = root[root[i]]
                i = root[i]
            return i

        for e in edges:
            a, b = tuple(e)
            degree[a] += 1
            degree[b] += 1
            root[find(a)] = find(b)
        routed: set[int] = set()

        def weight(pair: frozenset[int]) -> tuple[int, tuple[object, ...]]:
            return (
                -sum(len(mr.kids) for mr in couples[pair]),
                tuple(sorted(self.mating_key(mr) for mr in couples[pair])),
            )

        for pair in sorted(pairs, key=weight):
            if pair in edges:
                continue  # the marriage of a partner standing between co-twins: its gap already makes it adjacent
            a, b = tuple(pair)
            if a in in_gap or b in in_gap or degree[a] >= 2 or degree[b] >= 2 or find(a) == find(b):
                routed |= {mr.index for mr in couples[pair]}
                continue
            edges.add(pair)
            degree[a] += 1
            degree[b] += 1
            root[find(a)] = find(b)
        adj: dict[int, set[int]] = defaultdict(set)
        for e in edges:
            a, b = tuple(e)
            adj[a].add(b)
            adj[b].add(a)
        atoms: list[tuple[int, ...]] = []
        seen: set[int] = set()
        for start in sorted(comp, key=self.ident):
            if start not in seen:
                sub = self._component(start, adj)
                seen |= sub
                atoms.append(self._orient(sub, adj))
        with_kids = sum(1 for mi in routed if self.g.matings[mi].kids)
        ident = tuple(sorted(tuple(self.ident(i) for i in atom) for atom in atoms))
        return (with_kids, len(routed), len(in_gap), ident), atoms, routed

    def _component(self, start: int, adj: dict[int, set[int]]) -> set[int]:
        comp = {start}
        frontier = [start]
        while frontier:
            i = frontier.pop()
            for j in adj[i]:
                if j not in comp:
                    comp.add(j)
                    frontier.append(j)
        return comp

    def _orient(self, comp: set[int], adj: dict[int, set[int]]) -> tuple[int, ...]:
        """Order a component into its canonical path.

        Of the path's two orientations, pick the one with fewer birth-order inversions among the siblings in it
        (a twin pair joined to one twin's spouse must not read younger-first), then the lexicographically
        smallest ``(generation, index)`` sequence, so the orientation is stable and reproduces v1's
        ``partner_a``-left default.
        """
        if len(comp) == 1:
            return (next(iter(comp)),)
        ends = sorted((i for i in comp if len(adj[i]) <= 1), key=self.ident)
        if len(ends) != 2:  # pragma: no cover - callers pass only paths (``_is_path``, ``_arrange``)
            return tuple(sorted(comp, key=self.ident))
        best: tuple[int, tuple[_layout.Ident, ...]] | None = None
        best_path: tuple[int, ...] = ()
        for end in ends:
            path = [end]
            prev = -1
            cur = end
            while True:
                nxts = [j for j in adj[cur] if j != prev]
                if not nxts:
                    break
                prev, cur = cur, nxts[0]
                path.append(cur)
            key = (_inversions(self.birth, path), tuple(self.ident(i) for i in path))
            if best is None or key < best:
                best, best_path = key, tuple(path)
        return best_path


# --- init_order: birth-order DFS from founders ------------------------------------------------------------


def _is_path(comp: set[int], adj: dict[int, set[int]]) -> bool:
    """Whether the connected ``comp`` is a simple path: no member with three neighbours, and no cycle."""
    degrees = [len(adj[i]) for i in comp]
    return max(degrees, default=0) <= 2 and sum(degrees) == 2 * (len(comp) - 1)


def _init_order(model: _Model, kid_order: dict[int, list[int]] | None = None) -> dict[int, list[int]]:
    """First-appearance order per rank from a DFS, regrouped so each atom is contiguous.

    Founders in ``(generation, index)`` order, descending into each mating's children in birth
    (``Mating.offspring``) order, or in ``kid_order[mating index]`` where given (a facing-ends seed).
    """
    override = kid_order or {}
    g = model.g
    seen_i: set[int] = set()
    seen_m: set[int] = set()
    appear: dict[int, list[int]] = defaultdict(list)

    def visit_ind(i: int) -> None:
        if i in seen_i:
            return
        seen_i.add(i)
        appear[model.rank[i]].append(i)
        for mr in sorted(g.matings_of[i], key=model.mating_key):
            if model._drawn(mr):
                visit_mat(mr)

    def visit_mat(mr: _layout._Mating) -> None:
        if mr.index in seen_m:
            return
        seen_m.add(mr.index)
        appear[model.rank[model.mnode(mr.index)]].append(model.mnode(mr.index))
        for pk in sorted(mr.partners, key=model.ident):
            visit_ind(pk)
        for k in override.get(mr.index, mr.kids):  # birth order, unless a seed reorders them
            visit_ind(k)

    for mr in sorted(g.partnerless, key=lambda mr: min((model.ident(k) for k in mr.kids), default=(0, 0, 0))):
        if model._drawn(mr):
            visit_mat(mr)
    for i in sorted(range(g.n), key=model.ident):
        visit_ind(i)

    return _regroup(model, appear)


_MAX_FACING_SEEDS = 6  # seeded runs per pedigree, each a full mincross; only while a tear remains


def _facing_seeds(model: _Model) -> list[dict[int, list[int]]]:
    """One child-order override per same-row marriage whose lines split at a mating, bringing them to facing ends.

    For partners ``x`` and ``y`` below a common couple, the nearest mating ``M`` with distinct children ``a_x``
    (``x`` or an ancestor of ``x``) and ``a_y`` (likewise for ``y``) is where the lines split. The seed puts ``a_y``
    right after ``a_x`` among ``M``'s children, and at every mating below on ``x``'s line the child toward ``x``
    last, on ``y``'s line the child toward ``y`` first, so the first-appearance order starts with the two
    families side by side and the partners at their facing ends. Seeds are in the marriages' identity order and ties
    between split matings break on identity, so neither depends on input order. Relatives only through a parent's
    two different matings (half-first-cousins) share no such ``M`` and get no seed.
    """
    g = model.g
    mating_of = {k: mr for mr in g.matings for k in mr.kids}

    def line(i: int) -> dict[int, list[int]]:
        """Each ancestor-or-self of ``i`` -> the path from it down to ``i`` (itself first)."""
        out: dict[int, list[int]] = {i: [i]}
        frontier = [i]
        while frontier:
            nxt: list[int] = []
            for c in frontier:
                mr = mating_of.get(c)
                if mr is None:
                    continue
                for p in mr.partners:
                    if p not in out:
                        out[p] = [p, *out[c]]
                        nxt.append(p)
            frontier = nxt
        return out

    seeds: list[dict[int, list[int]]] = []
    for mr in sorted(g.matings, key=model.mating_key):  # identity order, so the cap and early stop are shuffle-free
        if len(mr.partners) != 2 or not all(p in mating_of for p in mr.partners):
            continue
        x, y = sorted(mr.partners, key=model.ident)
        up_x, up_y = line(x), line(y)
        best: tuple[tuple[int, object], _layout._Mating, int, int] | None = None
        for m in g.matings:
            ax = [k for k in m.kids if k in up_x]
            ay = [k for k in m.kids if k in up_y]
            if ax and ay and ax[0] != ay[0]:
                rank = (-model.rank[m.kids[0]], model.mating_key(m))  # deepest split, then identity
                if best is None or rank < best[0]:
                    best = (rank, m, ax[0], ay[0])
        if best is None:
            continue  # not relatives: an ordinary couple
        _, top, ax, ay = best
        kids = [k for k in top.kids if k != ay]
        kids.insert(kids.index(ax) + 1, ay)
        seed = {top.index: kids}
        for path, at_end in ((up_x[ax], True), (up_y[ay], False)):
            for child in path[1:]:
                m = mating_of[child]
                rest = [k for k in m.kids if k != child]
                seed[m.index] = [*rest, child] if at_end else [child, *rest]
        seeds.append(seed)
    return seeds


def _reversed_atoms(model: _Model, order_map: dict[int, list[int]]) -> dict[int, list[int]]:
    """``order_map`` with every multi-member atom on the individuals' ranks reversed in place."""
    out = {r: list(nodes) for r, nodes in order_map.items()}
    for r in out:
        if r % 2 == 0:
            out[r] = [i for unit in _rank_units(model, out, r) for i in reversed(unit)]
    return out


def _regroup(model: _Model, appear: dict[int, list[int]]) -> dict[int, list[int]]:
    """Turn a first-appearance node list per rank into an order where each even-rank atom is contiguous.

    Odd ranks (mating connectors) pass through unchanged. On an even rank, atoms are laid out in the order of
    their first-appearing member, each expanded in its canonical orientation.
    """
    order_map: dict[int, list[int]] = {}
    for r in range(model.min_rank, model.max_rank + 1):
        nodes = appear.get(r, [])
        if r % 2 == 1:
            order_map[r] = list(nodes)
            continue
        placed: set[int] = set()
        out: list[int] = []
        for i in nodes:
            atom = model.atom_of[i]
            if atom[0] in placed:
                continue
            out.extend(atom)
            placed.update(atom)
        order_map[r] = out
    return order_map


# --- crossing counting (Barth-Jünger-Mutzel bilayer accumulator) ------------------------------------------


def _positions(order_map: dict[int, list[int]], r: int) -> dict[int, int]:
    return {node: k for k, node in enumerate(order_map[r])}


def _bilayer_crossings(model: _Model, upper: list[int], lower: list[int]) -> int:
    """Weighted crossings between adjacent ranks given their orders.

    BJM: count inversions of the lower endpoints when edges are visited in upper-then-lower order, via a
    Fenwick accumulator tree.
    """
    pos_lo = {node: k for k, node in enumerate(lower)}
    seq: list[int] = []  # lower endpoints, in (upper pos, lower pos) scan order
    for u in upper:
        targets = sorted(pos_lo[v] for v in model.down[u] if v in pos_lo)
        seq.extend(targets)
    if not seq:
        return 0
    size = len(lower)
    tree = [0] * (size + 1)

    def add(idx: int) -> None:
        idx += 1
        while idx <= size:
            tree[idx] += 1
            idx += idx & -idx

    def prefix(idx: int) -> int:
        idx += 1
        s = 0
        while idx > 0:
            s += tree[idx]
            idx -= idx & -idx
        return s

    crossings = 0
    for placed, pv in enumerate(seq):  # placed == edges already added
        crossings += placed - prefix(pv)  # already-placed edges with a strictly greater lower endpoint
        add(pv)
    return crossings


def _total_crossings(model: _Model, order_map: dict[int, list[int]]) -> int:
    return sum(
        _bilayer_crossings(model, order_map.get(r, []), order_map.get(r + 1, []))
        for r in range(model.min_rank, model.max_rank)
    )


def count_crossings(g: _layout._Graph, ranks: list[list[int]]) -> int:
    """Weighted crossings of a per-rank individual order (any engine's) over the half-rank graph of ``g``.

    Connector nodes on each half-rank are positioned at the mean of their partners' (or, for a founder sibship,
    children's) columns — where a mating symbol is drawn — so the count reflects the drawn connectors. Lets a
    test compare v1's and v2's orders on the same measure.
    """
    model = _Model(g)
    order_map: dict[int, list[int]] = {}
    for lvl, row in enumerate(ranks):
        order_map[2 * lvl] = list(row)
    for r in range(model.min_rank, model.max_rank + 1):
        if r % 2 == 0:
            order_map.setdefault(r, [])
            continue
        order_map[r] = _placed_connectors(model, order_map, r)
    return _total_crossings(model, order_map)


def _placed_connectors(model: _Model, order_map: dict[int, list[int]], r: int) -> list[int]:
    """The connector nodes of odd rank ``r``, ordered at the mean of their partners' (else children's) columns.

    That is where drawing puts a mating symbol, so crossings counted against this order are the drawn ones.
    """
    conns = model.connectors_on.get(r, [])
    up = _positions(order_map, r - 1) if r - 1 in order_map else {}
    dn = _positions(order_map, r + 1) if r + 1 in order_map else {}

    def key(n: int) -> tuple[float, tuple[tuple[_layout.Ident, ...], tuple[_layout.Ident, ...]]]:
        ups = [up[v] for v in model.up.get(n, ()) if v in up]
        ps = ups or [dn[v] for v in model.down.get(n, ()) if v in dn]
        return (sum(ps) / len(ps) if ps else 0.0, model.mating_key(model.g.matings[n - model.mat_base]))

    return sorted(conns, key=key)


def _band_crossings(model: _Model, order_map: dict[int, list[int]], lo: int, hi: int) -> int:
    """Crossings between consecutive ranks ``lo..hi`` (clamped to the model's ranks)."""
    lo, hi = max(lo, model.min_rank), min(hi, model.max_rank)
    return sum(_bilayer_crossings(model, order_map.get(r, []), order_map.get(r + 1, [])) for r in range(lo, hi))


# --- mincross: wmedian + transpose + tiny exact refinement -------------------------------------------------


def _median_value(positions: list[int]) -> float:
    """The median adjacent position (dot's ``median_value``), or ``-1`` for no fixed neighbours.

    An even count is interpolated toward the tighter side; ``-1`` leaves the node where it is.
    """
    m = len(positions)
    if m == 0:
        return -1.0
    positions = sorted(positions)
    mid = m // 2
    if m % 2 == 1:
        return float(positions[mid])
    if m == 2:
        return (positions[0] + positions[1]) / 2.0
    left = positions[mid - 1] - positions[0]
    right = positions[m - 1] - positions[mid]
    if left + right == 0:
        return (positions[mid - 1] + positions[mid]) / 2.0
    return (positions[mid - 1] * right + positions[mid] * left) / (left + right)


def _rank_units(model: _Model, order_map: dict[int, list[int]], r: int) -> list[tuple[int, ...]]:
    """The orderable units on rank ``r``: connector nodes (odd rank) or adjacency atoms (even rank)."""
    if r % 2 == 1:
        return [(node,) for node in order_map[r]]
    units: list[tuple[int, ...]] = []
    placed: set[int] = set()
    current = order_map[r]
    for i in current:
        if i in placed:
            continue
        atom = model.atom_of[i]
        # Members in their current order (a reversed atom reads back reversed); ``model.atom_of`` holds the
        # canonical orientation (fewest birth-order inversions, then identity) that ``_transpose_rank`` prefers.
        units.append(tuple(n for n in current if n in atom))
        placed.update(atom)
    return units


def _wmedian(model: _Model, order_map: dict[int, list[int]], down: bool) -> None:
    """One median sweep: reorder each rank by the median of its already-fixed neighbour rank.

    Units are atoms (even ranks) or connectors (odd ranks); a unit with no fixed neighbour keeps its slot
    (stable).
    """
    ranks = range(model.min_rank + 1, model.max_rank + 1) if down else range(model.max_rank - 1, model.min_rank - 1, -1)
    for r in ranks:
        adj = model.up if down else model.down
        pos_adj = _positions(order_map, r - 1 if down else r + 1)
        units = _rank_units(model, order_map, r)
        keys = [_unit_median(unit, adj, pos_adj) for unit in units]
        idx = list(range(len(units)))
        idx.sort(key=lambda u: keys[u] if keys[u] >= 0 else float(u))
        order_map[r] = [i for u in idx for i in units[u]]


def _unit_median(unit: tuple[int, ...], adj: dict[int, list[int]], pos_adj: dict[int, int]) -> float:
    ps = [pos_adj[v] for node in unit for v in adj.get(node, ()) if v in pos_adj]
    return _median_value(ps)


def _neighbour_cost(model: _Model, order_map: dict[int, list[int]], r: int) -> int:
    c = 0
    if r > model.min_rank:
        c += _bilayer_crossings(model, order_map.get(r - 1, []), order_map[r])
    if r < model.max_rank:
        c += _bilayer_crossings(model, order_map[r], order_map.get(r + 1, []))
    return c


def _birth_of(g: _layout._Graph) -> dict[int, tuple[int, int]]:
    """Individual -> (sibship id, birth position) from ``Mating.offspring`` order, for the birth-order rule."""
    return {o.child: (mr.index, k) for mr in g.matings for k, o in enumerate(mr.offspring)}


def _inversions(birth: dict[int, tuple[int, int]], seq: tuple[int, ...] | list[int]) -> int:
    """Sibling pairs in ``seq`` drawn out of birth order (an older sibling right of a younger one)."""
    total = 0
    for a in range(len(seq)):
        ba = birth.get(seq[a])
        if ba is None:
            continue
        for b in range(a + 1, len(seq)):
            bb = birth.get(seq[b])
            if bb is not None and bb[0] == ba[0] and ba[1] > bb[1]:
                total += 1
    return total


def _birth_gain(birth: dict[int, tuple[int, int]], left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Birth-order inversions removed by swapping adjacent ``left``/``right``.

    Sibling pairs across the two units that are currently reversed (older drawn right of younger) minus those
    the swap would reverse.
    """
    gain = 0
    for a in left:
        ba = birth.get(a)
        if ba is None:
            continue
        for b in right:
            bb = birth.get(b)
            if bb is not None and bb[0] == ba[0]:
                gain += 1 if ba[1] > bb[1] else -1
    return gain


def _transpose(model: _Model, order_map: dict[int, list[int]]) -> None:
    """Adjacent-unit swaps and atom reversals (dot's ``transpose``).

    A move is accepted on a strict crossing decrease or, on a crossing tie, a strict birth-order improvement /
    an atom's return to its canonical orientation.
    """
    birth = model.birth
    changed = True
    while changed:
        changed = False
        for r in range(model.min_rank, model.max_rank + 1):
            if _transpose_rank(model, order_map, r, birth):
                changed = True


def _swap_gain(
    model: _Model,
    adj_pos: list[tuple[dict[int, list[int]], dict[int, int]]],
    left: tuple[int, ...],
    right: tuple[int, ...],
) -> int:
    """Crossings removed by swapping two adjacent groups ``left``/``right`` on one rank.

    dot's ``in_cross`` + ``out_cross``. Only edges incident to the two groups can change, so count, on each
    adjacent rank, the endpoint pairs that cross now (left endpoint right of right endpoint) minus those that
    would cross after. Equals the exact change in ``_neighbour_cost``, at O(deg(left) * deg(right)) instead of
    a full recount.
    """
    before = after = 0
    for adj, pos in adj_pos:
        lt = [pos[v] for n in left for v in adj.get(n, ()) if v in pos]
        rt = [pos[v] for n in right for v in adj.get(n, ()) if v in pos]
        for a in lt:
            for b in rt:
                if a > b:
                    before += 1
                elif a < b:
                    after += 1
    return before - after


def _transpose_rank(model: _Model, order_map: dict[int, list[int]], r: int, birth: dict[int, tuple[int, int]]) -> bool:
    """One transpose pass over rank ``r``: atom reversals, then adjacent-unit swaps (dot's ``transpose``).

    Every move is priced by the local ``_swap_gain`` — the exact change in ``_neighbour_cost``; a reversal's is
    the sum over its member pairs — and accepted on a strict crossing decrease. On a crossing tie, a move is
    taken when it strictly reduces birth-order inversions, and an atom standing reversed from its canonical
    orientation (fewest inversions, then identity) is restored. So among equal-crossing orders birth order,
    then identity, are the weak preferences. Terminates: each accepted move strictly decreases (crossings,
    inversions, atoms out of canonical orientation), and a canonical atom never has more inversions than its
    reverse, so a birth repair and a canonical restore cannot undo each other.
    """
    improved = False
    units = _rank_units(model, order_map, r)
    adj_pos: list[tuple[dict[int, list[int]], dict[int, int]]] = []
    if r > model.min_rank:
        adj_pos.append((model.up, _positions(order_map, r - 1)))
    if r < model.max_rank:
        adj_pos.append((model.down, _positions(order_map, r + 1)))
    for u, unit in enumerate(units):  # atom reversals (a couple flip is the two-member case)
        if len(unit) < 2:
            continue
        # Reversing a unit flips the relative order of every pair in it, and a crossing depends only on relative
        # order, so the reversal's crossing change is the sum of its pairwise swap gains.
        gain = sum(
            _swap_gain(model, adj_pos, (unit[a],), (unit[b],))
            for a in range(len(unit))
            for b in range(a + 1, len(unit))
        )
        flipped = tuple(reversed(unit))
        canonical = unit == model.atom_of[unit[0]]
        birth_gain = _inversions(birth, unit) - _inversions(birth, flipped)
        if gain > 0 or (gain == 0 and (birth_gain > 0 or (birth_gain == 0 and not canonical))):
            units[u] = flipped
            improved = True
    for k in range(len(units) - 1):  # adjacent-unit swaps
        gain = _swap_gain(model, adj_pos, units[k], units[k + 1])
        if gain > 0 or (gain == 0 and _birth_gain(birth, units[k], units[k + 1]) > 0):
            units[k], units[k + 1] = units[k + 1], units[k]
            improved = True
    order_map[r] = [i for a in units for i in a]
    return improved


def _exact_ranks(model: _Model, order_map: dict[int, list[int]]) -> None:
    """Exact per-rank search on ranks with few atoms.

    Enumerate atom permutations and reversals and keep the order with the fewest crossings, then the fewest torn
    sibships, then the fewest birth-order inversions, then the smallest identity sequence (inversions before
    identity, so the settled map is not thrown away for trading birth order for identity). On an individuals' rank
    the cost is the crossings of the band either side, with its connector ranks re-placed for each trial. Makes
    small boundary-bridge ranks provably optimal and their choice deterministic.

    Crossings rank above tears here, unlike in the objective, because ``_rank_overlaps`` sees a lone child as a
    point: a lone child moved past another family, its descent then running along that family's bar, counts as
    a crossing and no tear. Ranking tears first chose those orders, and the drawing merged the two bars into one
    sibship with two sets of parents (57 of the 72 fuzzed pedigrees it made drawable). The fix is a tear measure
    that sees the descent, not this key.
    """
    for _ in range(2):  # two settling passes; small and convergent
        for r in range(model.min_rank, model.max_rank + 1):
            units = _rank_units(model, order_map, r)
            if not units or len(units) > _EXACT_ATOM_CAP:
                continue
            variants = [[unit, tuple(reversed(unit))] if len(unit) >= 2 else [unit] for unit in units]
            # On an individuals' rank, the connector ranks either side follow the trial order (re-placed as drawing
            # places them), so a move is costed against where its matings will sit, not where they sat before —
            # otherwise moving a couple looks costly against stale connectors and the search keeps a crossing.
            follow = [c for c in (r - 1, r + 1) if r % 2 == 0 and c in order_map and c % 2 == 1]
            saved = {c: list(order_map[c]) for c in follow}
            best_order: list[int] | None = None
            best_conns: dict[int, list[int]] = saved
            best_key: tuple[int, int, int, tuple[_layout.Ident, ...]] | None = None
            for perm in itertools.permutations(range(len(units))):
                for choice in itertools.product(*(range(len(v)) for v in variants)):
                    seq = [i for u in perm for i in variants[u][choice[u]]]
                    order_map[r] = seq
                    if follow:
                        for c in follow:
                            order_map[c] = _placed_connectors(model, order_map, c)
                        cost = _band_crossings(model, order_map, r - 2, r + 2)
                    else:
                        cost = _neighbour_cost(model, order_map, r)
                    ident = tuple(model.ident(i) for i in seq if not model.is_mating(i))
                    key = (cost, _rank_overlaps(model, r, seq), _inversions(model.birth, seq), ident)
                    if best_key is None or key < best_key:
                        best_key, best_order = key, list(seq)
                        best_conns = {c: list(order_map[c]) for c in follow}
            assert best_order is not None
            order_map[r] = best_order
            order_map.update(best_conns)


def _edge_length(model: _Model, order_map: dict[int, list[int]]) -> int:
    """Sum over edges of the index distance between the endpoints' positions — the secondary objective.

    Among equal-crossing orders it prefers parents over their children and partners near their kin (shorter
    drops, no off-centre descents), which the crossing count alone cannot see.
    """
    pos = {r: _positions(order_map, r) for r in order_map}
    total = 0
    for r in range(model.min_rank, model.max_rank):
        up, down = pos.get(r, {}), pos.get(r + 1, {})
        for u, k in up.items():
            total += sum(abs(k - down[v]) for v in model.down.get(u, ()) if v in down)
    return total


def _birth_order_inversions(model: _Model, order_map: dict[int, list[int]]) -> int:
    """Sibling pairs drawn out of their IR birth order (``Mating.offspring`` order), summed over sibships.

    Birth order is the weak preference of layout-v2.md: it yields to crossings, but not to edge length.
    """
    pos = {r: _positions(order_map, r) for r in order_map}
    total = 0
    for mr in model.g.matings:
        kids = [o.child for o in mr.offspring]
        where = [(pos[model.rank[k]][k], model.rank[k]) for k in kids if k in pos.get(model.rank[k], {})]
        for a in range(len(where)):
            for b in range(a + 1, len(where)):
                if where[a][1] == where[b][1] and where[a][0] > where[b][0]:
                    total += 1
    return total


def _rank_overlaps(model: _Model, r: int, order: list[int]) -> int:
    """Pairs of sibships on rank ``r`` whose child spans overlap under ``order`` — a torn sibship.

    A sibship is torn when another sibship's child stands between its first and last child, so their sib bars
    would overlap and a child read as issue of several matings. A spouse standing among siblings is not a child
    of another sibship there and tears nothing (published figures draw spouses inside sibships routinely).
    """
    pos = {node: k for k, node in enumerate(order)}
    spans = sorted(
        (min(pos[c] for c in kids), max(pos[c] for c in kids))
        for kids in model.sibships_on.get(r, ())
        if all(c in pos for c in kids)
    )
    overlaps = 0
    for a in range(len(spans)):
        for b in range(a + 1, len(spans)):
            if spans[b][0] > spans[a][1]:
                break
            overlaps += 1
    return overlaps


def _total_overlaps(model: _Model, order_map: dict[int, list[int]]) -> int:
    return sum(_rank_overlaps(model, r, order_map.get(r, [])) for r in model.sibships_on)


def _objective(model: _Model, order_map: dict[int, list[int]]) -> tuple[int, int, int, int]:
    """Lexicographic: torn sibships, then crossings, then birth-order inversions, then edge length.

    Sibship contiguity is strong and crossings weak (layout-v2.md), so no number of crossings buys a torn sibship.
    """
    return (
        _total_overlaps(model, order_map),
        _total_crossings(model, order_map),
        _birth_order_inversions(model, order_map),
        _edge_length(model, order_map),
    )


def _mincross(model: _Model, init: dict[int, list[int]], *, settle: bool = True) -> dict[int, list[int]]:
    """Dot's mincross loop, then a tiny exact search on small ranks.

    Alternate ``wmedian`` (down/up) and ``transpose``, keep the best order under the lexicographic objective
    (torn sibships, crossings, birth-order inversions, edge length) on strict improvement — no drift on ties, so the
    result is deterministic; stop when a down+up round leaves the order unchanged. The exact search then settles small
    ranks and fixes their tie-break (``_settle``); ``settle=False`` skips it, for a trial run whose winner alone is
    settled.
    """
    best = {r: list(nodes) for r, nodes in init.items()}
    best_key = _objective(model, best)
    cur = {r: list(nodes) for r, nodes in init.items()}
    for it in range(_MAX_ITERS):
        before = {r: list(nodes) for r, nodes in cur.items()}
        _wmedian(model, cur, down=(it % 2 == 0))
        _transpose(model, cur)
        key = _objective(model, cur)
        if key < best_key:
            best_key = key
            best = {r: list(nodes) for r, nodes in cur.items()}
        if cur == before and it % 2 == 1:  # a full down+up round changed nothing: converged
            break
    return _settle(model, best) if settle else best


def _settle(model: _Model, order_map: dict[int, list[int]]) -> dict[int, list[int]]:
    """The exact search on small ranks, kept unless it scores worse under the objective (the settle guard)."""
    settled = {r: list(nodes) for r, nodes in order_map.items()}
    _exact_ranks(model, settled)
    return settled if _objective(model, settled) <= _objective(model, order_map) else order_map
