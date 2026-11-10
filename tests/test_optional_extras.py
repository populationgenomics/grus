"""The ``render`` extra (z3) stays optional: reading, validating and importing IR never import it."""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import sys

import pytest
import test_render

from grus import cli, ir, render
from grus.models import pedigree_pb2 as pb

_ROOT = pathlib.Path(__file__).parent.parent

# Run in a fresh interpreter with z3 unimportable: a module-level `import z3` anywhere on these paths fails the import.
_IMPORT_WITHOUT_Z3 = """
import sys
sys.modules["z3"] = None
import grus.ir, grus.models.pedigree_pb2, grus.models.layout_pb2, grus.convert, grus.render, grus.cli
"""


def test_the_ir_importers_and_renderer_import_without_z3() -> None:
    subprocess.run([sys.executable, "-c", _IMPORT_WITHOUT_Z3], cwd=_ROOT, check=True)


def test_reading_and_validating_ir_never_loads_z3() -> None:
    code = (
        "import sys, grus.ir, grus.convert\n"
        "from grus.models import pedigree_pb2\n"
        "grus.ir.validate_set(pedigree_pb2.PedigreeSet())\n"
        "assert 'z3' not in sys.modules, 'z3 was imported'\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=_ROOT, check=True)


def _trio() -> pb.Pedigree:
    p = test_render._load("trio")
    for ind in p.individuals:
        del ind.conditions[:]
    return p


def test_laying_out_without_z3_names_the_render_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)
    with pytest.raises(render.MissingExtraError, match=r"pip install 'grus\[render\]'"):
        render.render_svg(_trio())


def test_the_highs_solver_without_highspy_names_the_highs_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "highspy", None)
    geom = dataclasses.replace(render.DEFAULT_GEOMETRY, x_solver=render.XSolver.HIGHS)
    with pytest.raises(render.MissingExtraError, match=r"pip install 'grus\[highs\]'"):
        render.render_svg(_trio(), geom)


@pytest.mark.parametrize("command", ["render", "layout"])
def test_the_cli_reports_the_missing_render_extra(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    src = tmp_path / "p.pbtxt"
    src.write_text(ir.dump_pbtxt(_trio()))
    monkeypatch.setitem(sys.modules, "z3", None)
    assert cli.main([command, str(src)]) == 1
    _, err = capsys.readouterr()
    assert err.startswith(f"grus {command}: error: ") and "pip install 'grus[render]'" in err


def test_drawing_from_a_stored_layout_needs_no_z3(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = render.store_layout(_trio())
    monkeypatch.setitem(sys.modules, "z3", None)
    assert render.render_svg(_trio(), stored_layout=stored).startswith("<svg")
