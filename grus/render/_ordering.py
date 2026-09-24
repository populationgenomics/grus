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
individuals kept contiguous on their rank, orientation free). A same-generation cross-marriage (a
boundary-bridge join) is an ordinary couple atom whose two members belong to different families, so ordering
alone brings the two sibships' ends together — no special placement pass. Sibship contiguity is emergent (the
children of one mating share a connector, so crossing-minimisation clusters them), matching v1. An individual
needing more than two 1-D neighbours (>2 matings) overflows: the two highest-weight matings stay adjacent, the
rest are **routed** (recorded in ``Ordering.routed`` for Stage C; Stage B still defers routed shapes as v1
does). Determinism is bit-exact: no RNG, sorted integer/identity iteration, a fixed pass count, ``best``
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
        routed: mating indices demoted to routed edges (an individual's overflow matings). Empty for tier-1
            and the boundary-bridge joins; non-empty shapes still defer in Stage B (Stage C draws the routes).
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
    order_map = _init_order(model)
    order_map = _mincross(model, order_map)
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
        self._build_layers()
        self._build_atoms()
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

    def ident(self, i: int) -> tuple[int, int]:
        """Stable IR identity of an individual — used for every tie-break so the order is shuffle-invariant."""
        ind = self.g.individuals[i]
        return (ind.generation, ind.index)

    # -- overflow: an individual with >2 matings keeps its two heaviest; the rest route --
    def _resolve_overflow(self) -> None:
        for i in range(self.g.n):
            ms = [mr for mr in self.g.matings_of[i] if mr.b is not None and mr.index not in self.routed]
            if len(ms) <= 2:
                continue
            # keep the two heaviest adjacencies (most offspring); route the rest, tie-broken by mating index.
            ms.sort(key=lambda mr: (-len(mr.kids), mr.index))
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
        self.min_rank = min(self.rank.values(), default=0)  # a founder-sibship connector sits at rank -1

    # -- atoms: per even rank, the must-be-adjacent chains (couples + twin groups) --
    def _build_atoms(self) -> None:
        g = self.g
        adj: dict[int, set[int]] = defaultdict(set)

        def link(a: int, b: int) -> None:
            adj[a].add(b)
            adj[b].add(a)

        for mr in g.matings:
            if not self._drawn(mr):
                continue
            if mr.b is not None and g.level[mr.a] == g.level[mr.b]:
                link(mr.a, mr.b)
            groups: dict[int, list[int]] = defaultdict(list)
            for o in mr.offspring:
                if o.twin_group is not None:
                    groups[o.twin_group].append(o.child)
            for kids in groups.values():
                for a, b in itertools.pairwise(kids):
                    link(a, b)

        # each connected component becomes one atom (a canonical path). Overflow already routed above; a
        # residual non-path component (Stage-C shape) is emitted sorted and the caller's guards defer it.
        self.atom_of: dict[int, tuple[int, ...]] = {}
        for lvl in range(self.nlevels):
            members = [i for i in range(g.n) if g.level[i] == lvl]
            seen: set[int] = set()
            for start in sorted(members, key=self.ident):
                if start in seen:
                    continue
                comp = self._component(start, adj)
                seen |= comp
                atom = self._orient(comp, adj)
                for i in atom:
                    self.atom_of[i] = atom

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
        if len(ends) != 2:  # not a simple path (a cycle or a branch) — Stage C; emit sorted, caller defers
            return tuple(sorted(comp, key=self.ident))
        best: tuple[int, tuple[tuple[int, int], ...]] | None = None
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


def _init_order(model: _Model) -> dict[int, list[int]]:
    """First-appearance order per rank from a DFS, regrouped so each atom is contiguous.

    Founders in ``(generation, index)`` order, descending into each mating's children in birth
    (``Mating.offspring``) order.
    """
    g = model.g
    seen_i: set[int] = set()
    seen_m: set[int] = set()
    appear: dict[int, list[int]] = defaultdict(list)

    def visit_ind(i: int) -> None:
        if i in seen_i:
            return
        seen_i.add(i)
        appear[model.rank[i]].append(i)
        for mr in sorted(g.matings_of[i], key=lambda mr: mr.index):
            if model._drawn(mr):
                visit_mat(mr)

    def visit_mat(mr: _layout._Mating) -> None:
        if mr.index in seen_m:
            return
        seen_m.add(mr.index)
        appear[model.rank[model.mnode(mr.index)]].append(model.mnode(mr.index))
        for pk in sorted(mr.partners, key=model.ident):
            visit_ind(pk)
        for k in mr.kids:  # birth order
            visit_ind(k)

    for mr in sorted(g.partnerless, key=lambda mr: min((model.ident(k) for k in mr.kids), default=(0, 0))):
        if model._drawn(mr):
            visit_mat(mr)
    for i in sorted(range(g.n), key=model.ident):
        visit_ind(i)

    return _regroup(model, appear)


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
        conns = [n for n, rr in model.rank.items() if rr == r and model.is_mating(n)]
        pos_up = _positions(order_map, r - 1) if r - 1 in order_map else {}
        pos_dn = _positions(order_map, r + 1) if r + 1 in order_map else {}

        def key(n: int, up: dict[int, int] = pos_up, dn: dict[int, int] = pos_dn) -> tuple[float, int]:
            ups = [up[v] for v in model.up.get(n, ()) if v in up]
            ps = ups or [dn[v] for v in model.down.get(n, ()) if v in dn]
            mean = sum(ps) / len(ps) if ps else 0.0
            return (mean, n)

        order_map[r] = sorted(conns, key=key)
    return _total_crossings(model, order_map)


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
    torn = model.sibships_on.get(r) is not None

    def overlaps(trial: list[tuple[int, ...]]) -> int:
        return _rank_overlaps(model, r, [i for a in trial for i in a]) if torn else 0

    current = overlaps(units)
    for u, unit in enumerate(units):  # atom reversals (a couple flip is the two-member case)
        if len(unit) < 2:
            continue
        flipped = tuple(reversed(unit))
        after = overlaps([*units[:u], flipped, *units[u + 1 :]])
        # Reversing a unit flips the relative order of every pair in it, and a crossing depends only on relative
        # order, so the reversal's crossing change is the sum of its pairwise swap gains.
        gain = sum(
            _swap_gain(model, adj_pos, (unit[a],), (unit[b],))
            for a in range(len(unit))
            for b in range(a + 1, len(unit))
        )
        canonical = unit == model.atom_of[unit[0]]
        birth_gain = _inversions(birth, unit) - _inversions(birth, flipped)
        if after < current or (
            after == current and (gain > 0 or (gain == 0 and (birth_gain > 0 or (birth_gain == 0 and not canonical))))
        ):
            units[u] = flipped
            current = after
            improved = True
    for k in range(len(units) - 1):  # adjacent-unit swaps
        trial = [*units[:k], units[k + 1], units[k], *units[k + 2 :]]
        after = overlaps(trial)
        gain = _swap_gain(model, adj_pos, units[k], units[k + 1])
        if after < current or (
            after == current and (gain > 0 or (gain == 0 and _birth_gain(birth, units[k], units[k + 1]) > 0))
        ):
            units[k], units[k + 1] = units[k + 1], units[k]
            current = after
            improved = True
    order_map[r] = [i for a in units for i in a]
    return improved


def _exact_ranks(model: _Model, order_map: dict[int, list[int]]) -> None:
    """Exact per-rank search on ranks with few atoms.

    Enumerate atom permutations and reversals, keep the min-crossing order (tie-broken by fewer birth-order
    inversions, then the smallest identity sequence — the objective's order, so the settled map is not thrown
    away for trading birth order for identity). Makes small boundary-bridge ranks provably optimal and their
    choice deterministic.
    """
    for _ in range(2):  # two settling passes; small and convergent
        for r in range(model.min_rank, model.max_rank + 1):
            units = _rank_units(model, order_map, r)
            if not units or len(units) > _EXACT_ATOM_CAP:
                continue
            variants = [[unit, tuple(reversed(unit))] if len(unit) >= 2 else [unit] for unit in units]
            best_order: list[int] | None = None
            best_key: tuple[int, int, int, tuple[tuple[int, int], ...]] | None = None
            for perm in itertools.permutations(range(len(units))):
                for choice in itertools.product(*(range(len(v)) for v in variants)):
                    seq = [i for u in perm for i in variants[u][choice[u]]]
                    order_map[r] = seq
                    cost = _neighbour_cost(model, order_map, r)
                    ident = tuple(model.ident(i) for i in seq if not model.is_mating(i))
                    key = (_rank_overlaps(model, r, seq), cost, _inversions(model.birth, seq), ident)
                    if best_key is None or key < best_key:
                        best_key, best_order = key, list(seq)
            assert best_order is not None
            order_map[r] = best_order


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


def _mincross(model: _Model, init: dict[int, list[int]]) -> dict[int, list[int]]:
    """Dot's mincross loop, then a tiny exact search on small ranks.

    Alternate ``wmedian`` (down/up) and ``transpose``, keep the best order under the lexicographic objective
    (crossings, birth-order inversions, edge length) on strict improvement — no drift on ties, so the result is
    deterministic; stop when a down+up round leaves the order unchanged. The exact search then settles small
    ranks and fixes their tie-break.
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
    settled = {r: list(nodes) for r, nodes in best.items()}
    _exact_ranks(model, settled)
    if _objective(model, settled) <= best_key:
        best = settled
    return best
