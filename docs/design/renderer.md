# Design: the renderer (IR → SVG)

**Status:** current **Related:** [`ir.md`](ir.md) (input contract), [`svg-output.md`](svg-output.md) (how the drawing is
organised in the document: groups, ids and data attributes for interactive use), `architecture.md` (why deterministic).
Implementable spec: `../plans/03-renderer-tier1.md`.

## Overview

Deterministic IR → SVG. Two parts: **layout** (assign each individual a generation row and an x position) and
**drawing** (emit Bennett-standard symbols and connecting lines from that grid). No LLM. Layout is the v2 constraint
model (ranks fixed, x from a deterministic solve over soft pedigree constraints, every relationship a routed edge — full
spec in [`layout-v2.md`](layout-v2.md)); this doc summarises it and specifies the drawing.

## Background

"Well laid out" is the hard core of the project. Pedigree layout is a specialized graph-drawing problem — generational
ranking, keeping couples adjacent, centering children under parents, minimizing line crossings. v1 ported the kinship2
`align.pedigree` recursive nuclear-family algorithm, but its accreted special-case passes could not draw common
consanguineous / multi-mate shapes; layout is now the **constraint model** in [`layout-v2.md`](layout-v2.md) (Sugiyama's
coordinate phase specialised with pedigree constraints), which draws them in place. We own the SVG emission either way,
so we control glyph-level convention fidelity — the thing the eval judge scores.

## Non-goals

- No interactivity, animation, or theming in the output itself. One conventional, stable, reproducible layout per IR;
  the hooks a consumer builds interaction on are the separate contract of [`svg-output.md`](svg-output.md).
- Not a general graph-drawing engine; the algorithm assumes pedigree structure.

## Design

### Own the emission

We do **not** adopt an existing renderer as a black box: the judge compares *convention fidelity* (proband arrow,
consanguinity double line, MZ-twin bar, deceased slash, carrier dot), so we need glyph-level control that
pedigreejs/Madeline do not cheaply give. We own the SVG emission (below) and drive it from an internal layout — v1
ported kinship2's `align.pedigree` recursion; layout is now the constraint model in [`layout-v2.md`](layout-v2.md),
reimplemented from published graph-drawing descriptions, not vendored source (kinship2 is GPL/LGPL, Madeline GPL; grus
is MIT).

### Layout (constraint model)

Full spec in [`layout-v2.md`](layout-v2.md); the shape drawing depends on:

1. **Generation ranks (hard)** — each individual's row is its IR generation, the row the figure draws it on. A descent
   spanning several rows passes through a cell on each row it crosses. An avuncular (cross-generation) join is drawn by
   duplicating the shallower partner as a **ghost** on the deeper row; interlocking consanguinity loops are detected and
   deferred.
1. **Ordering** — a deterministic left→right order per rank by weighted-median + transposition crossing minimization
   (dot's `mincross`), couples and twin groups kept contiguous as adjacency atoms. A same-generation cross-lineage /
   loop marriage becomes an ordinary adjacent couple once ordering pulls each partner to its sibship end; an individual
   with >2 matings keeps its two heaviest adjacencies and **routes** the rest.
1. **x-coordinate solve** — from the fixed order, one lexicographic linear program: every descent on its own sib bar,
   then centring (with a half-sibling hinge spreading at most `sib_gap`), then even spacing, then compactness, subject
   to hard non-overlap. Contiguity **blocks** (an ordinary couple, a twin group, a founder-sib run) are rigid, so they
   never split. The default backend (z3) solves over exact rationals, so positions are identical on every platform;
   HiGHS is an optional alternative. Non-overlap is always satisfiable in 1-D, so layout rarely fails.
1. **Line routing** — from the `(level, x)` grid, draw symbols and connectors (below), including a routed orthogonal
   polyline for each non-adjacent (overflow) mating.

**Layout ↔ drawing interface** — per-level parallel arrays (kinship2's shape): for each level, `n` (cell count), `nid`
(individual row index per column), `pos` (x), `fam` (parent-couple column on the level above), `spouse` (0 /
adjacent-spouse / adjacent-spouse-with-double-line — the last set straight from the explicit `Mating.consanguineous`
flag, never inferred), `twins` (0 / MZ / DZ / unknown, with the sib to the right), `childless` (0 / by-choice /
infertility on the couple's left column, from `Mating.childlessness`), plus `founder_sibships` (parentless sib groups),
`ghost_of` (a ghost cell → the real individual it duplicates), and `routed` (matings drawn as routed edges rather than
adjacent straight lines). Drawing reads only these arrays.

### Drawing (Bennett symbols)

Map grid to pixels (`px = X0 + GEN_MARKER_GUTTER + x·X_UNIT`, `py = Y0 + (level-1)·GEN_HEIGHT`). Symbol by gender (□ ○
◇); affected = solid fill; a **carrier** by the `Geometry.carrier_style` render option (*not* IR meaning — both styles
read the same `Condition.inheritance` + condition legend). `CarrierStyle.INHERITANCE_GLYPH` (default, matching the
existing literature): X-linked → central dot, else a region fill. `CarrierStyle.PARTITION_FILL` (NSGC 2022 §4.5, dot
retired): every carrier is a region fill regardless of inheritance. A region fill is a rectangle clipped to the shape
via a per-symbol `clipPath`, so one code path covers □ ○ ◇; the region is keyed to the carried condition's index in the
pedigree's condition legend (distinct names, phenotype labels first) — so two carriers of *different* named variants
fill *opposite* halves (a compound het reads as opposite sides), while a single or unnamed carrier is the plain left
half (2-split; quadrants for 3–4 conditions). Plus deceased slash and proband arrow. Connectors: **mating line**
(horizontal between partners; doubled for consanguinity — the double line is emitted iff `spouse==2`, which is set only
from the explicit `Mating.consanguineous` flag, for every adjacent couple including founders), **descent/sibship line**
(vertical drop from the mating midpoint → horizontal sib bar → per-child stubs), a **founder sibship**'s implied hanger
(a partnerless mating: no parent cell, so the sib bar hangs from a short vertical stub rising to a point instead of a
descent drop), **twins** (child stubs converge to one point; MZ adds a joining bar), and the **childless glyph** (a
couple with no offspring: a stub from the mating midpoint down to a short horizontal bar — one bar for
`CHILDLESSNESS_BY_CHOICE`, two parallel bars for `CHILDLESSNESS_INFERTILITY` — drawn instead of a descent).

Not yet drawn (extracted and diffed, but no glyph): relationship `status` (separation / divorce slashes on the mating
line) and multi-`Condition` partition fills (an individual affected by several *named* conditions → quadrant shading;
the single-condition affected/carrier fills above are drawn, the multi-region case is not).

**Label stack.** Under each symbol, a centred vertical stack of text lines, built per individual as an ordered
`[local_id, *annotation_texts]` with empties **and duplicates** dropped (first occurrence wins): line 1 is the pedigree
`local_id` (e.g. "II-4"), then each `Annotation`'s verbatim `text` (a genotype "N/N", a measurement "175", …). An
`ANNOTATION_TYPE_INDIVIDUAL_NUMBER` annotation is **skipped** — it is the bare arabic index ("2") already carried by
`local_id` ("II-2"), so drawing it would duplicate the id. De-duplication additionally collapses any annotation whose
text equals a line already present (e.g. an extractor that repeats the id). The deprecated `label` field is never drawn.
Lines use `LABEL_SIZE`, separated by `LABEL_LINE_GAP`; the first line sits `LABEL_GAP` below the symbol's bottom edge.

**Generation markers.** A Roman numeral (the row's IR generation, so a pedigree whose top row is `II` starts there) is
drawn once per row in a reserved **left gutter** of width `GEN_MARKER_GUTTER`, at the gutter's horizontal centre and on
the row's symbol-centre `y`. The whole drawing shifts right by the gutter (hence the `GEN_MARKER_GUTTER` term in `px`);
the gutter sits left of `MARGIN`, and because `MARGIN` exceeds the leftmost symbol's proband-arrow / slash overhang, the
marker never collides with a symbol or an arrow — the collision that made an earlier, gutter-less attempt untenable.

**Spacing scales with the labels.** The stack hangs in the generation gap below its symbol, so the reserved space is
sized from the actual pedigree, not a fixed band:

- *Vertical.* The reserved label band = `LABEL_GAP + max_lines·LABEL_SIZE + (max_lines−1)·LABEL_LINE_GAP`, where
  `max_lines` is the tallest stack in the pedigree; the canvas extends this far below the bottom row so its labels are
  never clipped. `GEN_HEIGHT` is a floor: the effective row pitch is raised to
  `SYMBOL_SIZE + band + LABEL_GAP + SIB_STUB` so a parent's stack clears the sib bar and descent lines of the row below.
- *Horizontal.* SVG text width isn't measurable at build time, so a line's width is estimated conservatively as
  `0.6·LABEL_SIZE` px per character (a sans-serif average advance). `X_UNIT` sets the geometric column pitch (a floor);
  label widening is **local**, not global — the layout-x→pixel-x map is a longest-path over left-to-right constraints:
  the geometric floor between consecutive columns, plus, for each adjacent same-row pair, a clearance of
  `(width_left + width_right)/2 + LABEL_SIZE` so their stacks don't collide. Only the gaps whose labels would overlap
  widen; one wide annotation no longer inflates the whole figure's pitch. The map is monotonic (equal layout-x → equal
  pixel-x), so descents and mating lines stay vertical. The canvas width and origin are sized from true content bounds
  (symbol half or label half, whichever reaches further per cell), so an outermost label wider than its symbol is not
  clipped. All deterministic functions of the pedigree, keeping golden bytes stable.

Spacing constants exposed: `GEN_HEIGHT`, `X_UNIT`, `SYMBOL_SIZE`, `COUPLE_GAP`, `SIB_GAP`, `SIB_STUB`,
`DOUBLE_LINE_OFFSET`, `LABEL_SIZE`, `LABEL_GAP`, `LABEL_LINE_GAP`, `GEN_MARKER_GUTTER`.

### Figure render (PedigreeSet → tiled SVG)

A figure is a *set* of pedigrees (`ir.md`), so `render_set_svg(PedigreeSet)` composes one SVG from the per-pedigree
`render_svg`: each pedigree is laid out and drawn unchanged, then the tiles are stacked vertically and titled by their
`Pedigree.title`, each wrapped in a nested `<svg viewBox>` that carries its own coordinate system — so drawing stays
byte-identical and single-pedigree goldens are untouched. A pedigree the layout **defers** (an interlocking loop, a
child of two matings, a routed mating with offspring — see Deferred) degrades to a labelled dashed placeholder so the
rest of the figure still renders rather than the whole figure failing; an empty set is a minimal canvas. v1 stacks
vertically; a grid for many-family figures is a later refinement (slice 13).

## Deferred

Non-overlap and generation rank are the only hard constraints and non-overlap is always satisfiable in 1-D, so the
constraint layout **rarely fails** — the worst case is an ugly-but-correct routed drawing, a strictly better contract
than v1's "detect and defer". The shapes v1 deferred now draw: a **cousin marriage** (with or without siblings) is an
ordinary adjacent doubled couple once ordering pulls each cousin to its sibship end; an **avuncular** join draws via the
ghost; **boundary-bridge** two-lineage joins order adjacently; a **>2-mate** individual routes its overflow matings. The
mechanism is in [`layout-v2.md`](layout-v2.md).

The residual deferrals (raised as `DeferredFeatureError`, surfaced as a placeholder — never a wrong drawing):

- **Interlocking consanguinity loops** — a cycle that survives excluding the same-generation cross-lineage joins (double
  first cousins, an individual reachable from a founder by two non-marriage paths). Every drawable loop closes on a
  single cross-mating that ordering makes an adjacent couple; a surviving cycle is one the ordering cannot open that way
  (`_detect_loops`).
- **A child of more than one mating** — adoption / multi-parentage the graph model does not yet place (`_derive`).
- **A couple across generations** other than an avuncular join between two partners with drawn parents — a marry-in
  numbered on a different generation from their partner has no drawn form; it is deferred rather than moved onto the
  partner's row (`_rank`).
- **A routed / overflow mating that *has* offspring** — descent from a non-adjacent parent pair is not yet drawn, so a
  > 2-mate individual whose overflow mating bears children defers rather than mislay the descent (`_build`).
- **Sib bars that meet** — two sibships whose drawn bars overlap or touch on a row: a torn sibship, or a drop the
  x-solve leaves beside its children whose bar extension reaches a neighbour's. Either reads as one sibship with several
  sets of parents; or a drawn group that is not exactly one mating's children from that mating's partners, a layout bug
  the check keeps from reaching a figure (`_overlapping_sibships`).

## Alternatives considered

- **pedigreejs as a black-box SVG renderer** — proven and medical, but GPL, JS (would pull the stack out of Python),
  d3-hierarchy layout weaker on consanguinity, and its editor-oriented model + styling resist the glyph-level control
  the judge needs.
- **Madeline as a subprocess** — best-in-class complex-pedigree layout, but GPL C++ and its own SVG styling; kept as the
  aesthetic benchmark/oracle, not the renderer.
- **Graphviz `dot` (via ped_draw)** — generic hierarchical layout, not pedigree-aware (no couple nodes, sib bars, or
  loop handling) — poor convention fidelity.
- **From-scratch layout heuristics** — rejected; the layout is the constraint-optimization model of
  [`layout-v2.md`](layout-v2.md) (Sugiyama's coordinate phase + pedigree constraints), which is graph-drawing prior art
  specialised, not an invented heuristic. Its own alternatives (keep v1's passes, duplicate-to-a-tree, Madeline's
  hybrid, `dot` as-is) are weighed there.

## References

kinship2 "Pedigree alignment" vignette (Therneau; the implementable reference — `kindepth`, `alignped1–4`, the
`n/nid/pos/fam/spouse/twins` arrays) and `plot.pedigree`; Sinnwell, Therneau, Schaid, *Hum Hered* 2014;78:91. Madeline
2.0 (Trager et al., *Bioinformatics* 2007;23:1854). CraneFoot (Mäkinen et al., *EJHG* 2005;13:987). Symbols: Bennett et
al., *J Genet Couns* 2008;17:424 (2022 revision 1002/jgc4.1621). Full annotated list in `../plans/03-renderer-tier1.md`.
