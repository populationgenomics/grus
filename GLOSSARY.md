# Glossary

Shared terms across grus docs and code. Pedigree conventions follow Bennett/NSGC (see docs/design/ir.md references).

## Pedigree domain

- **Pedigree** — a family diagram showing individuals, their gender, clinical status, and relationships across
  generations.
- **Individual** — one person, drawn as a symbol: square (man), circle (woman), diamond (nonbinary or unknown gender).
  The symbol denotes gender identity (Bennett 2022); sex assigned at birth is a separate optional annotation.
- **Affection status** — whether an individual has the condition: unaffected (empty symbol), affected (filled), unknown
  ("?").
- **Carrier** — heterozygous for a recessive allele; drawn with a central dot. **Obligate carrier** — a carrier inferred
  from the inheritance pattern rather than tested.
- **Proband** — the first affected individual who sought evaluation, marked with an arrow + "P". **Consultand** — the
  individual who consulted (may be unaffected); also an arrow.
- **Deceased** — drawn with a diagonal slash through the symbol.
- **Mating / mating line** — a partnership between two individuals, drawn as a horizontal line. **Consanguineous** —
  partners who are blood relatives; drawn as a *double* mating line.
- **Sibship** — the set of children of one mating, hung below it on a descent/sibship line, in birth order left→right.
- **Twins** — offspring sharing a birth: **MZ** (monozygotic, a joining bar), **DZ** (dizygotic), unknown zygosity
  ("?").
- **Generation** — a horizontal row, numbered with Roman numerals (I, II, III …); individuals within a generation
  numbered with Arabic numerals (II-1, II-2 …).
- **Founder** — an individual with no parents drawn; sits at the top of a lineage.

## Project

- **IR** — the intermediate representation: the meaning-only protobuf model of a pedigree
  (schema/proto/grus/models/pedigree.proto). Serialized as **pbtxt** (human-readable goldens) or **proto3-JSON** (the
  LLM emission surface).
- **extract / extractor** — figure → IR, a Claude vision call (docs/design/extraction is folded into architecture +
  slice 04).
- **render / renderer** — IR → SVG, deterministic layout + drawing (docs/design/renderer.md).
- **Round-trip** — figure → IR → figure′ (→ IR′), the label-free quality signal.
- **Structural semantic diff** — a proto-aware comparison of two IRs (match individuals, compare
  gender/status/relationships) yielding per-element precision/recall — the primary automated metric.
- **Judge** — the equivalence verdict. **human** (the oracle, via the review UI), **structural** (the semantic diff,
  primary automated), **visual** (an LLM comparing images, coarse secondary).
- **Golden / golden set** — hand-authored correct IRs for a small diverse figure set; the ground truth for decomposed
  per-stage metrics.
- **Judgment** — a proto record of one equivalence verdict (pair, verdict, reason, judge id, provenance).
- **Funnel** — the corpus-construction pipeline: caption filter (1a) → CLIP classifier (1b) → Claude enrichment (2)
  (docs/design/corpus.md).
- **Tier 1 / 2 / 3** — the staged Bennett vocabulary scope (docs/design/ir.md).
