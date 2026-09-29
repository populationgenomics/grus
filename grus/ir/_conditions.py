"""A person's condition entries, read through the set's declarations.

docs/design/ir.md, "Conditions are declared once, by id". An entry names its condition by id; the name and the mode
of inheritance live on the ``ConditionDef``. Every consumer that needs them — the renderer's legend, fills and key,
the structural diff — reads an entry through a ``Conditions`` table built from the set's declarations.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from grus.models import pedigree_pb2 as pb


@dataclasses.dataclass(frozen=True)
class Entry:
    """One of a person's condition entries, resolved: its declaration's name and inheritance, and its own status.

    Attributes:
        condition_id: the declaration's id.
        name: the declared name ("" = the figure's sole, unnamed condition).
        status: the entry's ``ConditionStatus``.
        inheritance: the declared ``Inheritance``, or ``None`` when the declaration states none.
        onset_age: the entry's age at onset, verbatim, or ``None``.
    """

    condition_id: str
    name: str
    status: int
    inheritance: int | None
    onset_age: str | None


class UndeclaredConditionError(KeyError):
    """An entry names a ``condition_id`` no declaration in the table has."""


class Conditions:
    """The set's condition declarations by id, for reading a person's entries.

    Build from ``PedigreeSet.conditions``; an empty table serves a pedigree with no condition entries.
    """

    def __init__(self, declarations: Iterable[pb.ConditionDef] = ()) -> None:
        self._by_id: dict[str, pb.ConditionDef] = {d.id: d for d in declarations}

    def __contains__(self, condition_id: object) -> bool:
        return condition_id in self._by_id

    def declaration(self, condition_id: str) -> pb.ConditionDef:
        """The declaration of ``condition_id``.

        Raises:
            UndeclaredConditionError: no declaration has this id.
        """
        try:
            return self._by_id[condition_id]
        except KeyError:
            raise UndeclaredConditionError(
                f"condition_id {condition_id!r} is not declared; conditions are declared in PedigreeSet.conditions"
            ) from None

    def entries(self, ind: pb.Individual) -> list[Entry]:
        """``ind``'s entries in order, each read through its declaration."""
        out = []
        for c in ind.conditions:
            d = self.declaration(c.condition_id)
            out.append(
                Entry(
                    condition_id=c.condition_id,
                    name=d.name,
                    status=c.status,
                    inheritance=d.inheritance if d.HasField("inheritance") else None,
                    onset_age=c.onset_age if c.HasField("onset_age") else None,
                )
            )
        return out
