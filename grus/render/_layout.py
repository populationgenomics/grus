"""Shared layout front end + the layout->drawing seam (IR -> a (level, x) grid).

Holds the pieces both the ranking and the x-solve build on; the placement itself is the v2 constraint
solver in ``_layout2`` (``layout``), which reuses everything here. What lives here:

1. ``_derive`` — parent/spouse/children maps from ``Mating``s, indexing everyone ``0..n-1`` by their
   position in ``Pedigree.individuals`` (the ``Position`` id is for drawing only);
2. the avuncular ``ghost`` duplication (``_duplicate_cross_generation``) and the cross-lineage / loop
   detection (``_cross_matings``, ``_detect_loops``);
3. ranking (``_rank``): each individual's row is its IR ``generation`` (the drawn row), offset so the
   pedigree's first generation is row 0 — so a detached branch sits on its own generations and a child
   drawn more than one row below its parents stays there;
4. pass-throughs (``_insert_passthroughs``): a descent spanning several rows gets one synthetic cell on
   each row it crosses, the layered-drawing dummy node, so ordering, x-solve and ``_build`` see only
   adjacent-row edges and drawing emits one line through the crossed rows;
5. ``_build`` — emit the per-level arrays (``n / nid / pos / fam / spouse / twins / ...``), the only
   interface the drawing step reads, from a within-rank order. A single-parent sibship is signalled to
   drawing by a zero ``spouse`` on the parent column.

A consanguinity loop that survives excluding the cross-lineage joins, and a few topologies ``_build``
cannot express, are **detected and deferred** (``DeferredFeatureError``), never mislaid out. A ``Mating``
has zero partners (a founder sibship), one (a lone drawn parent), or two.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from grus.models import pedigree_pb2 as pb

Key = tuple[int, int]

# Childlessness values that draw a glyph (by choice / infertility); NONE and UNSPECIFIED draw nothing.
_CHILDLESS_DRAWN = frozenset({int(pb.CHILDLESSNESS_BY_CHOICE), int(pb.CHILDLESSNESS_INFERTILITY)})


class DeferredFeatureError(Exception):
    """The IR uses a topology the layout deliberately does not draw — surfaced, not mislaid out.

    The v2 constraint layout draws far more than v1 did (loops that close on a single cross-mating,
    >2-mate individuals via routed edges, two-lineage joins brought adjacent by the ordering), so the
    residual deferral set is small: an interlocking consanguinity loop (a cycle that survives excluding the
    cross-lineage joins — ``_detect_loops``); an individual who is a child of more than one mating
    (``_derive``); a couple whose partners are on different generations other than an avuncular join between
    two born-in partners, which draws via the ghost (``_rank``); an order ``_build`` cannot express (a routed
    or overflow mating that *has* offspring — descent from a non-adjacent parent pair); and a torn sibship
    whose descent bars would overlap. The IR still round-trips; the gap is meant to surface in the eval diff
    rather than a wrong drawing.
    """


@dataclass(frozen=True)
class RoutedMating:
    """A mating drawn as a routed orthogonal polyline, not a straight adjacent line (layout v2, Stage C).

    A mating whose partners are not adjacent same-rank cells cannot be expressed by ``spouse`` (which flags
    only an adjacent couple ``k, k+1``); it is carried here instead, in cell coordinates, so drawing can route
    an edge between the partners without re-deriving adjacency. ``Layout.routed`` is empty for v1 and for every
    adjacency-drawable shape, so v1 output — and any pedigree without a routed mating — is unchanged.

    Attributes:
        a: ``(level, column)`` of one partner.
        b: ``(level, column)`` of the other partner (same ``level`` as ``a`` for a same-rank routed mating —
            an overflow >2-mate connector; a cross-rank avuncular join is still handled by the ``ghost``).
        consanguineous: draw the routed edge doubled.
        children: each child's ``(level, column)`` — empty for a childless routed mating. Stage C routes only
            childless matings (the >2-mate overflow case); a routed mating *with* offspring still defers, so
            ``children`` is presently always empty. Kept on the seam so a later stage can drop descent from
            the routed edge's midpoint without another seam change.
    """

    a: tuple[int, int]
    b: tuple[int, int]
    consanguineous: bool
    children: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class Layout:
    """kinship2's per-level parallel arrays — the layout->drawing seam.

    Every list is indexed by a 0-based level ``L`` (``L = 0`` is the top generation); within a level,
    columns ``k`` run left to right by increasing ``pos``. Individual indices are positions in the source
    ``Pedigree.individuals``. ``pos`` is in abstract layout units (drawing scales by ``Geometry.x_unit``).

    Attributes:
        n: ``n[L]`` — number of individuals (cells) on level ``L``.
        nid: ``nid[L][k]`` — the individual index in cell ``(L, k)``.
        pos: ``pos[L][k]`` — the x of that cell.
        fam: ``fam[L][k]`` — the column, on level ``L-1``, of this individual's parent (for a couple, the
            *left* member — the right is the next column; for a lone single parent, that parent's column);
            ``-1`` if it has no drawn parents. Whether ``fam`` names a couple or a lone parent is read from
            ``spouse`` on level ``L-1``.
        spouse: ``spouse[L][k]`` — 0 if cell ``k`` is not the left member of a couple, 1 if it mates the
            cell to its right, 2 if that mating is consanguineous (a double line). A lone single parent is
            0 (no mate) — which is how drawing tells a lone-parent descent from a couple's.
        twins: ``twins[L][k]`` — 0, or a ``pb.ZygosityType`` value if cell ``k`` and the cell to its right
            are co-twins (1 MZ, 2 DZ, 3 unknown zygosity).
        childless: ``childless[L][k]`` — 0 if cell ``k`` is not the left member of a childless couple, else
            the couple's ``pb.Childlessness`` value (2 by choice, 3 infertility). Drawing hangs the Bennett
            childless glyph (a stub to one bar for choice, two for infertility) from the mating midpoint.
            ``NONE`` (1) and ``UNSPECIFIED`` (0) both leave it 0 — a fertile couple draws nothing.
        ghost_of: ``{ghost_index: real_index}`` — cells whose ``nid`` is a **synthetic duplicate** of a real
            individual, drawn for a cross-generation join (an avuncular marriage: the shallower partner is
            duplicated down to the deeper partner's row so the marriage is a same-row couple). Drawing renders
            the real individual's glyph at the ghost cell and a dashed "same individual" link to the real cell.
            Empty for tier-1/2 pedigrees. A ghost index is ``>= len(Pedigree.individuals)``.
        founder_sibships: each ``(level, columns)`` a sibship whose parents are undrawn (a partnerless mating).
            Drawing hangs these from an implied hanger stub with a sib bar and no parent cells (they carry no
            ``fam`` entry). ``columns`` are cell columns on ``level``, left to right. Empty when none.
        routed: matings drawn as routed edges rather than adjacent straight lines (a >2-mate individual's
            overflow mating — the partners cannot both be its neighbour). Empty for v1 and for every
            adjacency-drawable shape, so it never perturbs existing output. See ``RoutedMating``.
        first_generation: the IR ``generation`` drawn on level 0; level ``L`` draws generation
            ``first_generation + L``. Rows between the first and last generation are kept even when empty
            (a detached branch several generations down).
        passthrough: cells whose ``nid`` is a **synthetic pass-through** on a row a descent crosses — the
            couple (or lone parent) heads the first pass-through, each heads the next, and the last heads the
            real sibship, which keeps its twins and birth order. Drawing emits no symbol for a pass-through,
            only the line through it, and one ``sibship`` group from the real parents to the real children.
            A pass-through index is ``>= len(Pedigree.individuals)``, like a ghost's.
    """

    n: list[int]
    nid: list[list[int]]
    pos: list[list[float]]
    fam: list[list[int]]
    spouse: list[list[int]]
    twins: list[list[int]]
    childless: list[list[int]] = field(default_factory=list)
    ghost_of: dict[int, int] = field(default_factory=dict)
    founder_sibships: list[tuple[int, tuple[int, ...]]] = field(default_factory=list)
    routed: list[RoutedMating] = field(default_factory=list)
    first_generation: int = 1
    passthrough: frozenset[int] = frozenset()


@dataclass(frozen=True)
class _Off:
    """One resolved child edge: the child's row index plus optional twin grouping."""

    child: int
    twin_group: int | None
    twin_type: int


@dataclass(frozen=True)
class _Mating:
    """A mating with its partner(s) and offspring resolved to row indices. Zero, one, or two partners.

    Zero partners is a *founder sibship*: the offspring are siblings via an undrawn parent couple, so there
    is no ``a`` / ``b`` (both raise) — such a mating is placed as a bare sibship, not a couple or lone parent.
    """

    index: int
    partners: tuple[int, ...]
    consanguineous: bool
    offspring: tuple[_Off, ...]
    childlessness: int = 0  # pb.Childlessness value; carried through for drawing (0/NONE draws nothing)

    @property
    def partnerless(self) -> bool:
        return not self.partners

    @property
    def a(self) -> int:
        return self.partners[0]

    @property
    def b(self) -> int | None:
        return self.partners[1] if len(self.partners) == 2 else None

    @property
    def kids(self) -> tuple[int, ...]:
        return tuple(o.child for o in self.offspring)

    def other(self, x: int) -> int:
        """The partner that is not ``x`` — only valid for a two-partner mating."""
        return self.partners[1] if x == self.partners[0] else self.partners[0]


@dataclass
class _Graph:
    """The pedigree as index-based adjacency the layout works over."""

    individuals: list[pb.Individual]
    index: dict[Key, int]
    matings: list[_Mating]
    matings_of: dict[int, list[_Mating]]
    parents: dict[int, tuple[int, ...]]
    partnerless: list[_Mating] = field(default_factory=list)  # founder sibships (matings with no drawn partner)
    foundersib_members: set[int] = field(default_factory=set)  # individuals grouped by a partnerless mating
    level: list[int] = field(default_factory=list)
    passthrough_to: dict[int, int] = field(default_factory=dict)  # pass-through -> first real child it leads to

    @property
    def n(self) -> int:
        return len(self.individuals)

    def label(self, i: int) -> str:
        """The drawn position of ``i``; a pass-through names the descent it carries, which is in the input."""
        if i in self.passthrough_to:
            return f"descent to {self.label(self.passthrough_to[i])}"
        ind = self.individuals[i]
        return f"{ind.generation}-{ind.index}"


def _derive(p: pb.Pedigree) -> _Graph:
    """Resolve positions to row indices and build the mating / parent adjacency (validate ran first).

    A partnerless mating (a founder sibship) contributes no parent edge — its offspring stay founders (no drawn
    parent) — but still claims its offspring, so a member cannot also be the child of another mating.
    """
    index = {(ind.generation, ind.index): i for i, ind in enumerate(p.individuals)}
    matings: list[_Mating] = []
    matings_of: dict[int, list[_Mating]] = {i: [] for i in range(len(p.individuals))}
    parents: dict[int, tuple[int, ...]] = {}
    partnerless: list[_Mating] = []
    foundersib_members: set[int] = set()
    claimed: set[int] = set()
    for mi, m in enumerate(p.matings):
        pks = [index[(pos.generation, pos.index)] for pos in _partner_positions(m)]
        offs: list[_Off] = []
        for o in m.offspring:
            child = index[(o.child.generation, o.child.index)]
            if child in claimed:
                raise DeferredFeatureError(
                    f"individual {g_label(p, child)!r} is a child of more than one mating (adoption / tier 3)"
                )
            claimed.add(child)
            if pks:
                parents[child] = tuple(pks)
            tg = o.twin_group if o.HasField("twin_group") else None
            offs.append(_Off(child=child, twin_group=tg, twin_type=int(o.twin_type)))
        mr = _Mating(
            index=mi,
            partners=tuple(pks),
            consanguineous=m.consanguineous,
            offspring=tuple(offs),
            childlessness=int(m.childlessness),
        )
        matings.append(mr)
        if pks:
            for pk in pks:
                matings_of[pk].append(mr)
        else:
            partnerless.append(mr)
            foundersib_members.update(mr.kids)
    return _Graph(
        individuals=list(p.individuals),
        index=index,
        matings=matings,
        matings_of=matings_of,
        parents=parents,
        partnerless=partnerless,
        foundersib_members=foundersib_members,
    )


def _partner_positions(m: pb.Mating) -> list[pb.Position]:
    partners: list[pb.Position] = []
    if m.HasField("partner_a"):
        partners.append(m.partner_a)
    if m.HasField("partner_b"):
        partners.append(m.partner_b)
    return partners


def g_label(p: pb.Pedigree, i: int) -> str:
    ind = p.individuals[i]
    return f"{ind.generation}-{ind.index}"


def _detect_loops(g: _Graph, cross: set[int]) -> None:
    """Refuse consanguinity loops the ordering cannot open as an adjacent couple — deferred, not mislaid out.

    Model each individual and each mating as a node; join every mating to its partner(s) and to each
    child. A loop-free pedigree is a forest of these nodes, so any component with a cycle (edges >= nodes)
    is a loop. A plain nuclear family is a tree, so this never false-positives on tier-1 shapes.

    Cross-lineage matings are **excluded** from the graph: a loop closed solely by one — a cousin marriage
    is the canonical case, two descendants of a shared ancestor marrying — reduces to a forest here, and the
    ordering draws that join as an ordinary (adjacent, consanguineous) couple once it pulls each partner to
    its sibship end. A cycle that survives excluding the cross-matings is a loop the ordering cannot open as
    a single adjacent couple (interlocking loops, an individual reachable by two non-marriage paths), and
    stays deferred.
    """
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(x: tuple[str, int]) -> tuple[str, int]:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: tuple[str, int], b: tuple[str, int]) -> None:
        parent[find(b)] = find(a)

    nodes: set[tuple[str, int]] = {("i", i) for i in range(g.n)}
    edges = 0
    for mr in g.matings:
        if mr.index in cross:
            continue  # drawn by the ordering as an adjacent consanguineous couple; not a loop to break here
        mnode = ("m", mr.index)
        nodes.add(mnode)
        for endpoint in [("i", pk) for pk in mr.partners] + [("i", k) for k in mr.kids]:
            edges += 1
            union(mnode, endpoint)
    components = len({find(node) for node in nodes})
    if edges - len(nodes) + components > 0:  # pragma: no cover - defensive: every cycle is closed by a
        # cross-mating (both partners born-in), so excluding those always leaves a forest; a surviving cycle
        # would be a _derive/_cross_matings bug — defer cleanly rather than mislay it out (an ordinary
        # adjacent consanguineous couple is what the ordering draws for the excluded cross-matings).
        raise DeferredFeatureError(
            "consanguinity loop unsupported (tier 3): a cycle survives after the cross-lineage joins "
            "(e.g. interlocking loops, or an individual reachable from a founder by two non-marriage paths)"
        )


def _cross_matings(g: _Graph) -> set[int]:
    """Indices of matings that join two drawn lineages — both partners are born-in (have drawn parents).

    A couple whose *both* members belong to (different) drawn subtrees is not a marry-in the ranking hangs
    off one lineage; it is a join of two lineages. ``_detect_loops`` **excludes** these from the cycle test
    (a same-generation cross-mating is how every drawable consanguinity loop closes, so excluding it leaves a
    forest). The v2 ordering then
    draws the join as an ordinary adjacent couple by pulling each partner to its sibship end — no special
    placement pass. Single-parent matings are exempt (one partner).

    A **founder-sibship member** counts as born-in here: its lineage block is its sibship, so a marriage
    between two such members (c19: I-10 x I-11, each in its own gen-I sibship) is likewise a two-lineage
    join, not a marry-in.
    """
    return {mr.index for mr in g.matings if mr.b is not None and _born_in(g, mr.a) and _born_in(g, mr.b)}


def _born_in(g: _Graph, i: int) -> bool:
    """Whether ``i``'s horizontal position is fixed by its own lineage block.

    That is a drawn parent, or a founder sibship (an undrawn parent couple whose siblings are laid out as a
    group).
    """
    return i in g.parents or i in g.foundersib_members


def _duplicate_cross_generation(g: _Graph) -> dict[int, int]:
    """Turn each genuine cross-generation join into a same-row couple by duplicating the shallower partner.

    A cross-lineage join whose two born-in partners sit on **different IR generations** is an avuncular
    marriage (c18: an uncle marries his niece) — it cannot be a single horizontal mating line, and pulling
    one lineage across the generation boundary would distort a drawn generation. Instead, following
    kinship2/CraneFoot, the shallower partner is **duplicated**: a synthetic ghost individual is created on
    the deeper partner's generation and substituted into the join, so the marriage becomes an ordinary
    marry-in couple the ranking and ordering place as a same-row couple (the ghost has no parents and the
    deeper partner's generation, so it ranks onto that row). The real partner keeps its own row and its other
    matings; drawing renders
    the ghost as a duplicate of the real individual with a dashed "same individual" link.

    Returns ``{ghost_index: real_index}`` for the drawing step; mutates ``g`` in place (appends the ghost
    individual, rewrites the join's partners and its children's parent pointers). Same-generation cross joins
    are left untouched — the ordering brings their partners adjacent as an ordinary couple. A partner already
    ghosted for another join, or a join both of whose partners would need duplicating, is left for the loop
    defenses downstream (interlocking cases stay deferred).
    """
    ghost_of: dict[int, int] = {}
    for mi, mr in enumerate(list(g.matings)):
        if mr.b is None or mr.a not in g.parents or mr.b not in g.parents:
            continue  # not a two-lineage join
        gen_a, gen_b = g.individuals[mr.a].generation, g.individuals[mr.b].generation
        if gen_a == gen_b:
            continue  # same-generation join: brought adjacent by the ordering, not duplicated
        shallow, deep = (mr.a, mr.b) if gen_a < gen_b else (mr.b, mr.a)
        ghost = len(g.individuals)
        g.individuals.append(pb.Individual(generation=g.individuals[deep].generation, index=10_000 + mi))
        g.parents = dict(g.parents)  # ghost is parentless; real `shallow` keeps its parents
        new = _Mating(
            index=mi,
            partners=(ghost, deep),
            consanguineous=mr.consanguineous,
            offspring=mr.offspring,
            childlessness=mr.childlessness,
        )
        g.matings[mi] = new
        g.matings_of[ghost] = [new]
        g.matings_of[shallow] = [m for m in g.matings_of[shallow] if m.index != mi]
        g.matings_of[deep] = [new if m.index == mi else m for m in g.matings_of[deep]]
        for k in mr.kids:
            g.parents[k] = (ghost, deep)
        ghost_of[ghost] = shallow
    return ghost_of


def _rank(g: _Graph) -> int:
    """Set ``g.level`` from each individual's IR ``generation``; return the generation drawn on level 0.

    Generation is the drawn row (docs/design/ir.md), so it is the rank: no depth is computed. A branch whose
    founders are drawn several generations down keeps its generations, and a child drawn more than one row
    below its parents (beside half-siblings whose other parent is lower) stays there — ``_insert_passthroughs``
    carries its descent across the rows between. ``grus.ir.validate`` has already checked that every child is
    below its parents and that siblings share a generation.

    Raises:
        DeferredFeatureError: a couple's partners are on different generations. The one cross-generation
            marriage drawn — an avuncular join of two born-in partners — was turned into a same-row couple by
            ``_duplicate_cross_generation`` before ranking; any other has no drawn form.
    """
    first = min((ind.generation for ind in g.individuals), default=1)
    g.level = [ind.generation - first for ind in g.individuals]
    for mr in g.matings:
        if mr.b is not None and g.level[mr.a] != g.level[mr.b]:
            raise DeferredFeatureError(
                f"couple {g.label(mr.a)!r} x {g.label(mr.b)!r} spans generations "
                f"{g.individuals[mr.a].generation} and {g.individuals[mr.b].generation}; only a marriage between "
                "two partners with drawn parents is drawn across generations"
            )
    return first


def _insert_passthroughs(g: _Graph) -> frozenset[int]:
    """Carry every descent that spans more than one row through a synthetic cell on each row it crosses.

    For a mating whose children sit ``d > 1`` rows below its partners (siblings share a row, so the gap is the
    sibship's), the mating's offspring become one pass-through on the next row; each pass-through is the lone
    parent of the next; the last is the lone parent of the real children, keeping their birth order and twin
    groups. After this every parent -> child edge joins adjacent rows, which is all the ordering, x-solve and
    ``_build`` handle. A pass-through's identity is ``(row generation, -(1_000_000 * child generation + child
    index))`` from the sibship's first child: stable under a shuffle of the input, and negative, so it can never
    equal a real individual's (indexes are >= 1) and the ordering's tie-break stays deterministic.

    Mutates ``g`` in place (appends the pass-through individuals and their lone-parent matings, rewrites the
    spanning mating's offspring and the parent pointers); returns the pass-through indices.
    """
    passthrough: set[int] = set()
    for mi, mr in enumerate(list(g.matings)):
        if mr.partnerless or not mr.offspring:
            continue
        top = g.level[mr.partners[0]]
        gap = g.level[mr.offspring[0].child] - top
        if gap <= 1:
            continue
        first_child = g.individuals[mr.offspring[0].child]
        chain: list[int] = []
        for row in range(top + 1, top + gap):
            cell = len(g.individuals)
            g.individuals.append(
                pb.Individual(
                    generation=first_child.generation - (top + gap - row),
                    index=-(1_000_000 * first_child.generation + first_child.index),
                )
            )
            g.level.append(row)
            g.matings_of[cell] = []
            g.passthrough_to[cell] = mr.offspring[0].child
            chain.append(cell)
            passthrough.add(cell)
        head = _Mating(
            index=mi,
            partners=mr.partners,
            consanguineous=mr.consanguineous,
            offspring=(_Off(child=chain[0], twin_group=None, twin_type=0),),
            childlessness=mr.childlessness,
        )
        g.matings[mi] = head
        for pk in mr.partners:
            g.matings_of[pk] = [head if m.index == mi else m for m in g.matings_of[pk]]
        g.parents[chain[0]] = mr.partners
        for upper, lower in itertools.pairwise(chain):
            _add_lone_parent_mating(g, upper, (_Off(child=lower, twin_group=None, twin_type=0),))
            g.parents[lower] = (upper,)
        _add_lone_parent_mating(g, chain[-1], mr.offspring)
        for k in mr.kids:
            g.parents[k] = (chain[-1],)
    return frozenset(passthrough)


def _add_lone_parent_mating(g: _Graph, parent: int, offspring: tuple[_Off, ...]) -> None:
    mating = _Mating(index=len(g.matings), partners=(parent,), consanguineous=False, offspring=offspring)
    g.matings.append(mating)
    g.matings_of[parent].append(mating)


def _build(
    g: _Graph,
    xpos: dict[int, float],
    ghost_of: dict[int, int],
    foundersib_groups: list[tuple[int, ...]],
    *,
    first_generation: int,
    passthrough: frozenset[int],
) -> Layout:
    """Bucket placed individuals into per-level arrays and derive fam / spouse / twins / founder sibships."""
    base_x = min(xpos.values()) if xpos else 0.0
    x = {k: v - base_x for k, v in xpos.items()}
    nlev = max(g.level) + 1 if g.level else 0

    cols: list[list[int]] = [[] for _ in range(nlev)]
    for i in range(g.n):
        cols[g.level[i]].append(i)
    for row in cols:
        row.sort(key=lambda i: (x[i], i))

    colof: dict[int, int] = {}
    for row in cols:
        for k, i in enumerate(row):
            colof[i] = k

    n = [len(row) for row in cols]
    pos = [[x[i] for i in row] for row in cols]

    couples = {frozenset(mr.partners): mr.consanguineous for mr in g.matings if mr.b is not None}
    spouse = [[0] * n[level] for level in range(nlev)]
    for level in range(nlev):
        for k in range(n[level] - 1):
            consanguineous = couples.get(frozenset((cols[level][k], cols[level][k + 1])))
            if consanguineous is not None:
                spouse[level][k] = 2 if consanguineous else 1

    # childlessness rides on the left member of a couple (2 by choice, 3 infertility); 0/NONE draws nothing
    childless_of = {frozenset(mr.partners): mr.childlessness for mr in g.matings if mr.b is not None}
    childless = [[0] * n[level] for level in range(nlev)]
    for level in range(nlev):
        for k in range(n[level] - 1):
            value = childless_of.get(frozenset((cols[level][k], cols[level][k + 1])), 0)
            if value in _CHILDLESS_DRAWN:
                childless[level][k] = value

    cotwin: dict[int, tuple[int, int, int]] = {}
    for mr in g.matings:
        for o in mr.offspring:
            if o.twin_group is not None:
                cotwin[o.child] = (mr.index, o.twin_group, o.twin_type)
    twins = [[0] * n[level] for level in range(nlev)]
    for level in range(nlev):
        for k in range(n[level] - 1):
            left, right = cotwin.get(cols[level][k]), cotwin.get(cols[level][k + 1])
            if left is not None and right is not None and left[0] == right[0] and left[1] == right[1]:
                twins[level][k] = left[2]

    fam = [[-1] * n[level] for level in range(nlev)]
    for level in range(1, nlev):
        for k, i in enumerate(cols[level]):
            par = g.parents.get(i)
            if not par:
                continue
            if len(par) == 1:
                (pa,) = par
                if g.level[pa] != level - 1:
                    raise DeferredFeatureError(  # pragma: no cover - guards against a placement bug
                        f"single parent of {g.label(i)!r} is not on the row above"
                    )
                fam[level][k] = colof[pa]
            else:
                pa, pb = par
                ca, cb = colof[pa], colof[pb]
                if g.level[pa] != level - 1 or g.level[pb] != level - 1 or abs(ca - cb) != 1:
                    # the order cannot express this child's descent: its parents are not an adjacent couple on
                    # the row above (a routed / >2-mate overflow mating that has offspring). Defer — descent
                    # from a non-adjacent parent pair is drawn by a later stage.
                    raise DeferredFeatureError(f"parents of {g.label(i)!r} are not an adjacent couple on the row above")
                fam[level][k] = min(ca, cb)

    founder_sibships: list[tuple[int, tuple[int, ...]]] = []
    for group in foundersib_groups:
        levels = {g.level[i] for i in group}
        if len(levels) != 1:
            raise DeferredFeatureError(  # pragma: no cover - guards against a placement bug
                f"founder sibship {[g.label(i) for i in group]} spans more than one row"
            )
        (lvl,) = levels
        founder_sibships.append((lvl, tuple(sorted(colof[i] for i in group))))

    return Layout(
        n=n,
        nid=cols,
        pos=pos,
        fam=fam,
        spouse=spouse,
        twins=twins,
        childless=childless,
        ghost_of=ghost_of,
        founder_sibships=founder_sibships,
        first_generation=first_generation,
        passthrough=passthrough,
    )
