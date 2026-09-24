"""The x-solve (grus/render/_xsolve.py): one lexicographic linear program, two backends.

The layout's goldens pin the Z3 result byte-for-byte; these tests pin what the model promises: descent centring
first, compactness settling what centring leaves free (no drift, no iteration budget), a single optimum, and the
floating-point HiGHS backend agreeing with the exact one.
"""

from __future__ import annotations

import dataclasses

import pytest
import test_render

from grus import render

_EPS = 1e-6


def _at(p, lay):  # type: ignore[no-untyped-def]
    idx = test_render._index(p)
    coords = test_render._coords(lay)
    return {pos: coords[i][2] for pos, i in idx.items()}


def test_hinge_family_is_centred_and_compact() -> None:
    # II-2 marries twice and II-5 (married in, no drawn parents) is held only by their couple's midpoint: sliding
    # II-5 by 2t and III-5 by t moves no child off its parents, so centring cannot place them. The old relaxation
    # sweeps drifted along that direction until their pass cap (70 units wide for 12 people). Compactness, ranked
    # below centring, places them: III-5 under II-2 x II-5 and packed against its row.
    p = test_render._load("hinge_family")
    lay = render.layout(p)
    x = _at(p, lay)
    assert abs((x[(2, 2)] + x[(2, 5)]) / 2 - x[(3, 5)]) < _EPS
    assert abs(x[(3, 5)] - x[(3, 4)] - render.DEFAULT_GEOMETRY.sib_gap) < _EPS
    assert max(v for row in lay.pos for v in row) < 10


@pytest.mark.parametrize("name", test_render._NAMES)
def test_z3_is_repeatable(name: str) -> None:
    p = test_render._load(name)
    assert render.layout(p) == render.layout(p)


@pytest.mark.parametrize("name", test_render._NAMES)
def test_highs_agrees_with_z3(name: str) -> None:
    pytest.importorskip("highspy")
    p = test_render._load(name)
    exact = render.layout(p)
    floating = render.layout(p, dataclasses.replace(render.DEFAULT_GEOMETRY, x_solver=render.XSolver.HIGHS))
    assert floating.nid == exact.nid
    for a, b in zip(exact.pos, floating.pos, strict=True):
        assert all(abs(u - v) < _EPS for u, v in zip(a, b, strict=True)), f"{name}: backends disagree"
