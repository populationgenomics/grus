# Pedigree data-file formats — importer reference

**Status:** research notes (2026-09-13). `docs/research/` holds source-cited research notes that inform design docs;
they are not design decisions. Target: importers into the IR in
[`../../schema/proto/grus/models/pedigree.proto`](../../schema/proto/grus/models/pedigree.proto) (see
[`../design/ir.md`](../design/ir.md)). Every claim cites a primary source (official docs or reference-implementation
code). Local mirrors of the fetched sources were read at `/tmp/pedfmt/` (not committed).

## Shared IR-mapping rules (apply to every format)

- **No generation numbers anywhere.** No format below carries a generation label; `Position.generation` and `.index`
  must be *synthesised* (topological depth from founders + a deterministic within-generation order). The source id goes
  to `Individual.external_id`.
- **Parent columns → `Mating`.** Group children by unordered parent pair; one `Mating` per pair with `offspring` in file
  order. Both parents absent = founder (no mating). Exactly one parent present = single-parent `Mating` with only
  `partner_a` (IR: "a single drawn parent is one partner — no phantom stand-in"). Never invent the other parent, even
  where the source tool would (kinship2 `fixParents`, Open Pedigree virtual parents).
- **Per-individual affected flag → one `Condition`** with `name = ""` (sole condition) unless the format names traits.
- **Consanguinity**: IR says explicit, never inferred (`ir.md` non-goals). Set `Mating.consanguineous` only when the
  source carries an explicit flag; graph-derivable consanguinity stays `false`.
- **Sex code → `Gender`** by the 2022 assume-aligned rule (`ir.md`): male → `GENDER_MAN`, female → `GENDER_WOMAN`,
  unknown/0 → `GENDER_UNKNOWN`; `sex_assigned_at_birth` unset.
- Enum zero (`*_UNSPECIFIED`) is never emitted; unknown affection → `CONDITION_STATUS_UNKNOWN` or omit the `Condition`
  (empty list = "status not indicated", `pedigree.proto` L107). Pick one policy per importer and document it.

______________________________________________________________________

## 1. PLINK `.fam` / `.ped` (and LINKAGE pre-makeped)

Sources: PLINK 1.9 formats [fam](https://www.cog-genomics.org/plink/1.9/formats#fam),
[ped](https://www.cog-genomics.org/plink/1.9/formats#ped); PLINK 1.9
[input](https://www.cog-genomics.org/plink/1.9/input) (irregular files, phenotype encoding); PLINK 1.9
[filter › --make-founders](https://www.cog-genomics.org/plink/1.9/filter#make_founders); PLINK 2 formats
[.fam](https://www.cog-genomics.org/plink/2.0/formats#fam),
[.psam](https://www.cog-genomics.org/plink/2.0/formats#psam); MERLIN
[input files](https://csg.sph.umich.edu/abecasis/Merlin/tour/input_files.html) (LINKAGE/QTDT pedigree file).

### `.fam` (PLINK 1.9 and 2)

"A text file with no header line, and one line per sample with the following six fields":

| #   | Field                      | Encoding (verbatim)                                                              |
| --- | -------------------------- | -------------------------------------------------------------------------------- |
| 1   | Family ID (`FID`)          | string                                                                           |
| 2   | Within-family ID (`IID`)   | string; "cannot be '0'"                                                          |
| 3   | Within-family ID of father | "'0' if father isn't in dataset"                                                 |
| 4   | Within-family ID of mother | "'0' if mother isn't in dataset"                                                 |
| 5   | Sex code                   | "'1' = male, '2' = female, '0' = unknown"                                        |
| 6   | Phenotype value            | "'1' = control, '2' = case, '-9'/'0'/non-numeric = missing data if case/control" |

"If there are any numeric phenotype values other than {-9, 0, 1, 2}, the phenotype is interpreted as a quantitative
trait instead of case/control status. In this case, -9 normally still designates a missing phenotype."
([1.9 formats#fam](https://www.cog-genomics.org/plink/1.9/formats#fam))

Phenotype flags ([1.9 input](https://www.cog-genomics.org/plink/1.9/input)): "Missing phenotypes are normally expected
to be encoded as -9. You can change this to another integer with --missing-phenotype. … nonnumeric values such as 'NA'
are rejected since they're treated as missing phenotypes no matter what." "Case/control phenotypes are expected to be
encoded as 1=unaffected (control), 2=affected (case); 0 is accepted as an alternate missing value encoding. If you use
the --1 flag, 0 is interpreted as unaffected status instead, while 1 maps to affected."

Delimiter: whitespace (PLINK describes its sample files as "space- or tab-delimited",
[1.9 input › --pheno](https://www.cog-genomics.org/plink/1.9/input)). Column-dropping variants: "--no-fid --no-parents
--no-sex --no-pheno … allow you to use .fam or .ped files which lack family ID, parental ID, sex, and/or phenotype
columns" ([1.9 input](https://www.cog-genomics.org/plink/1.9/input)).

Family ID semantics: IIDs are unique only *within* a FID (field name is "Within-family ID"); the `(FID, IID)` pair is
the sample key (e.g. `--update-ids` expects "Old family ID, Old within-family ID, New family ID, New within-family ID",
[1.9 data](https://www.cog-genomics.org/plink/1.9/data#update_indiv)). Parent columns are within-family IDs, so parents
are always in the same family.

**One-parent-missing rule** ([1.9 filter](https://www.cog-genomics.org/plink/1.9/filter#make_founders), identical text
in [2.0 filter](https://www.cog-genomics.org/plink/2.0/filter#make_founders)): "By default, if parental IDs are provided
for a sample, they are not treated as a founder even if neither parent is in the dataset. With no modifiers,
--make-founders clears both parental IDs whenever at least one parent is not in the dataset, and the affected samples
are now considered founders. The 'require-2-missing' modifier causes this to only happen when both parents are missing."
So: a row with one parent `0` and the other set is a nonfounder with one known parent; PLINK does **not** synthesise the
other parent (it only optionally *drops* the known one).

### `.ped` (PLINK; = LINKAGE pre-makeped layout + genotypes)

"Contains no header line, and one line per sample with 2V+6 fields where V is the number of variants. The first six
fields are the same as those in a .fam file." ([1.9 formats#ped](https://www.cog-genomics.org/plink/1.9/formats#ped)).
Fields 7.. are allele pairs; missing allele "'0' in .ped and similar files"
([1.9 input](https://www.cog-genomics.org/plink/1.9/input)).

LINKAGE pre-makeped (MERLIN/QTDT convention,
[MERLIN input files](https://csg.sph.umich.edu/abecasis/Merlin/tour/input_files.html)): "family identifier, an
individual identifier, a link to each parent (if available) and finally an indicator of each individual's sex", then
phenotypes/markers; sex "2 (female) and 1 (male)"; missing parent `0`; affection "U or 1 for unaffecteds, A or 2 for
affecteds, and X or 0 for missing phenotypes". Official example (`basic.ped`):

```
1   1   0  0  1
1   2   0  0  2
1   3   0  0  1
1   4   1  2  2
1   5   3  4  2
1   6   3  4  1
```

Post-makeped LINKAGE adds pointer columns (first offspring, next paternal/maternal sib) and a proband column; column
positions become sex=8, proband=9, phenotype=10 (as parsed by Open Pedigree's importer, see §4, which autodetects
`Ped:`/`Per:` in line 1).

### `.psam` (PLINK 2)

"A text file which usually has at least one header line, where only the last header line starts with '#FID' or '#IID'.
This final header line specifies the columns"; recognised columns: `IID` (required), `SID` (source ID), `PAT`
("individual ID of father, '0' if unknown"), `MAT`, `SEX` ("'1' = male, '2' = female, 'NA'/'0' = unknown"). "(FID must
either be the first column, or absent. If it's absent, all FID values are now assumed to be '0'.) Any other value is
treated as a phenotype/covariate name". "If no header line is present, the columns are assumed to be in .fam file order
(FID, IID, PAT, MAT, SEX, PHENO1)." ([2.0 formats#psam](https://www.cog-genomics.org/plink/2.0/formats#psam)). Sex also
accepted as `M`/`F` in `--update-sex` input ("1 or M = male, 2 or F = female, 0 = missing",
[1.9 data](https://www.cog-genomics.org/plink/1.9/data#update_indiv)).

### Mapping to grus IR

| PED/FAM field                 | IR                                                                   |
| ----------------------------- | -------------------------------------------------------------------- |
| FID                           | `Pedigree.id` / `Label{kind=FAMILY}`; one `Pedigree` per FID         |
| IID                           | `Individual.external_id`; `Position` synthesised                     |
| father / mother (`PAT`/`MAT`) | `Mating{partner_a, partner_b}` grouped by pair; `0` → absent partner |
| sex 1/2/0                     | `GENDER_MAN` / `GENDER_WOMAN` / `GENDER_UNKNOWN`                     |
| pheno 2 / 1 / 0,-9,NA         | one `Condition{status=AFFECTED / UNAFFECTED / omit-or-UNKNOWN}`      |
| quantitative pheno            | `Annotation{type=MEASUREMENT}` (or drop)                             |
| `SID` (.psam)                 | ignore or append to `external_id`                                    |

Not expressible / must be synthesised: `Position` (derived); deceased, proband, consultand, carrier, twins, zygosity,
consanguinity, adoption, reproductive outcome, sibship count, relationship status — none exist; leave defaults
(`consanguineous=false`, no twin groups). One-parent rows → single-parent `Mating`. Genotype columns → drop (or
`Annotation{GENOTYPE}` only if a variant list is supplied).

______________________________________________________________________

## 2. kinship2 `pedigree()` (R) and Pedixplorer

Sources: CRAN [kinship2](https://cran.r-project.org/package=kinship2); reference implementation
[`R/pedigree.R`](https://github.com/mayoverse/kinship2/blob/master/R/pedigree.R) and
[`R/fixParents.R`](https://github.com/mayoverse/kinship2/blob/master/R/fixParents.R) (roxygen = the CRAN help pages);
Pedixplorer [`R/AllConstructor.R`](https://github.com/LouisLeNezet/Pedixplorer/blob/devel/R/AllConstructor.R),
[`R/norm_data.R`](https://github.com/LouisLeNezet/Pedixplorer/blob/devel/R/norm_data.R).

Not a file format: an in-memory constructor `pedigree(id, dadid, momid, sex, affected, status, relation, famid, missid)`
over parallel vectors, typically loaded from a CSV with those columns.

| Arg              | Encoding (from roxygen / code)                                                                                                                                                                                                                                                                                                                                                                  |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`             | required, no `NA`, no blank strings; must be unique (within `famid` if given — internally `famid/id`)                                                                                                                                                                                                                                                                                           |
| `dadid`, `momid` | "Founders' parents should be coded to NA, or another value specified by missid"                                                                                                                                                                                                                                                                                                                 |
| `missid`         | default `0` if `id` numeric, `""` otherwise                                                                                                                                                                                                                                                                                                                                                     |
| `sex`            | character `"male","female","unknown","terminated"` (case-insensitive, may be truncated) or numeric `1=male, 2=female, 3=unknown, 4=terminated`; if `min(sex)==0` the codes are shifted (`fixParents` warns "Setting 0=male, 1=female, 2=unknown, 3=terminated"); out-of-range → unknown; error if all unknown                                                                                   |
| `affected`       | vector or **multi-column matrix** ("status with respect to multiple traits"); logical/factor/integer → `0/1` = unaffected/affected after subtracting the min; `NA` = missing                                                                                                                                                                                                                    |
| `status`         | "Censor/Vital status (0="censored", 1="dead")"                                                                                                                                                                                                                                                                                                                                                  |
| `relation`       | matrix/data.frame with columns `id1, id2, code` (+ `famid` as 4th column when `famid` is used); codes `1`=MZ twin, `2`=DZ twin, `3`=UZ twin (unknown zygosity), `4`=spouse ("necessary in order to place a marriage with no children"); character codes `"MZ twin","DZ twin","UZ twin","spouse"` accepted; for ≥3-tuples "only necessary to specify the pairwise zygosity" of adjacent subjects |
| `famid`          | optional; result is a `pedigreeList`; parents must be in the same family ("Mother's family != subject's family" is an error)                                                                                                                                                                                                                                                                    |

Constraints enforced by the constructor (`pedigree.R`): "Subjects must have both a father and mother, or have neither";
fathers must be `male` and mothers `female` ("Id not male, but is a father"); referenced parents must exist; twins must
share both parents ("Twins found with different mothers"); MZ twins must share sex ("MZ Twins with different genders");
no self-spouse/self-twin.

`fixParents(id, dadid, momid, sex, missid)` (`fixParents.R`): "First look to add parents whose ids are given in
momid/dadid [but absent]. Second, fix sex of parents. Last look to add second parent for children for whom only one
parent id is given." Added parents get ids `addin-N` (character) or `max(id)+k` (numeric), sex 1/2, and missing parents.

Official example (`pedigree.R` roxygen):

```r
data(minnbreast)
bpeds <- with(minnbreast, pedigree(id, fatherid, motherid, sex, affected = proband, famid = famid))
rel8 <- data.frame(id1 = c(137, 138, 139), id2 = c(138, 139, 140), code = c(1, 2, 2))
bped.id8 <- with(minnbreast[minnbreast$famid == 8, ],
                 pedigree(id, fatherid, motherid, sex, affected = proband, relation = rel8))
```

**Pedixplorer** (Bioconductor fork, `AllConstructor.R`):
`Pedigree(obj, rel_df, cols_ren_ped, ..., normalize = TRUE, missid = c(NA_character_, "0"))` takes a data.frame with
columns
`id, dadid, momid, famid, sex, fertility, miscarriage, deceased, avail, evaluated, consultand, proband, affection, carrier, asymptomatic, adopted, dateofbirth, dateofdeath`
(minimum `id, dadid, momid, sex`); `cols_ren_ped` default aliases: `id="indId"`, `dadid="fatherId"`, `momid="motherId"`,
`famid="family"`, `sex="gender"`, `fertility=c("sterilisation","steril")`, `miscarriage=c("miscarriage","aborted")`,
`deceased=c("status","dead","vitalStatus")`, `avail="available"`, `evaluated="evaluation"`, `consultand="consultant"`,
`affection="affected"`, `asymptomatic="presymptomatic"`, `adopted="adoption"`, `dateofbirth=c("dob","birth")`,
`dateofdeath=c("dod","death")`. Boolean columns take `1`/`0` (`norm_data.R`); `famid` is merged into `id` with `_`.
`rel_df` = `id1, id2, code(, famid)` with the same 1–4 codes. Normalisation enforces "Either have both parents or none",
"All fathers are male", "All mothers are female", "No parents are infertile or aborted".

### Mapping to grus IR

| kinship2 / Pedixplorer                                                                                            | IR                                                                                                                                                                                                        |
| ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `famid`                                                                                                           | `Pedigree.id`; one `Pedigree` per family                                                                                                                                                                  |
| `id`                                                                                                              | `external_id`; `Position` synthesised                                                                                                                                                                     |
| `dadid`/`momid` (both or neither)                                                                                 | `Mating{partner_a,partner_b}`; founders = no mating                                                                                                                                                       |
| `sex` 1/2/3                                                                                                       | MAN / WOMAN / UNKNOWN; `4 terminated` → `GENDER_UNKNOWN` + `reproductive_outcome=TERMINATION`                                                                                                             |
| `affected` column *k*                                                                                             | `Condition{name=colname_k, status}` per column; `NA` → UNKNOWN                                                                                                                                            |
| `status` 1                                                                                                        | `deceased=true`                                                                                                                                                                                           |
| `relation` code 1/2/3                                                                                             | `Offspring.twin_group` (new id per connected twin set) + `twin_type` MZ/DZ/UNKNOWN                                                                                                                        |
| `relation` code 4                                                                                                 | childless `Mating` (no offspring)                                                                                                                                                                         |
| Pedixplorer `proband`, `consultand`, `carrier`, `asymptomatic`, `evaluated`, `adopted`, `deceased`, `miscarriage` | `proband`, `consultand`, `Condition{CARRIER}`, `Condition{PRESYMPTOMATIC}`, `documented_evaluation`, `Offspring.adoption=ADOPTION_IN` (direction unknown), `deceased`, `reproductive_outcome=MISCARRIAGE` |
| Pedixplorer `fertility`                                                                                           | `Mating.childlessness=INFERTILITY` on that individual's mating(s)                                                                                                                                         |
| Pedixplorer `dateofbirth`/`dateofdeath`                                                                           | `Annotation{type=AGE}` verbatim                                                                                                                                                                           |

Not expressible / synthesise: `Position`; kinship2 has no proband/consultand/carrier/adoption/consanguinity/relationship
status; a single-parent row is *rejected* by the constructor (importer may accept it as single-parent `Mating` but must
not run `fixParents`-style synthesis); `terminated` sex conflates gender-unknown with pregnancy loss.

______________________________________________________________________

## 3. GA4GH Phenopackets v2 — `Family` / `Pedigree`

Sources:
[`core/pedigree.proto`](https://raw.githubusercontent.com/phenopackets/phenopacket-schema/master/src/main/proto/phenopackets/schema/v2/core/pedigree.proto),
[`core/individual.proto`](https://raw.githubusercontent.com/phenopackets/phenopacket-schema/master/src/main/proto/phenopackets/schema/v2/core/individual.proto),
[`phenopackets.proto`](https://raw.githubusercontent.com/phenopackets/phenopacket-schema/master/src/main/proto/phenopackets/schema/v2/phenopackets.proto);
docs [pedigree](https://phenopacket-schema.readthedocs.io/en/latest/pedigree.html),
[family](https://phenopacket-schema.readthedocs.io/en/latest/family.html); proto3 JSON rules
[protobuf.dev](https://protobuf.dev/programming-guides/json/).

```protobuf
message Pedigree {
  repeated Person persons = 1;
  message Person {
    enum AffectedStatus { MISSING = 0; UNAFFECTED = 1; AFFECTED = 2; }
    string family_id = 1; string individual_id = 2; string paternal_id = 3; string maternal_id = 4;
    Sex sex = 5; AffectedStatus affected_status = 6;
  }
}
enum Sex { UNKNOWN_SEX = 0; FEMALE = 1; MALE = 2; OTHER_SEX = 3; }          // individual.proto
message VitalStatus { enum Status { UNKNOWN_STATUS = 0; ALIVE = 1; DECEASED = 2; }
  Status status = 1; TimeElement time_of_death = 2; OntologyClass cause_of_death = 3; uint32 survival_time_in_days = 4; }
message Family { string id = 1; Phenopacket proband = 2; repeated Phenopacket relatives = 3;
  bool consanguinous_parents = 7;   // "flag to indicate that the parents of the proband are consanguinous"
  core.Pedigree pedigree = 4; repeated core.File files = 5; core.MetaData meta_data = 6; }
```

Docs: all six `Person` fields are REQUIRED; "The phenopacket schema has implemented a PED-compatible data-model";
"Individuals whose parents are not represented in the PED file are known as founders; their parents are represented by a
zero"; "Pedigree.individual_id MUST map to the PhenoPacket.Individual.id"; "It is allowable for the Pedigree to have
individuals that do not have an associated Phenopacket". Per-individual clinical detail (`Individual.vital_status`,
`Individual.sex`, `karyotypic_sex`, `gender: OntologyClass`, `date_of_birth`, `time_at_last_encounter`) lives on the
`Phenopacket.subject` of `Family.proband` / `Family.relatives`, not on `Pedigree.Person`. Proband = `Family.proband` (a
whole Phenopacket), so it is identified by matching `proband.subject.id` to a `Person.individual_id`.

proto3 JSON ([protobuf.dev](https://protobuf.dev/programming-guides/json/)): field names are lowerCamelCase (`familyId`,
`individualId`, `paternalId`, `maternalId`, `affectedStatus`, `consanguinousParents`, `metaData`; parsers also accept
the original snake_case); enums are emitted by name ("Parsers accept both enum names and integer values"); fields at
their default value are omitted by serialisers when the field has no presence — so an absent `sex` means `UNKNOWN_SEX`,
absent `affectedStatus` means `MISSING`, absent `consanguinousParents` means `false`. Minimal `Family` JSON built from
these rules:

```json
{ "id": "FAM1",
  "proband": { "id": "pp-ch1", "subject": { "id": "ch1", "sex": "FEMALE", "vitalStatus": { "status": "ALIVE" } }, "metaData": { } },
  "relatives": [ { "id": "pp-m11", "subject": { "id": "m11", "sex": "MALE", "vitalStatus": { "status": "DECEASED" } }, "metaData": { } } ],
  "consanguinousParents": false,
  "pedigree": { "persons": [
    { "familyId": "FAM1", "individualId": "m11", "paternalId": "0", "maternalId": "0", "sex": "MALE",   "affectedStatus": "UNAFFECTED" },
    { "familyId": "FAM1", "individualId": "f11", "paternalId": "0", "maternalId": "0", "sex": "FEMALE", "affectedStatus": "UNAFFECTED" },
    { "familyId": "FAM1", "individualId": "ch1", "paternalId": "m11", "maternalId": "f11", "sex": "FEMALE", "affectedStatus": "AFFECTED" } ] },
  "metaData": { } }
```

### Mapping to grus IR

| Phenopackets                                                             | IR                                                                                                                               |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `Family.id` / `Person.family_id`                                         | `Pedigree.id`                                                                                                                    |
| `Person.individual_id`                                                   | `external_id`; `Position` synthesised                                                                                            |
| `paternal_id`/`maternal_id` (`"0"` = founder; treat `""` as missing too) | `Mating` pair; one present → single-parent `Mating`                                                                              |
| `Person.sex` MALE/FEMALE/UNKNOWN_SEX/OTHER_SEX                           | MAN / WOMAN / UNKNOWN / `GENDER_UNKNOWN` + `Annotation{OTHER,"OTHER_SEX"}` (OTHER_SEX is phenotypic *sex*, not nonbinary gender) |
| `affected_status` AFFECTED/UNAFFECTED/MISSING                            | `Condition{"" , AFFECTED/UNAFFECTED}` / omit                                                                                     |
| `Family.proband.subject.id`                                              | `proband=true` on the matching individual                                                                                        |
| `subject.vital_status.status == DECEASED`                                | `deceased=true`; `time_of_death` → `Annotation{AGE}`                                                                             |
| `Family.consanguinous_parents`                                           | `Mating.consanguineous=true` on the proband's parents' mating (explicit flag — allowed)                                          |
| `subject.karyotypic_sex`                                                 | `Annotation{KARYOTYPE}`; `subject.gender` (OntologyClass) → `Gender` if it names a Bennett gender, else `Annotation{OTHER}`      |
| `phenotypicFeatures` / `diseases` on relatives' Phenopackets             | out of scope for a pedigree importer; optionally `Condition{name}` per disease                                                   |

Not expressible / synthesise: `Position`; twins, zygosity, adoption, reproductive outcome, sibship count, consultand,
carrier, relationship status; consanguinity only for the proband's parents (all other matings stay `false`).

______________________________________________________________________

## 4. Open Pedigree / PhenoTips JSON, plus its PED, BOADICEA, GEDCOM importers

Sources (reference implementation, `master`): open-pedigree
[`src/script/model/import.js`](https://github.com/phenotips/open-pedigree/blob/master/src/script/model/import.js),
[`model/export.js`](https://github.com/phenotips/open-pedigree/blob/master/src/script/model/export.js),
[`model/baseGraph.js`](https://github.com/phenotips/open-pedigree/blob/master/src/script/model/baseGraph.js),
[`pedigree.js`](https://github.com/phenotips/open-pedigree/blob/master/src/script/pedigree.js) (node menus = value
sets), [`view/person.js`](https://github.com/phenotips/open-pedigree/blob/master/src/script/view/person.js),
[`view/partnership.js`](https://github.com/phenotips/open-pedigree/blob/master/src/script/view/partnership.js),
[`view/importSelector.js`](https://github.com/phenotips/open-pedigree/blob/master/src/script/view/importSelector.js);
upstream PhenoTips
[`components/pedigree/resources/src/main/resources/pedigree/model/import.js`](https://github.com/phenotips/phenotips/blob/master/components/pedigree/resources/src/main/resources/pedigree/model/import.js)
(`initFromSimpleJSON`).

**Which importers exist where.** open-pedigree's import dialog offers exactly: "PED or LINKAGE (pre- or post- makeped)",
"GEDCOM", "BOADICEA", "GA4GH FHIR(JSON)" (`importSelector.js` L55–58). The **"simple JSON" parser (`initFromSimpleJSON`)
is only in upstream PhenoTips** (`pt import.js` L1014); open-pedigree retains just the property-name mapping
(`import.js` L1001–1021) and an exporter mapping (`export.js` L324–347). open-pedigree save/load uses its **internal
graph JSON**: `baseGraph.serialize()` emits an array of vertices
`{id, [width], prop:{…}, rel:true+hub:true | chhub:true | virt:true, outedges:[{to, [weight]}]}` read back by
`initFromPhenotipsInternal` (accepts `properties`/`prop`, `relationship`/`rel`, `name` as an alias for `id`, `gender`
strings `male/m/female/f`). Person→relationship→childhub→child edges encode the family graph.

### Simple JSON (PhenoTips upstream `initFromSimpleJSON`, spec = the header comment L939–1012)

"an array of objects, each object representing one person or one relationship". Official example:

```json
[ { "name": "f11", "sex": "female", "lifeStatus": "deceased" },
  { "name": "m11", "sex": "male" },
  { "name": "f12", "sex": "female", "disorders": [603235, "142763", "custom disorder"] },
  { "name": "m12", "sex": "male" },
  { "name": "m21", "sex": "male", "mother": "f11", "father": "m11" },
  { "name": "f21", "sex": "female", "mother": "f12", "father": "m12" },
  { "name": "ch1", "sex": "female", "mother": "f21", "father": "m21", "disorders": [603235], "proband": true },
  { "name": "m22", "sex": "male" },
  { "relationshipId": 1, "partner1": "f21", "partner2": "m22"} ]
```

Person keys (matched case-insensitively — `property.toLowerCase()`; values verbatim from the spec comment):

| Key                                                                                                                                             | Values / default                                                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                                                                                                                            | string/number; link-only, not stored                                                                                                                                                      |
| `proband`                                                                                                                                       | boolean; default true for the first object unless another is flagged; only the first flagged counts                                                                                       |
| `name` / `firstName`, `lastName`, `lastNameAtBirth`, `comments`, `externalId`                                                                   | strings                                                                                                                                                                                   |
| `sex`                                                                                                                                           | `"male"/"m"`, `"female"/"f"`, `"other"/"o"`, `"unknown"/"u"` (default unknown); internal `M/F/O/U` (open-pedigree normalises non-M/F to `U`, `abstractPerson.js` L48)                     |
| `mother`, `father`                                                                                                                              | reference resolved against id → externalId → name → firstName; "If one of the parents is given and the other one is not a virtual new node is created" (gender M/F, `comments:"unknown"`) |
| `twinGroup`                                                                                                                                     | integer; "All children of the same parents with the same twin group are considered twins"                                                                                                 |
| `monozygotic`                                                                                                                                   | boolean "(only applicable for twins)"                                                                                                                                                     |
| `adoptedIn`                                                                                                                                     | boolean (open-pedigree: `isAdopted`); upstream also `adoptedStatus` ∈ `"adoptedIn"`,`"adoptedOut"` (L210, L1504)                                                                          |
| `evaluated`                                                                                                                                     | boolean — "Documented evaluation" (`pedigree.js` menu label)                                                                                                                              |
| `birthDate`, `deathDate`, `deceasedAge`, `deceasedCause`, `aliveandwell`                                                                        | strings / boolean                                                                                                                                                                         |
| `gestationAge`                                                                                                                                  | 0–50 weeks (`pedigree.js` L492)                                                                                                                                                           |
| `lifeStatus`                                                                                                                                    | `"alive","deceased","aborted","miscarriage","stillborn","unborn"` (default alive; death date forces deceased) — same set validated in `person.js` `_isValidLifeStatus`                    |
| `disorders`                                                                                                                                     | array of OMIM ints or free strings                                                                                                                                                        |
| `carrierStatus`                                                                                                                                 | `''`, `'carrier'`, `'affected'`, `'presymptomatic'` (default `'affected'` if a disorder is given)                                                                                         |
| `childlessStatus` / `childlessReason`                                                                                                           | `none`, `'childless'`, `'infertile'` / string                                                                                                                                             |
| `numPersons`                                                                                                                                    | integer; "When present and not 0 this individual is treated as a 'person group'" (menu offers N,2–9)                                                                                      |
| `lostContact`, `nodeNumber`, `phenotipsId`, `features`, `nonstandard_features`, `genes`, `hpoTerms`, `candidateGenes`, `ethnicities`, `cancers` | see spec; `cancers` = `{<cancer>: {affected, ageAtDiagnosis, numericAgeAtDiagnosis, notes}}`                                                                                              |

Relationship objects: `relationshipId` (marker only), `partner1`, `partner2` (both required), `separated` boolean (→
internal `broken`), `consanguinity` ∈ `"Y"/"N"/"yes"/"no"` ("If not given it is computed automatically" — internal
`consangr` ∈ `A`(auto)/`Y`/`N`, `pedigree.js` L669–671), `childlessStatus`, `childlessReason`. A childless relationship
gets a placeholder child node internally. Proband = internal node id 0.

Export mapping (`export.js` L324–347): internal→JSON `fName→firstName`, `lName→lastName`, `isAdopted→adoptedIn`,
`dob→birthDate`, `dod→deathDate`, `externalID→externalId`, `gender→sex` with `M→"male"`, `F→"female"`, else `"unknown"`;
others same name
(`proband, comments, twinGroup, monozygotic, evaluated, gestationAge, lifeStatus, disorders, ethnicities, carrierStatus, numPersons, hpoTerms, candidateGenes, lostContact`).
Exporters: PED, SVG, PDF.

### Bundled PED/LINKAGE importer (`initFromPED`, `import.js` L186–367)

Whitespace split after stripping non `[a-zA-Z0-9_.\-\s*]`; ≥6 columns (≥10 post-makeped, detected by `Ped:` and `Per:`
in line 1; then sex=col 8, proband=col 9 (`==1` → node 0), phenotype=col 10). Multiple FIDs → error "multiple families
detected". Sex `1→M`, `2→F`, else `U`. Phenotype default scheme `2` affected, `1` unaffected, `0`/`-9` missing;
`affectedCodeOne` option: `1` affected, `0` unaffected, `-9` missing; other values optionally become named disorders
`"affected (phenotype N)"`. Affected → `carrierStatus:'affected'`, `disorders:['affected']`. One parent `0` → **virtual
parent node** created (`{'gender':'M'|'F','comments':'unknown'}`); father declared female / mother declared male →
error.

### Bundled BOADICEA importer (`initFromBOADICEA`, L407–582)

Line 1 must match `/^BOADICEA import pedigree file format 2/i`; 2 header lines skipped; ≥24 whitespace columns:
`FamID Name Target IndivID FathID MothID Sex Twin Dead Age Yob 1BrCa 2BrCa OvCa ProCa PanCa Gtest Mutn Ashkn ER PR HER2 CK14 CK56`
(comment L381–404). Mapped: `Target==1` → proband (node 0); `Sex` `F`→F else M; `Name` → `fName` unless integer;
`IndivID` → `externalID` (option); `Dead==1` → `lifeStatus:'deceased'`; `Yob≠0` → `dob = Yob-01-01`; cancer columns
11–15: `0` → comment "[-] …: unaffected", `AU` skipped, age → disorder code (`1BrCa`…) + comment "[+] …: at age N";
`Ashkn≠0` → `ethnicities:['Ashkenazi Jews']`. **`Twin`, `Age`, `Gtest`, `Mutn`, receptor columns are not parsed.**
Parents: same virtual-parent rule as PED.

### Bundled GEDCOM importer (`initFromGEDCOM`, L632–991)

Supported INDI tags: `NAME` (`given /surname/`), `SEX` (first char `f`/`m`), `BIRT.DATE→dob`,
`DEAT→lifeStatus 'deceased'` (+`DATE→dod`), `ADOP→isAdopted:true`, `NOTE`/`_COMMENT`/`_INFO`→comments, Cyrillic
`_GENSTAT` chars: `O` affected, `¬` "hearsay"→`presymptomatic`, `K` stillborn, `M` `childlessStatus:'infertile'`, `E`
comment "(untested)", `C` proband (TODO — not applied). FAM: `HUSB`, `WIFE`, `CHIL`; `FAMS` ignored; missing HUSB/WIFE →
virtual parent; FAM with no CHIL → virtual child (alert). Versions 5.5/5.5.1 expected (alert otherwise). Dates:
`ABT/EST/BEF/AFT` stripped, `BET a AND b` → a.

### Mapping to grus IR (simple JSON / internal properties)

| Open Pedigree                                                                          | IR                                                                                                                               |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `id`/`name`/`externalId`                                                               | `external_id` (prefer `externalId`); `Position` synthesised                                                                      |
| `sex` M/F/O/U                                                                          | MAN / WOMAN / `GENDER_UNKNOWN`+`Annotation{OTHER,"other"}` / UNKNOWN                                                             |
| `mother`,`father`                                                                      | `Mating` pair; only one given → single-parent `Mating` (do **not** create the virtual parent)                                    |
| `proband`                                                                              | `proband=true` (≤1 enforced by IR loader)                                                                                        |
| `carrierStatus` `affected`/`carrier`/`presymptomatic`/`''`                             | `Condition{status=AFFECTED/CARRIER/PRESYMPTOMATIC/UNAFFECTED}`; `disorders[i]` → `Condition.name` (one Condition per disorder)   |
| `evaluated`                                                                            | `documented_evaluation`                                                                                                          |
| `lifeStatus` deceased / stillborn / miscarriage / aborted / unborn                     | `deceased=true` / `reproductive_outcome` STILLBIRTH / MISCARRIAGE / TERMINATION / PREGNANCY                                      |
| `gestationAge`                                                                         | `Annotation{GESTATIONAL_AGE,"N weeks"}`                                                                                          |
| `twinGroup` + `monozygotic`                                                            | `Offspring.twin_group` + `twin_type` MZ if `monozygotic` else `ZYGOSITY_TYPE_UNKNOWN` (DZ is *not* distinguishable from unknown) |
| `adoptedIn` / `adoptedStatus`                                                          | `Offspring.adoption` IN/OUT + `parentage=ADOPTIVE` for IN                                                                        |
| `numPersons`                                                                           | `Individual.count` (N → `count_unspecified=true`)                                                                                |
| `childlessStatus` (person or relationship)                                             | `Mating.childlessness` BY_CHOICE(`childless`)/INFERTILITY; `childlessReason` → `Mating.annotations`                              |
| relationship `separated`                                                               | `Mating.status=SEPARATED`                                                                                                        |
| relationship `consanguinity` `Y`                                                       | `Mating.consanguineous=true`; `N`/absent/auto → `false` (never compute)                                                          |
| `birthDate`,`deathDate`,`deceasedAge`,`comments`,`hpoTerms`,`candidateGenes`,`cancers` | \`Annotation{AGE / AGE / AGE / OTHER / PHENOTYPE / VARIANT                                                                       |
| `lostContact`, `ethnicities`, `nodeNumber`, `phenotipsId`, `aliveandwell`              | drop or `Annotation{OTHER}`; `nodeNumber` is *not* a generation label                                                            |

Not expressible / synthesise: `Position`; consultand (absent — PhenoTips has only proband); `sex_assigned_at_birth`;
adoption "by relative"; donor/surrogate roles; ectopic outcome; divorced vs separated (only `separated`); DZ vs unknown
zygosity; per-condition carrier status (single `carrierStatus` applies to all `disorders`). Internal-format
`rel`/`chhub`/`virt` nodes and `width` are layout artefacts — drop.

______________________________________________________________________

## 5. Madeline 2.0 PDE data table

Sources: reference implementation [piratical/Madeline_2.0_PDE](https://github.com/piratical/Madeline_2.0_PDE) —
[`src/FieldLabels.cpp`](https://github.com/piratical/Madeline_2.0_PDE/blob/master/src/FieldLabels.cpp) (label
constants), [`src/DataTable.cpp`](https://github.com/piratical/Madeline_2.0_PDE/blob/master/src/DataTable.cpp) L320–560
(column classification), value lookup tables in
`src/{Gender,Affected,LivingDead,Proband,Consultand,Sampled,Carrier,Pregnancy, RelationshipEnded,Infertility,Sterility,Boolean}.h`,
`src/Twin.cpp`, and examples in
[`ci/testdata/*.data`](https://github.com/piratical/Madeline_2.0_PDE/tree/master/ci/testdata). The documentation site
`https://madeline.med.umich.edu/madeline/` returned HTTP 503 on 2026-09-13 and `documentation/` in the repo only points
to it (`SeeWebDocumentation.txt`); the code and test data are the available primary source.

**Layout** (from `ci/testdata`): two accepted text layouts — (a) one tab-delimited header row + tab-delimited rows
(`cs_001.data`, `cx_001.data`, `ic_002.data`); (b) "long" header: one column name per line, a blank line, then
whitespace-aligned rows (`tw_001.data`, `collapsed.data`, `pregnancies.data`). Column order is free (header-driven);
names are matched case-insensitively (constants are upper-case, test data uses `Individualid`, `FamilyId`,
`IndividualId`). Missing value is `.` (`Twin::MISSING_TWIN` → `"."`; all founders' parents are `.`).

**Columns** (`FieldLabels.cpp` L37–84; classification comments in `DataTable.cpp`):

| Label                                                                                                        | Role                                                                                                                                       | Accepted values (lookup tables)                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FamilyId`, `IndividualId`, `Father`, `Mother`, `Gender`                                                     | required core (`DataTable.cpp` L909–913)                                                                                                   | Gender: `m/f/M/F/male/female/Male/Female/MALE/FEMALE/♂/♀/男/女`; `.` = unknown (`si_001` row `U00106 .`)                                                                          |
| `DOB`                                                                                                        | optional core                                                                                                                              | `yyyy.mm.dd`, partial `yyyy.mm`, `yyyy` (`Date.cpp` "Set a date from a yyyy.mm.dd string"; `si_001.data`)                                                                         |
| `MZTwin`, `DZTwin`                                                                                           | optional core                                                                                                                              | group marker string (e.g. `A`); rows sharing a marker in the same column form one twin set; `.` = not a twin; Madeline appends type char `M`/`D`/`U` internally (`Twin.h` L58–66) |
| `Affected` and **any column whose name starts with `Affected`** (`Affected_HD`, `Affected_GL`, `Affected_T`) | optional core; "*Any* field that starts with the Affected field's prefix … is treated as an affection field"; first seen = default shading | `a/u/A/U/affected/unaffected/Affected/Unaffected/AFFECTED/UNAFFECTED`; `.` missing                                                                                                |
| `Deceased`                                                                                                   | optional core                                                                                                                              | `Y/N/y/n/yes/no/Yes/No/YES/NO/dead/alive/deceased/living`                                                                                                                         |
| `Proband`                                                                                                    | optional core                                                                                                                              | `Y/N…/p/P/proband/Proband`                                                                                                                                                        |
| `Consultand`                                                                                                 | optional core; "Treat consultand just like proband"                                                                                        | `Y/N…`                                                                                                                                                                            |
| `Sampled`                                                                                                    | optional core                                                                                                                              | `Y/N…/s/S/u/U`                                                                                                                                                                    |
| `Carrier`                                                                                                    | optional core                                                                                                                              | `Y/N…/c/C/carrier/Carrier/CARRIER`                                                                                                                                                |
| `ObligateCarrier`                                                                                            | optional core                                                                                                                              | boolean (`collapsed.data` uses `Y`)                                                                                                                                               |
| `Pregnancy`                                                                                                  | optional core                                                                                                                              | `Y/N…/P/p/pregnancy/Pregnancy` (`pregnancies.data`: `P`, with a free extra `Gestation` column `20wks`)                                                                            |
| `RelationshipEnded`                                                                                          | optional core                                                                                                                              | `d/s/e/D/S/E/y/Y/yes/Yes/YES/divorced/separated/ended` (all map to one ENDED boolean)                                                                                             |
| `Infertility`                                                                                                | optional core                                                                                                                              | `azoospermia` (male), `endometriosis` (female), any case                                                                                                                          |
| `Sterility`                                                                                                  | optional core                                                                                                                              | `vasectomy`, `tubal`, `tubal ligation`, any case                                                                                                                                  |
| `Collapsed`                                                                                                  | optional core                                                                                                                              | group marker string (`A`,`B`,`C` in `collapsed.data`); rows sharing a marker are drawn as one collapsed sibship                                                                   |
| `SampleQuantity`                                                                                             | optional core                                                                                                                              | number (`1.85`, `0.25`)                                                                                                                                                           |
| `Superscript`                                                                                                | optional core                                                                                                                              | free text drawn as superscript                                                                                                                                                    |
| `Donor`                                                                                                      | optional core                                                                                                                              | mapping in `Donor.cpp` (values not extracted)                                                                                                                                     |
| `FirstName`, `LastName`, `Name`, other                                                                       | free label columns; `IndividualId` is the default node label                                                                               |                                                                                                                                                                                   |

Official-style minimal example (`ci/testdata/tw_001.data`, long-header layout, abridged):

```
FamilyId
IndividualId
Gender
Father
Mother
Deceased
Proband
DOB
MZTwin
DZTwin
Sampled
Affected
Name

tw_001      00020       M .           .           Y . .          . . . . .
tw_001      00021       F .           .           Y . .          . . . . .
tw_001      00022       M 00020       00021       . Y 1987.02.12 . . . A Fred
tw_001      00023       F 00020       00021       . . 1985.04.17 . . . . Nancy
```

Consanguinity test cases (`cs_*.data`) carry **no flag column** — Madeline detects loops from the graph. All test rows
have both parents or neither; one-parent behaviour not verified.

### Mapping to grus IR

| Madeline                                                 | IR                                                                                                                                                                                                                                                |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FamilyId`                                               | `Pedigree.id`; one `Pedigree` per family                                                                                                                                                                                                          |
| `IndividualId`                                           | `external_id`; `Position` synthesised                                                                                                                                                                                                             |
| `Father`/`Mother` (`.` = none)                           | `Mating` pair                                                                                                                                                                                                                                     |
| `Gender` m/f/.                                           | MAN / WOMAN / UNKNOWN                                                                                                                                                                                                                             |
| `Affected*` columns                                      | one `Condition{name=suffix after "Affected_" ("" for plain Affected), status}` per column                                                                                                                                                         |
| `Carrier` Y                                              | `Condition{status=CARRIER}` (name = default affected column, or ""); `ObligateCarrier` → same + `Annotation{OTHER,"obligate carrier"}`                                                                                                            |
| `Deceased`                                               | `deceased`                                                                                                                                                                                                                                        |
| `Proband` / `Consultand`                                 | `proband` / `consultand`                                                                                                                                                                                                                          |
| `MZTwin` / `DZTwin` marker                               | `Offspring.twin_group` (one id per marker) + `twin_type` MZ / DZ                                                                                                                                                                                  |
| `Pregnancy` P                                            | `reproductive_outcome=PREGNANCY`; extra `Gestation` text → `Annotation{GESTATIONAL_AGE}`                                                                                                                                                          |
| `Collapsed` marker                                       | collapse rows sharing a marker into one `Individual` with `count=n` (lossy: drops per-member ids) — or keep individuals and ignore                                                                                                                |
| `RelationshipEnded`                                      | `Mating.status` — value keeps divorced vs separated distinct in the *input* (`d`/`s`) but the lookup collapses to one boolean; importer may map `d/divorced→DIVORCED`, `s/separated→SEPARATED`, else SEPARATED, on the row individual's mating(s) |
| `Infertility` / `Sterility`                              | `Mating.childlessness=INFERTILITY` + `Annotation` with the stated reason                                                                                                                                                                          |
| `Sampled`, `SampleQuantity`, `Superscript`, `DOB`, names | `Annotation{OTHER / MEASUREMENT / OTHER / AGE / OTHER}` or `documented_evaluation` for Sampled (judgement call — Sampled is "DNA sampled", not "records reviewed")                                                                                |
| `Donor`                                                  | `reproductive_role` (values TBD)                                                                                                                                                                                                                  |

Not expressible / synthesise: `Position`; consanguinity (graph-derived only → `false`); adoption; presymptomatic;
stillbirth/SAB/TOP (only `Pregnancy`); `Deceased` has no date/age column (DOB only); per-condition carrier status (one
`Carrier` flag).

______________________________________________________________________

## 6. Brief: CanRisk v2/v4 and GEDCOM 5.5.1

### CanRisk pedigree file (v2 deprecated; v3, v4 current)

Sources:
[v2 spec (DEPRECATED)](https://canrisk.atlassian.net/wiki/spaces/FAQS/pages/1950875649/CanRisk+Pedigree+Data+v2+File+Format+Specification),
[v4 spec](https://canrisk.atlassian.net/wiki/spaces/FAQS/pages/2749661185/CanRisk+Pedigree+Data+v4+File+Format+Specification),
[knowledgebase overview](https://canrisk.atlassian.net/wiki/spaces/FAQS/overview) (v1/v2 deprecated, v3/v4 current);
official web-service test data
[`bws/tests/data/d7.canrisk4`](https://github.com/CCGE-BOADICEA/bws/blob/master/bws/tests/data/d7.canrisk4).

"A simple TAB-delimited text format consisting of two mandatory header records followed by a series of pedigree data
records, one for each family member." Header 1: `##CanRisk 2.0` (v2) / `##CanRisk 4` (v4 test data); optional risk
factor lines `##key=value` (e.g. `##menarche=15`, `##BMI=24.58`, `##ethnicity=…`, `##PRS_BC=alpha=…,zscore=…`); header 2
= the column line. v4 column line (from `d7.canrisk4`):

```
##FamID Name Target IndivID FathID MothID Sex MZtwin Dead Age Yob BC1 BC2 OC PRO PAN Ashkn BRCA1 BRCA2 PALB2 ATM CHEK2 BARD1 RAD51D RAD51C BRIP1 HOXB13 ER:PR:HER2:CK14:CK56
```

Values (v2/v4 spec): `FamID` ≤13 chars; `Name` ≤8 chars; `Target` `1` = risk-calculation target, `0` other; `IndivID` ≤7
chars; `FathID`/`MothID` id or `0`; `Sex` `M`/`F`; `MZtwin` `0` = not identical twin, `1–9, A` = identifies MZ twin
pairs (same code = same pair); `Dead` `0` alive / `1` dead; `Age` age at last follow-up (`0` = unspecified); `Yob` (`0`
unspecified); `BC1 BC2 OC PRO PAN` age at diagnosis (`0` = unaffected, `AU` = affected, unknown age); `Ashkn` `0/1`;
gene columns `type:result` with type `0` untested / `S` mutation search / `T` direct test and result `0`/`P`/`N`;
pathology `ER:PR:HER2:CK14:CK56` each `0`/`N`/`P`. Rule: "Each family member must have either: (1) no parents specified
… or (2) both parents specified." Example rows (`d7.canrisk4`):

```
XXXA	F1	1	PB	PF	PM	F	0	0	45	1979	0	0	0	0	0	0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0:0:0:0
XXXA	202	0	PM	0	0	F	0	0	67	1958	55	0	0	0	0	0	S:N	S:P	S:N	S:N	S:N	S:N	S:N	S:N	S:N	T:N	N:N:N:0:0
XXXA	NA	0	PGA	0	0	M	0	1	0	0	0	0	0	0	0	0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0	0:0:0:0:0
```

Multiple pedigrees per file are allowed (`multi.canrisk4` repeats the `##CanRisk 4` header block).

IR mapping: `FamID→Pedigree.id`; `IndivID→external_id`; `Name→Annotation{CHOSEN_NAME}`; parents→`Mating` (both-or-none
enforced); `Sex→Gender`; `Target=1→proband` (CanRisk semantics = consultand/target — arguably `consultand`);
`Dead→ deceased`; `MZtwin code→twin_group + MONOZYGOTIC`; each cancer column with non-`0` →
`Condition{name="breast cancer"…, status=AFFECTED, onset_age=value}` (`AU` → no onset); `0` → UNAFFECTED for that cancer
(or omit); gene `*:P` → `Annotation{GENOTYPE,"BRCA1 P"}` (do not set CARRIER — mutation-positive ≠ Bennett carrier by
phenotype); `Age`/`Yob` → `Annotation{AGE}`. Not expressible: `Position`; DZ twins; consanguinity; adoption; pregnancy
outcomes; consultand distinct from target; risk factors (drop).

### GEDCOM 5.5.1 (family-graph subset)

Source: [GEDCOM 5.5.1 spec PDF](https://gedcom.io/specifications/ged551.pdf) — line grammar ch.1 pp.9–12; `FAM_RECORD`
p.24; `INDIVIDUAL_RECORD` p.25; `CHILD_TO_FAMILY_LINK` p.31–32; `INDIVIDUAL_EVENT_STRUCTURE` p.34;
`SPOUSE_TO_FAMILY_LINK` p.40; `ADOPTED_BY_WHICH_PARENT` p.42; `CHILD_LINKAGE_STATUS` p.44; `PEDIGREE_LINKAGE_TYPE` p.57;
`SEX_VALUE` p.61.

Line grammar: `gedcom_line := level + delim + [optional_xref_ID] + tag + [optional_line_value] + terminator`; level 0–99
without leading zeros; delimiter is a single space; xref `@…@` ≤22 chars, unique in file; a new record starts at level
0; file = `0 HEAD … 0 TRLR`. Relevant structures (verbatim tag lines, `{min:max}`):

```
0 @<XREF:FAM>@ FAM                      {1:1}
  1 <<FAMILY_EVENT_STRUCTURE>>          {0:M}   // MARR [Y|<NULL>], DIV, ANUL, ENGA, …
  1 HUSB @<XREF:INDI>@                  {0:1}
  1 WIFE @<XREF:INDI>@                  {0:1}
  1 CHIL @<XREF:INDI>@                  {0:M}   // "preferred order … is chronological by birth"
  1 NCHI <COUNT_OF_CHILDREN>            {0:1}   // "reported number of children … regardless of whether … represented"
0 @<XREF:INDI>@ INDI                    {1:1}
  1 SEX <SEX_VALUE>                     {0:1}   // M = Male, F = Female, U = "Undetermined from available records"
  1 <<INDIVIDUAL_EVENT_STRUCTURE>>      {0:M}   // BIRT [Y|<NULL>] (+2 FAMC @F@), DEAT [Y|<NULL>], ADOP (+2 FAMC @F@ +3 ADOP [HUSB|WIFE|BOTH])
  1 <<CHILD_TO_FAMILY_LINK>>            {0:M}   // 1 FAMC @F@  2 PEDI [adopted|birth|foster|sealing]  2 STAT [challenged|disproven|proven]
  1 <<SPOUSE_TO_FAMILY_LINK>>           {0:M}   // 1 FAMS @F@
```

"There can be no more than one HUSB/father and one WIFE/mother listed in each FAM_RECORD. … a man [in] more than one
family union … would appear in more than one FAM_RECORD. The family record structure assumes that the HUSB/father is
male and WIFE/mother is female." Death is asserted by `1 DEAT Y` or a `DEAT` with `DATE`/`PLAC`; "the presence of a
level number and a tag alone should not be used to assert data". `ADOPTED_BY_WHICH_PARENT`: `HUSB`, `WIFE`, `BOTH`.

IR mapping: `FAM` → `Mating{partner_a=HUSB, partner_b=WIFE, offspring=CHIL in order}`; `FAM` with `DIV` →
`status= DIVORCED`; `SEX M/F/U` → MAN/WOMAN/UNKNOWN; `DEAT` → `deceased`; `FAMC.PEDI adopted` or `ADOP` event →
`Offspring. adoption=IN` + `parentage=ADOPTIVE` (the birth-family `FAMC`, if also present, → `adoption=OUT` on that
edge); `PEDI foster` → `parentage=ADOPTIVE` + annotation; `NCHI` → hint for `count`; `NAME` → `Annotation{CHOSEN_NAME}`;
`@I1@` → `external_id`. Not expressible: affection/carrier (no standard tag; vendor `_GENSTAT` only),
proband/consultand, twins (same `BIRT.DATE` is a heuristic — do not infer), consanguinity, pregnancy outcomes (only
`AGE_AT_EVENT STILLBORN` inside an event age), `Position`. A FAM missing HUSB or WIFE → single-parent `Mating`.
