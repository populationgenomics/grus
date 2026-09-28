"""Graph-invariant validation for the pedigree IR.

Two layers, split by what protobuf can express (docs/design/ir.md, "Validation"):

* field-local / single-message rules are protovalidate CEL options on the `.proto` (``Position``
  components >= 1, ``gender`` and ``Condition.status`` are real values not the zero sentinel,
  ``twin_type`` set iff ``twin_group``); and
* cross-message graph invariants — referential integrity, ``Position`` uniqueness, and generation order
  (each child below its parents, siblings on one row) — cannot be expressed field-locally and live here.

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
    non-distinct mating partners, or a generation-order violation (a child not below one of its parents, or
    one mating's offspring on different generations — which also rules out an ancestry cycle). Several
    probands are allowed: a family ascertained through more than one member has more than one (Bennett), and
    figures draw each arrow.
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
    known, and references before generation order so every edge endpoint is a real node.

    Raises:
        protovalidate.ValidationError: a field-local / single-message rule failed.
        IntegrityError: a cross-message graph invariant failed.
    """
    protovalidate.validate(p)
    _check_mating_shape(p)
    positions = _check_unique_positions(p)
    _check_references(p, positions)
    _check_reproductive_outcome_terminal(p)
    _check_generation_order(p)
    _check_supports(p, positions)


def validate_set(ps: pb.PedigreeSet) -> None:
    """Fail loud unless every ``Pedigree`` in the set is well-formed. An empty set is valid (K=0).

    Each ``Pedigree`` is validated **independently** — ``Position`` uniqueness and referential integrity are
    per-pedigree invariants (docs/design/ir.md, "Figure scope"), so distinct families reusing ``I-1``/``II-1``
    never collide. A per-pedigree failure is re-raised as an ``IntegrityError`` naming the offending pedigree
    (its index and labels), chaining the original error.

    Citations are set-level: their ids are unique across the set, and every evidence or support id — the set's
    and each pedigree's — resolves to one (docs/design/ir.md, "Citations").

    Raises:
        IntegrityError: some ``Pedigree`` in the set failed field-local or graph validation, or a citation is
            malformed, duplicated or dangling.
    """
    ids = _check_citations(ps)
    for i, p in enumerate(ps.pedigrees):
        labels = [label.text for label in p.labels]
        tag = f"pedigrees[{i}]" + (f" (labels={labels})" if labels else "")
        try:
            validate(p)
        except (protovalidate.ValidationError, IntegrityError) as e:
            raise IntegrityError(f"{tag} is not a valid pedigree: {e}") from e
        for ref in p.evidence:
            if ref not in ids:
                raise IntegrityError(f"{tag}: evidence cites unknown citation {ref!r}")
        for j, support in enumerate(p.supports):
            for ref in support.citations:
                if ref not in ids:
                    raise IntegrityError(f"{tag}: supports[{j}] ({support.field}) cites unknown citation {ref!r}")


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


def _check_generation_order(p: pb.Pedigree) -> None:
    """Each child is below each of its parents, and one mating's offspring share a generation.

    ``generation`` is the drawn row, numbered top-down, so a child's generation exceeds every partner's — by
    one ordinarily, by more when the figure draws the child lower (beside half-siblings whose other parent is a
    generation down, or across omitted generations) — and a sibship hangs from one sibling line on one row.
    Because generation strictly increases along every parent -> child edge, no individual can be its own
    ancestor: this subsumes an acyclicity check.
    """
    for i, m in enumerate(p.matings):
        generations = sorted({off.child.generation for off in m.offspring})
        if len(generations) > 1:
            raise IntegrityError(
                f"matings[{i}] offspring span generations {generations}; one mating's children share a generation"
            )
        for role, ref in zip(("partner_a", "partner_b"), _partners(m), strict=False):
            for off in m.offspring:
                if off.child.generation <= ref.generation:
                    raise IntegrityError(
                        f"matings[{i}] offspring {_key(off.child)} is not below its parent {role} {_key(ref)}; "
                        "a child's generation must exceed each parent's"
                    )


# The closed list of paths a ``Support.field`` may name (docs/design/ir.md, "Citations"), by what it scopes:
# an individual's fields and a condition's (``condition.*``, naming the condition) scope people; a mating's fields
# scope couples; an offspring edge's fields scope the child; ``labels`` is the whole pedigree's. The one place the
# list lives; a test checks each path against the schema.
_INDIVIDUAL_FIELDS = (
    "gender",
    "deceased",
    "proband",
    "consultand",
    "reproductive_outcome",
    "reproductive_role",
    "count",
    "count_unspecified",
    "documented_evaluation",
    "sex_assigned_at_birth",
    "external_id",
    "annotations",
)
_CONDITION_FIELDS = ("condition.status", "condition.name", "condition.inheritance", "condition.onset_age")
_MATING_FIELDS = ("consanguineous", "status", "childlessness", "annotations")
_OFFSPRING_FIELDS = ("twin_group", "twin_type", "parentage", "adoption")
# Path -> the kinds of thing it may describe. ``annotations`` is both an individual's and a mating's; the support's
# scope says which (couples for a mating's, people for an individual's).
SUPPORT_FIELDS: dict[str, frozenset[str]] = {}
for _kind, _paths in (
    ("individual", _INDIVIDUAL_FIELDS),
    ("condition", _CONDITION_FIELDS),
    ("mating", _MATING_FIELDS),
    ("offspring", _OFFSPRING_FIELDS),
    ("pedigree", ("labels",)),
):
    for _path in _paths:
        SUPPORT_FIELDS[_path] = SUPPORT_FIELDS.get(_path, frozenset()) | {_kind}


def _check_citations(ps: pb.PedigreeSet) -> set[str]:
    """The set's citation ids, once each citation is well-formed, the ids are unique and the set's evidence resolves."""
    ids: set[str] = set()
    for i, c in enumerate(ps.citations):
        try:
            protovalidate.validate(c)
        except protovalidate.ValidationError as e:
            raise IntegrityError(f"citations[{i}] ({c.id!r}) is malformed: {e}") from e
        if c.id in ids:
            raise IntegrityError(f"citations[{i}]: citation id {c.id!r} is used twice; ids are unique in the set")
        ids.add(c.id)
    for ref in ps.evidence:
        if ref not in ids:
            raise IntegrityError(f"set evidence cites unknown citation {ref!r}")
    return ids


def _mating_ref_key(m: pb.Mating | pb.MatingRef) -> frozenset[Key]:
    """A couple (or lone parent) as its partners' positions, unordered: how a ``MatingRef`` names a ``Mating``."""
    return frozenset(_key(getattr(m, role)) for role in ("partner_a", "partner_b") if m.HasField(role))


def _check_supports(p: pb.Pedigree, positions: set[Key]) -> None:
    """Each support names a listed field, a scope that exists and suits it, and a condition iff it needs one."""
    matings = {_mating_ref_key(m) for m in p.matings}
    children = {_key(off.child) for m in p.matings for off in m.offspring}
    by_key = {(ind.generation, ind.index): ind for ind in p.individuals}
    for j, s in enumerate(p.supports):
        where = f"supports[{j}] ({s.field!r})"
        kinds = SUPPORT_FIELDS.get(s.field)
        if kinds is None:
            raise IntegrityError(f"{where}: unknown field; a support names one of {sorted(SUPPORT_FIELDS)}")
        people = [_key(pos) for pos in s.individuals]
        for key in people:
            if key not in positions:
                raise IntegrityError(f"{where}: individual {key} is not in the pedigree")
        for ref in s.matings:
            if _mating_ref_key(ref) not in matings:
                raise IntegrityError(f"{where}: no mating of {sorted(_mating_ref_key(ref))} in the pedigree")
        if s.matings and people:
            raise IntegrityError(f"{where}: scopes both individuals and matings; a support scopes one kind")
        if s.matings and "mating" not in kinds:
            raise IntegrityError(f"{where}: scopes matings, but {s.field} is not a mating field")
        if people and not kinds - {"mating", "pedigree"}:
            raise IntegrityError(f"{where}: scopes individuals, but {s.field} is a {'/'.join(sorted(kinds))} field")
        # The one kind this support describes: only ``annotations`` has two, and couples in scope make it a mating's.
        kind = "mating" if s.matings else ("individual" if "individual" in kinds else next(iter(kinds)))
        if kind == "offspring":
            for key in people:
                if key not in children:
                    raise IntegrityError(f"{where}: {key} is no one's child; an offspring field scopes the child")
        if (kind == "condition") != s.HasField("condition"):
            raise IntegrityError(f"{where}: names a condition iff the field is condition.*")
        if kind == "condition":
            scope = [by_key[key] for key in people] or list(p.individuals)
            if not any(c.name == s.condition for ind in scope for c in ind.conditions):
                raise IntegrityError(f"{where}: no one in scope has condition {s.condition!r}")
