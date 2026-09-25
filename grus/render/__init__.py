"""grus.render — deterministic IR -> SVG pedigree drawing (docs/design/renderer.md).

Two separable steps joined at the per-level array seam: ``layout`` ranks generations, orders each rank by
crossing minimization, and solves x on a constraint model (docs/design/layout-v2.md); ``render_svg`` draws
Bennett-standard symbols and connectors from that grid. ``Geometry`` is the shared spacing bag. Non-overlap
and generation rank are the only hard constraints, so layout rarely fails; the residual topologies it does
not draw (an interlocking loop, a child of two matings, a torn sibship) raise ``DeferredFeatureError``
rather than being mislaid out.

A layout can be computed once and stored (``store_layout`` -> a ``PedigreeLayout`` proto), then drawn from with
``render_svg(..., stored_layout=...)``: the same bytes without the layout's cost, refused with ``StaleLayoutError`` if
the pedigree, the layout's geometry or the layout algorithm changed since (docs/design/layout-store.md).
"""

from __future__ import annotations

from grus.render._draw import render_set_svg, render_svg, render_svgs
from grus.render._geometry import DEFAULT_GEOMETRY, CarrierStyle, Geometry
from grus.render._layout import DeferredFeatureError, Layout
from grus.render._layout2 import layout
from grus.render._ordering import Ordering, order
from grus.render._raster import rasterize
from grus.render._store import LAYOUT_VERSION, StaleLayoutError, load_layout, store_layout
from grus.render._xsolve import XSolver

__all__ = [
    "DEFAULT_GEOMETRY",
    "LAYOUT_VERSION",
    "CarrierStyle",
    "DeferredFeatureError",
    "Geometry",
    "Layout",
    "Ordering",
    "StaleLayoutError",
    "XSolver",
    "layout",
    "load_layout",
    "order",
    "rasterize",
    "render_set_svg",
    "render_svg",
    "render_svgs",
    "store_layout",
]
