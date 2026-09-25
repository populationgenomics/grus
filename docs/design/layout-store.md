# Design: stored layouts (lay out once, draw many times)

**Status:** draft **Related:** [`renderer.md`](renderer.md) (the layout → drawing split this stores the middle of),
[`layout-v2.md`](layout-v2.md) (how the layout is computed, and why it is slow), [`ir.md`](ir.md) (the pedigree the
layout is of; `Position` identity), [`svg-output.md`](svg-output.md) (what drawing emits).

## Overview

Rendering a pedigree has two halves: **layout** (which row and x each individual gets, which cells are couples, where
each descent hangs) and **drawing** (symbols and lines from that grid). Layout is an ordering search plus an exact z3
linear program and takes seconds to minutes on a large pedigree; drawing takes milliseconds. A consumer that redraws the
same pedigree — restyled, at another raster size, with changed colours — pays the layout every time.

A **stored layout** is a protobuf record of grus's own computed layout of one pedigree: the whole seam drawing reads,
not just positions. It is computed once and saved; drawing from it skips the layout entirely and produces the **same
bytes** a fresh render would. It is a read-only cache: it carries a key (a content hash of the pedigree, the
layout-affecting geometry, and a layout-algorithm version), and drawing from a layout whose key does not match the
pedigree and geometry in hand fails rather than drawing something stale.

## Background

The seam between the halves is the in-memory `Layout` in [`grus/render/_layout.py`](../../grus/render/_layout.py):
per-row lists of cells, each cell an individual with an x, plus the relations drawing needs — each child's parent
column, couple flags with the right-hand neighbour, childlessness, twin groups by membership, founder sibships, the
generation drawn on row 0, and per-child `lone` flags (a child that descends from one drawn parent rather than the
couple its column heads). Drawing reads nothing else from the layout step; everything it needs about an individual's
appearance it reads from the pedigree itself.

Some cells are not individuals of the pedigree. The layout front end adds three kinds of **synthetic cell**:

- a **ghost** duplicates a real individual on a lower row so an avuncular (cross-generation) marriage is a same-row
  couple; drawing renders the real individual's symbol there with a dashed "same individual" link;
- a **pass-through** stands on each row a descent crosses when children sit more than one generation below their
  parents; drawing emits only the line through it;
- a **phantom partner** is the omitted other parent of a lone-parent sibship, so the descent drops from a marriage line
  to nothing; drawing emits the line and no symbol.

In memory every cell is an index into `Pedigree.individuals`, synthetic cells taking indices past the end. That index
depends on the order individuals appear in the input, which the IR does not give meaning to. The layout itself is
invariant to that order — its every tie-break is on `Position` — so a shuffle of the input changes the indices and not
the drawing.

Positions are already quantised to 1e-6 layout units before drawing sees them, so they survive a round trip through a
`double` exactly.

## Non-goals

- **Hand-edited layouts.** A stored layout is grus's output, never an input a person authors. See Open questions.
- **The source figure's layout.** Bounding boxes of the symbols in an extracted figure are a different thing — where the
  paper drew each person, not where grus would. They would share nothing with this record but the pedigree; an `origin`
  field is left as the extension point if they are ever stored beside it.
- **Drawing geometry.** Elbow tracks (where a drop that misses its sib bar turns) are derived by drawing from positions
  alone, deterministically, so they are not stored; nor is anything pixel-level.
- **Cross-version reuse.** A layout computed by one layout algorithm is not drawn under another; see Staleness.

## Design

### What is stored

The record is the `PedigreeLayout` message in
[`schema/proto/grus/models/layout.proto`](../../schema/proto/grus/models/layout.proto). It holds a key and one outcome:

- a **placement** — the rows of cells, left to right, each with its identity, x, parent column, `lone` flag and its
  couple / childless relation to its right-hand neighbour; the twin groups and founder sibships in (row, column) terms;
  and the first generation. This is `Layout` field for field, so converting in either direction is lossless.
- or a **deferral** — the reason the layout declined to draw the pedigree (`DeferredFeatureError`), so a figure render
  can draw its placeholder tile without repeating a search that ends in the same refusal.

Columns are positions within the stored rows, which is the layout's own coordinate system and does not depend on input
order. The record never mentions an index into `Pedigree.individuals`.

### Cells are keyed by identity

A cell names what it draws, not where that sits in the input:

- a real individual by its `Position`;
- a ghost by the marriage it exists for: the real individual it duplicates, the partner it marries, that marriage's
  first child, and a rank by content among repeated marriages of the same pair (an uncle and niece with two matings have
  two ghosts). The layout breaks its ordering ties on the same key, so a ghost's place does not depend on input order
  either;
- a phantom by the lone parent and the first child of the sibship it partners;
- a pass-through by the first child of the descent it carries; its row gives its generation.

Every key is built from real `Position`s, so the record passes the same field rules as the IR. Reading a stored layout
back re-runs the cheap front end of the layout (validation, ghosts, phantoms, pass-throughs — no ordering, no solve),
matches each stored cell to the front end's cell by key, and requires the two cell sets to be identical row for row.
That gives back the exact in-memory `Layout` a fresh layout would, synthetic indices included, and a second, structural
check that the record belongs to this pedigree.

Because nothing is keyed by input order and the layout is order-invariant, two inputs that differ only in the order of
their individuals or matings produce byte-identical records.

### Staleness: the key

A stored layout is valid for a pedigree and a geometry only if a fresh layout would produce it. The key captures every
input to the layout:

- **a content digest of the pedigree** — SHA-256 over a canonical serialization: individuals sorted by `Position`,
  matings sorted by their own serialized bytes, the whole message serialized deterministically. Offspring order, which
  the IR gives meaning (birth order), is kept. The digest covers the whole pedigree, not only the fields the layout
  reads today: annotations change label widths, which change spacing, and tracking which fields matter would be one more
  thing every layout change must keep in step. A change to a title then invalidates a layout it did not affect; that
  costs one recomputation and never a wrong drawing.
- **the layout-affecting geometry**, stored as values so a mismatch can say which: `couple_gap` and `sib_gap` (the row
  separations), `label_size`, `label_box_width`, `label_gap` and `x_unit` (label clearance is computed in pixels and
  converted to layout units, and a label beside a centre drop reserves `label_gap`), `symbol_size` and `carrier_style`
  (a count mark's size and reach scale with the symbol, and a carrier's dot sends a count outside it), and `x_solver`
  (the HiGHS backend agrees with z3 only up to the position quantum, so the same key must mean the same solver).
  Everything else in `Geometry` — row height, stubs, margins, the vertical label rhythm — is read only by drawing and
  may vary freely. A test requires every `Geometry` field the layout modules read to be in the key, so a field that
  starts to affect spacing cannot be left out.
- **the layout-algorithm version**, an integer grus bumps whenever a change alters any layout. A test pins a digest of
  every golden's stored layout, under each x-solver, to the current version, so a change that moves a golden's layout
  without a bump fails. Each pin, per golden and solver, holds the golden's pedigree digest and the placement's digest.
  A placement may change only together with its pedigree digest (the golden's IR was edited); a changed placement under
  an unchanged digest is a layout change and takes a new version, and a pin whose golden is gone fails until removed. A
  layout change that touches no golden is not caught; the goldens are the coverage.

Drawing from a stored layout checks all three and raises on the first mismatch, naming it. There is no fallback to a
fresh layout: a caller who wants recompute-on-stale does it explicitly, and a silent recompute would hide a cache that
never hits.

### Reading a record back: what is checked

A stored layout is a cache grus writes itself, checked for staleness and internal consistency, not authenticated: anyone
who can edit it can edit the pedigree. Before drawing, the loader checks, in order:

1. the schema's field rules (protovalidate);
1. the key: algorithm version, layout-affecting geometry, pedigree digest;
1. the cells: exactly the cells the pedigree lays out, each on its row, none twice;
1. `first_generation`, against the pedigree;
1. every relation that the row order and x determine — parent columns, couple and childless flags, `lone`, twin groups,
   founder sibships — rebuilt by the same code the layout uses and required to equal the stored values; every couple's
   partners must be adjacent;
1. what the order and x must satisfy: each row in increasing x from 0, every adjacent pair at least its separation
   (label clearance included, less the position quantum), and no two sibships' bars overlapping.

These catch a stale record and a bug in grus's own serializer or in a version change, cheaply; they do not re-run the
ordering search or the x-solve, so they do not establish that the order and x are the ones grus would choose.

### Exactness

Drawing from a stored layout is byte-identical to `render_svg` on the same pedigree and geometry. This follows from the
lossless conversion (the drawing step receives an equal `Layout`) and is pinned by a test that round-trips every golden.

### Interfaces

- **Library.** A function lays a pedigree out and returns its `PedigreeLayout`; the render entry points take an optional
  stored layout and draw from it after checking the key. Entry points live in
  [`grus/render/__init__.py`](../../grus/render/__init__.py).
- **CLI.** `grus layout` writes the stored layout of an IR file (one record per pedigree for a set);
  `grus render --layout` draws from it.

### Evolution

The schema evolves additively like the IR's: fields and enum members are added, never renumbered or repurposed, so a
record written by an older grus still parses. Whether it still *draws* is the version's call: a grus whose layout
differs refuses the old record as stale, and a grus that only adds fields for a new layout feature draws old records
unchanged.

Version 2 records twin groups by membership (`Placement.twin_groups`), since a partner may stand between co-twins
([`layout-v2.md`](layout-v2.md), Twins with partners); the per-cell `Cell.twin_right` it replaced is no longer written,
and a record that sets it is refused. Version 2 also defers a mating whose partners cannot stand side by side, so
`Placement.routed` is no longer written either, and a record that sets it is refused.

## Alternatives considered

- **Store positions only** (x per `Position`) and recompute the rest. The relations are cheap to derive given the order,
  but deriving them means re-running most of `_build`, and the synthetic cells need positions too, so the record would
  have to name them anyway. Storing the whole seam keeps drawing a pure function of the record and the pedigree, and
  makes the round-trip test a plain equality.
- **Store the ordering and re-solve x.** Smaller, and robust to a change in the solve's tie-break. Rejected: the solve
  is the slow half on large pedigrees, which is the cost this exists to remove.
- **Key cells by index into `Pedigree.individuals`.** Simplest to write and read. Rejected: the index is input order,
  which the IR does not give meaning to; a re-serialized or re-imported pedigree would scramble the layout.
- **Key a ghost by the real individual plus an ordinal**, as its SVG id does (`ghost-II-1-2`). The ordinal is assigned
  in cell order, so it is a property of the layout, not of the pedigree: reading the record back could not tell which of
  two ghosts of one individual marries whom without trusting the stored adjacency. The marriage key names the marriage
  the ghost exists for.
- **Store the front end's synthetic `Position`s verbatim** (phantoms and pass-throughs have stable negative indices).
  Rejected: negative indices fail the IR's field rules, and the encoding is an implementation detail a version bump
  would have to track; semantic keys state what the cell is.
- **Hash a layout-relevant projection of the pedigree.** Fewer spurious invalidations. Rejected: it has to track every
  field any future layout reads, and a missed field is a silently stale drawing — the one failure this key exists to
  prevent.
- **Recompute on a stale key.** Friendlier for a caller who does not care. Rejected: it turns a cache bug into a slow
  path nobody notices; a caller can catch the error and recompute.

## Open questions

- **Serialization stability of the digest.** Deterministic protobuf serialization is stable in practice for messages
  without maps, but protobuf does not promise it across releases. If a protobuf upgrade changed it, every stored layout
  would read as stale — safe, but a mass recomputation. A hand-rolled canonical encoding would remove the dependency at
  the cost of maintaining it as the IR grows.
- **Hand edits.** Letting a person adjust a stored layout (nudge a cell, swap siblings) would reuse the consistency
  checks above as the validator, and need a notion of a layout that is valid without being grus's own output — for
  instance an `origin` field saying who placed it.
