"""SVG → PNG rasterization, for the eval and `grus render --png`.

The round-trip re-extracts a rendered candidate, but the vision model reads raster, not SVG; and the visual
judge compares figure images. resvg (``resvg-py``, the optional ``raster`` extra) renders the named pattern
fills crisply at any zoom and ships as a self-contained wheel. cairosvg, the previous rasterizer, resampled
pattern tiles into a blur whenever it scaled, softening every carrier hatch, and needed native libcairo. The
import is lazy, so importing this module needs no rasterizer.
"""

from __future__ import annotations

from typing import Any


def rasterize(svg: str, *, scale: float = 2.0) -> bytes:
    """Render an SVG document string to PNG bytes on white, scaled up ``scale``x for legibility.

    ``scale`` > 1 keeps a small pedigree's glyphs readable to the vision model; a 1x raster of a compact
    figure can be too small, reintroducing extraction error the eval would misattribute to the pipeline. The
    background is opaque white: a transparent PNG shows its empty regions as whatever the viewer composites
    it on, which need not be white.
    """
    if scale <= 0:
        raise ValueError(f"scale must be positive, got {scale}")
    import resvg_py  # type: ignore[import-not-found]  # lazy: the optional `raster` extra

    svg_to_bytes: Any = resvg_py.svg_to_bytes
    return bytes(svg_to_bytes(svg_string=svg, zoom=scale, background="white"))
