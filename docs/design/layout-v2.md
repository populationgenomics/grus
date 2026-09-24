# Design: layout v2 — pedigree layout as constrained optimization

**Status:** current **Related:** [`renderer.md`](renderer.md) (v1 layout + drawing this replaces the layout half of),
[`ir.md`](ir.md) (input; note the birth-order demotion below). Supersedes the v1 layout algorithm and its cross-join /
loop special-casing; the drawing (symbol glyphs, connectors) is reused.

## Overview

v1 layout is the kinship2 single-mate recursion plus accreted special-case passes (`_place_cross`, `_slide_and_pull`,
`_pull`, `_spread_for_children`, `_relocate_adjacent`, the cross-generation `ghost`, the overlapping-bars guard). Each
pass re-derives a fragment of a general layout algorithm, and common shapes still **defer** to a placeholder: a cousin
marriage where both partners have siblings (c05), an individual with >2 matings, a multi-mate hinge with a single-parent
side (c09), a boundary-bridge box-in (c05-class). v2 replaces the recursion **and** the special cases with one model:
**layout is a constrained coordinate assignment under a constraint hierarchy** — generation ranks are fixed, a small set
of strengthed pedigree constraints is resolved by *lexicographic relaxation* (drop the weakest constraint in any
conflict, demoting the affected mating to a **routed edge**), and a deterministic combinatorial solve places x under the
survivors. Not a weighted objective.

## Background — why the model, not more passes

The v1 passes exist because v1 bakes two things as **hard** that are not: sibling **birth order** (drawn L→R in
`Mating.offspring` order) and **couple adjacency**. Both are provably soft:

- **Birth order** cannot be hard: a sibling who marries an out-of-sibship partner is *forced* to a sibship end (couple
  adjacency + sibship contiguity leave no other spot), which reorders siblings. Madeline reorders freely for exactly
  this reason. Birth order is a *preference* that yields where a mating forces it.
- **Couple adjacency** cannot be hard either: a sibship has two ends and a node has two 1-D neighbours, so three
  cross-marriages in one sibship — or anyone married three times — makes full adjacency impossible. Real pedigrees draw
  the un-adjacent mating as a routed line.

So the only genuinely hard constraints are **non-overlap** and **generation rank**. Everything else is a *preferred*
constraint with a strength, relaxed by priority when it conflicts (below). That is the general constraint-based layout
paradigm (Sugiyama's coordinate phase; Borning's constraint hierarchies; Dwyer's IPSep-CoLa; kinship2's deferred
`alignped4` is a baby version). `dot` already has this *architecture* but pedigree-blind constraints (graph-y output);
Madeline has the right *constraints* as bespoke heuristics. v2 = the constraint architecture **with** pedigree
constraints, expressed as a strength hierarchy, not code paths.

## Design

### Ranks (hard)

y is the individual's IR generation — the row the figure draws them on ([`ir.md`](ir.md)) — offset so the first
generation is row 0. It is read, not computed. Computing it, as kinship2's `kindepth` does (depth is the longest path
from a founder, then a spouse's lineage is pulled down to share a row), agrees with the figure on a connected pedigree
drawn by the book, and disagrees exactly where real figures depart from it: a branch drawn detached because its
attachment is uncertain has depth 0 and lands in row I, and a child drawn beside half-siblings whose other parent is a
generation lower comes out one row too high. Both occur in the corpus.

Reading rows from the IR means a descent can span several rows. Each row it crosses gets a **pass-through** cell, the
dummy node of layered graph drawing: the couple heads the first, each heads the next, and the last heads the real
sibship. Ordering, the x-solve and drawing then only ever see edges between adjacent rows; a pass-through is ordered and
spaced like a symbol and drawn as the line through its row. A couple always shares a row. An avuncular marriage, whose
partners are on different generations, becomes a same-row couple by duplicating the shallower partner as a **ghost** on
the deeper row; any other couple across generations has no drawn form and defers.

A **lone-parent mating** (the other parent omitted from the figure) gets a **phantom partner** cell beside the parent,
so it is an ordinary couple to ordering and the x-solve, and drawing emits a marriage line to where the partner would
stand and no symbol — as the literature draws an omitted partner. Each lone-parent sibship then drops from its own line,
so a parent's half-sibships by different, undrawn partners never share a drop and bar. A phantom's line carries no
stretch cost: with no partner symbol its length means nothing, so it stretches as far as centring its drop needs.

### The solve (x-positions)

Continuous x per individual and per mating node (a mating node sits between its partners; descent drops from it). Three
stages:

0. **Relaxation pre-pass** — resolve constraint conflicts statically (below): compute which matings demote to `routed`
   by dropping the weakest constraint in each conflict, to a fixpoint over the strength order. Everything downstream
   sees the surviving (blockable) matings plus the `routed` set.
1. **Ordering** — the left→right order within each rank, over the surviving blocks. Deterministic heuristic
   (weighted-median + transposition sweeps, as in `dot`); a small exact search only where the pruned space is tiny. The
   objective is lexicographic — **torn sibships, then crossings, then birth-order inversions, then total edge length** —
   and the loop keeps the best iterate under it on strict improvement, stopping when a down+up round changes nothing
   (not at the first zero-crossing order: the settling pass is what puts a parent over its child among equal-crossing
   orders). Torn sibships rank first only in choosing the best iterate: each transposition move is priced locally by
   crossings (the two swapped units' endpoint pairs, `dot`'s in/out-cross) and accepted on a strict crossing decrease,
   or on a tie when it strictly repairs birth order or returns an atom to its canonical orientation — so every sweep
   terminates. The moves are adjacent-unit swaps and reversals of a whole adjacency atom (a couple, a twin group, or a
   chain of both, such as twins joined to one twin's spouse); an atom starts in the orientation with fewer birth-order
   inversions. The moves are local, so a birth-order repair that needs two ranks to change together (a twin marrying
   into another family, whose parents' couples would have to swap) is not found. When the best order still tears a
   sibship, the loop runs once more from every atom reversed and keeps the better result: an atom that starts in birth
   order where a marriage below needs the reverse (twins who both marry, one twin's child marrying a cousin) otherwise
   leaves a tear that local moves cannot repair. Pricing moves by tears first was tried and rejected: it traded
   crossings without limit and stopped searches early on shapes that were already clean. The exact search ranks
   crossings before tears too: the tear measure sees a lone child as a point, so moving one past another family counts
   only as a crossing, though its descent then runs along that family's bar and the drawing reads as one sibship with
   two sets of parents. A tear the search cannot avoid still defers; for cousins whose families sit far apart the fix is
   the routed consanguineous marriage (below), not reordering. Crossing minimization is the *weak*-strength tie-breaker
   among orders the stronger constraints leave free — never overriding contiguity or adjacency.
1. **x-coordinate assignment** — given the ordering and the surviving blocks, place x by one **lexicographic linear
   program** (`_xsolve.py`), subject to the row separations and block rigidity. Its objectives, each minimised among the
   optima of those before it: (0) every descent lands on its own sib bar — the parents' midpoint within the children's
   span; (1) centring — each midpoint over its children's centroid, plus a stretch cost on a half-sibling hinge's
   couples, cheap up to `sib_gap` and steep beyond, so a hinge spreads that far to centre its families and a descent
   drops to the end of its bar rather than a couple line crossing the figure; (2) the largest excess gap, so slack
   spreads evenly; (3) the total excess gap; (4) a fixed tie-break, so the optimum is one point. The default backend
   (z3) solves over exact rationals; HiGHS is an optional floating-point alternative. A relaxed (routed) mating exerts
   no couple-adjacency pull here, so each parent-anchored partner settles under its own parents.
1. **Edge routing** — draw each relationship (see below).

### Hard constraints

- **Non-overlap:** any two symbols on a rank are ≥ min-separation apart.
- **Rank:** y = generation (above).

Non-overlap is always satisfiable in 1-D, so layout **rarely fails**: the worst case is an ugly-but-correct routed
drawing, a strictly better contract than v1's "detect and defer". The residual deferrals (`DeferredFeatureError`, drawn
as a placeholder — never a wrong drawing) are listed in [`renderer.md`](renderer.md) "Deferred": an interlocking loop, a
child of more than one mating, a routed union *with offspring*, sib bars that meet (a torn sibship, or a drop's bar
extension reaching a neighbour's).

### Soft constraints — a strength hierarchy, relaxed lexicographically (not a weighted sum)

The soft terms are a **constraint hierarchy** with discrete strengths, **not** a weighted objective. When two
constraints conflict, drop the **lowest-strength** one involved (record it as routed) and retry — never trade a stronger
constraint for a weaker, never minimize a weighted sum of violations. This is the load-bearing choice that keeps the
solve combinatorial: strengths are Borning's constraint hierarchies / Cassowary's required-vs-preferred, resolved by
priority, not a QP over `Σ wᵢ·violationᵢ`. The moment relaxation is driven by weighted cost instead of priority,
determinism is at the mercy of a numeric solver — the line we hold.

Strengths, strongest first:

- **required** — non-overlap; generation rank (the hard core above; never relaxed — layout instead relaxes a preferred
  constraint to routed).
- **strong** — **descent anchor** (a lone child sits under its parents' mating midpoint; a sibship centres under its
  mating node); **sibship contiguity**; **twin/MZ atomicity**.
- **medium** — **couple adjacency** (partners next to each other, mating node between them).
- **weak** — **crossing count**; **same-row couple** (partners on one rank else a routed cross-rank mating); **birth
  order** (sibling x-order matches `Mating.offspring`); **compactness** (width / edge length).

Relaxation is what subsumes v1's special cases and demotes matings to routed, each falling out of one rule — *drop the
weakest constraint in a conflict*:

- **Consanguinity / two-lineage join** (LTBP3 cousins): both partners carry a *strong* descent anchor pinning them far
  apart under their own parents; *medium* couple adjacency can't also hold, so it loses — the union is **routed** as a
  long (double, if consanguineous) line spanning the gap, descent from its midpoint. A one-sided join (a marry-in with
  no parents in the graph) has only one anchor, no conflict, so it stays an adjacent couple — the discriminator is
  *both* partners anchored.
- **Overflow** (a 3rd cross-marriage into a two-ended sibship; an individual with >2 matings and two 1-D neighbours):
  more *medium* adjacencies than slots — keep the two highest, route the rest.
- **Mated sibling** reaching a sibship end reorders siblings: *weak* birth order yields to *strong* contiguity +
  *medium* adjacency, no penalty bookkeeping.

Crossing count sits at *weak* deliberately: readability never overrides a family's structural anchors — it breaks ties
among orderings the stronger constraints leave free (this is why ordering is heavily pruned).

Conflicts are detected **statically** from IR structure (anchor count per node, adjacency degree vs slots) and resolved
in a pre-pass to a fixpoint over the strength order, producing the `routed` set the solver and router consume — no
dynamic place-detect-repair loop. Iterate to fixpoint because relaxations interact: routing one mating frees a slot that
can resolve a downstream overflow.

### Edge routing (net-new capability)

v1 draws a mating only between adjacent symbols (straight horizontal) and descent as a vertical drop; that is why soft
adjacency needs routing. v2 draws **every relationship as an edge**, straight where it can be and **routed** (orthogonal
polyline, dummy-node chain across intervening ranks) otherwise:

- **mating** — straight horizontal if partners adjacent same-rank; else a routed line (the third-marriage connector, the
  avuncular cross-rank line, a consanguinity-loop line between non-adjacent cousins). A consanguineous routed union is a
  double line spanning the gap.
- **descent** — mating node → sib bar → per-child stubs (v1's shape, but from the mating node, wherever it is). The
  mating node of a routed union sits at the midpoint of its (non-adjacent) partners, so a routed union **with
  offspring** drops its sibship from that midpoint — the case v1 (and Stage D) deferred.
- Routing minimizes crossings and length and must not pass through a symbol.

This one capability subsumes v1's cross-join passes, the ghost, and the loop deferral: a loop/multi-mate/avuncular
relationship is just an edge the router draws.

### Determinism

Goldens are byte-compared, so the whole pipeline must be **bit-deterministic**: no RNG, fixed iteration order,
deterministic tie-breaks. The strength hierarchy is resolved by lexicographic relaxation, and x by a linear program the
default backend solves over exact rationals, so the failure mode that killed `alignped4` in v1 (floating solver settings
drifting the residual across a rounding boundary) cannot arise: the same model gives the same rationals on every
platform, and the final objective makes the optimum unique. `pos` is quantised to a fixed grid (Stage D), which also
absorbs the HiGHS backend's tolerance.

## Alternatives considered

- **Keep v1 + keep patching.** The current path: each new consanguineous / multi-mate shape is a new pass or a new
  deferral. Unbounded special cases; common shapes (cousin-marriage-with-siblings) stay deferred. Rejected — this plan
  exists to stop it.
- **Reduce to a tree by duplicating individuals** (kinship2 / CraneFoot). Draw every loop/extra-mate as a duplicate node
  with a dashed "same person" link, then the v1 recursion handles the tree. Deterministic and always terminates, and we
  already do it for avuncular (the ghost). Rejected as the *general* strategy: duplicated nodes are unfaithful to the
  source figure (which draws the person once), hurting the round-trip judge and reviewer trust.
- **Madeline's heuristic hybrid.** Recursion + reorder siblings to expose loop-mated members at sibship ends + in-place
  loop edges, duplicate only the residual. Produces the right output, and its *insights* (mated members go to ends; up
  to two per sibship) are adopted here — but as **emergent consequences of the soft constraints**, not as coded rules.
  Rejected as an architecture: it re-encodes the objective as bespoke heuristics, keeping the patch treadmill.
- **Compute ranks from the matings** (kinship2's `kindepth` plus marry-in alignment, v1's phase 1). Needs no generation
  data, which suits an importer that has none — and grus's importers do exactly this to synthesise generations. For
  drawing it is rejected: the IR already carries the drawn row, and where the computed rank differs, the figure is right
  and the computation is wrong (a detached branch, a half-sibling drawn a row lower).
- **Graphviz `dot` as-is.** The right optimization architecture, but pedigree-blind constraints (couples drift, children
  off-centre, graph-y bends). v2 is "dot's architecture with pedigree constraints", not dot.

## Open questions

- **Strength hierarchy.** *Resolved:* strengths are discrete (required > strong > medium > weak), resolved by
  lexicographic relaxation to routed, **not** a tunable weight vector — so there is nothing to tune and no weighted sum
  to solve (Stage E already found the hierarchy is structural). The one non-obvious ranking, descent-anchor (strong)
  above couple-adjacency (medium), is what produces the consanguinity-union-as-routed drawing; validate it against the
  review set, not by re-weighting.
- **x-assignment by relaxation sweeps.** Down/up barycentre sweeps with a per-row PAVA resolve, as shipped before the
  linear program: dependency-free and fast. Rejected: the sweeps balance centring but have no compactness term, so
  anything held only through couple midpoints — a half-sibling hinge's married-in spouse and the family descended
  through that couple, which can slide together without moving any child off its parents — drifted until the pass cap (a
  12-person pedigree 70 units wide, a 45-person one 458), and the result depended on the cap. Their fixed points also
  left descents beside their own sib bars (859 of them over 1,562 fuzzed pedigrees, against 37 under the program). A
  pattern-matched compaction pass fixed one shape and missed others of the class.
- **A quadratic x-solve.** Squared centring distances trade more gently than absolute ones, and HiGHS solves them. Not
  taken: a quadratic needs a floating solver, which gives up the exact reproducibility the goldens rest on, and the
  linear program's balance objective recovers the even spread absolute distances otherwise lack.
- **Edge-routing style.** Orthogonal polylines (matches Bennett) vs splines; how loop edges avoid symbols; whether a
  routed mating reads as clearly as an adjacent one.
- **Birth-order demotion.** *Done (Stage D):* demoted from ir.md's "one semantic horizontal fact" to a *soft*
  preference; the **data stays in the IR** (`Mating.offspring` order), surfaceable as a per-symbol number where position
  no longer conveys it. Low risk in practice — the IR is extracted from already-laid-out paper figures, so the extracted
  birth order rarely conflicts with a good layout. ir.md carries the matching edit.
- **Judge / eval.** A faithful, no-duplication, occasionally-routed layout should score *better* against the source than
  v1's deferrals — but the visual judge and the structural diff must be re-checked once v2 lands.
- **Migration.** *Done (Stage D):* v2 is the only engine — the `LayoutEngine` flag and v1's placement machinery
  (`_Placer` + the cross-join / relocation / spread / ghost-placement passes) are removed. Tier-1 goldens are
  byte-identical to v1 except `founder_sibship_marry_in` (its join couple now sits at true `couple_gap`); the whole
  review set draws. `pos` is quantised to a fixed grid for cross-platform byte stability.
