# Design: the pedigree IR

**Status:** current **Related:** `architecture.md` (stage boundaries), [`renderer.md`](renderer.md) (consumes the IR),
`eval.md` (diffs two IRs). Contract:
[`../../schema/proto/grus/models/pedigree.proto`](../../schema/proto/grus/models/pedigree.proto).

## Overview

The IR is a **meaning-only, normalized** model of a pedigree: who is in it, their gender and clinical status, and how
they relate — never geometry. It is a **protobuf** schema, serialized as **pbtxt / proto3-JSON**. It is the sole
interface between the three stages **and** a text form we reason over directly, so its fidelity *is* the product's
quality — and its shape must suit querying, not just render round-trip. What a person's symbol shows is read through the
figure's own key: a fill keyed to a disease records a clinical status, one keyed to a variant records a genotype, and
one keyed to anything else ("Control") records only that, so no mark is forced into a disease status the figure does not
draw.

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
- **What a person's symbol shows is a list of marks**, each naming a phenotype or other entry of the figure's key, not a
  flat affection+carrier pair. A person can be affected with one condition and a carrier of another — the 2022 standard
  draws this by partitioning the symbol into per-condition fill regions (and moved carrier itself from a dot to a fill)
  — so one mark is one region keyed to the legend, and the single-condition case is one mark. What a mark says, and how
  the key declares what it means, is under "The key" below.
- **Labels are `repeated Label{text, kind}`** (kind ∈ family/panel/gene/phenotype/other), not a single `title` — a
  pedigree often carries several at once, each a distinct fact for reasoning; the renderer picks the display title. The
  panel label lives here (as-drawn meaning), not in `Provenance`.

### Graph model

Mating-centric, so the visual vocabulary is representable directly:

- `Individual` — node: `Position` (`generation`/`index`), `gender`, optional `sex_assigned_at_birth`, `marks`,
  `deceased`, `proband`, `consultand`, `documented_evaluation` (the NSGC `*` — finding documented / records reviewed),
  optional `external_id` (an opaque study/sample id printed for the person — passthrough; identity + reference stay
  `Position`), and full-coverage axes below; `genotypes` (below); plus `annotations` (typed `text` + kind) for every
  remaining piece of as-drawn text (genotype, variant, age, karyotype, gestational age, pronoun, …).
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
  optional string key_id = 8;          // a KeyEntry id, iff `field` starts "mark." or "key."
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
  stillbirth the caption states, a phenotype named only in the caption). **One field per support**, and one sentence may
  back several supports by sharing a citation id: "II-1, the proband, and her affected sibs II-2 and II-3" is `proband`
  for II-1 and `mark.status` for II-1..II-3, not one support claiming every field for everyone.

`field` is a path into the IR, checked against a closed list: the `Individual` fields (`gender`, `deceased`, `proband`,
`consultand`, `documented_evaluation`, `reproductive_outcome`, `reproductive_role`, `count`, `count_unspecified`,
`sex_assigned_at_birth`, `external_id`, `annotations`), a person's mark as `mark.key_id` (that they show the entry at
all, all an other mark states), `mark.status`, `mark.grade` and `mark.onset_age`, and the key entry as `key.text`,
`key.inheritance` and `key.grades` (each with `key_id` naming the entry; a `key.*` path scopes the whole pedigree only,
since a text or a mode belongs to the entry, not to one person), the `Mating` fields (`consanguineous`, `status`,
`childlessness`, `annotations`; a support's scope says whose `annotations` it means), the `Offspring` fields
(`twin_group`, `twin_type`, `parentage`, `adoption`) scoped by the child, and `labels` for the pedigree. The loader
fails loud on an unknown id, an unknown path, a person or couple not in the pedigree, a key entry no one in scope shows,
a fact the cited entry or mark cannot hold (an other entry's inheritance, a grade on a mark that has a status), or a
region with x1 \<= x0 or y1 \<= y0. That a quote appears verbatim in its document is checked where the document text is
available (the store), as case records check theirs; the IR alone cannot.

A `Region` locates the **source**, not the drawing, so it does not break meaning-only: it says where on the paper's page
a pedigree was read, never where anything sits in a rendering.

### Genotypes: loci declared once, a genotype per person

A figure often prints genotypes beside its people: `c.797G>A/+`, `M1/M2`, `+/-`, `T/G`. Two data formats grus imports
carry the same thing in structured form: PED's marker columns and a Phenopacket's variant interpretations. All three
describe a **genotype table**: rows are people, columns are loci, and each cell is what one person carries at one locus.
Typed annotation text keeps what is printed but not what it means. `M1/M2` means something only together with the legend
("M1, c.1234A>G; M2, c.2000del"), and a reader of the IR alone cannot connect the person to a variant. In a sample of
published pedigrees, about 60% of genotype labels were shorthand or bare bases that need such a key.

So the IR records the table directly, in three parts:

- A **locus** is a place people are genotyped at: one variant, a gene with several alleles, or a marker. It is declared
  once per set, in `PedigreeSet.loci`, with a local id, the name and gene as printed, and its alleles. This is the same
  pattern as key entries (below), for the same reasons: one place for a fact shared by many people, an id for citations
  to point at, and no free-text join.
- An **allele** is a symbol genotypes use (`+`, `M1`, `G`, `★`, `1`) and what the source says it stands for: a printed
  name, whether it is the reference, and any resolvable identifiers it prints (HGVS, rsID, ClinVar, ClinGen, SPDI, VRS).
  An allele may be several variants in cis, a haplotype, named as such (`c.[76A>C;83G>C]`).
- A **genotype** is one person's alleles at one locus, as copies. Zygosity is how often a symbol repeats, so no separate
  field can disagree with it: `[M, M]` is homozygous, `[M, +]` heterozygous, `[M]` hemizygous. One or two copies.

**Copies not stated.** A source often says that a person carries a variant without saying how many copies: a fill keyed
to the variant, "mutation present", a Phenopacket with no allelic state. Copies cannot record that, since `[M]` already
means hemizygous, and guessing `[M, +]` would state a zygosity the source does not. So a genotype may say that its
copies are not stated: it lists each allele the person carries once, and states nothing about how many copies of each
there are or what the other copy is. Such a genotype has no phase set, since phase needs slots, and the diff never
treats it as equal to a genotype that states copies: "carries M" and `[M, +]` are different claims, even though one is
consistent with the other.

```
locus l1 "c.1234A>G" gene SLC26A4: + (reference), M1 "c.1234A>G"
locus l2 "c.2000del" gene SLC26A4: + (reference), M2 "c.2000del"

II-1  l1 [M1, +] phase set a     annotation "M1/M2" shows l1, l2
      l2 [+, M2] phase set a     → M1 and M2 on different haplotypes: in trans
```

**Phase.** Whether two variants are in cis or in trans decides whether a person with two recessive variants is affected,
and figures and papers state it ("compound heterozygous", "maternally inherited"). A genotype may name a **phase set**,
as VCF does with `PS`: one person's genotypes in the same phase set line up slot by slot, so slot *i* across them is one
haplotype. In the example, `M1` is in slot 0 and `M2` in slot 1, so they are in trans; `l2 [M2, +]` would put them in
cis. A genotype without a phase set states no phase. Because slots align, genotypes sharing a phase set have the same
number of copies. Phase relates heterozygous genotypes only: a homozygous or hemizygous member of a phase set states
nothing by being there, and a phase set with one heterozygous genotype states no phase. Which parent a haplotype came
from is not recorded (see Open questions).

**Annotations keep the drawn text.** A genotype records meaning; the annotation still records what the figure prints,
and the renderer draws only the annotation. A `GENOTYPE` or `VARIANT` annotation may name the loci its text shows,
linking the label to the genotypes, and one label can show several (`M1/M2` over two variant loci). The genotype may say
more than the label: a figure that prints `M1/+` beside a legend that gives the HGVS has both. An imported record has
genotypes and no annotation, and so draws none; drawing genotypes is a renderer choice, not a property of the IR.

**Recorded as stated, never inferred.** An untested person has no genotype; an obligate carrier is not filled in; a
hemizygous call is not checked against sex assigned at birth, since the source states the copies and the IR does not
second-guess them.

**Checks** (loader, loud):

- locus ids are unique and distinct from citation and key entry ids;
- every locus can be told apart by what it states, so two records' loci can be matched: a named locus's (gene, name) is
  declared once, and unnamed loci, whose alleles alone are named, may share a gene (a legend often names only the
  alleles) but not every allele;
- allele symbols are unique within a locus, and at most one allele is the reference;
- every genotype's locus resolves, each of its alleles is one of that locus's symbols, and a person has at most one
  genotype per locus;
- genotypes sharing a phase set have equal copies, and a genotype whose copies are not stated lists each allele once and
  is in no phase set;
- an annotation names loci only if it is a `GENOTYPE` or `VARIANT` annotation of a person genotyped at each of them, and
  a mating's annotation names none;
- a declaration no genotype references is an error, as for key entries. A locus is a column of the table: declaring one
  says someone was genotyped there. A variant a figure names without saying who carries it ("SLC26A4 c.1234A>G" over the
  pedigree) is a pedigree-level fact, recorded as a gene label, not an empty column; allowing empty columns would make
  "declared" and "someone carries it" two facts with nothing to tell them apart.

The identity those checks and the diff use is exported from `grus.ir`, so a caller merging several sets (one per figure
of a paper, say) can declare one locus per identity and re-point genotypes at it.

**Consumers.** `diff_set` compares a person's genotypes by what they state: each locus by its (gene, name) — and an
unnamed one by its alleles too — each allele by its symbol, printed name and whether it is the reference, unphased
copies as a multiset and a phase set as its multiset of haplotypes. So ids, phase-set names and swapping every slot of a
phase set at once do not register as differences. An allele's identifiers are not compared: they are other spellings of
what its name states, and one reader may copy them where another does not. A support cites genotypes or locus
declarations with paths `genotype.*` (scoped to people) and `locus.*` (the whole pedigree) and names the loci, several
when one sentence covers several ("M1 and M2 in trans" backs the phase at both), as `mark.*` and `key.*` paths name a
key entry. Stored layouts ignore genotypes and an annotation's loci, since layout reads only the annotation's text.

### The key: what a person's marks mean

A figure draws marks on its people — a filled symbol, a half or a quadrant, a hatch, a dot — and its key says what each
mark means. Usually the meaning is the clinical status of a disease: filled is affected, a hatched half is a carrier of
the second condition, and a figure with no key at all means "filled = affected". But published figures use the key for
other things too, and an IR that assumes every mark is a disease status has no right answer for them:

- **A fill keyed to a variant**, not a disease: "■ p.Arg256Cys, ▨ p.Thr423Met" on half symbols, quadrants keyed to
  genotypes at three genes. Recording the variant as a disease forces a reader to call each filled person `AFFECTED` or
  `CARRIER`, a clinical judgement the figure does not draw. What it says is that the person carries the variant.
- **A grade the standard has no symbol for**: grey for "suspected from history, not examined", or a key's "?" meaning
  "not tested". `AFFECTED` and `UNKNOWN` both misstate these.
- **An entry that is not about a phenotype at all**: "Control", "persons examined". Recorded as a condition, "Control:
  affected" means "is a control", and a consumer counting affected people — a segregation check — counts the controls.

These are not rare. Reading 751 published figures twice with the same model and prompt, the two readings disagreed on a
condition or a status in 52 figures, and about two thirds of those were figures like these, where a reader had to force
the key into the disease-status enum and the two readings forced it differently.

So the IR separates the key from what it means. A **key entry** is one thing the figure's key defines, declared once per
set with a local id (`k1`) and its text as printed, and it says what kind of thing it denotes:

- a **phenotype** — a disease, trait or finding, named as printed — with its mode of inheritance (AD/AR/XLD/XLR/Y/ mito)
  when the figure states it, never inferred from the pedigree pattern, and any **grades** its key defines beyond the
  standard statuses (unaffected, affected, carrier, presymptomatic, unknown);
- a **genotype** — a genotype at a declared locus, as a pattern: "carries M1", or exactly `[M1, M1]`;
- **other** — anything that is neither: "Control", "examined". Its text is all it says.

A person's **marks** say which phenotype and other entries their symbol shows, one per entry. A phenotype mark also says
what is true of the person: a standard status (the grades a symbol can draw without a key) or one of the entry's grades,
and the age at onset when it is drawn — the as-drawn age for this person and phenotype, verbatim ("42", "40s",
"prenatal"), kept on the mark so the age stays bound to its phenotype. An other mark says only that the person shows it.
Genotype entries are not marked: a person shows one when their genotype matches it (below).

```
key k1 phenotype "hearing loss"     grades: "suspected from history, not examined"
key k2 genotype  "p.Arg256Cys"      carries M1 at l1
key k3 other     "Control"

II-1  mark k1 affected               genotype l1 carries M1, copies not stated  → shows k2
II-2  mark k3
I-2   mark k1 "suspected from history, not examined"
```

The rest of this section takes the decisions in turn.

**Declared once, by id, per set.** A key entry's text, kind and inheritance belong to the entry, not to each person who
shows it. Stored per person they would repeat on every mark, a spelling difference would silently split one entry in
two, and a renderer legend or a citation could find the entry only by joining on free text. So entries are declared once
with local ids, as citations and loci are, and at set level: a multi-family figure usually has one key ("EB phenotypes
in families 1 and 2"), and both families' people reference the same entries.

**An unnamed phenotype has empty text.** The common "filled = affected" figure has no key at all: its implied key is one
phenotype entry with empty text, so every mark references an entry and there is no special case. A figure's plain
"affected" beside named entries (often carrier-only conditions) is the same unnamed phenotype.

**Meaning-only.** An entry records what the key says a mark means, never what the mark looks like: not its colour, its
pattern or which quadrant it fills. Which region an entry occupies is the renderer's choice.

**A genotype is shown, not marked.** A fill keyed to a variant states a genotype, and the IR already has one place for
genotypes: the genotype table (above). Marking the person as well would store the fact twice, and the two copies could
disagree. So a genotype entry is a pattern over the table, and a person shows it exactly when their genotype at its
locus matches. A pattern either states copies, matching only a genotype with the same copies — so a key that grades
zygosity ("■ homozygous, ▨ heterozygous") is two entries at one locus — or states only the alleles carried, matching any
genotype that carries them. The reader records the genotype the key implies; a fill that says only "carries the variant"
is a genotype whose copies are not stated. The cost: a person genotyped as carrying a keyed variant always shows the
entry, so a figure that prints a carrier's genotype but leaves the symbol unfilled cannot be recorded as drawn. That is
a figure contradicting its own key, and the record keeps the genotype.

**A grade belongs to its phenotype.** A key-defined grade qualifies a phenotype — "suspected" hearing loss is still
about hearing loss — so it is declared on that phenotype's entry, and a person's mark names one of the declared grades
in place of a standard status. Declaring grades keeps a free-text grade from splitting on spelling, as declaring entries
does for names, and the loader can check each mark's grade. A severity scale ("tier 1/2/3") is a set of grades too. An
entry that says nothing about any phenotype or genotype in the figure is an other entry; an entry that qualifies a
phenotype's status is a grade of it. Only those two homes exist, so two readers of one key do not have a third to split
on.

**Status stays the standard's.** A consumer asking who is affected reads phenotype marks with the `AFFECTED` status, and
nothing else: controls, examined persons, carriers of a keyed variant and suspected cases never count, because none of
them is recorded as affected.

**A pedigree's declarations live in its set.** A bare `Pedigree` has nowhere to declare key entries or loci, so one with
marks or genotypes is validated and drawn with its set's declarations passed alongside, and those declarations are
checked as they would be in the set; a file holding one is a one-pedigree `PedigreeSet`.

**Checks** (loader, loud):

- entry ids are unique and distinct from citation and locus ids; entry texts are unique across the set, so one entry
  cannot be declared twice; only a phenotype entry may have empty text (the unnamed phenotype);
- a phenotype's grades are unique and non-empty; a genotype pattern names a declared locus and its alleles, lists each
  allele once when it states no copies, and no two genotype entries state the same pattern;
- every mark's entry resolves and is a phenotype or other entry, never a genotype one; a person marks an entry at most
  once; a phenotype mark has a standard status or one of its entry's grades, and an other mark has neither, nor an onset
  age;
- every entry is shown by someone — marked, or for a genotype entry matched — since an entry no one shows claims a mark
  the figure does not draw.

**Consumers.**

- The renderer draws every entry a person shows in the same legend-keyed fills it draws a phenotype in: an other or
  genotype entry is a flat fill labelled with its text. How to draw a key-defined grade is not settled; until it is, a
  pedigree with one is deferred, as other undrawn features are ([`renderer.md`](renderer.md)).
- `diff_set` and `match_individuals` compare a person's marks by what their entries state — the kind, the text and the
  inheritance — so ids assigned differently in two records still match, and compare each phenotype mark's status or
  grade. Onset age is recorded but not scored. Genotype entries add nothing to compare per person: whether a person
  shows one follows from their genotype, which is compared already, and whether a reading keyed it as a fill is how the
  figure draws it, not what it means.
- The stored-layout digest ([`layout-store.md`](layout-store.md)) reads what a person shows through the entries, so an
  entry's id never enters it, and a pedigree recorded only with standard phenotype statuses digests as it did before the
  key existed: its stored layouts stay valid.

**Emission.** A model declares the key once, usually from the figure's legend, then writes short ids on each symbol,
which is easier to get right than repeating a long name. The importers ([`convert.md`](convert.md)) state phenotypes
only: no data format grus imports has a key, so each declares one phenotype entry per distinct stated name, ids `k1`,
`k2`, … by first appearance.

**Migration.** The key replaces 0.4's condition declarations, a breaking change to the IR's shape, made below 1.0 as a
minor release. Every 0.4 condition was a phenotype by construction, so the migration (`grus migrate`) is mechanical:
each condition declaration becomes a phenotype entry with its name as the text, each person's condition entry a mark,
and each support citing a condition cites the entry. Which old conditions were variants or controls cannot be recovered
from the record; that needs the figure read again. The migration also reads records from before conditions were declared
(when each entry carried its own name and inheritance), declaring one entry per distinct name; entries of one name
stating different modes, or one person with two entries of one name, have no current form and fail loudly.

### Concept coverage — the full Bennett vocabulary, in the schema

The schema represents the **entire** Bennett/NSGC concept space (2008 Figs 1–4 + the 2022 revision), so no figure is
unrepresentable — **independent of** what the extraction prompt currently emits or the renderer currently draws. Those
two implement the vocabulary *progressively*, driven by measured corpus incidence (`corpus.md`); the IR does not wait
for them. Covered: gender + sex-assigned-at-birth (incl. nonbinary, VSC via `marks` shading); affection, carrier,
presymptomatic, multi-condition partition; deceased, proband, consultand; reproductive outcomes (pregnancy, stillbirth,
SAB, TOP, ectopic) and count-collapsed sibships; consanguinity, separated/divorced, single parent, childlessness/
infertility; twins (MZ/DZ/unknown); adoption (in/out, dashed vs solid descent); ART donor/surrogate; family/panel/gene
labels. Evolution is additive by default: CI runs `buf breaking` (FILE) against the baseline, through
`tools/schema/compat.py`, which passes a break only when the same change raises the major version in `pyproject.toml`
above the baseline's, or below 1.0 the minor — the breaking release, with every consumer migrated in it. A removed
field's number and name are reserved, never reused. Downstream bounds its pin below the next breaking release
(`grus>=0.4,<0.5` before 1.0, `grus>=1.2,<2` after). The pre-release rewrites (slice 22) are history.

### Serialization

**Text, not binary.** grus has no read-modify-write, so we use the readable text projections: **pbtxt** is the canonical
golden/human surface (comment-able, diffable), **proto3-JSON** is the LLM emission surface (structured-output → parse →
validate). The lost-unknowns caveat of text projections does not bite because nothing does a lossy RMW of an IR.

**Embedding the IR in another schema.** A downstream schema can hold grus messages, a `map<int32, PedigreeSet>` for
instance, by importing `grus/models/pedigree.proto`. The wheel ships each `.proto` beside its stub, at the path the stub
registers, so the import resolves from the installed package and the generated code reuses grus's stubs. The protos
import `buf/validate/validate.proto`, which the `protovalidate` wheel does not ship; the downstream schema resolves it
from its own protovalidate dependency.

### Validation, split by what protobuf can express

- **Field-local / single-message** rules are **protovalidate** options on the `.proto`: `Position` components ≥ 1;
  `gender` and a mark's status are real values not the zero sentinel; a key entry is exactly one kind; a key entry's id,
  a mark's `key_id` and a set `Support.key_id` are non-empty; a genotype whose copies are not stated is unphased and
  lists each allele once; `twin_type` set iff `twin_group` present; `Individual.count` ≥ 1 when set (1 is one person,
  the same as absent) and never together with `count_unspecified` (a known and an unknown number at once).
- **Graph invariants** live in the `grus.ir` loader, which fails loud: every referenced `Position` exists among the
  pedigree's individuals; `(generation, index)` is unique per pedigree; a mating's two partners are distinct; offspring
  exist; a child's generation exceeds each parent's, and one mating's offspring share a generation. Generation is the
  drawn row, so a child is usually one below its parents but may be further down — drawn beside half-siblings whose
  other parent is a generation lower, or across omitted generations — and generation increasing along every descent edge
  also rules out ancestry cycles. Several probands are valid (a family ascertained through more than one member;
  imported cohort data has them). Citations, when present, are checked the same way (`validate_set`): each is
  well-formed (a non-empty id, exactly one anchor, a region with page ≥ 0 and x1 > x0, y1 > y0 — protovalidate), ids are
  unique across the set, every evidence and support id resolves, a support's `field` is on the closed path list
  (`grus.ir.SUPPORT_FIELDS`, exported with the path prefixes so a prompt can list the valid paths), it names a declared
  `key_id` iff the field is `mark.*` or `key.*` and someone in scope marks (`mark.*`) or shows (`key.*`) that entry, a
  declaration path scopes the whole pedigree, every person and couple in scope is in the pedigree, an offspring field is
  scoped by the child, and a mating field by couples. Key entries and marks are checked as listed under "The key", and
  loci and genotypes as listed under "Genotypes". Each failure names the pedigree and the item. Enum `*_UNSPECIFIED`
  zeros are sentinels, never domain values; rare axes are `optional` (absent = the natural default — LIVE / BIOLOGICAL /
  CURRENT / …), so the sentinel is never emitted.

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

- **An entry-centred table** (each key entry listing the positions that show it, with their status), like matings
  listing partners. Rejected: a model transcribes a figure symbol by symbol, and this splits one person's fills across
  entry rows referenced back by position, where dangling and duplicated references creep in; and matching, diffs and
  drawing are all per person and would rebuild the per-person view. The entry-centred view (a case-record `Assertion`
  with `applies_to`) is a mechanical projection of the per-person form, so an export can produce it without storing it.

- **Name as the join key, with a check that marks agree on inheritance.** Smaller, but it keeps a free-text join (typos
  split an entry; renaming means editing every mark) and gives supports and the legend no entity to point at.

- **A kind added to 0.4's condition declarations** (an additive change: `ConditionDef` gains a genotype or other kind,
  and a condition entry's status becomes optional). No break, but the messages keep a name that no longer fits — "the
  condition Control" — and an entry's status is then required for some kinds and forbidden for others, a rule the shape
  no longer shows. Below 1.0 a break costs a minor release; a misnamed core message would cost every reader.

- **Genotype entries marked as well as derived**: a person lists a genotype entry among their marks and also has the
  genotype, and the loader checks the two agree (as an annotation's loci are checked against the genotypes). It can
  record a carrier the figure leaves unfilled, but stores one fact twice and asks the reader to write it twice.

- **A genotype entry as a locus and an allele**, shown by anyone who carries the allele. It cannot express a key that
  grades zygosity, which recessive figures often do ("■ homozygous, ▨ heterozygous"); a pattern can.

- **Key-defined grades as free text on each mark.** Simpler, but a spelling difference splits one grade in two, nothing
  checks a grade against the key, and it overlaps with other entries: the same "suspected, not examined" key line could
  be either, and two readers would split on which.

- **Grades as other entries.** One kind fewer, but it loses which phenotype the grade qualifies, so "suspected hearing
  loss" no longer says anything about hearing loss to a consumer reading the hearing-loss phenotype.

- **Citations as evidence lists on every entity** (case records' shape: `repeated string evidence` on each message).
  Rejected for grus: one sentence usually covers several people, so the same quote repeats across entities, and an id on
  an individual does not say which of its fields it grounds. A `Support` names the field and lists its scope once.

- **A support with several fields.** Rejected: fields times people over-claims ("II-1 is the proband; II-2 and II-3 are
  affected" would make all three probands). One field per support, sharing citation ids.

- **Figure-level citations only.** Simpler, but it can never say which recorded facts came from outside the drawing,
  which is what curation of caption-stated facts needs.

- **Genotypes as annotation text only**, with a convention for the shorthand. Rejected: `M1/M2` means nothing without
  the legend, and parsing free text to recover a variant is what a structured IR exists to avoid.

- **Genotypes replacing the drawn annotation**, the renderer drawing `M1/+` from the genotype. One encoding of the fact,
  but the drawn text is then a renderer's formatting choice (separator, order, a locus prefix when there are several), a
  figure's exact label is lost, and an imported cohort with many markers would draw a stack of lines no figure has.
  Keeping both, linked by the annotation's loci, records what is printed and what it means.

- **Per-allele copy counts** (`{M: 2}`) instead of a list of copies. It states zygosity as directly, but a count has no
  slots, so it cannot say which variants share a haplotype.

- **A `phased` flag** on each genotype, meaning its order is maternal then paternal. It phases one locus against the
  parents but cannot say that variants at two loci are in trans, which is the case that matters for compound
  heterozygosity; the phase set covers both.

## Open questions

- **Parent of origin.** A phase set says which variants share a haplotype, not which parent it came from. A figure that
  states "maternally inherited" has nowhere to put it beyond a support's citation; the additive fix is an optional
  origin per haplotype slot.

- **One locus per variant or per gene.** Both are valid: `M1/M2` reads naturally as a gene-level locus with two alleles,
  while VCF and Phenopackets keep one variant per locus (the Phenopackets importer does). An exporter will need one
  convention, and a mapping from the other.

- **ART multi-party parenthood.** `reproductive_role` + `parentage` records donor/surrogate roles but does not fully pin
  the 3-party genetic≠gestational≠rearing cases of Bennett 2022 Fig 5. KIN models these as *edges* (sperm/ovum donor,
  gestational carrier → child); the additive fix is a `PARENTAGE_GESTATIONAL` member on the `Offspring` edge, which
  would make `reproductive_role` derivable. Near-zero incidence in rare-disease pedigrees; revisit if the corpus shows
  ART.
