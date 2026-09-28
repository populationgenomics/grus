# Design: the pedigree IR

**Status:** current **Related:** `architecture.md` (stage boundaries), [`renderer.md`](renderer.md) (consumes the IR),
`eval.md` (diffs two IRs). Contract:
[`../../schema/proto/grus/models/pedigree.proto`](../../schema/proto/grus/models/pedigree.proto).

## Overview

The IR is a **meaning-only, normalized** model of a pedigree: who is in it, their gender and clinical status, and how
they relate — never geometry. It is a **protobuf** schema, serialized as **pbtxt / proto3-JSON**. It is the sole
interface between the three stages **and** a text form we reason over directly, so its fidelity *is* the product's
quality — and its shape must suit querying, not just render round-trip.

## Background

The IR sits between a vision LLM (which emits it) and a deterministic renderer (which consumes it) and doubles as the
golden-set and round-trip artifact. That imposes four requirements at once, which no existing format meets together:
meaning-only, full visual vocabulary, reliably LLM-emittable, and a precise contract a renderer can trust. So we own the
schema, standing on established concepts.

## Non-goals

- **No presentation.** No coordinates, spacing, canvas size, colours, fonts, or symbol positions. The renderer derives
  all geometry.
- **No genetic inference.** The IR records what the figure *shows* (including an explicit obligate-carrier annotation if
  drawn), not what could be computed from the pedigree.
- **Not a wire/RPC or at-rest RMW format.** IRs are authored/emitted whole; see Serialization.

## Design

### Meaning-only, and why

Carrying geometry would let the pipeline preserve *pixels* while corrupting *meaning* — a wrong diagram that looks
almost the same would pass a visual judge. Meaning-only removes that failure mode. **Sibling birth order** (left→right)
is retained as `Mating.offspring` list order, but it is a **soft layout preference**, not a load-bearing horizontal
fact: a marriage can force a sibling to a sibship end and reorder it (the layout honours birth order only where a good
layout allows — see [`layout-v2.md`](layout-v2.md)). Where the order must be conveyed unambiguously it is surfaced as a
per-symbol number, never by x-position alone. The as-drawn **generation** number is stored as a *label* (identity, see
below), but the **topological** generation used for layout is *derived* by the renderer from the graph — the number is
never trusted for positioning.

### Normalized, and why

The IR is a text form we apply reasoning to, so each fact is a typed field tagged as what it is, decomposed, stored
once:

- **Identity is a `Position` — `generation:int` + `index:int`** (I→1, II→2; the arabic index within the generation),
  *not* a composite `"II-2"` string. `Position` is also the cross-message **reference key**: matings and offspring name
  a `Position`, so there is no separate opaque id to keep in sync, and nothing re-stores the number as an annotation.
  Every drawn node gets a `Position` (assign an index even when the figure omits the printed number); the pair is unique
  within a pedigree. An IR **imported** from a data format (PED, kinship2, Phenopackets `Family`, Open Pedigree —
  [`convert.md`](convert.md)) has no as-drawn numbers, so the importer *synthesises* the `Position` (generation by
  aligned depth, index by source order) and keeps the source id in `external_id`; `Provenance.source_format` records
  that the labels are assigned, not drawn. The renderer never trusts `generation` for placement either way.
- **Gender vs sex.** The symbol denotes **gender identity** (Bennett 2022): `gender` ∈ man/woman/nonbinary/unknown
  (□○◇). **Sex assigned at birth** is the orthogonal optional `sex_assigned_at_birth` (AMAB/AFAB/UAAB). Storing "man",
  not "square", keeps it meaning-only. Pre-2022 figures draw phenotypic sex; they map onto `gender` by the standard's
  assume-aligned rule (2022 Box 1.1.f), with `sex_assigned_at_birth` unset — and the moment a figure annotates AMAB/AFAB
  or draws nonbinary, it is captured faithfully.
- **Clinical status is `repeated Condition{name, status, inheritance, onset_age}`**, not a flat affection+carrier pair.
  A person can be affected with one condition and a carrier of another — the 2022 standard draws this by partitioning
  the symbol into per-condition fill regions (and moved carrier itself from a dot to a fill). One `Condition` = one
  region keyed to the legend; `status` ∈ unaffected/affected/carrier/presymptomatic/unknown. The single-condition case
  is one entry. `inheritance` (AD/AR/XLD/XLR/Y/mito, absent = unspecified) records the condition's mode **only when the
  figure states it** — never inferred from the pedigree pattern (see no-inference non-goal); the carrier *glyph* is a
  render choice, not implied by this field. `onset_age` is the as-drawn age at onset/diagnosis for **this** condition,
  verbatim ("42", "40s", "prenatal") — kept on the condition rather than as a floating annotation so the age↔condition
  binding survives.
- **Labels are `repeated Label{text, kind}`** (kind ∈ family/panel/gene/phenotype/other), not a single `title` — a
  pedigree often carries several at once, each a distinct fact for reasoning; the renderer picks the display title. The
  panel label lives here (as-drawn meaning), not in `Provenance`.

### Graph model

Mating-centric, so the visual vocabulary is representable directly:

- `Individual` — node: `Position` (`generation`/`index`), `gender`, optional `sex_assigned_at_birth`, `conditions`,
  `deceased`, `proband`, `consultand`, `documented_evaluation` (the NSGC `*` — finding documented / records reviewed),
  optional `external_id` (an opaque study/sample id printed for the person — passthrough; identity + reference stay
  `Position`), and full-coverage axes below; plus `annotations` (typed `text` + kind) for every remaining piece of
  as-drawn text (genotype, variant, age, karyotype, gestational age, pronoun, …).
- `Mating` — edge: an **optional** `partner_a` and **optional** `partner_b` (a single drawn parent is one partner — no
  phantom stand-in; **both absent** is a *founder sibship*: the offspring are siblings via an undrawn parent couple, so
  a partnerless mating needs ≥2 offspring and no stray lone `partner_b`), `consanguineous` (explicit, *not* inferred),
  `status` (separated/divorced), `childlessness`, and `offspring` in birth order.
- `Offspring` — child edge: a `Position`, optional `twin_group` + `twin_type` (zygosity — MZ/DZ/trizygotic/unknown),
  `parentage` (biological / adoptive / donor — solid vs dashed descent) and `adoption` (in / out / by-relative).

An individual with multiple mates appears in multiple `Mating`s. `father`/`mother`/spouse/children maps the renderer
needs are *derived* from matings, keeping a lossless mapping to the universal PED graph core.

### Figure scope: one figure is a *set* of pedigrees

A figure maps to **0..N** pedigrees. Rare-disease figures routinely draw several unrelated families (Family 1/2/3)
and/or a pedigree beside non-pedigree panels. So the extraction unit is a **`PedigreeSet`** — `repeated Pedigree` plus a
figure-level `Provenance` (source figure URI + DOI). Each `Pedigree` carries its as-drawn labels in `labels`.

- **K = 0** — a candidate that holds no pedigree; extraction returns an *empty* set (fail-loud over invent). The VLM
  enumerating zero pedigrees *is* the negative verdict (`corpus.md`).
- **K ≥ 1** — one or several families, and/or a pedigree among non-pedigree panels.

Families are **separate `Pedigree` messages, never one merged graph**: `Position` uniqueness and referential integrity
are *per-pedigree* invariants (the `grus.ir` loader runs them per `Pedigree`), so distinct families reusing I-1/II-1
never collide.

### Citations: where the record came from

A record may say where its content came from: the figure it was read from, the caption, or a sentence elsewhere in the
paper. Citations are **optional** and **provenance, not meaning**: nothing needs them, the renderer ignores them, and
`diff_set` ignores them, so a record with and without citations draws and diffs the same. They follow the case-record
pattern (themis `caserecord`: model-local ids into one normalised list, referenced by id), so a grus record embedded in
a case record grounds its facts the same way.

```proto
message Citation {           // in PedigreeSet.citations, the normalised list
  string id = 1;             // local ("c1"), unique in the set; what evidence and supports refer to
  string document_id = 2;    // the caller's id for the source document (e.g. a corpus paper id); never model-written
  oneof anchor {
    string quote = 3;        // verbatim text that appears in the document
    Region region = 4;       // an area of one page
  }
}
message Region {             // page units, origin top left: PDF points on a PDF page, pixels on a bare image
  int32 page = 1;            // 0-based
  float x0 = 2; float y0 = 3; float x1 = 4; float y1 = 5;
}
message Support {            // one kind of fact, about some people or the whole pedigree, and what backs it
  repeated string citations = 1;
  string field = 2;          // a path into the IR, from a closed list (below)
  optional string condition = 3;       // which condition, when `field` starts "condition."
  repeated Position individuals = 4;   // scope; none of individuals/matings = the whole pedigree
  repeated MatingRef matings = 5;      // a couple, by its partners' positions
}
message MatingRef { optional Position partner_a = 1; optional Position partner_b = 2; }

// PedigreeSet gains: repeated Citation citations; repeated string evidence;
// Pedigree gains:    repeated string evidence;  repeated Support supports;
```

Two levels of evidence, and an optional third:

- **`PedigreeSet.evidence`**: the figure as a whole, its region on the page (or the whole image) and its caption. The
  extraction harness supplies these; it knows where it found the figure and what the caption says.
- **`Pedigree.evidence`**: the panel this pedigree is drawn in, which only the reader knows in a multi-panel figure.
- **`Pedigree.supports`**: which facts came from where, when that matters, mainly for a fact the figure does not draw (a
  stillbirth the caption states, a condition named only in the caption). **One field per support**, and one sentence may
  back several supports by sharing a citation id: "II-1, the proband, and her affected sibs II-2 and II-3" is `proband`
  for II-1 and `condition.status` for II-1..II-3, not one support claiming every field for everyone.

`field` is a path into the IR, checked against a closed list: the `Individual` fields (`gender`, `deceased`, `proband`,
`consultand`, `documented_evaluation`, `reproductive_outcome`, `reproductive_role`, `count`, `count_unspecified`,
`sex_assigned_at_birth`, `external_id`, `annotations`), the `Condition` fields as `condition.status`, `condition.name`,
`condition.inheritance` and `condition.onset_age` (with `condition` naming it), the `Mating` fields (`consanguineous`,
`status`, `childlessness`, `annotations`; a support's scope says whose `annotations` it means), the `Offspring` fields
(`twin_group`, `twin_type`, `parentage`, `adoption`) scoped by the child, and `labels` for the pedigree. The loader
fails loud on an unknown id, an unknown path, a person or couple not in the pedigree, a condition no one in scope has,
or a region with x1 \<= x0 or y1 \<= y0. That a quote appears verbatim in its document is checked where the document
text is available (the store), as case records check theirs; the IR alone cannot.

A `Region` locates the **source**, not the drawing, so it does not break meaning-only: it says where on the paper's page
a pedigree was read, never where anything sits in a rendering.

### Concept coverage — the full Bennett vocabulary, in the schema

The schema represents the **entire** Bennett/NSGC concept space (2008 Figs 1–4 + the 2022 revision), so no figure is
unrepresentable — **independent of** what the extraction prompt currently emits or the renderer currently draws. Those
two implement the vocabulary *progressively*, driven by measured corpus incidence (`corpus.md`); the IR does not wait
for them. Covered: gender + sex-assigned-at-birth (incl. nonbinary, VSC via `conditions` shading); affection, carrier,
presymptomatic, multi-condition partition; deceased, proband, consultand; reproductive outcomes (pregnancy, stillbirth,
SAB, TOP, ectopic) and count-collapsed sibships; consanguinity, separated/divorced, single parent, childlessness/
infertility; twins (MZ/DZ/unknown); adoption (in/out, dashed vs solid descent); ART donor/surrogate; family/panel/gene
labels. Evolution is additive-only from the first public release (`buf breaking`, FILE); the pre-release rewrites (slice
22\) are history.

### Serialization

**Text, not binary.** grus has no read-modify-write, so we use the readable text projections: **pbtxt** is the canonical
golden/human surface (comment-able, diffable), **proto3-JSON** is the LLM emission surface (structured-output → parse →
validate). The lost-unknowns caveat of text projections does not bite because nothing does a lossy RMW of an IR.

### Validation, split by what protobuf can express

- **Field-local / single-message** rules are **protovalidate** options on the `.proto`: `Position` components ≥ 1;
  `gender` and `Condition.status` are real values not the zero sentinel; `twin_type` set iff `twin_group` present;
  `Individual.count` ≥ 1 when set (1 is one person, the same as absent) and never together with `count_unspecified` (a
  known and an unknown number at once).
- **Graph invariants** live in the `grus.ir` loader, which fails loud: every referenced `Position` exists among the
  pedigree's individuals; `(generation, index)` is unique per pedigree; a mating's two partners are distinct; offspring
  exist; a child's generation exceeds each parent's, and one mating's offspring share a generation. Generation is the
  drawn row, so a child is usually one below its parents but may be further down — drawn beside half-siblings whose
  other parent is a generation lower, or across omitted generations — and generation increasing along every descent edge
  also rules out ancestry cycles. Several probands are valid (a family ascertained through more than one member;
  imported cohort data has them). Citations, when present, are checked the same way (`validate_set`): each is
  well-formed (a non-empty id, exactly one anchor, a region with page ≥ 0 and x1 > x0, y1 > y0 — protovalidate), ids are
  unique across the set, every evidence and support id resolves, a support's `field` is on the closed path list
  (`grus.ir.SUPPORT_FIELDS`, exported with `grus.ir.SUPPORT_CONDITION_PREFIX` so a prompt can list the valid paths), it
  names a `condition` iff the field is `condition.*` and someone in scope has it, every person and couple in scope is in
  the pedigree, an offspring field is scoped by the child, and a mating field by couples. Each failure names the
  pedigree and the item. Enum `*_UNSPECIFIED` zeros are sentinels, never domain values; rare axes are `optional` (absent
  = the natural default — LIVE / BIOLOGICAL / CURRENT / …), so the sentinel is never emitted.

## Alternatives considered

- **PED / LINKAGE** — universal but too lean (parent-child graph only; no deceased/proband/carrier/twin/consanguinity).
  Kept as a lossless *export* target, not the IR.

- **CanRisk / BOADICEA** — richer but bloated with cancer risk factors and tied to that tool.

- **Phenopackets v2 `Family` as the IR** — assessed field-by-field against the primary protos
  ([`../research/phenopackets-vs-bespoke-ir.md`](../research/phenopackets-vs-bespoke-ir.md)). Its pedigree core is a
  verbatim PED row plus one family-level `consanguinous_parents` bool: no mating entity (so no founder sibship,
  childless or separated couple, same-gender parents, per-couple consanguinity, birth order), no twins, adoption,
  pregnancy outcomes, carrier/presymptomatic, consultand, counts, or as-drawn labels; no extension slot on any core
  message; a required CURIE on every disease and a `MetaData` block per relative. About 60% of this schema's surface
  would have no home. Rejected as the IR; kept as an **importer/exporter target** (`convert.md`).

- **GA4GH Pedigree Standard (Individual + KIN-coded Relationship)** — the edge vocabulary is the best available
  (biological/adoptive/donor/gestational parent, MZ/polyzygotic multiple birth, consanguineous/separated partner) and we
  cite its KIN codes on our enum members for a mechanical future export. Rejected as the IR: its only serializations are
  a draft FHIR IG and a protobuf PR closed unmerged in 2025; edge-only modelling has no sibship entity; `affected` is a
  boolean; "reduced form" is advice, so one pedigree has many encodings (hostile to golden diffs and structured output).

- **FHIR `FamilyMemberHistory`** — heavyweight and relative-to-a-patient rather than a graph; not pursued.

- **A custom DSL / grammar** — nicer to read, but a bespoke parser plus lower first-pass LLM validity. protobuf gives
  structured-output emission and principled evolution instead.

- **A single opaque string id + parse "II-2" at use** — rejected: normalize once (numbers as numbers), don't re-parse.

- **Keep `title` a single string** — rejected: a pedigree's family/panel/gene labels are distinct facts for reasoning.

- **Citations as evidence lists on every entity** (case records' shape: `repeated string evidence` on each message).
  Rejected for grus: one sentence usually covers several people, so the same quote repeats across entities, and an id on
  an individual does not say which of its fields it grounds. A `Support` names the field and lists its scope once.

- **A support with several fields.** Rejected: fields times people over-claims ("II-1 is the proband; II-2 and II-3 are
  affected" would make all three probands). One field per support, sharing citation ids.

- **Figure-level citations only.** Simpler, but it can never say which recorded facts came from outside the drawing,
  which is what curation of caption-stated facts needs.

## Open questions

- **ART multi-party parenthood.** `reproductive_role` + `parentage` records donor/surrogate roles but does not fully pin
  the 3-party genetic≠gestational≠rearing cases of Bennett 2022 Fig 5. KIN models these as *edges* (sperm/ovum donor,
  gestational carrier → child); the additive fix is a `PARENTAGE_GESTATIONAL` member on the `Offspring` edge, which
  would make `reproductive_role` derivable. Near-zero incidence in rare-disease pedigrees; revisit if the corpus shows
  ART.
