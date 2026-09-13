"""Regenerate the committed Python stubs from the hand-authored protos (docs/design/ir.md).

Fully local — no BSR remote plugins:

1. ``buf export`` materializes the protos + their ``buf.lock``-pinned deps (``buf/validate``) into a
   temp tree. A one-time cached module fetch, not a remote-plugin execution.
2. ``grpcio-tools``' ``protoc`` emits the Python message stubs + ``.pyi`` over every proto (plus the
   used ``buf/validate`` dep — the ``protovalidate`` wheels ship no Python stub). Its bundled
   ``protoc`` pins the generated-code version to the protobuf 6.x runtime; well-known types
   (``google.protobuf.*``) resolve from ``grpcio-tools``' bundled includes.

grus has no gRPC services, so no ``_pb2_grpc`` stubs are generated.

Run with ``uv run --group codegen python -m tools.schema.regen``; ``buf`` must be on ``PATH``.
Pure generation only — ``buf lint``/``buf breaking`` and stub-import checks live in CI, not here.
"""

from __future__ import annotations

import importlib.resources
import pathlib
import shutil
import subprocess
import sys
import tempfile

from grpc_tools import protoc

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
# grpcio-tools' bundled well-known-type protos. The protoc.main() API — unlike the CLI — does not
# add this include itself.
_WELL_KNOWN = importlib.resources.files("grpc_tools") / "_proto"


def _protoc(include: pathlib.Path, protos: list[str]) -> None:
    """Run ``grpcio-tools`` protoc over ``protos`` (relative to ``include``), writing to the repo root."""
    args = [
        "protoc",
        f"--proto_path={include}",
        f"--proto_path={_WELL_KNOWN}",
        f"--python_out={_REPO_ROOT}",
        f"--pyi_out={_REPO_ROOT}",
        *protos,
    ]
    if protoc.main(args) != 0:
        raise SystemExit(f"protoc failed for {protos}")


def main() -> int:
    """Export the buf module and regenerate every committed Python stub; return the exit code."""
    if shutil.which("buf") is None:
        raise SystemExit("buf not found on PATH; install buf (https://buf.build) to regenerate stubs")

    with tempfile.TemporaryDirectory() as tmp:
        export = pathlib.Path(tmp)
        # Materialize protos + buf.lock-pinned deps (buf/validate) for protoc's include path.
        subprocess.run(["buf", "export", ".", "--output", str(export)], cwd=_REPO_ROOT, check=True)
        protos = sorted(str(p.relative_to(export)) for p in export.rglob("*.proto"))
        print("schema/regen: python message + pyi stubs (all protos + used deps)")
        _protoc(export, protos)
    print("schema/regen: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
