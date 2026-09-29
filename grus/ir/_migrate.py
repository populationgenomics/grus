"""Migrate a pre-1.0 pedigree record to the 1.0 schema (docs/design/ir.md, "Conditions are declared once, by id").

Before 1.0 a person's condition entry carried its own ``name`` and ``inheritance``, and a citation ``Support`` named
its condition by name. 1.0 declares each condition once in ``PedigreeSet.conditions`` and references it by id. The
migration reads the old record through the 0.x schema, rebuilt here from the current one, declares one condition per
distinct name in order of first appearance (``k1``, ``k2``, …), points every entry and support at its declaration,
and validates the result. A support of a declaration field (``condition.name``, ``condition.inheritance``) that
scoped the people whose entries it described now describes the one declaration, so it loses that scope. Entries of
one name that state different modes of inheritance cannot become one declaration, so they fail loudly; an entry that
states none takes the others'. Two entries of one name on one person (pre-1.0 allowed, say, affected and carrier)
have no 1.0 form either, and fail loudly too.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from typing import Any

from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory, text_format
from google.protobuf.message import Message

from grus.ir._conditions import Conditions
from grus.ir._validate import SUPPORT_FIELDS, validate_set
from grus.models import pedigree_pb2 as pb

_PACKAGE = pb.DESCRIPTOR.package
_LEGACY = f"{_PACKAGE}.legacy"


class MigrationError(Exception):
    """A pre-1.0 record cannot be migrated.

    Entries of one condition disagree on inheritance, a person has two entries of one condition, or a support names a
    condition no one has.
    """


def _add_with_deps(pool: descriptor_pool.DescriptorPool, fd: Any, done: set[str]) -> None:
    for dep in fd.dependencies:
        if dep.name not in done:
            _add_with_deps(pool, dep, done)
    if fd.name not in done:
        pool.Add(descriptor_pb2.FileDescriptorProto.FromString(fd.serialized_pb))
        done.add(fd.name)


@functools.cache
def _legacy_classes() -> tuple[type[Message], type[Message]]:
    """The 0.x ``PedigreeSet`` and ``Pedigree`` classes: today's schema with the per-entry name and inheritance back."""
    pool = descriptor_pool.DescriptorPool()
    done: set[str] = set()
    for dep in pb.DESCRIPTOR.dependencies:
        _add_with_deps(pool, dep, done)
    fdp = descriptor_pb2.FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    fdp.name, fdp.package = "grus/models/pedigree_legacy.proto", _LEGACY
    for msg in fdp.message_type:
        for field in msg.field:
            if field.type_name.startswith(f".{_PACKAGE}."):
                field.type_name = f".{_LEGACY}." + field.type_name.removeprefix(f".{_PACKAGE}.")
    by_name = {m.name: m for m in fdp.message_type}

    def drop(msg: descriptor_pb2.DescriptorProto, name: str) -> None:
        (gone,) = [f for f in msg.field if f.name == name]
        keep = [f for f in msg.field if f.name != name]
        if gone.proto3_optional:  # its synthetic oneof goes with it; later oneofs shift down
            del msg.oneof_decl[gone.oneof_index]
            for f in keep:
                if f.HasField("oneof_index") and f.oneof_index > gone.oneof_index:
                    f.oneof_index -= 1
        del msg.field[:]
        msg.field.extend(keep)
        del msg.reserved_range[:]
        del msg.reserved_name[:]

    def optional(msg: descriptor_pb2.DescriptorProto, name: str, number: int, **kind: Any) -> None:
        msg.oneof_decl.add(name=f"_{name}")
        msg.field.add(
            name=name,
            number=number,
            label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
            proto3_optional=True,
            oneof_index=len(msg.oneof_decl) - 1,
            json_name=name,
            **kind,
        )

    condition = by_name["Condition"]
    drop(condition, "condition_id")
    condition.field.add(
        name="name", number=1, label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING, json_name="name",
    )  # fmt: skip
    optional(
        condition,
        "inheritance",
        3,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_ENUM,
        type_name=f".{_LEGACY}.Inheritance",
    )
    support = by_name["Support"]
    drop(support, "condition_id")
    optional(support, "condition", 3, type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    drop(by_name["PedigreeSet"], "conditions")
    pool.Add(fdp)
    get = message_factory.GetMessageClass
    return get(pool.FindMessageTypeByName(f"{_LEGACY}.PedigreeSet")), get(
        pool.FindMessageTypeByName(f"{_LEGACY}.Pedigree")
    )


def legacy_pedigree(p: pb.Pedigree, conditions: Iterable[pb.ConditionDef]) -> Message:
    """``p`` in the 0.x schema: each entry carries its declaration's name and inheritance, as before 1.0.

    A support's ``condition_id`` does not carry over. The stored-layout digest serializes this form, so a pedigree
    migrated from 0.x keeps the digest it had and a declaration's id never enters it.

    Raises:
        UndeclaredConditionError: an entry names a ``condition_id`` no declaration has.
    """
    table = Conditions(conditions)
    _, legacy_class = _legacy_classes()
    out: Any = legacy_class.FromString(p.SerializeToString())
    out.DiscardUnknownFields()  # the entries' condition_id and the supports' condition_id
    for ind, legacy_ind in zip(p.individuals, out.individuals, strict=True):
        for e, c in zip(table.entries(ind), legacy_ind.conditions, strict=True):
            c.name = e.name
            if e.inheritance is not None:
                c.inheritance = e.inheritance
    return out


def _parse_legacy(text: str, *, json: bool) -> Message:
    """A pre-1.0 ``PedigreeSet`` from ``text``, or a bare pre-1.0 ``Pedigree`` wrapped as a one-pedigree set."""
    legacy_set, legacy_pedigree = _legacy_classes()
    parse = json_format.Parse if json else text_format.Parse
    try:
        return parse(text, legacy_set())
    except (json_format.ParseError, text_format.ParseError):
        pedigree = parse(text, legacy_pedigree())
    out: Any = legacy_set()
    out.pedigrees.add().CopyFrom(pedigree)
    return out


def _migrate(old: Any) -> pb.PedigreeSet:
    stated: dict[str, set[pb.Inheritance]] = {}
    for p in old.pedigrees:
        for ind in p.individuals:
            names = [c.name for c in ind.conditions]
            if len(set(names)) != len(names):
                dup = sorted({n for n in names if names.count(n) > 1})
                raise MigrationError(
                    f"individual ({ind.generation}, {ind.index}) has several entries of condition(s) {dup}; "
                    "1.0 allows one entry per condition, so keep the one that holds"
                )
            for c in ind.conditions:
                modes = stated.setdefault(c.name, set())
                if c.HasField("inheritance"):
                    modes.add(c.inheritance)
    for name, modes in stated.items():
        if len(modes) > 1:
            names = sorted(pb.Inheritance.Name(m) for m in modes)
            raise MigrationError(f"entries of condition {name!r} disagree on inheritance: {names}")
    ids = {name: f"k{i + 1}" for i, name in enumerate(stated)}
    new = pb.PedigreeSet.FromString(old.SerializeToString())
    new.DiscardUnknownFields()  # the entries' old name and inheritance, and supports' old condition
    for name, cid in ids.items():
        d = new.conditions.add(id=cid, name=name)
        if stated[name]:
            (d.inheritance,) = stated[name]
    for p_old, p_new in zip(old.pedigrees, new.pedigrees, strict=True):
        for i_old, i_new in zip(p_old.individuals, p_new.individuals, strict=True):
            for c_old, c_new in zip(i_old.conditions, i_new.conditions, strict=True):
                c_new.condition_id = ids[c_old.name]
        for k, (s_old, s_new) in enumerate(zip(p_old.supports, p_new.supports, strict=True)):
            if s_old.HasField("condition"):
                if s_old.condition not in ids:
                    raise MigrationError(f"supports[{k}] names condition {s_old.condition!r}, which no entry has")
                s_new.condition_id = ids[s_old.condition]
            if SUPPORT_FIELDS.get(s_new.field) == {"declaration"}:
                del s_new.individuals[:]
                del s_new.matings[:]
    validate_set(new)
    return new


def migrate_pbtxt(text: str) -> pb.PedigreeSet:
    """A pre-1.0 ``PedigreeSet`` or bare ``Pedigree`` in pbtxt, as a validated 1.0 ``PedigreeSet``.

    Raises:
        MigrationError: see ``MigrationError``.
        IntegrityError: the migrated record is not valid 1.0 IR.
    """
    return _migrate(_parse_legacy(text, json=False))


def migrate_json(text: str) -> pb.PedigreeSet:
    """``migrate_pbtxt`` for a proto3-JSON record."""
    return _migrate(_parse_legacy(text, json=True))
