"""Graph-invariant validation for the pedigree IR.

Two layers, split by what protobuf can express (docs/design/ir.md, "Validation"):

* field-local / single-message rules are protovalidate CEL options on the `.proto` (``Position``
  components >= 1, ``gender`` and ``Condition.status`` are real values not the zero sentinel,
  ``twin_type`` set iff ``twin_group``); and
* cross-message graph invariants — referential integrity, ``Position`` uniqueness,
  and acyclicity — cannot be expressed field-locally and live here.

`validate` runs both and fails loud. Identity and the cross-message reference key are the ``Position``
``(generation, index)`` pair; a ``Mating`` may have one partner (a single drawn parent) or two.
"""

from __future__ import annotations

import protovalidate

from grus.models import pedigree_pb2 as pb

# The hashable form of a Position — the identity + reference key within one pedigree.
Key = tuple[int, int]


class IntegrityError(Exception):
    """A cross-message graph invariant was violated.

    Distinct from ``protovalidate.ValidationError`` (a field-local rule): this covers a dangling
    ``Position`` reference (a mating partner or an offspring child), a duplicate ``(generation, index)``,
    non-distinct mating partners, or an ancestry cycle (an individual that is its own ancestor — the
    tier-1 renderer requires an acyclic graph). Several probands are allowed: a family ascertained through
    more than one member has more than one (Bennett), and figures draw each arrow.
    """


def _key(pos: pb.Position) -> Key:
    return (pos.generation, pos.index)


def _partners(m: pb.Mating) -> list[pb.Position]:
    """A mating's drawn partners: each of ``partner_a`` / ``partner_b`` that is set.

    Two partners is an ordinary couple, one is a single drawn parent, none is a *founder sibship* (a
    partnerless mating grouping siblings whose parent couple is undrawn).
    """
    partners: list[pb.Position] = []
    if m.HasField("partner_a"):
        partners.append(m.partner_a)
    if m.HasField("partner_b"):
        partners.append(m.partner_b)
    return partners


def validate(p: pb.Pedigree) -> None:
    """Fail loud unless ``p`` is a well-formed pedigree IR.

    Runs protovalidate (which recurses into every nested message) for the field-local rules, then the
    graph invariants below. Order matters: uniqueness is checked before references so the position set is
    known, and references before acyclicity so every edge endpoint is a real node.

    Raises:
        protovalidate.ValidationError: a field-local / single-message rule failed.
        IntegrityError: a cross-message graph invariant failed.
    """
    protovalidate.validate(p)
    _check_mating_shape(p)
    positions = _check_unique_positions(p)
    _check_references(p, positions)
    _check_reproductive_outcome_terminal(p)
    _check_acyclic(p)


def validate_set(ps: pb.PedigreeSet) -> None:
    """Fail loud unless every ``Pedigree`` in the set is well-formed. An empty set is valid (K=0).

    Each ``Pedigree`` is validated **independently** — ``Position`` uniqueness and referential integrity are
    per-pedigree invariants (docs/design/ir.md, "Figure scope"), so distinct families reusing ``I-1``/``II-1``
    never collide. A per-pedigree failure is re-raised as an ``IntegrityError`` naming the offending pedigree
    (its index and labels), chaining the original error.

    Raises:
        IntegrityError: some ``Pedigree`` in the set failed field-local or graph validation.
    """
    for i, p in enumerate(ps.pedigrees):
        try:
            validate(p)
        except (protovalidate.ValidationError, IntegrityError) as e:
            labels = [label.text for label in p.labels]
            tag = f" (labels={labels})" if labels else ""
            raise IntegrityError(f"pedigrees[{i}]{tag} is not a valid pedigree: {e}") from e


def _check_mating_shape(p: pb.Pedigree) -> None:
    """Each mating is a couple (two partners), a single drawn parent (``partner_a`` only), or a founder sibship.

    A *founder sibship* is a partnerless mating (both partners absent) grouping siblings whose parent couple is
    undrawn; it must group **>= 2** offspring (a single parentless individual is just a founder, not a sibship)
    and carry no stray partner. ``partner_b`` set without ``partner_a`` is malformed (the lone drawn parent is
    always ``partner_a``).
    """
    for i, m in enumerate(p.matings):
        if not m.HasField("partner_a"):
            if m.HasField("partner_b"):
                raise IntegrityError(f"matings[{i}] sets partner_b without partner_a; a lone parent is partner_a")
            if len(m.offspring) < 2:
                raise IntegrityError(
                    f"matings[{i}] is a partnerless (founder) sibship with {len(m.offspring)} offspring; "
                    "a founder sibship needs >= 2 siblings"
                )


def _check_unique_positions(p: pb.Pedigree) -> set[Key]:
    """Return the set of individual positions, raising if any ``(generation, index)`` is repeated."""
    seen: set[Key] = set()
    duplicates: set[Key] = set()
    for ind in p.individuals:
        key = (ind.generation, ind.index)
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    if duplicates:
        raise IntegrityError(f"duplicate (generation, index): {sorted(duplicates)}")
    return seen


def _check_references(p: pb.Pedigree, positions: set[Key]) -> None:
    """Every mating partner and offspring child must resolve to an individual; two partners distinct."""
    for i, m in enumerate(p.matings):
        partners = _partners(m)
        for role, ref in zip(("partner_a", "partner_b"), partners, strict=False):
            if _key(ref) not in positions:
                raise IntegrityError(f"matings[{i}].{role} references unknown position {_key(ref)}")
        if len(partners) == 2 and _key(partners[0]) == _key(partners[1]):
            raise IntegrityError(f"matings[{i}] partners must be distinct, both {_key(partners[0])}")
        for off in m.offspring:
            if _key(off.child) not in positions:
                raise IntegrityError(f"matings[{i}] offspring references unknown position {_key(off.child)}")


# Pregnancy / loss outcomes: the individual is a terminal node in the drawn pedigree — it never reproduces.
_TERMINAL_OUTCOMES = frozenset(
    {
        pb.REPRODUCTIVE_OUTCOME_PREGNANCY,
        pb.REPRODUCTIVE_OUTCOME_STILLBIRTH,
        pb.REPRODUCTIVE_OUTCOME_MISCARRIAGE,
        pb.REPRODUCTIVE_OUTCOME_TERMINATION,
        pb.REPRODUCTIVE_OUTCOME_ECTOPIC,
    }
)


def _check_reproductive_outcome_terminal(p: pb.Pedigree) -> None:
    """Reject a pregnancy or loss that appears as a mating partner.

    A pregnancy / stillbirth / SAB / TOP / ectopic never reproduces, so it may not be a mating partner and
    therefore has no offspring. Adoption and parentage are deliberately NOT constrained against each other: an
    adopted-out child is the couple's biological child raised elsewhere.
    """
    partners = {_key(ref) for m in p.matings for ref in _partners(m)}
    for ind in p.individuals:
        if ind.reproductive_outcome in _TERMINAL_OUTCOMES and (ind.generation, ind.index) in partners:
            raise IntegrityError(
                f"individual {(ind.generation, ind.index)} is a pregnancy/loss "
                f"({pb.ReproductiveOutcome.Name(ind.reproductive_outcome)}) but is a mating partner; "
                "a pregnancy/loss node cannot reproduce"
            )


def _check_acyclic(p: pb.Pedigree) -> None:
    """No individual may be its own ancestor (the parent->child graph must be a DAG).

    Kahn's algorithm: if a topological pass cannot consume every node, the residue lies on one or more
    ancestry cycles. Covers the self-loop case (an individual listed as offspring of its own mating).
    """
    children: dict[Key, list[Key]] = {(ind.generation, ind.index): [] for ind in p.individuals}
    indegree: dict[Key, int] = {(ind.generation, ind.index): 0 for ind in p.individuals}
    edges: set[tuple[Key, Key]] = set()
    for m in p.matings:
        for off in m.offspring:
            child = _key(off.child)
            for parent in _partners(m):
                edge = (_key(parent), child)
                if edge in edges:
                    continue
                edges.add(edge)
                children[_key(parent)].append(child)
                indegree[child] += 1

    stack = [node for node, deg in indegree.items() if deg == 0]
    consumed = 0
    while stack:
        node = stack.pop()
        consumed += 1
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                stack.append(child)

    if consumed != len(indegree):
        cyclic = sorted(node for node, deg in indegree.items() if deg > 0)
        raise IntegrityError(f"ancestry cycle: individual(s) are their own ancestor: {cyclic}")
