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
- ``interpretations[].diagnosis.genomicInterpretations[].variantInterpretation.variationDescriptor`` -> a locus
  per distinct variant (one locus per variant, alleles ``ref`` and ``alt``) and a genotype for the person
  ``subjectOrBiosampleId`` names (a biosample id: the packet's subject) by ``allelicState``: heterozygous (or
  compound heterozygous) ``[ref, alt]``, homozygous ``[alt, alt]``, hemizygous ``[alt]``. A variant whose allelic
  state is absent or another term states no genotype and is dropped.

Unmapped Phenopacket content (phenotypic features, measurements, gene-level interpretations, files, metadata) is
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


def _conditions_from_diseases(diseases: list[dict[str, Any]]) -> list[_core.Condition]:
    out: list[_core.Condition] = []
    for d in diseases:
        term = _get(d, "term", {}) or {}
        out.append(
            _core.Condition(
                name=str(_get(term, "label") or _get(term, "id", "")),
                status=pb.CONDITION_STATUS_UNAFFECTED if _get(d, "excluded", False) else pb.CONDITION_STATUS_AFFECTED,
                onset_age=_time_element(_get(d, "onset")) or None,
            )
        )
    return out


# GENO allelic states -> the genotype's alt copies (with ref for heterozygous): by id, else by label.
_ALLELIC_STATE = {
    "GENO:0000135": ("ref", "alt"),  # heterozygous
    "GENO:0000402": ("ref", "alt"),  # compound heterozygous: heterozygous at each variant; phase is not stated
    "GENO:0000136": ("alt", "alt"),  # homozygous
    "GENO:0000134": ("alt",),  # hemizygous
}
_ALLELIC_LABEL = {
    "heterozygous": "GENO:0000135",
    "compound heterozygous": "GENO:0000402",
    "homozygous": "GENO:0000136",
    "hemizygous": "GENO:0000134",
}
# Expression syntax prefix -> identifier scheme; any other syntax is OTHER.
_SYNTAX = {"hgvs": pb.IDENTIFIER_SCHEME_HGVS, "spdi": pb.IDENTIFIER_SCHEME_SPDI, "vrs": pb.IDENTIFIER_SCHEME_VRS}


class _Loci:
    """The loci a file's variant descriptors declare: one per distinct variant, ids ``l1``, ``l2``, … by first sight.

    A variant is identified by what its descriptor states, most specific first: the VRS id, an HGVS expression, the
    VCF record, the label, the descriptor's own id.
    """

    def __init__(self) -> None:
        self.declared: list[pb.Locus] = []
        self._by_key: dict[str, str] = {}

    def locus_id(self, vd: dict[str, Any]) -> str:
        key = _variant_key(vd)
        if key not in self._by_key:
            self._by_key[key] = f"l{len(self.declared) + 1}"
            self.declared.append(_locus(self._by_key[key], vd))
        return self._by_key[key]


def _expressions(vd: dict[str, Any]) -> list[tuple[str, str]]:
    return [(str(_get(e, "syntax", "")), str(_get(e, "value", ""))) for e in _get(vd, "expressions", []) or []]


def _vrs_id(vd: dict[str, Any]) -> str:
    variation = _get(vd, "variation", {}) or {}
    for allele in (variation, _get(variation, "allele", {}) or {}):
        for name in ("_id", "id"):
            if value := allele.get(name):
                return str(value)
    return ""


def _variant_key(vd: dict[str, Any]) -> str:
    if vrs := _vrs_id(vd):
        return f"vrs:{vrs}"
    for syntax, value in _expressions(vd):
        if syntax.lower().startswith("hgvs") and value:
            return f"hgvs:{value}"
    if vcf := _get(vd, "vcfRecord"):
        fields = ("genomeAssembly", "chrom", "pos", "ref", "alt")
        return "vcf:" + ":".join(str(_get(vcf, f, "")) for f in fields)
    if label := _get(vd, "label"):
        return f"label:{label}"
    if vid := _get(vd, "id"):
        return f"id:{vid}"
    raise _core.PedigreeImportError(
        "a variationDescriptor identifies no variant (no VRS, expression, VCF, label or id)"
    )


def _locus(locus_id: str, vd: dict[str, Any]) -> pb.Locus:
    """The locus of one variant: named by its label (else its first expression), alleles ``ref`` and ``alt``."""
    expressions = [(s, v) for s, v in _expressions(vd) if v]
    name = str(_get(vd, "label", "") or (expressions[0][1] if expressions else ""))
    identifiers = [
        pb.Identifier(
            scheme=next((v for k, v in _SYNTAX.items() if s.lower().startswith(k)), pb.IDENTIFIER_SCHEME_OTHER),
            value=value,
        )
        for s, value in expressions
    ]
    if vrs := _vrs_id(vd):
        identifiers.append(pb.Identifier(scheme=pb.IDENTIFIER_SCHEME_VRS, value=vrs))
    vcf = _get(vd, "vcfRecord", {}) or {}
    rsid = str(_get(vcf, "id", ""))
    if rsid.startswith("rs"):
        identifiers.append(pb.Identifier(scheme=pb.IDENTIFIER_SCHEME_RSID, value=rsid))
    locus = pb.Locus(
        id=locus_id,
        name=name,
        alleles=[
            pb.Allele(symbol="ref", name=str(_get(vcf, "ref", "")), reference=True),
            pb.Allele(symbol="alt", name=name, identifiers=identifiers),
        ],
    )
    if symbol := _get(_get(vd, "geneContext", {}) or {}, "symbol"):
        locus.gene = str(symbol)
    return locus


def _genotypes(packet: dict[str, Any], subject: str, known: set[str], loci: _Loci) -> dict[str, list[pb.Genotype]]:
    """The genotypes a packet's variant interpretations state, by person id."""
    out: dict[str, list[pb.Genotype]] = {}
    for interpretation in _get(packet, "interpretations", []) or []:
        diagnosis = _get(interpretation, "diagnosis", {}) or {}
        for gi in _get(diagnosis, "genomicInterpretations", []) or []:
            vd = _get(_get(gi, "variantInterpretation", {}) or {}, "variationDescriptor")
            if not vd:
                continue  # a gene-level interpretation states no genotype
            state = _get(vd, "allelicState", {}) or {}
            geno = str(_get(state, "id", "")) or _ALLELIC_LABEL.get(str(_get(state, "label", "")).lower(), "")
            if geno not in _ALLELIC_STATE:
                continue
            who = str(_get(gi, "subjectOrBiosampleId", ""))
            out.setdefault(who if who in known else subject, []).append(
                pb.Genotype(locus_id=loci.locus_id(vd), alleles=_ALLELIC_STATE[geno])
            )
    return out


def _family(fam: dict[str, Any], loci: _Loci) -> tuple[list[_core.Person], _core.Extras, str]:
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
    genotypes: dict[str, list[pb.Genotype]] = {}
    for subject, packet in packets.items():
        for who, stated in _genotypes(packet, subject, known, loci).items():
            for g in stated:
                same = [x for x in genotypes.get(who, []) if x.locus_id == g.locus_id]
                if same and same[0] != g:
                    raise _core.PedigreeImportError(
                        f"person {who!r}: interpretations state different allelic states for one variant"
                    )
                if not same:
                    genotypes.setdefault(who, []).append(g)

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
        conditions: list[_core.Condition] = []
        status = _AFFECTED.get(str(_get(p, "affectedStatus", "MISSING")))
        if status is not None:
            conditions.append(_core.Condition(status=status))

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
                genotypes=genotypes.get(pid, []),
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
    sources: list[tuple[list[_core.Person], _core.Extras]] = []
    loci = _Loci()
    for fam in families:
        if not isinstance(fam, dict):
            raise _core.PedigreeImportError("expected a Family object")
        people, extras, family_id = _family(fam, loci)
        # Each Family is one pedigree; build_sets groups by Person.family, which we set uniformly.
        for person in people:
            person.family = family_id
        sources.append((people, extras))
    if not sources:
        raise _core.PedigreeImportError("no families found")
    return _core.build_sets(sources, provenance=pb.Provenance(source_format=FORMAT), loci=loci.declared)
