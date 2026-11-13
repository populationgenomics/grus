"""Tests for the schema-compat gate's version rule: which version bump makes a schema break a breaking release."""

from __future__ import annotations

import pytest

from tools.schema import compat


def _pyproject(version: str) -> str:
    return f'[project]\nname = "grus"\nversion = "{version}"\n'


@pytest.mark.parametrize(
    ("base", "head", "breaks"),
    [
        ("0.4.0", "0.5.0", True),  # below 1.0 the minor is the breaking release
        ("0.4.0", "0.4.1", False),
        ("0.9.3", "1.0.0", True),
        ("1.2.0", "2.0.0", True),
        ("1.2.0", "1.3.0", False),  # from 1.0 on a minor is additive
        ("1.2.0", "1.2.1", False),
    ],
)
def test_a_break_needs_a_breaking_release(base: str, head: str, breaks: bool) -> None:
    assert (compat.breaking_release(_pyproject(head)) > compat.breaking_release(_pyproject(base))) == breaks
