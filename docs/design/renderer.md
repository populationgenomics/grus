# Design: the renderer (IR → SVG)

**Status:** current **Related:** [`ir.md`](ir.md) (input contract), [`svg-output.md`](svg-output.md) (how the drawing is
organised in the document: groups, ids and data attributes for interactive use), `architecture.md` (why deterministic).
Implementable spec: `../plans/03-renderer-tier1.md`.

## Overview

Deterministic IR → SVG. Two parts: **layout** (assign each individual a generation row and an x position) and
**drawing** (emit Bennett-standard symbols and connecting lines from that grid). No LLM. Layout is the v2 constraint
model (ranks fixed, x from a deterministic solve over soft pedigree constraints, every relationship an edge — full spec
in [`layout-v2.md`](layout-v2.md)); this doc summarises it and specifies the drawing.

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
consanguinity double line, MZ-twin bar, deceased slash, carrier fill), so we need glyph-level control that
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
   (dot's `mincross`), couples and twin groups kept contiguous as adjacency atoms (one partner of either twin may stand
   between two co-twins, so a twin with two partners keeps both marriages adjacent). A same-generation cross-lineage /
   loop marriage becomes an ordinary adjacent couple once ordering pulls each partner to its sibship end; an individual
   with more partners than sides keeps its heaviest adjacencies and **routes** the rest, and a routed mating defers.
1. **x-coordinate solve** — from the fixed order, one lexicographic linear program: every descent on its own sib bar,
   then centring (with a half-sibling hinge spreading at most `sib_gap`), then even spacing, then compactness, subject
   to hard non-overlap. Contiguity **blocks** (an ordinary couple, a twin group, a founder-sib run) are rigid, so they
   never split. The default backend (z3) solves over exact rationals, so positions are identical on every platform;
   HiGHS is an optional alternative. Non-overlap is always satisfiable in 1-D, so layout rarely fails.
1. **Line routing** — from the `(level, x)` grid, draw symbols and connectors (below). Every drawn couple is adjacent; a
   routed mating has no drawn form yet and defers.

**Layout ↔ drawing interface** — per-level parallel arrays (kinship2's shape): for each level, `n` (cell count), `nid`
(individual row index per column), `pos` (x), `fam` (parent-couple column on the level above), `spouse` (0 /
adjacent-spouse / adjacent-spouse-with-double-line — the last set straight from the explicit `Mating.consanguineous`
flag, never inferred), `twin_groups` (each drawn twin group by its members' columns, with its zygosity — by membership,
since a partner may stand between co-twins), `childless` (0 / by-choice / infertility on the couple's left column, from
`Mating.childlessness`), plus `founder_sibships` (parentless sib groups), and `ghost_of` (a ghost cell → the real
individual it duplicates). Drawing reads only these arrays.

### Drawing (Bennett symbols)

Map grid to pixels (`px = X0 + GEN_MARKER_GUTTER + x·X_UNIT`, `py = Y0 + (level-1)·GEN_HEIGHT`). Symbol by gender (□ ○
◇), clinical status as fills inside it (below), plus deceased slash and proband arrow. A **count-collapsed** symbol (a
group drawn as one: `Individual.count` > 1, or `count_unspecified`) carries its number, or `n` for an unknown number;
`count` absent or 1 is one person and draws none (`ir.validate` rejects `count` < 1 and `count` with
`count_unspecified`). The count never overprints another mark. When nothing runs through the symbol's centre it is
centred inside, at `0.45·SYMBOL_SIZE` shrunk so its estimated width fits the shape (0.8 of the size for a square, 0.7
for a circle, 0.5 for a diamond), white when the whole shape is a dark affected tone (index 0 or 1, below) and black
otherwise. When a mark runs through the centre — the unknown `?`, the X-linked carrier dot, a divider or a lone carrier
section's inner edge, the presymptomatic line, the deceased slash — it moves beside the symbol's upper right at
`0.35·SYMBOL_SIZE`: past the slash's tip, above a mating line leaving that side, clear of the arrow (lower left) and the
labels (below); the x-solve spaces the next cell so the count clears both its label and its symbol. Either way it has a
3 px halo in the contrasting colour, so it reads over any fill. A ghost draws the same fills as its real cell and places
its count as the real cell does. Connectors: **mating line** (horizontal between partners; doubled for consanguinity —
the double line is emitted iff `spouse==2`, which is set only from the explicit `Mating.consanguineous` flag, for every
adjacent couple including founders), **descent/sibship line** (vertical drop from the mating midpoint → horizontal sib
bar → per-child stubs; a drop the order leaves beside its children, as in a crossing or a cousin standing beside its
mate, turns at its own elbow track above the bars with rounded corners and lands on its bar's near end, and elbows
sharing a row gap stagger, a drop standing over another's landing leg turning higher), a **founder sibship**'s implied
hanger (a partnerless mating: no parent cell, so the sib bar hangs from a short vertical stub rising to a point instead
of a descent drop), **twins** (child stubs converge to one point; MZ adds a joining bar), and the **childless glyph** (a
couple with no offspring: a stub from the mating midpoint down to a short horizontal bar — one bar for
`CHILDLESSNESS_BY_CHOICE`, two parallel bars for `CHILDLESSNESS_INFERTILITY` — drawn instead of a descent).

Not yet drawn (extracted and diffed, but no glyph): relationship `status` (separation / divorce slashes on the mating
line).

#### Clinical status: fills, sections and the key

The drawing follows the NSGC 2022 revision (Bennett et al., §4.5 and Figure 2), which says three things about status:

1. A symbol showing **one** condition is not divided. Only a symbol showing more than one is divided into sections, one
   per condition, each with its own fill.
1. **Carrier status is a fill**, not the central dot of the 2008 standard: a dot cannot say *which* of several
   conditions a person carries, and a condition's fill can hide it. The standard's example is a HEXA and CFTR carrier
   drawn in halves, and says a horizontal line fill *could* be used for one half and a vertical for the other; what it
   requires is a unique fill per section, defined in the key.
1. **Every fill is defined in the key** (Box 1 item 3). The standard never ties a fill to a mode of inheritance.

Figure 2 divides only a symbol that shows more than one condition; it does not forbid more. grus divides **every**
symbol of a pedigree whose legend has two or more conditions (below), because the sections are then a fixed frame: a
condition sits in the same place on every symbol, filled or not, and a reader sees which conditions a symbol does *not*
show. A pedigree with one condition, the common case, stays undivided, as the standard draws it.

**Condition index.** A condition's index is its place in the pedigree's condition legend: distinct names, phenotype
labels first, then by first appearance in `Position` order, with an unnamed condition (the figure's sole, unlabelled
one) taking the last index. The index picks both the condition's fills and its section, pedigree-wide, so a condition
looks the same and sits in the same place on every symbol that shows it. Every legend entry holds its index whether or
not any symbol is filled for it — a phenotype label nobody is shaded for still takes a section and counts towards
quadrants and the four-fill limit — so the drawing of one individual never depends on another's status.

**Fills: the tone says the condition, the texture the status.**

| index | affected             | carrier                         |
| ----- | -------------------- | ------------------------------- |
| 0     | black `#000000`      | black with white `/` hatch      |
| 1     | dark grey `#484848`  | dark grey with white `\` hatch  |
| 2     | mid grey `#9c9c9c`   | mid grey with black `/` hatch   |
| 3     | light grey `#dbdbdb` | light grey with black `\` hatch |

A carrier is the condition's own tone with a diagonal hatch in the contrasting colour, so "carrier of A" and "affected
with A" share A's tone and differ only in texture, and a carrier of two conditions is two hatched sections, never a
solid, affected-looking symbol. The hatch alternates direction by index, so neighbouring tones — the pairs that merge
first when a figure is rastered small — also differ in direction.

The standard's example fills (horizontal and vertical lines, which it says *could* be used) are avoided: a vertical line
through a symbol is its presymptomatic glyph and the dividers are vertical, and horizontal lines echo the mating lines,
sib bars and the childless bar. Diagonal lines are also what survives a small raster: a dot pattern (tried at a 17%
cover, 3.5 px dots) blurs into its tone below about half size, leaving only a grey level to tell fills apart, while a
line keeps its streaks down to about a third. Below that the hatch blurs too, and a fill is told from its neighbours by
grey level and faint direction, which reads side by side (the key sits beside every drawing) and is marginal in
isolation for carrier 0 against affected 1 and for carrier 1 against carrier 2.

A pattern tiles from a fixed origin, so where its lines fall on a symbol depends on where the symbol is — at an
arbitrary phase a line lies flush against a section edge or the outline and reads as a thicker edge, or a mark. Every
fill is therefore drawn in its symbol's own frame and moved into place, so each pattern has one phase per shape. The
lines lie on a diagonal lattice whose cell is a quarter of the symbol size. The diagonals run parallel to a diamond's
edges, so a square or diamond is placed with its centre on the lattice's midpoint, putting a diamond's edges midway
between two lines; a circle is placed with a lattice line through its centre, so that no line is a near-tangent chord
hugging its outline. A key swatch is placed like a square. No line runs along a section edge, a divider or an outline;
lines cross them. There is no fifth distinct fill: a pedigree that would fill a condition at index 4 or above defers (a
placeholder, never a reused fill).

**Sections, dividers and sector edges.** A pedigree with two conditions in its legend divides every symbol into halves
(index 0 left, 1 right); with three or four, into quadrants (top-left, top-right, bottom-left, bottom-right). The
divider lines through the centre are drawn on every symbol, filled or not, at half the outline's stroke width, so they
read as the frame and not as a mark. A section is a rectangle clipped to the shape by a per-symbol `clipPath`, so one
code path covers □ ○ ◇. Every filled section has a visible edge: where it meets the outline that is the outline, and
where it meets another section, a divider. In a pedigree with one condition, which has no dividers, a lone carrier's
half gets that one thin line down the centre as its inner edge; nothing is stroked twice. A key swatch has the same thin
outline.

**What a symbol shows.**

- Affected with exactly one condition and carrying none: the whole shape in that condition's affected tone. An affected
  symbol of a single-condition pedigree is solid black, as before.
- Otherwise every affected and every carried condition fills its own section. A carrier of one condition is one hatched
  section in its index's place — a whole-shape fill would move the condition off its place; affected with one condition
  and a carrier of another is a flat section and a hatched one. A condition that is both affected and carried on one
  individual (same-named entries) draws as affected.
- The **presymptomatic** line is drawn at the outline's full stroke width and runs one stroke width past the outline at
  top and bottom, everywhere, so in a divided pedigree it never reads as the thinner vertical divider it lies on.
- Unknown (`?`), deceased and proband marks are unchanged, drawn over the fills; in a divided pedigree the `?` has a
  white halo so it reads over the dividers.

**Carrier style.** `Geometry.carrier_style` is a render option, not IR meaning; both styles read the same `Condition`
entries. `CarrierStyle.PARTITION_FILL`, the default, is the standard as above. `CarrierStyle.INHERITANCE_GLYPH` draws
figures the way the pre-2022 literature does: on a symbol with no affected condition, an X-linked carrier is a central
dot, edged white so it reads over a tone; every other carrier is a section as above.

**The key.** Whenever a pedigree draws a fill, a key below the drawing defines each fill drawn — the standard requires
it, and without it a tone or a hatch means nothing to a reader who does not hold the IR. It has one entry per (index,
status) drawn, ordered by index with affected first: a square swatch in that fill, the size of one symbol quadrant and
drawn about its own centre, and a label — the condition's name when affected, `Carrier: <name>` when carried
(`X-linked carrier: <name>` for the dot), and `Affected` / `Carrier` for an unnamed condition. Entries run left to right
and wrap at the drawing's width (or the widest entry's, if wider). The key sits `KEY_GAP` below the lowest label or
arrow and the canvas grows to hold it, so it never overlaps the drawing; a pedigree with no fills has no key and an
unchanged canvas. A composed figure keys each pedigree tile on its own, since indices are per pedigree.

**Label stack.** Under each symbol, a centred vertical stack of text lines, built per individual as an ordered
`[local_id, *annotation_texts]` with empties **and duplicates** dropped (first occurrence wins): line 1 is the pedigree
`local_id` (e.g. "II-4"), then each `Annotation`'s verbatim `text` (a genotype "N/N", a measurement "175", …). An
`ANNOTATION_TYPE_INDIVIDUAL_NUMBER` annotation is **skipped** — it is the bare arabic index ("2") already carried by
`local_id` ("II-2"), so drawing it would duplicate the id. De-duplication additionally collapses any annotation whose
text equals a line already present (e.g. an extractor that repeats the id). The deprecated `label` field is never drawn.
Lines use `LABEL_SIZE`, separated by `LABEL_LINE_GAP`; the first line sits `LABEL_GAP` below the symbol's bottom edge. A
symbol whose descent drops from its own centre (a count-collapsed parent, or a lone parent with no free side for a
phantom) would have that line run through a centred stack, so its stack moves beside the line, `LABEL_GAP` from it, on
the side no couple line (and its descent) leaves: left when the cell's partner is on its right, else right. With couples
on both sides it stays right, and a couple's drop on that side can still cross it.

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
  `0.6·LABEL_SIZE` px per character (a sans-serif average advance) (`_labels.py`). Label widening is **local** and lives
  in the x-solve: each adjacent same-row pair's minimum separation is the larger of its geometric gap and the left
  cell's right label reach plus the right cell's left reach plus `LABEL_SIZE`
  (`(width_left + width_right)/2 + LABEL_SIZE` for two centred stacks; a stack beside its drop reaches to one side
  only), in layout units, so only the gaps whose labels would overlap widen and one wide annotation does not inflate the
  whole figure's pitch. Drawing then maps layout-x to pixel-x by one scale, `X_UNIT` px per unit. A widening applied
  after the solve (it used to be) keeps lines vertical but moves every midpoint off the one the solve centred. The
  canvas width and origin are sized from true content bounds (symbol half or label reach, whichever is further per side
  of each cell), so an outermost label wider than its symbol is not clipped. All deterministic functions of the
  pedigree, keeping golden bytes stable.

Spacing constants exposed: `GEN_HEIGHT`, `X_UNIT`, `SYMBOL_SIZE`, `COUPLE_GAP`, `SIB_GAP`, `SIB_STUB`,
`DOUBLE_LINE_OFFSET`, `LABEL_SIZE`, `LABEL_GAP`, `LABEL_LINE_GAP`, `GEN_MARKER_GUTTER`, and the key's `KEY_GAP`,
`KEY_ENTRY_GAP` (drawing-only: the key never moves a symbol).

### Figure render (PedigreeSet → tiled SVG)

A figure is a *set* of pedigrees (`ir.md`), so `render_set_svg(PedigreeSet)` composes one SVG from the per-pedigree
`render_svg`: each pedigree is laid out and drawn unchanged, then the tiles are stacked vertically and titled by their
`Pedigree.title`, each wrapped in a nested `<svg viewBox>` that carries its own coordinate system — so drawing stays
byte-identical and single-pedigree goldens are untouched. A pedigree the layout **defers** (an interlocking loop, a
child of two matings, a routed mating — see Deferred) degrades to a labelled dashed placeholder so the rest of the
figure still renders rather than the whole figure failing; an empty set is a minimal canvas. v1 stacks vertically; a
grid for many-family figures is a later refinement (slice 13).

## Deferred

Non-overlap and generation rank are the only hard constraints and non-overlap is always satisfiable in 1-D, so the
constraint layout **rarely fails**. The shapes v1 deferred now draw: a **cousin marriage** (with or without siblings) is
an ordinary adjacent doubled couple once ordering pulls each cousin to its sibship end; an **avuncular** join draws via
the ghost; **boundary-bridge** two-lineage joins order adjacently; a **twin with two partners** stands one between the
co-twins. The mechanism is in [`layout-v2.md`](layout-v2.md).

The residual deferrals (raised as `DeferredFeatureError`, surfaced as a placeholder — never a wrong drawing):

- **Interlocking consanguinity loops** — a cycle that survives excluding the same-generation cross-lineage joins (double
  first cousins, an individual reachable from a founder by two non-marriage paths). Every drawable loop closes on a
  single cross-mating that ordering makes an adjacent couple; a surviving cycle is one the ordering cannot open that way
  (`_detect_loops`).
- **A child of more than one mating** — adoption / multi-parentage the graph model does not yet place (`_derive`).
- **A couple across generations** other than an avuncular join between two partners with drawn parents — a marry-in
  numbered on a different generation from their partner has no drawn form; it is deferred rather than moved onto the
  partner's row (`_rank`).
- **A routed mating** — partners that cannot stand side by side: a third partner, or a twin whose sides are both taken
  (a twin pair holds three partners). Its drawn form, a track over the row, read as a sibship
  ([`layout-v2.md`](layout-v2.md), Alternatives considered), so it defers (`_layout2.layout`).
- **A fifth filled condition** — a fill (not the X-linked dot) for a condition at legend index 4 or above. The fills
  table has four distinct fills per status, and reusing one would draw two conditions alike (`_draw`).
- **A torn sibship** — two sibships whose children's spans overlap on a row, which reads as one sibship with several
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

- **Divide every symbol** (kinship2) — rejected: the standard divides only a symbol showing more than one condition, and
  a divided empty symbol is the presymptomatic glyph.

- **Pie sectors, as Figure 2 draws three conditions on a circle** — rejected for fixed halves / quadrants keyed by
  index: sectors sized to each individual's own conditions would move a condition from symbol to symbol.

- **Carrier as a dot stipple on its tone** — rejected: at a density that reads, the dots blur into their tone below
  about half size, and then only the grey level tells carrier from affected; diagonal lines keep their streaks longer.

- **Carrier as hatch alone, on white** (`/`, `\`, cross-hatch, chevrons) — rejected: without the condition's tone a
  carrier and an affected individual of the same condition share nothing visible, and the four hatches are harder to
  tell apart small than four tones.

- **Colour fills** — not the default: grey tones and hatches survive greyscale print and photocopy, and a consumer that
  wants colour recolours the named fills (`svg-output.md`).

- **A key only on request** — rejected: the standard requires every fill to be defined, and a figure without its key is
  unreadable once it leaves the IR.

## References

kinship2 "Pedigree alignment" vignette (Therneau; the implementable reference — `kindepth`, `alignped1–4`, the
`n/nid/pos/fam/spouse/twins` arrays) and `plot.pedigree`; Sinnwell, Therneau, Schaid, *Hum Hered* 2014;78:91. Madeline
2.0 (Trager et al., *Bioinformatics* 2007;23:1854). CraneFoot (Mäkinen et al., *EJHG* 2005;13:987). Symbols: Bennett et
al., *J Genet Couns* 2008;17:424 (2022 revision 1002/jgc4.1621). Full annotated list in `../plans/03-renderer-tier1.md`.
