"""Phase A tests: per-file normalisation and group concatenation on synthetic files."""

from __future__ import annotations

from datetime import timedelta

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
from tests.conftest import AIS_KEY_FMT, GRIS_KEY_FMT, NC_FILL, write_forcing_file

pytestmark = [
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    # a kept missing_value differing from _FillValue: xarray masks both, by design
    pytest.mark.filterwarnings("ignore:variable .* has multiple fill values"),
]


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


def _time_ds(raw, units, calendar):
    return xr.Dataset(
        coords={"time": ("time", np.asarray(raw), {"units": units, "calendar": calendar})}
    )


def _std_days(year, month, day):
    import cftime

    return float(
        cftime.date2num(
            cftime.DatetimeGregorian(year, month, day), SUBMISSION_TIME_UNITS, SUBMISSION_CALENDAR
        )
    )


@pytest.mark.parametrize(
    "units, calendar, year",
    [
        ("days since 1850-01-01 00:00:00", "standard", 1850),
        ("days since 1850-01-16 00:00:00.000000", "noleap", 1850),
        ("days since 1950-01-16", "365_day", 1950),
        ("days since 1850-01-01", "proleptic_gregorian", 1850),
        # 60 leap days between epoch and dates: a day-count reinterpretation
        # would land about two months early; date components must be kept.
        ("days since 1850-01-16", "noleap", 2100),
        ("days since 1850-01-01", "NoLeap", 2100),
        ("hours since 1850-01-01", "gregorian", 2100),
    ],
)
def test_normalize_time_calendars(units, calendar, year):
    import cftime

    dates = [cftime.datetime(year, m, 15, calendar=calendar.lower()) for m in range(1, 13)]
    raw = cftime.date2num(dates, units, calendar.lower())
    out = normalize_time(_time_ds(np.asarray(raw, dtype="f8"), units, calendar))
    assert out["time"].dtype == np.float64
    assert out["time"].values[0] == _std_days(year, 1, 15)
    # what to_icechunk writes: units/calendar as attributes, no fill value
    assert out["time"].attrs["units"] == SUBMISSION_TIME_UNITS
    assert out["time"].attrs["calendar"] == SUBMISSION_CALENDAR
    assert out["time"].encoding == {"_FillValue": None}
    assert out["time"].attrs["source_time_calendar"] == calendar.lower()
    assert out["time"].attrs["source_time_units"] == units
    # 15 July in the standard calendar, whatever the source calendar
    back = cftime.num2date(out["time"].values[6], SUBMISSION_TIME_UNITS, SUBMISSION_CALENDAR)
    assert (back.year, back.month, back.day) == (year, 7, 15)
    # idempotent, and the source encoding survives a second pass
    again = normalize_time(out)
    assert np.array_equal(again["time"].values, out["time"].values)
    assert again["time"].attrs["source_time_units"] == units


def test_normalize_time_noleap_day_count_differs():
    """Guard for the test above: day-count reinterpretation is ~60 days off in 2100."""
    import cftime

    raw = cftime.date2num(
        cftime.datetime(2100, 1, 15, calendar="noleap"), "days since 1850-01-16", "noleap"
    )
    out = normalize_time(_time_ds(np.array([raw], dtype="f8"), "days since 1850-01-16", "noleap"))
    naive = raw + 15  # the same day count from 1850-01-01
    assert 55 < out["time"].values[0] - naive < 65


def test_normalize_time_int64_and_subsecond():
    out = normalize_time(_time_ds(np.array([0, 31], dtype="i8"), "days since 1850-01-16", "noleap"))
    assert out["time"].dtype == np.float64 and list(out["time"].values) == [15.0, 46.0]
    out = normalize_time(_time_ds(np.array([0.5, 1.5]), "seconds since 1850-01-01", "standard"))
    assert out["time"].values[0] == pytest.approx(0.5 / 86400, abs=1e-12)


@pytest.mark.parametrize(
    "units, calendar, match",
    [
        ("days since 1850-01-01", "360_day", "calendar '360_day'"),
        ("days since 1850-01-01", "julian", "calendar 'julian'"),
        ("days since 1850-01-01", "all_leap", "calendar 'all_leap'"),
        ("days since 1850-01-01", "366_day", "calendar '366_day'"),
        ("months since 1850-01-01", "standard", "cannot decode time"),
    ],
)
def test_normalize_time_rejects_calendars(units, calendar, match):
    with pytest.raises(VirtualizeError, match=match):
        normalize_time(_time_ds(np.array([0.0, 1.0]), units, calendar))


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
        global_attrs={
            "history_of_appended_files": "ncks ...",
            "nco_openmp_thread_number": 1,
            "Comment": "monthly means",
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
    # VirtualiZarr's base64 fill value attribute is kept so readers mask fill cells
    assert ds["acabf"].attrs["_FillValue"] == "AAAAAAAAnkc="
    assert float(ds["acabf"].data.metadata.fill_value) == pytest.approx(9.96921e36)
    assert "missing_value" not in ds["acabf"].attrs
    for c in ("time", "x", "y", "crs"):
        assert ds[c].encoding.get("_FillValue", "unset") is None, c
        assert "_FillValue" not in ds[c].attrs, c
    assert ds["time"].values[0] == 14.0 and ds["time"].attrs["units"] == SUBMISSION_TIME_UNITS
    assert "ismip7_group" not in ds.attrs  # set by concat_group, not per file
    assert ds.attrs["ismip7_product"] == "SDBN1-32000m" and ds.attrs["ismip7_file_version"] == "v2"
    assert ds.attrs["source_key"] == key
    for noisy in ("history", "history_of_appended_files", "nco_openmp_thread_number"):
        assert noisy not in ds.attrs
    assert ds.attrs["Comment"] == "monthly means" and ds.attrs["title"] == "acabf synthetic"
    assert any("latitude_of_projection_origin=90.0 for AIS" in n for n in o.notes)


def test_open_forcing_file_float32_nan_fill_coords_latlon_bounds(synthetic_root, local_registry):
    key = _key()
    url = _write(
        synthetic_root,
        key,
        coord_dtype=np.float32,
        coord_fill_nan=True,
        write_latlon=True,
        write_bounds=True,
        time_dtype="i8",
        time_units="days since 1850-01-16",
        calendar="noleap",
    )
    o = open_forcing_file(key, local_registry, url=url)
    ds = o.ds
    assert set(ds.variables) == {"acabf", "time", "x", "y", "crs"}
    assert ds["x"].dtype == np.float64 and np.array_equal(ds["x"].values, o.grid.x())
    assert ds["x"].encoding == {"_FillValue": None} and "_FillValue" not in ds["x"].attrs
    assert "coordinates" not in ds["acabf"].attrs and "bounds" not in ds["time"].attrs
    assert ds["time"].dtype == np.float64 and ds["time"].values[0] == 14.0


def test_open_forcing_file_gris_crs(synthetic_root, local_registry):
    key = GRIS_KEY_FMT.format(
        var="acabf", year=1850, esm="CESM2-WACCM", exp="historical", product="SDBN1-16000m", ver=3
    )
    # the GrIS SDBN1 files describe the southern projection
    ais_attrs = grid_registry()[("AIS", 32000)].cf_grid_mapping_attrs()
    url = _write(
        synthetic_root,
        key,
        region="GrIS",
        resolution_m=16000,
        crs_attrs=ais_attrs,
        crs_name="polar_stereographic",
    )
    o = open_forcing_file(key, local_registry, url=url)
    assert o.grid.region == "GrIS" and o.ds.sizes == {"time": 12, "y": 181, "x": 106}
    assert o.ds["crs"].attrs["latitude_of_projection_origin"] == 90.0
    assert "3413" in o.ds["crs"].attrs["crs_wkt"] and "polar_stereographic" not in o.ds
    assert o.ds.attrs["ismip7_grid"] == "GrIS 16000 m (EPSG:3413)"
    assert any(
        "source polar_stereographic had latitude_of_projection_origin=-90.0" in n for n in o.notes
    )


def test_open_forcing_file_missing_value(synthetic_root, local_registry):
    key = _key()
    url = _write(synthetic_root, key, missing_value=NC_FILL)  # as in the real SDBN1 files
    o = open_forcing_file(key, local_registry, url=url)
    assert "missing_value" not in o.ds["acabf"].attrs and not o.notes
    url = _write(synthetic_root, key, missing_value=-999.0)
    o = open_forcing_file(key, local_registry, url=url)
    assert o.ds["acabf"].attrs["missing_value"] == -999.0
    assert any("kept missing_value=-999.0" in n for n in o.notes)


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


def test_open_forcing_file_rejects_bad_coordinates(synthetic_root, local_registry):
    import netCDF4

    key = _key()
    url = _write(synthetic_root, key, y_descending=True)
    with pytest.raises(VirtualizeError, match="y runs in the opposite direction"):
        open_forcing_file(key, local_registry, url=url)
    url = _write(synthetic_root, key, write_xy=False)
    with pytest.raises(VirtualizeError, match="no x coordinate variable"):
        open_forcing_file(key, local_registry, url=url)
    # a NaN coordinate must not slip through the tolerance check (NaN > 1 is False)
    url = _write(synthetic_root, key, coord_dtype=np.float32, coord_fill_nan=True)
    with netCDF4.Dataset(synthetic_root / key, "a") as nc:
        nc["x"][5] = np.nan
    with pytest.raises(VirtualizeError, match="x has 1 non-finite values"):
        open_forcing_file(key, local_registry, url=url)


def test_open_forcing_file_rejects_dates_outside_filename_year(synthetic_root, local_registry):
    key = _key(year=1851)
    path = synthetic_root / key
    write_forcing_file(path, year=1850)  # 1850 dates in a _1851 file
    with pytest.raises(VirtualizeError, match="dates span 1850-1850, the filename says 1851-1851"):
        open_forcing_file(key, local_registry, url=f"file://{path}")


def test_open_forcing_file_wraps_parser_errors(synthetic_root, local_registry):
    key = _key()
    path = synthetic_root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a netcdf file")
    with pytest.raises(VirtualizeError, match="cannot open"):
        open_forcing_file(key, local_registry, url=f"file://{path}")


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


def _group(root, registry, specs):
    """``specs``: list of (key, write kwargs). Writes the files and concatenates them."""
    urls = {k: _write(root, k, **kw) for k, kw in specs}
    return concat_group([parse_key(k).forcing for k, _ in specs], registry, urls=urls)


def test_concat_group_two_years_two_variables(synthetic_root, local_registry):
    keys = [_key(var=v, year=y) for v in ("acabf", "tas") for y in (1850, 1851)]
    g = _group(synthetic_root, local_registry, [(k, {"seed": i}) for i, k in enumerate(keys)])
    assert g.group_path == "CESM2-WACCM/historical/SDBN1-32000m" and g.n_files == 4
    ds = g.ds
    assert set(ds.data_vars) == {"acabf", "tas"} and ds.sizes["time"] == 24
    assert ds["acabf"].data.manifest.shape_chunk_grid == (24, 1, 1)
    assert np.all(np.diff(ds["time"].values) > 0) and ds["time"].values[12] == 379.0
    assert ds.attrs["ismip7_group"] == g.group_path and ds.attrs["ismip7_n_source_files"] == 4
    assert "source_key" not in ds.attrs
    assert ds.attrs["source_time_units"] == "days since 1850-01-01 00:00:00"
    assert ds.attrs["source_time_calendar"] == "standard"
    assert (
        ds.attrs["ismip7_file_version"] == "v2" and ds["tas"].attrs["ismip7_file_version"] == "v2"
    )
    assert ds["time"].attrs["units"] == SUBMISSION_TIME_UNITS
    assert ds["acabf"].attrs["_FillValue"] == "AAAAAAAAnkc="
    # the virtual references still point at the right files, in year order
    paths = ds["acabf"].data.manifest._paths
    assert paths[0, 0, 0].endswith("_1850.nc") and paths[23, 0, 0].endswith("_1851.nc")


def test_concat_group_multistep_chunks_with_short_last_file(synthetic_root, local_registry):
    keys = [_key(year=y) for y in (1850, 1851)]
    g = _group(
        synthetic_root,
        local_registry,
        [(keys[0], {"chunks": (4, 191, 191)}), (keys[1], {"chunks": (4, 191, 191), "n_time": 10})],
    )
    arr = g.ds["acabf"].data
    assert g.ds.sizes["time"] == 22 and arr.manifest.shape_chunk_grid == (6, 1, 1)
    paths = arr.manifest._paths
    assert paths[2, 0, 0].endswith("_1850.nc") and paths[5, 0, 0].endswith("_1851.nc")
    # a partial chunk is only allowed in the last file
    g2 = [_key(year=y, exp="ssp585") for y in (2015, 2016)]
    with pytest.raises(VirtualizeError, match="cannot concatenate years 2015-2016"):
        _group(
            synthetic_root,
            local_registry,
            [(g2[0], {"chunks": (5, 191, 191)}), (g2[1], {"chunks": (5, 191, 191)})],
        )


def test_concat_group_rejects_time_mismatch(synthetic_root, local_registry):
    keys = [_key(var="acabf", year=1850), _key(var="acabf", year=1851), _key(var="tas", year=1850)]
    with pytest.raises(
        VirtualizeError,
        match=r"time axis of tas differs from acabf \(12 vs 24 steps\)",
    ):
        _group(synthetic_root, local_registry, [(k, {}) for k in keys])


def _shifted_months(days: float) -> np.ndarray:
    import cftime

    dates = [cftime.datetime(1850, m, 15) + timedelta(days=days) for m in range(1, 13)]
    return np.asarray(cftime.date2num(dates, "days since 1850-01-01", "standard"), dtype="f8")


def test_concat_group_aligns_small_time_offset_between_variables(synthetic_root, local_registry):
    """GrIS sgd files stamp 16 Jan / 14 Feb (noleap) where their siblings stamp the 15th:
    the same monthly axis with a skewed encoding, aligned to the majority and recorded."""
    specs = [
        (_key(var="acabf"), {}),
        (_key(var="tas"), {}),
        (_key(var="sgd"), {"time_values": _shifted_months(1.0)}),
    ]
    g = _group(synthetic_root, local_registry, specs)
    ds = g.ds
    assert ds.sizes["time"] == 12 and "ismip7_time_axes" not in ds.attrs
    np.testing.assert_array_equal(ds["time"].values, _shifted_months(0.0))
    note = "sgd: time stamps up to 1 days from those of acabf; aligned to the acabf axis"
    assert ds.attrs["ismip7_time_axis_notes"] == [note]
    assert note in g.notes


def test_concat_group_rejects_time_offset_between_variables(synthetic_root, local_registry):
    specs = [(_key(var="acabf"), {}), (_key(var="tas"), {"time_values": _shifted_months(3.0)})]
    with pytest.raises(
        VirtualizeError, match=r"time axis of tas differs from acabf \(up to 3 days apart\)"
    ):
        _group(synthetic_root, local_registry, specs)


def test_concat_group_rejects_gap_and_mixed_versions(synthetic_root, local_registry):
    with pytest.raises(VirtualizeError, match="gap between files 1850-1850 and 1852-1852"):
        _group(synthetic_root, local_registry, [(_key(year=y), {}) for y in (1850, 1852)])
    with pytest.raises(VirtualizeError, match=r"mixed file versions \[2, 3\]"):
        _group(
            synthetic_root,
            local_registry,
            [(_key(year=1850, ver=2), {}), (_key(year=1851, ver=3), {})],
        )


def test_concat_group_rejects_overlap_and_mixed_groups(synthetic_root, local_registry):
    a = parse_key(_key(year=1850)).forcing
    b = parse_key(_key(year=1850, exp="ssp585")).forcing
    with pytest.raises(VirtualizeError, match="several groups"):
        concat_group([a, b], local_registry)
    fmt = _key(year=9999).replace("_9999.nc", "_{}.nc")
    k1, k2 = fmt.format("1850-1859"), fmt.format("1855-1864")
    urls = {}
    for k, y in ((k1, 1850), (k2, 1855)):
        write_forcing_file(synthetic_root / k, year=y)
        urls[k] = f"file://{synthetic_root / k}"
    with pytest.raises(VirtualizeError, match="overlapping files 1850-1859 and 1855-1864"):
        concat_group([parse_key(k).forcing for k in (k1, k2)], local_registry, urls=urls)


def test_concat_group_rejects_different_fill_values(synthetic_root, local_registry):
    with pytest.raises(VirtualizeError, match="fill value"):
        _group(
            synthetic_root,
            local_registry,
            [(_key(year=1850), {}), (_key(year=1851), {"fill_value": -9999.0})],
        )
    with pytest.raises(VirtualizeError, match="missing_value"):
        _group(
            synthetic_root,
            local_registry,
            [
                (_key(year=1850), {"missing_value": -1.0}),
                (_key(year=1851), {"missing_value": -2.0}),
            ],
        )


def test_concat_group_records_differing_source_time_and_versions(synthetic_root, local_registry):
    specs = [
        (_key(var="acabf", year=1850, ver=3), {}),
        (_key(var="acabf", year=1851, ver=3), {"time_units": "days since 1851-01-01"}),
        (_key(var="tas", year=1850), {}),
        (_key(var="tas", year=1851), {}),
    ]
    g = _group(synthetic_root, local_registry, specs)
    ds = g.ds
    assert ds.sizes["time"] == 24
    units = ds.attrs["source_time_units"]
    assert isinstance(units, list) and len(units) == 4
    assert any(u.endswith("_v3_1851.nc: days since 1851-01-01") for u in units)
    assert ds.attrs["source_time_calendar"] == "standard"
    assert "source_time_units" not in ds["time"].attrs
    assert ds.attrs["ismip7_file_version"] == ["acabf: v3", "tas: v2"]
    assert ds["acabf"].attrs["ismip7_file_version"] == "v3"


def test_concat_group_checks_opened_and_time_presence(synthetic_root, local_registry):
    keys = [_key(var="acabf"), _key(var="tas")]
    urls = {k: _write(synthetic_root, k) for k in keys}
    files = [parse_key(k).forcing for k in keys]
    opened = [open_forcing_file(k, local_registry, url=urls[k]) for k in keys]
    with pytest.raises(VirtualizeError, match="opened files do not match"):
        concat_group(files, local_registry, opened=opened[:1])
    # first variable without time must not skip the check
    opened[0].ds = opened[0].ds.isel(time=0, drop=True)
    with pytest.raises(VirtualizeError, match="only some variables have a time axis"):
        concat_group(files, local_registry, opened=opened)
    # the caller's datasets are not modified
    assert "ismip7_file_version" not in opened[1].ds["tas"].attrs


def test_grid_registry_small_grids_exist():
    g = grid_registry()
    assert g[("AIS", 32000)].shape == (191, 191)
    assert g[("GrIS", 16000)].shape == (181, 106)


# --------------------------------------------------------------------------- process pool


def _pool_group(root, n=6):
    keys = [_key(year=y) for y in range(1850, 1850 + n)]
    urls = {k: _write(root, k, seed=i) for i, k in enumerate(keys)}
    return [parse_key(k, 1000).forcing for k in keys], urls


@pytest.fixture
def no_semaphores(monkeypatch):
    """What Lambda looks like: no /dev/shm, so every POSIX semaphore fails with ENOSYS."""
    import _multiprocessing
    import errno

    class NoSemLock:
        SEM_VALUE_MAX = _multiprocessing.SemLock.SEM_VALUE_MAX

        def __init__(self, *a, **kw):
            raise OSError(errno.ENOSYS, "Function not implemented")

    monkeypatch.setattr(_multiprocessing, "SemLock", NoSemLock)


def test_process_pool_needs_no_semaphores(synthetic_root, no_semaphores, caplog):
    import multiprocessing

    with pytest.raises(OSError, match="Function not implemented"):
        multiprocessing.get_context("forkserver").Lock()  # the mock bites
    files, urls = _pool_group(synthetic_root)
    g = concat_group(files, urls=urls, max_workers=3, use_processes=True)
    assert g.n_files == 6 and g.ds.sizes["time"] == 72
    assert not [r for r in caplog.records if r.levelname == "WARNING"]  # no thread fallback


def test_process_pool_matches_threads_and_reports_errors(synthetic_root):
    files, urls = _pool_group(synthetic_root, n=4)
    procs = concat_group(files, urls=urls, max_workers=2, use_processes=True)
    threads = concat_group(files, urls=urls, max_workers=2)
    assert procs.ds.identical(threads.ds)
    bad = dict(urls)
    bad[files[2].key] = f"file://{synthetic_root}/missing.nc"
    with pytest.raises(VirtualizeError, match="cannot open"):
        concat_group(files, urls=bad, max_workers=2, use_processes=True)


def test_process_pool_falls_back_to_threads_when_processes_cannot_start(
    synthetic_root, monkeypatch, caplog
):
    import errno
    import multiprocessing.process

    def refuse(self):
        raise OSError(errno.EACCES, "no processes here")

    monkeypatch.setattr(multiprocessing.process.BaseProcess, "start", refuse)
    files, urls = _pool_group(synthetic_root, n=3)
    with caplog.at_level("WARNING"):
        g = concat_group(files, urls=urls, max_workers=2, use_processes=True)
    assert g.n_files == 3
    assert any("cannot start worker processes" in r.getMessage() for r in caplog.records)


def test_process_pool_refuses_an_injected_registry(synthetic_root, local_registry):
    files, urls = _pool_group(synthetic_root, n=2)
    with pytest.raises(ValueError, match="registry cannot be passed"):
        concat_group(files, local_registry, urls=urls, max_workers=2, use_processes=True)


def test_registry_is_cached_per_process():
    from ismip7_helper.virtualize import _cached_registry

    assert _cached_registry(True) is _cached_registry(True)
    assert _cached_registry(False) is not _cached_registry(True)


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


def test_concat_group_keeps_monthly_and_annual_variables_on_separate_axes(
    synthetic_root, local_registry
):
    """The gradient fields (dacabfdz, ...) are annual in files whose siblings are monthly."""
    import cftime

    keys, urls = [], {}
    for var, n_time in (("acabf", 12), ("dacabfdz", 1)):
        for year in (1850, 1851):
            k = _key(var=var, year=year)
            tv = None
            if n_time == 1:
                tv = np.asarray(
                    cftime.date2num(
                        [cftime.datetime(year, 7, 1, calendar="standard")],
                        "days since 1850-01-01 00:00:00",
                        "standard",
                    ),
                    dtype="f8",
                )
            urls[k] = _write(synthetic_root, k, n_time=n_time, time_values=tv)
            keys.append(k)
    g = concat_group([parse_key(k).forcing for k in keys], local_registry, urls=urls)
    ds = g.ds
    assert ds["acabf"].dims == ("time", "y", "x") and ds.sizes["time"] == 24
    assert ds["dacabfdz"].dims == ("time_annual", "y", "x") and ds.sizes["time_annual"] == 2
    assert ds["time_annual"].attrs["units"] == "days since 1850-01-01"
    assert ds.attrs["ismip7_time_axes"] == ["time: acabf", "time_annual: dacabfdz"]
