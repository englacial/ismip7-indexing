"""Unit tests for the ISMIP7 convention models, parsers and registries."""

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from ismip7_helper.conventions import (
    ForcingKind,
    Severity,
    SubmissionFile,
    VariableType,
    experiment_registry,
    grid_for_shape,
    grid_registry,
    parse_key,
    parse_submission_path,
    variable_registry,
)
from ismip7_helper.conventions.parse import parse_forcing_key, parse_submission_filename

DATA = Path(__file__).parent / "data"


# --------------------------------------------------------------------------- registries


def test_variable_registry_matches_checker():
    v = variable_registry()
    assert len(v) == 42
    assert v["lithk"].type == VariableType.ST and v["lithk"].mandatory
    assert v["acabf"].type == VariableType.FL and v["acabf"].units == "kg m-2 s-1"
    assert v["litemp"].dims == ("x", "y", "z", "t")
    assert v["lim"].is_scalar and not v["lim"].is_gridded
    assert v["refgeoid"].dims == ("x", "y") and not v["refgeoid"].has_time
    assert v["hfgeoubed"].range_severity == Severity.WARNING
    assert v["lithk"].value_range("GrIS") == (0, 5000)
    assert v["lithk"].fill_policy == "forbidden"


def test_experiment_registry():
    e = experiment_registry()
    assert set(e) == {
        "historical",
        "ssp370",
        "ssp245",
        "ssp126",
        "ssp585",
        "ssp534-over",
        "ctrl",
        "ocx",
    }
    assert e["ssp585"].allows_years(2015, 2300)
    assert not e["ssp585"].allows_years(2015, 2100)
    assert e["historical"].allows_years(1985, 2014)
    assert not e["historical"].fixed_start


def test_grid_registry():
    g = grid_registry()
    assert len(g) == 11
    ais8 = g[("AIS", 8000)]
    assert ais8.shape == (761, 761) and ais8.epsg == 3031
    assert ais8.x()[0] == -3040000 and ais8.x()[-1] == 3040000
    gris1 = g[("GrIS", 1000)]
    assert gris1.shape == (2881, 1681) and gris1.epsg == 3413
    assert np.isclose(gris1.y()[-1], -3450000 + 2880 * 1000)
    assert grid_for_shape("AIS", 3041, 3041).resolution_m == 2000
    assert grid_for_shape("GrIS", 721, 421).resolution_m == 4000
    assert grid_for_shape("AIS", 2881, 1681) is None


def test_grid_crs_attrs_are_hemisphere_correct():
    ais = grid_registry()[("AIS", 8000)].cf_grid_mapping_attrs()
    gris = grid_registry()[("GrIS", 1000)].cf_grid_mapping_attrs()
    assert ais["latitude_of_projection_origin"] == -90.0 and ais["standard_parallel"] == -71.0
    assert (
        gris["latitude_of_projection_origin"] == 90.0
        and gris["straight_vertical_longitude_from_pole"] == -45.0
    )
    assert "3031" in ais["crs_wkt"] and "3413" in gris["crs_wkt"]


# --------------------------------------------------------------------------- submissions


def test_submission_filename_roundtrip():
    s = parse_submission_filename(
        "lithk_GrIS_VUW_PISM1_m001_CESM2-WACCM_f001_ctrl_C001_2015-2300.nc"
    )
    assert s is not None
    assert (s.variable, s.region, s.group, s.model) == ("lithk", "GrIS", "VUW", "PISM1")
    assert s.esm == "CESM2-WACCM" and s.experiment == "ctrl" and s.set_id == "CORE"
    assert s.expected_directory == "GrIS/VUW/PISM1/CORE/C001"
    assert s.check() == []


def test_submission_path_from_uaf_upload():
    key = "ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C007/acabf_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp585_C007_2015-2300.nc"
    r = parse_key(key)
    assert r.submission is not None and r.ok and r.findings == []
    assert r.submission.run_id == "GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp585_C007"


def test_submission_uppercase_ocx_is_flagged():
    # UAF's C011 set used 'OCX'; the checker's list has lower-case 'ocx'.
    key = "ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/acabf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc"
    r = parse_key(key)
    codes = {f.code for f in r.findings}
    assert "unknown_experiment" in codes
    msg = next(f.message for f in r.findings if f.code == "unknown_experiment")
    assert "lower case" in msg


def test_submission_year_range_checked():
    r = parse_submission_path(
        "GrIS/UAF/PISM/CORE/C007/lithk_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp585_C007_2015-2100.nc"
    )
    assert {f.code for f in r.findings} == {"year_range"}
    assert not r.ok


def test_submission_bad_names():
    r = parse_submission_path(
        "GrIS/UAF/PISM/CORE/C001/scalar_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_historical_C001_1985-2014.nc"
    )
    assert {f.code for f in r.findings} == {"unknown_variable"}
    r = parse_submission_path("GrIS/UAF/PISM/CORE/C001/lithk_GrIS_UAF_PISM_historical.nc")
    assert {f.code for f in r.findings} == {"bad_filename"}
    r = parse_submission_path("GrIS/UAF/PISM/CORE/C001/compliance_checker_log.txt")
    assert {f.code for f in r.findings} == {"non_data_file"} and r.ok


def test_submission_directory_mismatch():
    r = parse_submission_path(
        "GrIS/UAF/PISM/ESM/C001/lithk_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ctrl_C001_2015-2300.nc"
    )
    assert {f.code for f in r.findings} == {"directory_mismatch"}


def test_submission_field_rules():
    with pytest.raises(ValidationError):
        SubmissionFile(
            variable="lithk",
            region="GrIS",
            group="MY_GROUP",
            model="PISM1",
            ism_member="m001",
            esm="CESM2-WACCM",
            forcing_member="f001",
            experiment="ctrl",
            set_counter="C001",
            year_start=2015,
            year_end=2300,
            filename="x",
        )
    with pytest.raises(ValidationError):
        SubmissionFile(
            variable="lithk",
            region="GrIS",
            group="VUW",
            model="PISM1",
            ism_member="m1",
            esm="CESM2-WACCM",
            forcing_member="f001",
            experiment="ctrl",
            set_counter="C001",
            year_start=2015,
            year_end=2300,
            filename="x",
        )


# --------------------------------------------------------------------------- forcing


@pytest.mark.parametrize(
    "key, expect",
    [
        (
            "ismip/ismip7-ais-forcing/data/CESM2-WACCM/ssp585/SDBN1-2000m/acabf/acabf_AIS_CESM2-WACCM_ssp585_SDBN1-2000m_v2_2150.nc",
            dict(
                kind=ForcingKind.ANNUAL,
                source="CESM2-WACCM",
                experiment="ssp585",
                product="SDBN1-2000m",
                product_model="SDBN1",
                resolution_m=2000,
                variable="acabf",
                version=2,
                year_start=2150,
                year_end=2150,
                group_path="CESM2-WACCM/ssp585/SDBN1-2000m",
            ),
        ),
        (
            "ismip/ismip7-gris-forcing/data/MRI-ESM2-0/historical/GEMB-SDBN1-1000m/tas-anomaly/tas-anomaly_GrIS_MRI-ESM2-0_historical_GEMB-SDBN1-1000m_v2_1900.nc",
            dict(
                kind=ForcingKind.ANNUAL,
                product_model="GEMB-SDBN1",
                resolution_m=1000,
                variable="tas-anomaly",
            ),
        ),
        (
            "ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/ocean/tf/tf_AIS_CESM2-WACCM_historical_ocean_v3_1850-1859.nc",
            dict(
                kind=ForcingKind.MULTIYEAR,
                product="ocean",
                resolution_m=8000,
                year_start=1850,
                year_end=1859,
                version=3,
            ),
        ),
        (
            "ismip/ismip7-gris-forcing/data/CESM2-WACCM/ctrl/ocean-4000m/so/so_GrIS_CESM2-WACCM_ctrl_ocean-4000m_v2_2200.nc",
            dict(kind=ForcingKind.ANNUAL, product_model="ocean", resolution_m=4000),
        ),
        (
            "ismip/ismip7-ais-forcing/data/OCX/RACMO2.3p2-ERA/SDBN1-8000m/acabf/acabf_AIS_RACMO2.3p2-ERA_OCX_SDBN1-8000m_v1_1979.nc",
            dict(
                kind=ForcingKind.ANNUAL,
                source="RACMO2.3p2-ERA",
                experiment="ocx",
                product="SDBN1-8000m",
                group_path="RACMO2.3p2-ERA/ocx/SDBN1-8000m",
            ),
        ),
        (
            "ismip/ismip7-gris-forcing/data/OCX/EN4/ocean-4000m/tf/tf_GrIS_EN4_OCX_ocean-4000m_v1_1950.nc",
            dict(source="EN4", experiment="ocx", product_model="ocean", resolution_m=4000),
        ),
        (
            "ismip/ismip7-ais-forcing/data/OCX/ocean/main/so_AIS_OCX_ocean_main_v2_1950-2025.nc",
            dict(
                kind=ForcingKind.MULTIYEAR,
                source="ocean",
                experiment="ocx",
                product="main",
                variable="so",
                year_start=1950,
                year_end=2025,
            ),
        ),
        (
            "ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/SDBN1-2000m/extra/climatology/acabf/acabf_AIS_CESM2-WACCM_historical_SDBN1-2000m_v2_1960-1989.nc",
            dict(
                kind=ForcingKind.CLIMATOLOGY,
                variable="acabf",
                year_start=1960,
                year_end=1989,
                group_path="climatology/CESM2-WACCM/historical/SDBN1-2000m",
            ),
        ),
        (
            "ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/ocean/extras/bias/ct/ct_AIS_CESM2-WACCM_historical_ocean_extras_bias_v3_1995-2024.nc",
            dict(
                kind=ForcingKind.BIAS,
                variable="ct",
                product="ocean",
                group_path="bias/CESM2-WACCM/historical/ocean",
            ),
        ),
        (
            "ismip/ismip7-ais-forcing/data/CESM2-WACCM/ssp585/fracture/lake_properties_cesm2waccm_ssp585_ismip7_8km-v2.1.nc",
            dict(kind=ForcingKind.FRACTURE, variable="lake_properties", product="fracture"),
        ),
        (
            "ismip/ismip7-ais-forcing/data/grid/ocean/ISMIP7/8km-60m/ISMIP7_8km-60m_AIS_grid_ocean_v3.nc",
            dict(kind=ForcingKind.GRID, version=3),
        ),
        (
            "ismip/ismip7-ais-forcing/data/parameterisations/fracture/ant_surf_roughness_8km.nc",
            dict(kind=ForcingKind.PARAMETERISATION, variable="ant"),
        ),
    ],
)
def test_forcing_keys_parse(key, expect):
    r = parse_key(key)
    assert r.forcing is not None, r.findings
    assert r.ok, r.findings
    for attr, val in expect.items():
        assert getattr(r.forcing, attr) == val, (attr, getattr(r.forcing, attr), val)


def test_forcing_no_warnings_on_clean_key():
    r = parse_key(
        "ismip/ismip7-ais-forcing/data/CESM2-WACCM/ssp585/SDBN1-2000m/acabf/acabf_AIS_CESM2-WACCM_ssp585_SDBN1-2000m_v2_2150.nc"
    )
    assert r.findings == []


def test_forcing_mismatches_are_found():
    r = parse_forcing_key(
        "ismip/ismip7-ais-forcing/data/CESM2-WACCM/ssp585/SDBN1-2000m/acabf/pr_AIS_MRI-ESM2-0_ssp126_SDBN1-8000m_v2_2150.nc"
    )
    assert r.forcing is not None
    assert {f.code for f in r.findings} == {
        "variable_mismatch",
        "source_mismatch",
        "experiment_mismatch",
        "product_mismatch",
    }
    assert r.ok  # mismatches are warnings
    r = parse_forcing_key(
        "ismip/ismip7-gris-forcing/data/CESM2-WACCM/ssp585/SDBN1-1000m/acabf/acabf_AIS_CESM2-WACCM_ssp585_SDBN1-1000m_v2_2150.nc"
    )
    assert "region_mismatch" in {f.code for f in r.findings} and not r.ok


def test_forcing_bad_filename():
    r = parse_forcing_key(
        "ismip/ismip7-ais-forcing/data/CESM2-WACCM/ssp585/SDBN1-2000m/acabf/acabf_2150.nc"
    )
    assert {f.code for f in r.findings} == {"bad_filename"} and not r.ok
    r = parse_forcing_key("ismip/ismip7-ais-forcing/data/CESM2-WACCM/ssp585/SDBN1-2000m/README")
    assert {f.code for f in r.findings} == {"non_data_file"} and r.ok


def test_unknown_esm_and_experiment_are_soft():
    r = parse_forcing_key(
        "ismip/ismip7-ais-forcing/data/FOO-ESM/ssp999/dEBM2-8000m/pr/pr_AIS_FOO-ESM_ssp999_dEBM2-8000m_v1_2020.nc"
    )
    assert {f.code for f in r.findings} == {"unknown_esm", "unknown_experiment"} and r.ok


def test_other_datasets_are_recorded_not_rejected():
    r = parse_key(
        "ismip/ismip7-ais-observations/data/mipkit/AntarcticaObsISMIP7-v1.2.nc", 9083800000
    )
    assert r.record is None and r.ok and r.dataset == "ismip7-ais-observations"


def test_sample_listing_parses():
    keys = (DATA / "sample_keys.txt").read_text().split()
    results = [parse_key(k) for k in keys]
    forcing = [r for r in results if r.dataset in ("ismip7-ais-forcing", "ismip7-gris-forcing")]
    errors = [r for r in forcing if not r.ok]
    # one stray working file in the AIS ocean climatology extras; see docs/conventions_report.md
    assert [r.key.rsplit("/", 1)[1] for r in errors] == [
        "sa_CESM2-WACCM_climatology_r1i1p1f1_ismip8km_60m_1850_199501-201412.nc"
    ]
    assert len([r for r in forcing if r.forcing]) > 1000
