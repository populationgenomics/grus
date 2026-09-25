"""Differential of two grus trees over a corpus: what a renderer change moves.

Each tree lays out and draws every case in its own worker pool, one tree after the other so their timings do not
compete. The comparison is per case: newly deferring, newly drawing, a changed deferral reason, and different SVG
bytes; then corpus-wide centring error, off-bar descents and layout-time percentiles for each side.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import pathlib
from collections.abc import Sequence

from tools.fuzz import corpus, probe, workers


def run(tree: pathlib.Path, cases: Sequence[corpus.Case], jobs: int, highs: bool) -> dict[str, probe.Result]:
    """Every case's result in ``tree``, keyed by case key."""
    with workers.pool(tree, jobs) as pool:
        results = pool.imap(functools.partial(probe.measure, highs=highs), cases, chunksize=1)
        return {r.key: r for r in results}


@dataclasses.dataclass(frozen=True)
class Comparison:
    """Case keys by what changed from A to B."""

    newly_deferring: list[str]
    newly_drawing: list[str]
    reason_changed: list[str]
    bytes_differ: list[str]

    @property
    def changed(self) -> int:
        return len(self.newly_deferring) + len(self.newly_drawing) + len(self.reason_changed) + len(self.bytes_differ)


def compare(a: dict[str, probe.Result], b: dict[str, probe.Result]) -> Comparison:
    """Compare two runs over the same corpus.

    Raises:
        ValueError: the runs cover different cases.
    """
    if a.keys() != b.keys():
        raise ValueError("the two runs cover different cases")
    c = Comparison([], [], [], [])
    for k in a:
        ra, rb = a[k], b[k]
        if ra.drawn and rb.deferred is not None:
            c.newly_deferring.append(k)
        elif ra.deferred is not None and rb.drawn:
            c.newly_drawing.append(k)
        elif ra.deferred is not None and rb.deferred is not None:
            if ra.deferred != rb.deferred:
                c.reason_changed.append(k)
        elif ra.drawn and rb.drawn and ra.drawn.svg_sha1 != rb.drawn.svg_sha1:
            c.bytes_differ.append(k)
    return c


def _drawn(r: probe.Result) -> probe.Drawn:
    if r.drawn is None:
        raise ValueError(f"{r.key} did not draw")
    return r.drawn


def _pct(xs: list[float], q: float) -> float:
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def _side(name: str, rs: dict[str, probe.Result]) -> list[str]:
    drawn = [r.drawn for r in rs.values() if r.drawn]
    ts = sorted(r.seconds for r in rs.values())
    off = [d.off_bar for d in drawn]
    cent = [d.centring_error for d in drawn if d.centring_error > 1e-6]
    return [
        f"{name}: draws {len(drawn)}, defers {len(rs) - len(drawn)}",
        f"  centring error > 1e-6 in {len(cent)} (max {max(cent, default=0.0):g})",
        f"  descents off their bar: {sum(off)} in {sum(1 for o in off if o)} pedigrees (max {max(off, default=0)}),"
        f" {sum(d.off_bar_meeting for d in drawn)} meeting another bar",
        f"  layout seconds: p50 {_pct(ts, 0.5):.3f}  p90 {_pct(ts, 0.9):.3f}  p99 {_pct(ts, 0.99):.3f}"
        f"  max {ts[-1]:.3f}  total {sum(ts):.1f}",
    ]


def report(
    names: tuple[str, str], a: dict[str, probe.Result], b: dict[str, probe.Result], c: Comparison, show: int
) -> str:
    """A plain-text report, listing at most ``show`` cases per change."""
    lines = [f"{len(a)} pedigrees; A = {names[0]}, B = {names[1]}"]

    def section(title: str, keys: list[str], detail: str) -> None:
        lines.append(f"{title}: {len(keys)}")
        lines.extend("  " + detail.format(k=k, a=a[k], b=b[k]) for k in keys[:show])
        if len(keys) > show:
            lines.append(f"  ... {len(keys) - show} more")

    section("newly deferring", c.newly_deferring, "{k}: {b.deferred}")
    section("newly drawing", c.newly_drawing, "{k}: was {a.deferred}")
    section("deferral reason changed", c.reason_changed, "{k}: {a.deferred} -> {b.deferred}")
    widths = {k: (_drawn(a[k]).width, _drawn(b[k]).width) for k in c.bytes_differ}
    narrower = sum(1 for wa, wb in widths.values() if wb < wa - 1e-6)
    wider = sum(1 for wa, wb in widths.values() if wb > wa + 1e-6)
    section(
        f"SVG bytes differ (narrower {narrower}, wider {wider})",
        c.bytes_differ,
        "{k}: width {a.drawn.width:g} -> {b.drawn.width:g}",
    )
    lines += _side("A", a) + _side("B", b)
    slow = sorted(a, key=lambda k: -b[k].seconds)[:5]
    lines.append("slowest on B: " + ", ".join(f"{k} {b[k].seconds:.2f}s (A {a[k].seconds:.2f}s)" for k in slow))
    lines.append(f"changed: {c.changed}")
    return "\n".join(lines)


def dump(path: pathlib.Path, a: dict[str, probe.Result], b: dict[str, probe.Result]) -> None:
    """Write both runs' raw results as JSON, for analysis beyond the report."""
    raw = {"a": [dataclasses.asdict(r) for r in a.values()], "b": [dataclasses.asdict(r) for r in b.values()]}
    path.write_text(json.dumps(raw, indent=1))
