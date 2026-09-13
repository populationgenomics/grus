"""Open Pedigree / PhenoTips "simple JSON" importer (docs/design/convert.md, "Open Pedigree").

The interchange form PhenoTips documents (``initFromSimpleJSON``): a JSON array whose objects are either a
person (``name``/``id``/``externalId``, ``sex``, ``mother``/``father`` references, ``proband``,
``lifeStatus``, ``disorders`` + ``carrierStatus``, ``twinGroup`` + ``monozygotic``, ``adoptedIn`` /
``adoptedStatus``, ``evaluated``, ``numPersons``, dates and comments) or a relationship (``partner1``,
``partner2``, ``separated``, ``consanguinity``, ``childlessStatus``, ``childlessReason``). Keys are matched
case-insensitively, as the reference parser does. open-pedigree's own save format (the internal graph JSON
with ``rel``/``chhub``/``virt`` nodes) is a layout artefact and is not accepted.

Deviations from the reference parser, all in the no-inference direction: a person given one parent gets a
single-parent mating (PhenoTips creates a virtual parent); no default proband (PhenoTips makes the first
object the proband when none is flagged); ``consanguinity`` absent or ``"auto"`` stays false (PhenoTips
computes it from the graph). ``monozygotic`` false means zygosity *unknown*, not dizygotic — the format does
not distinguish DZ from unknown.
"""

from __future__ import annotations

import json
from typing import Any

from grus.convert._core import Extras, PedigreeImportError, Person, build_set
from grus.models import pedigree_pb2 as pb

FORMAT = "openpedigree"

_SEX = {
    "male": pb.GENDER_MAN,
    "m": pb.GENDER_MAN,
    "female": pb.GENDER_WOMAN,
    "f": pb.GENDER_WOMAN,
    "other": pb.GENDER_UNKNOWN,
    "o": pb.GENDER_UNKNOWN,
    "unknown": pb.GENDER_UNKNOWN,
    "u": pb.GENDER_UNKNOWN,
    "": pb.GENDER_UNKNOWN,
}
_LIFE: dict[str, pb.ReproductiveOutcome] = {
    "stillborn": pb.REPRODUCTIVE_OUTCOME_STILLBIRTH,
    "miscarriage": pb.REPRODUCTIVE_OUTCOME_MISCARRIAGE,
    "aborted": pb.REPRODUCTIVE_OUTCOME_TERMINATION,
    "unborn": pb.REPRODUCTIVE_OUTCOME_PREGNANCY,
}
_CARRIER: dict[str, pb.ConditionStatus] = {
    "affected": pb.CONDITION_STATUS_AFFECTED,
    "carrier": pb.CONDITION_STATUS_CARRIER,
    "presymptomatic": pb.CONDITION_STATUS_PRESYMPTOMATIC,
    "": pb.CONDITION_STATUS_UNAFFECTED,
}


def _lower_keys(obj: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in obj.items()}


def _truthy(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "y", "1")) or v == 1


class _Refs:
    """Resolve a parent/partner reference the way the reference parser does: id, externalId, name, firstName."""

    def __init__(self, persons: list[dict[str, Any]]) -> None:
        self.by_key: dict[str, str] = {}
        for i, p in enumerate(persons):
            pid = _person_id(p, i)
            for key in ("id", "externalid", "name", "firstname"):
                if key in p and p[key] is not None:
                    self.by_key.setdefault(str(p[key]), pid)
            self.by_key.setdefault(pid, pid)

    def __call__(self, ref: Any, *, what: str) -> str:
        try:
            return self.by_key[str(ref)]
        except KeyError:
            raise PedigreeImportError(f"{what} reference {ref!r} names no person") from None


def _person_id(p: dict[str, Any], i: int) -> str:
    """The source identifier we keep: externalId, else name, else id, else the array position."""
    for key in ("externalid", "name", "id"):
        if key in p and p[key] not in (None, ""):
            return str(p[key])
    return f"#{i}"


def _person(p: dict[str, Any], i: int, refs: _Refs) -> Person:
    pid = _person_id(p, i)
    sex = str(p.get("sex", "") or "").lower()
    if sex not in _SEX:
        raise PedigreeImportError(f"person {pid!r}: unrecognised sex {sex!r}")
    annotations: list[pb.Annotation] = []
    if sex in ("other", "o"):
        annotations.append(pb.Annotation(text="other", type=pb.ANNOTATION_TYPE_OTHER))

    life = str(p.get("lifestatus", "alive") or "alive").lower()
    deceased = life == "deceased" or bool(p.get("deathdate"))
    if life not in ("alive", "deceased") and life not in _LIFE:
        raise PedigreeImportError(f"person {pid!r}: unrecognised lifeStatus {life!r}")

    disorders = p.get("disorders") or []
    carrier = str(p.get("carrierstatus", "affected" if disorders else "") or "").lower()
    if carrier not in _CARRIER:
        raise PedigreeImportError(f"person {pid!r}: unrecognised carrierStatus {carrier!r}")
    conditions = [pb.Condition(name=str(d), status=_CARRIER[carrier]) for d in disorders]
    if not disorders and carrier in ("carrier", "presymptomatic"):
        conditions.append(pb.Condition(status=_CARRIER[carrier]))

    for key, atype, prefix in (
        ("birthdate", pb.ANNOTATION_TYPE_AGE, "b. "),
        ("deathdate", pb.ANNOTATION_TYPE_AGE_AT_DEATH, "d. "),
        ("deceasedage", pb.ANNOTATION_TYPE_AGE_AT_DEATH, "d. "),
        ("comments", pb.ANNOTATION_TYPE_OTHER, ""),
    ):
        if p.get(key) not in (None, ""):
            annotations.append(pb.Annotation(text=f"{prefix}{p[key]}", type=atype))
    if p.get("gestationage") not in (None, "", 0):
        annotations.append(pb.Annotation(text=f"{p['gestationage']} wk", type=pb.ANNOTATION_TYPE_GESTATIONAL_AGE))
    for term in p.get("hpoterms") or []:
        annotations.append(pb.Annotation(text=str(term), type=pb.ANNOTATION_TYPE_PHENOTYPE))
    for gene in p.get("candidategenes") or []:
        annotations.append(pb.Annotation(text=f"gene: {gene}", type=pb.ANNOTATION_TYPE_OTHER))

    adoption: pb.Adoption | None = None
    status = str(p.get("adoptedstatus", "") or "").lower()
    if _truthy(p.get("adoptedin")) or _truthy(p.get("isadopted")) or status == "adoptedin":
        adoption = pb.ADOPTION_IN
    elif status == "adoptedout":
        adoption = pb.ADOPTION_OUT

    twin_group = p.get("twingroup")
    num = p.get("numpersons")
    return Person(
        id=pid,
        father=refs(p["father"], what="father") if p.get("father") not in (None, "") else None,
        mother=refs(p["mother"], what="mother") if p.get("mother") not in (None, "") else None,
        gender=_SEX[sex],
        conditions=conditions,
        deceased=deceased,
        proband=_truthy(p.get("proband")),
        documented_evaluation=_truthy(p.get("evaluated")),
        reproductive_outcome=_LIFE.get(life),
        count=int(num) if isinstance(num, int) and num > 0 else None,
        annotations=annotations,
        twin_group=int(twin_group) if twin_group not in (None, "") else None,
        twin_type=(
            pb.ZYGOSITY_TYPE_MONOZYGOTIC
            if twin_group not in (None, "") and _truthy(p.get("monozygotic"))
            else pb.ZYGOSITY_TYPE_UNKNOWN
            if twin_group not in (None, "")
            else pb.ZYGOSITY_TYPE_UNSPECIFIED
        ),
        adoption=adoption,
        parentage=pb.PARENTAGE_ADOPTIVE if adoption == pb.ADOPTION_IN else None,
    )


def _relationship(r: dict[str, Any], refs: _Refs, extras: Extras) -> None:
    if "partner1" not in r or "partner2" not in r:
        raise PedigreeImportError("relationship object lacks partner1/partner2")
    pair = frozenset({refs(r["partner1"], what="partner1"), refs(r["partner2"], what="partner2")})
    if len(pair) != 2:
        raise PedigreeImportError("relationship partners must be two distinct people")
    extras.spouses.add(pair)
    if _truthy(r.get("separated")):
        extras.status[pair] = pb.RELATIONSHIP_STATUS_SEPARATED
    if str(r.get("consanguinity", "") or "").lower() in ("y", "yes", "true"):
        extras.consanguineous.add(pair)
    childless = str(r.get("childlessstatus", "") or "").lower()
    if childless == "childless":
        extras.childless[pair] = pb.CHILDLESSNESS_BY_CHOICE
    elif childless == "infertile":
        extras.childless[pair] = pb.CHILDLESSNESS_INFERTILITY
    elif childless not in ("", "none"):
        raise PedigreeImportError(f"unrecognised childlessStatus {childless!r}")
    if r.get("childlessreason"):
        extras.annotations[pair] = [pb.Annotation(text=str(r["childlessreason"]), type=pb.ANNOTATION_TYPE_OTHER)]


def import_openpedigree(text: str) -> pb.PedigreeSet:
    """Parse a simple-JSON array into a validated single-pedigree ``PedigreeSet``."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise PedigreeImportError(f"not JSON: {e}") from e
    if not isinstance(doc, list):
        raise PedigreeImportError("expected a JSON array of person / relationship objects")
    objs = [_lower_keys(o) for o in doc if isinstance(o, dict)]
    persons = [o for o in objs if "partner1" not in o and "partner2" not in o]
    relationships = [o for o in objs if o not in persons]
    if not persons:
        raise PedigreeImportError("no person objects found")
    refs = _Refs(persons)
    people = [_person(p, i, refs) for i, p in enumerate(persons)]
    extras = Extras()
    for r in relationships:
        _relationship(r, refs, extras)
    return build_set(people, extras, provenance=pb.Provenance(source_format=FORMAT))
