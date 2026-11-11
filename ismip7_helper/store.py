"""Phase B of the pipeline: write virtual groups into an Icechunk repository.

One repository per region. Data chunks stay in the source files on
source.coop and are reached through a *virtual chunk container* registered in
the repository config; coordinates and the ``crs`` scalar are stored inline.

The same code writes to a local directory (tests, dry runs) or to S3 (the
``englacial/ismip7`` prefix on source.coop), selected by :class:`StoreTarget`.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import xarray as xr

from . import SOURCE_BUCKET, SOURCE_REGION, TARGET_PREFIX
from .conventions.models import SUBMISSION_CALENDAR
from .virtualize import COORD_VARS, SOURCE_URL, GroupResult, VirtualizeError

logger = logging.getLogger(__name__)

#: Virtual chunk container prefix for the public source data. Readers pass
#: exactly this string (trailing slash included) as the key of
#: ``authorize_virtual_chunk_access``.
SOURCE_CONTAINER_PREFIX = f"{SOURCE_URL}/ismip/"
#: Manifest split size, in *chunks along the time dimension* (not timesteps:
#: an array stored with 4-step chunks gets 480 timesteps per manifest). Most
#: products have one timestep per chunk, so 120 chunks of a 2 km AIS array is
#: ~17k references per manifest; 8 km products get small manifests, which is
#: cheap. A browser client (icechunk-js) fetches one split per read.
MANIFEST_SPLIT_CHUNKS = 120

#: Group attribute: snapshot id of the write that passed verification.
VERIFIED_ATTR = "ismip7_verified"
#: Group attribute: :func:`sources_sha256` of the files the group was built from.
SOURCES_ATTR = "ismip7_sources_sha256"
#: Group attribute holding the normalisation notes (what was aligned, padded,
#: re-labelled, stored natively or spread), capped at NOTES_ATTR_MAX entries.
NOTES_ATTR = "ismip7_notes"
NOTES_ATTR_MAX = 2000
#: Root attribute: the region a repository holds.
REGION_ATTR = "ismip7_region"


class RepositoryNotFoundError(FileNotFoundError):
    """The target holds no repository (only the runners create one)."""


# --------------------------------------------------------------------------- target


@dataclass(frozen=True)
class StoreTarget:
    """Where a repository lives.

    ``local:/path/to/repo`` or ``s3://bucket/prefix``. For S3 the write
    credentials come from the environment, an explicit ``credentials``
    mapping (``access_key_id``/``secret_access_key``/``session_token`` and
    optionally ``expires_after``, as returned by :func:`load_sc_creds`), or a
    ``get_credentials`` callback returning ``icechunk.S3StaticCredentials``,
    which icechunk calls again when the previous credentials expire.
    Credentials never appear in the repr.
    """

    url: str
    region: str = SOURCE_REGION
    credentials: dict[str, Any] | None = field(default=None, hash=False, repr=False)
    #: ``url_prefix`` -> local root, for virtual chunks that live on local disk
    #: (tests only). Production repos reference the public bucket.
    local_containers: dict[str, str] = field(default_factory=dict, hash=False)
    #: S3-compatible endpoint to write through (e.g. ``https://data.source.coop``)
    #: and the region to sign for; taken from the credentials when they carry one.
    endpoint_url: str | None = None
    endpoint_region: str | None = None
    get_credentials: Callable[[], Any] | None = field(
        default=None, hash=False, repr=False, compare=False
    )

    @property
    def is_local(self) -> bool:
        return self.url.startswith("local:")

    @property
    def has_write_credentials(self) -> bool:
        return self.is_local or bool(self.credentials) or self.get_credentials is not None

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


def check_target_region(target: StoreTarget | str, region: str) -> None:
    """Refuse a target named ``icechunk-<other region>`` (``--region AIS`` into the GrIS repo)."""
    url = target.url if isinstance(target, StoreTarget) else target
    name = url.rstrip("/").rsplit("/", 1)[-1].lower()
    for reg in ("ais", "gris"):
        if name.startswith(f"icechunk-{reg}") and reg != region.lower():
            raise ValueError(f"target {url} is the {reg.upper()} repository, not {region}")


def parse_sc_creds(raw: str | dict[str, Any], *, source: str = "credentials") -> dict[str, Any]:
    """Parse source.coop / AWS-style temporary credentials into the keys icechunk wants.

    Accepts snake_case, ``AccessKeyId``-style and ``aws_*`` spellings, at the
    top level or nested under ``Credentials`` (the STS response shape). An
    ISO-8601 ``Expiration`` becomes ``expires_after`` (a timezone-aware datetime).
    """
    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    for nest in ("Credentials", "credentials"):
        if isinstance(data.get(nest), dict):
            data = data[nest]
            break
    pick = lambda *names: next((data[n] for n in names if data.get(n)), None)  # noqa: E731
    out: dict[str, Any] = {
        "access_key_id": pick("access_key_id", "AccessKeyId", "aws_access_key_id"),
        "secret_access_key": pick("secret_access_key", "SecretAccessKey", "aws_secret_access_key"),
        "session_token": pick("session_token", "SessionToken", "aws_session_token"),
    }
    if not out["access_key_id"] or not out["secret_access_key"]:
        raise ValueError(f"{source}: no access key / secret found; keys present: {sorted(data)}")
    # source.coop issues credentials for its own S3-compatible endpoint
    # (https://data.source.coop); they are not valid against AWS S3 directly.
    endpoint = pick("endpoint_url", "EndpointUrl", "endpoint")
    if endpoint:
        out["endpoint_url"] = str(endpoint).rstrip("/")
        region = pick("region_name", "region", "Region")
        if region:
            out["endpoint_region"] = str(region)
    exp = pick("expires_after", "Expiration", "expiration")
    if exp:
        try:
            when = exp if isinstance(exp, datetime) else datetime.fromisoformat(str(exp))
        except ValueError:
            logger.warning("%s: cannot parse Expiration %r; ignoring it", source, exp)
        else:
            out["expires_after"] = when if when.tzinfo else when.astimezone()
    return {k: v for k, v in out.items() if v}


def load_sc_creds(path: str | Path) -> dict[str, Any]:
    """Read a source.coop credentials file (``sc_creds.json``); see :func:`parse_sc_creds`."""
    return parse_sc_creds(Path(path).read_text(), source=str(path))


def static_credentials(creds: dict[str, Any]) -> Any:
    """``icechunk.S3StaticCredentials`` from a :func:`parse_sc_creds` mapping."""
    import icechunk

    return icechunk.S3StaticCredentials(
        access_key_id=creds["access_key_id"],
        secret_access_key=creds["secret_access_key"],
        session_token=creds.get("session_token"),
        expires_after=creds.get("expires_after"),
    )


class CredentialsExpiringError(RuntimeError):
    """Write credentials expire before the work that needs them can finish."""


def check_credentials_lifetime(
    creds: dict[str, Any], needed_until: datetime, *, source: str = "write credentials"
) -> None:
    """Refuse credentials whose ``expires_after`` is not later than ``needed_until``.

    Used by the Lambda handler (needed until the invocation's deadline) and
    the fan-out (needed for its own writes, before it invokes anything), so
    expiring credentials fail before the work rather than at the commit.
    Credentials without an expiry pass (they cannot be checked).
    """
    exp = creds.get("expires_after")
    if exp is None:
        return
    if exp.tzinfo is None:
        exp = exp.astimezone()
    if exp <= needed_until:
        now = datetime.now(needed_until.tzinfo)
        state = "expired" if exp <= now else "expire"
        raise CredentialsExpiringError(
            f"{source}: credentials {state} at {exp.isoformat(timespec='seconds')}, before "
            f"{needed_until.isoformat(timespec='seconds')} when they are still needed; "
            "fetch fresh source.coop credentials (and update the secret) and re-run"
        )


# --------------------------------------------------------------------------- repo


def repository_config(
    target: StoreTarget,
    *,
    split_chunks: int = MANIFEST_SPLIT_CHUNKS,
) -> Any:
    """Repository config: virtual chunk containers plus manifest splitting along time.

    Splitting is by ``split_chunks`` chunks along any dimension named ``time``.
    Manifest preloading is left at icechunk's default (it only applies to
    coordinate-like arrays, so raising it would not help the data arrays).
    """
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
        splitting=icechunk.ManifestSplittingConfig.from_dict(
            {
                icechunk.ManifestSplitCondition.AnyArray(): {
                    icechunk.ManifestSplitDimCondition.DimensionName("time"): split_chunks
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
    creds = dict(target.credentials or {})
    endpoint = target.endpoint_url or creds.pop("endpoint_url", None)
    endpoint_region = target.endpoint_region or creds.pop("endpoint_region", None)
    kw: dict[str, Any] = {"bucket": bucket, "prefix": prefix, "region": target.region}
    if endpoint:
        bucket, prefix = proxy_layout(bucket, prefix)
        kw.update(
            bucket=bucket,
            prefix=prefix,
            endpoint_url=endpoint,
            force_path_style=True,
            region=endpoint_region or target.region,
        )
    if target.get_credentials is not None:
        kw["get_credentials"] = target.get_credentials
    elif creds:
        kw.update(creds)
    else:
        kw["from_env"] = True
    return icechunk.s3_storage(**kw)


def proxy_layout(bucket: str, prefix: str) -> tuple[str, str]:
    """Map a public-bucket location to the source.coop proxy's layout.

    On ``https://data.source.coop`` the S3 bucket is the account
    (``englacial``) and the key is the rest (``ismip7/icechunk-ais/...``);
    objects written there appear anonymously in the public bucket under
    ``englacial/ismip7/...``, where readers expect them.
    """
    if bucket == SOURCE_BUCKET and "/" in prefix:
        account, rest = prefix.split("/", 1)
        return account, rest
    return bucket, prefix


def open_repository(target: StoreTarget, *, create: bool = False, readonly: bool = False) -> Any:
    """Open the repository at ``target``.

    With ``create`` make it (spec v2) if it does not exist; this is for the
    runners (``ismip7-virtualize``, ``ismip7-fanout``), which create it once
    before any worker starts. A concurrent creation is tolerated (the loser
    opens the winner's repository). Without ``create`` a missing repository
    raises :class:`RepositoryNotFoundError`.
    """
    import icechunk

    storage = storage_for(target)
    cfg = repository_config(target)
    creds = container_credentials(target)

    def _open() -> Any:
        return icechunk.Repository.open(storage, config=cfg, authorize_virtual_chunk_access=creds)

    if icechunk.Repository.exists(storage):
        return _open()
    if readonly or not create:
        raise RepositoryNotFoundError(
            f"no Icechunk repository at {target.url}; ismip7-virtualize or ismip7-fanout "
            "creates it (with its group hierarchy) before writing groups"
        )
    try:
        return icechunk.Repository.create(
            storage,
            config=cfg,
            authorize_virtual_chunk_access=creds,
            spec_version=icechunk.SpecVersion.v2,
        )
    except icechunk.IcechunkError as e:  # another process created it first
        logger.info("create of %s failed (%s); opening instead", target.url, e)
        if not icechunk.Repository.exists(storage):
            raise
        return _open()


def ancestors(group_path: str) -> list[str]:
    """Proper ancestor groups of ``group_path``, outermost first (the root ``""`` excluded)."""
    parts = group_path.strip("/").split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def ensure_hierarchy(
    repo: Any,
    group_paths: Iterable[str],
    branch: str = "main",
    *,
    region: str | None = None,
) -> str | None:
    """Create the root group and every ancestor group of ``group_paths`` in one commit.

    Two sessions that both create the same new node (the root of a fresh
    repository, or a shared ``<ESM>`` / ``<ESM>/<experiment>`` group) cannot
    be rebased onto each other, so the runners call this before any worker
    writes a group; workers then only add leaf groups under existing parents,
    which rebase cleanly. Idempotent: returns the new snapshot id, or ``None``
    when everything already exists. With ``region``, a new root records it as
    the ``ismip7_region`` attribute and an existing root with a different
    region is refused.
    """
    import zarr

    session = repo.writable_session(branch)
    try:
        root = zarr.open_group(session.store, mode="r")
    except (FileNotFoundError, zarr.errors.GroupNotFoundError):
        root = zarr.open_group(
            session.store, mode="w-", attributes={REGION_ATTR: region} if region else None
        )
    have = root.attrs.get(REGION_ATTR)
    if region and have and have != region:
        raise ValueError(f"repository holds {have} groups, not {region}")
    wanted = sorted({a for gp in group_paths for a in ancestors(gp)}, key=lambda p: p.count("/"))
    for path in wanted:
        root.require_group(path)
    if not session.has_uncommitted_changes:
        return None
    snap = session.commit(
        f"create hierarchy ({len(wanted)} parent groups)",
        metadata={"operation": "ensure_hierarchy", "groups": wanted, **provenance()},
    )
    logger.info("created hierarchy for %d groups as %s", len(wanted), snap)
    return snap


def tag_release(repo: Any, name: str, branch: str = "main") -> str:
    """Tag the current head of ``branch`` as ``name``; returns the tagged snapshot id."""
    snap = repo.lookup_branch(branch)
    repo.create_tag(name, snapshot_id=snap)
    return snap


# --------------------------------------------------------------------------- provenance


def sources_sha256(sources: Iterable[tuple[str, int | None]]) -> str:
    """SHA-256 over the sorted ``(key, size)`` pairs a group is built from.

    The index and the stored group attribute use the same digest, so a
    runner can tell a current group from one built from republished files.
    """
    pairs = sorted(
        ((str(k), None if s is None else int(s)) for k, s in sources),
        key=lambda p: (p[0], -1 if p[1] is None else p[1]),
    )
    blob = json.dumps(pairs, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


@functools.cache
def provenance() -> dict[str, str]:
    """Package version and, when known, the git commit of the code that writes."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        out = {"ismip7_helper_version": version("ismip7-helper")}
    except PackageNotFoundError:
        out = {"ismip7_helper_version": "unknown"}
    sha = os.environ.get("ISMIP7_GIT_SHA")
    if not sha:
        repo_dir = Path(__file__).resolve().parent.parent
        if (repo_dir / ".git").exists():
            try:
                sha = subprocess.run(
                    ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                sha = None
    if sha:
        out["ismip7_helper_git_sha"] = sha
    return out


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
    ``encoding`` if an older caller left them there. A ``time`` without units
    raises ``ValueError`` (relabelling it would silently change its meaning);
    a missing calendar defaults to the CF default. Idempotent; the input is
    not modified.
    """
    ds = ds.copy()
    if "time" in ds.variables:
        t = ds["time"]
        if "units" not in t.attrs:
            if "units" not in t.encoding:
                raise ValueError("time has no units (neither in attrs nor in encoding)")
            t.attrs["units"] = t.encoding["units"]
        if "calendar" not in t.attrs:
            t.attrs["calendar"] = t.encoding.get("calendar", SUBMISSION_CALENDAR)
        t.encoding.pop("units", None)
        t.encoding.pop("calendar", None)
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
    sources_sha256: str | None = None


def _is_ancestor_creation_conflict(err: Any, group_path: str) -> bool:
    """True if every conflict in a ``RebaseFailedError`` is a concurrent creation of an ancestor."""
    import icechunk

    conflicts = list(getattr(err, "conflicts", None) or [])
    if not conflicts:
        return False
    parents = {"/"} | {"/" + a for a in ancestors(group_path)}
    return all(
        c.conflict_type == icechunk.ConflictType.NewNodeConflictsWithExistingNode
        and c.path.rstrip("/") in {p.rstrip("/") for p in parents}
        for c in conflicts
    )


def _whole_second_after(t: datetime | None) -> datetime | None:
    """``t`` rounded up to a whole second.

    Icechunk keeps ``last_updated_at`` in whole seconds and refuses a chunk
    whose object was modified after it; a file written earlier in the same
    second (sub-second mtimes on local disk) would otherwise be refused.
    """
    if t is None:
        return None
    return t if t.microsecond == 0 else t.replace(microsecond=0) + timedelta(seconds=1)


def write_group(
    repo: Any,
    result: GroupResult,
    *,
    overwrite: bool = False,
    branch: str = "main",
    rebase_tries: int = 200,
    message: str | None = None,
    restage_tries: int = 8,
) -> WriteResult:
    """Write one group in its own commit, rebasing on concurrent commits to other groups.

    Call :func:`ensure_hierarchy` first so the parents exist. As a backstop,
    a rebase that fails only because another session created the same
    ancestor group is retried in a fresh session (up to ``restage_tries``),
    and so is a commit that lost every one of its ``rebase_tries`` races
    (a hundred Lambdas committing at once): after a random pause, so the
    herd thins out.

    Virtual references are stamped with ``result.opened_at`` (when the
    headers were read), so a source file republished after that is refused
    on read rather than read back wrong. The group attribute
    ``ismip7_sources_sha256`` and the commit metadata record the digest of
    the source ``(key, size)`` pairs; the commit metadata also records the
    operation, file version(s) and the writing code's version.
    """
    import icechunk

    gp = result.group_path
    ds = prepare_for_write(result.ds)
    digest = sources_sha256(result.sources) if result.sources else None
    if digest:
        ds.attrs[SOURCES_ATTR] = digest
    if result.notes:
        notes = list(result.notes[:NOTES_ATTR_MAX])
        if len(result.notes) > NOTES_ATTR_MAX:
            notes.append(f"... {len(result.notes) - NOTES_ATTR_MAX} more notes in the run report")
        ds.attrs[NOTES_ATTR] = notes
    operation = "overwrite" if overwrite else "write"
    msg = message or f"{'rewrite' if overwrite else 'write'} {gp} ({result.n_files} files)"
    meta = {
        "operation": operation,
        "group": gp,
        "n_source_files": result.n_files,
        "source_keys_prefix": ds.attrs.get("source_keys_prefix", ""),
        "ismip7_file_version": ds.attrs.get("ismip7_file_version"),
        "sources_sha256": digest,
        "headers_read_at": result.opened_at.isoformat() if result.opened_at else None,
        **provenance(),
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    mode = "w" if overwrite else "w-"
    stamp = _whole_second_after(result.opened_at)
    for attempt in range(restage_tries + 1):
        session = repo.writable_session(branch)
        ds.vz.to_icechunk(session.store, group=gp, mode=mode, last_updated_at=stamp)
        _write_native_tails(session, gp, result)
        try:
            snap = session.commit(
                msg,
                metadata=meta,
                rebase_with=icechunk.ConflictDetector(),
                rebase_tries=rebase_tries,
            )
            break
        except icechunk.RebaseFailedError as e:
            if attempt < restage_tries and _is_ancestor_creation_conflict(e, gp):
                logger.warning("%s: a parent group was created concurrently; re-staging", gp)
                continue
            raise
        except icechunk.ConflictError:
            if attempt >= restage_tries:
                raise
            _conflict_pause(gp, attempt)
    nrefs = int(result.ds.vz.nrefs())
    nbytes = int(result.ds.vz.nbytes)
    logger.info("committed %s as %s (%d refs)", gp, snap, nrefs)
    return WriteResult(gp, snap, result.n_files, nbytes, nrefs, digest)


def _write_native_tails(session: Any, group_path: str, result: GroupResult) -> None:
    """Copy each :class:`~.virtualize.NativeTail` into the staged group as native chunks.

    The source steps are read whole (h5py over ranged reads) and written
    through zarr, which re-chunks and encodes them with the array's own
    chunk grid and codecs; the first step is read back and compared. Only
    these steps are ever copied into the store.
    """
    import h5py
    import zarr

    for tail in result.native_tails:
        with open_source(tail.url) as fh, h5py.File(fh, "r") as h:
            data = h[tail.var][...]
        if data.shape[0] != tail.n:
            raise VirtualizeError(
                f"{group_path}/{tail.var}: {tail.key} has {data.shape[0]} steps, expected {tail.n}"
            )
        arr = zarr.open_array(session.store, path=f"{group_path}/{tail.var}", mode="r+")
        arr[tail.start : tail.start + tail.n] = data
        back = arr[tail.start]
        if not np.array_equal(back, data[0], equal_nan=True):
            raise VirtualizeError(f"{group_path}/{tail.var}: native steps read back differently")
        logger.info(
            "%s: %s: %d step(s) from %s stored natively from index %d",
            group_path,
            tail.var,
            tail.n,
            os.path.basename(tail.key),
            tail.start,
        )


def _conflict_pause(group_path: str, attempt: int) -> None:
    """Sleep a random 1-8 s (growing with ``attempt``) before re-staging a commit."""
    import random
    import time

    pause = random.uniform(1, 4) * min(attempt + 1, 2)
    logger.warning("%s: commit lost its rebase races; re-staging in %.1f s", group_path, pause)
    time.sleep(pause)


def mark_verified(
    repo: Any,
    group_path: str,
    snapshot_id: str,
    *,
    branch: str = "main",
    rebase_tries: int = 200,
    restage_tries: int = 8,
) -> str:
    """Set ``ismip7_verified = <snapshot_id>`` on the group in a small follow-up commit."""
    import icechunk
    import zarr

    for attempt in range(restage_tries + 1):
        session = repo.writable_session(branch)
        g = zarr.open_group(session.store, path=group_path, mode="r+")
        g.attrs[VERIFIED_ATTR] = snapshot_id
        try:
            return session.commit(
                f"verified {group_path}",
                metadata={
                    "operation": "mark_verified",
                    "group": group_path,
                    "verified_snapshot": snapshot_id,
                    **provenance(),
                },
                rebase_with=icechunk.ConflictDetector(),
                rebase_tries=rebase_tries,
            )
        except icechunk.ConflictError:
            if attempt >= restage_tries:
                raise
            _conflict_pause(group_path, attempt)
    raise AssertionError("unreachable")


def delete_group(
    repo: Any,
    group_path: str,
    *,
    branch: str = "main",
    reason: str = "",
    rebase_tries: int = 50,
) -> str:
    """Delete one group (and its arrays) in its own commit."""
    import icechunk
    import zarr

    parent, _, name = group_path.strip("/").rpartition("/")
    session = repo.writable_session(branch)
    g = zarr.open_group(session.store, path=parent, mode="r+") if parent else None
    if g is None:
        g = zarr.open_group(session.store, mode="r+")
    del g[name]
    return session.commit(
        f"delete {group_path}" + (f": {reason}" if reason else ""),
        metadata={"operation": "delete", "group": group_path, "reason": reason, **provenance()},
        rebase_with=icechunk.ConflictDetector(),
        rebase_tries=rebase_tries,
    )


def existing_groups(repo: Any, branch: str = "main") -> dict[str, dict[str, Any]]:
    """Group paths written by this pipeline, with their attributes.

    A group counts when its attributes carry ``ismip7_group`` (every group
    :func:`~.virtualize.concat_group` builds does); ancestors created by
    :func:`ensure_hierarchy` do not. Walks the group tree with ``list_dir``
    and reads one ``zarr.json`` per group, stopping at data groups, so no
    array and no manifest is ever touched (a full ``store.list()`` would
    enumerate every chunk key: millions, read from every manifest).
    A fresh repository (no root group yet) gives ``{}``.
    """
    from zarr.core.buffer import default_buffer_prototype
    from zarr.core.sync import sync

    store = repo.readonly_session(branch).store

    async def _collect() -> dict[str, dict[str, Any]]:
        import asyncio

        proto = default_buffer_prototype()
        found: dict[str, dict[str, Any]] = {}

        async def visit(path: str) -> None:
            buf = await store.get(f"{path}/zarr.json" if path else "zarr.json", prototype=proto)
            if buf is None:
                return
            meta = json.loads(buf.to_bytes())
            if meta.get("node_type") != "group":
                return
            attrs = meta.get("attributes") or {}
            if "ismip7_group" in attrs:
                found[path] = attrs  # a data group: its children are arrays
                return
            names = [n async for n in store.list_dir(path)]
            children = [n.rstrip("/") for n in names if n.rstrip("/") != "zarr.json"]
            await asyncio.gather(*(visit(f"{path}/{c}" if path else c) for c in children))

        await visit("")
        return found

    return sync(_collect())


# --------------------------------------------------------------------------- verify


def open_group(
    repo: Any,
    group_path: str,
    branch: str = "main",
    *,
    snapshot_id: str | None = None,
    **kw: Any,
) -> xr.Dataset:
    """Open one group as an xarray Dataset, at the head of ``branch`` or at ``snapshot_id``."""
    session = (
        repo.readonly_session(snapshot_id=snapshot_id)
        if snapshot_id
        else repo.readonly_session(branch)
    )
    return xr.open_zarr(session.store, group=group_path, consolidated=False, **kw)


class _RangedFile(io.RawIOBase):
    """Read-only, seekable file over a block-cached ranged reader.

    The reader returns bytes-like objects h5py does not accept directly; this
    adapts it through ``readinto``.
    """

    def __init__(self, reader: Any) -> None:
        self._r = reader

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._r.seek(offset, whence)

    def tell(self) -> int:
        return self._r.tell()

    def readinto(self, b: Any) -> int:
        mv = memoryview(b).cast("B")
        data = memoryview(bytes(self._r.read(len(mv))))
        mv[: len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            self._r.close()
        finally:
            super().close()


@functools.cache
def _source_store() -> Any:
    from obstore.store import S3Store

    return S3Store(SOURCE_BUCKET, region=SOURCE_REGION, skip_signature=True)


# How verification and native-chunk writes read a source file: in blocks of
# SOURCE_READ_BLOCK_BYTES cached up to SOURCE_READ_CACHE_BYTES. h5py walks the
# chunk index with many small, scattered reads and then reads whole chunks;
# a read-ahead buffer (``obstore.open_reader``) refetched its 1 MiB on every
# backward seek, which made one climatology step take about 250 s in Lambda.
SOURCE_READ_BLOCK_BYTES = 1 << 20
SOURCE_READ_CACHE_BYTES = 256 << 20


def open_source(url: str) -> BinaryIO:
    """Open a source file (``s3://`` on the public bucket, or ``file://``) for ranged reads."""
    from obspec_utils.readers import BlockStoreReader
    from obstore.store import LocalStore

    if url.startswith("file://"):
        path = url[len("file://") :]
        store, key = LocalStore(os.path.dirname(path)), os.path.basename(path)
    elif url.startswith(SOURCE_URL + "/"):
        store, key = _source_store(), url[len(SOURCE_URL) + 1 :]
    else:
        raise ValueError(f"not a source URL: {url}")
    reader = BlockStoreReader(
        store,
        key,
        block_size=SOURCE_READ_BLOCK_BYTES,
        max_cached_blocks=SOURCE_READ_CACHE_BYTES // SOURCE_READ_BLOCK_BYTES,
    )
    return _RangedFile(reader)  # type: ignore[return-value]


def _scalar(v: Any) -> Any:
    return None if v is None else np.asarray(v).ravel()[0]


def _source_steps(
    f: BinaryIO, variable: str, n_steps: int, rng: np.random.Generator
) -> tuple[int, list[int], list[np.ndarray]]:
    """Pick ``n_steps`` random steps along axis 0 of ``variable`` and read them decoded.

    Returns the axis length, the steps and the slabs. Decoded like xarray
    decodes the stored array: values equal to ``_FillValue`` (or the HDF5
    fill value when there is no such attribute) or ``missing_value`` become
    NaN, then ``scale_factor``/``add_offset`` apply.
    """
    import h5py

    with h5py.File(f, "r") as h:
        dset = h[variable]
        n = int(dset.shape[0])
        steps = sorted(int(k) for k in rng.choice(n, min(n_steps, n), replace=False))
        attrs = dset.attrs
        fills = [
            _scalar(attrs.get("_FillValue", dset.fillvalue)),
            _scalar(attrs.get("missing_value")),
        ]
        scale, offset = _scalar(attrs.get("scale_factor")), _scalar(attrs.get("add_offset"))
        out = []
        for k in steps:
            raw = np.asarray(dset[k])
            mask = np.zeros(raw.shape, bool)
            for fv in fills:
                if fv is not None:
                    mask |= np.isnan(raw) if np.isnan(fv) else raw == fv
            val = raw.astype(raw.dtype if raw.dtype.kind == "f" else np.float64)
            if scale is not None:
                val = val * scale
            if offset is not None:
                val = val + offset
            val[mask] = np.nan
            out.append(val)
    return n, steps, out


@dataclass
class VerifyResult:
    group_path: str
    checked: list[tuple[str, str]]  # (variable, source url)
    mismatches: list[str]

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _file_offsets(marr: Any) -> list[tuple[str, int]]:
    """``(source url, first index along axis 0)`` per source file of a virtual array.

    Each row of chunks along axis 0 lives in one file; the path is the first
    non-empty entry of the row (an all-empty row has no file).
    """
    manifest = marr.manifest
    grid = manifest.shape_chunk_grid
    chunk0 = int(marr.metadata.chunks[0])
    first: dict[str, int] = {}
    for c in range(grid[0]):
        for rest in np.ndindex(*grid[1:]):
            entry = manifest.get_entry((c, *rest))
            path = entry["path"] if entry else ""
            if path:
                first.setdefault(path, c * chunk0)
                break
    return list(first.items())


def verify_group(
    repo: Any,
    result: GroupResult,
    *,
    n_files: int = 2,
    n_steps: int = 1,
    seed: int | None = None,
    snapshot_id: str | None = None,
    branch: str = "main",
    opener: Callable[[str], BinaryIO] | None = None,
) -> VerifyResult:
    """Compare stored virtual data against the source files, NaN-equal.

    For ``n_files`` random source files per variable, ``n_steps`` random
    steps along axis 0 (``time``, or ``month`` for climatologies) are read
    from the source with ranged reads (h5py over obstore; whole objects are
    never downloaded) and compared with the same steps read through the
    repository at ``snapshot_id`` (the write being verified; the head of
    ``branch`` if not given). ``opener`` replaces :func:`open_source` in tests.
    """
    rng = np.random.default_rng(seed)
    gp = result.group_path
    checked: list[tuple[str, str]] = []
    mismatches: list[str] = []
    try:
        stored = open_group(repo, gp, branch, snapshot_id=snapshot_id, decode_times=False)
    except Exception as e:
        return VerifyResult(gp, checked, [f"{gp}: open failed: {type(e).__name__}: {e}"])
    for var in result.ds.data_vars:
        marr = result.ds[var].data
        if not hasattr(marr, "manifest"):
            continue
        files = _file_offsets(marr)
        if not files:
            mismatches.append(f"{var}: no source files in the manifest")
            continue
        picks = rng.choice(len(files), min(n_files, len(files)), replace=False)
        for url, t0 in (files[i] for i in picks):
            checked.append((var, url))
            started = time.monotonic()
            try:
                arr = stored[var]
                axis = arr.dims[0]
                with (opener or open_source)(url) as fh:
                    n_src, ks, src = _source_steps(fh, var, n_steps, rng)
                logger.info(
                    "%s: verify %s: source steps read in %.1f s",
                    gp,
                    var,
                    time.monotonic() - started,
                )
                if t0 + n_src > arr.sizes[axis]:
                    mismatches.append(
                        f"{var} @ {url}: {n_src} steps from {t0} exceed the stored "
                        f"{arr.sizes[axis]}"
                    )
                    continue
                for k, s in zip(ks, src, strict=True):
                    got = arr.isel({axis: t0 + k}).values
                    logger.info(
                        "%s: verify %s: step %d compared at %.1f s",
                        gp,
                        var,
                        k,
                        time.monotonic() - started,
                    )
                    if got.shape != s.shape or not np.array_equal(got, s, equal_nan=True):
                        diff = np.nanmax(np.abs(got - s)) if got.shape == s.shape else "n/a"
                        mismatches.append(
                            f"{var} @ {url} step {k}: shape {got.shape} vs {s.shape}, "
                            f"max |diff| {diff}"
                        )
            except Exception as e:  # a changed or truncated source file surfaces as a read error
                mismatches.append(f"{var} @ {url}: read failed: {type(e).__name__}: {e}")
    return VerifyResult(gp, checked, mismatches)


__all__ = [
    "MANIFEST_SPLIT_CHUNKS",
    "REGION_ATTR",
    "NOTES_ATTR",
    "SOURCES_ATTR",
    "SOURCE_CONTAINER_PREFIX",
    "VERIFIED_ATTR",
    "CredentialsExpiringError",
    "RepositoryNotFoundError",
    "StoreTarget",
    "VerifyResult",
    "WriteResult",
    "ancestors",
    "check_credentials_lifetime",
    "check_target_region",
    "container_credentials",
    "delete_group",
    "ensure_hierarchy",
    "existing_groups",
    "load_sc_creds",
    "mark_verified",
    "open_group",
    "open_repository",
    "open_source",
    "parse_sc_creds",
    "prepare_for_write",
    "provenance",
    "repository_config",
    "sources_sha256",
    "static_credentials",
    "storage_for",
    "tag_release",
    "verify_group",
    "write_group",
]
