"""Stored layouts: the ``Layout`` seam <-> the ``PedigreeLayout`` proto (docs/design/layout-store.md).

A stored layout is grus's computed layout of one pedigree, keyed so drawing can refuse it when stale. Cells are keyed by
identity built from real ``Position``s, never by index into ``Pedigree.individuals``, so the record is independent of
input order. Reading one back re-runs the layout's cheap front end (``_layout2._prepare``: validation, ghosts,
phantoms, pass-throughs; no ordering, no solve) and matches each stored cell to the front end's cell by key, which
yields the exact ``Layout`` a fresh layout would, synthetic indices included.
"""

from __future__ import annotations

import dataclasses
import hashlib

import protovalidate

from grus.models import layout_pb2 as lpb
from grus.models import pedigree_pb2 as pb
from grus.render import _geometry, _layout, _layout2, _xsolve

LAYOUT_VERSION = 1
"""The layout-algorithm version a stored layout records. Bump it in any change that alters a layout."""

# A cell's identity as a hashable key. The pass-through key carries its row: one descent crossing several rows has one
# pass-through per row, all keyed by the same first child.
_CellKey = tuple[str, *tuple[int, ...]]

_NO_CHILD = (0, 0)  # a childless ghost's first-child slot in its key (no Position is (0, 0))
_XSOLVER = {_xsolve.XSolver.Z3: lpb.X_SOLVER_Z3, _xsolve.XSolver.HIGHS: lpb.X_SOLVER_HIGHS}
_COUPLE_LINE = {1: lpb.COUPLE_LINE_SINGLE, 2: lpb.COUPLE_LINE_DOUBLE}
_SPOUSE = {v: k for k, v in _COUPLE_LINE.items()}


class StaleLayoutError(Exception):
    """A stored layout does not belong to the pedigree and geometry it is drawn with.

    Its key (layout-algorithm version, pedigree digest, layout-affecting geometry) differs, or its cells are not the
    cells the pedigree lays out. Recompute the layout; a stale record is never drawn.
    """


def pedigree_digest(p: pb.Pedigree) -> bytes:
    """SHA-256 of ``p``'s canonical serialization: independent of the order of its individuals and matings.

    Individuals sort by ``Position``, matings by their own serialized bytes; everything else, offspring (birth) order
    included, is kept as is. The whole pedigree is covered, not only what the layout reads today.
    """
    canon = pb.Pedigree()
    canon.CopyFrom(p)
    individuals = sorted(p.individuals, key=lambda ind: (ind.generation, ind.index))
    matings = sorted(p.matings, key=lambda m: m.SerializeToString(deterministic=True))
    del canon.individuals[:]
    del canon.matings[:]
    canon.individuals.extend(individuals)
    canon.matings.extend(matings)
    return hashlib.sha256(canon.SerializeToString(deterministic=True)).digest()


def layout_geometry(geom: _geometry.Geometry) -> lpb.LayoutGeometry:
    """The ``Geometry`` fields the layout reads; the rest are drawing-only."""
    return lpb.LayoutGeometry(
        couple_gap=geom.couple_gap,
        sib_gap=geom.sib_gap,
        label_size=geom.label_size,
        label_box_width=geom.label_box_width,
        x_unit=geom.x_unit,
        x_solver=_XSOLVER[geom.x_solver],
    )


def layout_key(p: pb.Pedigree, geom: _geometry.Geometry) -> lpb.LayoutKey:
    """The key a layout of ``p`` under ``geom`` by this grus carries."""
    return lpb.LayoutKey(
        algorithm_version=LAYOUT_VERSION,
        pedigree_digest=pedigree_digest(p),
        geometry=layout_geometry(geom),
    )


def store_layout(p: pb.Pedigree, geometry: _geometry.Geometry | None = None) -> lpb.PedigreeLayout:
    """Lay ``p`` out under ``geometry`` and record the result, a deferral included.

    Raises:
        grus.ir.ValidationError | grus.ir.IntegrityError: ``p`` is not a well-formed IR.
    """
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    try:
        lay = _layout2.layout(p, geom)
    except _layout.DeferredFeatureError as deferred:
        return lpb.PedigreeLayout(key=layout_key(p, geom), deferred=str(deferred))
    return to_proto(p, lay, geom)


def load_layout(
    p: pb.Pedigree, stored: lpb.PedigreeLayout, geometry: _geometry.Geometry | None = None
) -> _layout.Layout:
    """The ``Layout`` of ``p`` under ``geometry`` that ``stored`` records, after checking it is not stale.

    Raises:
        grus.ir.ValidationError: ``stored`` breaks a field rule of the layout schema.
        StaleLayoutError: ``stored`` was laid out by another layout-algorithm version, from different pedigree content,
            or under different layout-affecting geometry; or its cells are not the cells ``p`` lays out.
        DeferredFeatureError: ``stored`` records that the layout deferred ``p``, as a fresh layout would raise.
    """
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    protovalidate.validate(stored)
    _check_key(stored.key, p, geom)
    if stored.WhichOneof("outcome") == "deferred":
        raise _layout.DeferredFeatureError(stored.deferred)
    if stored.placement_digest != _digest(stored.placement):
        raise StaleLayoutError("stored placement does not match its digest: it was edited after grus wrote it")
    return from_proto(p, stored.placement, geom)


def _check_key(key: lpb.LayoutKey, p: pb.Pedigree, geom: _geometry.Geometry) -> None:
    if key.algorithm_version != LAYOUT_VERSION:
        raise StaleLayoutError(
            f"stored layout is from layout algorithm version {key.algorithm_version}; "
            f"this grus lays out with version {LAYOUT_VERSION}"
        )
    want = layout_geometry(geom)
    for field in lpb.LayoutGeometry.DESCRIPTOR.fields:
        stored_value, value = getattr(key.geometry, field.name), getattr(want, field.name)
        if stored_value != value:
            raise StaleLayoutError(f"stored layout has {field.name}={stored_value!r}; the geometry has {value!r}")
    if key.pedigree_digest != pedigree_digest(p):
        raise StaleLayoutError("stored layout is of different pedigree content (its digest does not match)")


def to_proto(p: pb.Pedigree, lay: _layout.Layout, geom: _geometry.Geometry) -> lpb.PedigreeLayout:
    """Record ``lay``, a layout of ``p`` under ``geom``, as a ``PedigreeLayout``."""
    keys = _cell_keys(p, _layout2._prepare(p))
    placement = lpb.Placement(first_generation=lay.first_generation)
    for level, row in enumerate(lay.nid):
        out = placement.rows.add()
        for k, idx in enumerate(row):
            cell = out.cells.add(x=lay.pos[level][k], lone=lay.lone[level][k])
            _set_identity(cell, keys[idx])
            if lay.fam[level][k] >= 0:
                cell.parent_column = lay.fam[level][k]
            if lay.spouse[level][k]:
                cell.couple_right = _COUPLE_LINE[lay.spouse[level][k]]
            if lay.twins[level][k]:
                cell.MergeFrom(lpb.Cell(twin_right=pb.ZygosityType.Name(lay.twins[level][k])))
            if lay.childless[level][k]:
                cell.MergeFrom(lpb.Cell(childless=pb.Childlessness.Name(lay.childless[level][k])))
    for level, cols in lay.founder_sibships:
        placement.founder_sibships.add(row=level, columns=cols)
    for rm in lay.routed:
        placement.routed.add(
            a=_ref(rm.a), b=_ref(rm.b), consanguineous=rm.consanguineous, children=[_ref(c) for c in rm.children]
        )
    return lpb.PedigreeLayout(key=layout_key(p, geom), placement=placement, placement_digest=_digest(placement))


def _digest(placement: lpb.Placement) -> bytes:
    return hashlib.sha256(placement.SerializeToString(deterministic=True)).digest()


def from_proto(p: pb.Pedigree, placement: lpb.Placement, geom: _geometry.Geometry) -> _layout.Layout:
    """The ``Layout`` a stored placement of ``p`` under ``geom`` records — equal to the fresh layout it was taken from.

    Verified, not trusted: the cells are exactly the cells ``p`` lays out, row for row; ``first_generation``; every
    relation (parent columns, couple, twin and childless flags, ``lone``, founder sibships, routed matings), rebuilt
    from the stored row order and x and required to equal the stored values; each row's x in order and every adjacent
    pair at least its separation (label clearance included); and no two sibships' bars overlapping. Trusted: that the
    order and the x are the ones grus's search and solve chose — a record edited within all of the above draws a
    correct figure of ``p``, though not grus's.

    Raises:
        StaleLayoutError: any check above fails.
    """
    prep = _layout2._prepare(p)
    g = prep.graph
    index = {key: idx for idx, key in _cell_keys(p, prep).items()}
    nid: list[list[int]] = []
    for level, row in enumerate(placement.rows):
        cells: list[int] = []
        for cell in row.cells:
            key = _identity(cell, level)
            idx = index.get(key)
            if idx is None:
                raise StaleLayoutError(f"row {level} holds {_describe(key)}, which this pedigree does not lay out")
            if g.level[idx] != level:
                raise StaleLayoutError(f"{_describe(key)} is on row {level}; this pedigree lays it out on another row")
            cells.append(idx)
        nid.append(cells)
    placed = [idx for row in nid for idx in row]
    if len(placed) != len(set(placed)):
        raise StaleLayoutError("a cell appears more than once")
    if len(placed) != len(index):
        missing = sorted(_describe(key) for key, idx in index.items() if idx not in set(placed))
        raise StaleLayoutError(f"cells this pedigree lays out are missing: {', '.join(missing)}")
    if len(nid) != (max(g.level) + 1 if g.level else 0):
        raise StaleLayoutError(f"{len(nid)} rows stored; this pedigree lays out {max(g.level) + 1}")
    _check_columns(placement)

    rows = [list(row.cells) for row in placement.rows]
    stored = _layout.Layout(
        n=[len(row) for row in nid],
        nid=nid,
        pos=[[c.x for c in row] for row in rows],
        fam=[[c.parent_column if c.HasField("parent_column") else -1 for c in row] for row in rows],
        spouse=[[_SPOUSE[c.couple_right] if c.HasField("couple_right") else 0 for c in row] for row in rows],
        twins=[[int(c.twin_right) for c in row] for row in rows],
        childless=[[int(c.childless) for c in row] for row in rows],
        ghost_of=prep.ghost_of,
        founder_sibships=[(fs.row, tuple(fs.columns)) for fs in placement.founder_sibships],
        routed=[
            _layout.RoutedMating(
                a=(rm.a.row, rm.a.column),
                b=(rm.b.row, rm.b.column),
                consanguineous=rm.consanguineous,
                children=tuple((c.row, c.column) for c in rm.children),
            )
            for rm in placement.routed
        ],
        first_generation=placement.first_generation,
        passthrough=prep.passthrough,
        phantom=prep.phantom,
        lone=[[c.lone for c in row] for row in rows],
    )
    _verify(p, prep, stored, geom)
    return stored


# Two neighbours may stand this much (layout units) closer than their separation: the quantum the layout rounds
# positions to, since rounding each of two positions can close their gap by up to one quantum.
_SEPARATION_SLACK = 10.0**-_layout2._POS_QUANTUM


def _verify(p: pb.Pedigree, prep: _layout2._Prepared, stored: _layout.Layout, geom: _geometry.Geometry) -> None:
    """Rebuild what the stored order and x determine and check what they must satisfy (see ``from_proto``)."""
    g = prep.graph
    if stored.first_generation != prep.first_generation:
        raise StaleLayoutError(
            f"stored first_generation is {stored.first_generation}; this pedigree's is {prep.first_generation}"
        )
    xpos = {idx: x for row, xs in zip(stored.nid, stored.pos, strict=True) for idx, x in zip(row, xs, strict=True)}
    try:
        built = _layout._build(
            g,
            xpos,
            prep.ghost_of,
            [mr.kids for mr in g.partnerless],
            first_generation=prep.first_generation,
            passthrough=prep.passthrough,
            phantom=prep.phantom,
        )
    except _layout.DeferredFeatureError as e:
        raise StaleLayoutError(f"the stored order cannot be drawn: {e}") from e
    if built.nid != stored.nid or built.pos != stored.pos:
        raise StaleLayoutError("a stored row is not in increasing x, or its leftmost cell is not at 0")
    built = dataclasses.replace(built, routed=_layout2._routed_matings(g, built, _routed_indices(g, stored)))
    for name in ("fam", "spouse", "twins", "childless", "lone", "founder_sibships", "routed"):
        if getattr(built, name) != getattr(stored, name):
            raise StaleLayoutError(f"the stored {name} relations are not the ones the stored order implies")
    seps = _layout2._row_seps(built, geom.couple_gap, geom.sib_gap, _layout2._label_clearance(p, built, geom))
    for level, xs in enumerate(stored.pos):
        for k in range(len(xs) - 1):
            gap, need = xs[k + 1] - xs[k], seps[level][k]
            if gap < need - _SEPARATION_SLACK:
                raise StaleLayoutError(f"cells {k} and {k + 1} on row {level} stand {gap:g} apart; they need {need:g}")
    if (why := _layout2._overlapping_sibships(g, built)) is not None:
        raise StaleLayoutError(f"the stored layout draws wrongly: {why}")


def _routed_indices(g: _layout._Graph, stored: _layout.Layout) -> frozenset[int]:
    """The matings a placement routes: the stored routed pairs, which must include every non-adjacent couple."""
    cell = {(level, k): idx for level, row in enumerate(stored.nid) for k, idx in enumerate(row)}
    col = {idx: (level, k) for (level, k), idx in cell.items()}
    by_pair: dict[frozenset[int], list[int]] = {}
    for mr in g.matings:
        if mr.b is not None:
            by_pair.setdefault(frozenset(mr.partners), []).append(mr.index)
    routed: set[int] = set()
    for rm in stored.routed:
        pair = frozenset((cell[rm.a], cell[rm.b]))
        if pair not in by_pair:
            raise StaleLayoutError("a stored routed mating joins two cells that are not partners")
        routed.update(by_pair[pair])
    for pair, matings in by_pair.items():
        (la, ka), (lb, kb) = sorted(col[i] for i in pair)
        adjacent = la == lb and kb == ka + 1
        if not adjacent and not set(matings) <= routed:
            raise StaleLayoutError("a couple whose partners are not adjacent is neither adjacent nor routed")
    return frozenset(routed)


def _cell_keys(p: pb.Pedigree, prep: _layout2._Prepared) -> dict[int, _CellKey]:
    """Every cell the front end lays out -> its identity key; raises if two cells would share one."""
    g = prep.graph
    n = len(p.individuals)
    keys: dict[int, _CellKey] = {i: ("individual", *_pos(p.individuals[i])) for i in range(n)}
    for ghost in prep.ghost_of:
        gk = g.ghost_key[ghost]
        keys[ghost] = ("ghost", *gk.real, *gk.partner, *(gk.first_child or _NO_CHILD), gk.occurrence)
    for cell in prep.phantom:
        parent = g.phantom_of[cell]
        (mating,) = g.matings_of[cell]
        first_child = p.matings[mating.index].offspring[0].child
        keys[cell] = ("phantom", *_pos(p.individuals[parent]), first_child.generation, first_child.index)
    for cell in prep.passthrough:
        keys[cell] = ("pass_through", g.level[cell], *_pos(p.individuals[g.passthrough_to[cell]]))
    if len(set(keys.values())) != len(keys):  # pragma: no cover - every key is unique by construction
        raise AssertionError("two cells of this pedigree share an identity")
    return keys


def _pos(ind: pb.Individual) -> tuple[int, int]:
    return (ind.generation, ind.index)


def _set_identity(cell: lpb.Cell, key: _CellKey) -> None:
    kind, *v = key
    if kind == "individual":
        cell.individual.CopyFrom(_position(v[0], v[1]))
    elif kind == "ghost":
        cell.ghost.real.CopyFrom(_position(v[0], v[1]))
        cell.ghost.partner.CopyFrom(_position(v[2], v[3]))
        if (v[4], v[5]) != _NO_CHILD:
            cell.ghost.first_child.CopyFrom(_position(v[4], v[5]))
        cell.ghost.occurrence = v[6]
    elif kind == "phantom":
        cell.phantom.parent.CopyFrom(_position(v[0], v[1]))
        cell.phantom.first_child.CopyFrom(_position(v[2], v[3]))
    else:  # a pass-through; its row (v[0]) is where it is stored
        cell.pass_through.first_child.CopyFrom(_position(v[1], v[2]))


def _position(generation: int, index: int) -> pb.Position:
    return pb.Position(generation=generation, index=index)


def _identity(cell: lpb.Cell, level: int) -> _CellKey:
    kind = cell.WhichOneof("identity")
    if kind == "individual":
        return ("individual", cell.individual.generation, cell.individual.index)
    if kind == "ghost":
        gh = cell.ghost
        first = _ppos(gh.first_child) if gh.HasField("first_child") else _NO_CHILD
        return ("ghost", *_ppos(gh.real), *_ppos(gh.partner), *first, gh.occurrence)
    if kind == "phantom":
        return ("phantom", *_ppos(cell.phantom.parent), *_ppos(cell.phantom.first_child))
    if kind == "pass_through":
        return ("pass_through", level, *_ppos(cell.pass_through.first_child))
    raise StaleLayoutError(f"a cell on row {level} has no identity")


def _ppos(pos: pb.Position) -> tuple[int, int]:
    return (pos.generation, pos.index)


def _describe(key: _CellKey) -> str:
    kind, *v = key
    if kind == "individual":
        return f"individual {v[0]}-{v[1]}"
    if kind == "ghost":
        child = "childless" if (v[4], v[5]) == _NO_CHILD else f"first child {v[4]}-{v[5]}"
        return f"the ghost of {v[0]}-{v[1]} marrying {v[2]}-{v[3]} ({child}, occurrence {v[6]})"
    if kind == "phantom":
        return f"the omitted partner of {v[0]}-{v[1]} (first child {v[2]}-{v[3]})"
    return f"the pass-through on row {v[0]} of the descent to {v[1]}-{v[2]}"


def _check_columns(placement: lpb.Placement) -> None:
    """Every column a placement names falls inside its row, so drawing never indexes past one."""
    widths = [len(row.cells) for row in placement.rows]

    def inside(level: int, col: int) -> bool:
        return 0 <= level < len(widths) and 0 <= col < widths[level]

    for level, row in enumerate(placement.rows):
        for k, c in enumerate(row.cells):
            if c.HasField("parent_column") and not inside(level - 1, c.parent_column):
                raise StaleLayoutError(f"cell {k} on row {level} names parent column {c.parent_column}, not a cell")
            last = k == len(row.cells) - 1
            if last and (c.HasField("couple_right") or c.HasField("twin_right") or c.HasField("childless")):
                raise StaleLayoutError(f"the last cell on row {level} is paired with a right-hand neighbour")
    for fs in placement.founder_sibships:
        if not all(inside(fs.row, col) for col in fs.columns):
            raise StaleLayoutError(f"a founder sibship on row {fs.row} names a column outside the row")
    for rm in placement.routed:
        if not all(inside(r.row, r.column) for r in (rm.a, rm.b, *rm.children)):
            raise StaleLayoutError("a routed mating names a cell outside the layout")


def _ref(cell: tuple[int, int]) -> lpb.CellRef:
    return lpb.CellRef(row=cell[0], column=cell[1])
