"""``grus`` — the command-line entry point for the public surface: validate, render, import.

Each subcommand is a thin wrapper over a tested library seam (``grus.ir``, ``grus.render``,
``grus.convert``), so nothing here beyond argument parsing and file I/O is load-bearing. The
CPG-internal stages (extract, eval, review, corpus) keep their own ``python -m grus.<stage>``
entry points and are not dispatched from here.

Input is a ``PedigreeSet`` or a bare ``Pedigree`` in either text surface (pbtxt / proto3-JSON); the
surface is taken from the file suffix (``.json`` -> JSON, anything else -> pbtxt) unless ``--format``
says otherwise. A bare ``Pedigree`` renders as a single drawing; a set renders as the composed figure.

``layout`` writes an IR file's stored layout (a ``PedigreeLayout``, or a ``PedigreeSetLayout`` for a set) in the
same text surfaces, chosen by the output suffix; ``render --layout`` draws from it instead of laying out
(docs/design/layout-store.md).

Exit status: 0 on success; 1 when the input fails (``validate``) or the ``--layout`` file cannot be drawn from —
unreadable, unparseable, the wrong kind for the input (a set's layout for a single pedigree or the reverse), invalid, or
stale — reported as one line naming the file and the cause; 2 for a usage error.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Literal

import protovalidate
from google.protobuf import json_format, text_format

from grus import convert, ir, render
from grus.models import layout_pb2 as lpb
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


def _describe(e: BaseException) -> str:
    """The error's message, plus each protovalidate violation (``individuals[0].count: must be … [int32.gte]``).

    A protovalidate ``ValidationError`` says only "invalid Pedigree"; the violations name the field and rule. A set's
    per-pedigree failure wraps the pedigree's error, so the cause is described too.
    """
    parts = [str(e)]
    for err in (e, e.__cause__):
        if isinstance(err, ir.ValidationError):
            for v in err.violations:
                path = ".".join(
                    el.field_name + (f"[{el.subscript.value}]" if el.subscript is not None else "")
                    for el in (v.proto.field.elements if v.proto.field is not None else [])
                )
                parts.append(f"{path or '(message)'}: {v.proto.message} [{v.proto.rule_id}]")
    return "; ".join(parts)


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
            print(f"{path}: INVALID: {_describe(e)}", file=sys.stderr)
            continue
        n = len(loaded.pedigrees) if isinstance(loaded, pb.PedigreeSet) else 1
        print(f"{path}: ok ({n} pedigree{'s' if n != 1 else ''})")
    return 1 if failures else 0


class _LayoutFileError(Exception):
    """A ``--layout`` file that cannot be used: unreadable, unparseable, the wrong kind, or invalid."""


def _read_layout[Stored: (lpb.PedigreeLayout, lpb.PedigreeSetLayout)](path: pathlib.Path, kind: type[Stored]) -> Stored:
    """Parse and validate a stored-layout file of ``kind`` (a set's layout for a set input, else one pedigree's)."""
    parse = json_format.Parse if _text_format(path, None) == "json" else text_format.Parse
    try:
        text = path.read_text()
    except OSError as e:
        raise _LayoutFileError(f"cannot read it: {e.strerror or e}") from e
    try:
        record = parse(text, kind())
    except (json_format.ParseError, text_format.ParseError) as e:
        other = lpb.PedigreeLayout if kind is lpb.PedigreeSetLayout else lpb.PedigreeSetLayout
        try:
            parse(text, other())
        except (json_format.ParseError, text_format.ParseError):
            raise _LayoutFileError(f"not a {kind.__name__}: {e}") from e
        input_kind = "a pedigree set" if kind is lpb.PedigreeSetLayout else "a single pedigree"
        raise _LayoutFileError(f"holds a {other.__name__}, but the input is {input_kind}") from e
    try:
        protovalidate.validate(record)
    except protovalidate.ValidationError as e:
        raise _LayoutFileError(f"invalid {kind.__name__}: {'; '.join(_violation(v) for v in e.violations)}") from e
    return record


def _violation(v: protovalidate.Violation) -> str:
    path = v.proto.field
    parts = [
        el.field_name if el.subscript is None else f"{el.field_name}[{el.subscript.value}]"
        for el in (path.elements if path is not None else [])
    ]
    return f"{'.'.join(parts) or '(message)'}: {v.proto.message}"


def _cmd_render(args: argparse.Namespace) -> int:
    loaded = load_ir(args.input, _text_format(args.input, args.format))
    geom = _geometry(args)
    try:
        if isinstance(loaded, pb.PedigreeSet):
            layouts = list(_read_layout(args.layout, lpb.PedigreeSetLayout).pedigrees) if args.layout else None
            svg = render.render_set_svg(loaded, geom, id_prefix=args.id_prefix, stored_layouts=layouts)
        else:
            one = _read_layout(args.layout, lpb.PedigreeLayout) if args.layout else None
            svg = render.render_svg(loaded, geom, id_prefix=args.id_prefix, stored_layout=one)
    except (_LayoutFileError, render.StaleLayoutError) as e:  # the stored layout cannot be drawn from
        print(f"grus render: error: {args.layout}: {e}", file=sys.stderr)
        return 1
    except ValueError as e:  # the renderer's argument checks (id_prefix), reported as a usage error
        print(f"grus render: error: {e}", file=sys.stderr)
        return 2
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


def _cmd_layout(args: argparse.Namespace) -> int:
    loaded = load_ir(args.input, _text_format(args.input, args.format))
    geom = _geometry(args)
    record: lpb.PedigreeLayout | lpb.PedigreeSetLayout = (
        lpb.PedigreeSetLayout(pedigrees=[render.store_layout(p, geom) for p in loaded.pedigrees])
        if isinstance(loaded, pb.PedigreeSet)
        else render.store_layout(loaded, geom)
    )
    out: pathlib.Path | None = args.output
    fmt = args.to or (_text_format(out, None) if out is not None else "pbtxt")
    text = json_format.MessageToJson(record) if fmt == "json" else text_format.MessageToString(record)
    if out is None:
        sys.stdout.write(text)
    else:
        out.write_text(text)
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
    """Build the ``grus`` argument parser with its validate / render / layout / import subcommands."""
    parser = argparse.ArgumentParser(prog="grus", description="Pedigree IR tools: validate, render, layout, import.")
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
        "--id-prefix",
        default="",
        help="namespace for every id in the SVG, for a page that inlines several figures (default: none)",
    )
    render_cmd.add_argument(
        "--carrier-style",
        choices=[s.value for s in render.CarrierStyle],
        help="carrier glyph convention (default: inheritance_glyph)",
    )
    render_cmd.add_argument(
        "--layout",
        type=pathlib.Path,
        help="draw from this stored layout (from `grus layout`) instead of laying out; exit 1 if unusable or stale",
    )
    render_cmd.set_defaults(func=_cmd_render)

    layout_cmd = sub.add_parser("layout", help="lay an IR file out once and write the stored layout")
    layout_cmd.add_argument("input", type=pathlib.Path)
    layout_cmd.add_argument("-o", "--output", type=pathlib.Path, help="output path (default: stdout)")
    layout_cmd.add_argument("--format", choices=("pbtxt", "json"), help="override the suffix-derived text surface")
    layout_cmd.add_argument(
        "--to", choices=("pbtxt", "json"), help="text surface to write (default: from the output suffix, else pbtxt)"
    )
    layout_cmd.set_defaults(func=_cmd_layout, carrier_style=None)

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
