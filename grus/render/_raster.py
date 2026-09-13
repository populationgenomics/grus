"""SVG → PNG rasterization for the eval (docs/plans/16-svg-rasterizer.md).

The round-trip re-extracts a rendered candidate, but the vision model reads raster, not SVG; and the visual
judge compares figure images. cairosvg is imported lazily (it needs native libcairo), so importing this
module — and running the unit tests — needs neither the package nor the native lib, exactly as the Anthropic
client keeps ``anthropic`` out of the core path.
"""

from __future__ import annotations

from typing import Any


def rasterize(svg: str, *, scale: float = 2.0) -> bytes:
    """Render an SVG document string to PNG bytes (cairosvg), scaled up ``scale``x for legibility.

    ``scale`` > 1 keeps a small pedigree's glyphs readable to the vision model; a 1x raster of a compact
    figure can be too small, reintroducing extraction error the eval would misattribute to the pipeline.
    """
    import cairosvg  # type: ignore[import-not-found]  # lazy: needs native libcairo, kept out of the core path

    # Bind through Any so type-checking is identical whether or not cairosvg is installed in the env — its
    # stub types svg2png as returning bytes|None and scale as int, neither of which holds for our call.
    svg2png: Any = cairosvg.svg2png
    return svg2png(bytestring=svg.encode("utf-8"), scale=scale)
