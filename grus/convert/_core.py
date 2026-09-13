"""The shared graph core every importer reduces to (docs/design/convert.md, "The shared graph core").

A format module parses its file into ``Person`` records (opaque ids, parent ids, per-person facts) plus
format-level extras; ``build_set`` groups them into pedigrees, derives one ``Mating`` per distinct parent
pair, synthesises the ``Position`` identity (kinship2-style ``kindepth`` with couple alignment for the
generation; source order within the generation for the index), and validates the result through
``grus.ir`` so no importer ever returns an invalid IR.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from grus import ir
from grus.models import pedigree_pb2 as pb


class PedigreeImportError(ValueError):
    """The source file is malformed or references an unknown individual. Fails loud, names the offender."""


@dataclass
class Person:
    """One individual as a source format states it. ``id`` is the source's opaque identifier.

    ``father`` / ``mother`` are source ids or ``None`` for missing. Everything else is optional per-person
    fact the format could express; an importer sets only what its source states.
    """

    id: str
    gender: pb.Gender
    father: str | None = None
    mother: str | None = None
    family: str = ""
    conditions: list[pb.Condition] = field(default_factory=list)
    deceased: bool = False
    proband: bool = False
    consultand: bool = False
    documented_evaluation: bool = False
    sex_assigned_at_birth: pb.SexAssignedAtBirth | None = None
    reproductive_outcome: pb.ReproductiveOutcome | None = None
    reproductive_role: pb.ReproductiveRole | None = None
    count: int | None = None
    count_unspecified: bool = False
    annotations: list[pb.Annotation] = field(default_factory=list)
    # Offspring-edge facts (about this person as a child of its parents).
    twin_group: int | None = None
    twin_type: pb.ZygosityType = pb.ZYGOSITY_TYPE_UNSPECIFIED
    parentage: pb.Parentage | None = None
    adoption: pb.Adoption | None = None
    keep_external_id: bool = True


@dataclass
class Extras:
    """Format-level facts that are about matings, not people."""

    # (partner id, partner id) -> consanguineous; also declares a childless couple when no child names the pair.
    consanguineous: set[frozenset[str]] = field(default_factory=set)
    childless: dict[frozenset[str], pb.Childlessness] = field(default_factory=dict)
    status: dict[frozenset[str], pb.RelationshipStatus] = field(default_factory=dict)
    spouses: set[frozenset[str]] = field(default_factory=set)  # explicit couples with no children in the file
    annotations: dict[frozenset[str], list[pb.Annotation]] = field(default_factory=dict)  # mating-level text
    labels: dict[str, list[pb.Label]] = field(default_factory=dict)  # family -> labels


def build_set(
    people: list[Person], extras: Extras | None = None, *, provenance: pb.Provenance | None = None
) -> pb.PedigreeSet:
    """Group ``people`` by family, build each ``Pedigree``, validate, return the set (families in source order)."""
    extras = extras or Extras()
    by_family: dict[str, list[Person]] = defaultdict(list)
    for person in people:
        by_family[person.family].append(person)
    ps = pb.PedigreeSet()
    for family, members in by_family.items():
        ps.pedigrees.append(build_pedigree(members, extras, family_id=family))
    if provenance is not None:
        ps.provenance.CopyFrom(provenance)
    ir.validate_set(ps)
    return ps


def build_pedigree(people: list[Person], extras: Extras, *, family_id: str = "") -> pb.Pedigree:
    """Build one ``Pedigree`` from the members of one family (see module doc)."""
    ids = [p.id for p in people]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise PedigreeImportError(f"duplicate individual id(s) in family {family_id!r}: {dup}")
    known = set(ids)
    for p in people:
        for parent in (p.father, p.mother):
            if parent is not None and parent not in known:
                raise PedigreeImportError(
                    f"individual {p.id!r} names parent {parent!r}, which is not in family {family_id!r}"
                )
        if p.father is not None and p.father == p.mother:
            raise PedigreeImportError(f"individual {p.id!r} names the same id {p.father!r} as both parents")

    # One mating per distinct parent pair, offspring in source order. Key = (father, mother) with None allowed
    # on one side (single-parent). Explicit childless couples from extras are appended after.
    matings: dict[tuple[str | None, str | None], list[Person]] = {}
    for p in people:
        if p.father is None and p.mother is None:
            continue
        matings.setdefault((p.father, p.mother), []).append(p)
    declared = (
        extras.spouses | extras.consanguineous | set(extras.childless) | set(extras.status) | set(extras.annotations)
    )
    for pair in sorted((x for x in declared if len(x) == 2), key=sorted):
        a, b = sorted(pair)
        if a not in known or b not in known:
            continue  # belongs to another family
        if not any({k[0], k[1]} == {a, b} for k in matings):
            # Orientation unknown for a childless couple: father slot gets the man if the genders say so.
            ga = next(p.gender for p in people if p.id == a)
            first, second = (a, b) if ga == pb.GENDER_MAN else (b, a)
            matings[(first, second)] = []

    generation = _rank(people, matings)
    index = _index(people, generation)
    pos = {p.id: pb.Position(generation=generation[p.id], index=index[p.id]) for p in people}

    ped = pb.Pedigree()
    if family_id:
        ped.id = family_id
        ped.labels.append(pb.Label(text=family_id, kind=pb.LABEL_KIND_FAMILY))
    for label in extras.labels.get(family_id, []):
        ped.labels.append(label)
    for p in people:
        ind = ped.individuals.add(generation=pos[p.id].generation, index=pos[p.id].index, gender=p.gender)
        if p.sex_assigned_at_birth is not None:
            ind.sex_assigned_at_birth = p.sex_assigned_at_birth
        ind.conditions.extend(p.conditions)
        ind.deceased = p.deceased
        ind.proband = p.proband
        ind.consultand = p.consultand
        ind.documented_evaluation = p.documented_evaluation
        if p.reproductive_outcome is not None:
            ind.reproductive_outcome = p.reproductive_outcome
        if p.reproductive_role is not None:
            ind.reproductive_role = p.reproductive_role
        if p.count is not None:
            ind.count = p.count
        ind.count_unspecified = p.count_unspecified
        ind.annotations.extend(p.annotations)
        if p.keep_external_id:
            ind.external_id = p.id
    for (father, mother), kids in matings.items():
        m = ped.matings.add()
        # The IR puts a lone drawn parent in partner_a (validate enforces it), whichever column it came from.
        present = [x for x in (father, mother) if x is not None]
        if present:
            m.partner_a.CopyFrom(pos[present[0]])
        if len(present) == 2:
            m.partner_b.CopyFrom(pos[present[1]])
        pair = frozenset(x for x in (father, mother) if x is not None)
        if pair in extras.consanguineous:
            m.consanguineous = True
        if pair in extras.status:
            m.status = extras.status[pair]
        if pair in extras.childless:
            m.childlessness = extras.childless[pair]
        m.annotations.extend(extras.annotations.get(pair, []))
        for kid in kids:
            off = m.offspring.add(child=pos[kid.id])
            if kid.twin_group is not None:
                off.twin_group = kid.twin_group
                off.twin_type = kid.twin_type or pb.ZYGOSITY_TYPE_UNKNOWN
            if kid.parentage is not None:
                off.parentage = kid.parentage
            if kid.adoption is not None:
                off.adoption = kid.adoption
    return ped


def _rank(people: list[Person], matings: dict[tuple[str | None, str | None], list[Person]]) -> dict[str, int]:
    """1-based generation: longest founder path, then level every two-partner couple (aligned ``kindepth``)."""
    depth = {p.id: 0 for p in people}
    parents = {p.id: [x for x in (p.father, p.mother) if x is not None] for p in people}

    def relax_down() -> None:
        for _ in range(len(people) + 1):
            changed = False
            for pid, par in parents.items():
                if par:
                    base = max(depth[x] for x in par) + 1
                    if base > depth[pid]:
                        depth[pid] = base
                        changed = True
            if not changed:
                return
        raise PedigreeImportError("ancestry cycle: an individual is its own ancestor")

    ancestors_cache: dict[str, frozenset[str]] = {}

    def ancestors(pid: str) -> frozenset[str]:
        if pid not in ancestors_cache:
            acc: set[str] = set()
            for x in parents[pid]:
                acc.add(x)
                acc |= ancestors(x)
            ancestors_cache[pid] = frozenset(acc)
        return ancestors_cache[pid]

    couples = [(a, b) for (a, b) in matings if a is not None and b is not None]
    relax_down()
    for _ in range(len(people) ** 2 + 1):
        worst = max(couples, key=lambda c: abs(depth[c[0]] - depth[c[1]]), default=None)
        if worst is None or depth[worst[0]] == depth[worst[1]]:
            break
        shallow, deep = sorted(worst, key=lambda x: depth[x])
        delta = depth[deep] - depth[shallow]
        for node in ancestors(shallow) | {shallow}:
            depth[node] += delta
        relax_down()
    return {pid: d + 1 for pid, d in depth.items()}


def _index(people: list[Person], generation: dict[str, int]) -> dict[str, int]:
    """1-based index within each generation, in source order."""
    counter: dict[int, int] = defaultdict(int)
    out: dict[str, int] = {}
    for p in people:
        counter[generation[p.id]] += 1
        out[p.id] = counter[generation[p.id]]
    return out
