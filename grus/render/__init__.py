"""grus.render — deterministic IR -> SVG pedigree drawing (docs/design/renderer.md).

Two separable steps joined at the per-level array seam: ``layout`` ranks generations, orders each rank by
crossing minimization, and solves x on a constraint model (docs/design/layout-v2.md); ``render_svg`` draws
Bennett-standard symbols and connectors from that grid. ``Geometry`` is the shared spacing bag. Non-overlap
and generation rank are the only hard constraints, so layout rarely fails; the residual topologies it does
not draw (an interlocking loop, a child of two matings, a torn sibship) raise ``DeferredFeatureError``
rather than being mislaid out.
"""

from __future__ import annotations

from ._draw import render_set_svg, render_svg, render_svgs
from ._geometry import DEFAULT_GEOMETRY, CarrierStyle, Geometry
from ._layout import DeferredFeatureError, Layout
from ._layout2 import layout
from ._ordering import Ordering, order
from ._raster import rasterize

__all__ = [
    "DEFAULT_GEOMETRY",
    "CarrierStyle",
    "DeferredFeatureError",
    "Geometry",
    "Layout",
    "Ordering",
    "layout",
    "order",
    "rasterize",
    "render_set_svg",
    "render_svg",
    "render_svgs",
]
