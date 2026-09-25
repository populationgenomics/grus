"""Spacing constants for the renderer (docs/design/renderer.md, "Drawing").

``Geometry`` is the single knob bag shared by layout and drawing. Two of its fields — ``couple_gap``
and ``sib_gap`` — are *layout* units (abstract column spacing that ``layout`` bakes into ``pos``); the
rest are *pixel* constants the drawing step applies when it maps the grid to an SVG canvas
(``px = x0 + x * x_unit``, ``py = y0 + level * gen_height``). Keeping both here means one call site
picks a consistent set; the default reproduces the committed goldens.
"""

from __future__ import annotations

import dataclasses
import enum

from grus.render._xsolve import XSolver


class CarrierStyle(enum.Enum):
    """Which drawing convention to use for a carrier — a rendering choice, not IR meaning.

    Both read the same IR (``Condition.inheritance`` + the pedigree's condition legend); this only picks
    the glyph, so a re-render can match either the existing literature or the current standard.
    """

    INHERITANCE_GLYPH = "inheritance_glyph"  # pre-2022 / practitioner: X-linked -> central dot, else half/region fill
    PARTITION_FILL = "partition_fill"  # NSGC 2022 §4.5: legend-keyed divided fill, dot retired, any inheritance


@dataclasses.dataclass(frozen=True)
class Geometry:
    """Layout + drawing spacing. Defaults are the golden-producing values.

    Attributes:
        gen_height: vertical pixels between adjacent generation rows.
        x_unit: horizontal pixels per layout x-unit (scales ``Layout.pos``).
        symbol_size: side of a square / diameter of a circle / diagonal of a diamond, in pixels.
        couple_gap: layout-unit spacing between the two members of a mating (the tightest spacing,
            so partners are always adjacent on their row).
        sib_gap: layout-unit clearance between two sibling subtrees when packed side by side.
        sib_stub: pixels the horizontal sibship bar sits above the children's symbol tops.
        double_line_offset: pixel separation of the two parallel lines of a consanguineous mating.
        routed_stub: pixels a routed mating's horizontal track sits above the row's symbol tops (an
            orthogonal edge for a >2-mate overflow, running over the intervening symbols).
        routed_track_gap: extra rise per additional routed track sharing one row, so multiple routed
            edges stagger deterministically instead of coinciding.
        elbow_gap: pixels between elbow tracks above a sib bar. A drop the order leaves beside its own children
            (a crossing descent, a cousin standing beside its mate) turns at its own track above the bar, runs
            across and drops onto its bar, so it never runs along another sibship's bar; the row pitch opens by
            one gap per track a figure needs.
        label_size: font size (pixels) of one line of an individual's label stack.
        label_gap: pixels between a symbol's bottom edge and the top of its label stack.
        label_line_gap: pixels between adjacent lines within the label stack.
        margin: blank pixels around the drawn content (also absorbs proband-arrow / slash overhang).
        gen_marker_gutter: width (pixels) of the reserved left gutter that holds the per-row generation
            marker (a Roman numeral). Drawing shifts right by this much; the gutter sits left of the
            ``margin``, so the margin still absorbs the leftmost symbol's arrow/slash overhang and keeps
            it clear of the gutter.
        label_box_width: minimum reserved label width per individual, in em of ``label_size`` (0 = none).
            A floor under the estimated width of the widest label line, so a consumer may replace label
            text client-side with anything up to this wide and stay clear of the neighbours
            (docs/design/svg-output.md).
        label_box_height: minimum reserved label band below each symbol, in em of ``label_size`` (0 =
            none). A floor under the tallest label stack, so a consumer may add lines up to this tall and
            stay clear of the row below.

    ``gen_height`` and ``x_unit`` are floors: drawing opens the row pitch and column pitch further
    when a pedigree's label stacks need it (see ``_draw``). The label stack under each symbol lists the
    pedigree id first, then any annotation lines; the reserved space scales with the pedigree's tallest
    stack (rows) and widest line (columns).
    """

    gen_height: float = 90.0
    x_unit: float = 56.0
    symbol_size: float = 36.0
    couple_gap: float = 1.0
    sib_gap: float = 1.5
    sib_stub: float = 22.0
    double_line_offset: float = 4.0
    routed_stub: float = 14.0  # pixels a routed mating's horizontal track sits above the row's symbol tops
    routed_track_gap: float = 8.0  # extra rise per additional routed track on one row (deterministic stagger)
    elbow_gap: float = 8.0  # pixels between elbow tracks above a sib bar (and the lowest track above the bar)
    childless_stub: float = 18.0  # vertical drop below a childless couple's mating line to the bar
    childless_bar: float = 12.0  # half-width of the horizontal bar (no children / infertility)
    childless_bar_gap: float = 5.0  # vertical gap between the two bars of the infertility glyph
    label_size: float = 11.0
    label_gap: float = 6.0
    label_line_gap: float = 2.0
    label_box_width: float = 0.0
    label_box_height: float = 0.0
    margin: float = 48.0
    gen_marker_gutter: float = 40.0
    # default matches the existing literature (dot for X-linked carriers), so re-renders line up with the
    # corpus figures reviewers compare against; PARTITION_FILL gives the NSGC-2022-normalized output.
    carrier_style: CarrierStyle = CarrierStyle.INHERITANCE_GLYPH
    # The x-solve backend. Z3 is exact (bit-identical positions everywhere, what the goldens are pinned to);
    # HIGHS is the optional floating-point alternative (the ``highs`` extra).
    x_solver: XSolver = XSolver.Z3


DEFAULT_GEOMETRY = Geometry()
