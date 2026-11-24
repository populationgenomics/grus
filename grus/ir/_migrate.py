"""Migrate a record written before the key to the current schema (docs/design/ir.md, "The key", Migration).

Two older shapes are read:

* **0.4**: ``PedigreeSet.conditions`` declared each condition once (``ConditionDef``: id, name, inheritance) and each
  person's ``Individual.conditions`` held entries referencing them (``Condition``: condition_id, status, onset age).
  Every condition was a phenotype by construction, so each declaration becomes a phenotype ``KeyEntry`` with its name
  as the text, each entry a ``Mark``, and each support citing a condition cites the entry, its path renamed.
* **pre-0.4**: each condition entry carried its own ``name`` and ``inheritance``, and a support named its condition by
  name. It is first brought to 0.4 by declaring one condition per distinct name in order of first appearance (``k1``,
  ``k2``, …, skipping an id a citation or locus has). Entries of one name that state different modes of inheritance
  cannot become one declaration, and two entries of one name on one person (allowed then, say affected and carrier)
  have no later form; both fail loudly.

Both are read through the frozen 0.4 descriptor (``_schema_0_4``), the pre-0.4 schema derived from it. A record already
in the current shape migrates to itself. The result is validated.
"""

from __future__ import annotations

import functools
import itertools
from collections.abc import Iterable
from typing import Any

from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory, text_format
from google.protobuf.message import Message

from grus.ir import _key_entries, _schema_0_4, _validate
from grus.models import pedigree_pb2 as pb

_PACKAGE = pb.DESCRIPTOR.package
_V0_4 = f"{_PACKAGE}.v0_4"
_PRE_0_4 = f"{_PACKAGE}.pre_0_4"
_DIGEST = f"{_PACKAGE}.digest"
# Records of the 0.4, pre-0.4 and digest schemas are messages of classes built at run time (message_factory), so
# no stub types their fields: they are annotated Any.
# 0.4's support paths, by what each became.
_PATHS = {
    "condition.status": "mark.status",
    "condition.onset_age": "mark.onset_age",
    "condition.name": "key.text",
    "condition.inheritance": "key.inheritance",
}
_DECLARATION_PATHS = frozenset({"condition.name", "condition.inheritance"})


class MigrationError(Exception):
    """A pre-0.4 record cannot be migrated.

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


def _moved(
    serialized: bytes, package: str
) -> tuple[descriptor_pb2.FileDescriptorProto, dict[str, descriptor_pb2.DescriptorProto]]:
    """A serialized pedigree schema moved to ``package``, and its messages by name."""
    fdp = descriptor_pb2.FileDescriptorProto.FromString(serialized)
    fdp.name, fdp.package = f"grus/models/pedigree_{package.rsplit('.', 1)[-1]}.proto", package
    for msg in fdp.message_type:
        for field in msg.field:
            if field.type_name.startswith(f".{_PACKAGE}."):
                field.type_name = f".{package}." + field.type_name.removeprefix(f".{_PACKAGE}.")
    return fdp, {m.name: m for m in fdp.message_type}


def _schema(package: str) -> tuple[descriptor_pb2.FileDescriptorProto, dict[str, descriptor_pb2.DescriptorProto]]:
    """The frozen 0.4 schema, moved to ``package``, and its messages by name."""
    return _moved(_schema_0_4.SERIALIZED, package)


def _named_condition(condition: descriptor_pb2.DescriptorProto, package: str) -> None:
    """A 0.4 ``Condition`` as before 0.4: its declaration's name and inheritance in place of its id."""
    _drop(condition, "condition_id")
    _add(condition, "name", 1, type=_STRING)
    _add(condition, "inheritance", 3, optional=True, type=descriptor_pb2.FieldDescriptorProto.TYPE_ENUM,
         type_name=f".{package}.Inheritance")  # fmt: skip


def _classes(fdp: descriptor_pb2.FileDescriptorProto) -> dict[str, type[Message]]:
    pool = descriptor_pool.DescriptorPool()
    done: set[str] = set()
    for dep in pb.DESCRIPTOR.dependencies:
        _add_with_deps(pool, dep, done)
    pool.Add(fdp)
    return {
        name: message_factory.GetMessageClass(pool.FindMessageTypeByName(f"{fdp.package}.{name}"))
        for name in ("PedigreeSet", "Pedigree", "Condition")
    }


def _drop(msg: descriptor_pb2.DescriptorProto, name: str) -> None:
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


def _add(msg: descriptor_pb2.DescriptorProto, name: str, number: int, *, optional: bool = False, **kind: Any) -> None:
    field = msg.field.add(
        name=name, number=number, label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL, json_name=name, **kind
    )
    if optional:
        msg.oneof_decl.add(name=f"_{name}")
        field.proto3_optional = True
        field.oneof_index = len(msg.oneof_decl) - 1


_STRING = descriptor_pb2.FieldDescriptorProto.TYPE_STRING


@functools.cache
def _v0_4() -> dict[str, type[Message]]:
    """The 0.4 ``PedigreeSet``, ``Pedigree`` and ``Condition`` classes."""
    return _classes(_schema(_V0_4)[0])


@functools.cache
def _pre_0_4() -> dict[str, type[Message]]:
    """The pre-0.4 classes: 0.4 with each entry's name and inheritance back in place of its id."""
    fdp, by_name = _schema(_PRE_0_4)
    _named_condition(by_name["Condition"], _PRE_0_4)
    support = by_name["Support"]
    _drop(support, "condition_id")
    _add(support, "condition", 3, optional=True, type=_STRING)
    _drop(by_name["PedigreeSet"], "conditions")
    return _classes(fdp)


@functools.cache
def _digest_classes() -> dict[str, type[Message]]:
    """The current schema with each person's marks replaced by pre-0.4 conditions, for the stored-layout digest.

    Built from the live schema, so a field added to it reaches the digest (and its classification test). The
    ``Condition`` also has ``kind`` and ``grade``, which no pre-0.4 record has: ``legacy_pedigree`` writes them for an
    entry a pre-0.4 condition could not express, and leaves them unset otherwise.
    """
    fdp, by_name = _moved(pb.DESCRIPTOR.serialized_pb, _DIGEST)
    (condition,) = [m for m in _schema(_DIGEST)[0].message_type if m.name == "Condition"]
    _named_condition(condition, _DIGEST)
    _add(condition, "kind", 10, type=_STRING)
    _add(condition, "grade", 11, optional=True, type=_STRING)
    fdp.message_type.add().CopyFrom(condition)
    individual = by_name["Individual"]
    _drop(individual, "marks")
    individual.field.add(
        name="conditions",
        number=5,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=f".{_DIGEST}.Condition",
        json_name="conditions",
    )
    return _classes(fdp)


def legacy_pedigree(p: pb.Pedigree, key_entries: Iterable[pb.KeyEntry]) -> Message:
    """``p`` in the pre-0.4 encoding: each entry ``p``'s people show, as a condition carrying its entry's facts.

    What a person shows (``Key.shown``: marks, then matched genotype entries) becomes their conditions in that order:
    the entry's text as the name, a phenotype mark's status and the entry's inheritance. A genotype or other entry adds
    its kind, and a graded mark its grade, fields a pre-0.4 record never sets; so a pedigree of phenotype marks with
    standard statuses encodes as it did before the key. Marks do not carry over; every other field does, and the
    digest clears what layout does not read. The stored-layout digest serializes this form, so an entry's id never
    enters it.

    Raises:
        UndeclaredKeyEntryError: a mark names an undeclared entry.
    """
    key = _key_entries.Key(key_entries)
    classes = _digest_classes()
    out: Any = classes["Pedigree"].FromString(p.SerializeToString())
    out.DiscardUnknownFields()  # the marks
    for ind, legacy_ind in zip(p.individuals, out.individuals, strict=True):
        for shown in key.shown(ind):
            c = legacy_ind.conditions.add(name=shown.text)
            if shown.status is not None:
                c.status = shown.status
            if shown.inheritance is not None:
                c.inheritance = shown.inheritance
            if shown.kind != _key_entries.PHENOTYPE:
                c.kind = shown.kind
            if shown.grade is not None:
                c.grade = shown.grade
    return out


def _parse(text: str, set_cls: type[Message], pedigree_cls: type[Message], *, json: bool) -> Any:
    """``text`` as a ``set_cls``, or a bare ``pedigree_cls`` wrapped as a one-pedigree set; None if it is neither."""
    parse = json_format.Parse if json else text_format.Parse
    try:
        return parse(text, set_cls())
    except (json_format.ParseError, text_format.ParseError):
        pass
    try:
        pedigree = parse(text, pedigree_cls())
    except (json_format.ParseError, text_format.ParseError):
        return None
    out: Any = set_cls()
    out.pedigrees.add().CopyFrom(pedigree)
    return out


def _to_0_4(old: Any) -> Any:
    """A pre-0.4 set in the 0.4 shape: one declaration per distinct condition name, by first appearance."""
    stated: dict[str, set[int]] = {}
    for p in old.pedigrees:
        for ind in p.individuals:
            names = [c.name for c in ind.conditions]
            if len(set(names)) != len(names):
                dup = sorted({n for n in names if names.count(n) > 1})
                raise MigrationError(
                    f"individual ({ind.generation}, {ind.index}) has several entries of condition(s) {dup}; "
                    "a person has one entry per condition, so keep the one that holds"
                )
            for c in ind.conditions:
                modes = stated.setdefault(c.name, set())
                if c.HasField("inheritance"):
                    modes.add(c.inheritance)
    for name, modes in stated.items():
        if len(modes) > 1:
            names = sorted(pb.Inheritance.Name(m) for m in modes)
            raise MigrationError(f"entries of condition {name!r} disagree on inheritance: {names}")
    taken = {c.id for c in old.citations} | {locus.id for locus in old.loci}
    fresh = (f"k{i}" for i in itertools.count(1) if f"k{i}" not in taken)
    ids = {name: next(fresh) for name in stated}
    new: Any = _v0_4()["PedigreeSet"].FromString(old.SerializeToString())
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
            if s_new.field in _DECLARATION_PATHS:  # now describes the one declaration, not the people it scoped
                del s_new.individuals[:]
                del s_new.matings[:]
    return new


def _from_0_4(old: Any) -> pb.PedigreeSet:
    """A 0.4 set in the current shape: each condition a phenotype entry, each entry a mark."""
    new = pb.PedigreeSet.FromString(old.SerializeToString())
    new.DiscardUnknownFields()  # the conditions, the entries and supports' condition_id
    for d in old.conditions:
        e = new.key_entries.add(id=d.id, text=d.name)
        e.phenotype.SetInParent()
        if d.HasField("inheritance"):
            e.phenotype.inheritance = d.inheritance
    for p_old, p_new in zip(old.pedigrees, new.pedigrees, strict=True):
        for i_old, i_new in zip(p_old.individuals, p_new.individuals, strict=True):
            for c in i_old.conditions:
                m = i_new.marks.add(key_id=c.condition_id, status=c.status)
                if c.HasField("onset_age"):
                    m.onset_age = c.onset_age
        for s_old, s_new in zip(p_old.supports, p_new.supports, strict=True):
            if s_old.HasField("condition_id"):
                s_new.key_id = s_old.condition_id
            s_new.field = _PATHS.get(s_new.field, s_new.field)
    return new


def _references(old: Any) -> bool:
    """Whether every entry of a set parsed as 0.4 names a condition, as 0.4 requires.

    A pre-0.4 entry with neither a name nor an inheritance (the unnamed condition) also parses as a 0.4 entry, with no
    ``condition_id``; such a record is pre-0.4.
    """
    return all(c.condition_id for p in old.pedigrees for ind in p.individuals for c in ind.conditions)


def _migrate(text: str, *, json: bool) -> pb.PedigreeSet:
    v0_4, pre = _v0_4(), _pre_0_4()
    if (current := _parse(text, pb.PedigreeSet, pb.Pedigree, json=json)) is not None:
        new: pb.PedigreeSet = current
    elif (old := _parse(text, v0_4["PedigreeSet"], v0_4["Pedigree"], json=json)) is not None and _references(old):
        new = _from_0_4(old)
    elif (older := _parse(text, pre["PedigreeSet"], pre["Pedigree"], json=json)) is not None:
        new = _from_0_4(_to_0_4(older))
    else:
        raise MigrationError("not a pedigree set or pedigree in the current, 0.4 or pre-0.4 schema")
    _validate.validate_set(new)
    return new


def migrate_pbtxt(text: str) -> pb.PedigreeSet:
    """A ``PedigreeSet`` or bare ``Pedigree`` in pbtxt, in any schema since conditions, as a validated current set.

    Raises:
        MigrationError: see ``MigrationError``; or the text is a set or pedigree in no known schema.
        IntegrityError: the migrated record is not valid IR.
    """
    return _migrate(text, json=False)


def migrate_json(text: str) -> pb.PedigreeSet:
    """``migrate_pbtxt`` for a proto3-JSON record."""
    return _migrate(text, json=True)
