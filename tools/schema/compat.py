"""Gate a schema change on backward compatibility, unless it is a breaking release (docs/design/ir.md).

``buf breaking`` (FILE) against ``BASE_REF``: a clean result passes. A break passes only when ``pyproject.toml``'s
breaking release at HEAD exceeds the one at ``BASE_REF``, the signal that a breaking release migrates every consumer;
any other break fails. The breaking release is the major version, or below 1.0 the minor (``0.4`` to ``0.5`` may
break, as semver's major version zero and Cargo have it). Run as ``python -m tools.schema.compat BASE_REF`` (``buf``
on ``PATH``).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def breaking_release(pyproject: str) -> tuple[int, int]:
    """The release line of the project version in a ``pyproject.toml`` text that a break must raise.

    ``(major, 0)`` from 1.0 on; ``(0, minor)`` below it.
    """
    version = tomllib.loads(pyproject)["project"]["version"]
    release = re.match(r"(\d+)(?:\.(\d+))?", version)
    if release is None:
        raise ValueError(f"version {version!r} does not start with a release number")
    major, minor = int(release[1]), int(release[2] or 0)
    return (major, 0) if major else (0, minor)


def _show(release: tuple[int, int]) -> str:
    return f"{release[0]}.{release[1]}" if release[0] == 0 else str(release[0])


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m tools.schema.compat BASE_REF", file=sys.stderr)
        return 2
    base_ref = argv[0]
    result = subprocess.run(["buf", "breaking", "--against", f".git#ref={base_ref}"], cwd=_ROOT, check=False)
    if result.returncode == 0:
        return 0
    base = subprocess.run(
        ["git", "show", f"{base_ref}:pyproject.toml"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout
    head, was = breaking_release((_ROOT / "pyproject.toml").read_text()), breaking_release(base)
    if head > was:
        print(f"schema-compat: breaking change allowed by the release bump {_show(was)} -> {_show(head)}")
        return 0
    print(
        f"schema-compat: breaking change without a breaking release (still {_show(head)}); "
        "bump the major version (the minor below 1.0) or stay additive"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
