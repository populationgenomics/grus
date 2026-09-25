"""Command line for the fuzz tooling: ``python -m tools.fuzz {gen,diff,shuffle,minimize}``."""

from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import sys
from collections.abc import Sequence

from google.protobuf import text_format

from grus import ir
from tools.fuzz import corpus, diff, gen, minimize, shuffle, trees, workers


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


def _shuffle(args: argparse.Namespace) -> int:
    cases = _cases(args)
    with trees.materialised([args.tree]) as (t,):
        results = shuffle.run(t, cases, args.shuffles, args.jobs, args.highs)
    print(shuffle.report(results, args.show))
    return 1 if shuffle.varying(results) else 0


def _minimize(args: argparse.Namespace) -> int:
    if (args.input is None) == (args.seed is None):
        raise SystemExit("minimize takes exactly one of --input and --seed")
    p = ir.load_pbtxt(args.input.read_text()) if args.input else gen.gen(args.seed, args.maxgen)
    specs = [args.tree] + ([args.tree_b] if args.tree_b else [])
    with trees.materialised(specs) as dirs, contextlib.ExitStack() as stack:
        pools = [stack.enter_context(workers.pool(d, args.jobs)) for d in dirs]
        search = minimize.tree_search(args.predicate, pools, args.reason, args.shuffles, args.highs)
        n = len(p.individuals)
        q, renumbered = minimize.minimize(p, search, batch=args.jobs)
    args.output.write_text(ir.dump_pbtxt(q))
    note = "" if renumbered else " (not renumbered: renumbering lost the failure)"
    print(f"{args.output}: {n} -> {len(q.individuals)} individuals{note}")
    return 0


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

    p = sub.add_parser("shuffle", help="check that drawings do not depend on the order of individuals and matings")
    p.add_argument("tree", help="a git ref or a directory holding grus/")
    _corpus_args(p)
    _run_args(p)
    p.add_argument("--shuffles", type=int, default=3, help="shuffled copies drawn per pedigree (default 3)")
    p.add_argument("--show", type=int, default=20, help="varying cases listed (default 20)")
    p.set_defaults(func=_shuffle)

    p = sub.add_parser(
        "minimize",
        help="shrink a failing pedigree while a predicate holds",
        epilog="predicates: " + "; ".join(f"{k}: {v}" for k, v in minimize.PREDICATES.items()),
    )
    p.add_argument("predicate", choices=sorted(minimize.PREDICATES))
    p.add_argument("tree", help="a git ref or a directory holding grus/")
    p.add_argument("tree_b", nargs="?", help="the second tree, for regress and bytes")
    p.add_argument("--input", type=pathlib.Path, help="a Pedigree pbtxt to minimise")
    p.add_argument("--seed", type=int, help="minimise this fuzz seed instead of --input")
    p.add_argument("--maxgen", type=int, default=4, help="generations for --seed (default 4)")
    p.add_argument("-o", "--output", type=pathlib.Path, required=True, help="where to write the minimised pbtxt")
    p.add_argument("--reason", default="", help="count only deferrals whose message contains this")
    p.add_argument("--shuffles", type=int, default=3, help="shuffled copies, for the shuffle predicate (default 3)")
    _run_args(p)
    p.set_defaults(func=_minimize)

    args = parser.parse_args(argv)
    return args.func(args)
