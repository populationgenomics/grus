"""The ``grus`` CLI: thin wrappers over ``grus.ir`` / ``grus.render`` — test the wiring, not the library."""

from __future__ import annotations

import pathlib
import re

import pytest

from grus import cli, ir
from grus.models import pedigree_pb2 as pb

_GOLDENS = pathlib.Path(__file__).parent / "goldens"


def _trio() -> pb.Pedigree:
    return ir.load_pbtxt((_GOLDENS / "trio.pbtxt").read_text())


def test_load_ir_bare_pedigree_pbtxt(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "p.pbtxt"
    f.write_text(ir.dump_pbtxt(_trio()))
    assert isinstance(cli.load_ir(f, "pbtxt"), pb.Pedigree)


def test_load_ir_set_json(tmp_path: pathlib.Path) -> None:
    ps = pb.PedigreeSet(pedigrees=[_trio()])
    f = tmp_path / "s.json"
    f.write_text(ir.dump_set_json(ps))
    loaded = cli.load_ir(f, "json")
    assert isinstance(loaded, pb.PedigreeSet) and len(loaded.pedigrees) == 1


def test_validate_reports_ok_and_invalid(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "good.pbtxt"
    good.write_text(ir.dump_pbtxt(_trio()))
    bad = tmp_path / "bad.pbtxt"
    bad.write_text(
        "individuals { generation: 1 index: 1 gender: GENDER_MAN }\n"
        "matings { partner_a { generation: 9 index: 9 } }\n"  # dangling Position reference
    )
    assert cli.main(["validate", str(good), str(bad)]) == 1
    out, err = capsys.readouterr()
    assert "good.pbtxt: ok (1 pedigree)" in out
    assert "bad.pbtxt: INVALID" in err


@pytest.mark.parametrize(
    ("fields", "rule"),
    [
        ("count: 0", "count"),
        ("count: -2", "count"),
        ("count: 3 count_unspecified: true", "individual.count_exclusive"),
    ],
)
def test_validate_reports_an_invalid_count(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], fields: str, rule: str
) -> None:
    bad = tmp_path / "bad.pbtxt"
    bad.write_text(f"individuals {{ generation: 1 index: 1 gender: GENDER_MAN {fields} }}\n")
    assert cli.main(["validate", str(bad)]) == 1
    _, err = capsys.readouterr()
    assert "bad.pbtxt: INVALID" in err and rule in err


def test_render_writes_svg_file_and_matches_library(tmp_path: pathlib.Path) -> None:
    from grus.render import render_svg

    src = tmp_path / "p.pbtxt"
    src.write_text(ir.dump_pbtxt(_trio()))
    out = tmp_path / "p.svg"
    assert cli.main(["render", str(src), "-o", str(out)]) == 0
    assert out.read_text() == render_svg(_trio())


def test_render_set_to_stdout(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "s.pbtxt"
    src.write_text(ir.dump_set_pbtxt(pb.PedigreeSet(pedigrees=[_trio()])))
    assert cli.main(["render", str(src)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("<svg") and "</svg>" in out


def test_render_carrier_style_option(tmp_path: pathlib.Path) -> None:
    from grus.render import CarrierStyle, Geometry, render_svg

    p = ir.load_pbtxt((_GOLDENS / "carrier_inheritance.pbtxt").read_text())
    src = tmp_path / "c.pbtxt"
    src.write_text(ir.dump_pbtxt(p))
    out = tmp_path / "c.svg"
    assert cli.main(["render", str(src), "-o", str(out), "--carrier-style", "partition_fill"]) == 0
    assert out.read_text() == render_svg(p, Geometry(carrier_style=CarrierStyle.PARTITION_FILL))


def test_import_writes_ir_and_roundtrips_through_validate(tmp_path: pathlib.Path) -> None:
    fixture = pathlib.Path(__file__).parent / "convert_fixture" / "two_families.fam"
    out = tmp_path / "fam.pbtxt"
    assert cli.main(["import", str(fixture), "-o", str(out)]) == 0
    ps = ir.load_set_pbtxt(out.read_text())
    assert [p.id for p in ps.pedigrees] == ["FAM1", "FAM2"] and ps.provenance.source_format == "ped"
    assert cli.main(["validate", str(out)]) == 0
    svg = tmp_path / "fam.svg"
    assert cli.main(["render", str(out), "-o", str(svg)]) == 0 and svg.read_text().startswith("<svg")


def test_import_json_to_json_sniffed(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = pathlib.Path(__file__).parent / "convert_fixture" / "simple.openpedigree.json"
    assert cli.main(["import", str(fixture), "--to", "json"]) == 0
    ps = ir.load_set_json(capsys.readouterr().out)
    assert ps.provenance.source_format == "openpedigree"


def test_render_id_prefix_namespaces_ids(tmp_path: pathlib.Path) -> None:
    src = pathlib.Path(__file__).parent / "goldens" / "carrier_inheritance.pbtxt"  # has clip-path ids
    out = tmp_path / "out.svg"
    assert cli.main(["render", str(src), "--id-prefix", "fig3-", "-o", str(out)]) == 0
    ids = re.findall(r'\bid="([^"]+)"', out.read_text())
    assert ids and all(i.startswith("fig3-") for i in ids)


def test_render_rejects_an_unsafe_id_prefix(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = pathlib.Path(__file__).parent / "goldens" / "trio.pbtxt"
    out = tmp_path / "out.svg"
    assert cli.main(["render", str(src), "--id-prefix", 'bad"prefix', "-o", str(out)]) == 2
    assert "id_prefix" in capsys.readouterr().err and not out.exists()


@pytest.mark.parametrize("suffix", [".pbtxt", ".json"])
def test_layout_then_render_from_it_matches_a_fresh_render(tmp_path: pathlib.Path, suffix: str) -> None:
    from grus.render import render_svg

    p = ir.load_pbtxt((_GOLDENS / "lone_parent_sibships.pbtxt").read_text())
    src = tmp_path / "p.pbtxt"
    src.write_text(ir.dump_pbtxt(p))
    stored = tmp_path / f"p.layout{suffix}"
    out = tmp_path / "p.svg"
    assert cli.main(["layout", str(src), "-o", str(stored)]) == 0
    assert cli.main(["render", str(src), "--layout", str(stored), "-o", str(out)]) == 0
    assert out.read_text() == render_svg(p)


def test_layout_of_a_set_renders_the_figure(tmp_path: pathlib.Path) -> None:
    from grus.render import render_set_svg

    ps = pb.PedigreeSet(pedigrees=[_trio(), ir.load_pbtxt((_GOLDENS / "twins.pbtxt").read_text())])
    src = tmp_path / "s.pbtxt"
    src.write_text(ir.dump_set_pbtxt(ps))
    stored = tmp_path / "s.layout.pbtxt"
    out = tmp_path / "s.svg"
    assert cli.main(["layout", str(src), "-o", str(stored)]) == 0
    assert cli.main(["render", str(src), "--layout", str(stored), "-o", str(out)]) == 0
    assert out.read_text() == render_set_svg(ps)


def test_render_refuses_a_stale_layout(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "p.pbtxt"
    src.write_text(ir.dump_pbtxt(_trio()))
    stored = tmp_path / "p.layout.pbtxt"
    assert cli.main(["layout", str(src), "-o", str(stored)]) == 0
    edited = _trio()
    edited.individuals[0].deceased = not edited.individuals[0].deceased
    src.write_text(ir.dump_pbtxt(edited))
    assert cli.main(["render", str(src), "--layout", str(stored)]) == 1
    assert "digest" in capsys.readouterr().err


def test_layout_of_a_set_with_repeated_avuncular_marriages(tmp_path: pathlib.Path) -> None:
    import test_layout2

    from grus.render import render_set_svg

    ps = pb.PedigreeSet(pedigrees=list(test_layout2.GHOST_PEDIGREES.values()))
    src = tmp_path / "s.pbtxt"
    src.write_text(ir.dump_set_pbtxt(ps))
    stored = tmp_path / "s.layout.pbtxt"
    out = tmp_path / "s.svg"
    assert cli.main(["layout", str(src), "-o", str(stored)]) == 0
    assert cli.main(["render", str(src), "--layout", str(stored), "-o", str(out)]) == 0
    assert out.read_text() == render_set_svg(ps)
