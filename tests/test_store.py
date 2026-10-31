"""Phase B tests: write virtual groups to a local Icechunk repo, read back, verify."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from ismip7_helper.conventions import parse_key
from ismip7_helper.store import (
    StoreTarget,
    existing_groups,
    load_sc_creds,
    open_group,
    open_repository,
    prepare_for_write,
    repository_config,
    verify_group,
    write_group,
)
from ismip7_helper.virtualize import concat_group, local_registry
from tests.conftest import AIS_KEY_FMT, write_forcing_file

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _keys(exp="historical", years=(1850, 1851), variables=("acabf", "tas")):
    return [
        AIS_KEY_FMT.format(var=v, year=y, esm="CESM2-WACCM", exp=exp, product="SDBN1-32000m", ver=2)
        for v in variables
        for y in years
    ]


@pytest.fixture
def local_group(synthetic_root):
    """A concatenated two-variable, two-year group backed by synthetic files."""
    keys = _keys()
    urls = {}
    for i, k in enumerate(keys):
        p = synthetic_root / k
        write_forcing_file(
            p,
            year=int(k[-7:-3]),
            variable=k.rsplit("/", 1)[1].split("_")[0],
            seed=i,
            chunks=(4, 191, 191) if "/tas/" in k else None,
        )
        urls[k] = f"file://{p}"
    files = [parse_key(k).forcing for k in keys]
    return concat_group(files, local_registry("/"), urls=urls), keys


@pytest.fixture
def local_target(tmp_path, synthetic_root):
    return StoreTarget(
        url=f"local:{tmp_path / 'repo'}",
        local_containers={f"file://{synthetic_root}/": str(synthetic_root)},
    )


def test_store_target_parsing():
    t = StoreTarget.parse("s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais")
    assert t.bucket_and_prefix == (
        "us-west-2.opendata.source.coop",
        "englacial/ismip7/icechunk-ais",
    )
    assert StoreTarget.region_default("GrIS").url.endswith("/englacial/ismip7/icechunk-gris")
    assert StoreTarget.parse("local:/tmp/x").path == "/tmp/x"
    with pytest.raises(ValueError):
        StoreTarget.parse("/tmp/x")


def test_load_sc_creds(tmp_path):
    p = tmp_path / "sc_creds.json"
    p.write_text(
        '{"AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST", "Expiration": "x"}'
    )
    assert load_sc_creds(p) == {
        "access_key_id": "AK",
        "secret_access_key": "SK",
        "session_token": "ST",
    }
    p.write_text('{"foo": 1}')
    with pytest.raises(ValueError):
        load_sc_creds(p)


def test_repository_config_has_source_container_and_time_splitting(local_target):
    cfg = repository_config(local_target)
    prefixes = {c.url_prefix for c in cfg.virtual_chunk_containers.values()}
    assert "s3://us-west-2.opendata.source.coop/ismip/" in prefixes
    assert any(p.startswith("file://") for p in prefixes)
    assert cfg.manifest.splitting is not None and cfg.manifest.preload.max_total_refs == 200_000


def test_prepare_for_write_moves_time_encoding_to_attrs(local_group):
    result, _ = local_group
    ds = prepare_for_write(result.ds)
    assert ds["time"].attrs["units"] == "days since 1850-01-01"
    assert ds["time"].attrs["calendar"] == "standard"
    assert ds["time"].encoding == {}
    assert all("_FillValue" not in ds[v].attrs for v in ds.variables)
    # the original is untouched
    assert "units" not in result.ds["time"].attrs


def test_write_read_verify_roundtrip(local_group, local_target, synthetic_root):
    result, keys = local_group
    repo = open_repository(local_target, create=True)
    wr = write_group(repo, result)
    assert wr.group_path == "CESM2-WACCM/historical/SDBN1-32000m" and wr.n_files == 4
    assert wr.nrefs == 24 + 6  # acabf one chunk per step, tas chunks of 4 steps
    assert existing_groups(repo) == {wr.group_path}

    ds = open_group(repo, wr.group_path)
    assert ds["time"].dtype == "datetime64[ns]"
    assert (
        str(ds["time"].values[0])[:10] == "1850-01-15"
        and str(ds["time"].values[12])[:10] == "1851-01-15"
    )
    assert (
        ds["crs"].attrs["epsg_code"] == "EPSG:3031" and ds["acabf"].attrs["grid_mapping"] == "crs"
    )
    assert ds.attrs["ismip7_group"] == wr.group_path

    # byte-for-byte against the source files, through the store's virtual references
    import netCDF4

    for var, key in (("acabf", keys[1]), ("tas", keys[3])):  # the 1851 files
        with netCDF4.Dataset(synthetic_root / key) as nc:
            src = nc[var][:].filled(np.nan)
        got = ds[var].isel(time=slice(12, 24)).values
        assert np.array_equal(got, src, equal_nan=True)

    vr = verify_group(repo, result, n_files=2)
    assert vr.ok and len(vr.checked) == 4

    # a second write of the same group is refused unless overwrite is set
    from zarr.errors import ContainsGroupError

    with pytest.raises(ContainsGroupError):
        write_group(repo, result)
    wr2 = write_group(repo, result, overwrite=True)
    assert wr2.snapshot_id != wr.snapshot_id
    assert len(list(repo.ancestry(branch="main"))) == 3  # root + 2 commits


def test_verify_detects_corruption(local_group, local_target, synthetic_root, monkeypatch):
    result, keys = local_group
    repo = open_repository(local_target, create=True)
    write_group(repo, result)
    # overwrite one source file with different data: the store now disagrees with the file
    k = keys[0]
    write_forcing_file(synthetic_root / k, year=1850, variable="acabf", seed=99)
    vr = verify_group(repo, result, n_files=2, seed=1)
    assert not vr.ok and any("acabf" in m for m in vr.mismatches)


def test_two_groups_commit_independently(local_group, local_target, synthetic_root):
    result, _ = local_group
    repo = open_repository(local_target, create=True)
    write_group(repo, result)
    keys = _keys(exp="ssp585", years=(2015,), variables=("acabf",))
    urls = {}
    for k in keys:
        p = synthetic_root / k
        write_forcing_file(p, year=2015, variable="acabf", seed=5)
        urls[k] = f"file://{p}"
    r2 = concat_group([parse_key(k).forcing for k in keys], local_registry("/"), urls=urls)
    write_group(repo, r2)
    assert existing_groups(repo) == {
        "CESM2-WACCM/historical/SDBN1-32000m",
        "CESM2-WACCM/ssp585/SDBN1-32000m",
    }
    ds = open_group(repo, r2.group_path)
    assert ds.sizes["time"] == 12 and isinstance(ds, xr.Dataset)
