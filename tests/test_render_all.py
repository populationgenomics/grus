"""Every golden and every case of the standard fuzz corpus draws, not only lays out.

A refusal can come from drawing as well as layout (a fill the drawer has no form for), and a passing layout check
would hide it. So every golden is drawn in full, and every fuzz case that lays out is drawn as the generator gives it
and again with a legend of exactly 1, 2, 4 and 6 conditions spread over its individuals: every layout that succeeds
must also draw.
"""

from __future__ import annotations

import functools
import pathlib

import pytest

from grus import ir, render
from grus.models import pedigree_pb2 as pb
from tools.fuzz import gen

_GOLDENS = pathlib.Path(__file__).parent / "goldens"
_NAMES = sorted(path.stem for path in _GOLDENS.glob("*.pbtxt"))
_SEEDS = range(20)  # the corpus the fuzz tests use (test_fuzz_gen)
_STATUSES = (pb.CONDITION_STATUS_AFFECTED, pb.CONDITION_STATUS_CARRIER, pb.CONDITION_STATUS_UNAFFECTED)


@pytest.mark.parametrize("name", _NAMES)
def test_every_golden_draws(name: str) -> None:
    p = ir.load_pbtxt((_GOLDENS / f"{name}.pbtxt").read_text())
    svg = render.render_svg(p)
    assert 'class="individual' in svg


@functools.cache
def _lays_out(seed: int) -> bool:
    """Whether fuzz case ``seed`` lays out (a deferral here is the layout's, and not this test's concern)."""
    try:
        render.layout(gen.gen(seed, 4))
    except render.DeferredFeatureError:
        return False
    return True


def _with_conditions(p: pb.Pedigree, n: int) -> pb.Pedigree:
    """``p`` with a legend of ``n`` conditions spread over its individuals: statuses and sets vary by position."""
    out = pb.Pedigree()
    out.CopyFrom(p)
    names = [f"cond{i}" for i in range(n)]
    out.labels.extend(pb.Label(text=name, kind=pb.LABEL_KIND_PHENOTYPE) for name in names)  # the legend is exactly n
    for ind in out.individuals:
        k = ind.generation * 7 + ind.index * 3
        for j, name in enumerate(names):
            status = _STATUSES[(k + j) % len(_STATUSES)]
            if (k >> j) % 2:
                ind.conditions.add(name=name, status=status)
    return out


@pytest.mark.parametrize("seed", _SEEDS)
@pytest.mark.parametrize("conditions", [0, 1, 2, 4, 6])
def test_every_fuzz_case_that_lays_out_draws(seed: int, conditions: int) -> None:
    if not _lays_out(seed):
        pytest.skip("the layout defers this case")
    p = _with_conditions(gen.gen(seed, 4), conditions) if conditions else gen.gen(seed, 4)
    render.render_svg(p)  # raises on any drawing-time refusal
