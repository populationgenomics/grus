"""Tests for grus.render.rasterize: SVG -> PNG.

resvg lives in the optional ``raster`` extra, so these skip when it is absent rather than failing; CI's default
groups omit it, and the ``pytest-raster`` job asserts the import before running them.
"""

from __future__ import annotations

import io
import pathlib
import re

import pytest

from grus import render

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><circle cx="20" cy="20" r="15"/></svg>'


def _rasterize_or_skip(svg: str, **kwargs: float) -> bytes:
    # rasterize lazily imports resvg_py, which raises ImportError when the `raster` extra is absent.
    try:
        return render.rasterize(svg, **kwargs)
    except ImportError as error:
        pytest.skip(f"resvg unavailable: {error}")


def test_rasterize_returns_png_bytes() -> None:
    assert _rasterize_or_skip(_SVG)[:8] == _PNG_MAGIC


def test_rasterize_scale_increases_output() -> None:
    small = _rasterize_or_skip(_SVG, scale=1.0)
    large = _rasterize_or_skip(_SVG, scale=3.0)
    assert small[:8] == _PNG_MAGIC and large[:8] == _PNG_MAGIC
    assert len(large) > len(small)  # more pixels at higher scale


@pytest.mark.parametrize("name", ["compound_carrier", "condition_fills", "count_marks", "trio"])
def test_goldens_with_fills_rasterize(name: str) -> None:
    # Every named fill must survive the rasterizer the eval judge sees (cairosvg once failed on objectBoundingBox
    # patterns painted more than twice).
    svg = (pathlib.Path(__file__).parent / "goldens" / f"{name}.svg").read_text()
    assert _rasterize_or_skip(svg)[:8] == _PNG_MAGIC


def test_rasterize_rejects_a_non_positive_scale() -> None:
    with pytest.raises(ValueError, match="scale"):
        render.rasterize(_SVG, scale=0)


def test_background_is_opaque_white() -> None:
    pil = pytest.importorskip("PIL.Image")
    image = pil.open(io.BytesIO(_rasterize_or_skip(_SVG))).convert("RGBA")
    assert image.getpixel((1, 1)) == (255, 255, 255, 255)


def test_carrier_hatch_stays_crisp_when_scaled() -> None:
    # The white hatch over a black carrier tone must stay near-white at 2x. cairosvg resampled pattern tiles
    # into a grey blur (peaks around 150), which is what the eval's vision model would then have read.
    pil = pytest.importorskip("PIL.Image")
    golden = (pathlib.Path(__file__).parent / "goldens" / "compound_carrier.svg").read_text()
    pattern = re.search(r'<pattern id="fill-carrier-0".*?</pattern>', golden, re.S)
    assert pattern is not None, "compound_carrier defines the condition-0 carrier fill"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72">'
        f"<defs>{pattern.group(0)}</defs>"
        '<rect width="72" height="72" fill="url(#fill-carrier-0)"/></svg>'
    )
    pixels = pil.open(io.BytesIO(_rasterize_or_skip(svg, scale=2.0))).convert("L").tobytes()
    assert max(pixels) >= 230, "carrier hatch lines blurred below near-white"
    assert min(pixels) <= 25, "carrier tone lost its black"
