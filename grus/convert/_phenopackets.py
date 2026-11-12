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


# GENO allelic states -> the genotype's copies of ``alt`` (with ``ref`` for heterozygous).
_ALLELIC_STATE = {
    "GENO:0000135": ("ref", "alt"),  # heterozygous
    "GENO:0000458": ("ref", "alt"),  # simple heterozygous
    "GENO:0000402": ("ref", "alt"),  # compound heterozygous: heterozygous at each variant; phase is not stated
    "GENO:0000136": ("alt", "alt"),  # homozygous
    "GENO:0000134": ("alt",),  # hemizygous
    "GENO:0000604": ("alt",),  # hemizygous X-linked
    "GENO:0000605": ("alt",),  # hemizygous Y-linked
}
_ALLELIC_LABEL = {
    "heterozygous": "GENO:0000135",
    "simple heterozygous": "GENO:0000458",
    "compound heterozygous": "GENO:0000402",
    "homozygous": "GENO:0000136",
    "hemizygous": "GENO:0000134",
    "hemizygous x-linked": "GENO:0000604",
    "hemizygous y-linked": "GENO:0000605",
}
_UNSTATED_STATE = {"", "GENO:0000137"}  # absent, or "unspecified zygosity": no genotype
# Expression syntax prefix -> identifier scheme; any other syntax is OTHER.
_SYNTAX = {"hgvs": pb.IDENTIFIER_SCHEME_HGVS, "spdi": pb.IDENTIFIER_SCHEME_SPDI, "vrs": pb.IDENTIFIER_SCHEME_VRS}


class _Loci:
    """The loci a file's variant descriptors declare: one per distinct variant, ids ``l1``, ``l2``, … by first sight.

    Two descriptors are one variant when they share an identifying statement (``_tokens``): a VRS id, the VCF record,
    or a genomic, coding or other non-protein HGVS expression. One describing a variant by none of those falls back to
    its gene with its label, protein HGVS or id.
    """

    def __init__(self) -> None:
        self.declared: list[pb.Locus] = []
        self._by_token: dict[str, str] = {}
        self._by_name: dict[tuple[str | None, str], str] = {}

    def locus_id(self, vd: dict[str, Any]) -> str:
        """The id of ``vd``'s locus, declaring it on first sight.

        Raises:
            PedigreeImportError: ``vd`` shares identifying statements with two loci, or is named as another is.
        """
        tokens = _tokens(vd)
        found = sorted({self._by_token[t] for t in tokens if t in self._by_token})
        if len(found) > 1:
            raise _core.PedigreeImportError(f"a variationDescriptor matches two variants ({', '.join(found)})")
        if found:
            locus_id = found[0]
        else:
            locus_id = f"l{len(self.declared) + 1}"
            locus = _locus(locus_id, vd)
            name = (locus.gene if locus.HasField("gene") else None, locus.name)
            if locus.name and name in self._by_name:
                raise _core.PedigreeImportError(
                    f"two different variants are both named {locus.name!r} (gene {name[0]!r}); "
                    "they share no VRS id, VCF record or HGVS expression to tell whether they are one"
                )
            self._by_name[name] = locus_id
            self.declared.append(locus)
        self._by_token.update(dict.fromkeys(tokens, locus_id))
        return locus_id


def _expressions(vd: dict[str, Any]) -> list[tuple[str, str]]:
    """``vd``'s expressions as (lower-cased syntax, value), those with a value."""
    out = [(str(_get(e, "syntax", "")).lower(), str(_get(e, "value", ""))) for e in _get(vd, "expressions", []) or []]
    return [(s, v) for s, v in out if v]


def _vrs_id(vd: dict[str, Any]) -> str:
    """The VRS id of ``vd``'s variation, whichever member of the Variation oneof carries it."""
    variation = _get(vd, "variation", {}) or {}
    for candidate in (variation, *(v for v in variation.values() if isinstance(v, dict))):
        for name in ("_id", "id", "Id"):
            if value := candidate.get(name):
                return str(value)
    return ""


def _vcf(vd: dict[str, Any]) -> dict[str, Any]:
    return _get(vd, "vcfRecord", {}) or {}


def _gene(vd: dict[str, Any]) -> str:
    return str(_get(_get(vd, "geneContext", {}) or {}, "symbol", "") or "")


def _tokens(vd: dict[str, Any]) -> list[str]:
    """The statements that identify ``vd``'s variant, strongest first; a weak fallback when there are none.

    Raises:
        PedigreeImportError: ``vd`` states nothing that identifies a variant.
    """
    tokens = []
    if vrs := _vrs_id(vd):
        tokens.append(f"vrs:{vrs}")
    if vcf := _vcf(vd):
        fields = ("genomeAssembly", "chrom", "pos", "ref", "alt")
        tokens.append("vcf:" + ":".join(str(_get(vcf, f, "")) for f in fields))
    tokens += [f"{s}:{v}" for s, v in _expressions(vd) if s.startswith("hgvs") and s != "hgvs.p"]
    if tokens:
        return tokens
    gene = _gene(vd)
    protein = next((v for s, v in _expressions(vd) if s == "hgvs.p"), "")
    for kind, value in (("label", _get(vd, "label")), ("hgvs.p", protein), ("id", _get(vd, "id"))):
        if value:
            return [f"{kind}:{gene}:{value}"]
    raise _core.PedigreeImportError(
        "a variationDescriptor identifies no variant (no VRS id, VCF record, expression, label or id)"
    )


def _name(vd: dict[str, Any]) -> str:
    """``vd``'s printed name: its label, else its coding, genomic or other HGVS, else its VCF record, else protein."""
    if label := _get(vd, "label"):
        return str(label)
    expressions = dict(reversed(_expressions(vd)))  # the first expression of each syntax
    for syntax in ("hgvs.c", "hgvs.g", "hgvs"):
        if syntax in expressions:
            return expressions[syntax]
    if vcf := _vcf(vd):
        return f"{_get(vcf, 'chrom', '')}:{_get(vcf, 'pos', '')} {_get(vcf, 'ref', '')}>{_get(vcf, 'alt', '')}"
    return expressions.get("hgvs.p", "")


def _identifiers(vd: dict[str, Any]) -> list[pb.Identifier]:
    """The resolvable names ``vd`` prints, once each: its expressions, VRS id and rsIDs."""
    stated = [
        (next((v for k, v in _SYNTAX.items() if s.startswith(k)), pb.IDENTIFIER_SCHEME_OTHER), value)
        for s, value in _expressions(vd)
    ]
    if vrs := _vrs_id(vd):
        stated.append((pb.IDENTIFIER_SCHEME_VRS, vrs))
    for rsid in str(_get(_vcf(vd), "id", "")).split(";"):
        if rsid.startswith("rs"):
            stated.append((pb.IDENTIFIER_SCHEME_RSID, rsid))
    return [pb.Identifier(scheme=scheme, value=value) for scheme, value in dict.fromkeys(stated)]


def _locus(locus_id: str, vd: dict[str, Any]) -> pb.Locus:
    """The locus of one variant (``_name``), alleles ``ref`` (named by the VCF REF, when stated) and ``alt``."""
    name = _name(vd)
    locus = pb.Locus(
        id=locus_id,
        name=name,
        alleles=[
            pb.Allele(symbol="ref", name=str(_get(_vcf(vd), "ref", "")), reference=True),
            pb.Allele(symbol="alt", name=name, identifiers=_identifiers(vd)),
        ],
    )
    if gene := _gene(vd):
        locus.gene = gene
    return locus


def _copies(vd: dict[str, Any]) -> tuple[str, ...] | None:
    """The copies ``vd``'s allelic state gives, or None when it states no zygosity.

    Raises:
        PedigreeImportError: the allelic state is a term this importer does not know.
    """
    state = _get(vd, "allelicState", {}) or {}
    geno, label = str(_get(state, "id", "")), str(_get(state, "label", "")).lower()
    geno = geno if geno in _ALLELIC_STATE else _ALLELIC_LABEL.get(label, geno)
    if geno in _ALLELIC_STATE:
        return _ALLELIC_STATE[geno]
    if geno in _UNSTATED_STATE:
        return None
    raise _core.PedigreeImportError(f"unrecognised allelicState {geno!r} ({label!r})")


def _owner(who: str, subject: str, known: set[str], biosamples: dict[str, str]) -> str:
    """The person ``subjectOrBiosampleId`` names: a person, a biosample's individual, or (absent) the subject.

    Raises:
        PedigreeImportError: the id names neither a person nor one of the packet's biosamples, or the biosample's
            individual is not in the pedigree.
    """
    if not who:
        return subject
    if who in known:
        return who
    if who in biosamples:
        owner = biosamples[who] or subject
        if owner not in known:
            raise _core.PedigreeImportError(f"biosample {who!r} belongs to {owner!r}, who is not in pedigree.persons")
        return owner
    raise _core.PedigreeImportError(f"subjectOrBiosampleId {who!r} names neither a person nor a biosample")


def _genotypes(packet: dict[str, Any], subject: str, known: set[str], loci: _Loci) -> dict[str, list[pb.Genotype]]:
    """The genotypes a packet's variant interpretations state, by person id."""
    biosamples = {
        str(_get(b, "id", "")): str(_get(b, "individualId", "") or "") for b in _get(packet, "biosamples", []) or []
    }
    out: dict[str, list[pb.Genotype]] = {}
    for interpretation in _get(packet, "interpretations", []) or []:
        diagnosis = _get(interpretation, "diagnosis", {}) or {}
        for gi in _get(diagnosis, "genomicInterpretations", []) or []:
            vd = _get(_get(gi, "variantInterpretation", {}) or {}, "variationDescriptor")
            if not vd:
                continue  # a gene-level interpretation states no genotype
            copies = _copies(vd)
            if copies is None:
                continue
            who = _owner(str(_get(gi, "subjectOrBiosampleId", "")), subject, known, biosamples)
            out.setdefault(who, []).append(pb.Genotype(locus_id=loci.locus_id(vd), alleles=copies))
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
