"""Pedigree layout: the constraint model (``layout``) — rank -> ordering -> block x-solve -> routed edges.

This is the layout half of the renderer (docs/design/layout-v2.md). ``_layout`` provides the shared front
end (validation, the avuncular ``ghost``, cross/loop detection, ranking by IR generation, pass-throughs for a
descent spanning rows) and the ``_build`` seam to the drawing arrays; ``_ordering`` provides the
crossing-minimizing within-rank order.
``layout`` wires them: prepare the graph, order each rank, ``_build`` the per-level arrays from that order,
then replace ``pos`` with the deterministic x-solve below and attach any routed matings.

The x-solve (``_x_model`` + ``_xsolve.solve``) chooses every x from one lexicographic linear program over the
fixed order: descent centring first, then even spacing, then compactness, then a fixed tie-break, subject to the
row separations and the rigid contiguity blocks (``_xsolve`` states the model and why it replaced iterative
sweeps). The final ``pos`` is quantised to a fixed grid. Design: docs/design/layout-v2.md; plan: docs/plans/28.
"""

from __future__ import annotations

import collections
import dataclasses
import itertools

from grus import ir
from grus.models import pedigree_pb2 as pb
from grus.render import _geometry, _layout, _ordering, _xsolve

_POS_QUANTUM = 6  # decimal places the final x is rounded to, so a floating backend's residual can't tip a
# rounding boundary — the goldens are byte-compared, so pos must be bit-stable across arch/compiler


@dataclasses.dataclass(frozen=True)
class _Sibship:
    """One drawn descent group: a couple's two cells (or a lone parent's one) and their children, left-to-right.

    Founder sibships (undrawn parents) are not ``_Sibship``s — they exert no centring pull, only the row's
    min-separation holds their members — so they never enter the solve's relation set.
    """

    parents: tuple[int, ...]
    children: tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class _Block:
    """A contiguity block on one row: a maximal run of columns held rigid, each at its min-separation from the next.

    The x-solve fixes every consecutive pair in a block at exactly its row separation, so the block moves as one
    body and never splits. A lone cell is a size-1 block.

    Attributes:
        cols: the block's column indices on its row, contiguous and left-to-right.
    """

    cols: tuple[int, ...]


def layout(p: pb.Pedigree, geometry: _geometry.Geometry | None = None) -> _layout.Layout:
    """Lay ``p`` out on the (level, x) grid; return the per-level arrays the drawing step reads.

    The v2 constraint model, in four stages: validate and prepare the graph (``_prepare`` — the avuncular
    ``ghost`` duplication, cross/loop detection, rows from IR generation, pass-throughs); order
    each rank by crossing minimization (``_ordering.order``); ``_build`` the per-level arrays (``fam`` /
    ``spouse`` / ``twins`` / founder sibships) from that order; then replace ``pos`` with the deterministic
    x-solve (``_x_model`` + ``_xsolve.solve``, quantised) and attach any routed matings.

    A consanguinity-loop mating is an ordinary adjacent (double-line) couple once the ordering pulls each
    partner to its sibship end, so boundary-bridge cross-joins and cousin marriages with siblings draw with
    zero crossings from the order alone. An individual with >2 matings overflows: the ordering keeps its two
    heaviest adjacencies and demotes the rest to :attr:`grus.render.Ordering.routed`, carried as a
    :class:`RoutedMating` for the drawing step to route as an orthogonal edge. An avuncular cross-generation
    join draws via the ``ghost`` (a same-row couple on the deeper partner's rank).

    Raises:
        grus.ir.ValidationError | grus.ir.IntegrityError: ``p`` is not a well-formed IR.
        DeferredFeatureError: a topology the model does not draw (see :class:`DeferredFeatureError`) — an
            interlocking loop, a child of more than one mating, a couple across generations other than an
            avuncular join, an order ``_build`` cannot express (a routed/overflow mating that *has* offspring),
            or a torn sibship whose descent bars would overlap.
    """
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    prep = _prepare(p)
    g = prep.graph

    ordering = _ordering.order(g, prep.cross)
    xpos = {i: float(k) for row in ordering.ranks for k, i in enumerate(row)}
    foundersib_groups = [mr.kids for mr in g.partnerless]
    built = _layout._build(  # a routed/overflow mating with offspring defers here
        g,
        xpos,
        prep.ghost_of,
        foundersib_groups,
        first_generation=prep.first_generation,
        passthrough=prep.passthrough,
    )

    routed = _routed_matings(g, built, ordering.routed)
    sibships = _relations(built)
    seps = _row_seps(built, geom.couple_gap, geom.sib_gap)
    blocks = _blocks(built, seps, sibships)
    x = _xsolve.solve(_x_model(built, seps, sibships, blocks, geom), geom.x_solver)
    origin = min((x[c] for row in built.nid for c in row), default=0.0)
    pos = [[round(x[c] - origin, _POS_QUANTUM) for c in row] for row in built.nid]
    result = dataclasses.replace(built, pos=pos, routed=routed)
    if _overlapping_sibships(g, result):
        raise _layout.DeferredFeatureError(
            "the ordering could not keep every sibship contiguous, so two sibships' descent bars overlap (a "
            "child would read as issue of several matings) — an interlocking loop/multi-mate shape; deferred"
        )
    return result


@dataclasses.dataclass(frozen=True)
class _Prepared:
    """The ranked graph the ordering and ``_build`` read, with what the front end derived on the way."""

    graph: _layout._Graph
    ghost_of: dict[int, int]
    cross: set[int]
    first_generation: int
    passthrough: frozenset[int]


def _prepare(p: pb.Pedigree) -> _Prepared:
    """Validate ``p`` and run the layout front end: ghosts, cross/loop detection, ranks, pass-throughs.

    Order matters: the ghost turns an avuncular join into a same-row couple before ``_rank`` checks that every
    couple shares a row, and pass-throughs are inserted after ranking, from the ranked rows.
    """
    ir.validate(p)
    g = _layout._derive(p)
    ghost_of = _layout._duplicate_cross_generation(g)
    cross = _layout._cross_matings(g)
    _layout._detect_loops(g, cross)
    first_generation = _layout._rank(g)
    passthrough = _layout._insert_passthroughs(g)
    return _Prepared(g, ghost_of, cross, first_generation, passthrough)


def _routed_matings(g: _layout._Graph, lay: _layout.Layout, routed: frozenset[int]) -> list[_layout.RoutedMating]:
    """Turn the ordering's routed mating indices into drawable ``RoutedMating``s in cell coordinates.

    Each routed mating is an overflow adjacency an individual with >2 matings could not keep — its two partners
    sit non-adjacent on the same rank. The partners' cells come from the built ``nid`` order (their columns).
    A routed mating with offspring never reaches here: ``_build`` cannot express its children's ``fam`` (parents
    non-adjacent) and raises, so ``layout`` has already deferred — ``children`` is therefore always empty, but
    is carried for a later stage that draws descent from a routed node.
    """
    if not routed:
        return []
    cellof = {i: (lvl, k) for lvl, row in enumerate(lay.nid) for k, i in enumerate(row)}
    out: list[_layout.RoutedMating] = []
    for mi in sorted(routed):
        mr = g.matings[mi]
        if mr.b is None:  # pragma: no cover - only two-partner matings overflow (a lone parent has no mate)
            continue
        out.append(
            _layout.RoutedMating(
                a=cellof[mr.a],
                b=cellof[mr.b],
                consanguineous=mr.consanguineous,
                children=tuple(cellof[k] for k in mr.kids if k in cellof),
            )
        )
    return out


def _overlapping_sibships(g: _layout._Graph, lay: _layout.Layout) -> bool:
    """Whether any two matings' child x-spans overlap on a row — the torn-sibship backstop.

    A cross-join order that pulls a partner with siblings to its mate can tear that sibship, stretching its sib
    bar until it overlaps another (a child reads as the issue of several matings). Ordering keeps sibships
    contiguous, so this backstop never fires on a drawable shape; ``layout`` defers rather than mislay out
    anything an order somehow tears (an interlocking loop/multi-mate shape).
    """
    xof = {i: lay.pos[level][k] for level, row in enumerate(lay.nid) for k, i in enumerate(row)}
    spans: dict[int, list[tuple[float, float]]] = collections.defaultdict(list)
    for mr in g.matings:
        kids = [k for k in mr.kids if k in xof]
        if not kids:
            continue
        xs = [xof[k] for k in kids]
        spans[g.level[kids[0]]].append((min(xs), max(xs)))
    for row in spans.values():
        row.sort()
        reach = float("-inf")
        for lo, hi in row:
            if lo < reach - 1e-9:
                return True
            reach = max(reach, hi)
    return False


def _relations(lay: _layout.Layout) -> list[_Sibship]:
    """Reconstruct the drawn descent groups from the Layout arrays (order-only; independent of the final ``pos``).

    Founder sibships carry no parent anchor, so they are omitted — the row's min-separation alone holds their
    members together at their fixed order.
    """
    out: list[_Sibship] = []
    for level in range(1, len(lay.nid)):
        groups: dict[int, list[int]] = collections.defaultdict(list)  # parent column on level-1 -> child columns
        for k in range(lay.n[level]):
            pc = lay.fam[level][k]
            if pc >= 0:
                groups[pc].append(k)  # k ascends, so children stay left-to-right
        for pc in sorted(groups):
            children = tuple(lay.nid[level][k] for k in groups[pc])
            if lay.spouse[level - 1][pc]:
                parents = (lay.nid[level - 1][pc], lay.nid[level - 1][pc + 1])
            else:
                parents = (lay.nid[level - 1][pc],)
            out.append(_Sibship(parents=parents, children=children))
    return out


def _couples(lay: _layout.Layout) -> list[tuple[int, int]]:
    """Each drawn couple as ``(left cell, right cell)``.

    Adjacent columns flagged by ``spouse`` (any mating, childless included).
    """
    return [
        (lay.nid[level][k], lay.nid[level][k + 1])
        for level in range(len(lay.nid))
        for k in range(lay.n[level] - 1)
        if lay.spouse[level][k]
    ]


def _row_seps(lay: _layout.Layout, couple_gap: float, sib_gap: float) -> list[list[float]]:
    """Hard min-separation between each adjacent pair on a row: ``couple_gap`` within a couple, else ``sib_gap``.

    Both are >= ``couple_gap``, so the non-overlap invariant holds; the tighter couple gap realises the soft
    couple-adjacency preference as a separation floor (couples read tighter than sibships, as in v1).
    """
    return [
        [couple_gap if lay.spouse[level][k] else sib_gap for k in range(lay.n[level] - 1)]
        for level in range(len(lay.nid))
    ]


def _x_model(
    lay: _layout.Layout,
    seps: list[list[float]],
    sibships: list[_Sibship],
    blocks: list[list[_Block]],
    geom: _geometry.Geometry,
) -> _xsolve.Model:
    """The x model for this order: row separations, block rigidity, descent groups, and hinge couples' stretch."""
    rows = lay.nid
    gaps = tuple((row[k], row[k + 1], seps[level][k]) for level, row in enumerate(rows) for k in range(len(row) - 1))
    # A bonded pair sits exactly its row separation apart. Taken from ``seps`` directly, not as a difference of the
    # block's cumulative float offsets, whose rounding can land an ulp below the separation and, under exact
    # arithmetic, contradict the pair's ``>= sep`` gap.
    rigid = tuple((rows[level][a], rows[level][b], seps[level][a]) for level, a, b in _block_pairs(blocks))
    bonded = {(rows[level][a], rows[level][b]) for level, a, b in _block_pairs(blocks)}
    couples = tuple(
        (left, right, geom.couple_gap, geom.sib_gap - geom.couple_gap)
        for left, right in _couples(lay)
        if (left, right) not in bonded
    )
    return _xsolve.Model(
        cells=tuple(c for row in rows for c in row),
        gaps=gaps,
        rigid=rigid,
        sibships=tuple((s.parents, s.children) for s in sibships),
        couples=couples,
    )


def _block_pairs(blocks: list[list[_Block]]) -> list[tuple[int, int, int]]:
    """Each adjacent pair of columns inside a block: ``(level, left col, right col)``."""
    return [(level, a, b) for level, row in enumerate(blocks) for blk in row for a, b in itertools.pairwise(blk.cols)]


def _blocks(lay: _layout.Layout, seps: list[list[float]], sibships: list[_Sibship]) -> list[list[_Block]]:
    """Partition each row into contiguity blocks — the rigid bodies the solve translates as units.

    A block is a maximal run of adjacent columns joined by a **cohesion bond**. Three relations bond:

    * an **ordinary couple** (``spouse`` flag) — but only when both partners mate exactly once. A multi-mate
      individual (a half-sib hinge, an overflow) is excluded, so its couples stay free and it spreads to centre
      over each of its sibships (``half_sibs``); this is the ordinary-vs-hinge distinction kept from the couple
      cohesion this generalizes.
    * **co-twins** (``twins`` flag) — a twin group never separates.
    * a **founder-sib floater**: a member of a partnerless mating's sibship that heads no descent (no drawn
      children) has no anchor of its own, so centring alone would not keep it beside an anchored sibling pulled
      away (c19's leaf ``1-1``). It bonds to one same-sibship neighbour — the left if present,
      else the right — so it travels with the group. A sibling that *does* head a subtree is already anchored
      by that subtree and is not bonded (over-rigidifying it would stop each subtree centring independently).

    Bonds are transitive through shared members: c19's leaf-founder / couple / founder-leaf chain fuses the
    whole gen-1 row into one block that translates to centre the couple over its children, the leaves riding
    along. Every column lands in exactly one block (a lone cell is a size-1 block).
    """
    anchored = {p for s in sibships for p in s.parents}  # a cell heading a drawn descent group is anchored
    mates: dict[int, int] = collections.defaultdict(int)
    for left, right in _couples(lay):
        mates[left] += 1
        mates[right] += 1
    fs_group: dict[tuple[int, int], int] = {}  # (level, column) -> founder-sibship id
    for gid, (level, cols) in enumerate(lay.founder_sibships):
        for c in cols:
            fs_group[(level, c)] = gid
    out: list[list[_Block]] = []
    for level in range(len(lay.nid)):
        ncols = lay.n[level]
        bond = [False] * max(ncols - 1, 0)  # bond[k]: columns k and k+1 share a block
        for k in range(ncols - 1):
            if lay.spouse[level][k]:
                left, right = lay.nid[level][k], lay.nid[level][k + 1]
                if mates[left] == 1 and mates[right] == 1:
                    bond[k] = True
            if lay.twins[level][k]:
                bond[k] = True
        for k in range(ncols):
            gid = fs_group.get((level, k))
            if gid is None or lay.nid[level][k] in anchored:
                continue  # not a founder-sib floater — anchored members position by their subtree
            if k > 0 and fs_group.get((level, k - 1)) == gid:
                bond[k - 1] = True
            elif k + 1 < ncols and fs_group.get((level, k + 1)) == gid:
                bond[k] = True
        out.append(_row_blocks(bond, ncols))
    return out


def _row_blocks(bond: list[bool], ncols: int) -> list[_Block]:
    """Cut a row into its bonded runs."""
    blocks: list[_Block] = []
    k = 0
    while k < ncols:
        j = k
        while j < ncols - 1 and bond[j]:
            j += 1
        blocks.append(_Block(cols=tuple(range(k, j + 1))))
        k = j + 1
    return blocks
