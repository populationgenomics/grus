"""Format importers: each fixture -> validated PedigreeSet with the documented mapping; every import renders."""

from __future__ import annotations

import json
import pathlib

import pytest

from grus import convert, render
from grus.models import pedigree_pb2 as pb

_FIX = pathlib.Path(__file__).parent / "convert_fixture"


def _by_ext(ped: pb.Pedigree) -> dict[str, pb.Individual]:
    return {i.external_id: i for i in ped.individuals}


def _named(ps: pb.PedigreeSet, ind: pb.Individual) -> list[tuple[str, int]]:
    """``ind``'s conditions as ``(declared name, status)``."""
    name = {d.id: d.name for d in ps.conditions}
    return [(name[c.condition_id], c.status) for c in ind.conditions]


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


def _genotypes(ps: pb.PedigreeSet, ind: pb.Individual) -> dict[str, list[str]]:
    """``ind``'s genotypes by their locus's name, as allele ids."""
    name = {locus.id: locus.name for locus in ps.loci}
    return {name[g.locus_id]: list(g.allele_ids) for g in ind.genotypes}


def _called(ps: pb.PedigreeSet, ind: pb.Individual) -> dict[str, list[str]]:
    """``ind``'s genotypes by their locus's name, as allele names."""
    loci = {locus.id: locus for locus in ps.loci}
    names = {(locus.id, a.id): a.name for locus in ps.loci for a in locus.alleles}
    return {loci[g.locus_id].name: [names[(g.locus_id, x)] for x in g.allele_ids] for g in ind.genotypes}


def test_ped_genotypes_are_dropped_unless_asked() -> None:
    ps = convert.import_file(_FIX / "markers.ped")
    assert not ps.loci and not any(ind.genotypes for ind in ps.pedigrees[0].individuals)


def test_ped_genotypes_read_with_the_map() -> None:
    ps = convert.import_file(_FIX / "markers.ped", genotypes=True)
    # The third marker is never called, so it declares no locus; a missing marker id is named chr:bp.
    assert [(locus.name, [a.name for a in locus.alleles]) for locus in ps.loci] == [
        ("rs123", ["A", "G"]),
        ("12:2000", ["1", "2"]),
    ]
    assert not any(a.reference for locus in ps.loci for a in locus.alleles)  # PED states no reference
    assert not any(p.allele_symbols for p in ps.pedigrees)  # nor prints a figure
    ext = _by_ext(ps.pedigrees[0])
    assert _called(ps, ext["mum"]) == {"rs123": ["G", "G"], "12:2000": ["2", "2"]}
    assert _called(ps, ext["kid"]) == {"rs123": ["A", "G"], "12:2000": ["1", "2"]}
    render.render_set_svg(ps)


def test_ped_haploid_calls_are_one_copy() -> None:
    # PLINK doubles a haploid call: chrX for a man, chrY, chrMT. chrXY (pseudo-autosomal) and a woman's chrX are
    # diploid. A negative position excludes a marker, whose columns are still there.
    ped = "F a 0 0 1 1 A A C C G G T T A G\nF b 0 0 2 1 A G 0 0 G G T T A A\n"
    map_text = "X x1 0 10\nchrY y1 0 20\nMT m1 0 30\nXY p1 0 40\n1 gone 0 -50\n"
    ps = convert._ped.import_ped(ped, map_text=map_text)
    ext = _by_ext(ps.pedigrees[0])
    assert _called(ps, ext["a"]) == {"x1": ["A"], "y1": ["C"], "m1": ["G"], "p1": ["T", "T"]}
    assert _called(ps, ext["b"]) == {"x1": ["A", "G"], "m1": ["G"], "p1": ["T", "T"]}


@pytest.mark.parametrize(
    ("ped", "map_text", "match"),
    [
        ("F a 0 0 1 1 A 0\n", "1 rs1 0 10\n", "half-missing"),
        ("F a 0 0 1 1 A G\n", "1 rs1 0 10\n1 rs2 0 20\n", "6 \\+ 2 per marker"),
        ("F a 0 0 1 1 A G A G\n", "1 rs1 0 10\n1 rs1 0 20\n", r"marker\(s\) \['rs1'\] more than once"),
        ("F a 0 0 1 1 A G\n", "1 rs1\n", "expected 3 or 4 columns"),
        ("#FID IID SEX\nF a 1\n", "1 rs1 0 10\n", "has no genotype columns"),
        ("F a 0 0 1 1 A G\n", "X rs1 0 10\n", "haploid here but called heterozygous"),
        ("F a 0 0 2 1 A G\n", "MT rs1 0 10\n", "haploid here but called heterozygous"),
        ("F a 0 0 2 1 C C\n", "Y rs1 0 10\n", "on chrY is called for a woman"),
    ],
)
def test_ped_genotypes_fail_loud(ped: str, map_text: str, match: str) -> None:
    with pytest.raises(convert.PedigreeImportError, match=match):
        convert._ped.import_ped(ped, map_text=map_text)


def test_ped_genotypes_need_a_ped_and_its_map(tmp_path: pathlib.Path) -> None:
    with pytest.raises(convert.PedigreeImportError, match=r"PLINK \.ped only"):
        convert.import_file(_FIX / "two_families.fam", genotypes=True)
    (tmp_path / "x.ped").write_text("F a 0 0 1 1 A G\n")
    with pytest.raises(convert.PedigreeImportError, match=r"'x\.map' is missing"):
        convert.import_file(tmp_path / "x.ped", genotypes=True)


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
    ps = convert.import_text(text, "kinship2")
    ext = _by_ext(ps.pedigrees[0])
    assert [(d.id, d.name) for d in ps.conditions] == [("k1", "bc"), ("k2", "oc")], "declared once, by first use"
    assert _named(ps, ext["1"]) == [("bc", pb.CONDITION_STATUS_AFFECTED), ("oc", pb.CONDITION_STATUS_UNAFFECTED)]
    assert _named(ps, ext["2"]) == [("oc", pb.CONDITION_STATUS_AFFECTED)]


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
    assert _named(ps, ext["ch1"]) == [("cystic fibrosis", pb.CONDITION_STATUS_AFFECTED)] and cf.onset_age == "P2Y"
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


def _variant(state: str = "GENO:0000135", **fields: object) -> dict[str, object]:
    """A CFTR variationDescriptor in ``state``; ``fields`` replace its own (None removes one)."""
    vd: dict[str, object] = {
        "label": "c.1521_1523del",
        "geneContext": {"valueId": "HGNC:1884", "symbol": "CFTR"},
        "expressions": [{"syntax": "hgvs.c", "value": "NM_000492.4:c.1521_1523del"}],
        "vcfRecord": {
            "genomeAssembly": "GRCh38",
            "chrom": "7",
            "pos": "117559590",
            "id": "rs113993960",
            "ref": "ATCT",
            "alt": "A",
        },
        "allelicState": {"id": state, "label": "x"},
    }
    vd.update(fields)
    return {k: v for k, v in vd.items() if v is not None}


def _family_with_variants(*stated: tuple[str, dict[str, object]], biosamples: list[object] | None = None) -> str:
    """A trio whose child's packet interprets each ``(subjectOrBiosampleId, variationDescriptor)`` in ``stated``."""
    persons = [
        {"familyId": "A", "individualId": i, "paternalId": f, "maternalId": m, "sex": s}
        for i, f, m, s in (("f", "0", "0", "MALE"), ("m", "0", "0", "FEMALE"), ("c", "f", "m", "MALE"))
    ]
    interpretations = [
        {"subjectOrBiosampleId": who, "variantInterpretation": {"variationDescriptor": vd}} for who, vd in stated
    ]
    proband = {
        "subject": {"id": "c"},
        "biosamples": biosamples or [],
        "interpretations": [{"id": "i1", "diagnosis": {"genomicInterpretations": interpretations}}],
    }
    return json.dumps({"id": "A", "proband": proband, "pedigree": {"persons": persons}})


def _import(*stated: tuple[str, dict[str, object]], biosamples: list[object] | None = None) -> pb.PedigreeSet:
    return convert.import_text(_family_with_variants(*stated, biosamples=biosamples), "phenopackets")


def test_phenopackets_variant_interpretations_become_genotypes() -> None:
    ps = _import(
        ("c", _variant("GENO:0000136")),
        ("m", _variant("GENO:0000135")),
        ("bs1", _variant("GENO:0000136")),  # the child's biosample, stating what the child's own does
        biosamples=[{"id": "bs1"}],
    )
    (locus,) = ps.loci  # one variant, however many interpretations state it
    assert (locus.gene, locus.name) == ("CFTR", "c.1521_1523del")
    ref, alt = locus.alleles
    assert ref.reference and ref.name == "ATCT"
    assert {(i.scheme, i.value) for i in alt.identifiers} == {
        (pb.IDENTIFIER_SCHEME_HGVS, "NM_000492.4:c.1521_1523del"),
        (pb.IDENTIFIER_SCHEME_RSID, "rs113993960"),
    }
    ext = _by_ext(ps.pedigrees[0])
    assert _genotypes(ps, ext["m"]) == {"c.1521_1523del": ["ref", "alt"]}
    assert _genotypes(ps, ext["c"]) == {"c.1521_1523del": ["alt", "alt"]}
    assert not ext["f"].genotypes  # not interpreted: no genotype, nothing inferred


@pytest.mark.parametrize(
    ("state", "alleles"),
    [
        ("GENO:0000134", ["alt"]),
        ("GENO:0000604", ["alt"]),
        ("GENO:0000458", ["ref", "alt"]),
        ("GENO:0000402", ["ref", "alt"]),
    ],
)
def test_phenopackets_allelic_states(state: str, alleles: list[str]) -> None:
    ps = _import(("c", _variant(state)))
    assert _genotypes(ps, _by_ext(ps.pedigrees[0])["c"]) == {"c.1521_1523del": alleles}


def test_phenopackets_unstated_zygosity_states_no_genotype() -> None:
    for vd in (_variant("GENO:0000137"), _variant(allelicState=None)):
        assert not _import(("c", vd)).loci


def test_phenopackets_allelic_state_by_label() -> None:
    ps = _import(("c", _variant(allelicState={"id": "GENO:9999999", "label": "Homozygous"})))
    assert _genotypes(ps, _by_ext(ps.pedigrees[0])["c"]) == {"c.1521_1523del": ["alt", "alt"]}


def test_phenopackets_one_locus_per_distinct_description() -> None:
    # The same description, in any order of packets and under any descriptor id, is one locus; distinct descriptions
    # are distinct loci, named as written (a label, else the first expression), never by reading the HGVS.
    ps = _import(("c", _variant("GENO:0000136", id="v1")), ("m", _variant(id="v2")))
    assert len(ps.loci) == 1
    a = _variant(label=None, vcfRecord=None, expressions=[{"syntax": "hgvs.p", "value": "p.Arg2His"}])
    b = _variant(label=None, vcfRecord=None, expressions=[{"syntax": "hgvs.c", "value": "c.5G>T"}])
    for stated in ((("c", a), ("m", b)), (("m", b), ("c", a))):
        assert sorted(locus.name for locus in _import(*stated).loci) == ["c.5G>T", "p.Arg2His"]
    no_gene = _variant(label=None, vcfRecord=None, geneContext=None)
    assert [(locus.gene, locus.name) for locus in _import(("c", no_gene)).loci] == [("", "NM_000492.4:c.1521_1523del")]


def test_phenopackets_a_variant_stated_only_by_its_vrs_id_is_named_by_it() -> None:
    by_vrs = [
        _variant(label=None, vcfRecord=None, expressions=None, variation={"allele": {"_id": vrs}})
        for vrs in ("ga4gh:VA.a", "ga4gh:VA.b")
    ]
    ps = _import(("c", by_vrs[0]), ("m", by_vrs[1]))
    assert [locus.name for locus in ps.loci] == ["ga4gh:VA.a", "ga4gh:VA.b"]


def test_phenopackets_a_variant_without_label_or_expression_is_named_by_its_vcf_record() -> None:
    first = _variant(label=None, expressions=None)
    second = _variant(label=None, expressions=None, vcfRecord={"chrom": "7", "pos": "100", "ref": "G", "alt": "T"})
    assert [locus.name for locus in _import(("c", first), ("c", second)).loci] == ["7:117559590 ATCT>A", "7:100 G>T"]


def test_phenopackets_a_biosample_is_its_individuals() -> None:
    ps = _import(("bs1", _variant()), biosamples=[{"id": "bs1", "individualId": "f"}])
    assert _genotypes(ps, _by_ext(ps.pedigrees[0])["f"]) == {"c.1521_1523del": ["ref", "alt"]}


@pytest.mark.parametrize(
    ("stated", "match"),
    [
        ((("c", _variant("GENO:0000136")), ("c", _variant("GENO:0000135"))), "different allelic states"),
        ((("ghost", _variant()),), "names neither a person nor a biosample"),
        ((("c", _variant("GENO:0000999")),), "unrecognised allelicState"),
        (  # a descriptor's id is local to its document and names no variant
            (("c", _variant(label=None, vcfRecord=None, expressions=None, id="v1")),),
            "states no label, expression, VCF record or VRS id",
        ),
        (  # one name, two descriptions: whether they are one variant is the caller's to settle, in either order
            (("c", _variant()), ("m", _variant(vcfRecord=None))),
            "is described two different ways",
        ),
        (
            (("m", _variant(vcfRecord=None)), ("c", _variant())),
            "is described two different ways",
        ),
    ],
)
def test_phenopackets_variants_fail_loud(stated: tuple[tuple[str, dict[str, object]], ...], match: str) -> None:
    with pytest.raises(convert.PedigreeImportError, match=match):
        _import(*stated)


# --- Open Pedigree -------------------------------------------------------------------------------------------------


def test_openpedigree_simple_json_mapping() -> None:
    ps = convert.import_file(_FIX / "simple.openpedigree.json")
    (ped,) = ps.pedigrees
    ext = _by_ext(ped)
    assert ps.provenance.source_format == "openpedigree"
    assert ext["f11"].deceased and ext["ch1"].proband and ext["ch1"].documented_evaluation
    assert [n for n, _ in _named(ps, ext["f12"])] == ["603235", "142763", "custom disorder"]
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


def test_families_of_one_file_share_the_declarations() -> None:
    # Two Phenopackets families naming one disease: one declaration, referenced from both pedigrees.
    person = '{{"familyId": "{f}", "individualId": "x", "paternalId": "0", "maternalId": "0", "sex": "MALE"}}'
    packet = '{{"subject": {{"id": "x"}}, "diseases": [{{"term": {{"id": "OMIM:219700", "label": "CF"}}}}]}}'
    fam = '{{"id": "{f}", "proband": ' + packet + ', "pedigree": {{"persons": [' + person + "]}}}}"
    ps = convert.import_text("[" + fam.format(f="A") + ", " + fam.format(f="B") + "]", "phenopackets")
    assert [(d.id, d.name) for d in ps.conditions] == [("k1", "CF")]
    assert [c.condition_id for p in ps.pedigrees for c in p.individuals[0].conditions] == ["k1", "k1"]


def test_a_person_stating_one_condition_twice_fails_loud() -> None:
    # kinship2's carrier and asymptomatic columns both state the sole, unnamed condition: one entry per condition.
    text = "id,dadid,momid,sex,carrier,asymptomatic\n1,NA,NA,1,1,1\n"
    with pytest.raises(
        convert.PedigreeImportError, match=r"individual '1' states condition\(s\) \[''\] more than once"
    ):
        convert.import_text(text, "kinship2")
