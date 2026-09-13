"""``grus`` — the command-line entry point for the public surface: validate, render, import.

Each subcommand is a thin wrapper over a tested library seam (``grus.ir``, ``grus.render``,
``grus.convert``), so nothing here beyond argument parsing and file I/O is load-bearing. The
CPG-internal stages (extract, eval, review, corpus) keep their own ``python -m grus.<stage>``
entry points and are not dispatched from here.

Input is a ``PedigreeSet`` or a bare ``Pedigree`` in either text surface (pbtxt / proto3-JSON); the
surface is taken from the file suffix (``.json`` -> JSON, anything else -> pbtxt) unless ``--format``
says otherwise. A bare ``Pedigree`` renders as a single drawing; a set renders as the composed figure.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Literal

from google.protobuf import json_format, text_format

from grus import convert, ir, render
from grus.models import pedigree_pb2 as pb

TextFormat = Literal["pbtxt", "json"]


def _text_format(path: pathlib.Path, explicit: str | None) -> TextFormat:
    if explicit in ("pbtxt", "json"):
        return explicit  # type: ignore[return-value]
    return "json" if path.suffix.lower() == ".json" else "pbtxt"


def load_ir(path: pathlib.Path, fmt: TextFormat) -> pb.PedigreeSet | pb.Pedigree:
    """Parse and validate ``path`` as a ``PedigreeSet``, else as a bare ``Pedigree``.

    A set is tried first because its top-level field (``pedigrees``) is disjoint from a pedigree's; a
    text that parses as neither raises the *pedigree* parse error (the more common hand-authored shape).
    """
    text = path.read_text()
    parse = json_format.Parse if fmt == "json" else text_format.Parse
    try:
        ps = parse(text, pb.PedigreeSet())
    except (json_format.ParseError, text_format.ParseError):
        ps = None
    if ps is not None and (ps.pedigrees or ps.HasField("provenance")):
        ir.validate_set(ps)
        return ps
    p = parse(text, pb.Pedigree())
    ir.validate(p)
    return p


def _geometry(args: argparse.Namespace) -> render.Geometry:
    geom = render.DEFAULT_GEOMETRY
    if args.carrier_style:
        geom = render.Geometry(carrier_style=render.CarrierStyle(args.carrier_style))
    return geom


def _cmd_validate(args: argparse.Namespace) -> int:
    failures = 0
    for path in args.inputs:
        try:
            loaded = load_ir(path, _text_format(path, args.format))
        except Exception as e:  # report every failure, keep going
            failures += 1
            print(f"{path}: INVALID: {e}", file=sys.stderr)
            continue
        n = len(loaded.pedigrees) if isinstance(loaded, pb.PedigreeSet) else 1
        print(f"{path}: ok ({n} pedigree{'s' if n != 1 else ''})")
    return 1 if failures else 0


def _cmd_render(args: argparse.Namespace) -> int:
    loaded = load_ir(args.input, _text_format(args.input, args.format))
    geom = _geometry(args)
    svg = render.render_set_svg(loaded, geom) if isinstance(loaded, pb.PedigreeSet) else render.render_svg(loaded, geom)
    out: pathlib.Path | None = args.output
    if args.png:
        png = render.rasterize(svg, scale=args.scale)
        if out is None:
            sys.stdout.buffer.write(png)
        else:
            out.write_bytes(png)
        return 0
    if out is None:
        sys.stdout.write(svg)
    else:
        out.write_text(svg)
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    ps = convert.import_file(args.input, fmt=args.from_format)
    text = ir.dump_set_json(ps) if args.to == "json" else ir.dump_set_pbtxt(ps)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the ``grus`` argument parser with its validate / render / import subcommands."""
    parser = argparse.ArgumentParser(prog="grus", description="Pedigree IR tools: validate, render, import.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="parse + validate IR files (pbtxt / JSON), report each")
    validate.add_argument("inputs", nargs="+", type=pathlib.Path)
    validate.add_argument("--format", choices=("pbtxt", "json"), help="override the suffix-derived text surface")
    validate.set_defaults(func=_cmd_validate)

    render_cmd = sub.add_parser("render", help="render an IR file to SVG (or PNG)")
    render_cmd.add_argument("input", type=pathlib.Path)
    render_cmd.add_argument("-o", "--output", type=pathlib.Path, help="output path (default: stdout)")
    render_cmd.add_argument("--format", choices=("pbtxt", "json"), help="override the suffix-derived text surface")
    render_cmd.add_argument("--png", action="store_true", help="rasterize to PNG (needs the `raster` extra + libcairo)")
    render_cmd.add_argument("--scale", type=float, default=2.0, help="PNG scale factor (default 2.0)")
    render_cmd.add_argument(
        "--carrier-style",
        choices=[s.value for s in render.CarrierStyle],
        help="carrier glyph convention (default: inheritance_glyph)",
    )
    render_cmd.set_defaults(func=_cmd_render)

    imp = sub.add_parser("import", help="convert an external pedigree file into the IR")
    imp.add_argument("input", type=pathlib.Path)
    imp.add_argument(
        "--from",
        dest="from_format",
        choices=sorted(convert.IMPORTERS),
        help="source format (default: inferred from the suffix, or sniffed for .json; .csv needs kinship2)",
    )
    imp.add_argument("--to", choices=("pbtxt", "json"), default="pbtxt", help="IR text surface to write")
    imp.add_argument("-o", "--output", type=pathlib.Path, help="output path (default: stdout)")
    imp.set_defaults(func=_cmd_import)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI on ``argv`` (default ``sys.argv[1:]``) and return the process exit code."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
