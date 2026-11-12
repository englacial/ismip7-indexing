"""Colour ranges: sampling math, masking, both read paths, the backfill commit."""

from __future__ import annotations

import netCDF4
import numpy as np
import pytest

from ismip7_helper.conventions import parse_key
from ismip7_helper.ranges import (
    ACTUAL_RANGE_ATTR,
    RANGE_NOTE_ATTR,
    apply_ranges,
    estimate_ranges,
    estimate_stored_ranges,
    plan_ranges,
    range_axis,
    range_group,
    range_states,
    sample_indices,
)
from ismip7_helper.store import (
    StoreTarget,
    ensure_hierarchy,
    existing_groups,
    open_group,
    open_repository,
    write_group,
)
from ismip7_helper.virtualize import concat_group, local_registry
from tests.conftest import AIS_KEY_FMT, NC_FILL, write_forcing_file

pytestmark = [
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:variable .* has multiple fill values"),
]

HIST = "CESM2-WACCM/historical/SDBN1/32000m"


def _key(var, year, exp="historical"):
    return AIS_KEY_FMT.format(
        var=var, year=year, esm="CESM2-WACCM", exp=exp, product="SDBN1-32000m", ver=2
    )


def _write(root, key, **kw):
    p = root / key
    write_forcing_file(p, year=int(key[-7:-3]), variable=key.rsplit("/", 1)[1].split("_")[0], **kw)
    return f"file://{p}"


def _concat(root, specs):
    """``specs``: ``{key: write_forcing_file kwargs}``; returns the GroupResult and the urls."""
    urls = {k: _write(root, k, **kw) for k, kw in specs.items()}
    return concat_group([parse_key(k).forcing for k in specs], local_registry(), urls=urls), urls


def _truth(root, keys, var, steps):
    data = np.concatenate([netCDF4.Dataset(root / k)[var][:].filled(np.nan) for k in keys])
    v = data[steps]
    return float(np.nanmin(v)), float(np.nanmax(v))


@pytest.fixture
def local_target(tmp_path, synthetic_root):
    return StoreTarget(
        url=f"local:{tmp_path / 'repo'}",
        local_containers={f"file://{synthetic_root}/": str(synthetic_root)},
    )


def test_sample_indices_are_even_and_include_both_ends():
    s = sample_indices(3432, 16)
    assert len(s) == 16 and s[0] == 0 and s[-1] == 3431
    gaps = np.diff(s)
    assert gaps.min() >= 228 and gaps.max() <= 229  # 3431 / 15
    assert sample_indices(5, 16) == [0, 1, 2, 3, 4]
    assert sample_indices(1, 16) == [0] and sample_indices(0, 16) == []
    assert sample_indices(12, 1) == [0] and sample_indices(12, 2) == [0, 11]


def test_range_axis():
    assert range_axis(("time", "y", "x")) == "time"
    assert range_axis(("time_annual", "y", "x")) == "time_annual"
    assert range_axis(("month", "y", "x")) == "month"
    assert range_axis(("time", "z", "y", "x")) == "time"
    assert range_axis(("z", "y", "x")) == "z"
    assert range_axis(("time", "bnds")) is None and range_axis(("y", "x")) is None
    assert range_axis(("x",)) is None and range_axis(None) is None


def test_estimate_ranges_masks_fill_nan_and_missing_value(synthetic_root):
    """Fill cells, NaNs and listed missing_values stay out of the range; the rest is exact."""
    keys = [_key("acabf", y) for y in (1850, 1851, 1852)]
    result, _ = _concat(
        synthetic_root,
        {k: dict(seed=i, missing_value=-9999.0, fill_cell=True) for i, k in enumerate(keys)},
    )
    # plant outliers at the fill, NaN and missing_value, and a real spike in a sampled step
    with netCDF4.Dataset(synthetic_root / keys[1], "a") as nc:
        v = nc["acabf"]
        v.set_auto_mask(False)
        d = v[:]
        d[6, 3, 3] = NC_FILL  # step 18 of the series: sampled below
        d[6, 4, 4] = np.nan
        d[6, 5, 5] = -9999.0
        d[6, 6, 6] = 50.0
        d[7, 1, 1] = -500.0  # step 19: not sampled
        v[:] = d
    rr = estimate_ranges(result, n_steps=5)
    (vr,) = rr.ranges
    assert vr.var == "acabf" and vr.axis == "time" and vr.n_axis == 36
    assert vr.sampled == [0, 9, 18, 26, 35] and vr.n_read == 5
    assert (vr.low, vr.high) == _truth(synthetic_root, keys, "acabf", vr.sampled)
    assert vr.high == 50.0 and vr.low > -10  # the spike is in, the outliers are not
    assert vr.note == "sampled from 5 of 36 time steps"
    assert vr.attrs == {ACTUAL_RANGE_ATTR: [vr.low, 50.0], RANGE_NOTE_ATTR: vr.note}
    assert type(vr.low) is float and rr.notes == []
    assert rr.summary().startswith("colour ranges: 1 of 1 variables ranged from 5 sampled steps")

    apply_ranges(result.ds, rr)
    assert result.ds["acabf"].attrs[ACTUAL_RANGE_ATTR] == [vr.low, 50.0]


def test_all_masked_variable_gets_a_note_and_no_range(synthetic_root):
    keys = [_key("acabf", 1850), _key("tas", 1850)]
    result, _ = _concat(synthetic_root, {k: dict(seed=i) for i, k in enumerate(keys)})
    with netCDF4.Dataset(synthetic_root / keys[1], "a") as nc:
        nc["tas"].set_auto_mask(False)
        nc["tas"][:] = np.full((12, 191, 191), NC_FILL, "f4")
    rr = estimate_ranges(result, n_steps=4)
    by_var = {r.var: r for r in rr.ranges}
    assert by_var["acabf"].low is not None
    tas = by_var["tas"]
    assert tas.low is None and tas.high is None
    assert tas.note == "no finite values in 4 of 12 time steps sampled"
    assert tas.attrs == {RANGE_NOTE_ATTR: tas.note}
    assert rr.n_ranged == 1


def test_estimate_ranges_4d_ocean_column(synthetic_root):
    keys = [_key("thetao", y) for y in (1850, 1851)]
    result, _ = _concat(
        synthetic_root, {k: dict(seed=i, n_z=3, fill_cell=True) for i, k in enumerate(keys)}
    )
    assert result.ds["thetao"].dims == ("time", "z", "y", "x")
    rr = estimate_ranges(result, n_steps=3)
    (vr,) = rr.ranges
    assert vr.sampled == [0, 12, 23]
    assert (vr.low, vr.high) == _truth(synthetic_root, keys, "thetao", vr.sampled)


def test_estimate_ranges_annual_axis_and_native_tail(synthetic_root):
    """``dacabfdz`` on ``time_annual`` and a differently chunked file (a native tail) both sample."""
    import cftime

    def annual(year):
        return np.asarray(
            cftime.date2num(
                [cftime.datetime(year, 7, 1, calendar="standard")],
                "days since 1850-01-01 00:00:00",
                "standard",
            ),
            dtype="f8",
        )

    specs = {}
    for i, year in enumerate((1850, 1851, 1852)):
        specs[_key("acabf", year)] = dict(seed=i, chunks=(1, 96, 96) if year == 1852 else None)
        specs[_key("dacabfdz", year)] = dict(seed=10 + i, n_time=1, time_values=annual(year))
    result, _ = _concat(synthetic_root, specs)
    assert result.ds["dacabfdz"].dims == ("time_annual", "y", "x")
    assert [t.var for t in result.native_tails] == ["acabf"]
    rr = estimate_ranges(result, n_steps=4)
    by_var = {r.var: r for r in rr.ranges}
    assert by_var["acabf"].sampled == [0, 12, 23, 35]  # 35 is in the native tail
    acabf_keys = [k for k in specs if "/acabf/" in k]
    assert (by_var["acabf"].low, by_var["acabf"].high) == _truth(
        synthetic_root, acabf_keys, "acabf", [0, 12, 23, 35]
    )
    assert by_var["dacabfdz"].axis == "time_annual" and by_var["dacabfdz"].sampled == [0, 1, 2]
    assert by_var["dacabfdz"].note == "sampled from 3 of 3 time_annual steps"
    annual_keys = [k for k in specs if "/dacabfdz/" in k]
    assert (by_var["dacabfdz"].low, by_var["dacabfdz"].high) == _truth(
        synthetic_root, annual_keys, "dacabfdz", [0, 1, 2]
    )


def test_estimate_ranges_climatology_month_axis(synthetic_root, local_target):
    key = (
        "ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/SDBN1-32000m/extra/climatology/"
        "acabf/acabf_AIS_CESM2-WACCM_historical_SDBN1-32000m_v2_1960-1989.nc"
    )
    p = synthetic_root / key
    write_forcing_file(p, year=1960, variable="acabf", chunks=(4, 191, 191))
    with netCDF4.Dataset(p, "a") as nc:
        nc.renameDimension("time", "month")
        nc.renameVariable("time", "month")
        nc["month"].delncattr("calendar")
        nc["month"].units = "1"
        nc["month"][:] = np.arange(1, 13)
    result = concat_group([parse_key(key).forcing], local_registry(), urls={key: f"file://{p}"})
    rr = estimate_ranges(result, n_steps=16)
    (vr,) = rr.ranges
    assert vr.axis == "month" and vr.sampled == list(range(12))
    assert vr.note == "sampled from 12 of 12 month steps"
    assert (vr.low, vr.high) == _truth(synthetic_root, [key], "acabf", list(range(12)))
    # and through the store
    repo = open_repository(local_target, create=True)
    ensure_hierarchy(repo, [result.group_path])
    write_group(repo, result)
    rs = estimate_stored_ranges(repo, result.group_path, n_steps=16)
    assert (rs.ranges[0].low, rs.ranges[0].high) == (vr.low, vr.high)


def test_time_budget_stops_the_sampling_with_a_note(synthetic_root, monkeypatch):
    import types

    import ismip7_helper.ranges as ranges

    keys = [_key("acabf", 1850), _key("acabf", 1851), _key("tas", 1850)]
    result, _ = _concat(synthetic_root, {k: dict(seed=i) for i, k in enumerate(keys)})
    real = ranges.open_source
    clock = [0.0]

    def slow(url):  # every file open costs 100 s on the fake clock
        clock[0] += 100.0
        return real(url)

    monkeypatch.setattr(ranges, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    rr = estimate_ranges(result, n_steps=4, opener=slow, time_budget_s=50, max_workers=1)
    # acabf: steps 0 and 8 are in the first file, 15 and 23 in the second, which is never read
    (vr,) = rr.ranges
    assert vr.var == "acabf" and vr.sampled == [0, 8, 15, 23] and vr.n_read == 2
    assert vr.low is not None and vr.note == "sampled from 2 of 24 time steps (time budget)"
    assert rr.notes == ["tas: not sampled, the 50 s budget is used up"]


def test_backfill_through_the_store_commits_once_and_is_idempotent(synthetic_root, local_target):
    keys = [_key(v, y) for v in ("acabf", "tas") for y in (1850, 1851)]
    result, _ = _concat(
        synthetic_root,
        {k: dict(seed=i, fill_cell=k.endswith("1851.nc")) for i, k in enumerate(keys)},
    )
    repo = open_repository(local_target, create=True)
    ensure_hierarchy(repo, [result.group_path])
    write_group(repo, result)  # no ranges: a group written before they existed
    stored = open_group(repo, HIST)
    assert ACTUAL_RANGE_ATTR not in stored["acabf"].attrs
    states = range_states(repo, [HIST])
    assert states[HIST].arrays == ["acabf", "tas"] and states[HIST].ranged == []
    assert not states[HIST].complete and states[HIST].attrs["ismip7_n_source_files"] == 4
    assert plan_ranges([HIST], states) == ([HIST], {})

    n_commits = len(list(repo.ancestry(branch="main")))
    snap, rr = range_group(repo, HIST, n_steps=6)
    assert snap and len(rr.ranges) == 2
    assert len(list(repo.ancestry(branch="main"))) == n_commits + 1
    stored = open_group(repo, HIST)
    for var in ("acabf", "tas"):
        expect = _truth(
            synthetic_root, [k for k in keys if f"/{var}/" in k], var, [0, 5, 9, 14, 18, 23]
        )
        assert stored[var].attrs[ACTUAL_RANGE_ATTR] == list(expect)
        assert (
            stored[var].attrs[RANGE_NOTE_ATTR]
            == "sampled from 6 of 24 time steps through the store"
        )
        assert "units" in stored[var].attrs  # the other attributes are kept
    assert np.isnan(stored["acabf"].values[12, 0, 0])  # and so is the fill value
    # the source path gives the same numbers
    src = estimate_ranges(result, n_steps=6)
    assert {r.var: (r.low, r.high) for r in src.ranges} == {
        r.var: (r.low, r.high) for r in rr.ranges
    }

    states = range_states(repo, [HIST])
    assert states[HIST].complete and states[HIST].ranged == ["acabf", "tas"]
    assert plan_ranges([HIST], states) == ([], {HIST: "ranged"})
    assert plan_ranges([HIST], states, overwrite=True) == ([HIST], {})
    assert plan_ranges([HIST], states, patterns=["nope/*"]) == ([], {})
    assert existing_groups(repo)[HIST]["ismip7_group"] == HIST  # the group itself is untouched


def test_range_states_refuses_a_missing_group(local_target):
    from ismip7_helper.virtualize import VirtualizeError

    repo = open_repository(local_target, create=True)
    ensure_hierarchy(repo, ["A/b/c/8000m"])
    with pytest.raises(VirtualizeError, match="no such group"):
        range_states(repo, ["A/b/c/8000m"])
