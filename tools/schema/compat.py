"""Gate a schema change on backward compatibility, unless the package's major version rises (docs/design/ir.md).

``buf breaking`` (FILE) against ``BASE_REF``: a clean result passes. A break passes only when ``pyproject.toml``'s
major version at HEAD exceeds the one at ``BASE_REF``, the signal that a breaking release migrates every consumer;
any other break fails. Run as ``python -m tools.schema.compat BASE_REF`` (``buf`` on ``PATH``).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def major(pyproject: str) -> int:
    """The major component of the project version in a ``pyproject.toml`` text."""
    return int(tomllib.loads(pyproject)["project"]["version"].split(".")[0])


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
    head, was = major((_ROOT / "pyproject.toml").read_text()), major(base)
    if head > was:
        print(f"schema-compat: breaking change allowed by the major version bump {was} -> {head}")
        return 0
    print(f"schema-compat: breaking change without a major version bump (still {head}); bump it or stay additive")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
