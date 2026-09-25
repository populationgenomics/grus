# Renderer fuzz tools

The goldens under [`tests/goldens/`](../../tests/goldens/) pin a few dozen hand-written figures, byte for byte. A change
to the layout also moves pedigrees nobody wrote by hand: it can make a family that drew start deferring, widen a
drawing, pull a descent off its sibship bar, or make one pedigree take minutes. These tools show what a renderer change
moves across a corpus of generated pedigrees, before review.

The corpus comes from a seeded generator ([`gen.py`](gen.py)): founder couples, sibships with twins, marry-ins, cousin
marriages and second spouses, several generations deep. A seed names the same pedigree on every revision (a test pins
this), so a corpus is comparable across branches. A case is named `g{maxgen}:{seed}`; IR files added with `--ir` are
named by path.

## Before opening a renderer PR

From the repository root, with the branch checked out:

```sh
uv run --extra highs python -m tools.fuzz diff origin/main . --ir tests/goldens
uv run --extra highs python -m tools.fuzz shuffle .
```

The default corpus is seeds 0-299 at four generations; a run takes tens of minutes, since a few seeds take minutes each
to lay out. `diff` exits 1 when anything changed, which a drawing change is meant to do, so read the report rather than
the status:

- **newly deferring**: pedigrees that drew on `main` and now raise `DeferredFeatureError`, with the reason. Each is a
  regression unless the change means to stop drawing that shape.
- **newly drawing**: deferrals the change removed. Look at a few of them rendered.
- **SVG bytes differ**: drawings that changed, with their width before and after. Expected for a change to the drawing;
  unexpected for a refactor, where it should be zero.
- per side: centring error (a couple's midpoint against its children's mean), descents off their bar (and how many of
  those meet another sibship's bar), and layout-time percentiles and the slowest cases.

Put the summary lines in the PR description. Shuffle should report 0 varying, and exits 1 otherwise: the IR identifies
individuals by `Position`, not by list order, so a drawing that changes when the lists are reordered has a tie broken on
input order.

Times are wall-clock with every CPU busy. Compare the two sides of one run, not runs on different days, and treat small
ratios as noise: a tree diffed against itself on a loaded machine has shown sides 40% apart. A layout that takes minutes
shows up in `max` and in the slowest list.

## Commands

`python -m tools.fuzz <command> --help` gives every option.

- `gen SEED [--maxgen N]` prints a fuzz pedigree as pbtxt.
- `diff A B` lays out and draws the corpus with tree A, then with tree B, and compares them case by case.
  `--corpus MAXGEN:LO-HI` (repeatable) replaces the default seeds; `--ir PATH` (repeatable) adds a `Pedigree` or
  `PedigreeSet` pbtxt, or every one under a directory; `--json OUT` keeps the raw results; `--highs` solves with HiGHS.
- `shuffle TREE` draws each case as given and under `--shuffles N` (default 3) seeded reorderings of its individuals and
  matings. Offspring order is birth order and is never shuffled.
- `minimize PREDICATE TREE [TREE_B] (--input FILE | --seed S) -o OUT` shrinks a failing pedigree while the predicate
  holds, and writes it as pbtxt:
  - `defers`: it defers on TREE (`--reason TEXT` narrows to a deferral message containing TEXT);
  - `regress`: it draws on TREE and defers on TREE_B;
  - `bytes`: it draws on both with different SVG bytes;
  - `shuffle`: its drawing on TREE varies under shuffles.

A tree is a git ref, extracted with `git archive`, or a directory holding a `grus` package: `.` is the working tree,
uncommitted edits included. Only its `grus` and `buf` packages are put on the workers' path, so the tree under test
never replaces these tools.

## From a finding to a test

A newly deferring or varying case is easier to fix from its smallest form. Minimise it against the baseline, render it,
and add it as a regression test or a golden:

```sh
uv run --extra highs python -m tools.fuzz minimize regress origin/main . --seed 123 -o /tmp/min.pbtxt
uv run grus render /tmp/min.pbtxt -o /tmp/min.svg
```

The minimiser drops leaf children and whole matings with their descendants while the failure holds, then renumbers each
generation, so the result reads like a figure. It is greedy: the result is small, not guaranteed smallest.
