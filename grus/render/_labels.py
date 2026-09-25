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


def has_centre_mark(ind: pb.Individual, geom: _geometry.Geometry) -> bool:
    """Whether drawing puts a mark through the symbol's centre, where the count would go.

    The unknown-status ``?``, the X-linked carrier dot (``CarrierStyle.INHERITANCE_GLYPH``), the presymptomatic
    line and the deceased slash — the same conditions under which ``_draw`` emits them.
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
    )


def count_mark(ind: pb.Individual, geom: _geometry.Geometry) -> CountMark | None:
    """The count's placement: inside the symbol, shrunk to fit, unless a centre mark is there; None for one person.

    The count never overprints another mark: with one through the centre (``has_centre_mark``) it moves beside the
    symbol's upper right, clear of the slash (which leaves the upper-right corner above the symbol), the arrow (lower
    left), the labels (below) and a mating line (at centre height).
    """
    text = count_text(ind)
    if text is None:
        return None
    if has_centre_mark(ind, geom):
        return CountMark(text, COUNT_OUTSIDE_SCALE * geom.symbol_size, inside=False)
    fit = _COUNT_FIT.get(ind.gender, _COUNT_FIT_DIAMOND) * geom.symbol_size
    return CountMark(text, min(COUNT_SCALE * geom.symbol_size, fit / (0.6 * len(text))), inside=True)


def label_reach(ind: pb.Individual, geom: _geometry.Geometry, *, beside: bool) -> tuple[float, float]:
    """How far ``ind``'s drawing reaches left and right of its symbol's centre, in pixels: symbol, labels, count.

    A stack is centred under its symbol, unless ``beside``: a symbol whose descent drops from its own centre (a
    count-collapsed or sideless lone parent) would have that line run through a centred stack, so the stack is
    left-aligned ``label_gap`` right of the line instead. A count placed outside the symbol reaches right
    (``outside_count_reach``).
    """
    w = label_width(ind, geom)
    left, right = (0.0, geom.label_gap + w) if beside else (w / 2, w / 2)
    return left, max(right, outside_count_reach(ind, geom))


def outside_count_reach(ind: pb.Individual, geom: _geometry.Geometry) -> float:
    """How far right of the symbol's centre a count placed beside the symbol reaches, in pixels; 0.0 if none."""
    mark = count_mark(ind, geom)
    if mark is None or mark.inside:
        return 0.0
    return geom.symbol_size / 2 + COUNT_OUTSIDE_DX + 0.6 * mark.size * len(mark.text)


def label_width(ind: pb.Individual, geom: _geometry.Geometry) -> float:
    """Estimated pixel width of ``ind``'s widest label line (conservative — text is unmeasurable here).

    Floored by the minimum label box width, so a consumer may substitute label text up to that wide.
    """
    estimate = 0.6 * geom.label_size * max((len(line) for line in label_lines(ind)), default=1)
    return max(estimate, geom.label_box_width * geom.label_size)
