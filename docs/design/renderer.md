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
otherwise. When a mark runs through the centre — the unknown `?`, the X-linked carrier dot, a filled section's border,
the presymptomatic line, the deceased slash — it moves beside the symbol's upper right at `0.35·SYMBOL_SIZE`: past the
slash's tip, above a mating line leaving that side, clear of the arrow (lower left) and the labels (below); the x-solve
spaces the next cell so the count clears both its label and its symbol. Either way it has a 3 px halo in the contrasting
colour, so it reads over any fill. A ghost draws the same fills as its real cell and places its count as the real cell
does. Connectors: **mating line** (horizontal between partners; doubled for consanguinity — the double line is emitted
iff `spouse==2`, which is set only from the explicit `Mating.consanguineous` flag, for every adjacent couple including
founders), **descent/sibship line** (vertical drop from the mating midpoint → horizontal sib bar → per-child stubs; a
drop the order leaves beside its children, as in a crossing or a cousin standing beside its mate, turns at its own elbow
track above the bars with rounded corners and lands on its bar's near end, and elbows sharing a row gap stagger, a drop
standing over another's landing leg turning higher), a **founder sibship**'s implied hanger (a partnerless mating: no
parent cell, so the sib bar hangs from a short vertical stub rising to a point instead of a descent drop), **twins**
(child stubs converge to one point; MZ adds a joining bar), and the **childless glyph** (a couple with no offspring: a
stub from the mating midpoint down to a short horizontal bar — one bar for `CHILDLESSNESS_BY_CHOICE`, two parallel bars
for `CHILDLESSNESS_INFERTILITY` — drawn instead of a descent).

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

A symbol draws lines only around what it fills (below). Drawing every section's lines on every symbol would make the
fixed frame explicit, but a four- or six-spoke star on every empty symbol is noise, and a crossed square resembles
another symbol. An unfilled symbol is its outline alone, as the standard draws it.

**Condition index.** A condition's index is its place in the pedigree's condition legend. Index 0, which draws in the
most distinct fill (black), goes to the **primary** condition: the proband's affected condition when the proband is
affected, else the condition with the most affected individuals. An unnamed condition (the figure's plain "affected",
often beside named carrier-only conditions) is eligible like any other. The rest follow in the base order — phenotype
labels first, then by first appearance in `Position` order, then the unnamed condition — which also breaks ties. The
primary is the one a reader looks for first, so it gets the fill that survives every size and printer. The index picks
both the condition's fills and its section, pedigree-wide, so a condition looks the same and sits in the same place on
every symbol that shows it. The legend holds the conditions someone in the pedigree carries, under any status, each by
its declaration (`ir.md`, "Conditions are declared once, by id"): the name and the mode of inheritance come from the
set's `ConditionDef`, never from the entry, so the renderer takes the set's declarations alongside a pedigree
(`conditions=`; `render_set_svg` and `render_svgs` pass the set's own). A phenotype label orders the conditions it
names, but one that names no one's condition (a family's name, "DUH family" beside the condition "DUH") is not a
condition: it takes no index, makes no section and has no key entry, so it cannot turn a one-condition pedigree into
halves. A condition that occurs only as unaffected still holds its index, so the drawing of one individual never depends
on another's status.

**Fills: the tone says the condition, the texture the status.**

| index | affected             | carrier                         |
| ----- | -------------------- | ------------------------------- |
| 0     | black `#000000`      | black with white `/` hatch      |
| 1     | dark grey `#484848`  | dark grey with white `\` hatch  |
| 2     | mid grey `#9c9c9c`   | mid grey with black `/` hatch   |
| 3     | light grey `#dbdbdb` | light grey with black `\` hatch |
| 4     | near-black `#262626` | near-black with white `/` hatch |
| 5     | pale grey `#b8b8b8`  | pale grey with black `\` hatch  |

Conditions 4 and 5 take a near-black and a pale grey, tones the first four do not use. Their fills are distinct at full
size, but small, where the hatch blurs into its tone, they come close to others' (carrier 4, near-black with a white
hatch, blurs to about the grey of carriers 0 and 2). Six conditions cannot be told apart by grey alone, and they do not
have to: a pedigree with five or six conditions is drawn in sixths, where every fill sits in its own fixed wedge, so a
condition's position carries its identity and the tone only confirms it.

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
hugging its outline. A key swatch is placed like a square. Each pattern is one tile, forty cells across and centred on
the frame origin, so every fill part lies inside a single tile: a PDF renderer draws the seams between tiles as a faint
grid (seen through rsvg-convert's PDF in MuPDF), and a fill that never crosses a seam cannot show one. The hatch lines
therefore run unbroken across the tile. No line runs along a section edge, a divider or an outline; lines cross them.
There is no seventh fill: a pedigree that would fill a condition at index 6 or above defers (a placeholder, never a
reused fill), with a message naming the supported range.

**Sections, dividers and sector edges.** A pedigree with two conditions in its legend divides every symbol into halves
(index 0 left, 1 right); with three or four, into quadrants (top-left, top-right, bottom-left, bottom-right); with five
or six, into sixths. The standard asks for "however many subsections are necessary"; what lets a reader find a condition
is that each section points one way, the same way on every symbol.

Sixths are six wedges from the centre, each 60° on a circle, pointing at 10, 12 and 2 o'clock (indices 0-2, the top row)
and 8, 6 and 4 o'clock (3-5, the bottom row) — the reading order of halves and quadrants, index 0 upper left. The same
wedges divide the square and the diamond, cut off by their outlines, so a condition points the same way on every shape:
a 2×3 grid on the square would put index 1 in a different place on a square than on a circle, and a grid does not fit a
diamond at all. On the square the top and bottom wedges are narrower than the corner ones (about 190 against 230 square
pixels), on the diamond a little wider (120 against 100); neither is small enough to lose its fill. The wedges' edges
run along 1, 3, 5, 7, 9 and 11 o'clock: none is vertical, so the presymptomatic line, which is vertical, never lies on
one. In sixths every fill keeps its section, even a single affected condition (which elsewhere fills the whole shape),
because position identifies it there; in colour mode (below) the colour does too.

A section is a rectangle (in sixths a wedge) clipped to the shape by a per-symbol `clipPath`, so one code path covers □
○ ◇. Every filled section has a visible edge, and only filled sections do. Where it meets the outline its edge is the
outline; inside the symbol it gets a **border**, a line from the centre along each boundary it shares with the rest of
the symbol or with another filled section, at half the outline's stroke width. A boundary between two empty sections has
no line, a wholly filled symbol none, and no border is stroked twice. A lone carrier's half thus has the thin line down
the centre; a carrier's single quadrant, its two radii. A symbol's borders are one path, out from the centre along each
ray and back, with round joins: separate butt-capped lines left notches where rays meet at the centre, and a mitre join
spikes at a sixth's 60° angle. A ray's far end lies under the outline's stroke, so it needs no join there.

**What a symbol shows.**

- Affected with exactly one condition and carrying none: the whole shape in that condition's affected tone, except in
  sixths, where it is that condition's wedge. An affected symbol of a single-condition pedigree is solid black, as
  before.
- Otherwise every affected and every carried condition fills its own section. A carrier of one condition is one hatched
  section in its index's place — a whole-shape fill would move the condition off its place; affected with one condition
  and a carrier of another is a flat section and a hatched one. A person has one entry per condition (the loader checks
  it), so one condition is never both affected and carried on one symbol.
- The **presymptomatic** line is a heavy vertical bar: twice the outline's width, with round caps, over a white halo,
  inset from the outline at top and bottom. The weight parts it from a filled half's thin border it may lie on, the halo
  from any fill beneath it (a black whole fill, a dark tone, a hatch) and from a coincident border, and the inset from
  the child's stub arriving at the top, which would otherwise run on into it and read as a descent line through the
  symbol. The inset leaves the halo clear of the outline even at a diamond's narrow vertex. The line does not say which
  condition is presymptomatic, so the key does, with a swatch carrying the same bar (below).
- Unknown (`?`), deceased and proband marks are unchanged, drawn over the fills; on a symbol with borders the `?` has a
  white halo so it reads over them.

**Carrier style.** `Geometry.carrier_style` is a render option, not IR meaning; both styles read the same `Condition`
entries. `CarrierStyle.PARTITION_FILL`, the default, is the standard as above. `CarrierStyle.INHERITANCE_GLYPH` draws
figures the way the pre-2022 literature does: on a symbol with no affected condition, a carrier of a condition declared
X-linked is a central dot, edged white so it reads over a tone; every other carrier is a section as above.

**Colour mode.** `Geometry.palette` (the `render --colour` flag) is a render option like the carrier style.
`Palette.GREYSCALE`, the default, is the table above and survives any printer. `Palette.COLOUR` paints the condition
tones black (the primary condition) and then orange, blue, bluish green, vermillion and reddish purple from the
Okabe-Ito palette, chosen to stay distinct under the common colour-vision deficiencies; its yellow is left out as too
light against white. A carrier is still the tone with a diagonal hatch in white or black, whichever contrasts more with
it, alternating direction by index. Only the tones change — the same patterns, ids, sections and hatch geometry — so
colour mode meets the same edge rules, and a consumer can still restyle each tone. In colour, a condition's identity is
carried by its colour as well as its position, which is what greys cannot do for six conditions or for a light wedge.

**The key.** Whenever a pedigree draws a fill, a key below the drawing defines each fill drawn — the standard requires
it, and without it a tone or a hatch means nothing to a reader who does not hold the IR. It has one entry per (index,
status) drawn, ordered by index with affected first, then carrier, then presymptomatic: its swatches and a label. The
swatches look like the symbols. A fill the symbols only ever draw whole (a person affected with that condition alone)
has a whole swatch, a plain square filled all over; a fill drawn in a section has a section swatch, a small square
filled in that section only, with its borders, so the key shows where the condition sits. A fill drawn both ways —
condition A whole on a person with only A, and in its section on a person with A and B — has both, whole first, so every
symbol has its form in the key. In sixths a lone condition keeps its wedge, so their swatches are sections. In a divided
pedigree the swatches are three quarters of the symbol size, so a section reads; in an undivided one, a quadrant's size.
Then the label — the condition's name when affected, `Carrier: <name>` when carried (`X-linked carrier: <name>` for the
dot), `Presymptomatic: <name>` (a swatch with the presymptomatic bar) for each condition someone is presymptomatic for —
the standard asks for the condition in the legend — and `Affected` / `Carrier` / `Presymptomatic` for an unnamed
condition. Entries run left to right and wrap at the drawing's width (or the widest entry's, if wider). The key sits
`KEY_GAP` below the lowest label or arrow and the canvas grows to hold it, so it never overlaps the drawing; a pedigree
with no fills has no key and an unchanged canvas. A composed figure keys each pedigree tile on its own, since indices
are per pedigree.

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
display title (the family label, else the first panel or other label; a phenotype or gene label names a condition or
gene, never the pedigree, so a pedigree with only those has no title), each wrapped in a nested `<svg viewBox>` that
carries its own coordinate system — so drawing stays byte-identical and single-pedigree goldens are untouched. A
pedigree the layout **defers** (an interlocking loop, a child of two matings, a routed mating — see Deferred) degrades
to a labelled dashed placeholder so the rest of the figure still renders rather than the whole figure failing; an empty
set is a minimal canvas. v1 stacks vertically; a grid for many-family figures is a later refinement (slice 13).

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
- **A seventh filled condition** — a fill (not the X-linked dot) for a condition at legend index 6 or above. There are
  six sections and six fills per status, and reusing one would draw two conditions alike (`_draw`).
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

- **Sixths as a 2×3 grid on the square** — rejected: a condition would sit in a different place on a square than on a
  circle or a diamond, and the grid has no counterpart on a diamond.

- **Conditions coded by combinations of regions** (as some source figures draw many conditions) — rejected: overloaded,
  and a quadrant's position then reads as an inheritance pattern.

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
