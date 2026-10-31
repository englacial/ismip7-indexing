"""Phase A tests: per-file normalisation and group concatenation on synthetic files."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from virtualizarr.manifests import ManifestArray

from ismip7_helper.conventions import grid_registry, parse_key
from ismip7_helper.conventions.models import SUBMISSION_CALENDAR, SUBMISSION_TIME_UNITS
from ismip7_helper.virtualize import (
    VirtualizeError,
    concat_group,
    normalize_time,
    open_forcing_file,
)
from tests.conftest import AIS_KEY_FMT, write_forcing_file

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _key(
    var="acabf", year=1850, esm="CESM2-WACCM", exp="historical", product="SDBN1-32000m", ver=2
):
    return AIS_KEY_FMT.format(var=var, year=year, esm=esm, exp=exp, product=product, ver=ver)


def _write(root, key, **kw):
    path = root / key
    write_forcing_file(
        path, year=int(key[-7:-3]), variable=key.rsplit("/", 1)[1].split("_")[0], **kw
    )
    return f"file://{path}"


# --------------------------------------------------------------------------- time


@pytest.mark.parametrize(
    "units, calendar, expect_first",
    [
        ("days since 1850-01-01 00:00:00", "standard", 14.0),  # 1850-01-15
        ("days since 1850-01-16 00:00:00.000000", "noleap", 14.0),  # date components kept
        ("days since 1950-01-16", "365_day", 36538.0),  # 1950-01-15 -> days from 1850 (standard)
        ("days since 1850-01-01", "proleptic_gregorian", 14.0),
    ],
)
def test_normalize_time_calendars(units, calendar, expect_first):
    import cftime

    year = 1950 if "1950" in units else 1850
    dates = [cftime.datetime(year, m, 15, calendar=calendar) for m in range(1, 13)]
    raw = cftime.date2num(dates, units, calendar)
    ds = xr.Dataset(
        coords={
            "time": ("time", np.asarray(raw, dtype="f8"), {"units": units, "calendar": calendar})
        }
    )
    out = normalize_time(ds)
    assert out["time"].dtype == np.float64
    assert out["time"].values[0] == expect_first
    assert out["time"].encoding == {
        "units": SUBMISSION_TIME_UNITS,
        "calendar": SUBMISSION_CALENDAR,
        "dtype": "float64",
    }
    assert out["time"].attrs["source_time_calendar"] == calendar
    assert "units" not in out["time"].attrs
    # 15 July in the standard calendar, whatever the source calendar
    back = cftime.num2date(out["time"].values[6], SUBMISSION_TIME_UNITS, SUBMISSION_CALENDAR)
    assert (back.month, back.day) == (7, 15)


def test_normalize_time_rejects_bad_axes():
    ds = xr.Dataset(
        coords={"time": ("time", np.array([3.0, 2.0]), {"units": "days since 1850-01-01"})}
    )
    with pytest.raises(VirtualizeError, match="strictly increasing"):
        normalize_time(ds)
    ds = xr.Dataset(coords={"time": ("time", np.array([1.0, 2.0]))})
    with pytest.raises(VirtualizeError, match="no units"):
        normalize_time(ds)


# --------------------------------------------------------------------------- per file


def test_open_forcing_file_normalises(synthetic_root, local_registry):
    key = _key()
    url = _write(
        synthetic_root,
        key,
        coord_dtype=np.int64,
        crs_attrs={
            "grid_mapping_name": "polar_stereographic",
            "latitude_of_projection_origin": 90.0,
        },
    )
    o = open_forcing_file(key, local_registry, url=url)
    ds = o.ds
    assert o.grid.resolution_m == 32000 and o.forcing.variable == "acabf"
    assert isinstance(ds["acabf"].data, ManifestArray)
    assert ds["acabf"].data.manifest.shape_chunk_grid == (12, 1, 1)
    assert ds["x"].dtype == np.float64 and ds["x"].values[0] == o.grid.xfirst
    assert ds["crs"].attrs["latitude_of_projection_origin"] == -90.0
    assert "3031" in ds["crs"].attrs["crs_wkt"]
    assert ds["acabf"].attrs["grid_mapping"] == "crs"
    assert "_FillValue" not in ds["acabf"].attrs and "missing_value" not in ds["acabf"].attrs
    assert float(ds["acabf"].data.metadata.fill_value) == pytest.approx(9.96921e36)
    assert ds["time"].values[0] == 14.0
    assert ds.attrs["ismip7_group"] if "ismip7_group" in ds.attrs else True
    assert ds.attrs["ismip7_product"] == "SDBN1-32000m" and ds.attrs["ismip7_file_version"] == "v2"
    assert "history" not in ds.attrs and ds.attrs["source_key"] == key
    assert any("latitude_of_projection_origin=90.0 for AIS" in n for n in o.notes)


def test_open_forcing_file_drops_extra_variables(synthetic_root, local_registry):
    key = _key()
    url = _write(synthetic_root, key, extra_var=True)
    o = open_forcing_file(key, local_registry, url=url)
    assert list(o.ds.data_vars) == ["acabf"]
    assert any("dropped extra data variables ['extra']" in n for n in o.notes)


def test_open_forcing_file_rejects_shifted_grid(synthetic_root, local_registry):
    key = _key()
    url = _write(synthetic_root, key, coord_offset_m=500.0)
    with pytest.raises(VirtualizeError, match="differs from the AIS 32000 m grid"):
        open_forcing_file(key, local_registry, url=url)


def test_open_forcing_file_rejects_wrong_resolution_claim(synthetic_root, local_registry):
    key = _key(product="SDBN1-8000m")  # file is on the 32 km grid
    url = _write(synthetic_root, key)
    with pytest.raises(VirtualizeError, match="product says 8000 m"):
        open_forcing_file(key, local_registry, url=url)


def test_open_forcing_file_rejects_unparseable_key(local_registry):
    with pytest.raises(VirtualizeError):
        open_forcing_file(
            "ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/SDBN1-32000m/acabf/junk.nc",
            local_registry,
        )


# --------------------------------------------------------------------------- per group


def test_concat_group_two_years_two_variables(synthetic_root, local_registry):
    keys = [_key(var=v, year=y) for v in ("acabf", "tas") for y in (1850, 1851)]
    urls = {k: _write(synthetic_root, k, seed=i) for i, k in enumerate(keys)}
    files = [parse_key(k).forcing for k in keys]
    g = concat_group(files, local_registry, urls=urls)
    assert g.group_path == "CESM2-WACCM/historical/SDBN1-32000m" and g.n_files == 4
    ds = g.ds
    assert set(ds.data_vars) == {"acabf", "tas"} and ds.sizes["time"] == 24
    assert ds["acabf"].data.manifest.shape_chunk_grid == (24, 1, 1)
    assert np.all(np.diff(ds["time"].values) > 0) and ds["time"].values[12] == 379.0
    assert ds.attrs["ismip7_group"] == g.group_path and ds.attrs["ismip7_n_source_files"] == 4
    assert "source_key" not in ds.attrs
    # the virtual references still point at the right files, in year order
    paths = ds["acabf"].data.manifest._paths
    assert paths[0, 0, 0].endswith("_1850.nc") and paths[23, 0, 0].endswith("_1851.nc")


def test_concat_group_rejects_time_mismatch(synthetic_root, local_registry):
    keys = [_key(var="acabf", year=1850), _key(var="acabf", year=1851), _key(var="tas", year=1850)]
    urls = {k: _write(synthetic_root, k) for k in keys}
    with pytest.raises(VirtualizeError, match="time axis of tas"):
        concat_group([parse_key(k).forcing for k in keys], local_registry, urls=urls)


def test_concat_group_rejects_overlap_and_mixed_groups(synthetic_root, local_registry):
    a = parse_key(_key(year=1850)).forcing
    b = parse_key(_key(year=1850, exp="ssp585")).forcing
    with pytest.raises(VirtualizeError, match="several groups"):
        concat_group([a, b], local_registry)
    # same year twice (e.g. two versions) overlaps
    c = parse_key(_key(year=1850, ver=3)).forcing
    urls = {a.key: _write(synthetic_root, a.key), c.key: _write(synthetic_root, c.key)}
    with pytest.raises(VirtualizeError, match="overlapping"):
        concat_group([a, c], local_registry, urls=urls)


def test_grid_registry_small_grids_exist():
    g = grid_registry()
    assert g[("AIS", 32000)].shape == (191, 191)
    assert g[("GrIS", 16000)].shape == (181, 106)


# --------------------------------------------------------------------------- network


@pytest.mark.network
def test_real_files_open_and_concat():
    from ismip7_helper.virtualize import source_registry

    reg = source_registry()
    base = "ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/SDBN1-8000m/{v}/{v}_AIS_CESM2-WACCM_historical_SDBN1-8000m_v2_{y}.nc"
    keys = [base.format(v=v, y=y) for v in ("acabf", "tas") for y in (1850, 1851)]
    g = concat_group([parse_key(k).forcing for k in keys], reg)
    assert g.ds.sizes == {"time": 24, "y": 761, "x": 761}
    assert g.ds["crs"].attrs["latitude_of_projection_origin"] == -90.0
    k = "ismip/ismip7-gris-forcing/data/CESM2-WACCM/historical/ocean-1000m/tf/tf_GrIS_CESM2-WACCM_historical_ocean-1000m_v2_1850.nc"
    o = open_forcing_file(k, reg)
    assert o.grid.resolution_m == 1000 and o.ds["time"].values[0] == 15.0
    assert o.ds["tf"].data.manifest.shape_chunk_grid == (3, 3, 3)
