"""Format importers: each fixture -> validated PedigreeSet with the documented mapping; every import renders."""

from __future__ import annotations

import pathlib

import pytest

from grus import convert, render
from grus.models import pedigree_pb2 as pb

_FIX = pathlib.Path(__file__).parent / "convert_fixture"


def _by_ext(ped: pb.Pedigree) -> dict[str, pb.Individual]:
    return {i.external_id: i for i in ped.individuals}


def _mating_of(ped: pb.Pedigree, child_ext: str) -> pb.Mating:
    pos = _by_ext(ped)[child_ext]
    for m in ped.matings:
        if any((o.child.generation, o.child.index) == (pos.generation, pos.index) for o in m.offspring):
            return m
    raise AssertionError(child_ext)


@pytest.mark.parametrize(
    "name",
    [
        "basic.pre",
        "two_families.fam",
        "trio.psam",
        "kinship2.csv",
        "family.phenopackets.json",
        "simple.openpedigree.json",
    ],
)
def test_every_fixture_imports_and_renders(name: str) -> None:
    ps = convert.import_file(_FIX / name, fmt="kinship2" if name.endswith(".csv") else None)
    assert ps.pedigrees and ps.provenance.source_format
    svg = render.render_set_svg(ps)
    assert "<svg" in svg and "deferred" not in svg.lower()


# --- PED -------------------------------------------------------------------------------------------------------


def test_linkage_pre_makeped_five_columns() -> None:
    ps = convert.import_file(_FIX / "basic.pre")
    (ped,) = ps.pedigrees
    ext = _by_ext(ped)
    assert ped.id == "1" and len(ext) == 6
    assert ext["4"].generation == 2 and ext["5"].generation == 3 and ext["6"].generation == 3
    assert not ext["4"].conditions  # no phenotype column
    assert ext["1"].gender == pb.GENDER_MAN and ext["2"].gender == pb.GENDER_WOMAN


def test_fam_families_phenotype_and_single_parent() -> None:
    ps = convert.import_file(_FIX / "two_families.fam")
    assert [p.id for p in ps.pedigrees] == ["FAM1", "FAM2"]
    f1, f2 = ps.pedigrees
    ext = _by_ext(f1)
    assert ext["kid1"].conditions[0].status == pb.CONDITION_STATUS_AFFECTED
    assert ext["dad"].conditions[0].status == pb.CONDITION_STATUS_UNAFFECTED
    assert not ext["kid2"].conditions  # -9 = missing -> not indicated
    (m,) = f2.matings
    assert m.HasField("partner_a") and not m.HasField("partner_b")  # mother only, no phantom father
    assert ps.provenance.source_format == "ped"


def test_psam_header_columns() -> None:
    ps = convert.import_file(_FIX / "trio.psam")
    (ped,) = ps.pedigrees
    assert ped.id == "T"
    assert _by_ext(ped)["kid"].conditions[0].status == pb.CONDITION_STATUS_AFFECTED


def test_ped_pheno_01_flag_and_quantitative_trait() -> None:
    ps = convert._ped.import_ped("F a 0 0 1 1\nF b 0 0 2 0\nF c a b 1 3.5\n", pheno_01=True)
    ext = _by_ext(ps.pedigrees[0])
    assert ext["a"].conditions[0].status == pb.CONDITION_STATUS_AFFECTED
    assert ext["b"].conditions[0].status == pb.CONDITION_STATUS_UNAFFECTED
    assert ext["c"].annotations[0].type == pb.ANNOTATION_TYPE_MEASUREMENT and ext["c"].annotations[0].text == "3.5"


def test_ped_rejects_short_rows_and_zero_iid() -> None:
    with pytest.raises(convert.PedigreeImportError, match="at least 5 columns"):
        convert.import_text("F a 0 0\n", "ped")
    with pytest.raises(convert.PedigreeImportError, match="'0'"):
        convert.import_text("F 0 0 0 1 1\n", "ped")


# --- kinship2 ----------------------------------------------------------------------------------------------------


def test_kinship2_embedded_relation_twins_and_spouse() -> None:
    ps = convert.import_file(_FIX / "kinship2.csv", fmt="kinship2")
    (ped,) = ps.pedigrees
    ext = _by_ext(ped)
    assert ext["1"].deceased and ext["3"].proband
    assert ext["3"].conditions[0].status == pb.CONDITION_STATUS_AFFECTED
    m = _mating_of(ped, "3")
    twins = [o for o in m.offspring if o.HasField("twin_group")]
    assert len(twins) == 2 and {o.twin_type for o in twins} == {pb.ZYGOSITY_TYPE_MONOZYGOTIC}
    childless = [m for m in ped.matings if not m.offspring]
    assert len(childless) == 1  # the code-4 spouse pair 5 x 6
    assert ext["6"].generation == ext["5"].generation  # marry-in aligned to the spouse's row


def test_kinship2_sidecar_relation(tmp_path: pathlib.Path) -> None:
    (tmp_path / "fam.csv").write_text("id,dadid,momid,sex\n1,0,0,1\n2,0,0,2\n3,1,2,1\n4,1,2,1\n")
    (tmp_path / "fam.rel.csv").write_text("id1,id2,code\n3,4,DZ twin\n")
    ps = convert.import_file(tmp_path / "fam.csv", fmt="kinship2")
    (m,) = ps.pedigrees[0].matings
    assert {o.twin_type for o in m.offspring} == {pb.ZYGOSITY_TYPE_DIZYGOTIC}


def test_kinship2_sex_words_shifted_codes_and_terminated() -> None:
    text = "id,dadid,momid,sex\na,,,male\nb,,,fem\nc,a,b,terminated\n"
    ext = _by_ext(convert.import_text(text, "kinship2").pedigrees[0])
    assert ext["a"].gender == pb.GENDER_MAN and ext["b"].gender == pb.GENDER_WOMAN
    assert ext["c"].gender == pb.GENDER_UNKNOWN and ext["c"].reproductive_outcome == pb.REPRODUCTIVE_OUTCOME_TERMINATION
    shifted = "id\tdadid\tmomid\tsex\na\t0\t0\t0\nb\t0\t0\t1\n"
    ext = _by_ext(convert.import_text(shifted, "kinship2").pedigrees[0])
    assert ext["a"].gender == pb.GENDER_MAN and ext["b"].gender == pb.GENDER_WOMAN


def test_kinship2_multi_trait_affected_matrix() -> None:
    text = "id,dadid,momid,sex,affected.bc,affected.oc\n1,NA,NA,1,1,0\n2,NA,NA,2,NA,1\n"
    ext = _by_ext(convert.import_text(text, "kinship2").pedigrees[0])
    assert [(c.name, c.status) for c in ext["1"].conditions] == [
        ("bc", pb.CONDITION_STATUS_AFFECTED),
        ("oc", pb.CONDITION_STATUS_UNAFFECTED),
    ]
    assert [(c.name, c.status) for c in ext["2"].conditions] == [("oc", pb.CONDITION_STATUS_AFFECTED)]


def test_kinship2_missing_required_column() -> None:
    with pytest.raises(convert.PedigreeImportError, match="'sex'"):
        convert.import_text("id,dadid,momid\n1,,\n", "kinship2")
    with pytest.raises(convert.PedigreeImportError, match="more fields"):
        convert.import_text("id,dadid,momid,sex\n1,,,1\n2,,,1,extra\n", "kinship2")  # one ragged row, not R row names


def test_kinship2_reads_r_write_table_output() -> None:
    # R's write.table default: space-separated, quoted strings, an unnamed leading row-names column.
    text = (
        '"ped" "id" "father" "mother" "sex" "affected"\n"1" 1 101 0 0 1 0\n"2" 1 102 0 0 2 1\n"3" 1 103 101 102 1 1\n'
    )
    (ped,) = convert.import_text(text, "kinship2").pedigrees
    ext = _by_ext(ped)
    assert ped.id == "1" and set(ext) == {"101", "102", "103"}
    assert ext["103"].conditions[0].status == pb.CONDITION_STATUS_AFFECTED and ext["101"].gender == pb.GENDER_MAN


# --- Phenopackets ---------------------------------------------------------------------------------------------------


def test_phenopackets_family_mapping() -> None:
    ps = convert.import_file(_FIX / "family.phenopackets.json")
    (ped,) = ps.pedigrees
    assert ped.id == "FAM1" and ps.provenance.source_format == "phenopackets"
    ext = _by_ext(ped)
    assert ext["ch1"].proband
    (cf,) = ext["ch1"].conditions  # diseases win over the bare affectedStatus
    assert (cf.name, cf.status, cf.onset_age) == ("cystic fibrosis", pb.CONDITION_STATUS_AFFECTED, "P2Y")
    assert ext["m11"].deceased
    assert [(a.type, a.text) for a in ext["m11"].annotations] == [(pb.ANNOTATION_TYPE_AGE_AT_DEATH, "P72Y")]
    assert ext["f11"].conditions[0].status == pb.CONDITION_STATUS_UNAFFECTED
    assert ext["ch2"].gender == pb.GENDER_UNKNOWN and not ext["ch2"].conditions  # OTHER_SEX, MISSING status
    assert _mating_of(ped, "ch1").consanguineous is True


def test_phenopackets_snake_case_and_array_of_families() -> None:
    text = """[{"id": "A", "pedigree": {"persons": [
        {"family_id": "A", "individual_id": "x", "paternal_id": "0", "maternal_id": "0",
         "sex": "MALE", "affected_status": "AFFECTED"}]}},
      {"id": "B", "pedigree": {"persons": [
        {"family_id": "B", "individual_id": "y", "paternal_id": "0", "maternal_id": "0", "sex": "FEMALE"}]}}]"""
    ps = convert.import_text(text, "phenopackets")
    assert [p.id for p in ps.pedigrees] == ["A", "B"]


def test_phenopackets_stray_packet_fails_loud() -> None:
    text = """{"id": "A", "relatives": [{"subject": {"id": "ghost"}}],
      "pedigree": {"persons": [{"familyId": "A", "individualId": "x", "paternalId": "0", "maternalId": "0",
                                "sex": "MALE"}]}}"""
    with pytest.raises(convert.PedigreeImportError, match="ghost"):
        convert.import_text(text, "phenopackets")


# --- Open Pedigree -------------------------------------------------------------------------------------------------


def test_openpedigree_simple_json_mapping() -> None:
    ps = convert.import_file(_FIX / "simple.openpedigree.json")
    (ped,) = ps.pedigrees
    ext = _by_ext(ped)
    assert ps.provenance.source_format == "openpedigree"
    assert ext["f11"].deceased and ext["ch1"].proband and ext["ch1"].documented_evaluation
    assert [c.name for c in ext["f12"].conditions] == ["603235", "142763", "custom disorder"]
    assert ext["ch5"].conditions[0].status == pb.CONDITION_STATUS_CARRIER
    assert ext["ch4"].reproductive_outcome == pb.REPRODUCTIVE_OUTCOME_MISCARRIAGE
    assert ext["ch4"].annotations[0].type == pb.ANNOTATION_TYPE_GESTATIONAL_AGE
    m = _mating_of(ped, "ch1")
    by_child = {
        ext_id: o
        for ext_id in ("ch2", "ch3", "ch5")
        for o in m.offspring
        if (o.child.generation, o.child.index) == (ext[ext_id].generation, ext[ext_id].index)
    }
    assert (
        by_child["ch2"].twin_type == pb.ZYGOSITY_TYPE_MONOZYGOTIC
        and by_child["ch2"].twin_group == by_child["ch3"].twin_group
    )
    assert by_child["ch5"].adoption == pb.ADOPTION_IN and by_child["ch5"].parentage == pb.PARENTAGE_ADOPTIVE
    rel = next(m for m in ped.matings if not m.offspring)
    assert rel.status == pb.RELATIONSHIP_STATUS_SEPARATED
    assert rel.childlessness == pb.CHILDLESSNESS_INFERTILITY and rel.annotations[0].text == "vasectomy"
    assert not rel.consanguineous
    # generations: grandparents 1, parents + m22 (marry-in) 2, children 3
    assert {ext[x].generation for x in ("f11", "m11", "f12", "m12")} == {1}
    assert {ext[x].generation for x in ("m21", "f21", "m22")} == {2}
    assert {ext[x].generation for x in ("ch1", "ch2", "ch3", "ch4", "ch5")} == {3}


def test_openpedigree_no_default_proband_and_single_parent() -> None:
    ps = convert.import_text('[{"name": "a", "sex": "f"}, {"name": "b", "sex": "m", "mother": "a"}]', "openpedigree")
    (ped,) = ps.pedigrees
    assert not any(i.proband for i in ped.individuals)
    (m,) = ped.matings
    assert m.HasField("partner_a") and not m.HasField("partner_b")


def test_openpedigree_bad_reference_fails_loud() -> None:
    with pytest.raises(convert.PedigreeImportError, match="mother reference"):
        convert.import_text('[{"name": "b", "sex": "m", "mother": "nobody"}]', "openpedigree")


# --- registry ------------------------------------------------------------------------------------------------------


def test_infer_format_sniffs_json_and_rejects_unknown(tmp_path: pathlib.Path) -> None:
    assert convert.infer_format(_FIX / "family.phenopackets.json") == "phenopackets"
    assert convert.infer_format(_FIX / "simple.openpedigree.json") == "openpedigree"
    assert convert.infer_format(pathlib.Path("x.fam")) == "ped"
    with pytest.raises(convert.UnknownFormatError):
        convert.infer_format(_FIX / "kinship2.csv")
    with pytest.raises(convert.UnknownFormatError, match="unknown format"):
        convert.import_text("", "gedcom")
