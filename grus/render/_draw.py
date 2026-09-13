"""Drawing: a ``Layout`` grid -> a Bennett-standard SVG pedigree (docs/design/renderer.md).

Reads only the per-level arrays (the geometry seam) plus each individual's symbol attributes from the
IR. Emits deterministic bytes — no randomness, no timestamps, fixed number formatting — so goldens are
stable. Symbols: square (man) / circle (woman) / diamond (nonbinary or unknown); filled by an affected
condition, with a carrier dot, presymptomatic vertical line, deceased slash, and proband/consultand
arrow. Connectors: mating line (doubled for consanguinity; a lone single parent has none), descent +
sibship bar with per-child stubs, a founder sibship's implied hanger stub (a bar with no parents, for
siblings via an undrawn couple), and twin convergence (MZ joining bar). A generation marker (Roman
numeral) is drawn once per row in a reserved left gutter.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict

from grus.models import pedigree_pb2 as pb

from ._geometry import DEFAULT_GEOMETRY, CarrierStyle, Geometry
from ._layout import DeferredFeatureError, Layout, RoutedMating
from ._layout2 import layout

_X_EPS = 1e-9  # float slack when clustering near-equal layout-x into one column

_STROKE = "#000000"
_WIDTH = 2.0
_FONT = "sans-serif"
_GEN_MARKER_SIZE = 16.0
_SET_GAP = 28.0  # vertical gap between stacked pedigrees in a figure render
_TITLE_SIZE = 15.0  # family/panel title above each pedigree tile
_TITLE_GAP = 6.0  # gap between a title and its pedigree

# One stacked pedigree in a figure render: (title, width, height, body-elements).
_Tile = tuple[str, float, float, list[str]]


def render_svg(p: pb.Pedigree, geometry: Geometry | None = None) -> str:
    """Validate, lay out, and draw ``p``; return a complete, deterministic SVG document string."""
    geom = geometry or DEFAULT_GEOMETRY
    lay = layout(p, geom)
    return _Draw(p, lay, geom).svg()


def render_set_svg(pedigree_set: pb.PedigreeSet, geometry: Geometry | None = None) -> str:
    """Render a whole figure's ``PedigreeSet`` as one SVG (docs/design/renderer.md).

    Each pedigree is laid out and drawn exactly as ``render_svg`` does, then the tiles are stacked
    vertically and titled by their display label (``_display_title`` — the FAMILY label if any, else the
    first). A pedigree the tier-1 layout defers becomes a labelled placeholder box so the rest of the
    figure still renders. An empty set yields a minimal empty canvas.
    """
    geom = geometry or DEFAULT_GEOMETRY
    tiles: list[_Tile] = []
    for ped in pedigree_set.pedigrees:
        title = _display_title(ped)
        try:
            draw = _Draw(ped, layout(ped, geom), geom, id_prefix=f"p{len(tiles)}-")
            width, height = draw.dimensions()
            tiles.append((title, width, height, draw.body()))
        except DeferredFeatureError as deferred:
            tiles.append(_placeholder_tile(title, str(deferred)))
    return _compose_tiles(tiles, geom)


def render_svgs(pedigree_set: pb.PedigreeSet, geometry: Geometry | None = None) -> list[tuple[str, str]]:
    """Render each pedigree in a set to its **own** standalone SVG document — one per family.

    Unlike ``render_set_svg`` (which stacks the families into a single composed canvas), this returns a
    ``(title, svg)`` per pedigree so a caller can present them separately (the review UI's render carousel).
    ``title`` is the display label (``_display_title``); a family the tier-1 layout defers becomes a labelled
    "deferred" placeholder SVG rather than raising, mirroring ``render_set_svg`` — so the list always has one
    entry per pedigree, each a complete document.
    """
    geom = geometry or DEFAULT_GEOMETRY
    out: list[tuple[str, str]] = []
    for ped in pedigree_set.pedigrees:
        title = _display_title(ped)
        try:
            svg = _Draw(ped, layout(ped, geom), geom).svg()
        except DeferredFeatureError as deferred:
            _, width, height, body = _placeholder_tile(title, str(deferred))
            svg = _svg_root(width, height, body)
        out.append((title, svg))
    return out


def _display_title(ped: pb.Pedigree) -> str:
    """The label to show above a pedigree tile: the FAMILY label if present, else the first label."""
    for label in ped.labels:
        if label.kind == pb.LABEL_KIND_FAMILY:
            return label.text
    return ped.labels[0].text if ped.labels else ""


def _compose_tiles(tiles: list[_Tile], geom: Geometry) -> str:
    """Stack tiles vertically into one SVG document, each centred under its title."""
    margin = geom.margin
    if not tiles:
        side = margin * 2
        return _svg_root(side, side, [])
    content_w = max(w for _, w, _, _ in tiles)
    body: list[str] = []
    y = margin
    for title, w, h, tile_body in tiles:
        if title:
            body.append(_text(margin + content_w / 2, y + _TITLE_SIZE / 2, _escape(title), _TITLE_SIZE))
            y += _TITLE_SIZE + _TITLE_GAP
        x = margin + (content_w - w) / 2  # centre a narrower pedigree in the figure column
        body.append(
            f'<svg x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" viewBox="0 0 {_num(w)} {_num(h)}">'
        )
        body += tile_body
        body.append("</svg>")
        y += h + _SET_GAP
    return _svg_root(margin * 2 + content_w, y - _SET_GAP + margin, body)


def _svg_root(width: float, height: float, body: list[str]) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}" height="{_num(height)}" '
        f'viewBox="0 0 {_num(width)} {_num(height)}">',
        *body,
        "</svg>",
    ]
    return "\n".join(out) + "\n"


def _placeholder_tile(title: str, reason: str) -> _Tile:
    """A dashed box naming a deferred (non-tier-1) pedigree, so a figure with one still renders the rest.

    The reason is word-wrapped (not clipped) and the box sized to fit, so a reviewer sees *why* the pedigree
    could not be drawn rather than a truncated fragment.
    """
    lines = ["deferred — not yet drawable:", *_wrap_words(reason, 58)]
    line_h = 16.0
    width = max(220.0, max(len(line) for line in lines) * 0.6 * 12.0 + 20.0)
    height = 14.0 + line_h * len(lines)
    body = [
        f'<rect x="1" y="1" width="{_num(width - 2)}" height="{_num(height - 2)}" fill="none" '
        f'stroke="{_STROKE}" stroke-dasharray="4 3"/>',
    ]
    y = 15.0
    for line in lines:
        body.append(_text(width / 2, y, _escape(line), 12.0))
        y += line_h
    return (title, width, height, body)


def _wrap_words(text: str, width: int) -> list[str]:
    """Greedy word-wrap ``text`` into lines of at most ``width`` characters (deterministic)."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if len(candidate) > width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(v: float) -> str:
    """Format a coordinate compactly and deterministically (3 dp, trailing zeros stripped, no -0)."""
    r = round(v, 3)
    if r == 0:
        r = 0.0
    return f"{r:.3f}".rstrip("0").rstrip(".")


class _Draw:
    """Holds the pedigree, its layout, and geometry; emits the SVG body once."""

    def __init__(self, p: pb.Pedigree, lay: Layout, geom: Geometry, id_prefix: str = "") -> None:
        self.p = p
        self.lay = lay
        self.geom = geom
        self.id_prefix = id_prefix  # namespaces SVG element ids (clipPaths) so composed tiles don't collide
        self.half = geom.symbol_size / 2
        # The label band = the deepest label stack. A proband/consultand with a wide annotation line opens
        # it up, because that line is pushed past the arrow (_line_ys) rather than overlapping it.
        self.label_band = max(
            (self._stack_bottom(ind) for ind in p.individuals),
            default=geom.label_gap + geom.label_size,
        )
        self.gen_height = max(geom.gen_height, geom.symbol_size + self.label_band + geom.label_gap + geom.sib_stub)
        self._legend = self._condition_legend()  # ordered condition names -> carrier fill region (which half)
        self._px = self._build_px_map()
        self._x_lo, self._x_hi = self._content_bounds()

    @staticmethod
    def _label_lines(ind: pb.Individual) -> list[str]:
        """Label stack for ``ind``: the as-drawn position id first, then each annotation's text.

        Line 1 is the reconstructed position ``"II-2"`` (Roman ``generation`` + ``index``); then each
        ``Annotation``'s verbatim text. Blanks and duplicates are dropped (first occurrence wins).
        """
        out: list[str] = []
        lines = [f"{_roman(ind.generation)}-{ind.index}"]
        lines += [a.text for a in ind.annotations]
        for line in lines:
            if line and line not in out:
                out.append(line)
        return out

    def _arrow_bottom(self, ind: pb.Individual) -> float:
        """How far a proband/consultand arrow reaches below the symbol's bottom edge (0.0 if none).

        The arrow points into the lower-left corner; its tail — and, for a proband, the 'P' just below the
        tail — is the lowest point. Used to push a colliding label line past it.
        """
        if not (ind.proband or ind.consultand):
            return 0.0
        reach = self.geom.symbol_size / math.sqrt(2)  # the arrow tail
        if ind.proband:
            reach += 4 + 15 / 2  # the 'P' label sits just below the tail (font 15, centred)
        return reach

    def _line_ys(self, ind: pb.Individual) -> list[float]:
        """Each label line's baseline, as an offset below the symbol's bottom edge.

        Lines centre under the symbol. A proband/consultand arrow occupies the lower-left; a label line
        wide enough to reach into it (wider than the symbol) and sitting in its vertical band is pushed
        down — together with the lines beneath it — past the arrow (docs/design/renderer.md). Narrow lines
        (the position id) are untouched, so an arrowed node with only a short id renders unchanged.
        """
        size, gap = self.geom.label_size, self.geom.label_gap
        step = size + self.geom.label_line_gap
        arrow_bottom = self._arrow_bottom(ind)
        ys: list[float] = []
        push = 0.0
        for line in self._label_lines(ind):
            base = gap + size / 2 + len(ys) * step + push
            wide = 0.6 * size * len(line) > self.geom.symbol_size  # extends past the symbol's left edge
            if arrow_bottom and wide and base - size / 2 < arrow_bottom:
                delta = arrow_bottom + gap - (base - size / 2)
                push += delta
                base += delta
            ys.append(base)
        return ys

    def _stack_bottom(self, ind: pb.Individual) -> float:
        """The bottom of ``ind``'s label stack, as an offset below the symbol's bottom edge."""
        ys = self._line_ys(ind)
        return (max(ys) + self.geom.label_size / 2) if ys else self.geom.label_gap + self.geom.label_size

    def _ind_at(self, idx: int) -> pb.Individual:
        """The individual in cell ``idx`` — resolving a synthetic ghost (cross-generation duplicate) to its real."""
        return self.p.individuals[self.lay.ghost_of.get(idx, idx)]

    def _label_w(self, ind: pb.Individual) -> float:
        """Estimated pixel width of ``ind``'s widest label line (conservative — text is unmeasurable here)."""
        return 0.6 * self.geom.label_size * max((len(line) for line in self._label_lines(ind)), default=1)

    def _build_px_map(self) -> dict[float, float]:
        """Map each layout-x to a pixel offset, widening only the gaps where wide labels would collide.

        A single global column pitch (``x_unit`` scaled to the widest label *anywhere*) spreads every
        column to fit one wide annotation — the dominant source of horizontal whitespace. Instead keep
        the geometric pitch (``x_unit`` per layout unit) as a floor and add width only across the gaps
        whose two adjacent same-row labels would otherwise overlap. The result is a longest-path over
        left-to-right constraints — the geometric floor between consecutive columns, plus a per-row
        label-clearance between each adjacent pair — so it is monotonic and equal layout-x map to equal
        pixel-x, keeping every descent drop and mating line vertical.
        """
        xs = sorted({v for row in self.lay.pos for v in row})
        if not xs:
            return {}
        cols: list[float] = [xs[0]]  # cluster near-equal x (float slack) into one column each
        col_of: dict[float, int] = {xs[0]: 0}
        for v in xs[1:]:
            if v - cols[-1] > _X_EPS:
                cols.append(v)
            col_of[v] = len(cols) - 1
        clearances: dict[int, list[tuple[int, float]]] = defaultdict(list)  # right col -> [(left col, min gap px)]
        for level, row in enumerate(self.lay.pos):
            order = sorted(range(len(row)), key=lambda k: row[k])
            for left, right in itertools.pairwise(order):
                cl, cr = col_of[row[left]], col_of[row[right]]
                if cl == cr:
                    continue
                wi = self._label_w(self._ind_at(self.lay.nid[level][left]))
                wj = self._label_w(self._ind_at(self.lay.nid[level][right]))
                clearances[cr].append((cl, (wi + wj) / 2 + self.geom.label_size))
        offset = [0.0] * len(cols)
        for c in range(1, len(cols)):
            offset[c] = offset[c - 1] + (cols[c] - cols[c - 1]) * self.geom.x_unit
            for cl, gap in clearances[c]:
                offset[c] = max(offset[c], offset[cl] + gap)
        return {v: offset[col_of[v]] for v in xs}

    def _content_bounds(self) -> tuple[float, float]:
        """Leftmost / rightmost pixel offset touched by any symbol *or* its centred label stack.

        A label is centred on its symbol and can be wider than it, so it — not the symbol — sets the
        canvas edge. Returns the min/max content edge in ``_px`` offset units (symbol half or label
        half, whichever reaches further from each cell's centre)."""
        los: list[float] = []
        his: list[float] = []
        for level, row in enumerate(self.lay.pos):
            for k, x in enumerate(row):
                extent = max(self.half, self._label_w(self._ind_at(self.lay.nid[level][k])) / 2)
                los.append(self._px[x] - extent)
                his.append(self._px[x] + extent)
        return (min(los), max(his)) if los else (0.0, 0.0)

    def px(self, x: float) -> float:
        # Shift so the leftmost content edge (symbol or overhanging label) sits at gutter + margin, so a
        # wide outermost label is not clipped into the gutter. Symbol-only figures keep _x_lo == -half.
        return self.geom.gen_marker_gutter + self.geom.margin - self._x_lo + self._px[x]

    def py(self, level: int) -> float:
        return self.geom.margin + self.half + level * self.gen_height

    def dimensions(self) -> tuple[float, float]:
        """The drawing's (width, height) in px — the canvas the body is drawn into."""
        nlev = len(self.lay.nid)
        width = self.geom.gen_marker_gutter + self.geom.margin * 2 + (self._x_hi - self._x_lo)
        height = self.geom.margin * 2 + self.geom.symbol_size + max(nlev - 1, 0) * self.gen_height + self.label_band
        return width, height

    def body(self) -> list[str]:
        """The SVG elements (no root ``<svg>``) — markers, matings, descents, symbols, in draw order.

        Ghost links (dashed "same individual" connectors for a duplicated cross-generation partner) are drawn
        before the symbols so the glyphs sit on top of the line ends.
        """
        return [
            *self._gen_markers(),
            *self._matings(),
            *self._routed_matings(),
            *self._descents(),
            *self._childless(),
            *self._founder_sibships(),
            *self._ghost_links(),
            *self._symbols(),
        ]

    def svg(self) -> str:
        width, height = self.dimensions()
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}" height="{_num(height)}" '
            f'viewBox="0 0 {_num(width)} {_num(height)}">',
            *self.body(),
            "</svg>",
        ]
        return "\n".join(out) + "\n"

    def _gen_markers(self) -> list[str]:
        """One Roman-numeral generation marker per row, centred in the reserved left gutter."""
        x = self.geom.gen_marker_gutter / 2
        return [_text(x, self.py(level), _roman(level + 1), _GEN_MARKER_SIZE) for level in range(len(self.lay.nid))]

    # --- connectors -------------------------------------------------------------------------------

    def _matings(self) -> list[str]:
        """Horizontal mating line between each adjacent couple; a double line for consanguinity."""
        out: list[str] = []
        for level in range(len(self.lay.nid)):
            for k in range(self.lay.n[level] - 1):
                kind = self.lay.spouse[level][k]
                if not kind:
                    continue
                y = self.py(level)
                x1 = self.px(self.lay.pos[level][k]) + self.half
                x2 = self.px(self.lay.pos[level][k + 1]) - self.half
                if kind == 2:
                    off = self.geom.double_line_offset / 2
                    out.append(_line(x1, y - off, x2, y - off))
                    out.append(_line(x1, y + off, x2, y + off))
                else:
                    out.append(_line(x1, y, x2, y))
        return out

    def _routed_matings(self) -> list[str]:
        """Orthogonal routed edge for each mating whose partners are not adjacent (layout v2, Stage C).

        A >2-mate individual can keep only two 1-D neighbours, so a third mating's partner sits non-adjacent on
        the same row (``Layout.routed``). The edge leaves each partner's top edge, rises to a clear horizontal
        track *above* the row, and drops to the other partner — so it passes over any symbols between the
        partners without touching one (the track clears every symbol top; the vertical legs sit at the partner
        columns and only meet their own partner at its top edge). Consanguinity doubles the track. Several
        routed edges on one row stagger by ``routed_track_gap`` (ordered by ``(min col, max col)``) so they
        never coincide — deterministic. Descent from a routed mating is not drawn (Stage C routes only
        childless matings; a routed mating with offspring falls back to v1 upstream)."""
        out: list[str] = []
        by_row: dict[int, list[RoutedMating]] = defaultdict(list)
        for rm in self.lay.routed:
            by_row[rm.a[0]].append(rm)
        for level in sorted(by_row):
            ordered = sorted(by_row[level], key=lambda rm: (min(rm.a[1], rm.b[1]), max(rm.a[1], rm.b[1])))
            for track, rm in enumerate(ordered):
                out += self._routed_edge(rm, track)
        return out

    def _routed_edge(self, rm: RoutedMating, track: int) -> list[str]:
        la, ka = rm.a
        lb, kb = rm.b
        xl, xr = sorted((self.px(self.lay.pos[la][ka]), self.px(self.lay.pos[lb][kb])))
        y_top = self.py(la) - self.half  # both partners share the row; leave from the symbol top edge
        track_y = y_top - self.geom.routed_stub - track * self.geom.routed_track_gap

        def path(s: float) -> str:
            # s offsets the whole orthogonal path outward (double line): legs out by s, track up by s.
            return _polyline([(xl - s, y_top), (xl - s, track_y - s), (xr + s, track_y - s), (xr + s, y_top)])

        if rm.consanguineous:
            d = self.geom.double_line_offset / 2
            return [path(d), path(-d)]
        return [path(0.0)]

    def _childless(self) -> list[str]:
        """Bennett childless glyph under a couple with no children: a vertical stub from the mating-line
        midpoint down to a short horizontal bar — one bar for childlessness by choice, two parallel bars
        for infertility. Drawn instead of a descent (the couple has no offspring)."""
        out: list[str] = []
        for level in range(len(self.lay.childless)):
            for k in range(self.lay.n[level] - 1):
                kind = self.lay.childless[level][k]
                if not kind:
                    continue
                y = self.py(level)
                mid_x = (self.px(self.lay.pos[level][k]) + self.px(self.lay.pos[level][k + 1])) / 2
                bar_y = y + self.geom.childless_stub
                half = self.geom.childless_bar
                out.append(_line(mid_x, y, mid_x, bar_y))
                out.append(_line(mid_x - half, bar_y, mid_x + half, bar_y))
                if kind == int(pb.CHILDLESSNESS_INFERTILITY):
                    bar2 = bar_y + self.geom.childless_bar_gap
                    out.append(_line(mid_x - half, bar2, mid_x + half, bar2))
        return out

    def _descents(self) -> list[str]:
        """Descent drop + sibship bar + per-child stubs, grouping children by their parent (couple or lone)."""
        out: list[str] = []
        for level in range(1, len(self.lay.nid)):
            groups: dict[int, list[int]] = {}
            for k in range(self.lay.n[level]):
                pc = self.lay.fam[level][k]
                if pc >= 0:
                    groups.setdefault(pc, []).append(k)
            for pc in sorted(groups):
                out += self._sibship(level, pc, groups[pc])
        return out

    def _sibship(self, level: int, pc: int, cols: list[int]) -> list[str]:
        top = self.py(level) - self.half
        parent_y = self.py(level - 1)
        # fam names the parent column: a couple's left member (spouse!=0), or a lone single parent
        # (spouse==0). The descent drops from the couple midpoint, or from the lone parent's centre.
        if self.lay.spouse[level - 1][pc]:
            mid_x = (self.px(self.lay.pos[level - 1][pc]) + self.px(self.lay.pos[level - 1][pc + 1])) / 2
        else:
            mid_x = self.px(self.lay.pos[level - 1][pc])
        if len(cols) == 1:
            cx = self.px(self.lay.pos[level][cols[0]])
            parent_pos = self.lay.pos[level - 1]
            mid_pos = (parent_pos[pc] + parent_pos[pc + 1]) / 2 if self.lay.spouse[level - 1][pc] else parent_pos[pc]
            if abs(self.lay.pos[level][cols[0]] - mid_pos) < 1e-6:
                return [_line(cx, parent_y, cx, top)]  # child centred under its parents: a straight drop
            # v2 can pull a lone child off its parents' midpoint (e.g. a cousin who must sit beside its
            # mate); route the descent from the mating midpoint down, across, and down to the child.
            bar_y = top - self.geom.sib_stub
            return [_line(mid_x, parent_y, mid_x, bar_y), _line(mid_x, bar_y, cx, bar_y), _line(cx, bar_y, cx, top)]
        bar_y = top - self.geom.sib_stub
        groups = self._child_groups(level, cols)
        attach = [sum(self.px(self.lay.pos[level][k]) for k in g) / len(g) for g in groups]
        if len(groups) == 1:
            return [_line(mid_x, parent_y, attach[0], bar_y), *self._twins(level, groups[0], bar_y, top)]
        out = [_line(mid_x, parent_y, mid_x, bar_y), _line(min(attach), bar_y, max(attach), bar_y)]
        for group, ax in zip(groups, attach, strict=True):
            if len(group) == 1:
                out.append(_line(ax, bar_y, ax, top))
            else:
                out += self._twins(level, group, bar_y, top)
        return out

    def _founder_sibships(self) -> list[str]:
        """A sib bar + implied hanger stub for each sibship whose parent couple is undrawn (Bennett).

        A founder sibship hangs from a short vertical stub rising to a point above the sib bar — no parent
        symbol, no descent drop — signalling siblings via an undrawn couple. Otherwise it is an ordinary
        sibship: a bar across the children's stubs, twins converging as usual.
        """
        out: list[str] = []
        for level, cols in self.lay.founder_sibships:
            out += self._founder_sibship(level, list(cols))
        return out

    def _founder_sibship(self, level: int, cols: list[int]) -> list[str]:
        top = self.py(level) - self.half
        bar_y = top - self.geom.sib_stub
        groups = self._child_groups(level, cols)
        attach = [sum(self.px(self.lay.pos[level][k]) for k in g) / len(g) for g in groups]
        hx = (min(attach) + max(attach)) / 2
        out = [
            _line(min(attach), bar_y, max(attach), bar_y),  # the sibship bar
            _line(hx, bar_y - self.geom.sib_stub, hx, bar_y),  # the implied hanger up to a point (no parents)
        ]
        for group, ax in zip(groups, attach, strict=True):
            if len(group) == 1:
                out.append(_line(ax, bar_y, ax, top))
            else:
                out += self._twins(level, group, bar_y, top)
        return out

    def _child_groups(self, level: int, cols: list[int]) -> list[list[int]]:
        """Split a sibship's columns into consecutive twin runs (one group each) and lone singletons."""
        groups: list[list[int]] = []
        j = 0
        while j < len(cols):
            r = j
            while r + 1 < len(cols) and cols[r + 1] == cols[r] + 1 and self.lay.twins[level][cols[r]]:
                r += 1
            groups.append(cols[j : r + 1])
            j = r + 1
        return groups

    def _twins(self, level: int, members: list[int], bar_y: float, top: float) -> list[str]:
        """Converge a twin group's stubs to one point on the bar; MZ adds a joining bar, ? if unknown."""
        xs = [self.px(self.lay.pos[level][k]) for k in members]
        gmid = sum(xs) / len(xs)
        out = [_line(gmid, bar_y, x, top) for x in xs]
        kind = self.lay.twins[level][members[0]]
        if kind == int(pb.ZYGOSITY_TYPE_MONOZYGOTIC):
            y = bar_y + 0.55 * (top - bar_y)
            lx = gmid + (xs[0] - gmid) * 0.55
            rx = gmid + (xs[-1] - gmid) * 0.55
            out.append(_line(lx, y, rx, y))
        elif kind == int(pb.ZYGOSITY_TYPE_UNKNOWN):
            out.append(_text(gmid, bar_y + 0.5 * (top - bar_y), "?", 14.0))
        return out

    # --- symbols ----------------------------------------------------------------------------------

    def _symbols(self) -> list[str]:
        out: list[str] = []
        for level in range(len(self.lay.nid)):
            for k in range(self.lay.n[level]):
                idx = self.lay.nid[level][k]
                cx, cy = self.px(self.lay.pos[level][k]), self.py(level)
                real = self.lay.ghost_of.get(idx)
                if real is not None:
                    out += self._ghost_symbol(self.p.individuals[real], cx, cy)
                else:
                    out += self._symbol(self.p.individuals[idx], cx, cy)
        return out

    def _ghost_symbol(self, ind: pb.Individual, cx: float, cy: float) -> list[str]:
        """A duplicated individual (cross-generation join): the same shape/affection and id label as the real
        one, but no arrow, annotations, or status marks — those belong to the primary instance. The dashed
        link (``_ghost_links``) ties it back to that instance."""
        affected = pb.CONDITION_STATUS_AFFECTED in {c.status for c in ind.conditions}
        out = [self._shape(ind.gender, cx, cy, _STROKE if affected else "#ffffff")]
        lines, ys = self._label_lines(ind), self._line_ys(ind)
        if lines:
            out.append(_text(cx, cy + self.half + ys[0], lines[0], self.geom.label_size))
        return out

    def _ghost_links(self) -> list[str]:
        """A dashed "same individual" connector from each ghost cell to the real individual's cell."""
        if not self.lay.ghost_of:
            return []
        centre: dict[int, tuple[float, float]] = {}
        for level in range(len(self.lay.nid)):
            for k in range(self.lay.n[level]):
                centre[self.lay.nid[level][k]] = (self.px(self.lay.pos[level][k]), self.py(level))
        out: list[str] = []
        for ghost, real in sorted(self.lay.ghost_of.items()):
            gx, gy = centre[ghost]
            rx, ry = centre[real]
            out.append(
                f'<line x1="{_num(gx)}" y1="{_num(gy)}" x2="{_num(rx)}" y2="{_num(ry)}" '
                f'stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}" stroke-dasharray="4 3"/>'
            )
        return out

    def _symbol(self, ind: pb.Individual, cx: float, cy: float) -> list[str]:
        statuses = {c.status for c in ind.conditions}
        affected = pb.CONDITION_STATUS_AFFECTED in statuses
        fill = _STROKE if affected else "#ffffff"
        out = [self._shape(ind.gender, cx, cy, fill)]
        if not affected and pb.CONDITION_STATUS_UNKNOWN in statuses:
            out.append(_text(cx, cy, "?", 20.0, fill=_STROKE if fill != _STROKE else "#ffffff"))
        if not affected and pb.CONDITION_STATUS_CARRIER in statuses:
            out += self._carrier_glyph(ind, cx, cy)
        if pb.CONDITION_STATUS_PRESYMPTOMATIC in statuses:
            out.append(_line(cx, cy - self.half, cx, cy + self.half))  # vertical line — presymptomatic carrier
        if ind.deceased:
            d = self.half * 1.4
            out.append(_line(cx - d, cy + d, cx + d, cy - d))
        if ind.proband:
            out += self._arrow(cx, cy, label="P")
        elif ind.consultand:
            out += self._arrow(cx, cy, label=None)
        base_y = cy + self.half
        for line, off in zip(self._label_lines(ind), self._line_ys(ind), strict=True):
            out.append(_text(cx, base_y + off, line, self.geom.label_size))
        return out

    def _condition_legend(self) -> list[str]:
        """Ordered distinct condition names in the pedigree — the key for which region a carrier fills.

        Phenotype legend labels come first (their drawn order), then any remaining condition names by first
        appearance. A condition's index here selects its fill region, so two carriers of *different* named
        conditions get *different* halves (a compound het reads as opposite halves), consistently pedigree-wide.
        """
        order: list[str] = []
        seen: set[str] = set()
        for label in self.p.labels:
            if label.kind == pb.LABEL_KIND_PHENOTYPE and label.text and label.text not in seen:
                seen.add(label.text)
                order.append(label.text)
        for ind in self.p.individuals:
            for c in ind.conditions:
                if c.name and c.name not in seen:
                    seen.add(c.name)
                    order.append(c.name)
        return order

    def _carrier_glyph(self, ind: pb.Individual, cx: float, cy: float) -> list[str]:
        """The carrier mark. Under ``CarrierStyle.INHERITANCE_GLYPH`` (default, matching existing literature) an
        X-linked carrier is a central dot and everything else a region fill; under ``PARTITION_FILL`` (NSGC 2022,
        dot retired) every carrier is a region fill regardless of inheritance."""
        carriers = [c for c in ind.conditions if c.status == pb.CONDITION_STATUS_CARRIER]
        if self.geom.carrier_style is CarrierStyle.INHERITANCE_GLYPH:
            x_linked = {pb.INHERITANCE_X_LINKED_RECESSIVE, pb.INHERITANCE_X_LINKED_DOMINANT}
            if {c.inheritance for c in carriers} & x_linked:
                return [f'<circle cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(self.half * 0.26)}" fill="{_STROKE}"/>']
        return self._carrier_fill(ind, cx, cy, carriers)

    def _carrier_fill(self, ind: pb.Individual, cx: float, cy: float, carriers: list[pb.Condition]) -> list[str]:
        """Shade the region(s) keyed to the carrier's condition, clipped to the symbol's shape.

        The filled region is the carried condition's index in the pedigree legend, so two carriers of
        different named conditions fill different halves (a compound het reads as opposite halves) while a
        single or unnamed carrier is the plain left half. The shape geometry is reused as a clipPath, so a
        region rectangle becomes a half-disc / half-square / triangle with no per-shape math; the same-colour
        fill over the black outline leaves the border crisp.
        """
        n = len(self._legend)
        indices = sorted({self._legend.index(c.name) for c in carriers if c.name and c.name in self._legend})
        if not indices or n > 4:
            indices, slots = [0], 2  # unnamed / unkeyable / too many conditions -> plain left half
        else:
            slots = 2 if n <= 2 else 4
        clip_id = f"{self.id_prefix}c{ind.generation}-{ind.index}"
        clip = f'<clipPath id="{clip_id}">{self._shape(ind.gender, cx, cy, "none")}</clipPath>'
        return [clip, *(self._region_rect(cx, cy, i, slots, clip_id) for i in indices)]

    def _region_rect(self, cx: float, cy: float, index: int, slots: int, clip_id: str) -> str:
        """A fill rectangle for region ``index`` of a ``slots``-way split (2 = left/right halves, 4 = quadrants
        TL/TR/BL/BR), clipped to the symbol shape."""
        h = self.half
        if slots == 2:
            rx, ry, rw, rh = (cx - h if index == 0 else cx), cy - h, h, 2 * h
        else:
            rx = cx - h if index in (0, 2) else cx
            ry = cy - h if index in (0, 1) else cy
            rw = rh = h
        return (
            f'<rect x="{_num(rx)}" y="{_num(ry)}" width="{_num(rw)}" height="{_num(rh)}" '
            f'fill="{_STROKE}" clip-path="url(#{clip_id})"/>'
        )

    def _shape(self, gender: pb.Gender, cx: float, cy: float, fill: str) -> str:
        h = self.half
        attrs = f'fill="{fill}" stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}"'
        if gender == pb.GENDER_MAN:
            return f'<rect x="{_num(cx - h)}" y="{_num(cy - h)}" width="{_num(2 * h)}" height="{_num(2 * h)}" {attrs}/>'
        if gender == pb.GENDER_WOMAN:
            return f'<circle cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(h)}" {attrs}/>'
        pts = f"{_num(cx)},{_num(cy - h)} {_num(cx + h)},{_num(cy)} {_num(cx)},{_num(cy + h)} {_num(cx - h)},{_num(cy)}"
        return f'<polygon points="{pts}" {attrs}/>'

    def _arrow(self, cx: float, cy: float, label: str | None) -> list[str]:
        """Proband/consultand arrow into the symbol's lower-left corner; ``P`` label for a proband."""
        s = self.geom.symbol_size
        hx, hy = cx - self.half, cy + self.half  # head at the lower-left corner
        ux, uy = 1 / math.sqrt(2), -1 / math.sqrt(2)  # pointing up-right, toward the symbol
        tx, ty = hx - ux * s, hy - uy * s
        out = [_line(tx, ty, hx, hy)]
        for ang in (math.radians(150), math.radians(-150)):
            ca, sa = math.cos(ang), math.sin(ang)
            out.append(_line(hx, hy, hx + 9 * (ux * ca - uy * sa), hy + 9 * (ux * sa + uy * ca)))
        if label:
            out.append(_text(tx - 8, ty + 4, label, 15.0))
        return out


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


def _roman(n: int) -> str:
    """Roman numeral for a positive generation number ``n`` (deterministic; ``n`` is small)."""
    if n < 1:
        raise ValueError(f"generation number must be >= 1, got {n}")
    out: list[str] = []
    for value, sym in _ROMAN:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def _line(x1: float, y1: float, x2: float, y2: float) -> str:
    return (
        f'<line x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}" '
        f'stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}"/>'
    )


def _polyline(points: list[tuple[float, float]]) -> str:
    pts = " ".join(f"{_num(x)},{_num(y)}" for x, y in points)
    return f'<polyline points="{pts}" fill="none" stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}"/>'


def _text(x: float, y: float, s: str, size: float, fill: str = _STROKE) -> str:
    return (
        f'<text x="{_num(x)}" y="{_num(y)}" font-family="{_FONT}" font-size="{_num(size)}" '
        f'fill="{fill}" text-anchor="middle" dominant-baseline="central">{s}</text>'
    )
