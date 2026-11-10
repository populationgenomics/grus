"""The error raised when a renderer path needs an optional extra that is not installed."""

from __future__ import annotations


class MissingExtraError(ImportError):
    """A renderer path needs an optional extra (``render``, ``raster``) that is not installed.

    An ``ImportError``, so a caller already guarding the optional import keeps working; the message names the
    ``pip install`` line that supplies it.
    """

    def __init__(self, what: str, extra: str) -> None:
        super().__init__(f"{what} needs the optional {extra!r} extra: pip install 'grus[{extra}]'")
        self.extra = extra
