"""Phase B tests: write virtual groups to a local Icechunk repo, read back, verify."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from ismip7_helper.conventions import parse_key
from ismip7_helper.store import (
    SOURCE_CONTAINER_PREFIX,
    SOURCES_ATTR,
    VERIFIED_ATTR,
    RepositoryNotFoundError,
    StoreTarget,
    check_target_region,
    delete_group,
    ensure_hierarchy,
    existing_groups,
    load_sc_creds,
    mark_verified,
    open_group,
    open_repository,
    prepare_for_write,
    repository_config,
    sources_sha256,
    storage_for,
    tag_release,
    verify_group,
    write_group,
)
from ismip7_helper.virtualize import concat_group, key_to_url, local_registry
from tests.conftest import AIS_KEY_FMT, write_forcing_file

pytestmark = [
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:variable .* has multiple fill values"),
]


def _keys(
    exp="historical",
    years=(1850, 1851),
    variables=("acabf", "tas"),
    esm="CESM2-WACCM",
    product="SDBN1-32000m",
):
    return [
        AIS_KEY_FMT.format(var=v, year=y, esm=esm, exp=exp, product=product, ver=2)
        for v in variables
        for y in years
    ]


def _group(root, seed=0, **kw):
    """Write synthetic files for one group and concatenate them."""
    keys = _keys(**kw)
    urls = {}
    for i, k in enumerate(keys):
        p = root / k
        write_forcing_file(
            p, year=int(k[-7:-3]), variable=k.rsplit("/", 1)[1].split("_")[0], seed=seed + i
        )
        urls[k] = f"file://{p}"
    return concat_group([parse_key(k).forcing for k in keys], local_registry(), urls=urls)


def _stage(repo, result, mode="w-"):
    """Stage a group write in its own session without committing (a concurrent worker)."""
    session = repo.writable_session("main")
    prepare_for_write(result.ds).vz.to_icechunk(session.store, group=result.group_path, mode=mode)
    return session


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
            fill_cell=k.endswith("acabf_AIS_CESM2-WACCM_historical_SDBN1-32000m_v2_1851.nc"),
        )
        urls[k] = f"file://{p}"
    files = [parse_key(k).forcing for k in keys]
    return concat_group(files, local_registry(), urls=urls), keys


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


def test_check_target_region():
    check_target_region(StoreTarget.region_default("AIS"), "AIS")
    check_target_region("local:/tmp/anything", "GrIS")
    with pytest.raises(ValueError, match="AIS repository"):
        check_target_region(StoreTarget.region_default("AIS"), "GrIS")


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
    # the STS response shape, with a parseable expiry
    p.write_text(
        '{"Credentials": {"AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST",'
        ' "Expiration": "2026-11-01T12:00:00Z"}}'
    )
    c = load_sc_creds(p)
    assert c["access_key_id"] == "AK" and c["session_token"] == "ST"
    assert c["expires_after"].isoformat() == "2026-11-01T12:00:00+00:00"
    p.write_text('{"foo": 1}')
    with pytest.raises(ValueError):
        load_sc_creds(p)


def test_credentials_stay_out_of_repr():
    t = StoreTarget(
        url="s3://bucket/prefix",
        credentials={
            "access_key_id": "AKID",
            "secret_access_key": "SECRET",
            "session_token": "TOK",
        },
    )
    r = repr(t)
    assert "SECRET" not in r and "TOK" not in r and "AKID" not in r and "s3://bucket" in r
    assert t.has_write_credentials and not StoreTarget(url="s3://b/p").has_write_credentials
    storage_for(t)  # static credentials are accepted

    from ismip7_helper.lambda_handler import SecretCredentials

    t2 = StoreTarget(url="s3://bucket/prefix", get_credentials=SecretCredentials("ismip7/x"))
    assert "SecretCredentials" not in repr(t2) and t2.has_write_credentials
    storage_for(t2)  # a (picklable) refresh callback is accepted; not called until a request


def test_repository_config_has_source_container_and_time_splitting(local_target):
    cfg = repository_config(local_target)
    prefixes = {c.url_prefix for c in cfg.virtual_chunk_containers.values()}
    assert "s3://us-west-2.opendata.source.coop/ismip/" in prefixes
    assert any(p.startswith("file://") for p in prefixes)
    assert cfg.manifest.splitting is not None and cfg.manifest.preload is None


def test_config_is_persisted_at_create(tmp_path):
    import icechunk

    t = StoreTarget(url=f"local:{tmp_path / 'repo'}")
    open_repository(t, create=True)
    stored = icechunk.Repository.fetch_config(storage_for(t))
    assert SOURCE_CONTAINER_PREFIX in {
        c.url_prefix for c in stored.virtual_chunk_containers.values()
    }
    assert stored.manifest.splitting is not None
    # a config-less open sees it too
    plain = icechunk.Repository.open(storage_for(t))
    assert plain.config.manifest.splitting is not None


def test_manifest_split_unit_is_chunks_along_time(synthetic_root, tmp_path):
    """Four years of 1-step chunks split every 12 chunks gives four manifests for the array."""
    import icechunk

    from ismip7_helper.store import container_credentials

    result = _group(synthetic_root, years=(1850, 1851, 1852, 1853), variables=("acabf",))

    def n_manifests(split, name):
        t = StoreTarget(
            url=f"local:{tmp_path / name}",
            local_containers={f"file://{synthetic_root}/": str(synthetic_root)},
        )
        repo = icechunk.Repository.create(
            storage_for(t),
            config=repository_config(t, split_chunks=split),
            authorize_virtual_chunk_access=container_credentials(t),
            spec_version=icechunk.SpecVersion.v2,
        )
        write_group(repo, result)
        return len(list((tmp_path / name / "manifests").iterdir()))

    assert n_manifests(1000, "one") + 3 == n_manifests(12, "split")


def test_open_repository_does_not_create_unless_asked(tmp_path):
    t = StoreTarget(url=f"local:{tmp_path / 'repo'}")
    with pytest.raises(RepositoryNotFoundError, match="creates it"):
        open_repository(t)
    with pytest.raises(RepositoryNotFoundError):
        open_repository(t, readonly=True)
    open_repository(t, create=True)
    open_repository(t)
    open_repository(t, create=True)  # existing: opened


def test_existing_groups_on_a_fresh_repo(local_target):
    assert existing_groups(open_repository(local_target, create=True)) == {}


def test_ensure_hierarchy(local_target):
    import zarr

    repo = open_repository(local_target, create=True)
    gps = ["CESM2-WACCM/historical/SDBN1-32000m", "climatology/UKESM1-0-LL/ssp585/GEMB-8000m"]
    snap = ensure_hierarchy(repo, gps, region="AIS")
    assert snap is not None
    root = zarr.open_group(repo.readonly_session("main").store, mode="r")
    assert root.attrs["ismip7_region"] == "AIS"
    for p in ("CESM2-WACCM", "CESM2-WACCM/historical", "climatology/UKESM1-0-LL/ssp585"):
        assert isinstance(root[p], zarr.Group)
    assert "CESM2-WACCM/historical/SDBN1-32000m" not in root
    assert ensure_hierarchy(repo, gps, region="AIS") is None  # idempotent, no commit
    assert len(list(repo.ancestry(branch="main"))) == 2
    assert existing_groups(repo) == {}  # parents are not data groups
    with pytest.raises(ValueError, match="AIS"):
        ensure_hierarchy(repo, gps, region="GrIS")
    meta = next(iter(repo.ancestry(branch="main"))).metadata
    assert meta["operation"] == "ensure_hierarchy" and "ismip7_helper_version" in meta


def test_prepare_for_write_is_a_noop_safety_net(local_group):
    result, _ = local_group
    ds = prepare_for_write(result.ds)
    # virtualize already left time as to_icechunk needs it
    assert ds["time"].attrs["units"] == "days since 1850-01-01"
    assert ds["time"].attrs["calendar"] == "standard"
    assert ds["time"].encoding == {"_FillValue": None}
    for c in ("time", "x", "y", "crs"):
        assert "_FillValue" not in ds[c].attrs and ds[c].encoding["_FillValue"] is None
    # the data variables keep VirtualiZarr's fill value attribute
    assert ds["acabf"].attrs["_FillValue"] == result.ds["acabf"].attrs["_FillValue"]
    again = prepare_for_write(ds)
    assert all(again[v].attrs == ds[v].attrs for v in ds.variables)


def test_prepare_for_write_fixes_legacy_coordinates():
    ds = xr.Dataset(
        coords={
            "time": ("time", np.array([14.0]), {"_FillValue": np.nan}),
            "x": ("x", np.array([0.0, 1.0]), {"_FillValue": np.nan}),
        }
    )
    ds["time"].encoding = {"units": "days since 1850-01-01", "calendar": "standard"}
    out = prepare_for_write(ds)
    assert out["time"].attrs == {"units": "days since 1850-01-01", "calendar": "standard"}
    assert out["time"].encoding == {"_FillValue": None} and out["x"].attrs == {}
    # the input is not modified
    assert "_FillValue" in ds["time"].attrs and "units" in ds["time"].encoding


def test_write_read_verify_roundtrip(local_group, local_target, synthetic_root):
    result, keys = local_group
    repo = open_repository(local_target, create=True)
    wr = write_group(repo, result)
    assert wr.group_path == "CESM2-WACCM/historical/SDBN1-32000m" and wr.n_files == 4
    assert wr.nrefs == 24 + 6  # acabf one chunk per step, tas chunks of 4 steps
    found = existing_groups(repo)
    assert list(found) == [wr.group_path]
    # the group records the digest of its sources (sizes unknown here)
    assert found[wr.group_path][SOURCES_ATTR] == sources_sha256((k, None) for k in keys)
    assert wr.sources_sha256 == found[wr.group_path][SOURCES_ATTR]
    assert VERIFIED_ATTR not in found[wr.group_path]
    assert SOURCES_ATTR not in result.ds.attrs  # the input is not modified

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
    # the fill cell written at [0, 0, 0] of the 1851 acabf file reads back masked
    assert np.isnan(ds["acabf"].values[12, 0, 0]) and np.isfinite(ds["acabf"].values[12, 0, 1])
    assert float(ds["acabf"].encoding["_FillValue"]) == pytest.approx(9.96921e36)
    # coordinates are stored without a fill value
    raw = open_group(repo, wr.group_path, decode_times=False, mask_and_scale=False)
    for c in ("time", "x", "y", "crs"):
        assert "_FillValue" not in raw[c].attrs and "_FillValue" not in raw[c].encoding, c
    assert raw["time"].attrs["units"] == "days since 1850-01-01"

    # byte-for-byte against the source files, through the store's virtual references
    import netCDF4

    for var, key in (("acabf", keys[1]), ("tas", keys[3])):  # the 1851 files
        with netCDF4.Dataset(synthetic_root / key) as nc:
            src = nc[var][:].filled(np.nan)
        got = ds[var].isel(time=slice(12, 24)).values
        assert np.array_equal(got, src, equal_nan=True)

    vr = verify_group(repo, result, n_files=2, n_steps=3, snapshot_id=wr.snapshot_id)
    assert vr.ok and len(vr.checked) == 4

    # a second write of the same group is refused unless overwrite is set
    from zarr.errors import ContainsGroupError

    with pytest.raises(ContainsGroupError):
        write_group(repo, result)
    wr2 = write_group(repo, result, overwrite=True)
    assert wr2.snapshot_id != wr.snapshot_id
    assert len(list(repo.ancestry(branch="main"))) == 3  # root + 2 commits


def test_commit_metadata(local_group, local_target):
    result, _ = local_group
    repo = open_repository(local_target, create=True)
    wr = write_group(repo, result)
    meta = next(iter(repo.ancestry(branch="main"))).metadata
    assert meta["operation"] == "write" and meta["group"] == wr.group_path
    assert meta["sources_sha256"] == wr.sources_sha256 and meta["ismip7_file_version"] == "v2"
    assert meta["n_source_files"] == 4 and meta["ismip7_helper_version"]
    assert meta["headers_read_at"] == result.opened_at.isoformat()
    write_group(repo, result, overwrite=True)
    assert next(iter(repo.ancestry(branch="main"))).metadata["operation"] == "overwrite"
    snap = tag_release(repo, "v0.1")
    assert repo.lookup_tag("v0.1") == snap


def test_mark_verified_and_delete_group(local_group, local_target):
    result, _ = local_group
    repo = open_repository(local_target, create=True)
    wr = write_group(repo, result)
    mark_verified(repo, wr.group_path, wr.snapshot_id)
    assert existing_groups(repo)[wr.group_path][VERIFIED_ATTR] == wr.snapshot_id
    delete_group(repo, wr.group_path, reason="test")
    assert existing_groups(repo) == {}
    assert next(iter(repo.ancestry(branch="main"))).metadata["operation"] == "delete"
    # the written snapshot still holds it
    assert "acabf" in open_group(repo, wr.group_path, snapshot_id=wr.snapshot_id)


def test_verify_detects_corruption(local_group, local_target, synthetic_root, monkeypatch):
    result, keys = local_group
    repo = open_repository(local_target, create=True)
    write_group(repo, result)
    # overwrite one source file with different data: the store now disagrees with the file
    # (a read error if the store notices the newer mtime, else a value mismatch)
    k = keys[0]
    write_forcing_file(synthetic_root / k, year=1850, variable="acabf", seed=99)
    vr = verify_group(repo, result, n_files=2, seed=1)
    assert not vr.ok and any("acabf" in m for m in vr.mismatches)


def test_verify_compares_against_an_injected_source(local_group, local_target, synthetic_root):
    """A source that differs from what was written is reported, via ranged reads."""
    from ismip7_helper.store import open_source

    result, _ = local_group
    repo = open_repository(local_target, create=True)
    wr = write_group(repo, result)
    other = synthetic_root / "other.nc"
    write_forcing_file(other, year=1850, variable="acabf", seed=42)
    write_forcing_file(synthetic_root / "other_tas.nc", year=1850, variable="tas", seed=43)

    def opener(url):
        name = "other.nc" if "/acabf/" in url else "other_tas.nc"
        return open_source(f"file://{synthetic_root / name}")

    vr = verify_group(repo, result, n_files=1, snapshot_id=wr.snapshot_id, opener=opener)
    assert not vr.ok and len(vr.mismatches) == 2 and "max |diff|" in vr.mismatches[0]


def test_verify_reads_steps_not_whole_files(local_group, local_target, synthetic_root):
    """The source is read through ranged requests: far fewer bytes than the file."""
    from ismip7_helper.store import open_source

    result, _ = local_group
    repo = open_repository(local_target, create=True)
    wr = write_group(repo, result)
    read = {}

    def opener(url):
        f = open_source(url)
        orig = f.readinto

        def counting(b):
            n = orig(b)
            read[url] = read.get(url, 0) + n
            return n

        f.readinto = counting
        return f

    assert verify_group(repo, result, n_files=2, snapshot_id=wr.snapshot_id, opener=opener).ok
    assert read
    for url, n in read.items():
        size = Path(url[len("file://") :]).stat().st_size
        assert 0 < n < size / 3, (n, size)  # header plus one step, not the whole file


def test_file_offsets_skip_empty_chunks():
    from types import SimpleNamespace

    from virtualizarr.manifests import ChunkManifest

    from ismip7_helper.store import _file_offsets

    m = ChunkManifest(
        entries={
            "0.0": {"path": "", "offset": 0, "length": 0},
            "0.1": {"path": "s3://b/a.nc", "offset": 1, "length": 2},
            "1.0": {"path": "s3://b/b.nc", "offset": 1, "length": 2},
            "1.1": {"path": "s3://b/b.nc", "offset": 3, "length": 2},
        }
    )
    marr = SimpleNamespace(manifest=m, metadata=SimpleNamespace(chunks=(12, 96)))
    assert _file_offsets(marr) == [("s3://b/a.nc", 0), ("s3://b/b.nc", 12)]


def test_prepare_for_write_refuses_unitless_time():
    ds = xr.Dataset(coords={"time": ("time", np.array([14.0]))})
    with pytest.raises(ValueError, match="no units"):
        prepare_for_write(ds)


def test_climatology_group_writes_and_verifies(synthetic_root, local_target):
    """A month-indexed climatology (no time axis) goes through write and verify."""
    import netCDF4

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
    f = parse_key(key).forcing
    result = concat_group([f], local_registry(), urls={key: f"file://{p}"})
    assert result.group_path.startswith("climatology/") and "month" in result.ds["acabf"].dims
    repo = open_repository(local_target, create=True)
    ensure_hierarchy(repo, [result.group_path])
    wr = write_group(repo, result)
    vr = verify_group(repo, result, n_files=1, n_steps=4, snapshot_id=wr.snapshot_id)
    assert vr.ok, vr.mismatches
    assert list(existing_groups(repo)) == [result.group_path]


def test_refs_match_the_s3_container_prefix(local_group, tmp_path):
    """Refs built by key_to_url fall under the anonymous source container, and nothing else does."""
    result, keys = local_group
    assert key_to_url(keys[0]).startswith(SOURCE_CONTAINER_PREFIX)

    def to_s3(path):
        return key_to_url(path.split("/bucket/", 1)[1])

    s3_ds = result.ds.vz.rename_paths(to_s3)
    s3_result = type(result)(result.group_path, s3_ds, result.n_files, [], result.sources)
    repo = open_repository(StoreTarget(url=f"local:{tmp_path / 's3only'}"), create=True)
    write_group(repo, s3_result)
    locations = repo.readonly_session("main").all_virtual_chunk_locations()
    assert locations and all(u.startswith(SOURCE_CONTAINER_PREFIX) for u in locations)
    assert {key_to_url(k) for k in keys} == set(locations)

    # a reference outside the container is refused at write time
    bad = result.ds.vz.rename_paths(lambda p: "s3://us-west-2.opendata.source.coop/other/x.nc")
    bad_result = type(result)("CESM2-WACCM/ssp585/SDBN1-32000m", bad, 1, [])
    with pytest.raises(Exception, match=r"(?i)container"):
        write_group(repo, bad_result)


def test_concurrent_writes_share_a_new_parent_after_ensure_hierarchy(synthetic_root, local_target):
    import icechunk

    repo = open_repository(local_target, create=True)
    r1 = _group(synthetic_root, esm="UKESM1-0-LL", exp="ssp585", years=(2015,))
    r2 = _group(
        synthetic_root, esm="UKESM1-0-LL", exp="ssp585", years=(2015,), product="GEMB-32000m"
    )
    # without the hierarchy the second commit cannot rebase (both create /UKESM1-0-LL ...)
    s1, s2 = _stage(repo, r1), _stage(repo, r2)
    s1.commit("one", rebase_with=icechunk.ConflictDetector())
    with pytest.raises(icechunk.RebaseFailedError) as err:
        s2.commit("two", rebase_with=icechunk.ConflictDetector(), rebase_tries=5)
    from ismip7_helper.store import _is_ancestor_creation_conflict

    assert _is_ancestor_creation_conflict(err.value, r2.group_path)

    # after ensure_hierarchy two staged sessions under the same new parent both commit
    r3 = _group(synthetic_root, esm="MPI-ESM1-2-HR", exp="historical", years=(1850,))
    r4 = _group(
        synthetic_root, esm="MPI-ESM1-2-HR", exp="historical", years=(1850,), product="GEMB-32000m"
    )
    ensure_hierarchy(repo, [r3.group_path, r4.group_path])
    s3, s4 = _stage(repo, r3), _stage(repo, r4)
    s3.commit("three", rebase_with=icechunk.ConflictDetector())
    s4.commit("four", rebase_with=icechunk.ConflictDetector(), rebase_tries=5)
    assert {r1.group_path, r3.group_path, r4.group_path} == set(existing_groups(repo))


def test_same_group_twice_conflicts(synthetic_root, local_target):
    import icechunk

    from ismip7_helper.store import _is_ancestor_creation_conflict

    repo = open_repository(local_target, create=True)
    r = _group(synthetic_root, years=(1850,))
    ensure_hierarchy(repo, [r.group_path])
    s1, s2 = _stage(repo, r), _stage(repo, r)
    s1.commit("one", rebase_with=icechunk.ConflictDetector())
    with pytest.raises(icechunk.RebaseFailedError) as err:
        s2.commit("two", rebase_with=icechunk.ConflictDetector(), rebase_tries=5)
    assert not _is_ancestor_creation_conflict(err.value, r.group_path)
    # two overwrites of the same group conflict too
    s1, s2 = _stage(repo, r, "w"), _stage(repo, r, "w")
    s1.commit("one", rebase_with=icechunk.ConflictDetector())
    with pytest.raises(icechunk.RebaseFailedError):
        s2.commit("two", rebase_with=icechunk.ConflictDetector(), rebase_tries=5)


def test_overwrite_beside_a_sibling_rebases(synthetic_root, local_target):
    import icechunk

    repo = open_repository(local_target, create=True)
    a = _group(synthetic_root, years=(1850,))
    b = _group(synthetic_root, years=(1850,), product="GEMB-32000m")
    ensure_hierarchy(repo, [a.group_path, b.group_path])
    write_group(repo, a)
    s1, s2 = _stage(repo, a, "w"), _stage(repo, b)
    s2.commit("sibling", rebase_with=icechunk.ConflictDetector())
    s1.commit("overwrite", rebase_with=icechunk.ConflictDetector(), rebase_tries=5)
    assert set(existing_groups(repo)) == {a.group_path, b.group_path}


def test_parallel_writes_on_a_fresh_repo(synthetic_root, local_target):
    """Six threads, two new ESMs, no hierarchy: the write_group backstop re-stages them all.
    With ensure_hierarchy first, they rebase without re-staging."""
    import threading

    import icechunk

    from ismip7_helper.store import container_credentials

    products = ("SDBN1-32000m", "dEBM2-32000m", "GEMB-32000m")
    results = [
        _group(synthetic_root, esm=e, product=p, years=(1850,), variables=("acabf",))
        for e in ("CESM2-WACCM", "UKESM1-0-LL")
        for p in products
    ]
    for hierarchy in (False, True):
        repo = icechunk.Repository.create(
            icechunk.in_memory_storage(),
            config=repository_config(local_target),
            authorize_virtual_chunk_access=container_credentials(local_target),
            spec_version=icechunk.SpecVersion.v2,
        )
        if hierarchy:
            ensure_hierarchy(repo, [r.group_path for r in results])
        out = {}
        barrier = threading.Barrier(len(results))

        def work(r, repo=repo, out=out, barrier=barrier):
            barrier.wait()
            try:
                write_group(repo, r)
                out[r.group_path] = "ok"
            except Exception as e:
                out[r.group_path] = type(e).__name__

        threads = [threading.Thread(target=work, args=(r,)) for r in results]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert set(out.values()) == {"ok"}, out
        assert set(existing_groups(repo)) == {r.group_path for r in results}


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
    r2 = concat_group([parse_key(k).forcing for k in keys], local_registry(), urls=urls)
    write_group(repo, r2)
    assert set(existing_groups(repo)) == {
        "CESM2-WACCM/historical/SDBN1-32000m",
        "CESM2-WACCM/ssp585/SDBN1-32000m",
    }
    ds = open_group(repo, r2.group_path)
    assert ds.sizes["time"] == 12 and isinstance(ds, xr.Dataset)


def test_kept_missing_value_masks_on_read(synthetic_root, local_target):
    (key,) = _keys(years=(1850,), variables=("acabf",))
    p = synthetic_root / key
    write_forcing_file(p, year=1850, variable="acabf", missing_value=-999.0)
    import netCDF4

    with netCDF4.Dataset(p, "a") as nc:
        nc["acabf"].set_auto_mask(False)
        nc["acabf"][0, 0, 0] = -999.0
    result = concat_group([parse_key(key).forcing], local_registry(), urls={key: f"file://{p}"})
    assert any("kept missing_value=-999.0" in n for n in result.notes)
    repo = open_repository(local_target, create=True)
    write_group(repo, result)
    ds = open_group(repo, result.group_path)
    assert np.isnan(ds["acabf"].values[0, 0, 0]) and np.isfinite(ds["acabf"].values[0, 0, 1])
    assert verify_group(repo, result, n_files=1).ok


def test_write_group_restages_after_losing_its_rebase_races(local_group, local_target, monkeypatch):
    """A commit that exhausts its rebase tries (ConflictError) is re-staged after a pause."""
    import icechunk

    from ismip7_helper import store as store_mod

    result, _ = local_group
    repo = open_repository(local_target, create=True)
    pauses: list[int] = []
    monkeypatch.setattr(store_mod, "_conflict_pause", lambda gp, attempt: pauses.append(attempt))
    real_session = repo.writable_session
    failures = {"left": 1}

    def _raise(*a, **k):
        raise icechunk.ConflictError("Failed to commit, expected parent: x, actual parent: y")

    def flaky_session(branch):
        session = real_session(branch)
        if failures["left"] > 0:
            failures["left"] -= 1
            session.commit = _raise  # type: ignore[method-assign]
        return session

    monkeypatch.setattr(repo, "writable_session", flaky_session)
    wr = write_group(repo, result)
    assert pauses == [0] and wr.group_path in existing_groups(repo)

    failures["left"] = 10  # every attempt fails: the error surfaces after restage_tries
    with pytest.raises(icechunk.ConflictError):
        write_group(repo, result, overwrite=True, restage_tries=1)
    assert pauses == [0, 0]


def test_native_tail_round_trip(synthetic_root, local_target):
    """A last file chunked unlike its series is stored natively and reads back exactly."""
    import xarray as xr

    from ismip7_helper.virtualize import NativeTail

    keys = _keys(years=(1850, 1851, 1852), variables=("acabf",))
    urls, paths = {}, {}
    for i, k in enumerate(keys):
        p = synthetic_root / k
        write_forcing_file(
            p,
            year=int(k[-7:-3]),
            variable="acabf",
            seed=i,
            chunks=(1, 96, 96) if k.endswith("1852.nc") else None,
            fill_cell=k.endswith("1852.nc"),
        )
        urls[k], paths[k] = f"file://{p}", p
    result = concat_group([parse_key(k).forcing for k in keys], local_registry(), urls=urls)
    assert result.native_tails == [NativeTail("acabf", 24, 12, urls[keys[-1]], keys[-1])]
    assert any("stored natively" in n for n in result.notes)
    manifest = result.ds["acabf"].data.manifest
    assert result.ds["acabf"].data.metadata.chunks == (1, 191, 191)
    assert manifest.shape_chunk_grid[0] == 36 and "24.0.0" not in manifest.dict()

    repo = open_repository(local_target, create=True)
    wr = write_group(repo, result)
    assert wr.nrefs == 36  # the chunk grid, placeholders included
    stored = open_group(repo, wr.group_path)["acabf"]
    with xr.open_dataset(paths[keys[-1]]) as tail:
        np.testing.assert_array_equal(stored.isel(time=slice(24, 36)).values, tail["acabf"].values)
        assert np.isnan(stored.isel(time=24).values[0, 0])  # the fill cell decodes as NaN
    with xr.open_dataset(paths[keys[0]]) as head:
        np.testing.assert_array_equal(stored.isel(time=slice(0, 12)).values, head["acabf"].values)
    assert verify_group(repo, result, n_files=3, seed=0).ok
