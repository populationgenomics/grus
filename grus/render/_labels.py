"""An individual's label stack and its estimated width, shared by the layout (label clearance) and drawing.

The layout needs the widths to separate neighbours whose labels would collide; drawing needs the same widths to
reserve each label box. One estimate serves both, so what the layout spaced for is what drawing draws.
"""

from __future__ import annotations

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


def label_reach(ind: pb.Individual, geom: _geometry.Geometry, *, beside: bool) -> tuple[float, float]:
    """How far ``ind``'s label stack reaches left and right of its symbol's centre, in pixels.

    A stack is centred under its symbol, unless ``beside``: a symbol whose descent drops from its own centre (a
    count-collapsed or sideless lone parent) would have that line run through a centred stack, so the stack is
    left-aligned ``label_gap`` right of the line instead.
    """
    w = label_width(ind, geom)
    return (0.0, geom.label_gap + w) if beside else (w / 2, w / 2)


def label_width(ind: pb.Individual, geom: _geometry.Geometry) -> float:
    """Estimated pixel width of ``ind``'s widest label line (conservative — text is unmeasurable here).

    Floored by the minimum label box width, so a consumer may substitute label text up to that wide.
    """
    estimate = 0.6 * geom.label_size * max((len(line) for line in label_lines(ind)), default=1)
    return max(estimate, geom.label_box_width * geom.label_size)
