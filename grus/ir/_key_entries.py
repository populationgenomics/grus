"""What a person's symbol shows, read through the set's key (docs/design/ir.md, "The key").

A person marks phenotype and other key entries by id; the entry's text, kind and inheritance live on the ``KeyEntry``. A
genotype entry is never marked: a person shows it when their genotype at its locus matches its pattern. Every consumer
that needs what a symbol shows — the renderer's legend, fills and key, the structural diff, the stored-layout digest —
reads it through a ``Key`` built from the set's entries.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from grus.models import pedigree_pb2 as pb

PHENOTYPE, GENOTYPE, OTHER = "phenotype", "genotype", "other"


@dataclasses.dataclass(frozen=True)
class Shown:
    """One key entry a person shows, resolved through its declaration.

    Attributes:
        key_id: the entry's id.
        kind: ``"phenotype"``, ``"genotype"`` or ``"other"``.
        text: the entry's text as printed (``""`` = the implied key's sole phenotype).
        status: a phenotype mark's ``ConditionStatus``; ``None`` for a grade, and for a genotype or other entry.
        grade: a phenotype mark's key-defined grade, or ``None``.
        inheritance: a phenotype entry's declared ``Inheritance``, or ``None`` when it states none.
        onset_age: a phenotype mark's age at onset, verbatim, or ``None``.
    """

    key_id: str
    kind: str
    text: str
    status: int | None = None
    grade: str | None = None
    inheritance: int | None = None
    onset_age: str | None = None


class UndeclaredKeyEntryError(KeyError):
    """A mark names a ``key_id`` no entry in the key has."""


def pattern_matches(pattern: pb.GenotypePattern, genotype: pb.Genotype) -> bool:
    """Whether ``genotype`` shows a genotype entry of ``pattern``.

    A pattern that states copies matches a genotype at its locus with the same copies; one whose copies are not stated
    matches any genotype at its locus carrying each of its alleles.
    """
    if genotype.locus_id != pattern.locus_id:
        return False
    if pattern.copies_unstated:
        return set(pattern.alleles) <= set(genotype.alleles)
    return not genotype.copies_unstated and sorted(pattern.alleles) == sorted(genotype.alleles)


class Key:
    """The set's key entries by id, for reading what a person's symbol shows.

    Build from ``PedigreeSet.key_entries``; an empty key serves a pedigree whose people show nothing.
    """

    def __init__(self, entries: Iterable[pb.KeyEntry] = ()) -> None:
        self._by_id: dict[str, pb.KeyEntry] = {e.id: e for e in entries}
        self._genotype = [e for e in self._by_id.values() if e.HasField("genotype")]

    def __contains__(self, key_id: object) -> bool:
        return key_id in self._by_id

    def entry(self, key_id: str) -> pb.KeyEntry:
        """The entry ``key_id``.

        Raises:
            UndeclaredKeyEntryError: no entry has this id.
        """
        try:
            return self._by_id[key_id]
        except KeyError:
            raise UndeclaredKeyEntryError(
                f"key_id {key_id!r} is not declared; key entries are declared in PedigreeSet.key_entries"
            ) from None

    def marked(self, ind: pb.Individual) -> list[Shown]:
        """``ind``'s marks in order, each read through its entry: the phenotype and other entries they show.

        Raises:
            UndeclaredKeyEntryError: a mark names an undeclared entry.
        """
        return [self._resolve(m) for m in ind.marks]

    def shown(self, ind: pb.Individual) -> list[Shown]:
        """Every entry ``ind`` shows: their marks in order, then the genotype entries their genotypes match.

        Raises:
            UndeclaredKeyEntryError: a mark names an undeclared entry.
        """
        out = self.marked(ind)
        for e in self._genotype:
            if any(pattern_matches(e.genotype, g) for g in ind.genotypes):
                out.append(Shown(key_id=e.id, kind=GENOTYPE, text=e.text))
        return out

    def _resolve(self, m: pb.Mark) -> Shown:
        e = self.entry(m.key_id)
        kind = e.WhichOneof("denotes")
        inheritance = e.phenotype.inheritance if kind == PHENOTYPE and e.phenotype.HasField("inheritance") else None
        return Shown(
            key_id=m.key_id,
            kind=kind or "",
            text=e.text,
            status=m.status if m.WhichOneof("state") == "status" else None,
            grade=m.grade if m.WhichOneof("state") == "grade" else None,
            inheritance=inheritance,
            onset_age=m.onset_age if m.HasField("onset_age") else None,
        )
