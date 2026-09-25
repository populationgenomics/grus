"""The fuzz differential: a tree against itself changes nothing, and each worker runs the tree it was given."""

import dataclasses
import pathlib

import pytest

from tools.fuzz import corpus, diff, trees

_REPO = pathlib.Path(__file__).resolve().parents[1]


def test_a_tree_against_itself_reports_no_change() -> None:
    cases = corpus.build(["4:0-3"], [_REPO / "tests" / "goldens" / "twins.pbtxt"])
    with trees.materialised([str(_REPO), str(_REPO)]) as (ta, tb):
        a = diff.run(ta, cases, jobs=2, highs=False)
        b = diff.run(tb, cases, jobs=2, highs=False)
    assert list(a) == [c.key for c in cases]
    c = diff.compare(a, b)
    assert c.changed == 0
    assert "changed: 0" in diff.report(("a", "b"), a, b, c, show=5)


def test_compare_sorts_changes() -> None:
    cases = corpus.build(["4:0-1"], [])
    with trees.materialised([str(_REPO)]) as (t,):
        a = diff.run(t, cases, jobs=1, highs=False)
    first, second = (a[c.key] for c in cases)
    assert first.drawn is not None and second.drawn is not None
    moved = {first.key: first, second.key: dataclasses.replace(second, drawn=None, deferred="a deferral")}
    c = diff.compare(a, moved)
    assert c.newly_deferring == [second.key]
    assert c.changed == 1


def test_a_directory_without_grus_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="no grus/render"), trees.materialised([str(tmp_path)]):
        pass


def test_overlapping_corpus_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        corpus.build(["4:0-3", "4:3"], [])


def test_a_tree_whose_renderer_does_not_import_fails_instead_of_hanging(tmp_path: pathlib.Path) -> None:
    (tmp_path / "grus" / "render").mkdir(parents=True)
    (tmp_path / "grus" / "render" / "__init__.py").write_text('raise ImportError("broken tree")\n')
    with trees.materialised([str(tmp_path)]) as (t,), pytest.raises(RuntimeError, match="broken tree"):
        diff.run(t, corpus.build(["4:0"], []), jobs=1, highs=False)
