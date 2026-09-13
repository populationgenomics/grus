"""Tests for grus.render.rasterize: SVG -> PNG (docs/plans/16-svg-rasterizer.md).

cairosvg needs native libcairo and lives in the optional ``raster`` dependency group, so these skip when it
(or the native lib) is absent rather than failing — CI's default groups omit it, mirroring how the extractor
tests never require ``anthropic``.
"""

from __future__ import annotations

import pytest

from grus import render

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><circle cx="20" cy="20" r="15"/></svg>'


def _rasterize_or_skip(svg: str, **kwargs: float) -> bytes:
    # rasterize lazily imports cairosvg, which raises ImportError if the package is absent (CI default
    # groups omit it) or OSError if the native libcairo is missing. Skip on either — neither is a failure.
    try:
        return render.rasterize(svg, **kwargs)
    except (ImportError, OSError) as error:
        pytest.skip(f"cairosvg/libcairo unavailable: {error}")


def test_rasterize_returns_png_bytes() -> None:
    assert _rasterize_or_skip(_SVG)[:8] == _PNG_MAGIC


def test_rasterize_scale_increases_output() -> None:
    small = _rasterize_or_skip(_SVG, scale=1.0)
    large = _rasterize_or_skip(_SVG, scale=3.0)
    assert small[:8] == _PNG_MAGIC and large[:8] == _PNG_MAGIC
    assert len(large) > len(small)  # more pixels at higher scale
