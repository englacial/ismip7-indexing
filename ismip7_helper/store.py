"""Phase B of the pipeline: write virtual groups into an Icechunk repository.

One repository per region. Data chunks stay in the source files on
source.coop and are reached through a *virtual chunk container* registered in
the repository config; coordinates and the ``crs`` scalar are stored inline.

The same code writes to a local directory (tests, dry runs) or to S3 (the
``englacial/ismip7`` prefix on source.coop), selected by :class:`StoreTarget`.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from . import SOURCE_BUCKET, SOURCE_REGION, TARGET_PREFIX
from .conventions.models import SUBMISSION_CALENDAR, SUBMISSION_TIME_UNITS
from .virtualize import COORD_VARS, SOURCE_URL, GroupResult

logger = logging.getLogger(__name__)

#: Virtual chunk container prefix for the public source data.
SOURCE_CONTAINER_PREFIX = f"{SOURCE_URL}/ismip/"
#: Timesteps per manifest split. 120 months of the 2 km AIS products is
#: ~17k references per manifest; 8 km products get small manifests, which is
#: cheap. A browser client (icechunk-js) fetches one split per read.
MANIFEST_SPLIT_TIMESTEPS = 120
#: Allow a readonly session to preload this many references (default 10k is
#: below one split of a 2 km array).
MANIFEST_PRELOAD_MAX_REFS = 200_000


# --------------------------------------------------------------------------- target


@dataclass(frozen=True)
class StoreTarget:
    """Where a repository lives.

    ``local:/path/to/repo`` or ``s3://bucket/prefix``. For S3 the write
    credentials come from the environment (or an explicit ``credentials``
    mapping with ``access_key_id``/``secret_access_key``/``session_token``,
    e.g. parsed from a source.coop ``sc_creds.json``).
    """

    url: str
    region: str = SOURCE_REGION
    credentials: dict[str, str] | None = field(default=None, hash=False)
    #: ``url_prefix`` -> local root, for virtual chunks that live on local disk
    #: (tests only). Production repos reference the public bucket.
    local_containers: dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def is_local(self) -> bool:
        return self.url.startswith("local:")

    @property
    def path(self) -> str:
        if not self.is_local:
            raise ValueError("not a local target")
        return self.url[len("local:") :]

    @property
    def bucket_and_prefix(self) -> tuple[str, str]:
        if self.is_local:
            raise ValueError("not an S3 target")
        rest = self.url[len("s3://") :]
        bucket, _, prefix = rest.partition("/")
        return bucket, prefix.rstrip("/")

    @classmethod
    def region_default(cls, region: str, bucket: str = SOURCE_BUCKET) -> StoreTarget:
        """The production repo for a region: ``s3://<bucket>/englacial/ismip7/icechunk-<reg>``."""
        return cls(url=f"s3://{bucket}/{TARGET_PREFIX}/icechunk-{region.lower()}")

    @classmethod
    def parse(cls, url: str, **kw: Any) -> StoreTarget:
        if not (url.startswith("local:") or url.startswith("s3://")):
            raise ValueError(f"target must be local:/path or s3://bucket/prefix, got {url!r}")
        return cls(url=url, **kw)


def load_sc_creds(path: str | Path) -> dict[str, str]:
    """Read a source.coop credentials file (``sc_creds.json``) into the keys icechunk wants."""
    raw = json.loads(Path(path).read_text())
    # source.coop hands out AWS-style temporary credentials; accept both spellings.
    pick = lambda *names: next((raw[n] for n in names if n in raw), None)  # noqa: E731
    out = {
        "access_key_id": pick("access_key_id", "AccessKeyId", "aws_access_key_id"),
        "secret_access_key": pick("secret_access_key", "SecretAccessKey", "aws_secret_access_key"),
        "session_token": pick("session_token", "SessionToken", "aws_session_token"),
    }
    if not out["access_key_id"] or not out["secret_access_key"]:
        raise ValueError(f"{path}: no access key / secret found; keys present: {sorted(raw)}")
    return {k: v for k, v in out.items() if v}


# --------------------------------------------------------------------------- repo


def repository_config(
    target: StoreTarget,
    *,
    split_timesteps: int = MANIFEST_SPLIT_TIMESTEPS,
) -> Any:
    """Repository config: virtual chunk containers plus manifest splitting along time."""
    import icechunk

    cfg = icechunk.RepositoryConfig.default()
    cfg.set_virtual_chunk_container(
        icechunk.VirtualChunkContainer(
            url_prefix=SOURCE_CONTAINER_PREFIX,
            store=icechunk.s3_store(region=SOURCE_REGION, anonymous=True),
        )
    )
    for prefix, root in target.local_containers.items():
        cfg.set_virtual_chunk_container(
            icechunk.VirtualChunkContainer(
                url_prefix=prefix, store=icechunk.local_filesystem_store(root)
            )
        )
    cfg.manifest = icechunk.ManifestConfig(
        preload=icechunk.ManifestPreloadConfig(max_total_refs=MANIFEST_PRELOAD_MAX_REFS),
        splitting=icechunk.ManifestSplittingConfig.from_dict(
            {
                icechunk.ManifestSplitCondition.AnyArray(): {
                    icechunk.ManifestSplitDimCondition.DimensionName("time"): split_timesteps
                }
            }
        ),
    )
    return cfg


def container_credentials(target: StoreTarget) -> dict[str, Any]:
    import icechunk

    m: dict[str, Any] = {SOURCE_CONTAINER_PREFIX: icechunk.s3_anonymous_credentials()}
    for prefix in target.local_containers:
        m[prefix] = icechunk.Credentials.LocalFileSystemAccess()
    return icechunk.containers_credentials(m)


def storage_for(target: StoreTarget) -> Any:
    import icechunk

    if target.is_local:
        return icechunk.local_filesystem_storage(target.path)
    bucket, prefix = target.bucket_and_prefix
    kw: dict[str, Any] = {"bucket": bucket, "prefix": prefix, "region": target.region}
    if target.credentials:
        kw.update(target.credentials)
    else:
        kw["from_env"] = True
    return icechunk.s3_storage(**kw)


def open_repository(target: StoreTarget, *, create: bool = False, readonly: bool = False) -> Any:
    """Open the repository at ``target``; with ``create`` make it if it does not exist (spec v2)."""
    import icechunk

    storage = storage_for(target)
    cfg = repository_config(target)
    creds = container_credentials(target)
    if readonly:
        return icechunk.Repository.open(storage, config=cfg, authorize_virtual_chunk_access=creds)
    if create and not icechunk.Repository.exists(storage):
        return icechunk.Repository.create(
            storage,
            config=cfg,
            authorize_virtual_chunk_access=creds,
            spec_version=icechunk.SpecVersion.v2,
        )
    return icechunk.Repository.open(storage, config=cfg, authorize_virtual_chunk_access=creds)


# --------------------------------------------------------------------------- write


def prepare_for_write(ds: xr.Dataset) -> xr.Dataset:
    """Safety net before ``to_icechunk``; a no-op on :func:`~.virtualize.concat_group` output.

    ``virtualize`` already leaves the dataset as ``to_icechunk`` needs it:
    ``time`` units/calendar as attributes (loaded variables are written by
    ``to_zarr``, which only CF-encodes datetime values), and the base64
    ``_FillValue`` attribute VirtualiZarr sets kept on the virtual data
    variables so readers mask fill cells. This only makes sure the coordinate
    variables (:data:`~.virtualize.COORD_VARS`) carry no ``_FillValue`` (CF
    discourages it) and that ``time`` has its units, moving them from
    ``encoding`` if an older caller left them there. Idempotent; the input is
    not modified.
    """
    ds = ds.copy()
    if "time" in ds.variables:
        t = ds["time"]
        for k, default in (("units", SUBMISSION_TIME_UNITS), ("calendar", SUBMISSION_CALENDAR)):
            if k not in t.attrs:
                t.attrs[k] = t.encoding.get(k, default)
            t.encoding.pop(k, None)
    for c in COORD_VARS:
        if c in ds.variables:
            for k in ("_FillValue", "missing_value"):
                ds[c].attrs.pop(k, None)
            ds[c].encoding["_FillValue"] = None
    return ds


@dataclass
class WriteResult:
    group_path: str
    snapshot_id: str
    n_files: int
    nbytes_virtual: int
    nrefs: int


def write_group(
    repo: Any,
    result: GroupResult,
    *,
    overwrite: bool = False,
    branch: str = "main",
    rebase_tries: int = 50,
    message: str | None = None,
) -> WriteResult:
    """Write one group in its own commit, rebasing on concurrent commits to other groups."""
    import icechunk

    ds = prepare_for_write(result.ds)
    session = repo.writable_session(branch)
    mode = "w" if overwrite else "w-"
    ds.vz.to_icechunk(session.store, group=result.group_path, mode=mode)
    msg = (
        message
        or f"{'rewrite' if overwrite else 'write'} {result.group_path} ({result.n_files} files)"
    )
    meta = {
        "group": result.group_path,
        "n_source_files": result.n_files,
        "source_keys_prefix": ds.attrs.get("source_keys_prefix", ""),
    }
    snap = session.commit(
        msg, metadata=meta, rebase_with=icechunk.ConflictDetector(), rebase_tries=rebase_tries
    )
    nrefs = int(result.ds.vz.nrefs())
    nbytes = int(result.ds.vz.nbytes)
    logger.info("committed %s as %s (%d refs)", result.group_path, snap, nrefs)
    return WriteResult(result.group_path, snap, result.n_files, nbytes, nrefs)


def existing_groups(repo: Any, branch: str = "main") -> set[str]:
    """Group paths that already hold arrays (so a run can skip or overwrite them)."""
    import zarr

    session = repo.readonly_session(branch)
    root = zarr.open_group(session.store, mode="r")
    found: set[str] = set()

    def walk(g, path):
        for name, child in g.members():
            if isinstance(child, zarr.Group):
                walk(child, f"{path}/{name}" if path else name)
            else:
                found.add(path)

    walk(root, "")
    return found


# --------------------------------------------------------------------------- verify


def open_group(repo: Any, group_path: str, branch: str = "main", **kw: Any) -> xr.Dataset:
    session = repo.readonly_session(branch)
    return xr.open_zarr(session.store, group=group_path, consolidated=False, **kw)


def _source_array(
    url: str, variable: str, fetch: Callable[[str], bytes] | None = None
) -> np.ndarray:
    """Read ``variable`` from the source NetCDF at ``url`` (``file://`` or ``s3://``)."""
    import netCDF4

    if url.startswith("file://"):
        path = url[len("file://") :]
        with netCDF4.Dataset(path) as nc:
            return nc[variable][:].filled(np.nan)
    data = (fetch or _fetch_s3)(url)
    with tempfile.NamedTemporaryFile(suffix=".nc") as tmp:
        tmp.write(data)
        tmp.flush()
        with netCDF4.Dataset(tmp.name) as nc:
            return nc[variable][:].filled(np.nan)


def _fetch_s3(url: str) -> bytes:
    import obstore

    store = obstore.store.from_url(SOURCE_URL, skip_signature=True, region=SOURCE_REGION)
    key = url[len(SOURCE_URL) + 1 :]
    return bytes(obstore.get(store, key).bytes())


@dataclass
class VerifyResult:
    group_path: str
    checked: list[tuple[str, str]]  # (variable, source url)
    mismatches: list[str]

    @property
    def ok(self) -> bool:
        return not self.mismatches


def verify_group(
    repo: Any,
    result: GroupResult,
    *,
    n_files: int = 2,
    seed: int = 0,
    fetch: Callable[[str], bytes] | None = None,
) -> VerifyResult:
    """Compare the stored virtual data for ``n_files`` random source files per variable
    against the NetCDF files themselves, byte for byte (NaN-equal)."""
    rng = np.random.default_rng(seed)
    stored = open_group(repo, result.group_path, decode_times=False)
    checked: list[tuple[str, str]] = []
    mismatches: list[str] = []
    for var in result.ds.data_vars:
        marr = result.ds[var].data
        if not hasattr(marr, "manifest"):
            continue
        paths = marr.manifest._paths
        time_chunk = int(marr.metadata.chunks[0])  # timesteps per chunk along axis 0
        # first time index of each distinct source file (chunk index * chunk length)
        first_index: dict[str, int] = {}
        for c in range(paths.shape[0]):
            p = str(paths[(c,) + (0,) * (paths.ndim - 1)])
            first_index.setdefault(p, c * time_chunk)
        files = list(first_index.items())
        picks = rng.choice(len(files), min(n_files, len(files)), replace=False)
        for p, t0 in (files[i] for i in picks):
            checked.append((var, p))
            try:
                src = _source_array(p, var, fetch)
                n = src.shape[0]
                got = stored[var].isel(time=slice(t0, t0 + n)).values
            except Exception as e:  # a changed or truncated source file surfaces as a decode error
                mismatches.append(f"{var} @ {p}: read failed: {type(e).__name__}: {e}")
                continue
            if got.shape != src.shape or not np.array_equal(got, src, equal_nan=True):
                diff = np.nanmax(np.abs(got - src)) if got.shape == src.shape else "n/a"
                mismatches.append(
                    f"{var} @ {p}: shape {got.shape} vs {src.shape}, max |diff| {diff}"
                )
    return VerifyResult(result.group_path, checked, mismatches)


__all__ = [
    "MANIFEST_PRELOAD_MAX_REFS",
    "MANIFEST_SPLIT_TIMESTEPS",
    "SOURCE_CONTAINER_PREFIX",
    "StoreTarget",
    "VerifyResult",
    "WriteResult",
    "container_credentials",
    "existing_groups",
    "load_sc_creds",
    "open_group",
    "open_repository",
    "prepare_for_write",
    "repository_config",
    "storage_for",
    "verify_group",
    "write_group",
]
