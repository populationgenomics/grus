"""The x-solve (grus/render/_xsolve.py): one lexicographic linear program, two backends.

The layout's goldens pin the Z3 result byte-for-byte; these tests pin what the model promises: descent centring
first, compactness settling what centring leaves free (no drift, no iteration budget), a single optimum, and the
floating-point HiGHS backend agreeing with the exact one.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest
import test_render

from grus import render
from grus.models import pedigree_pb2 as pb

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


def _p(g: int, i: int) -> pb.Position:
    return pb.Position(generation=g, index=i)


def _ind(g: int, i: int, gender: pb.Gender = pb.GENDER_MAN) -> pb.Individual:
    return pb.Individual(generation=g, index=i, gender=gender)


@pytest.mark.parametrize(("twins", "couple_gap", "sib_gap"), [(4, 0.5, 0.7), (6, 1.0, 1.1), (5, 0.3, 0.9)])
def test_rigid_gaps_are_exact_for_any_separation(twins: int, couple_gap: float, sib_gap: float) -> None:
    # A twin group is one rigid block. Its pairs were once fixed at differences of cumulative float offsets, which
    # can land an ulp below the separation: under exact arithmetic ``== d`` and ``>= sep`` then contradict, and the
    # model had no solution. The pairs now take the separation itself.
    p = pb.Pedigree(individuals=[_ind(1, 1), _ind(1, 2, pb.GENDER_WOMAN), *(_ind(2, k) for k in range(1, twins + 1))])
    p.matings.add(
        partner_a=_p(1, 1),
        partner_b=_p(1, 2),
        offspring=[
            pb.Offspring(child=_p(2, k), twin_group=1, twin_type=pb.ZYGOSITY_TYPE_MONOZYGOTIC)
            for k in range(1, twins + 1)
        ],
    )
    geom = dataclasses.replace(render.DEFAULT_GEOMETRY, couple_gap=couple_gap, sib_gap=sib_gap)
    row = render.layout(p, geom).pos[1]
    assert all(abs(b - a - sib_gap) < _EPS for a, b in itertools.pairwise(row))


@pytest.mark.parametrize("backend", list(render.XSolver))
def test_parts_with_no_descent_link_are_anchored(backend: render.XSolver) -> None:
    # Two families on disjoint generations share no descent, so nothing in the objectives ties one's position to the
    # other's; with a single pinned cell the tie-break was unbounded (Z3 returned an arbitrary point, HiGHS failed).
    # Every cell is now x >= 0, so each part settles at the left edge.
    if backend is render.XSolver.HIGHS:
        pytest.importorskip("highspy")
    p = pb.Pedigree(
        individuals=[
            _ind(1, 1),
            _ind(1, 2, pb.GENDER_WOMAN),
            _ind(2, 1),
            _ind(3, 1),
            _ind(3, 2, pb.GENDER_WOMAN),
            _ind(4, 1),
        ]
    )
    p.matings.add(partner_a=_p(1, 1), partner_b=_p(1, 2), offspring=[pb.Offspring(child=_p(2, 1))])
    p.matings.add(partner_a=_p(3, 1), partner_b=_p(3, 2), offspring=[pb.Offspring(child=_p(4, 1))])
    lay = render.layout(p, dataclasses.replace(render.DEFAULT_GEOMETRY, x_solver=backend))
    assert lay.pos == [[0.0, 1.0], [0.5], [0.0, 1.0], [0.5]]
