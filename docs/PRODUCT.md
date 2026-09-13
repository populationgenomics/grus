# grus — product north star

## What it is

grus converts between a **pedigree figure** (a family diagram in a genetics paper) and a **structured, meaning-only
intermediate representation** (the IR), in both directions:

- **extract**: figure image → IR (a Claude vision call).
- **render**: IR → a well-laid-out SVG pedigree (deterministic code).

Quality is measured by **round-tripping**: figure → IR → figure, then judging whether the two figures are *semantically
equivalent*. A faithful pipeline reproduces the meaning; a broken one is caught by the judge.

```
figure ──extract──▶ IR ──render──▶ figure′
  └────────────────── judge: figure ≡ figure′ ? ──────────────────┘
```

## Why these three principles

1. **Deterministic renderer, not an LLM.** "Well laid out" is a constraint-satisfaction graph-drawing problem; code
   gives reproducible, correct geometry and makes a failed round-trip diagnostic of *extraction*, not rendering.
   (docs/design/renderer.md)
1. **Meaning-only IR.** The IR captures who is in the pedigree, their gender/clinical status, and how they relate —
   never coordinates or layout. Carrying geometry would let the pipeline preserve *pixels* while corrupting *meaning*,
   and a visual judge would wave it through. Meaning-only forces the system to get the semantics right to score.
   (docs/design/ir.md)
1. **The judge compares meaning, and a human is the oracle.** The primary automated metric is a *structural semantic
   diff* of two IRs, not a pixel compare. But the judge is the weakest link, so we bootstrap with a human review UI
   (three-way verdict + reason) whose labels are the gold standard that later calibrates any automated judge.
   (docs/design/eval.md)

## Shape

- **Language**: Python only. (docs/design/architecture.md)
- **IR**: a protobuf schema, serialized as pbtxt/JSON for human-readable goldens and as the LLM emission surface.
  Additive-only evolution. (docs/design/ir.md, schema/proto/)
- **Renderer**: a port of the kinship2 `align.pedigree` recursive nuclear-family layout + Bennett symbol drawing.
  (docs/design/renderer.md)
- **Corpus**: mined from CPG's existing 38k-paper OA figure cache via a cheap funnel (caption filter → CLIP classifier →
  Claude enrichment); figures stay in GCS, referenced by URI. (docs/design/corpus.md)

## Scope

Vocabulary is tiered against the Bennett/NSGC standard (docs/design/ir.md):

- **v1 renderer** draws **tier 1**: gender (□○◇), affected/unaffected, deceased, proband, mating/sibship lines, birth
  order, generation numbering — acyclic, single-mate layout.
- **The IR covers tier 1 + tier 2** (carrier, consanguinity, multiple mates, twins) from day one, so extraction can
  transcribe them ahead of the renderer. When the renderer can't yet draw a feature, the round-trip diff flags it — a
  visible known-limitation, not a silent lie.

## Non-goals

- Not a pedigree *editor* or clinical tool — no interactive authoring beyond the review/golden UI.
- Not a general graph-drawing library — layout is pedigree-specific.
- The IR does not encode presentation (coordinates, colours, canvas size, fonts).
- v1 does not lay out consanguinity *loops* (shared drawn ancestor → cyclic graph); detect-and-defer.
- No closed-access figures committed; the programme repo (corpus, evals, review UI) stays private.

## Release surface

The figure-independent core ships as the public `grus` package: the schema, `grus.ir`, `grus.render`, and `grus.convert`
importers (PED, kinship2, Phenopackets `Family`, Open Pedigree), with the `grus` CLI. Extraction, the corpus funnel,
eval and the review UI are CPG-internal (docs/plans/34-public-release.md).

## Phase plan

- **Phase A** (docs/plans/01–06): scaffold → IR → renderer (tier 1) → extractor → review UI → round-trip harness. Ends
  with the human labelling the first round-trips.
- **Phase B** (docs/plans/07–10): corpus funnel → automated judges calibrated to human labels → golden set + decomposed
  per-stage metrics.
