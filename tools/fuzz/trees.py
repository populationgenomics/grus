"""Grus source trees to test, each isolated in a directory that holds only its ``grus`` and ``buf`` packages.

A tree is named by a directory holding a ``grus`` package (a checkout or a worktree) or by a git ref in the current
repository, which is extracted with ``git archive``. Only those two packages are exposed on the tree's path, so the
tree under test never shadows this tooling or anything else a worker imports.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator, Sequence

_PACKAGES = ("grus", "buf")


@contextlib.contextmanager
def materialised(specs: Sequence[str]) -> Iterator[list[pathlib.Path]]:
    """Yield one isolated directory per spec, for ``sys.path``; removed on exit.

    Raises:
        ValueError: a spec is neither a directory holding a grus tree nor a git ref.
    """
    with tempfile.TemporaryDirectory(prefix="grus-fuzz-") as tmp:
        yield [_materialise(spec, pathlib.Path(tmp) / f"tree{i}") for i, spec in enumerate(specs)]


def _materialise(spec: str, dest: pathlib.Path) -> pathlib.Path:
    dest.mkdir()
    src = pathlib.Path(spec)
    if src.is_dir():
        if not (src / "grus" / "render" / "__init__.py").is_file():
            raise ValueError(f"{spec} has no grus/render package")
        for name in _PACKAGES:
            if (src / name).is_dir():
                (dest / name).symlink_to((src / name).resolve())
        return dest
    rev = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}"], capture_output=True, text=True
    )
    if rev.returncode != 0:
        raise ValueError(f"{spec!r} is neither a directory holding a grus tree nor a git ref")
    sha = rev.stdout.strip()
    present = [n for n in _PACKAGES if subprocess.run(["git", "cat-file", "-e", f"{sha}:{n}"]).returncode == 0]
    archive = subprocess.run(["git", "archive", "--format=tar", sha, *present], capture_output=True, check=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest
