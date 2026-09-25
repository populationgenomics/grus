"""The fuzz generator is pinned: a seed names the same pedigree on every revision."""

import hashlib

import pytest
from google.protobuf import text_format

from grus import ir
from tools.fuzz import gen

# sha256 of the pbtxt of gen(seed, maxgen). A change here re-keys every fuzz corpus; make it only on purpose.
_PINNED = {
    (0, 4): ("f86b35bc245d5ae2cfa5854eac2bcdafd6478d347fc6bfae8acb8f46569fdb36", 9),
    (7, 4): ("05e3aefcf002637252e8d37d1035bf2e3a038c12a470abbfe7c919d7b14f204e", 44),
    (250, 5): ("ca4969c7b4fbf8084826267fd501867a8209a5d07cfb44675b3b655c803e8b8f", 51),
}


@pytest.mark.parametrize(("seed", "maxgen"), sorted(_PINNED))
def test_seed_reproduces_its_pedigree(seed: int, maxgen: int) -> None:
    p = gen.gen(seed, maxgen)
    digest, n = _PINNED[(seed, maxgen)]
    assert len(p.individuals) == n
    assert hashlib.sha256(text_format.MessageToString(p).encode()).hexdigest() == digest


@pytest.mark.parametrize("seed", range(20))
def test_pedigrees_are_valid_ir(seed: int) -> None:
    ir.validate(gen.gen(seed, 4))
