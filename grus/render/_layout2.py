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
from grus.render import _geometry, _labels, _layout, _ordering, _xsolve

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
        phantom=prep.phantom,
    )

    routed = _routed_matings(g, built, ordering.routed)
    sibships = _relations(built)
    seps = _row_seps(
        built, geom.couple_gap, geom.sib_gap, _label_clearance(p, built, geom), _count_clearance(p, built, geom)
    )
    blocks = _blocks(built, seps, sibships)
    model = _x_model(built, seps, sibships, blocks, geom)
    x = _xsolve.solve(model, geom.x_solver)
    for _ in range(_APART_PASSES):  # keep each drop that misses its bar out of other families' child columns
        extra = _apart(built, sibships, x, model.apart)
        if not extra:
            break
        model = dataclasses.replace(model, apart=model.apart + extra)
        x = _xsolve.solve(model, geom.x_solver)
    origin = min((x[c] for row in built.nid for c in row), default=0.0)
    pos = [[round(x[c] - origin, _POS_QUANTUM) for c in row] for row in built.nid]
    result = dataclasses.replace(built, pos=pos, routed=routed)
    if (why := _overlapping_sibships(g, result)) is not None:
        raise _layout.DeferredFeatureError(f"{why}; deferred")
    return result


@dataclasses.dataclass(frozen=True)
class _Prepared:
    """The ranked graph the ordering and ``_build`` read, with what the front end derived on the way."""

    graph: _layout._Graph
    ghost_of: dict[int, int]
    cross: set[int]
    first_generation: int
    passthrough: frozenset[int]
    phantom: frozenset[int]


def _prepare(p: pb.Pedigree) -> _Prepared:
    """Validate ``p`` and run the layout front end: ghosts, cross/loop detection, ranks, phantoms, pass-throughs.

    Order matters: the ghost turns an avuncular join into a same-row couple before ``_rank`` checks that every
    couple shares a row, and pass-throughs are inserted after ranking, from the ranked rows.
    """
    ir.validate(p)
    g = _layout._derive(p)
    ghost_of = _layout._duplicate_cross_generation(g)
    cross = _layout._cross_matings(g)
    _layout._detect_loops(g, cross)
    first_generation = _layout._rank(g)
    phantom = _layout._insert_phantoms(g)
    passthrough = _layout._insert_passthroughs(g)
    return _Prepared(g, ghost_of, cross, first_generation, passthrough, phantom)


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
    out.sort(key=lambda rm: (rm.a, rm.b))  # layout order, not the input's mating order
    return out


def _overlapping_sibships(g: _layout._Graph, lay: _layout.Layout) -> str | None:
    """Why the drawing would misstate a descent, or ``None``: the backstop against a silently wrong figure.

    Drawing reads sibships from ``fam`` and draws each as one bar from its children to its drop (the parents'
    midpoint, or a lone parent's x), so two failures read as a false family:

    * **a mixed group**: a drawn group whose children are not exactly one mating's, drawn from that mating's
      partners. ``fam`` records only the parent's column; phantom partners give each lone-parent mating its own
      couple, so this is a layout bug the check keeps from reaching a figure.
    * **overlapping bars**: two groups on a row whose children's spans overlap (a torn sibship), so a child
      would read as the issue of several matings. A drop beside its children does not extend its bar: it turns
      at its own elbow track above the bars (``_draw._sibship``), so only the children's spans can meet.

    ``layout`` defers on either rather than draw it.
    """
    mating_of = {k: mi for mi, mr in enumerate(g.matings) if not mr.partnerless for k in mr.kids}
    xof = {i: lay.pos[level][k] for level, row in enumerate(lay.nid) for k, i in enumerate(row)}
    bars: dict[int, list[tuple[float, float]]] = collections.defaultdict(list)
    for s in _relations(lay):
        matings = {mating_of.get(c) for c in s.children}
        mi = next(iter(matings))
        if len(matings) != 1 or mi is None or set(g.matings[mi].partners) != set(s.parents):
            return "a lone parent's sibships would draw as one family, or from the wrong parents"
        xs = [xof[c] for c in s.children]
        bars[g.level[s.children[0]]].append((min(xs), max(xs)))
    for row in bars.values():
        row.sort()
        for (_, hi), (lo, _) in itertools.pairwise(row):
            if lo <= hi + 1e-9:
                return "two sibships' sib bars would overlap and read as one family"
    return None


def _relations(lay: _layout.Layout) -> list[_Sibship]:
    """Reconstruct the drawn descent groups from the Layout arrays (order-only; independent of the final ``pos``).

    Founder sibships carry no parent anchor, so they are omitted — the row's min-separation alone holds their
    members together at their fixed order.
    """
    out: list[_Sibship] = []
    for level in range(1, len(lay.nid)):
        groups: dict[tuple[int, bool], list[int]] = collections.defaultdict(list)  # (parent column, lone) -> cols
        for k in range(lay.n[level]):
            pc = lay.fam[level][k]
            if pc >= 0:
                groups[(pc, lay.descends_from_one(level, k))].append(k)  # k ascends: children stay left-to-right
        for pc, lone in sorted(groups):
            children = tuple(lay.nid[level][k] for k in groups[(pc, lone)])
            heads = (pc,) if lone else (pc, pc + 1)
            parents = tuple(lay.nid[level - 1][c] for c in heads)
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


# A drop that misses its bar stands at least this far (layout units, a third of the sibling gap) from another family's
# child, so its vertical cannot be read as landing on that child; at most this many passes add such constraints.
_APART_CLEAR = 0.5
_APART_PASSES = 4


def _apart(
    lay: _layout.Layout,
    sibships: list[_Sibship],
    x: dict[int, float],
    held: tuple[tuple[tuple[int, ...], int, int, float], ...],
) -> tuple[tuple[tuple[int, ...], int, int, float], ...]:
    """The keep-apart constraints ``x`` still violates.

    Each drop that misses its bar, against each other family's child on its row (a pass-through included) standing
    within ``_APART_CLEAR`` of it.

    The side is the one ``x`` already has, or on an exact tie the side toward the drop's own bar, so the constraint
    is linear and moves the drop the short way. Constraints only accumulate across passes, so a side once chosen holds.
    """
    have = {(parents, cell) for parents, cell, _, _ in held}
    out: list[tuple[tuple[int, ...], int, int, float]] = []
    level_of = {c: level for level, row in enumerate(lay.nid) for c in row}
    for s in sibships:
        drop = sum(x[p] for p in s.parents) / len(s.parents)
        xs = [x[c] for c in s.children]
        if min(xs) - 1e-9 <= drop <= max(xs) + 1e-9:
            continue  # meets its own bar
        toward = 1 if drop > max(xs) else -1  # +1: its bar is to the left, so moving left (drop - x_c < 0) is toward
        own = set(s.children)
        for c in lay.nid[level_of[s.children[0]]]:
            if c in own or c in lay.phantom or lay.fam[level_of[c]][lay.nid[level_of[c]].index(c)] < 0:
                continue  # only another family's child (a married-in partner has no line above it)
            if (s.parents, c) in have or abs(drop - x[c]) >= _APART_CLEAR - 1e-9:
                continue
            side = (1 if drop > x[c] else -1) if abs(drop - x[c]) > 1e-9 else -toward
            out.append((s.parents, c, side, _APART_CLEAR))
    return tuple(out)


def _label_clearance(p: pb.Pedigree, lay: _layout.Layout, geom: _geometry.Geometry) -> dict[int, tuple[float, float]]:
    """Each cell's label reach left and right in layout units: the stack's reach plus half the gap between labels.

    Two neighbours need the left cell's right reach plus the right cell's left reach between centres for their
    label stacks not to touch (``(w_left + w_right) / 2 + label_size`` for two centred stacks). A stack set beside
    its own-centre drop (``_labels.label_reach``) reaches to one side only, and a count beside its symbol reaches
    right. A pass-through or phantom draws no label and needs none; a ghost reserves its real individual's label.

    A count beside the symbol is on the symbol's row, so it must also clear the right neighbour's *symbol*, whose
    label may be narrower than it: ``_row_seps`` adds that as its own floor (``_count_clearance``).
    """
    out: dict[int, tuple[float, float]] = {}
    for level, row in enumerate(lay.nid):
        for k, c in enumerate(row):
            if c in lay.passthrough or c in lay.phantom:
                out[c] = (0.0, 0.0)
                continue
            ind = p.individuals[lay.ghost_of.get(c, c)]
            left, right = _labels.label_reach(ind, geom, side=lay.label_side(level, k))
            out[c] = ((left + geom.label_size / 2) / geom.x_unit, (right + geom.label_size / 2) / geom.x_unit)
    return out


def _count_clearance(p: pb.Pedigree, lay: _layout.Layout, geom: _geometry.Geometry) -> dict[int, float]:
    """Each cell with a count beside its symbol -> the centre separation, in layout units, that clears the next symbol.

    The count's reach right of centre, then half the label gap, then the neighbour's symbol half.
    """
    out: dict[int, float] = {}
    for row in lay.nid:
        for c in row:
            if c in lay.passthrough or c in lay.phantom:
                continue
            if reach := _labels.outside_count_reach(p.individuals[lay.ghost_of.get(c, c)], geom):
                out[c] = (reach + geom.label_size / 2 + geom.symbol_size / 2) / geom.x_unit
    return out


def _row_seps(
    lay: _layout.Layout,
    couple_gap: float,
    sib_gap: float,
    clearance: dict[int, tuple[float, float]] | None = None,
    count_clearance: dict[int, float] | None = None,
) -> list[list[float]]:
    """Hard min-separation between each adjacent pair on a row.

    ``couple_gap`` within a couple, else ``sib_gap``, raised where the two cells' labels would otherwise collide
    (``clearance``, in layout units) or a count beside the left cell's symbol would reach the right cell's
    (``count_clearance``). Both floors are >= ``couple_gap``, so the non-overlap invariant holds; the
    tighter couple gap realises the soft couple-adjacency preference as a separation floor (couples read tighter
    than sibships, as in v1). The label clearance is a separation, not a drawing-time widening, so the solve's
    centring holds in pixels.
    """
    reach = clearance or {}
    count = count_clearance or {}
    return [
        [
            max(
                couple_gap if lay.spouse[level][k] else sib_gap,
                reach.get(lay.nid[level][k], (0.0, 0.0))[1] + reach.get(lay.nid[level][k + 1], (0.0, 0.0))[0],
                count.get(lay.nid[level][k], 0.0),
            )
            for k in range(lay.n[level] - 1)
        ]
        for level in range(len(lay.nid))
    ]


def _x_model(
    lay: _layout.Layout,
    seps: list[list[float]],
    sibships: list[_Sibship],
    blocks: list[list[_Block]],
    geom: _geometry.Geometry,
) -> _xsolve.Model:
    """The x model for this order: row separations, block rigidity, descent groups, and hinge couples' stretch.

    A line to an omitted partner (a phantom) has no stretch cost; see below.
    """
    rows = lay.nid
    gaps = tuple((row[k], row[k + 1], seps[level][k]) for level, row in enumerate(rows) for k in range(len(row) - 1))
    # A bonded pair sits exactly its row separation apart. Taken from ``seps`` directly, not as a difference of the
    # block's cumulative float offsets, whose rounding can land an ulp below the separation and, under exact
    # arithmetic, contradict the pair's ``>= sep`` gap.
    rigid = tuple((rows[level][a], rows[level][b], seps[level][a]) for level, a, b in _block_pairs(blocks))
    bonded = {(rows[level][a], rows[level][b]) for level, a, b in _block_pairs(blocks)}
    # A line to an omitted partner (a phantom) has no partner symbol to hold near, so its length costs nothing
    # here: it stretches as far as centring its drop needs, and compactness (levels 2-3) keeps it no longer.
    couples = tuple(
        (left, right, geom.couple_gap, geom.sib_gap - geom.couple_gap)
        for left, right in _couples(lay)
        if (left, right) not in bonded and left not in lay.phantom and right not in lay.phantom
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

    * an **ordinary couple** (``spouse`` flag) — but only when both partners mate exactly once and neither is
      bonded to anyone else. A multi-mate individual (a half-sib hinge, an overflow) is excluded, so its couples
      stay free and it spreads to centre over each of its sibships (``half_sibs``). So is a couple a partner of
      which is a co-twin or a founder-sib floater: bonded, the couple would join that group into one rigid chain
      (twins who both marry lock spouse, twin, twin, spouse together), and the chain's drops could not spread to
      reach their own children, whose bars then met. Free, it stretches at the hinge couples' cost.
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
        other = list(bond)  # the twin and floater bonds, before any couple joins them
        for k in range(ncols - 1):
            if lay.spouse[level][k] and not other[k]:
                left, right = lay.nid[level][k], lay.nid[level][k + 1]
                alone = not (k > 0 and other[k - 1]) and not (k + 1 < ncols - 1 and other[k + 1])
                if mates[left] == 1 and mates[right] == 1 and alone:
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
