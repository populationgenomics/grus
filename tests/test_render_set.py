"""Tests for grus.render.render_set_svg: a PedigreeSet -> one composed, tiled SVG (docs/plans/13).

Each pedigree is drawn by the same layout+draw as ``render_svg`` and stacked vertically; a tier-1-deferred
pedigree becomes a labelled placeholder so the rest of the figure still renders; an empty set is a minimal
canvas. Nested ``<svg>`` tiles keep each pedigree's coordinate system, so drawing is unchanged.
"""

from __future__ import annotations

from grus import render
from grus.models import pedigree_pb2 as pb


def _trio(title: str) -> pb.Pedigree:
    return pb.Pedigree(
        labels=[pb.Label(text=title, kind=pb.LABEL_KIND_FAMILY)],
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(
                generation=2,
                index=1,
                gender=pb.GENDER_MAN,
                conditions=[pb.Condition(status=pb.CONDITION_STATUS_AFFECTED)],
            ),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            )
        ],
    )


def _two_parented() -> pb.Pedigree:
    """A pedigree the tier-1 layout defers: a child (II-1) is offspring of more than one mating."""
    return pb.Pedigree(
        labels=[pb.Label(text="loopy", kind=pb.LABEL_KIND_FAMILY)],
        individuals=[
            pb.Individual(generation=1, index=1, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=2, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=1, index=3, gender=pb.GENDER_MAN),
            pb.Individual(generation=1, index=4, gender=pb.GENDER_WOMAN),
            pb.Individual(generation=2, index=1, gender=pb.GENDER_MAN),
        ],
        matings=[
            pb.Mating(
                partner_a=pb.Position(generation=1, index=1),
                partner_b=pb.Position(generation=1, index=2),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
            pb.Mating(
                partner_a=pb.Position(generation=1, index=3),
                partner_b=pb.Position(generation=1, index=4),
                offspring=[pb.Offspring(child=pb.Position(generation=2, index=1))],
            ),
        ],
    )


def test_empty_set_is_a_minimal_valid_svg() -> None:
    svg = render.render_set_svg(pb.PedigreeSet())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert svg.count("<svg") == 1 and svg.count("</svg>") == 1  # one root, no tiles


def test_set_stacks_each_pedigree_as_a_nested_svg_with_its_title() -> None:
    svg = render.render_set_svg(pb.PedigreeSet(pedigrees=[_trio("Family 1"), _trio("Family 2")]))
    assert svg.count("<svg") == 3  # one root + two nested tiles
    assert svg.count("</svg>") == 3
    assert "Family 1" in svg and "Family 2" in svg  # titles drawn


def test_tile_body_preserves_the_single_render_content() -> None:
    ped = _trio("Family 1")
    single = render.render_svg(ped)
    figure = render.render_set_svg(pb.PedigreeSet(pedigrees=[ped]))
    # the symbols are the same drawing, just wrapped in a nested <svg>: same square/circle counts.
    assert figure.count("<rect") == single.count("<rect")
    assert figure.count("<circle") == single.count("<circle")


def test_deferred_pedigree_becomes_a_placeholder_and_others_still_render() -> None:
    svg = render.render_set_svg(pb.PedigreeSet(pedigrees=[_trio("ok"), _two_parented()]))
    assert "deferred" in svg  # the tier-3 family is a placeholder box, not a crashed figure
    assert "stroke-dasharray" in svg
    assert "ok" in svg  # the renderable family rendered alongside it


def test_render_svgs_returns_one_document_per_pedigree() -> None:
    svgs = render.render_svgs(pb.PedigreeSet(pedigrees=[_trio("Family 1"), _trio("Family 2")]))
    assert [title for title, _ in svgs] == ["Family 1", "Family 2"]
    for _title, svg in svgs:  # each is its own standalone document, not a composed canvas
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert svg.count("<svg") == 1


def test_render_svgs_defers_per_pedigree_without_dropping_others() -> None:
    svgs = render.render_svgs(pb.PedigreeSet(pedigrees=[_trio("ok"), _two_parented()]))
    assert len(svgs) == 2
    assert "deferred" not in svgs[0][1] and svgs[0][1].count("<rect")  # the drawable family drew normally
    assert "deferred" in svgs[1][1]  # the deferred family is its own placeholder document


def test_render_svgs_empty_set_is_empty_list() -> None:
    assert render.render_svgs(pb.PedigreeSet()) == []


def test_render_set_is_deterministic() -> None:
    s = pb.PedigreeSet(pedigrees=[_trio("A"), _trio("B")])
    assert render.render_set_svg(s) == render.render_set_svg(s)


def test_title_is_xml_escaped() -> None:
    svg = render.render_set_svg(pb.PedigreeSet(pedigrees=[_trio("A & B <x>")]))
    assert "A &amp; B &lt;x&gt;" in svg
    assert "A & B <x>" not in svg  # the raw ampersand / angle brackets never leak into the SVG
