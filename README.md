# grus

A **meaning-only pedigree IR** (protobuf), a **deterministic renderer** that draws it as a Bennett/NSGC-standard SVG
pedigree, and **importers** from the pedigree file formats people already have. Python 3.13, MIT.

```sh
uv add grus            # or: pip install grus
grus import family.fam -o family.pbtxt      # PLINK/LINKAGE PED, kinship2 tables, Phenopackets Family, Open Pedigree JSON
grus validate family.pbtxt
grus render family.pbtxt -o family.svg      # --png with the `raster` extra (needs libcairo)
grus layout family.pbtxt -o family.layout.pbtxt   # lay out once; `grus render --layout` then draws from it
```

```python
from grus import convert, ir, render

ps = convert.import_file("family.fam")            # -> PedigreeSet, validated
svg = render.render_set_svg(ps)                   # one SVG for the figure; render.render_svg for one pedigree
text = ir.dump_set_pbtxt(ps)                      # canonical, diffable text form
```

## What the IR is

Who is in the pedigree, their gender and clinical status, and how they relate — never coordinates. Identity is
`Position{generation, index}` (the drawn "II-3"); relationships are `Mating{partner_a?, partner_b?, offspring[]}` so
single parents, founder sibships, consanguinity, separated couples and childlessness are first-class; clinical status is
a per-condition list (affected / carrier / presymptomatic, with inheritance and onset when stated); twins, adoption,
donor parentage, pregnancy outcomes and collapsed sibships live where the standard draws them. The schema covers the
whole Bennett 2008 + 2022 vocabulary and evolves additively. Contract:
[`schema/proto/grus/models/pedigree.proto`](schema/proto/grus/models/pedigree.proto); rationale and the assessment of
Phenopackets / GA4GH Pedigree as alternatives: [`docs/design/ir.md`](docs/design/ir.md).

## What the renderer does

Ranks generations, orders each row to minimise crossings while keeping couples adjacent and sibships contiguous, solves
x as an exact linear program (z3), and draws standard symbols and connectors — deterministically, so the same IR always
yields the same bytes on every platform. `Geometry(x_solver=XSolver.HIGHS)` with the `highs` extra swaps in a
floating-point solver. Consanguineous loops, multiple mates and cross-lineage marriages route as edges rather than
failing; the few shapes it still cannot draw raise `DeferredFeatureError` instead of drawing something wrong.
[`docs/design/renderer.md`](docs/design/renderer.md), [`docs/design/layout-v2.md`](docs/design/layout-v2.md).

## Importers

`grus.convert` reads PLINK `.fam`/`.ped`/`.psam` and LINKAGE `.pre`, kinship2/Pedixplorer tables (with the relation
matrix), GA4GH Phenopackets v2 `Family` JSON, and Open Pedigree / PhenoTips simple JSON. Generation and index are
synthesised (no data format carries them), the source id is kept in `external_id`, and nothing is inferred that the
source did not state. Per-format mapping tables: [`docs/design/convert.md`](docs/design/convert.md).

## Where it comes from

grus is built at the Centre for Population Genomics to round-trip pedigree *figures* in papers: figure → IR (a vision
model) → figure′ (this renderer), judged for semantic equivalence. That programme — the extraction prompt, corpus
mining, evaluation harness and review UI — is not part of this package; the IR and renderer are its stable core.
[`docs/PRODUCT.md`](docs/PRODUCT.md); terms in [`GLOSSARY.md`](GLOSSARY.md). Docs and docstrings here occasionally cite
design notes that stay in the private tree (`architecture.md`, `eval.md`, `corpus.md`, `docs/plans/`); those pointers
are provenance, not links.

## Developing

`uv sync`, then `uv run pytest`, `uv run --group lint pyright`, `pre-commit install`. CI runs the same gates
(`.github/workflows/`): pre-commit, pytest (with and without the `raster` extra), stub freshness, and `buf breaking`
against the base branch. A `v*` tag matching the pyproject version builds, publishes to PyPI (trusted publisher) and
creates the GitHub release (`release.yml`). Protos are the source of truth; after editing one, `buf lint`,
`buf breaking --against .git#branch=main`, and `uv run --group codegen python -m tools.schema.regen` (needs `buf`). A
renderer change runs the fuzz differential and shuffle check against `main` before its PR:
[`tools/fuzz/README.md`](tools/fuzz/README.md).
