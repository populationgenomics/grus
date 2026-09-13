"""PLINK ``.fam`` / ``.ped`` / ``.psam`` and LINKAGE pre-makeped importer (docs/design/convert.md, "PED").

Six whitespace-separated columns, no header: ``FID IID PAT MAT SEX PHENO`` (``.ped`` appends genotype
columns, which are dropped; a five-column LINKAGE ``.pre`` omits PHENO). A ``.psam`` names its columns on
the last ``#``-prefixed header line (``#FID``/``#IID``); without one, ``.fam`` order is assumed.

Codes (PLINK 1.9/2 formats page): parent ``0`` = not in dataset (one ``0`` = a single known parent, kept as
a single-parent mating — PLINK does not synthesise the other); sex ``1``/``M`` man, ``2``/``F`` woman, else
unknown; phenotype ``2`` affected, ``1`` unaffected, ``0``/``-9``/non-numeric missing (omitted), any other
numeric = a quantitative trait, kept verbatim as a MEASUREMENT annotation. ``pheno_01=True`` is PLINK's
``--1`` (``1`` affected, ``0`` unaffected). LINKAGE letter codes ``A``/``U``/``X`` are accepted too.
"""

from __future__ import annotations

from grus.convert import _core
from grus.models import pedigree_pb2 as pb

FORMAT = "ped"
SUFFIXES = (".ped", ".fam", ".psam", ".pre")

_MISSING_PARENT = {"0", "", "NA", "."}
_MISSING_PHENO = {"0", "-9", "NA", "X", "x", "", "."}


def _gender(code: str) -> pb.Gender:
    if code in ("1", "M", "m"):
        return pb.GENDER_MAN
    if code in ("2", "F", "f"):
        return pb.GENDER_WOMAN
    return pb.GENDER_UNKNOWN


def _phenotype(code: str, *, pheno_01: bool) -> tuple[pb.Condition | None, pb.Annotation | None]:
    """The single PED phenotype column -> (Condition, quantitative annotation), at most one set."""
    if code in ("A", "a"):
        return pb.Condition(status=pb.CONDITION_STATUS_AFFECTED), None
    if code in ("U", "u"):
        return pb.Condition(status=pb.CONDITION_STATUS_UNAFFECTED), None
    affected, unaffected = ("1", "0") if pheno_01 else ("2", "1")
    if code == affected:
        return pb.Condition(status=pb.CONDITION_STATUS_AFFECTED), None
    if code == unaffected:
        return pb.Condition(status=pb.CONDITION_STATUS_UNAFFECTED), None
    if code in _MISSING_PHENO:
        return None, None
    try:
        float(code)
    except ValueError:
        return None, None  # non-numeric = missing (PLINK rejects; we treat as not indicated)
    return None, pb.Annotation(text=code, type=pb.ANNOTATION_TYPE_MEASUREMENT)


def _columns(lines: list[str]) -> tuple[dict[str, int], list[list[str]]]:
    """Resolve the column index of each PED field; ``.psam`` from its header, else fixed ``.fam`` order."""
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in lines:
        if line.startswith("#"):
            if line.startswith(("#FID", "#IID")):
                header = line[1:].split()
            continue
        rows.append(line.split())
    if header is None:
        return {"FID": 0, "IID": 1, "PAT": 2, "MAT": 3, "SEX": 4, "PHENO": 5}, rows
    cols = {name: i for i, name in enumerate(header)}
    if "IID" not in cols:
        raise _core.PedigreeImportError("psam header names no IID column")
    for name in ("PAT", "MAT", "SEX"):
        cols.setdefault(name, -1)
    # The first non-standard column is the phenotype, per the .psam rules; none -> no phenotype.
    extra = [n for n in header if n not in ("FID", "IID", "SID", "PAT", "MAT", "SEX")]
    cols["PHENO"] = cols[extra[0]] if extra else -1
    cols.setdefault("FID", -1)
    return cols, rows


def import_ped(text: str, *, pheno_01: bool = False) -> pb.PedigreeSet:
    """Parse PED/FAM/PSAM/LINKAGE-pre text into a validated ``PedigreeSet`` (one pedigree per family id)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cols, rows = _columns(lines)
    people: list[_core.Person] = []
    for n, row in enumerate(rows, 1):
        if len(row) < 5:
            raise _core.PedigreeImportError(
                f"line {n}: expected at least 5 columns (FID IID PAT MAT SEX), got {len(row)}"
            )

        def col(name: str, row: list[str] = row) -> str:
            i = cols[name]
            return row[i] if 0 <= i < len(row) else ""

        iid = col("IID")
        if iid in ("", "0"):
            raise _core.PedigreeImportError(f"line {n}: within-family id may not be '0' or empty")
        father, mother = col("PAT"), col("MAT")
        condition, measurement = _phenotype(col("PHENO"), pheno_01=pheno_01)
        people.append(
            _core.Person(
                id=iid,
                family=col("FID"),
                father=None if father in _MISSING_PARENT else father,
                mother=None if mother in _MISSING_PARENT else mother,
                gender=_gender(col("SEX")),
                conditions=[condition] if condition else [],
                annotations=[measurement] if measurement else [],
            )
        )
    if not people:
        raise _core.PedigreeImportError("no pedigree rows found")
    return _core.build_set(people, provenance=pb.Provenance(source_format=FORMAT))
