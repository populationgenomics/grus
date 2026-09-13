"""The structural semantic diff of two pedigree IRs (docs/design/eval.md, "the metric stack").

This is the primary automated quality signal: compare IR vs IR' by *meaning*, never by drawn
position. It is fully layout-invariant — individuals are matched by relational role (not by their
as-drawn ``Position``, which may differ between a figure and its re-extraction), and only sets, edges,
and birth-order-within-sibship are compared. Failures are localized to a specific individual/attribute
or mating, so precision/recall decompose per element rather than collapsing to one opaque score.

Identity and the cross-message reference key are the ``Position`` ``(generation, index)`` pair; a subject
in a ``Mismatch`` is rendered ``"<gen>-<idx>"`` (e.g. ``"2-1"``). Treat ``a`` as the reference, ``b`` the
candidate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from grus.models import pedigree_pb2 as pb

# The hashable identity + reference key of an individual within one pedigree.
Key = tuple[int, int]


def _key(pos: pb.Position) -> Key:
    return (pos.generation, pos.index)


def _label(key: Key) -> str:
    """Render a position key as a Mismatch subject, e.g. ``(2, 1)`` -> ``"2-1"``."""
    return f"{key[0]}-{key[1]}"


@dataclass(frozen=True)
class Mismatch:
    """One localized semantic discrepancy between the two IRs.

    Attributes:
        subject: the entity that differs — an individual's position ``"<gen>-<idx>"`` (A-side) for a
            per-person attribute, or a mating key ``"mating(a x b)"`` (A-side partner positions, sorted)
            for a relationship attribute.
        field: the attribute that differs, e.g. ``"gender"``, ``"conditions"``, ``"consanguineous"``.
        a_value: the A-side (reference) value, rendered as an enum name or ``str``.
        b_value: the B-side (candidate) value, rendered the same way.
    """

    subject: str
    field: str
    a_value: str
    b_value: str


@dataclass
class Diff:
    """Structural difference between two pedigree IRs.

    ``matched`` are the A-side positions paired to a B individual (a bijection); ``only_in_a`` /
    ``only_in_b`` are the unpaired individuals on each side (position strings). ``mismatches`` are
    localized attribute and relationship discrepancies among matched entities. precision/recall are over
    individual identity recovery (A = reference); per-attribute fidelity lives in ``mismatches``.
    """

    matched: list[str]
    mismatches: list[Mismatch]
    only_in_a: list[str]
    only_in_b: list[str]

    def precision(self) -> float:
        """Fraction of candidate (B) individuals that matched a reference individual; 1.0 if B empty."""
        denom = len(self.matched) + len(self.only_in_b)
        return 1.0 if denom == 0 else len(self.matched) / denom

    def recall(self) -> float:
        """Fraction of reference (A) individuals recovered in the candidate; 1.0 if A empty."""
        denom = len(self.matched) + len(self.only_in_a)
        return 1.0 if denom == 0 else len(self.matched) / denom


@dataclass
class PedigreePair:
    """A matched (reference, candidate) pedigree within one figure, plus its per-pedigree diff.

    ``a_index`` / ``b_index`` are positions in the reference / candidate ``PedigreeSet.pedigrees``;
    ``a_title`` / ``b_title`` are the as-drawn labels joined (for reporting); ``diff`` is the structural
    diff of that single family.
    """

    a_index: int
    b_index: int
    a_title: str
    b_title: str
    diff: Diff


@dataclass
class SetDiff:
    """Structural difference between two ``PedigreeSet``s — a figure's 0..N pedigrees (docs/design/ir.md).

    ``matched`` pairs each reference pedigree to the candidate pedigree representing the same family;
    ``only_in_a`` are reference pedigrees with no candidate (missing families), ``only_in_b`` are spurious
    candidate pedigrees. precision/recall are exposed at family granularity (were the right families
    recovered) and individual granularity (aggregate identity recovery across every family; A = reference).
    """

    matched: list[PedigreePair]
    only_in_a: list[int]
    only_in_b: list[int]
    a_individuals: int
    b_individuals: int

    def family_precision(self) -> float:
        """Fraction of candidate families that matched a reference family; 1.0 if there are no candidates."""
        denom = len(self.matched) + len(self.only_in_b)
        return 1.0 if denom == 0 else len(self.matched) / denom

    def family_recall(self) -> float:
        """Fraction of reference families recovered in the candidate; 1.0 if there are no reference families."""
        denom = len(self.matched) + len(self.only_in_a)
        return 1.0 if denom == 0 else len(self.matched) / denom

    def individual_precision(self) -> float:
        """Matched individuals across all matched families over every candidate individual; 1.0 if B empty."""
        return 1.0 if self.b_individuals == 0 else self._matched_individuals() / self.b_individuals

    def individual_recall(self) -> float:
        """Matched individuals across all matched families over every reference individual; 1.0 if A empty."""
        return 1.0 if self.a_individuals == 0 else self._matched_individuals() / self.a_individuals

    def _matched_individuals(self) -> int:
        return sum(len(pair.diff.matched) for pair in self.matched)


@dataclass
class _Relations:
    """Layout-invariant relations derived from a pedigree's matings (no positional data)."""

    by_pos: dict[Key, pb.Individual]
    parents: dict[Key, frozenset[Key]]  # child -> its parents (1 or 2)
    partners: dict[Key, frozenset[Key]]  # individual -> mates
    children: dict[Key, frozenset[Key]]  # individual -> children across all its matings
    birth_rank: dict[Key, int]  # child -> 0-based index within its sibship (semantic); -1 if founder


def _partner_keys(m: pb.Mating) -> list[Key]:
    """A mating's partner positions: each of ``partner_a`` / ``partner_b`` that is set.

    Empty for a *founder sibship* (a partnerless mating grouping siblings via an undrawn parent couple);
    such a mating is aligned by its offspring set, not its (nonexistent) partners.
    """
    keys: list[Key] = []
    if m.HasField("partner_a"):
        keys.append(_key(m.partner_a))
    if m.HasField("partner_b"):
        keys.append(_key(m.partner_b))
    return keys


def _relations(p: pb.Pedigree) -> _Relations:
    by_pos = {(ind.generation, ind.index): ind for ind in p.individuals}
    parents: dict[Key, set[Key]] = defaultdict(set)
    partners: dict[Key, set[Key]] = defaultdict(set)
    children: dict[Key, set[Key]] = defaultdict(set)
    birth_rank: dict[Key, int] = {(ind.generation, ind.index): -1 for ind in p.individuals}
    for m in p.matings:
        pks = _partner_keys(m)
        for i, pa in enumerate(pks):
            for pb_ in pks[i + 1 :]:
                partners[pa].add(pb_)
                partners[pb_].add(pa)
        for rank, off in enumerate(m.offspring):
            child = _key(off.child)
            parents[child].update(pks)
            for pa in pks:
                children[pa].add(child)
            birth_rank[child] = rank
    return _Relations(
        by_pos=by_pos,
        parents={k: frozenset(v) for k, v in parents.items()},
        partners={k: frozenset(v) for k, v in partners.items()},
        children={k: frozenset(v) for k, v in children.items()},
        birth_rank=birth_rank,
    )


def match_individuals(a: pb.Pedigree, b: pb.Pedigree) -> dict[Key, Key]:
    """Map each matchable A individual ``Position`` to a B individual ``Position``.

    Positions may differ between a figure and its re-extraction, so matching is by relational role, not
    by ``(generation, index)``. Two passes, strongest signal first (docs/plans/02-ir-core.md):

    1. exact ``Position`` equality — the fast path when the as-drawn ids are preserved (golden-vs-golden);
    2. structural fingerprint — 1-WL colour refinement over the *disjoint union* of both mating/sibship
       graphs. Each node is seeded with its relational fingerprint (gender, its conditions signature,
       birth-order-within-sibship, and partner/child/parent degrees) and refined by its sorted neighbour
       colours until the partition is stable. Colours are content-derived and shared across both graphs,
       so a node and its structural twin converge to the same colour; individuals sharing a stable colour
       are structurally interchangeable and paired deterministically. Nothing positional enters it.

    Returns a partial injection A-pos -> B-pos; unmatched individuals surface via ``diff`` as
    only_in_a / only_in_b.
    """
    ra, rb = _relations(a), _relations(b)
    a_to_b: dict[Key, Key] = {}
    b_taken: set[Key] = set()

    # Pass 1: exact position.
    b_pos = set(rb.by_pos)
    for a_pos in ra.by_pos:
        if a_pos in b_pos:
            a_to_b[a_pos] = a_pos
            b_taken.add(a_pos)

    # Pass 2: structural fingerprint on the remainder.
    colour = _wl_colours(ra, rb)
    a_by_colour: dict[int, list[Key]] = defaultdict(list)
    b_by_colour: dict[int, list[Key]] = defaultdict(list)
    for a_pos in ra.by_pos:
        if a_pos not in a_to_b:
            a_by_colour[colour["a", a_pos]].append(a_pos)
    for b_key in rb.by_pos:
        if b_key not in b_taken:
            b_by_colour[colour["b", b_key]].append(b_key)
    for c, a_group in a_by_colour.items():
        b_group = b_by_colour.get(c, [])
        for a_pos, b_key in zip(sorted(a_group), sorted(b_group), strict=False):
            a_to_b[a_pos] = b_key
            b_taken.add(b_key)
    return a_to_b


def _conditions_sig(ind: pb.Individual) -> tuple[tuple[str, int, int], ...]:
    """A hashable, order-independent signature of an individual's conditions: sorted ``(name, status,
    inheritance)`` — inheritance included so a carrier's mode (which picks its glyph) is a scored difference."""
    return tuple(sorted((c.name, int(c.status), int(c.inheritance)) for c in ind.conditions))


def _wl_colours(ra: _Relations, rb: _Relations) -> dict[tuple[str, Key], int]:
    """1-WL colour refinement over the disjoint union of the two graphs.

    Nodes are keyed ``(tag, position)`` with ``tag`` in {"a", "b"}. Colours are integers assigned by
    value of a content tuple through one shared table, so identical structure yields identical colours
    across the two graphs. Refinement only ever splits a colour class; it is stable once the number of
    distinct colours stops growing.
    """
    rels = {"a": ra, "b": rb}
    nodes = [("a", i) for i in ra.by_pos] + [("b", i) for i in rb.by_pos]

    seed: dict[tuple[str, Key], object] = {}
    for tag, i in nodes:
        r = rels[tag]
        ind = r.by_pos[i]
        seed[tag, i] = (
            int(ind.gender),
            _conditions_sig(ind),
            r.birth_rank[i],
            len(r.partners.get(i, ())),
            len(r.children.get(i, ())),
            len(r.parents.get(i, ())),
        )
    colour = _compress(seed)
    distinct = len(set(colour.values()))
    for _ in range(len(nodes)):
        refined: dict[tuple[str, Key], object] = {}
        for tag, i in nodes:
            r = rels[tag]
            refined[tag, i] = (
                colour[tag, i],
                tuple(sorted(colour[tag, j] for j in r.parents.get(i, ()))),
                tuple(sorted(colour[tag, j] for j in r.partners.get(i, ()))),
                tuple(sorted(colour[tag, j] for j in r.children.get(i, ()))),
            )
        colour = _compress(refined)
        grown = len(set(colour.values()))
        if grown == distinct:
            break
        distinct = grown
    return colour


def _compress(raw: dict[tuple[str, Key], object]) -> dict[tuple[str, Key], int]:
    """Assign a stable integer to each distinct value, shared across both graphs' nodes."""
    labels: dict[object, int] = {}
    out: dict[tuple[str, Key], int] = {}
    for key, value in raw.items():
        if value not in labels:
            labels[value] = len(labels)
        out[key] = labels[value]
    return out


def diff(a: pb.Pedigree, b: pb.Pedigree) -> Diff:
    """Structurally diff two IRs. See the module docstring; ``a`` is the reference, ``b`` the candidate."""
    ra, rb = _relations(a), _relations(b)
    a_to_b = match_individuals(a, b)
    b_to_a = {v: k for k, v in a_to_b.items()}

    matched = sorted(_label(k) for k in a_to_b)
    only_in_a = sorted(_label(i) for i in ra.by_pos if i not in a_to_b)
    only_in_b = sorted(_label(i) for i in rb.by_pos if i not in b_to_a)

    mismatches: list[Mismatch] = []
    for a_pos in sorted(a_to_b):
        mismatches.extend(_individual_mismatches(_label(a_pos), ra.by_pos[a_pos], rb.by_pos[a_to_b[a_pos]]))
    mismatches.extend(_mating_mismatches(a, b, a_to_b, b_to_a))
    return Diff(matched=matched, mismatches=mismatches, only_in_a=only_in_a, only_in_b=only_in_b)


def diff_set(a: pb.PedigreeSet, b: pb.PedigreeSet) -> SetDiff:
    """Structurally diff two ``PedigreeSet``s: match families, diff each pair, report the unmatched.

    ``a`` is the reference, ``b`` the candidate. Families are matched by unique label then by structural
    similarity (``_match_pedigrees``); each matched pair is compared with the single-pedigree ``diff``.
    """
    pairs = _match_pedigrees(list(a.pedigrees), list(b.pedigrees))
    matched = [
        PedigreePair(ai, bi, _titles(a.pedigrees[ai]), _titles(b.pedigrees[bi]), diff(a.pedigrees[ai], b.pedigrees[bi]))
        for ai, bi in pairs
    ]
    a_used = {ai for ai, _ in pairs}
    b_used = {bi for _, bi in pairs}
    return SetDiff(
        matched=matched,
        only_in_a=[i for i in range(len(a.pedigrees)) if i not in a_used],
        only_in_b=[i for i in range(len(b.pedigrees)) if i not in b_used],
        a_individuals=sum(len(p.individuals) for p in a.pedigrees),
        b_individuals=sum(len(p.individuals) for p in b.pedigrees),
    )


def _titles(p: pb.Pedigree) -> str:
    """All of a pedigree's label texts joined, for reporting."""
    return ", ".join(label.text for label in p.labels)


def _match_key(p: pb.Pedigree) -> str:
    """A pedigree's family-matching key: the FAMILY label text if present, else the first label's text."""
    for label in p.labels:
        if label.kind == pb.LABEL_KIND_FAMILY:
            return label.text
    return p.labels[0].text if p.labels else ""


def _match_pedigrees(a_peds: list[pb.Pedigree], b_peds: list[pb.Pedigree]) -> list[tuple[int, int]]:
    """Pair reference to candidate pedigrees (as (a_index, b_index)): unique label, then structural best-match.

    A label is the reliable key when several families share one structure (six near-identical trios separable
    only by their "Family N" labels); structure covers the unlabelled remainder. A structural pair needs at
    least one matched individual — an unmatched family stays unmatched rather than pairing to a stranger.
    """
    a_avail = set(range(len(a_peds)))
    b_avail = set(range(len(b_peds)))
    pairs: list[tuple[int, int]] = []

    a_by_label = _unique_match_keys(a_peds, a_avail)
    b_by_label = _unique_match_keys(b_peds, b_avail)
    for label, ai in sorted(a_by_label.items()):
        bi = b_by_label.get(label)
        if bi is not None and bi in b_avail:
            pairs.append((ai, bi))
            a_avail.discard(ai)
            b_avail.discard(bi)

    scored: list[tuple[int, int, int, int]] = []
    for ai in sorted(a_avail):
        for bi in sorted(b_avail):
            d = diff(a_peds[ai], b_peds[bi])
            if d.matched:  # require overlap; a zero-overlap pair is two different families
                scored.append((len(d.matched), len(d.mismatches), ai, bi))
    scored.sort(key=lambda s: (-s[0], s[1], s[2], s[3]))  # most matched, then fewest mismatches, then low indices
    for _, _, ai, bi in scored:
        if ai in a_avail and bi in b_avail:
            pairs.append((ai, bi))
            a_avail.discard(ai)
            b_avail.discard(bi)
    return sorted(pairs)


def _unique_match_keys(peds: list[pb.Pedigree], avail: set[int]) -> dict[str, int]:
    """match-key -> index for available pedigrees whose (non-empty) key is unique among the available set."""
    counts: dict[str, int] = defaultdict(int)
    for i in avail:
        key = _match_key(peds[i])
        if key:
            counts[key] += 1
    return {_match_key(peds[i]): i for i in sorted(avail) if _match_key(peds[i]) and counts[_match_key(peds[i])] == 1}


_ENUM = {
    "gender": pb.Gender,
    "sex_assigned_at_birth": pb.SexAssignedAtBirth,
    "reproductive_outcome": pb.ReproductiveOutcome,
    "reproductive_role": pb.ReproductiveRole,
    "status": pb.RelationshipStatus,
    "childlessness": pb.Childlessness,
    "twin_type": pb.ZygosityType,
}


def _individual_mismatches(subject: str, ia: pb.Individual, ib: pb.Individual) -> list[Mismatch]:
    """Compare the gender / clinical / reproductive / annotation attributes of a matched pair."""
    out: list[Mismatch] = []
    for f in ("gender", "sex_assigned_at_birth", "reproductive_outcome", "reproductive_role"):
        va, vb = getattr(ia, f), getattr(ib, f)
        if va != vb:
            out.append(Mismatch(subject, f, _ENUM[f].Name(va), _ENUM[f].Name(vb)))
    if _conditions_sig(ia) != _conditions_sig(ib):
        out.append(Mismatch(subject, "conditions", _fmt_conditions(ia), _fmt_conditions(ib)))
    for f in ("deceased", "proband", "consultand", "count", "count_unspecified"):
        va, vb = getattr(ia, f), getattr(ib, f)
        if va != vb:
            out.append(Mismatch(subject, f, str(va), str(vb)))
    out.extend(_annotation_mismatches(subject, ia, ib))
    return out


def _fmt_conditions(ind: pb.Individual) -> str:
    """Render an individual's conditions as ``name:STATUS`` pairs (``none`` if there are none)."""
    parts = [f"{c.name or '-'}:{pb.ConditionStatus.Name(c.status)}" for c in ind.conditions]
    return ", ".join(sorted(parts)) if parts else "none"


def _annotation_mismatches(subject: str, ia: pb.Individual, ib: pb.Individual) -> list[Mismatch]:
    """Compare the two individuals' typed annotations, grouped by kind.

    Annotations are meaning, so they are compared as a set of ``(type, text)`` — never by draw order.
    For each annotation kind present on either side, the two sides' verbatim-text sets must match; a
    kind present on one side only, or the same kind with different text, is one localized ``Mismatch``
    under field ``annotation:<kind>`` (e.g. ``annotation:genotype``).
    """
    a_by_type, b_by_type = _texts_by_type(ia), _texts_by_type(ib)
    out: list[Mismatch] = []
    for t in sorted(a_by_type.keys() | b_by_type.keys()):
        atexts, btexts = a_by_type.get(t, frozenset()), b_by_type.get(t, frozenset())
        if atexts != btexts:
            out.append(Mismatch(subject, f"annotation:{_annotation_slug(t)}", _fmt_texts(atexts), _fmt_texts(btexts)))
    return out


def _texts_by_type(ind: pb.Individual) -> dict[int, frozenset[str]]:
    """The individual's annotation texts grouped by annotation type (a set per kind)."""
    grouped: dict[int, set[str]] = defaultdict(set)
    for ann in ind.annotations:
        grouped[ann.type].add(ann.text)
    return {t: frozenset(v) for t, v in grouped.items()}


def _annotation_slug(t: int) -> str:
    """Lower-cased short kind name for a Mismatch field, e.g. GENOTYPE -> ``genotype``."""
    return pb.AnnotationType.Name(t).removeprefix("ANNOTATION_TYPE_").lower()


def _fmt_texts(texts: frozenset[str]) -> str:
    """Render a set of annotation texts as a Mismatch value; ``absent`` when the side has none."""
    return ", ".join(sorted(texts)) if texts else "absent"


def _mating_mismatches(
    a: pb.Pedigree, b: pb.Pedigree, a_to_b: dict[Key, Key], b_to_a: dict[Key, Key]
) -> list[Mismatch]:
    """Compare matings aligned by their matched partner set — consanguinity, status, childlessness, offspring.

    A mating whose partners all map to a B mating's partner set is compared attribute-by-attribute. Missing
    relationships are only reported when every partner matched (otherwise the gap is already visible as an
    unmatched individual). Nothing positional is compared. Single-parent matings key on a one-element set.
    """
    out: list[Mismatch] = []
    b_matings = {frozenset(_partner_keys(m)): m for m in b.matings if _partner_keys(m)}
    for m in a.matings:
        a_partners = _partner_keys(m)
        if not a_partners:
            out.extend(_founder_sibship_mismatches(m, b, a_to_b, b_to_a))
            continue
        if any(k not in a_to_b for k in a_partners):
            continue
        key = f"mating({' x '.join(sorted(_label(k) for k in a_partners))})"
        bm = b_matings.get(frozenset(a_to_b[k] for k in a_partners))
        if bm is None:
            out.append(Mismatch(key, "relationship", "present", "absent"))
            continue
        if m.consanguineous != bm.consanguineous:
            out.append(Mismatch(key, "consanguineous", str(m.consanguineous), str(bm.consanguineous)))
        for f in ("status", "childlessness"):
            va, vb = getattr(m, f), getattr(bm, f)
            if va != vb:
                out.append(Mismatch(key, f, _ENUM[f].Name(va), _ENUM[f].Name(vb)))
        out.extend(_offspring_mismatches(key, m, bm, a_to_b, b_to_a))
    return out


def _founder_sibship_mismatches(
    m: pb.Mating, b: pb.Pedigree, a_to_b: dict[Key, Key], b_to_a: dict[Key, Key]
) -> list[Mismatch]:
    """Diff a founder sibship (a partnerless mating): no partners to match, so align on the offspring set.

    The A sibship is recovered iff some B founder sibship groups exactly the mapped offspring; report the
    grouping as absent otherwise (only once every sibling matched — a missing member already surfaces as an
    unmatched individual). When paired, birth order and twin grouping are compared as for any sibship.
    """
    a_children = [_key(off.child) for off in m.offspring]
    if any(c not in a_to_b for c in a_children):
        return []
    key = f"sibship({{{','.join(sorted(_label(c) for c in a_children))}}})"
    mapped = frozenset(a_to_b[c] for c in a_children)
    bm = next(
        (
            cand
            for cand in b.matings
            if not _partner_keys(cand) and frozenset(_key(o.child) for o in cand.offspring) == mapped
        ),
        None,
    )
    if bm is None:
        return [Mismatch(key, "sibship", "present", "absent")]
    return _offspring_mismatches(key, m, bm, a_to_b, b_to_a)


def _offspring_mismatches(
    key: str, m: pb.Mating, bm: pb.Mating, a_to_b: dict[Key, Key], b_to_a: dict[Key, Key]
) -> list[Mismatch]:
    """Compare a matched mating's sibship: relative birth order and twin grouping of matched children."""
    out: list[Mismatch] = []
    b_offspring = {_key(off.child): off for off in bm.offspring}

    a_order = [_key(off.child) for off in m.offspring if a_to_b.get(_key(off.child)) in b_offspring]
    mapped = {a_to_b[c] for c in a_order}
    b_order_as_a = [b_to_a[_key(off.child)] for off in bm.offspring if _key(off.child) in mapped]
    if a_order != b_order_as_a:
        out.append(Mismatch(key, "sibship_order", _fmt_ids(a_order), _fmt_ids(b_order_as_a)))

    for off in m.offspring:
        a_child = _key(off.child)
        b_child = a_to_b.get(a_child)
        if b_child not in b_offspring:
            continue
        boff = b_offspring[b_child]
        if off.twin_type != boff.twin_type:
            out.append(
                Mismatch(
                    _label(a_child),
                    "twin_type",
                    _ENUM["twin_type"].Name(off.twin_type),
                    _ENUM["twin_type"].Name(boff.twin_type),
                )
            )
        a_cotwins = {c for c in _cotwins(m, off) if c in a_to_b}
        b_cotwins = {b_to_a[c] for c in _cotwins(bm, boff) if c in b_to_a}
        if a_cotwins != b_cotwins:
            out.append(Mismatch(_label(a_child), "twin_group", _fmt_ids(a_cotwins), _fmt_ids(b_cotwins)))
    return out


def _cotwins(m: pb.Mating, off: pb.Offspring) -> frozenset[Key]:
    """The other children of ``m`` sharing ``off``'s twin group (empty if it is a singleton birth)."""
    if not off.HasField("twin_group"):
        return frozenset()
    return frozenset(
        _key(o.child)
        for o in m.offspring
        if _key(o.child) != _key(off.child) and o.HasField("twin_group") and o.twin_group == off.twin_group
    )


def _fmt_ids(ids: set[Key] | list[Key]) -> str:
    return "{" + ",".join(_label(k) for k in sorted(ids)) + "}"
