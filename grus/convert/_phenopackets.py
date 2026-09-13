"""GA4GH Phenopackets v2 ``Family`` importer (docs/design/convert.md, "Phenopackets").

A ``Family`` JSON document (proto3-JSON; lowerCamelCase or snake_case keys, enum names) or a JSON array of
them. The graph comes from ``family.pedigree.persons`` (a PED row each: ``paternalId``/``maternalId`` with
``"0"`` = not present); per-person detail comes from the ``Phenopacket``s in ``proband`` and ``relatives``
whose ``subject.id`` matches a person:

- ``proband.subject.id`` -> ``proband``; ``consanguinousParents`` -> ``consanguineous`` on the proband's
  parents' mating (the flag is family-level and defined as the proband's parents).
- ``sex`` MALE/FEMALE/UNKNOWN_SEX -> gender by the assume-aligned rule; OTHER_SEX (phenotypic sex "cannot be
  assessed", not a gender) -> unknown gender + an OTHER annotation.
- ``affectedStatus`` -> one unnamed ``Condition`` unless the person's packet lists ``diseases``, which win:
  ``term.label`` -> name, ``excluded`` -> UNAFFECTED, ``onset`` -> ``onset_age`` (the ISO duration or the
  ontology label, verbatim).
- ``vitalStatus.status == DECEASED`` -> ``deceased``; ``timeOfDeath`` -> AGE_AT_DEATH annotation.
- ``karyotypicSex`` -> KARYOTYPE annotation; a ``gender`` ontology label naming non-binary -> NONBINARY.

Unmapped Phenopacket content (phenotypic features, measurements, interpretations, files, metadata) is
dropped. Importing from Phenopackets does not require its Python package: the JSON is read structurally.
"""

from __future__ import annotations

import json
from typing import Any

from grus.convert import _core
from grus.models import pedigree_pb2 as pb

FORMAT = "phenopackets"

_MISSING_PARENT = {"0", ""}
_SEX = {
    "MALE": pb.GENDER_MAN,
    "FEMALE": pb.GENDER_WOMAN,
    "UNKNOWN_SEX": pb.GENDER_UNKNOWN,
    "OTHER_SEX": pb.GENDER_UNKNOWN,
    # proto3-JSON parsers also accept the integer values.
    "2": pb.GENDER_MAN,
    "1": pb.GENDER_WOMAN,
    "0": pb.GENDER_UNKNOWN,
    "3": pb.GENDER_UNKNOWN,
}
_AFFECTED = {
    "AFFECTED": pb.CONDITION_STATUS_AFFECTED,
    "UNAFFECTED": pb.CONDITION_STATUS_UNAFFECTED,
    "2": pb.CONDITION_STATUS_AFFECTED,
    "1": pb.CONDITION_STATUS_UNAFFECTED,
}


def _get(d: dict[str, Any], camel: str, default: Any = None) -> Any:
    """Read a proto3-JSON field by its lowerCamelCase name or its snake_case original."""
    if camel in d:
        return d[camel]
    snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in camel)
    return d.get(snake, default)


def _time_element(t: dict[str, Any] | None) -> str | None:
    """A ``TimeElement`` oneof, verbatim as text."""
    if not t:
        return None
    if age := _get(t, "age"):
        return str(_get(age, "iso8601duration", ""))
    if rng := _get(t, "ageRange"):
        start, end = _get(rng, "start", {}), _get(rng, "end", {})
        return f"{_get(start, 'iso8601duration', '')}..{_get(end, 'iso8601duration', '')}"
    if ga := _get(t, "gestationalAge"):
        return f"{_get(ga, 'weeks', 0)}w{_get(ga, 'days', 0)}d"
    if oc := _get(t, "ontologyClass"):
        return str(_get(oc, "label") or _get(oc, "id", ""))
    if ts := _get(t, "timestamp"):
        return str(ts)
    return None


def _conditions_from_diseases(diseases: list[dict[str, Any]]) -> list[pb.Condition]:
    out: list[pb.Condition] = []
    for d in diseases:
        term = _get(d, "term", {}) or {}
        c = pb.Condition(
            name=str(_get(term, "label") or _get(term, "id", "")),
            status=pb.CONDITION_STATUS_UNAFFECTED if _get(d, "excluded", False) else pb.CONDITION_STATUS_AFFECTED,
        )
        onset = _time_element(_get(d, "onset"))
        if onset:
            c.onset_age = onset
        out.append(c)
    return out


def _family(fam: dict[str, Any]) -> tuple[list[_core.Person], _core.Extras, str]:
    pedigree = _get(fam, "pedigree", {}) or {}
    persons: list[dict[str, Any]] = _get(pedigree, "persons", []) or []
    if not persons:
        raise _core.PedigreeImportError("Family has no pedigree.persons")
    family_id = str(_get(fam, "id", "") or _get(persons[0], "familyId", ""))

    packets: dict[str, dict[str, Any]] = {}
    proband_id: str | None = None
    if proband := _get(fam, "proband"):
        subject = _get(proband, "subject", {}) or {}
        proband_id = str(_get(subject, "id", ""))
        packets[proband_id] = proband
    for rel in _get(fam, "relatives", []) or []:
        subject = _get(rel, "subject", {}) or {}
        packets[str(_get(subject, "id", ""))] = rel

    known = {str(_get(p, "individualId", "")) for p in persons}
    stray = sorted(k for k in packets if k not in known)
    if stray:
        raise _core.PedigreeImportError(f"Phenopacket subject(s) {stray} are not in pedigree.persons")

    people: list[_core.Person] = []
    for p in persons:
        pid = str(_get(p, "individualId", ""))
        if not pid or pid == "0":
            raise _core.PedigreeImportError("pedigree.persons entry lacks an individualId")
        father, mother = str(_get(p, "paternalId", "0")), str(_get(p, "maternalId", "0"))
        sex_code = str(_get(p, "sex", "UNKNOWN_SEX"))
        gender = _SEX.get(sex_code)
        if gender is None:
            raise _core.PedigreeImportError(f"person {pid!r}: unrecognised sex {sex_code!r}")
        annotations: list[pb.Annotation] = []
        if sex_code in ("OTHER_SEX", "3"):
            annotations.append(pb.Annotation(text="OTHER_SEX", type=pb.ANNOTATION_TYPE_OTHER))
        conditions: list[pb.Condition] = []
        status = _AFFECTED.get(str(_get(p, "affectedStatus", "MISSING")))
        if status is not None:
            conditions.append(pb.Condition(status=status))

        deceased = False
        packet = packets.get(pid)
        if packet is not None:
            subject = _get(packet, "subject", {}) or {}
            vital = _get(subject, "vitalStatus", {}) or {}
            if str(_get(vital, "status", "")) in ("DECEASED", "2"):
                deceased = True
                tod = _time_element(_get(vital, "timeOfDeath"))
                if tod:
                    annotations.append(pb.Annotation(text=tod, type=pb.ANNOTATION_TYPE_AGE_AT_DEATH))
            karyo = _get(subject, "karyotypicSex")
            if karyo and str(karyo) not in ("UNKNOWN_KARYOTYPE", "0"):
                annotations.append(pb.Annotation(text=str(karyo), type=pb.ANNOTATION_TYPE_KARYOTYPE))
            if g := _get(subject, "gender"):
                label = str(_get(g, "label", "")).lower()
                if "non-binary" in label or "nonbinary" in label or "genderqueer" in label:
                    gender = pb.GENDER_NONBINARY
            if diseases := _get(packet, "diseases"):
                conditions = _conditions_from_diseases(diseases)
        people.append(
            _core.Person(
                id=pid,
                family=family_id,
                father=None if father in _MISSING_PARENT else father,
                mother=None if mother in _MISSING_PARENT else mother,
                gender=gender,
                conditions=conditions,
                deceased=deceased,
                proband=pid == proband_id,
                annotations=annotations,
            )
        )

    extras = _core.Extras()
    if _get(fam, "consanguinousParents", False) and proband_id is not None:
        pro = next((x for x in people if x.id == proband_id), None)
        if pro is None:
            raise _core.PedigreeImportError(f"proband {proband_id!r} is not in pedigree.persons")
        if pro.father and pro.mother:
            extras.consanguineous.add(frozenset({pro.father, pro.mother}))
        else:
            raise _core.PedigreeImportError(
                "consanguinousParents is set but the proband does not have two parents listed"
            )
    return people, extras, family_id


def import_phenopackets(text: str) -> pb.PedigreeSet:
    """Parse one ``Family`` (or a JSON array of families) into a validated ``PedigreeSet``."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise _core.PedigreeImportError(f"not JSON: {e}") from e
    families = doc if isinstance(doc, list) else [doc]
    ps = pb.PedigreeSet(provenance=pb.Provenance(source_format=FORMAT))
    for fam in families:
        if not isinstance(fam, dict):
            raise _core.PedigreeImportError("expected a Family object")
        people, extras, family_id = _family(fam)
        # Each Family is one pedigree; build_set groups by Person.family, which we set uniformly.
        for person in people:
            person.family = family_id
        ps.pedigrees.extend(_core.build_set(people, extras).pedigrees)
    if not ps.pedigrees:
        raise _core.PedigreeImportError("no families found")
    return ps
