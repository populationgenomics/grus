"""Shuffle invariance: a pedigree's drawing must not depend on the order its individuals and matings are listed in.

The IR gives identity by ``Position``, not by list order, so the renderer has to break every tie on identity. Each case
is drawn as given and under several seeded shuffles of its ``individuals`` and ``matings``; it varies if the outcomes
(SVG bytes, or a deferral) are not all equal. Offspring order is birth order, which the drawing keeps, so it is never
shuffled.
"""

from __future__ import annotations

import functools
import pathlib
from collections.abc import Sequence

from tools.fuzz import corpus, probe, workers


def run(
    tree: pathlib.Path, cases: Sequence[corpus.Case], shuffles: int, jobs: int, highs: bool
) -> dict[str, list[str]]:
    """Each case's outcomes (as given, then each shuffle) in ``tree``, keyed by case key."""
    with workers.pool(tree, jobs) as pool:
        task = functools.partial(probe.shuffle_case, shuffles=shuffles, highs=highs)
        return dict(pool.imap(task, cases, chunksize=1))


def varying(results: dict[str, list[str]]) -> list[str]:
    """Keys of the cases whose outcomes are not all equal."""
    return [k for k, outs in results.items() if len(set(outs)) > 1]


def report(results: dict[str, list[str]], show: int) -> str:
    bad = varying(results)
    lines = [f"{len(bad)} of {len(results)} vary under shuffle"]
    lines += [f"  {k}: {' '.join(o[:8] for o in results[k])}" for k in bad[:show]]
    if len(bad) > show:
        lines.append(f"  ... {len(bad) - show} more")
    return "\n".join(lines)
