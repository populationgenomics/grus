"""Worker pools that import grus from a given tree.

Workers are spawned, not forked, so each starts without the parent's grus. The initializer puts the tree first on
``sys.path`` before anything imports grus, and checks that the renderer then comes from that tree. This module must
not import grus at module level: the initializer is unpickled, and so this module imported, before it runs.

A pool respawns a worker whose initializer raises, and a worker that cannot import a task's module dies, so a bad tree
would hang the pool. The initializer records its error instead, and ``pool`` asks a worker for it before handing the
pool out; every worker runs the same initializer on the same tree, so one answer speaks for all.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import multiprocessing.pool
import os
import pathlib
import sys
from collections.abc import Iterator

# Per worker process: why its initializer failed.
_init_errors: list[str] = []


@contextlib.contextmanager
def pool(tree: pathlib.Path, jobs: int) -> Iterator[multiprocessing.pool.Pool]:
    """A pool of ``jobs`` workers whose ``grus`` is the one in ``tree``; terminated on exit.

    Raises:
        RuntimeError: the workers could not import the renderer from ``tree``.
    """
    with multiprocessing.get_context("spawn").Pool(jobs, initializer=_init, initargs=(str(tree),)) as p:
        errors = p.apply(_errors)
        if errors:
            raise RuntimeError(errors[0])
        yield p


def _errors() -> list[str]:
    return _init_errors


def _init(tree: str) -> None:
    if "grus" in sys.modules:
        _init_errors.append("grus was imported in a fuzz worker before its tree was put on sys.path")
        return
    sys.path.insert(0, tree)
    try:
        import grus.render  # after the sys.path insertion, by design
    except ImportError as e:
        _init_errors.append(f"a fuzz worker for {tree} could not import the renderer: {e!r}")
        return
    where = grus.render.__file__ or ""
    if not where.startswith(tree + os.sep):
        _init_errors.append(f"a fuzz worker for {tree} imported the renderer from {where}")
