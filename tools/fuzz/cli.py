"""Command line for the fuzz tooling: ``python -m tools.fuzz {gen,diff}``."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections.abc import Sequence

from google.protobuf import text_format

from tools.fuzz import corpus, diff, gen, trees


def _corpus_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--corpus",
        action="append",
        default=[],
        metavar="MAXGEN:LO-HI",
        help=f"generator seeds LO..HI (inclusive) at MAXGEN generations; repeatable (default {corpus.DEFAULT_CORPUS})",
    )
    p.add_argument(
        "--ir",
        action="append",
        default=[],
        type=pathlib.Path,
        metavar="PATH",
        help="a Pedigree or PedigreeSet pbtxt, or a directory searched for them; repeatable",
    )


def _run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--jobs", type=int, default=os.cpu_count(), help="worker processes per tree (default: all CPUs)")
    p.add_argument("--highs", action="store_true", help="solve x with HiGHS (the `highs` extra) instead of z3")


def _cases(args: argparse.Namespace) -> list[corpus.Case]:
    return corpus.build(args.corpus or [corpus.DEFAULT_CORPUS], args.ir)


def _gen(args: argparse.Namespace) -> int:
    sys.stdout.write(text_format.MessageToString(gen.gen(args.seed, args.maxgen)))
    return 0


def _diff(args: argparse.Namespace) -> int:
    cases = _cases(args)
    with trees.materialised([args.a, args.b]) as (ta, tb):
        a = diff.run(ta, cases, args.jobs, args.highs)
        b = diff.run(tb, cases, args.jobs, args.highs)
    c = diff.compare(a, b)
    print(diff.report((args.a, args.b), a, b, c, args.show))
    if args.json:
        diff.dump(args.json, a, b)
    return 1 if c.changed else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command; the exit status is 1 when a check finds a change, else 0."""
    parser = argparse.ArgumentParser(prog="python -m tools.fuzz", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("gen", help="print the fuzz pedigree for a seed as pbtxt")
    p.add_argument("seed", type=int)
    p.add_argument("--maxgen", type=int, default=4)
    p.set_defaults(func=_gen)

    p = sub.add_parser("diff", help="compare two grus trees over a corpus")
    p.add_argument("a", help="baseline tree: a git ref or a directory holding grus/")
    p.add_argument("b", help="tree under test: a git ref or a directory holding grus/")
    _corpus_args(p)
    _run_args(p)
    p.add_argument("--show", type=int, default=20, help="cases listed per change (default 20)")
    p.add_argument("--json", type=pathlib.Path, help="also write both runs' raw results here")
    p.set_defaults(func=_diff)

    args = parser.parse_args(argv)
    return args.func(args)
