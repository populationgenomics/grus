"""An individual's label stack and its estimated width, shared by the layout (label clearance) and drawing.

The layout needs the widths to separate neighbours whose labels would collide; drawing needs the same widths to
reserve each label box. One estimate serves both, so what the layout spaced for is what drawing draws.
"""

from __future__ import annotations

import dataclasses

from grus.models import pedigree_pb2 as pb
from grus.render import _geometry

_ROMAN = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


def roman(n: int) -> str:
    """Roman numeral for a positive generation number ``n`` (deterministic; ``n`` is small)."""
    if n < 1:
        raise ValueError(f"generation number must be >= 1, got {n}")
    out: list[str] = []
    for value, sym in _ROMAN:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def position(ind: pb.Individual) -> str:
    """The drawn position id, ``"II-3"`` — the individual's identity in the IR and in the document."""
    return f"{roman(ind.generation)}-{ind.index}"


def label_lines(ind: pb.Individual) -> list[str]:
    """Label stack for ``ind``: the as-drawn position id first, then each annotation's text.

    Line 1 is the reconstructed position ``"II-2"`` (Roman ``generation`` + ``index``); then each
    ``Annotation``'s verbatim text. Blanks and duplicates are dropped (first occurrence wins).
    """
    out: list[str] = []
    lines = [position(ind)]
    lines += [a.text for a in ind.annotations]
    for line in lines:
        if line and line not in out:
            out.append(line)
    return out


def count_text(ind: pb.Individual) -> str | None:
    """The text drawn inside a count-collapsed symbol: ``"n"`` for an unknown number, else the count; None for one.

    Bennett draws a group of individuals as one symbol with the number (or ``n``) inside it. ``count`` absent or 1
    is one person and draws nothing. The IR's protovalidate rules guarantee ``count >= 1`` when set and never
    together with ``count_unspecified``; the renderer validates before drawing.
    """
    if ind.count_unspecified:
        return "n"
    return str(ind.count) if ind.count > 1 else None


# Inside a symbol, the count's font is COUNT_SCALE of the symbol size, shrunk so its estimated width fits the
# shape's width at mid-height less a margin: most of a square, less of a circle, half a diamond's diagonal.
COUNT_SCALE = 0.45
_COUNT_FIT = {pb.GENDER_MAN: 0.8, pb.GENDER_WOMAN: 0.7}
_COUNT_FIT_DIAMOND = 0.5
# Outside a symbol, the count sits beside its upper right: COUNT_OUTSIDE_SCALE of the symbol size, COUNT_OUTSIDE_DX
# pixels right of the edge (past the deceased slash's tip, 0.4 of the half-size beyond the corner), top-aligned with
# the symbol so it stays above a mating line leaving the right side.
COUNT_OUTSIDE_SCALE = 0.35
COUNT_OUTSIDE_DX = 8.0
_X_LINKED = frozenset({pb.INHERITANCE_X_LINKED_RECESSIVE, pb.INHERITANCE_X_LINKED_DOMINANT})


@dataclasses.dataclass(frozen=True)
class CountMark:
    """Where and how large a count-collapsed symbol's number is drawn.

    Attributes:
        text: the number, or ``n`` for an unknown number.
        size: font size in pixels.
        inside: centred inside the symbol; else beside its upper right, left-aligned.
    """

    text: str
    size: float
    inside: bool


def condition_legend(p: pb.Pedigree) -> list[str]:
    """Ordered distinct condition names in the pedigree — the index that keys each condition's fills and section.

    The **primary** condition comes first, so it draws in the most distinct fill: the proband's affected condition
    when a proband is affected with a named one, else the condition with the most affected individuals. After it,
    and breaking ties, the base order: phenotype legend labels (their drawn order), then any remaining condition
    names by first appearance, walking individuals in ``Position`` order (never input order, so the legend and every
    fill are independent of how the IR lists its individuals). A condition's index selects its fills and its
    section, consistently pedigree-wide, so two conditions never share a section or a fill.
    """
    order: list[str] = []
    seen: set[str] = set()
    for label in p.labels:
        if label.kind == pb.LABEL_KIND_PHENOTYPE and label.text and label.text not in seen:
            seen.add(label.text)
            order.append(label.text)
    people = sorted(p.individuals, key=lambda ind: (ind.generation, ind.index))
    for ind in people:
        for c in ind.conditions:
            if c.name and c.name not in seen:
                seen.add(c.name)
                order.append(c.name)
    primary = _primary_condition(people, order)
    return order if primary is None else [primary, *(name for name in order if name != primary)]


def _primary_condition(people: list[pb.Individual], order: list[str]) -> str | None:
    """The condition drawn first: a proband's affected one, else the most affected; ties go to ``order``."""

    def affected(ind: pb.Individual) -> set[str]:
        return {c.name for c in ind.conditions if c.name and c.status == pb.CONDITION_STATUS_AFFECTED}

    for ind in people:
        if ind.proband and (named := affected(ind)):
            return min(named, key=order.index)
    counts = {name: sum(name in affected(ind) for ind in people) for name in order}
    best = max(counts.values(), default=0)
    return next(name for name in order if counts[name] == best) if best else None


def data_legend(p: pb.Pedigree) -> list[str]:
    """The ``data-conditions`` array: the named legend plus a trailing ``""`` slot when any condition is unnamed."""
    unnamed = any(not c.name for ind in p.individuals for c in ind.conditions)
    return [*condition_legend(p), *([""] if unnamed else [])]


def sixths(p: pb.Pedigree) -> bool:
    """Whether ``p`` is drawn in sixths (five or six conditions), where a lone affected condition keeps its wedge."""
    return len(data_legend(p)) > 4


def has_borders(ind: pb.Individual, geom: _geometry.Geometry, *, sixths: bool) -> bool:
    """Whether ``ind``'s symbol draws a filled section's border through its centre.

    A symbol with any section fill does: a carrier, several conditions, or in ``sixths`` any affected condition. A
    symbol wholly in one affected fill, or with none, is its outline alone; the X-linked dot is no section.
    """
    affected = {c.name for c in ind.conditions if c.status == pb.CONDITION_STATUS_AFFECTED}
    carriers = [c for c in ind.conditions if c.status == pb.CONDITION_STATUS_CARRIER and c.name not in affected]
    carried = len({c.name for c in carriers})
    if (
        geom.carrier_style is _geometry.CarrierStyle.INHERITANCE_GLYPH
        and not affected
        and any(c.inheritance in _X_LINKED for c in carriers)
    ):
        carried -= 1  # one carried condition is the dot, not a section
    filled = len(affected) + carried
    return filled > 1 or (filled == 1 and (carried == 1 or sixths))


def has_centre_mark(ind: pb.Individual, geom: _geometry.Geometry, *, sixths: bool) -> bool:
    """Whether drawing puts a mark through the symbol's centre, where the count would go.

    The unknown-status ``?``, the X-linked carrier dot (``CarrierStyle.INHERITANCE_GLYPH``), a filled section's
    border (``has_borders``), the presymptomatic line and the deceased slash — the same conditions under which
    ``_draw`` emits them.
    """
    statuses = {c.status for c in ind.conditions}
    affected = pb.CONDITION_STATUS_AFFECTED in statuses
    x_linked_dot = (
        geom.carrier_style is _geometry.CarrierStyle.INHERITANCE_GLYPH
        and not affected
        and any(c.status == pb.CONDITION_STATUS_CARRIER and c.inheritance in _X_LINKED for c in ind.conditions)
    )
    return (
        ind.deceased
        or pb.CONDITION_STATUS_PRESYMPTOMATIC in statuses
        or (not affected and pb.CONDITION_STATUS_UNKNOWN in statuses)
        or x_linked_dot
        or has_borders(ind, geom, sixths=sixths)
    )


def count_mark(ind: pb.Individual, geom: _geometry.Geometry, *, sixths: bool) -> CountMark | None:
    """The count's placement: inside the symbol, shrunk to fit, unless a centre mark is there; None for one person.

    The count never overprints another mark: with one through the centre (``has_centre_mark``) it moves beside the
    symbol's upper right, clear of the slash (which leaves the upper-right corner above the symbol), the arrow (lower
    left), the labels (below) and a mating line (at centre height).
    """
    text = count_text(ind)
    if text is None:
        return None
    if has_centre_mark(ind, geom, sixths=sixths):
        return CountMark(text, COUNT_OUTSIDE_SCALE * geom.symbol_size, inside=False)
    fit = _COUNT_FIT.get(ind.gender, _COUNT_FIT_DIAMOND) * geom.symbol_size
    return CountMark(text, min(COUNT_SCALE * geom.symbol_size, fit / (0.6 * len(text))), inside=True)


def label_reach(ind: pb.Individual, geom: _geometry.Geometry, *, side: int, sixths: bool) -> tuple[float, float]:
    """How far ``ind``'s drawing reaches left and right of its symbol's centre, in pixels: labels and count.

    ``side`` is ``Layout.label_side``: 0 for a stack centred under its symbol; for a symbol whose descent drops from
    its own centre (a count-collapsed or sideless lone parent), which would run through a centred stack, 1 for a
    stack aligned ``label_gap`` right of the line and -1 for one ``label_gap`` left of it. A count placed outside
    the symbol reaches right (``outside_count_reach``); ``sixths`` is ``sixths(pedigree)``.
    """
    w = label_width(ind, geom)
    left, right = {0: (w / 2, w / 2), 1: (0.0, geom.label_gap + w), -1: (geom.label_gap + w, 0.0)}[side]
    return left, max(right, outside_count_reach(ind, geom, sixths=sixths))


def outside_count_reach(ind: pb.Individual, geom: _geometry.Geometry, *, sixths: bool) -> float:
    """How far right of the symbol's centre a count placed beside the symbol reaches, in pixels; 0.0 if none."""
    mark = count_mark(ind, geom, sixths=sixths)
    if mark is None or mark.inside:
        return 0.0
    return geom.symbol_size / 2 + COUNT_OUTSIDE_DX + 0.6 * mark.size * len(mark.text)


def label_width(ind: pb.Individual, geom: _geometry.Geometry) -> float:
    """Estimated pixel width of ``ind``'s widest label line (conservative — text is unmeasurable here).

    Floored by the minimum label box width, so a consumer may substitute label text up to that wide.
    """
    estimate = 0.6 * geom.label_size * max((len(line) for line in label_lines(ind)), default=1)
    return max(estimate, geom.label_box_width * geom.label_size)
