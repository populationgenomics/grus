"""Drawing: a ``Layout`` grid -> a Bennett-standard SVG pedigree (docs/design/renderer.md).

Reads only the per-level arrays (the geometry seam) plus each individual's symbol attributes from the
IR. Emits deterministic bytes — no randomness, no timestamps, fixed number formatting — so goldens are
stable. Symbols: square (man) / circle (woman) / diamond (nonbinary or unknown); filled by an affected
condition, with a carrier dot, presymptomatic vertical line, deceased slash, and proband/consultand
arrow. Connectors: mating line (doubled for consanguinity; a lone single parent has none), descent +
sibship bar with per-child stubs, a founder sibship's implied hanger stub (a bar with no parents, for
siblings via an undrawn couple), and twin convergence (MZ joining bar). A generation marker (Roman
numeral) is drawn once per row in a reserved left gutter.

**Document structure** (docs/design/svg-output.md). Every drawn element belongs to a group that names the IR
fact it draws, so a consumer can select and restyle parts without reading coordinates:

* The root ``<svg>`` (or, in a composed figure, each tile's nested ``<svg>``) is ``class="pedigree"`` with
  ``data-title`` and ``data-conditions``, a JSON array of the pedigree's condition names in legend order —
  the same order that keys a carrier's fill region — with ``""`` appended when any individual has an
  unnamed condition, so every condition has an index. A deferred pedigree's placeholder is ``pedigree
  deferred``.
* ``<g class="individual …" id="{prefix}ind-{position}">`` per drawn cell: ``data-position`` (``"II-3"``),
  ``data-generation``, ``data-index``, ``data-gender`` (man / woman / nonbinary / unknown),
  ``data-external-id`` when set, and one ``data-condition-{i}`` per condition whose value is its status
  (affected / carrier / presymptomatic / unknown / …). State classes mirror the IR: ``affected``, ``carrier``,
  ``presymptomatic``, ``unknown``, ``deceased``, ``proband``, ``consultand``. A cross-generation duplicate is
  ``individual ghost`` with id ``{prefix}ghost-{position}`` and the same data attributes. Parts, in draw
  order: ``backing`` (the shape, white, no stroke), ``fill`` (status paint clipped to the shape, with
  ``data-condition`` naming the condition it paints), ``symbol`` (the shape as outline only), ``mark …``
  (``deceased``, ``presymptomatic``, ``unknown``, ``carrier`` dot, ``proband`` / ``consultand`` arrow group),
  ``label`` (one ``<text>`` per line), then ``hit`` — an invisible ``pointer-events="all"`` rectangle over
  the symbol and its reserved label box, the one element a consumer needs for hover and click.
* ``<g class="mating …" data-partners="I-1 I-2">`` per couple, with ``consanguineous``, ``routed``,
  ``childless-by-choice`` / ``childless-infertility`` as classes; holds the line(s), the childless glyph
  and a wide invisible ``hit`` stroke. ``<g class="sibship" data-parents=… data-children=…>`` per descent
  (``sibship founder`` for a parentless hanger). ``<g class="ghost-link" data-position=…>`` per dashed
  same-individual connector. ``<g class="generation" data-generation=…>`` per Roman-numeral marker.
* ``id_prefix`` goes in front of every id and id reference (clip paths included). A composed figure
  prefixes its tiles ``p0-``, ``p1-``, … under the caller's prefix; a caller inlining several figures on
  one page passes a distinct prefix per figure, since inline SVG shares the page's id space.

Appearance is set only through presentation attributes (never ``style``), so any consumer stylesheet rule
overrides it.
"""

from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict

from grus.models import pedigree_pb2 as pb
from grus.render import _geometry, _layout, _layout2

_X_EPS = 1e-9  # float slack when clustering near-equal layout-x into one column

_STROKE = "#000000"
_WIDTH = 2.0
_FONT = "sans-serif"
_GEN_MARKER_SIZE = 16.0
_SET_GAP = 28.0  # vertical gap between stacked pedigrees in a figure render
_TITLE_SIZE = 15.0  # family/panel title above each pedigree tile
_TITLE_GAP = 6.0  # gap between a title and its pedigree
_HIT_STROKE = 12.0  # width of the invisible pointer target laid over a mating line

# One stacked pedigree in a figure render: (title, width, height, body-elements, root-attributes).
_Tile = tuple[str, float, float, list[str], str]


def render_svg(p: pb.Pedigree, geometry: _geometry.Geometry | None = None, *, id_prefix: str = "") -> str:
    """Validate, lay out, and draw ``p``; return a complete, deterministic SVG document string."""
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    lay = _layout2.layout(p, geom)
    return _Draw(p, lay, geom, id_prefix=id_prefix).svg()


def render_set_svg(
    pedigree_set: pb.PedigreeSet, geometry: _geometry.Geometry | None = None, *, id_prefix: str = ""
) -> str:
    """Render a whole figure's ``PedigreeSet`` as one SVG (docs/design/renderer.md).

    Each pedigree is laid out and drawn exactly as ``render_svg`` does, then the tiles are stacked
    vertically and titled by their display label (``_display_title`` — the FAMILY label if any, else the
    first). A pedigree the tier-1 layout defers becomes a labelled placeholder box so the rest of the
    figure still renders. An empty set yields a minimal empty canvas. ``id_prefix`` namespaces every id in
    the document (each tile adds its own ``p{n}-`` under it) for a page that inlines several figures.
    """
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    tiles: list[_Tile] = []
    for ped in pedigree_set.pedigrees:
        title = _display_title(ped)
        try:
            draw = _Draw(ped, _layout2.layout(ped, geom), geom, id_prefix=f"{id_prefix}p{len(tiles)}-")
            width, height = draw.dimensions()
            tiles.append((title, width, height, draw.body(), draw.root_attrs()))
        except _layout.DeferredFeatureError as deferred:
            tiles.append(_placeholder_tile(title, str(deferred)))
    return _compose_tiles(tiles, geom)


def render_svgs(
    pedigree_set: pb.PedigreeSet, geometry: _geometry.Geometry | None = None, *, id_prefix: str = ""
) -> list[tuple[str, str]]:
    """Render each pedigree in a set to its **own** standalone SVG document — one per family.

    Unlike ``render_set_svg`` (which stacks the families into a single composed canvas), this returns a
    ``(title, svg)`` per pedigree so a caller can present them separately (the review UI's render carousel).
    ``title`` is the display label (``_display_title``); a family the tier-1 layout defers becomes a labelled
    "deferred" placeholder SVG rather than raising, mirroring ``render_set_svg`` — so the list always has one
    entry per pedigree, each a complete document.
    """
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    out: list[tuple[str, str]] = []
    for ped in pedigree_set.pedigrees:
        title = _display_title(ped)
        try:
            svg = _Draw(ped, _layout2.layout(ped, geom), geom, id_prefix=id_prefix).svg()
        except _layout.DeferredFeatureError as deferred:
            _, width, height, body, attrs = _placeholder_tile(title, str(deferred))
            svg = _svg_root(width, height, body, attrs)
        out.append((title, svg))
    return out


def _display_title(ped: pb.Pedigree) -> str:
    """The label to show above a pedigree tile: the FAMILY label if present, else the first label."""
    for label in ped.labels:
        if label.kind == pb.LABEL_KIND_FAMILY:
            return label.text
    return ped.labels[0].text if ped.labels else ""


def _compose_tiles(tiles: list[_Tile], geom: _geometry.Geometry) -> str:
    """Stack tiles vertically into one SVG document, each centred under its title."""
    margin = geom.margin
    if not tiles:
        side = margin * 2
        return _svg_root(side, side, [])
    content_w = max(w for _, w, _, _, _ in tiles)
    body: list[str] = []
    y = margin
    for title, w, h, tile_body, attrs in tiles:
        if title:
            body.append(_text(margin + content_w / 2, y + _TITLE_SIZE / 2, _escape(title), _TITLE_SIZE, cls="title"))
            y += _TITLE_SIZE + _TITLE_GAP
        x = margin + (content_w - w) / 2  # centre a narrower pedigree in the figure column
        body.append(
            f'<svg x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
            f'viewBox="0 0 {_num(w)} {_num(h)}" {attrs}>'
        )
        body += tile_body
        body.append("</svg>")
        y += h + _SET_GAP
    return _svg_root(margin * 2 + content_w, y - _SET_GAP + margin, body)


def _svg_root(width: float, height: float, body: list[str], attrs: str = "") -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}" height="{_num(height)}" '
        f'viewBox="0 0 {_num(width)} {_num(height)}"{" " + attrs if attrs else ""}>',
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
    return (title, width, height, body, f'class="pedigree deferred" data-title="{_attr(title)}"')


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


def _attr(s: str) -> str:
    """Escape ``s`` for a double-quoted attribute value."""
    return _escape(s).replace('"', "&quot;")


def _position(ind: pb.Individual) -> str:
    """The drawn position id, ``"II-3"`` — the individual's identity in the IR and in the document."""
    return f"{_roman(ind.generation)}-{ind.index}"


def _enum_word(enum: object, value: int, prefix: str) -> str:
    """``GENDER_WOMAN`` -> ``woman``: an enum value as the lower-case word a data attribute carries."""
    return enum.Name(value).removeprefix(prefix).lower()  # type: ignore[attr-defined]


def _open_g(classes: list[str], attrs: dict[str, str]) -> str:
    parts = [f'class="{" ".join(classes)}"'] + [f'{k}="{_attr(v)}"' for k, v in attrs.items()]
    return f"<g {' '.join(parts)}>"


def _num(v: float) -> str:
    """Format a coordinate compactly and deterministically (3 dp, trailing zeros stripped, no -0)."""
    r = round(v, 3)
    if r == 0:
        r = 0.0
    return f"{r:.3f}".rstrip("0").rstrip(".")


def _label_lines(ind: pb.Individual) -> list[str]:
    """Label stack for ``ind``: the as-drawn position id first, then each annotation's text.

    Line 1 is the reconstructed position ``"II-2"`` (Roman ``generation`` + ``index``); then each
    ``Annotation``'s verbatim text. Blanks and duplicates are dropped (first occurrence wins).
    """
    out: list[str] = []
    lines = [_position(ind)]
    lines += [a.text for a in ind.annotations]
    for line in lines:
        if line and line not in out:
            out.append(line)
    return out


class _Draw:
    """Holds the pedigree, its layout, and geometry; emits the SVG body once."""

    def __init__(self, p: pb.Pedigree, lay: _layout.Layout, geom: _geometry.Geometry, id_prefix: str = "") -> None:
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
        # The minimum label box (docs/design/svg-output.md) is a floor under the band, as under each width.
        self.label_band = max(self.label_band, geom.label_box_height * geom.label_size)
        self.gen_height = max(geom.gen_height, geom.symbol_size + self.label_band + geom.label_gap + geom.sib_stub)
        self._legend = self._condition_legend()  # ordered condition names -> carrier fill region (which half)
        # The data-attribute legend: the fill legend plus a slot for the unnamed sole condition, so every
        # condition an individual has maps to an index in `data-conditions`.
        unnamed = any(not c.name for ind in p.individuals for c in ind.conditions)
        self._data_legend = [*self._legend, *([""] if unnamed else [])]
        self._px = self._build_px_map()
        self._x_lo, self._x_hi = self._content_bounds()

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
        for line in _label_lines(ind):
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
        """Estimated pixel width of ``ind``'s widest label line (conservative — text is unmeasurable here).

        Floored by the minimum label box width, so a consumer may substitute label text up to that wide.
        """
        estimate = 0.6 * self.geom.label_size * max((len(line) for line in _label_lines(ind)), default=1)
        return max(estimate, self.geom.label_box_width * self.geom.label_size)

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
        half, whichever reaches further from each cell's centre).
        """
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
            *self._founder_sibships(),
            *self._ghost_links(),
            *self._symbols(),
        ]

    def root_attrs(self) -> str:
        """The ``pedigree`` group's attributes, for the root ``<svg>`` or a composed figure's tile."""
        legend = json.dumps(self._data_legend, ensure_ascii=False)
        return f'class="pedigree" data-title="{_attr(_display_title(self.p))}" data-conditions="{_attr(legend)}"'

    def svg(self) -> str:
        width, height = self.dimensions()
        return _svg_root(width, height, self.body(), self.root_attrs())

    def _gen_markers(self) -> list[str]:
        """One Roman-numeral generation marker per row, centred in the reserved left gutter."""
        x = self.geom.gen_marker_gutter / 2
        out: list[str] = []
        for level in range(len(self.lay.nid)):
            out.append(_open_g(["generation"], {"data-generation": str(level + 1)}))
            out.append(_text(x, self.py(level), _roman(level + 1), _GEN_MARKER_SIZE))
            out.append("</g>")
        return out

    def _cell_position(self, level: int, k: int) -> str:
        """The position id of the individual drawn in cell ``(level, k)`` (a ghost resolves to its real)."""
        return _position(self._ind_at(self.lay.nid[level][k]))

    # --- connectors -------------------------------------------------------------------------------

    def _matings(self) -> list[str]:
        """One ``mating`` group per adjacent couple: the line(s), any childless glyph, and a hit stroke."""
        out: list[str] = []
        for level in range(len(self.lay.nid)):
            for k in range(self.lay.n[level] - 1):
                kind = self.lay.spouse[level][k]
                if not kind:
                    continue
                childless = self.lay.childless[level][k] if level < len(self.lay.childless) else 0
                classes = ["mating"]
                if kind == 2:
                    classes.append("consanguineous")
                if childless == int(pb.CHILDLESSNESS_BY_CHOICE):
                    classes.append("childless-by-choice")
                elif childless == int(pb.CHILDLESSNESS_INFERTILITY):
                    classes.append("childless-infertility")
                partners = f"{self._cell_position(level, k)} {self._cell_position(level, k + 1)}"
                out.append(_open_g(classes, {"data-partners": partners}))
                y = self.py(level)
                x1 = self.px(self.lay.pos[level][k]) + self.half
                x2 = self.px(self.lay.pos[level][k + 1]) - self.half
                if kind == 2:
                    off = self.geom.double_line_offset / 2
                    out.append(_line(x1, y - off, x2, y - off))
                    out.append(_line(x1, y + off, x2, y + off))
                else:
                    out.append(_line(x1, y, x2, y))
                if childless:
                    out += self._childless_glyph(level, k, childless)
                out.append(_hit_line(x1, y, x2, y))
                out.append("</g>")
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
        childless matings; a routed mating with offspring falls back to v1 upstream).
        """
        out: list[str] = []
        by_row: dict[int, list[_layout.RoutedMating]] = defaultdict(list)
        for rm in self.lay.routed:
            by_row[rm.a[0]].append(rm)
        for level in sorted(by_row):
            ordered = sorted(by_row[level], key=lambda rm: (min(rm.a[1], rm.b[1]), max(rm.a[1], rm.b[1])))
            for track, rm in enumerate(ordered):
                classes = ["mating", "routed"] + (["consanguineous"] if rm.consanguineous else [])
                partners = f"{self._cell_position(*rm.a)} {self._cell_position(*rm.b)}"
                out.append(_open_g(classes, {"data-partners": partners}))
                out += self._routed_edge(rm, track)
                out.append("</g>")
        return out

    def _routed_edge(self, rm: _layout.RoutedMating, track: int) -> list[str]:
        la, ka = rm.a
        lb, kb = rm.b
        xl, xr = sorted((self.px(self.lay.pos[la][ka]), self.px(self.lay.pos[lb][kb])))
        y_top = self.py(la) - self.half  # both partners share the row; leave from the symbol top edge
        track_y = y_top - self.geom.routed_stub - track * self.geom.routed_track_gap

        def path(s: float) -> str:
            # s offsets the whole orthogonal path outward (double line): legs out by s, track up by s.
            return _polyline([(xl - s, y_top), (xl - s, track_y - s), (xr + s, track_y - s), (xr + s, y_top)])

        hit = _hit_polyline([(xl, y_top), (xl, track_y), (xr, track_y), (xr, y_top)])
        if rm.consanguineous:
            d = self.geom.double_line_offset / 2
            return [path(d), path(-d), hit]
        return [path(0.0), hit]

    def _childless_glyph(self, level: int, k: int, kind: int) -> list[str]:
        """Bennett childless glyph under the couple at cells ``k, k+1``.

        A vertical stub from the mating-line midpoint down to a short horizontal bar — one bar for
        childlessness by choice, two parallel bars for infertility. Drawn instead of a descent (the couple
        has no offspring).
        """
        y = self.py(level)
        mid_x = (self.px(self.lay.pos[level][k]) + self.px(self.lay.pos[level][k + 1])) / 2
        bar_y = y + self.geom.childless_stub
        half = self.geom.childless_bar
        out = [_line(mid_x, y, mid_x, bar_y), _line(mid_x - half, bar_y, mid_x + half, bar_y)]
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
                parents = [self._cell_position(level - 1, pc)]
                if self.lay.spouse[level - 1][pc]:
                    parents.append(self._cell_position(level - 1, pc + 1))
                children = " ".join(self._cell_position(level, k) for k in groups[pc])
                out.append(_open_g(["sibship"], {"data-parents": " ".join(parents), "data-children": children}))
                out += self._sibship(level, pc, groups[pc])
                out.append("</g>")
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
            children = " ".join(self._cell_position(level, k) for k in cols)
            out.append(_open_g(["sibship", "founder"], {"data-children": children}))
            out += self._founder_sibship(level, list(cols))
            out.append("</g>")
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
        """A duplicated individual (cross-generation join).

        The same shape/affection and id label as the real one, but no arrow, annotations, or status marks —
        those belong to the primary instance. The dashed link (``_ghost_links``) ties it back to that instance.
        """
        out = [self._open_individual(ind, ghost=True)]
        out.append(self._shape(ind.gender, cx, cy, "#ffffff", stroke=False, cls="backing"))
        out += self._affected_fill(ind, cx, cy)
        out.append(self._shape(ind.gender, cx, cy, "none", cls="symbol"))
        lines, ys = _label_lines(ind), self._line_ys(ind)
        if lines:
            out.append(_text(cx, cy + self.half + ys[0], lines[0], self.geom.label_size, cls="label"))
        out.append(self._hit_rect(ind, cx, cy))
        out.append("</g>")
        return out

    def _open_individual(self, ind: pb.Individual, *, ghost: bool) -> str:
        """The ``individual`` group's opening tag: state classes and the data attributes a consumer selects on."""
        statuses = {c.status for c in ind.conditions}
        classes = ["individual"] + (["ghost"] if ghost else [])
        for status, word in (
            (pb.CONDITION_STATUS_AFFECTED, "affected"),
            (pb.CONDITION_STATUS_CARRIER, "carrier"),
            (pb.CONDITION_STATUS_PRESYMPTOMATIC, "presymptomatic"),
            (pb.CONDITION_STATUS_UNKNOWN, "unknown"),
        ):
            if status in statuses:
                classes.append(word)
        for flag, word in ((ind.deceased, "deceased"), (ind.proband, "proband"), (ind.consultand, "consultand")):
            if flag:
                classes.append(word)
        position = _position(ind)
        attrs = {
            "id": f"{self.id_prefix}{'ghost' if ghost else 'ind'}-{position}",
            "data-position": position,
            "data-generation": str(ind.generation),
            "data-index": str(ind.index),
            "data-gender": _enum_word(pb.Gender, ind.gender, "GENDER_"),
        }
        if ind.HasField("external_id"):
            attrs["data-external-id"] = ind.external_id
        for c in ind.conditions:
            attrs[f"data-condition-{self._data_legend.index(c.name)}"] = _enum_word(
                pb.ConditionStatus, c.status, "CONDITION_STATUS_"
            )
        return _open_g(classes, attrs)

    def _affected_fill(self, ind: pb.Individual, cx: float, cy: float) -> list[str]:
        """The solid ``fill`` part of an affected individual: the whole shape, naming the first affected condition."""
        affected = [c for c in ind.conditions if c.status == pb.CONDITION_STATUS_AFFECTED]
        if not affected:
            return []
        idx = self._data_legend.index(affected[0].name)
        return [self._shape(ind.gender, cx, cy, _STROKE, stroke=False, cls="fill", extra=f'data-condition="{idx}"')]

    def _hit_rect(self, ind: pb.Individual, cx: float, cy: float) -> str:
        """The invisible pointer target: the symbol plus its reserved label box (docs/design/svg-output.md)."""
        w = max(self.geom.symbol_size, self._label_w(ind))
        h = self.geom.symbol_size + self.label_band
        return (
            f'<rect class="hit" x="{_num(cx - w / 2)}" y="{_num(cy - self.half)}" width="{_num(w)}" '
            f'height="{_num(h)}" fill="none" pointer-events="all"/>'
        )

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
            out.append(_open_g(["ghost-link"], {"data-position": _position(self.p.individuals[real])}))
            out.append(
                f'<line x1="{_num(gx)}" y1="{_num(gy)}" x2="{_num(rx)}" y2="{_num(ry)}" '
                f'stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}" stroke-dasharray="4 3"/>'
            )
            out.append("</g>")
        return out

    def _symbol(self, ind: pb.Individual, cx: float, cy: float) -> list[str]:
        """One ``individual`` group: backing, status fill, outline, marks, label lines, hit — in that order."""
        statuses = {c.status for c in ind.conditions}
        affected = pb.CONDITION_STATUS_AFFECTED in statuses
        out = [self._open_individual(ind, ghost=False)]
        out.append(self._shape(ind.gender, cx, cy, "#ffffff", stroke=False, cls="backing"))
        out += self._affected_fill(ind, cx, cy)
        carrier_fills, carrier_marks = (
            self._carrier_glyph(ind, cx, cy) if not affected and pb.CONDITION_STATUS_CARRIER in statuses else ([], [])
        )
        out += carrier_fills
        out.append(self._shape(ind.gender, cx, cy, "none", cls="symbol"))
        out += carrier_marks
        if not affected and pb.CONDITION_STATUS_UNKNOWN in statuses:
            out.append(_text(cx, cy, "?", 20.0, cls="mark unknown"))
        if pb.CONDITION_STATUS_PRESYMPTOMATIC in statuses:
            out.append(_line(cx, cy - self.half, cx, cy + self.half, cls="mark presymptomatic"))
        if ind.deceased:
            d = self.half * 1.4
            out.append(_line(cx - d, cy + d, cx + d, cy - d, cls="mark deceased"))
        if ind.proband:
            out += ['<g class="mark proband">', *self._arrow(cx, cy, label="P"), "</g>"]
        elif ind.consultand:
            out += ['<g class="mark consultand">', *self._arrow(cx, cy, label=None), "</g>"]
        base_y = cy + self.half
        for line, off in zip(_label_lines(ind), self._line_ys(ind), strict=True):
            out.append(_text(cx, base_y + off, line, self.geom.label_size, cls="label"))
        out.append(self._hit_rect(ind, cx, cy))
        out.append("</g>")
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

    def _carrier_glyph(self, ind: pb.Individual, cx: float, cy: float) -> tuple[list[str], list[str]]:
        """The carrier glyph as ``(fill parts under the outline, mark parts over it)``.

        Under ``CarrierStyle.INHERITANCE_GLYPH`` (default, matching existing literature) an X-linked carrier is
        a central dot (a mark) and everything else a region fill; under ``PARTITION_FILL`` (NSGC 2022, dot
        retired) every carrier is a region fill regardless of inheritance.
        """
        carriers = [c for c in ind.conditions if c.status == pb.CONDITION_STATUS_CARRIER]
        if self.geom.carrier_style is _geometry.CarrierStyle.INHERITANCE_GLYPH:
            x_linked = {pb.INHERITANCE_X_LINKED_RECESSIVE, pb.INHERITANCE_X_LINKED_DOMINANT}
            if {c.inheritance for c in carriers} & x_linked:
                dot = (
                    f'<circle class="mark carrier" cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(self.half * 0.26)}" '
                    f'fill="{_STROKE}"/>'
                )
                return [], [dot]
        return self._carrier_fill(ind, cx, cy, carriers), []

    def _carrier_fill(self, ind: pb.Individual, cx: float, cy: float, carriers: list[pb.Condition]) -> list[str]:
        """Shade the region(s) keyed to the carrier's condition, clipped to the symbol's shape.

        The filled region is the carried condition's index in the pedigree legend, so two carriers of
        different named conditions fill different halves (a compound het reads as opposite halves) while a
        single or unnamed carrier is the plain left half. The shape geometry is reused as a clipPath, so a
        region rectangle becomes a half-disc / half-square / triangle with no per-shape math; the same-colour
        fill over the black outline leaves the border crisp.
        """
        n = len(self._legend)
        keyed = {self._legend.index(c.name): c for c in carriers if c.name and c.name in self._legend}
        if not keyed or n > 4:
            # unnamed / unkeyable / too many conditions -> plain left half, naming the first carried condition
            regions = [(0, self._data_legend.index(carriers[0].name))]
            slots = 2
        else:
            regions = [(i, self._data_legend.index(keyed[i].name)) for i in sorted(keyed)]
            slots = 2 if n <= 2 else 4
        clip_id = f"{self.id_prefix}clip-{_position(ind)}"
        clip = f'<clipPath id="{clip_id}">{self._shape(ind.gender, cx, cy, "none", stroke=False)}</clipPath>'
        return [clip, *(self._region_rect(cx, cy, i, slots, clip_id, cond) for i, cond in regions)]

    def _region_rect(self, cx: float, cy: float, index: int, slots: int, clip_id: str, condition: int) -> str:
        """A ``fill`` rectangle for region ``index`` of a ``slots``-way split, clipped to the symbol shape.

        ``slots`` is 2 (left/right halves) or 4 (quadrants TL/TR/BL/BR); ``condition`` is the data-legend
        index of the condition the region paints.
        """
        h = self.half
        if slots == 2:
            rx, ry, rw, rh = (cx - h if index == 0 else cx), cy - h, h, 2 * h
        else:
            rx = cx - h if index in (0, 2) else cx
            ry = cy - h if index in (0, 1) else cy
            rw = rh = h
        return (
            f'<rect class="fill" data-condition="{condition}" x="{_num(rx)}" y="{_num(ry)}" width="{_num(rw)}" '
            f'height="{_num(rh)}" fill="{_STROKE}" clip-path="url(#{clip_id})"/>'
        )

    def _shape(
        self, gender: pb.Gender, cx: float, cy: float, fill: str, *, stroke: bool = True, cls: str = "", extra: str = ""
    ) -> str:
        """The gender shape at ``(cx, cy)``: ``fill`` paint, optionally stroked, with a part class."""
        h = self.half
        head = "".join(f"{part} " for part in (f'class="{cls}"' if cls else "", extra) if part)
        paint = f'fill="{fill}"' + (f' stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}"' if stroke else "")
        if gender == pb.GENDER_MAN:
            geometry = f'x="{_num(cx - h)}" y="{_num(cy - h)}" width="{_num(2 * h)}" height="{_num(2 * h)}"'
            return f"<rect {head}{geometry} {paint}/>"
        if gender == pb.GENDER_WOMAN:
            return f'<circle {head}cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(h)}" {paint}/>'
        pts = f"{_num(cx)},{_num(cy - h)} {_num(cx + h)},{_num(cy)} {_num(cx)},{_num(cy + h)} {_num(cx - h)},{_num(cy)}"
        return f'<polygon {head}points="{pts}" {paint}/>'

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


def _cls(cls: str) -> str:
    return f'class="{cls}" ' if cls else ""


def _line(x1: float, y1: float, x2: float, y2: float, cls: str = "") -> str:
    return (
        f'<line {_cls(cls)}x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}" '
        f'stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}"/>'
    )


def _hit_line(x1: float, y1: float, x2: float, y2: float) -> str:
    """An invisible wide stroke over a line, so a consumer can point at it (``pointer-events="stroke"``)."""
    return (
        f'<line class="hit" x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}" '
        f'stroke="none" stroke-width="{_num(_HIT_STROKE)}" pointer-events="stroke"/>'
    )


def _polyline(points: list[tuple[float, float]]) -> str:
    pts = " ".join(f"{_num(x)},{_num(y)}" for x, y in points)
    return f'<polyline points="{pts}" fill="none" stroke="{_STROKE}" stroke-width="{_num(_WIDTH)}"/>'


def _hit_polyline(points: list[tuple[float, float]]) -> str:
    pts = " ".join(f"{_num(x)},{_num(y)}" for x, y in points)
    return (
        f'<polyline class="hit" points="{pts}" fill="none" stroke="none" stroke-width="{_num(_HIT_STROKE)}" '
        f'pointer-events="stroke"/>'
    )


def _text(x: float, y: float, s: str, size: float, fill: str = _STROKE, cls: str = "") -> str:
    return (
        f'<text {_cls(cls)}x="{_num(x)}" y="{_num(y)}" font-family="{_FONT}" font-size="{_num(size)}" '
        f'fill="{fill}" text-anchor="middle" dominant-baseline="central">{s}</text>'
    )
