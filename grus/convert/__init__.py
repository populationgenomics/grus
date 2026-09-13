"""grus.convert — importers from external pedigree file formats into the IR (docs/design/convert.md).

Each importer is a pure function ``str -> PedigreeSet`` registered by format name; ``import_file``
picks one by explicit name, by file suffix, or (``.json``) by sniffing the document shape. Every
importer synthesises the ``Position`` identity (external formats carry string ids, not
generation/index — see the design doc), records the source in ``Provenance.source_format``, and
validates its output through ``grus.ir`` before returning, so a caller never holds an invalid IR.

Formats: ``ped`` (PLINK .fam/.ped/.psam, LINKAGE .pre), ``kinship2`` (kinship2/Pedixplorer table, with
the relation matrix embedded or in a ``<stem>.rel.csv`` sidecar), ``phenopackets`` (GA4GH Phenopackets v2
``Family`` JSON), ``openpedigree`` (PhenoTips / Open Pedigree simple JSON).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path

from grus.convert import _kinship2, _openpedigree, _ped, _phenopackets
from grus.convert._core import Extras, PedigreeImportError, Person, build_pedigree, build_set
from grus.models import pedigree_pb2 as pb

Importer = Callable[[str], pb.PedigreeSet]

# format name -> importer
IMPORTERS: dict[str, Importer] = {
    _ped.FORMAT: _ped.import_ped,
    _kinship2.FORMAT: _kinship2.import_kinship2,
    _phenopackets.FORMAT: _phenopackets.import_phenopackets,
    _openpedigree.FORMAT: _openpedigree.import_openpedigree,
}

# file suffix (lower-case, with dot) -> format name. ``.json`` is sniffed, ``.csv`` needs an explicit format.
_SUFFIXES: dict[str, str] = {suffix: _ped.FORMAT for suffix in _ped.SUFFIXES}


class UnknownFormatError(ValueError):
    """No importer is registered for the requested / inferred format."""


def infer_format(path: Path | str, text: str | None = None) -> str:
    """The format name for ``path``: by suffix, or for ``.json`` by the document's shape.

    A Phenopackets ``Family`` is an object with ``pedigree``/``proband``; Open Pedigree simple JSON is an
    array of person objects. Raises ``UnknownFormatError`` when neither applies.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _SUFFIXES:
        return _SUFFIXES[suffix]
    if suffix == ".json":
        doc = None
        with contextlib.suppress(json.JSONDecodeError, OSError):
            doc = json.loads(text if text is not None else path.read_text())
        if isinstance(doc, dict) and ("pedigree" in doc or "proband" in doc or "relatives" in doc):
            return _phenopackets.FORMAT
        if isinstance(doc, list) and doc and isinstance(doc[0], dict):
            first = {k.lower() for k in doc[0]}
            if "pedigree" in first or "proband" in first:
                return _phenopackets.FORMAT
            if first & {"name", "sex", "id", "externalid", "partner1"}:
                return _openpedigree.FORMAT
    raise UnknownFormatError(f"cannot infer a pedigree format from {path.name!r}; pass --from")


def import_text(text: str, fmt: str) -> pb.PedigreeSet:
    """Import ``text`` in format ``fmt`` (a key of ``IMPORTERS``)."""
    try:
        importer = IMPORTERS[fmt]
    except KeyError:
        known = ", ".join(sorted(IMPORTERS)) or "(none)"
        raise UnknownFormatError(f"unknown format {fmt!r}; known: {known}") from None
    return importer(text)


def import_file(path: Path | str, fmt: str | None = None) -> pb.PedigreeSet:
    """Import ``path``; the format is ``fmt`` or inferred (``infer_format``).

    kinship2 tables may carry their relation matrix in a ``<stem>.rel.csv`` sidecar next to the file.
    """
    path = Path(path)
    text = path.read_text()
    fmt = fmt or infer_format(path, text)
    if fmt == _kinship2.FORMAT:
        sidecar = path.with_name(path.stem + _kinship2.SIDECAR_SUFFIX)
        relation = sidecar.read_text() if sidecar.exists() else None
        return _kinship2.import_kinship2(text, relation)
    return import_text(text, fmt)


__all__ = [
    "IMPORTERS",
    "Extras",
    "Importer",
    "PedigreeImportError",
    "Person",
    "UnknownFormatError",
    "build_pedigree",
    "build_set",
    "import_file",
    "import_text",
    "infer_format",
]
