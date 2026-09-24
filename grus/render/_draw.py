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
  (affected / carrier / presymptomatic / unknown / …; same-named conditions share a slot and their statuses
  join space-separated, so select one with ``[data-condition-0~="carrier"]``). State classes mirror the IR,
  not the subset of marks the drawer chose: ``affected``, ``carrier``, ``presymptomatic``, ``unknown``,
  ``deceased``, ``proband``, ``consultand``. A cross-generation duplicate is ``individual ghost`` with the
  same classes and data attributes as its real cell and id ``{prefix}ghost-{position}`` (``-2``, ``-3``, …
  when one individual is ghosted more than once), so ``[data-position]`` lights both and
  ``.individual:not(.ghost)`` counts people.
  Parts, in draw order: ``backing`` (the shape, white, no stroke), ``fill`` (status paint inside the shape —
  the whole shape when affected, a legend-keyed region or the X-linked ``dot`` when a carrier — each with
  ``data-condition`` naming the condition it paints), ``symbol`` (the shape as outline only), ``mark …``
  (``deceased``, ``presymptomatic``, ``unknown``, ``proband`` / ``consultand`` arrow group), ``label`` (one
  ``<text>`` per line), then ``hit`` — an invisible ``pointer-events="all"`` rectangle over the symbol, its
  reserved label box and the arrow when one is drawn: the one element a consumer needs for hover and click.
* ``<g class="mating …" data-partners="I-1 I-2">`` per couple, with ``consanguineous``, ``routed``,
  ``childless-by-choice`` / ``childless-infertility`` as classes; holds the line(s), the childless glyph
  and a wide invisible ``hit`` stroke. ``<g class="sibship" data-parents=… data-children=…>`` per descent
  (``sibship founder`` for a parentless hanger); a descent drawn across rows — children more than one
  generation below their parents — is one group holding the line through every row it crosses.
  ``<g class="ghost-link" data-position=…>`` per dashed same-individual connector.
  ``<g class="generation" data-generation=…>`` per Roman-numeral marker, numbered by IR generation.
* ``id_prefix`` goes in front of every id and id reference (clip paths included); it must be empty or an
  id-safe token (a letter or underscore, then letters, digits, ``_``, ``.``, ``-``). A composed figure
  prefixes its tiles ``p0-``, ``p1-``, … under the caller's prefix; a caller inlining several figures on
  one page passes a distinct prefix per figure, since inline SVG shares the page's id space.

Appearance is set only through presentation attributes (never ``style``), so any consumer stylesheet rule
overrides it.
"""

from __future__ import annotations

import collections
import json
import math
import re
from typing import Protocol

from grus.models import pedigree_pb2 as pb
from grus.render import _geometry, _labels, _layout, _layout2

_position = _labels.position
_label_lines = _labels.label_lines
_roman = _labels.roman


_STROKE = "#000000"
_WIDTH = 2.0
_FONT = "sans-serif"
_GEN_MARKER_SIZE = 16.0
_SET_GAP = 28.0  # vertical gap between stacked pedigrees in a figure render
_TITLE_SIZE = 15.0  # family/panel title above each pedigree tile
_TITLE_GAP = 6.0  # gap between a title and its pedigree
_HIT_STROKE = 12.0  # width of the invisible pointer target laid over a mating line
# The proband's 'P': font size, and its offset left of and below the arrow tail (_arrow, _arrow_bottom, _hit_rect).
_ARROW_LABEL_SIZE = 15.0
_ARROW_LABEL_DX = 8.0
_ARROW_LABEL_DY = 4.0
_ID_PREFIX_RE = re.compile(r"^[A-Za-z_][\w.\-]*$")

# One stacked pedigree in a figure render: (title, width, height, body-elements, root-attributes).
_Tile = tuple[str, float, float, list[str], str]


def render_svg(p: pb.Pedigree, geometry: _geometry.Geometry | None = None, *, id_prefix: str = "") -> str:
    """Validate, lay out, and draw ``p``; return a complete, deterministic SVG document string."""
    _check_prefix(id_prefix)
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
    _check_prefix(id_prefix)
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    tiles: list[_Tile] = []
    for ped in pedigree_set.pedigrees:
        title = _display_title(ped)
        try:
            draw = _Draw(ped, _layout2.layout(ped, geom), geom, id_prefix=f"{id_prefix}p{len(tiles)}-")
            width, height = draw.dimensions()
            tiles.append((title, width, height, draw.body(), draw.root_attrs()))
        except _layout.DeferredFeatureError as deferred:
            tiles.append(_placeholder_tile(ped, str(deferred)))
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
    _check_prefix(id_prefix)
    geom = geometry or _geometry.DEFAULT_GEOMETRY
    out: list[tuple[str, str]] = []
    for ped in pedigree_set.pedigrees:
        title = _display_title(ped)
        try:
            svg = _Draw(ped, _layout2.layout(ped, geom), geom, id_prefix=id_prefix).svg()
        except _layout.DeferredFeatureError as deferred:
            _, width, height, body, attrs = _placeholder_tile(ped, str(deferred))
            svg = _svg_root(width, height, body, attrs)
        out.append((title, svg))
    return out


def _check_prefix(id_prefix: str) -> None:
    """Reject an ``id_prefix`` that could not start an XML id or would break a ``url(#…)`` reference."""
    if id_prefix and not _ID_PREFIX_RE.fullmatch(id_prefix):
        raise ValueError(f"id_prefix must be empty or match {_ID_PREFIX_RE.pattern}, got {id_prefix!r}")


def _condition_legend(p: pb.Pedigree) -> list[str]:
    """Ordered distinct condition names in the pedigree — the key for which region a carrier fills.

    Phenotype legend labels come first (their drawn order), then any remaining condition names by first
    appearance. A condition's index here selects its fill region, so two carriers of *different* named
    conditions get *different* halves (a compound het reads as opposite halves), consistently pedigree-wide.
    """
    order: list[str] = []
    seen: set[str] = set()
    for label in p.labels:
        if label.kind == pb.LABEL_KIND_PHENOTYPE and label.text and label.text not in seen:
            seen.add(label.text)
            order.append(label.text)
    for ind in p.individuals:
        for c in ind.conditions:
            if c.name and c.name not in seen:
                seen.add(c.name)
                order.append(c.name)
    return order


def _data_legend(p: pb.Pedigree) -> list[str]:
    """The ``data-conditions`` array: the fill legend plus a trailing ``""`` slot when any condition is unnamed."""
    unnamed = any(not c.name for ind in p.individuals for c in ind.conditions)
    return [*_condition_legend(p), *([""] if unnamed else [])]


def _pedigree_attrs(p: pb.Pedigree, *, deferred: bool = False) -> str:
    """The ``pedigree`` group's attributes, for the root ``<svg>``, a composed figure's tile, or a placeholder."""
    legend = json.dumps(_data_legend(p), ensure_ascii=False)
    cls = "pedigree deferred" if deferred else "pedigree"
    return f'class="{cls}" data-title="{_attr(_display_title(p))}" data-conditions="{_attr(legend)}"'


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


def _placeholder_tile(ped: pb.Pedigree, reason: str) -> _Tile:
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
    return (_display_title(ped), width, height, body, _pedigree_attrs(ped, deferred=True))


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
    """Escape ``s`` for a double-quoted attribute value.

    Newlines and tabs become character references: attribute-value normalisation would otherwise turn them
    into spaces on the way back out of a parser.
    """
    return _escape(s).replace('"', "&quot;").replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;")


class _EnumNames(Protocol):
    """The one method of a generated protobuf enum wrapper the drawer uses."""

    def Name(self, number: int) -> str: ...  # noqa: N802  # protobuf's spelling


def _enum_word(enum: _EnumNames, value: int, prefix: str) -> str:
    """``GENDER_WOMAN`` -> ``woman``: an enum value as the lower-case word a data attribute carries."""
    return enum.Name(value).removeprefix(prefix).lower()


def _open_g(classes: list[str], attrs: dict[str, str]) -> str:
    """A group's opening tag: ``id`` (when given) first, then ``class``, then the data attributes."""
    parts = [f'id="{_attr(attrs["id"])}"'] if "id" in attrs else []
    parts.append(f'class="{" ".join(classes)}"')
    parts += [f'{k}="{_attr(v)}"' for k, v in attrs.items() if k != "id"]
    return f"<g {' '.join(parts)}>"


def _num(v: float) -> str:
    """Format a coordinate compactly and deterministically (3 dp, trailing zeros stripped, no -0)."""
    r = round(v, 3)
    if r == 0:
        r = 0.0
    return f"{r:.3f}".rstrip("0").rstrip(".")


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
        self._legend = _condition_legend(p)  # ordered condition names -> carrier fill region (which half)
        self._data_legend = _data_legend(p)  # the same plus the unnamed slot, indexing `data-condition-{i}`
        # A real individual may be ghosted more than once; each ghost cell gets an ordinal for a unique id. Both
        # the ordinal and the ghost-link order follow layout cell order, which is shuffle-invariant.
        self._ghost_cell: dict[int, tuple[int, int]] = {}
        self._ghost_ordinal: dict[int, int] = {}
        seen: collections.Counter[int] = collections.Counter()
        for level in range(len(lay.nid)):
            for k in range(lay.n[level]):
                idx = lay.nid[level][k]
                if idx in lay.ghost_of:
                    self._ghost_cell[idx] = (level, k)
                    seen[lay.ghost_of[idx]] += 1
                    self._ghost_ordinal[idx] = seen[lay.ghost_of[idx]]
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
            reach += _ARROW_LABEL_DY + _ARROW_LABEL_SIZE / 2  # the 'P' sits just below the tail, centred
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

    def _cell_w(self, idx: int) -> float:
        """The width cell ``idx``'s label stack reserves; a pass-through or phantom is a line only and reserves none."""
        if idx in self.lay.passthrough or idx in self.lay.phantom:
            return 0.0
        return self._label_w(self._ind_at(idx))

    def _label_w(self, ind: pb.Individual) -> float:
        return _labels.label_width(ind, self.geom)

    def _build_px_map(self) -> dict[float, float]:
        """Map each layout-x to a pixel offset: ``x_unit`` per layout unit, one scale for the whole figure.

        The layout already separates neighbours whose labels would collide (``_layout2._row_seps``), so no gap
        needs widening here. A per-gap widening would keep lines vertical but move every midpoint off the one
        the x-solve centred, which is why the clearance lives in the solve and this map is only a scale.
        """
        return {v: v * self.geom.x_unit for row in self.lay.pos for v in row}

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
                idx = self.lay.nid[level][k]
                extent = 0.0 if idx in self.lay.phantom else max(self.half, self._cell_w(idx) / 2)
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
        return _pedigree_attrs(self.p)

    def svg(self) -> str:
        width, height = self.dimensions()
        return _svg_root(width, height, self.body(), self.root_attrs())

    def _gen_markers(self) -> list[str]:
        """One Roman-numeral generation marker per row, centred in the reserved left gutter."""
        x = self.geom.gen_marker_gutter / 2
        out: list[str] = []
        for level in range(len(self.lay.nid)):
            generation = self.lay.first_generation + level
            out.append(_open_g(["generation"], {"data-generation": str(generation)}))
            out.append(_text(x, self.py(level), _roman(generation), _GEN_MARKER_SIZE))
            out.append("</g>")
        return out

    def _ghost_ordinal_key(self, ghost: int) -> tuple[int, int]:
        return self._ghost_cell[ghost]

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
                left, right = self.lay.nid[level][k], self.lay.nid[level][k + 1]
                classes = ["mating"] + (["partner-omitted"] if {left, right} & self.lay.phantom else [])
                if kind == 2:
                    classes.append("consanguineous")
                if childless == int(pb.CHILDLESSNESS_BY_CHOICE):
                    classes.append("childless-by-choice")
                elif childless == int(pb.CHILDLESSNESS_INFERTILITY):
                    classes.append("childless-infertility")
                partners = " ".join(
                    self._cell_position(level, c) for c in (k, k + 1) if self.lay.nid[level][c] not in self.lay.phantom
                )
                out.append(_open_g(classes, {"data-partners": partners}))
                y = self.py(level)
                # A line leaves each drawn partner's edge; at an omitted partner it ends where that partner would
                # stand (the phantom's centre), as the literature draws a partner left out of the figure.
                x1 = self.px(self.lay.pos[level][k]) + (0.0 if left in self.lay.phantom else self.half)
                x2 = self.px(self.lay.pos[level][k + 1]) - (0.0 if right in self.lay.phantom else self.half)
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
        by_row: dict[int, list[_layout.RoutedMating]] = collections.defaultdict(list)
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
        """Descent drop + sibship bar + per-child stubs, grouping children by their parent (couple or lone).

        A sibship hung from a pass-through is the end of a descent across rows: its group names the real
        parents at the top of the chain and holds every hop — the drop to each pass-through, the line through
        it, and the final sibship. The hops into pass-throughs are drawn there, not as sibships of their own.
        """
        out: list[str] = []
        for level in range(1, len(self.lay.nid)):
            groups: dict[int, list[int]] = {}
            for k in range(self.lay.n[level]):
                pc = self.lay.fam[level][k]
                if pc >= 0 and self.lay.nid[level][k] not in self.lay.passthrough:
                    groups.setdefault(pc, []).append(k)
            for pc in sorted(groups):
                hops: list[str] = []
                top_level, top_pc = level - 1, pc
                while self.lay.nid[top_level][top_pc] in self.lay.passthrough:
                    through_y = self.py(top_level)
                    through_x = self.px(self.lay.pos[top_level][top_pc])
                    hops = [
                        *self._sibship(top_level, self.lay.fam[top_level][top_pc], [top_pc]),
                        _line(through_x, through_y - self.half, through_x, through_y),
                        *hops,
                    ]
                    top_pc = self.lay.fam[top_level][top_pc]
                    top_level -= 1
                heads = [top_pc, top_pc + 1] if self.lay.spouse[top_level][top_pc] else [top_pc]
                parents = [
                    self._cell_position(top_level, c)
                    for c in heads
                    if self.lay.nid[top_level][c] not in self.lay.phantom
                ]
                children = " ".join(self._cell_position(level, k) for k in groups[pc])
                out.append(_open_g(["sibship"], {"data-parents": " ".join(parents), "data-children": children}))
                out += hops
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
        # The bar reaches the drop: a hinge's couple the rows keep from centring over its children drops beside
        # them, and a bar spanning only the children would leave that drop ending in mid-air.
        out = [
            _line(mid_x, parent_y, mid_x, bar_y),
            _line(min(*attach, mid_x), bar_y, max(*attach, mid_x), bar_y),
        ]
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
                if idx in self.lay.passthrough:
                    continue  # a descent's line through this row, drawn by _descents
                if idx in self.lay.phantom:
                    continue  # an omitted partner: only its marriage line is drawn, by _matings
                cx, cy = self.px(self.lay.pos[level][k]), self.py(level)
                real = self.lay.ghost_of.get(idx)
                if real is not None:
                    out += self._ghost_symbol(self.p.individuals[real], cx, cy, self._ghost_ordinal[idx])
                else:
                    out += self._symbol(self.p.individuals[idx], cx, cy)
        return out

    def _ghost_symbol(self, ind: pb.Individual, cx: float, cy: float, ordinal: int) -> list[str]:
        """A duplicated individual (cross-generation join).

        The same shape/affection and id label as the real one, but no arrow, annotations, or status marks —
        those belong to the primary instance. The dashed link (``_ghost_links``) ties it back to that instance.
        """
        out = [self._open_individual(ind, ghost=ordinal)]
        out.append(self._shape(ind.gender, cx, cy, "#ffffff", stroke=False, cls="backing"))
        out += self._affected_fill(ind, cx, cy)
        out.append(self._shape(ind.gender, cx, cy, "none", cls="symbol"))
        lines, ys = _label_lines(ind), self._line_ys(ind)
        if lines:
            out.append(_text(cx, cy + self.half + ys[0], _escape(lines[0]), self.geom.label_size, cls="label"))
        out.append(self._hit_rect(ind, cx, cy, arrow=False))
        out.append("</g>")
        return out

    def _open_individual(self, ind: pb.Individual, *, ghost: int = 0) -> str:
        """The ``individual`` group's opening tag: state classes and the data attributes a consumer selects on.

        ``ghost`` is 0 for the real cell, else the 1-based ordinal of this ghost among the individual's ghosts.
        """
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
        kind = f"ghost-{position}" + (f"-{ghost}" if ghost > 1 else "") if ghost else f"ind-{position}"
        attrs = {
            "id": f"{self.id_prefix}{kind}",
            "data-position": position,
            "data-generation": str(ind.generation),
            "data-index": str(ind.index),
            "data-gender": _enum_word(pb.Gender, ind.gender, "GENDER_"),
        }
        if ind.HasField("external_id"):
            attrs["data-external-id"] = ind.external_id
        # Same-named conditions share a legend slot; their statuses join space-separated (CSS ``~=`` selects one).
        statuses_by_slot: dict[str, list[str]] = {}
        for c in ind.conditions:
            key = f"data-condition-{self._data_legend.index(c.name)}"
            word = _enum_word(pb.ConditionStatus, c.status, "CONDITION_STATUS_")
            if word not in statuses_by_slot.setdefault(key, []):
                statuses_by_slot[key].append(word)
        attrs.update({key: " ".join(words) for key, words in statuses_by_slot.items()})
        return _open_g(classes, attrs)

    def _affected_fill(self, ind: pb.Individual, cx: float, cy: float) -> list[str]:
        """The solid ``fill`` part of an affected individual: the whole shape, naming the first affected condition."""
        affected = [c for c in ind.conditions if c.status == pb.CONDITION_STATUS_AFFECTED]
        if not affected:
            return []
        idx = self._data_legend.index(affected[0].name)
        return [self._shape(ind.gender, cx, cy, _STROKE, stroke=False, cls="fill", extra=f'data-condition="{idx}"')]

    def _hit_rect(self, ind: pb.Individual, cx: float, cy: float, *, arrow: bool) -> str:
        """The invisible pointer target: the symbol, its reserved label box, and the arrow when one is drawn.

        ``arrow`` is whether this cell draws the proband/consultand arrow (a ghost never does).
        """
        w = max(self.geom.symbol_size, self._label_w(ind))
        left, right = cx - w / 2, cx + w / 2
        bottom = cy + self.half + self.label_band
        if arrow and (ind.proband or ind.consultand):
            # The arrow's tail sits symbol_size/sqrt(2) beyond the lower-left corner; a proband's 'P' hangs
            # left of and below the tail (_arrow), so extend to the tail plus the letter's half-width.
            tail_x = cx - self.half - self.geom.symbol_size / math.sqrt(2)
            left = min(left, tail_x - (_ARROW_LABEL_DX + 0.6 * _ARROW_LABEL_SIZE / 2 if ind.proband else 0))
            bottom = max(bottom, cy + self.half + self._arrow_bottom(ind))
        return (
            f'<rect class="hit" x="{_num(left)}" y="{_num(cy - self.half)}" width="{_num(right - left)}" '
            f'height="{_num(bottom - (cy - self.half))}" fill="none" pointer-events="all"/>'
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
        ghosts_in_cell_order = sorted(self.lay.ghost_of, key=self._ghost_ordinal_key)
        for ghost in ghosts_in_cell_order:
            real = self.lay.ghost_of[ghost]
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
        out = [self._open_individual(ind)]
        out.append(self._shape(ind.gender, cx, cy, "#ffffff", stroke=False, cls="backing"))
        out += self._affected_fill(ind, cx, cy)
        if not affected and pb.CONDITION_STATUS_CARRIER in statuses:
            out += self._carrier_glyph(ind, cx, cy)
        out.append(self._shape(ind.gender, cx, cy, "none", cls="symbol"))
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
            out.append(_text(cx, base_y + off, _escape(line), self.geom.label_size, cls="label"))
        out.append(self._hit_rect(ind, cx, cy, arrow=True))
        out.append("</g>")
        return out

    def _carrier_glyph(self, ind: pb.Individual, cx: float, cy: float) -> list[str]:
        """The carrier's ``fill`` parts, drawn under the outline.

        Under ``CarrierStyle.INHERITANCE_GLYPH`` (default, matching existing literature) an X-linked carrier is
        a central dot and everything else a region fill; under ``PARTITION_FILL`` (NSGC 2022, dot retired)
        every carrier is a region fill regardless of inheritance.
        """
        carriers = [c for c in ind.conditions if c.status == pb.CONDITION_STATUS_CARRIER]
        if self.geom.carrier_style is _geometry.CarrierStyle.INHERITANCE_GLYPH:
            x_linked = {pb.INHERITANCE_X_LINKED_RECESSIVE, pb.INHERITANCE_X_LINKED_DOMINANT}
            x_linked_carriers = [c for c in carriers if c.inheritance in x_linked]
            if x_linked_carriers:
                cond = self._data_legend.index(x_linked_carriers[0].name)
                return [
                    f'<circle class="fill dot" data-condition="{cond}" cx="{_num(cx)}" cy="{_num(cy)}" '
                    f'r="{_num(self.half * 0.26)}" fill="{_STROKE}"/>'
                ]
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
            out.append(_text(tx - _ARROW_LABEL_DX, ty + _ARROW_LABEL_DY, label, _ARROW_LABEL_SIZE))
        return out


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
