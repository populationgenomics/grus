"""The pedigrees a fuzz run lays out: generator seeds, plus IR files.

A case names one pedigree and loads it where it is laid out, so a worker parses it with the grus tree under test. A
case's ``key`` is its identity across runs and trees: ``g{maxgen}:{seed}`` for a generated pedigree, the path for a
``Pedigree`` pbtxt file, ``path#k`` for the ``k``-th pedigree of a ``PedigreeSet`` file.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from collections.abc import Iterable, Sequence

from google.protobuf import text_format

from grus import ir
from grus.models import pedigree_pb2 as pb
from tools.fuzz import gen

DEFAULT_CORPUS = "4:0-299"
_SPEC = re.compile(r"(\d+):(\d+)(?:-(\d+))?")


@dataclasses.dataclass(frozen=True)
class GenCase:
    maxgen: int
    seed: int

    @property
    def key(self) -> str:
        return f"g{self.maxgen}:{self.seed}"

    def load(self) -> pb.Pedigree:
        return gen.gen(self.seed, self.maxgen)


@dataclasses.dataclass(frozen=True)
class FileCase:
    path: str
    member: int | None  # index into a PedigreeSet file; None for a Pedigree file

    @property
    def key(self) -> str:
        return self.path if self.member is None else f"{self.path}#{self.member}"

    def load(self) -> pb.Pedigree:
        text = pathlib.Path(self.path).read_text()
        if self.member is None:
            return ir.load_pbtxt(text)
        return ir.load_set_pbtxt(text).pedigrees[self.member]


Case = GenCase | FileCase


def parse_spec(spec: str) -> list[GenCase]:
    """``MAXGEN:LO-HI`` (inclusive) or ``MAXGEN:SEED`` as generator cases.

    Raises:
        ValueError: ``spec`` is not of that form, or the range is empty.
    """
    m = _SPEC.fullmatch(spec)
    if m is None:
        raise ValueError(f"corpus spec {spec!r} is not MAXGEN:LO-HI or MAXGEN:SEED")
    maxgen, lo = int(m[1]), int(m[2])
    hi = int(m[3]) if m[3] is not None else lo
    if hi < lo:
        raise ValueError(f"corpus spec {spec!r} has an empty seed range")
    return [GenCase(maxgen, s) for s in range(lo, hi + 1)]


def file_cases(path: pathlib.Path) -> list[FileCase]:
    """One case per pedigree in a pbtxt file, or in every ``*.pbtxt`` under a directory.

    Raises:
        ValueError: a directory holds no pbtxt, or a file parses as neither a ``Pedigree`` nor a ``PedigreeSet``.
    """
    if path.is_dir():
        files = sorted(path.rglob("*.pbtxt"))
        if not files:
            raise ValueError(f"{path} holds no .pbtxt files")
        return [c for f in files for c in file_cases(f)]
    text = path.read_text()
    try:
        text_format.Parse(text, pb.Pedigree())
        return [FileCase(str(path), None)]
    except text_format.ParseError:
        pass
    try:
        ps = text_format.Parse(text, pb.PedigreeSet())
    except text_format.ParseError as e:
        raise ValueError(f"{path} is neither a Pedigree nor a PedigreeSet pbtxt") from e
    return [FileCase(str(path), k) for k in range(len(ps.pedigrees))]


def build(specs: Sequence[str], ir_paths: Iterable[pathlib.Path]) -> list[Case]:
    """The generator cases for ``specs`` followed by the file cases for ``ir_paths``, keys unique.

    Raises:
        ValueError: a spec or path is malformed, or two cases share a key.
    """
    cases: list[Case] = [c for s in specs for c in parse_spec(s)]
    cases += [c for p in ir_paths for c in file_cases(p)]
    keys = [c.key for c in cases]
    if len(set(keys)) != len(keys):
        raise ValueError("the corpus names a pedigree twice; check overlapping --corpus ranges and --ir paths")
    return cases
