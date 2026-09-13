# Design: importers (external pedigree formats → IR)

**Status:** current **Related:** [`ir.md`](ir.md) (the target), [`renderer.md`](renderer.md) (what an import is drawn
by), research: [`../research/pedigree-file-formats.md`](../research/pedigree-file-formats.md) (field lists, codes,
sources), [`../research/phenopackets-vs-bespoke-ir.md`](../research/phenopackets-vs-bespoke-ir.md) (why the IR is not
Phenopackets). Code: `grus/convert/`.

## Overview

`grus.convert` turns an external pedigree file into a validated `PedigreeSet`, so the renderer (and the IR diff) serve
data that never passed through a figure: genotyping cohorts (PLINK/LINKAGE PED), R analyses (kinship2/Pedixplorer
tables), clinical interchange (Phenopackets v2 `Family`), and editor exports (Open Pedigree simple JSON). Importers are
the priority; exporters are not designed here.

## Non-goals

- **No inference.** The IR records what the source *states*. An importer never derives consanguinity from the graph, a
  proband from position in the file, a missing parent from the other one, or inheritance mode from a pattern (`ir.md`
  no-inference non-goal). Where a reference tool does infer (kinship2 `fixParents`, Open Pedigree virtual parents and
  default proband, `consanguinity: auto`), the importer deliberately does not.
- **No lossless promise.** Each source carries less than the IR (most) or different things (Phenopackets); the mapping
  table per format states what lands where and what is dropped.
- **No exporters in this doc.** PED / Phenopackets `Family` export is a separate slice (the research note carries an
  exporter sketch).

## Design

### Identity: `Position` is synthesised

The IR's identity and reference key is `Position{generation, index}` — an *as-drawn* label when the IR comes from a
figure. Every external format identifies people by an opaque string id and carries no generation numbers, so an importer
**synthesises** the `Position`:

- **generation** = aligned `kindepth`: longest founder→node path, then every two-partner mating is levelled by pulling
  the shallower partner's whole lineage down (repeat to a fixpoint; depths only increase, so it terminates). This is the
  row kinship2 itself would plot. Generations are 1-based.
- **index** = 1-based position within the generation in **source order** (first appearance in the file), so an import is
  deterministic and stable under re-import.
- The source id is kept verbatim in `Individual.external_id`; it is never parsed and never the reference key.
- `Provenance.source_format` names the importer, marking every `Position` in the set as assigned, not drawn.

The renderer derives its own topological row and treats `generation` as a label, so a synthesised label costs nothing
there; `ir.md` carries the matching note.

### The shared graph core (`_core.py`)

Every format reduces to one record per individual — `Person(id, father, mother, gender, …facts)` — plus `Extras` for
facts about couples (explicit spouse pairs, consanguinity, separated/divorced, childlessness, mating annotations). The
core then:

1. **Groups into pedigrees** by the source family id (PED `FID`, kinship2 `famid`, one Phenopackets `Family`, one Open
   Pedigree document); each group is an independent `Pedigree` (`ir.md` "Figure scope"). The family id becomes
   `Pedigree.id` and a FAMILY label.
1. **Builds matings from parent pairs**: one `Mating` per distinct `(father, mother)` pair, offspring in source order.
   One parent present → a single-parent `Mating` (the lone parent in `partner_a`, no phantom). Both absent → founder.
   Explicit childless couples from `Extras` become offspring-less matings (the man in `partner_a` where genders say).
1. **Ranks and indexes** (above), emits `Individual`s and `Mating`s, and runs `grus.ir.validate_set`; an importer never
   returns an invalid IR. Malformed input (unknown parent id, duplicate id, cycle, ragged row) raises
   `PedigreeImportError` naming the offender.

Format modules only parse and map; `grus.convert.IMPORTERS` registers them by name and `import_file` dispatches by
suffix (`.ped .fam .psam .pre` → ped), by sniffing (`.json` → a `Family` object vs a simple-JSON array), or by explicit
`--from` (`.csv` is ambiguous and needs `kinship2`).

### Shared lossiness rules

- **Sex codes → `Gender`** by the assume-aligned rule (male → MAN, female → WOMAN, unknown/other → UNKNOWN);
  `sex_assigned_at_birth` is never set (no source states it). Phenopackets `OTHER_SEX` and Open Pedigree `other` are
  *sex*, not gender: UNKNOWN + an OTHER annotation carrying the source word.
- **A single affected column → one `Condition{name: "", status}`**; a missing value omits the `Condition` (status not
  indicated). `CONDITION_STATUS_UNKNOWN` is reserved for a drawn "?", which no data format states.
- **Missing parent codes** (`0`, `NA`, `.`, `""`) are missing; the other parent is kept as a single parent.
- **Dates and ages** are verbatim `Annotation`s (`b. …` AGE, `d. …` AGE_AT_DEATH); nothing is normalised.
- **Unmappable fields** are dropped and named below; a data column is not stuffed into `annotations` unless the table
  says so.

### Per-format mapping

**PED** (`_ped.py`; PLINK `.fam`/`.ped`/`.psam`, LINKAGE pre-makeped `.pre`; fixtures `basic.pre`, `two_families.fam`,
`trio.psam`)

| Source                                     | IR                                                                         |
| ------------------------------------------ | -------------------------------------------------------------------------- |
| `FID`                                      | `Pedigree.id` + FAMILY label; one pedigree per family                      |
| `IID`                                      | `external_id`                                                              |
| `PAT` / `MAT` (`0` = not in dataset)       | mating pair; one `0` → single-parent mating (PLINK does not repair)        |
| `SEX` `1`/`M`, `2`/`F`, else               | MAN, WOMAN, UNKNOWN                                                        |
| `PHENO` `2`/`1`, `0`/`-9`/`NA`/`A`/`U`/`X` | AFFECTED/UNAFFECTED, omitted, LINKAGE letters; `--1` scheme via `pheno_01` |
| quantitative `PHENO`                       | MEASUREMENT annotation, verbatim                                           |
| `.psam` `#FID/#IID …` header               | column mapping; first non-standard column is the phenotype                 |
| genotype columns, `SID`                    | dropped                                                                    |

Not expressible: deceased, proband, twins, consanguinity, adoption, outcomes, counts, relationship status.

**kinship2 / Pedixplorer** (`_kinship2.py`; a header CSV/TSV as fed to `pedigree()`; relation matrix embedded after a
blank line or in `<stem>.rel.csv`; fixture `kinship2.csv`)

| Source                                                                                                                           | IR                                                                                                                                                                                                                                                              |
| -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `famid`, `id`, `dadid`/`momid` (aliases per Pedixplorer)                                                                         | as PED                                                                                                                                                                                                                                                          |
| `sex` `1..4` / words (shifted if a `0` appears)                                                                                  | MAN, WOMAN, UNKNOWN; `terminated` → UNKNOWN + `reproductive_outcome=TERMINATION`                                                                                                                                                                                |
| `affected` / `affected.<trait>` columns                                                                                          | one `Condition` per column (`name` = suffix; `NA` omits; `0` = UNAFFECTED)                                                                                                                                                                                      |
| `status` 1                                                                                                                       | `deceased`                                                                                                                                                                                                                                                      |
| `relation` code 1/2/3 (MZ/DZ/UZ), pairwise                                                                                       | one `twin_group` per connected set + `twin_type` (disagreement → UNKNOWN)                                                                                                                                                                                       |
| `relation` code 4 (spouse)                                                                                                       | offspring-less `Mating`                                                                                                                                                                                                                                         |
| Pedixplorer `proband`, `consultand`, `carrier`, `asymptomatic`, `evaluated`, `adopted`, `miscarriage`, `dob`, `dod`, `fertility` | `proband`, `consultand`, CARRIER / PRESYMPTOMATIC conditions, `documented_evaluation`, `adoption=IN` + `parentage=ADOPTIVE`, `reproductive_outcome=MISCARRIAGE`, AGE / AGE_AT_DEATH annotations, `childlessness=INFERTILITY` on that person's childless matings |

Deviation: kinship2 rejects a single-parent row and `fixParents` invents the other parent; the importer keeps a
single-parent mating. Not expressible: consanguinity, relationship status, adoption direction.

**Phenopackets v2 `Family`** (`_phenopackets.py`; one `Family` JSON or an array; camelCase or snake_case keys, enum
names or ints; fixture `family.phenopackets.json`)

| Source                                                                                     | IR                                                                                                |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `Family.id` / `Person.familyId`                                                            | `Pedigree.id`; one pedigree per `Family`                                                          |
| `pedigree.persons[]` (`individualId`, `paternalId`, `maternalId`, `sex`, `affectedStatus`) | the graph, as PED (`"0"`/`""` missing; MISSING status omits the condition)                        |
| `proband.subject.id`                                                                       | `proband`                                                                                         |
| `consanguinousParents`                                                                     | `consanguineous` on the proband's parents' mating (fail loud if the proband lacks two parents)    |
| packet `diseases[]` (`term.label`, `excluded`, `onset`)                                    | replace the bare status: `Condition{name, AFFECTED/UNAFFECTED, onset_age}` (TimeElement verbatim) |
| `subject.vitalStatus` DECEASED, `timeOfDeath`                                              | `deceased`; AGE_AT_DEATH annotation                                                               |
| `subject.karyotypicSex`; `subject.gender` label naming non-binary                          | KARYOTYPE annotation; `GENDER_NONBINARY`                                                          |
| phenotypic features, measurements, interpretations, files, `metaData`                      | dropped                                                                                           |

A packet whose subject is not in `persons` fails loud. Not expressible: twins, adoption, outcomes, counts, consultand,
carrier, consanguinity on any other couple.

**Open Pedigree / PhenoTips simple JSON** (`_openpedigree.py`; a JSON array of person and relationship objects, keys
case-insensitive; fixture `simple.openpedigree.json`)

| Source                                                                                                   | IR                                                                                                                                      |
| -------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `externalId` › `name` › `id`                                                                             | `external_id`; references resolve id › externalId › name › firstName                                                                    |
| `sex` male/m, female/f, other/o, unknown/u                                                               | MAN, WOMAN, UNKNOWN (+ OTHER annotation), UNKNOWN                                                                                       |
| `mother` / `father`                                                                                      | mating pair; one given → single-parent mating (no virtual parent)                                                                       |
| `proband`, `evaluated`                                                                                   | `proband`, `documented_evaluation`; no default proband                                                                                  |
| `lifeStatus` deceased / stillborn / miscarriage / aborted / unborn                                       | `deceased` / STILLBIRTH / MISCARRIAGE / TERMINATION / PREGNANCY                                                                         |
| `disorders[]` × `carrierStatus` (`affected` default, `carrier`, `presymptomatic`, `''`)                  | one `Condition` per disorder with that status (a bare carrier/presymptomatic → one unnamed condition)                                   |
| `twinGroup`, `monozygotic`                                                                               | `twin_group`; MZ if monozygotic else UNKNOWN (the format has no DZ)                                                                     |
| `adoptedIn` / `isAdopted` / `adoptedStatus`                                                              | `adoption` IN (+ `parentage=ADOPTIVE`) / OUT                                                                                            |
| `numPersons`, `gestationAge`                                                                             | `count`; GESTATIONAL_AGE annotation                                                                                                     |
| `birthDate`, `deathDate`, `deceasedAge`, `comments`, `hpoTerms`, `candidateGenes`                        | AGE, AGE_AT_DEATH, AGE_AT_DEATH, OTHER, PHENOTYPE, OTHER (`gene: …`) annotations                                                        |
| relationship `partner1`/`partner2`, `separated`, `consanguinity` Y, `childlessStatus`, `childlessReason` | `Mating` (offspring-less if none), `status=SEPARATED`, `consanguineous`, `childlessness` BY_CHOICE/INFERTILITY, mating OTHER annotation |
| open-pedigree internal graph JSON (`rel`/`chhub`/`virt` nodes)                                           | not accepted (layout artefact)                                                                                                          |

Not expressible: consultand, `sex_assigned_at_birth`, adoption by relative, donor/surrogate, ectopic, divorced.

### Not built (and why)

Madeline-2 tables, CanRisk v3/v4 and GEDCOM 5.5.1 are documented in the research note with mapping tables. Each is a
parse-and-map module over the same core (Madeline in particular is close to a superset of kinship2's columns). They wait
for a user: CanRisk and GEDCOM are lossy in both directions and the Madeline site is down, so its column set is taken
from source code only.

## Alternatives considered

- **Reuse the renderer's ranking** (`grus.render._layout._kindepth` + `_align_couples`). Rejected: the renderer's
  alignment reads the IR's drawn `generation` to decide which cross-lineage joins to level, which does not exist before
  import; and `convert` must not depend on `render`.
- **Make `external_id` the identity for imported IRs.** Rejected: two identity schemes in one schema would split every
  consumer (diff, render, validate). The IR's key stays `Position`; the synthesis rule is the cost.
- **Infer consanguinity on import from shared ancestry.** Rejected by the no-inference non-goal; a figure draws a double
  line only when the author asserted it, and an imported IR should carry the same claim strength.
- **Import via the Phenopackets Python package.** Rejected: the JSON is structurally simple, the package would be the
  only heavy dependency in the public wheel, and its `Sex` integer footgun (PR #442) is avoided by reading names.

## Open questions

- **Exporters.** PED and Phenopackets `Family` export (the research note's sketch) would let a figure-derived IR feed
  cohort tooling; the `Position → id` rule ("I-3" unless `external_id`) and the two-same-gender-parents failure mode
  need deciding.
- **kinship2 relation matrix carriage.** Embedded-after-blank-line and `.rel.csv` sidecar are conventions this importer
  introduces (kinship2 has no file form for it). Revisit if a common CSV convention emerges.
