"""kinship2 / Pedixplorer table importer (docs/design/convert.md, "kinship2").

kinship2's ``pedigree()`` is an in-memory constructor over parallel vectors; the file form is the CSV/TSV a
user feeds it, with a header naming ``id, dadid, momid, sex`` and optionally ``famid``, ``affected*``
(one column per trait), ``status`` (1 = dead), plus Pedixplorer's extra columns (``proband``,
``consultand``, ``carrier``, ``asymptomatic``, ``evaluated``, ``adopted``, ``miscarriage``, ``fertility``,
``dateofbirth``, ``dateofdeath``) under their documented aliases. Header names are matched
case-insensitively.

The ``relation`` matrix (``id1, id2, code``; 1 MZ, 2 DZ, 3 UZ twin, 4 spouse) is a second table. It rides
in the same file after a blank line (a header line starting with ``id1``), or in a sidecar
``<stem>.rel.csv`` when importing by path.

Codes: sex ``1``/``2``/``3``/``4`` = male/female/unknown/terminated, or those words (prefix match); if any
``0`` appears the numeric codes shift down by one (kinship2's ``fixParents`` rule). ``terminated`` is a
pregnancy termination node: gender unknown + ``reproductive_outcome = TERMINATION``. Missing parent =
``NA``, ``""``, ``0`` or ``.``. kinship2 rejects a single-parent row; this importer keeps it as a
single-parent mating rather than synthesising the other parent.
"""

from __future__ import annotations

import csv
import io

from grus.convert._core import Extras, PedigreeImportError, Person, build_set
from grus.models import pedigree_pb2 as pb

FORMAT = "kinship2"
SIDECAR_SUFFIX = ".rel.csv"

_MISSING = {"", "NA", "0", ".", "NULL"}
_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "indid"),
    "dadid": ("dadid", "fatherid", "father"),
    "momid": ("momid", "motherid", "mother"),
    "famid": ("famid", "family", "fid", "ped"),
    "sex": ("sex", "gender"),
    "status": ("status", "deceased", "dead", "vitalstatus"),
    "proband": ("proband",),
    "consultand": ("consultand", "consultant"),
    "carrier": ("carrier",),
    "asymptomatic": ("asymptomatic", "presymptomatic"),
    "evaluated": ("evaluated", "evaluation"),
    "adopted": ("adopted", "adoption"),
    "miscarriage": ("miscarriage", "aborted"),
    "fertility": ("fertility", "sterilisation", "steril"),
    "dateofbirth": ("dateofbirth", "dob", "birth"),
    "dateofdeath": ("dateofdeath", "dod", "death"),
}
_TWIN_CODES: dict[str, pb.ZygosityType] = {
    "1": pb.ZYGOSITY_TYPE_MONOZYGOTIC,
    "2": pb.ZYGOSITY_TYPE_DIZYGOTIC,
    "3": pb.ZYGOSITY_TYPE_UNKNOWN,
    "mz twin": pb.ZYGOSITY_TYPE_MONOZYGOTIC,
    "dz twin": pb.ZYGOSITY_TYPE_DIZYGOTIC,
    "uz twin": pb.ZYGOSITY_TYPE_UNKNOWN,
}


def _split_tables(text: str) -> tuple[str, str | None]:
    """Separate the pedigree table from an embedded relation table (blank line + ``id1`` header)."""
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if not lines[i].strip() and i + 1 < len(lines) and lines[i + 1].strip().lower().startswith("id1"):
            return "\n".join(lines[:i]), "\n".join(lines[i + 1 :])
    return text, None


def _read(text: str) -> list[dict[str, str]]:
    """Read a header table as a list of lower-cased-key dicts.

    Accepts comma/tab/semicolon delimiters and R ``write.table`` output: a quoted header and a leading
    row-names column (every data row has one more field than the header), which is dropped.
    """
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    if "\t" in first:
        reader = csv.reader(io.StringIO(text), delimiter="\t")
    elif "," in first:
        reader = csv.reader(io.StringIO(text), delimiter=",")
    elif ";" in first:
        reader = csv.reader(io.StringIO(text), delimiter=";")
    else:  # R write.table default: space-separated, strings double-quoted
        reader = csv.reader(io.StringIO(text), delimiter=" ", skipinitialspace=True)
    records = list(reader)
    records = [r for r in records if any(cell.strip() for cell in r)]
    if not records:
        return []
    header = [h.strip().strip('"').lower() for h in records[0]]
    body = records[1:]
    if body and all(len(r) == len(header) + 1 for r in body):
        body = [r[1:] for r in body]  # R row names
    rows: list[dict[str, str]] = []
    for n, r in enumerate(body, 2):
        if len(r) > len(header):
            raise PedigreeImportError(f"line {n}: more fields than header columns")
        r = r + [""] * (len(header) - len(r))
        rows.append({k: v.strip().strip('"') for k, v in zip(header, r, strict=True)})
    return rows


def _resolve(header: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in header:
                found[canonical] = alias
                break
    for req in ("id", "dadid", "momid", "sex"):
        if req not in found:
            raise PedigreeImportError(f"kinship2 table lacks a {req!r} column (aliases: {_ALIASES[req]})")
    return found


def _sex_codes(values: list[str]) -> dict[str, tuple[pb.Gender, bool]]:
    """Value -> (gender, is_terminated). Numeric codes shift when a ``0`` is present."""
    numeric = [v for v in values if v.isdigit()]
    shift = 1 if "0" in numeric else 0
    table: dict[str, tuple[pb.Gender, bool]] = {}
    for v in set(values):
        low = v.lower()
        if low.isdigit():
            code = int(low) + shift
            table[v] = {
                1: (pb.GENDER_MAN, False),
                2: (pb.GENDER_WOMAN, False),
                3: (pb.GENDER_UNKNOWN, False),
                4: (pb.GENDER_UNKNOWN, True),
            }.get(code, (pb.GENDER_UNKNOWN, False))
        elif low and "male".startswith(low) and low != "":
            table[v] = (pb.GENDER_MAN, False)
        elif low and "female".startswith(low):
            table[v] = (pb.GENDER_WOMAN, False)
        elif low and "terminated".startswith(low):
            table[v] = (pb.GENDER_UNKNOWN, True)
        else:
            table[v] = (pb.GENDER_UNKNOWN, False)
    return table


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "t", "yes", "y")


def _affected(v: str) -> pb.ConditionStatus | None:
    low = v.strip().lower()
    if low in ("", "na", ".", "null"):
        return None  # kinship2: NA = missing; 0 is a real "unaffected"
    if low in ("1", "true", "t", "yes", "y", "affected", "2"):
        return pb.CONDITION_STATUS_AFFECTED
    if low in ("0", "false", "f", "no", "n", "unaffected"):
        return pb.CONDITION_STATUS_UNAFFECTED
    raise PedigreeImportError(f"unrecognised affected value {v!r}")


def _relations(text: str, extras: Extras, twin: dict[str, tuple[int, pb.ZygosityType]]) -> None:
    rows = _read(text)
    groups: dict[str, int] = {}
    types: dict[int, pb.ZygosityType] = {}
    next_group = 1
    for r in rows:
        a, b, code = r.get("id1", ""), r.get("id2", ""), r.get("code", "").lower()
        if not a or not b:
            raise PedigreeImportError("relation row lacks id1/id2")
        if code in ("4", "spouse"):
            extras.spouses.add(frozenset({a, b}))
            continue
        if code not in _TWIN_CODES:
            raise PedigreeImportError(f"unrecognised relation code {code!r} (1 MZ, 2 DZ, 3 UZ, 4 spouse)")
        ga, gb = groups.get(a), groups.get(b)
        g = ga or gb
        if g is None:
            g = next_group
            next_group += 1
        for x in (a, b):
            groups[x] = g
        # Pairwise codes within one multiple-birth set should agree; a disagreement means unknown zygosity.
        types[g] = _TWIN_CODES[code] if g not in types or types[g] == _TWIN_CODES[code] else pb.ZYGOSITY_TYPE_UNKNOWN
    for pid, g in groups.items():
        twin[pid] = (g, types[g])


def import_kinship2(text: str, relation_text: str | None = None) -> pb.PedigreeSet:
    """Parse a kinship2/Pedixplorer table (+ optional relation table) into a validated ``PedigreeSet``."""
    table, embedded = _split_tables(text)
    rows = _read(table)
    if not rows:
        raise PedigreeImportError("empty kinship2 table")
    cols = _resolve(set(rows[0]))
    affected_cols = [c for c in rows[0] if c.startswith("affect") and c not in cols.values()]
    sex_table = _sex_codes([r[cols["sex"]] for r in rows])

    extras = Extras()
    twin: dict[str, tuple[int, pb.ZygosityType]] = {}
    rel = relation_text if relation_text is not None else embedded
    if rel:
        _relations(rel, extras, twin)

    people: list[Person] = []
    for r in rows:
        pid = r[cols["id"]]
        if pid in _MISSING:
            raise PedigreeImportError("an id is missing or blank")
        gender, terminated = sex_table[r[cols["sex"]]]
        conditions: list[pb.Condition] = []
        for c in affected_cols:
            status = _affected(r[c])
            if status is not None:
                name = "" if c == "affected" or len(affected_cols) == 1 else c.removeprefix("affected").lstrip("._ ")
                conditions.append(pb.Condition(name=name, status=status))
        if "carrier" in cols and _truthy(r[cols["carrier"]]):
            conditions.append(pb.Condition(status=pb.CONDITION_STATUS_CARRIER))
        if "asymptomatic" in cols and _truthy(r[cols["asymptomatic"]]):
            conditions.append(pb.Condition(status=pb.CONDITION_STATUS_PRESYMPTOMATIC))
        annotations: list[pb.Annotation] = []
        if "dateofbirth" in cols and r[cols["dateofbirth"]] not in _MISSING:
            annotations.append(pb.Annotation(text=f"b. {r[cols['dateofbirth']]}", type=pb.ANNOTATION_TYPE_AGE))
        if "dateofdeath" in cols and r[cols["dateofdeath"]] not in _MISSING:
            annotations.append(pb.Annotation(text=f"d. {r[cols['dateofdeath']]}", type=pb.ANNOTATION_TYPE_AGE_AT_DEATH))
        outcome: pb.ReproductiveOutcome | None = None
        if terminated:
            outcome = pb.REPRODUCTIVE_OUTCOME_TERMINATION
        elif "miscarriage" in cols and _truthy(r[cols["miscarriage"]]):
            outcome = pb.REPRODUCTIVE_OUTCOME_MISCARRIAGE
        tg = twin.get(pid)
        dad, mum = r[cols["dadid"]], r[cols["momid"]]
        people.append(
            Person(
                id=pid,
                family=r[cols["famid"]] if "famid" in cols else "",
                father=None if dad in _MISSING else dad,
                mother=None if mum in _MISSING else mum,
                gender=gender,
                conditions=conditions,
                deceased="status" in cols and _truthy(r[cols["status"]]),
                proband="proband" in cols and _truthy(r[cols["proband"]]),
                consultand="consultand" in cols and _truthy(r[cols["consultand"]]),
                documented_evaluation="evaluated" in cols and _truthy(r[cols["evaluated"]]),
                reproductive_outcome=outcome,
                annotations=annotations,
                twin_group=tg[0] if tg else None,
                twin_type=tg[1] if tg else pb.ZYGOSITY_TYPE_UNSPECIFIED,
                adoption=pb.ADOPTION_IN if "adopted" in cols and _truthy(r[cols["adopted"]]) else None,
                parentage=pb.PARENTAGE_ADOPTIVE if "adopted" in cols and _truthy(r[cols["adopted"]]) else None,
            )
        )
    # Pedixplorer `fertility`: the individual is infertile -> childlessness on each of their childless matings.
    if "fertility" in cols:
        infertile = {r[cols["id"]] for r in rows if _truthy(r[cols["fertility"]])}
        for pair in extras.spouses:
            if pair & infertile:
                extras.childless[pair] = pb.CHILDLESSNESS_INFERTILITY
    return build_set(people, extras, provenance=pb.Provenance(source_format=FORMAT))
