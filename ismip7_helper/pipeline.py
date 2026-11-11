"""Run the virtualization for a set of groups: index -> virtualize -> write -> verify.

Usage::

    # dry run: which groups would be built
    ismip7-virtualize --index index.parquet --region AIS --groups 'CESM2-WACCM/historical/*' --dry-run

    # local repo, one group, verify two files per variable afterwards
    ismip7-virtualize --index index.parquet --region AIS \\
        --groups CESM2-WACCM/historical/SDBN1/8000m --target local:/tmp/icechunk-ais --verify 2

    # production (credentials from sc_creds.json)
    ismip7-virtualize --index index.parquet --region GrIS --target s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-gris \\
        --write-creds sc_creds.json

Groups are processed one after another here; Phase C fans the same
``commit_group`` out over Lambda. The runner creates the repository and the
parent groups first (``store.ensure_hierarchy``). An existing group is
skipped only when its sources digest matches the index and, with
``--verify``, it passed verification; otherwise it is rewritten. A group
that fails verification is deleted again, so a re-run redoes it.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .conventions import ForcingFile, ForcingKind, parse_key
from .store import (
    SOURCES_ATTR,
    VERIFIED_ATTR,
    CredentialsExpiringError,
    StoreTarget,
    check_credentials_lifetime,
    check_target_region,
    delete_group,
    ensure_hierarchy,
    existing_groups,
    load_sc_creds,
    mark_verified,
    open_repository,
    sources_sha256,
    verify_group,
    write_group,
)
from .virtualize import GroupResult, concat_group, source_registry

logger = logging.getLogger(__name__)

#: Kinds of forcing file that become arrays in a store group.
GROUPABLE_KINDS = (ForcingKind.ANNUAL, ForcingKind.MULTIYEAR, ForcingKind.CLIMATOLOGY)


def load_groups(
    index_path: str | Path,
    *,
    region: str,
    patterns: list[str] | None = None,
) -> dict[str, list[ForcingFile]]:
    """Group the indexed forcing files of ``region`` by store group path, filtered by globs."""
    import pandas as pd

    df = pd.read_parquet(index_path)
    df = df[(df["record_type"] == "forcing") & (df["region"] == region) & df["ok"]]
    df = df[df["kind"].isin([k.value for k in GROUPABLE_KINDS])]
    groups: dict[str, list[ForcingFile]] = {}
    for key, size, gp in zip(df["key"], df["size"], df["group_path"], strict=True):
        if gp is None or pd.isna(gp):
            continue
        if patterns and not any(fnmatch.fnmatchcase(gp, p) for p in patterns):
            continue
        res = parse_key(key, None if pd.isna(size) else int(size))
        if res.forcing is not None:
            groups.setdefault(gp, []).append(res.forcing)
    return dict(sorted(groups.items()))


def group_digest(files: list[ForcingFile]) -> str:
    """The sources digest of an index group (matches the stored ``ismip7_sources_sha256``)."""
    return sources_sha256((f.key, f.size) for f in files)


@dataclass
class GroupRun:
    group_path: str
    #: written | verified | skipped | failed | dry-run, and in fan-out reports also
    #: deferred (too big for Lambda), in-flight (still running when the fan-out
    #: was interrupted) and interrupted (a local run stopped by Ctrl-C)
    status: str
    n_files: int
    seconds: float
    snapshot_id: str | None = None
    nrefs: int | None = None
    error: str | None = None
    verify_mismatches: list[str] | None = None
    #: the group's write commit landed (it may since have been deleted)
    committed: bool = False
    #: the group was deleted again after failing verification
    deleted: bool = False
    sources_sha256: str | None = None
    #: why an existing group was rewritten, or why it was skipped or deferred
    reason: str | None = None
    #: normalisation notes: how many, and the first few (all are logged at INFO)
    n_notes: int | None = None
    notes_head: list[str] | None = None
    #: the worker's ``os.cpu_count()`` and pool size (to calibrate group sizing)
    cpu_count: int | None = None
    n_workers: int | None = None
    #: seconds per phase: ``open`` (read the headers and concatenate), ``commit``
    #: (write, and verify when asked)
    timings: dict[str, float] | None = None


#: How many notes a ``GroupRun`` carries in ``notes_head``.
NOTES_HEAD = 5


def record_notes(run: GroupRun, notes: list[str]) -> None:
    """Fill ``n_notes``/``notes_head`` from a group's notes."""
    run.n_notes = len(notes)
    run.notes_head = notes[:NOTES_HEAD] or None


@dataclass
class Plan:
    """What a runner does with each selected group."""

    write: dict[str, tuple[list[ForcingFile], bool, str]]  # path -> (files, overwrite, reason)
    skip: dict[str, tuple[list[ForcingFile], str]]  # path -> (files, reason)


def plan_groups(
    groups: dict[str, list[ForcingFile]],
    existing: dict[str, dict[str, Any]],
    *,
    verify: int = 0,
    overwrite: bool = False,
    max_groups: int | None = None,
) -> Plan:
    """Decide per group: write new, rewrite an existing one, or skip it.

    An existing group is skipped only if its ``ismip7_sources_sha256`` equals
    the digest of the index files and, when verifying, it carries
    ``ismip7_verified`` (a group written without verification is re-done by
    a verifying run). Otherwise it is rewritten (``overwrite``), with the
    reason recorded. ``max_groups`` caps the groups to write, after skipping.
    """
    write: dict[str, tuple[list[ForcingFile], bool, str]] = {}
    skip: dict[str, tuple[list[ForcingFile], str]] = {}
    for gp, files in groups.items():
        attrs = existing.get(gp)
        if attrs is None:
            write[gp] = (files, overwrite, "new")
        elif overwrite:
            write[gp] = (files, True, "--overwrite")
        elif attrs.get(SOURCES_ATTR) != group_digest(files):
            write[gp] = (files, True, "sources changed" if attrs.get(SOURCES_ATTR) else "no digest")
        elif verify and not attrs.get(VERIFIED_ATTR):
            write[gp] = (files, True, "not verified")
        else:
            skip[gp] = (files, "verified" if attrs.get(VERIFIED_ATTR) else "exists")
    if max_groups:
        write = dict(list(write.items())[:max_groups])
    return Plan(write, skip)


def _verify_within(timeout_s: float | None, *args, **kwargs):
    """``verify_group`` in a daemon thread; ``None`` if it has not returned in ``timeout_s``."""
    if timeout_s is None:
        return verify_group(*args, **kwargs)
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ismip7-verify")
    try:
        future = pool.submit(verify_group, *args, **kwargs)
        try:
            return future.result(timeout=max(timeout_s, 0))
        except FutureTimeout:
            return None
    finally:
        pool.shutdown(wait=False)  # a stuck reader dies with the process


def commit_group(
    repo: Any,
    result: GroupResult,
    *,
    overwrite: bool = False,
    verify_files: int = 0,
    branch: str = "main",
    verify_timeout_s: float | None = None,
) -> GroupRun:
    """Write a concatenated group, then (with ``verify_files``) verify it.

    Verification reads the snapshot just written. On success a follow-up
    commit sets ``ismip7_verified``; on failure the group is deleted again,
    so the store never carries a group known to be bad. Exceptions from the
    write propagate; any after it are recorded on the returned run (with
    ``committed=True``). A verification still running after
    ``verify_timeout_s`` (a stalled read) is abandoned: the group stays
    written but unverified, and the run says so in ``error``.
    """
    wr = write_group(repo, result, overwrite=overwrite, branch=branch)
    run = GroupRun(
        result.group_path,
        "written",
        wr.n_files,
        0.0,
        wr.snapshot_id,
        wr.nrefs,
        committed=True,
        sources_sha256=wr.sources_sha256,
    )
    if not verify_files:
        return run
    try:
        vr = _verify_within(
            verify_timeout_s, repo, result, n_files=verify_files, snapshot_id=wr.snapshot_id
        )
        if vr is None:
            run.error = f"verification timed out after {verify_timeout_s:.0f} s"
            return run
        run.verify_mismatches = vr.mismatches
        if vr.ok:
            mark_verified(repo, result.group_path, wr.snapshot_id, branch=branch)
            run.status = "verified"
        else:
            run.status, run.error = "failed", "verification mismatches"
            delete_group(repo, result.group_path, branch=branch, reason="failed verification")
            run.deleted = True
    except Exception as e:
        logger.exception("%s: verification step failed", result.group_path)
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
    return run


def run_group(
    group_path: str,
    files: list[ForcingFile],
    repo: Any,
    *,
    registry: Any = None,
    overwrite: bool = False,
    verify_files: int = 0,
    workers: int = 1,
) -> GroupRun:
    """Concatenate, write and optionally verify one group; never raises.

    ``workers > 1`` opens the files in that many processes (each with its own
    source registry, so ``registry`` is then not used).
    """
    t0 = time.time()
    notes: list[str] = []
    timings: dict[str, float] = {}
    try:
        if workers > 1:
            result = concat_group(files, max_workers=workers, use_processes=True)
        else:
            result = concat_group(files, registry or source_registry())
        timings["open"] = round(time.time() - t0, 1)
        notes = result.notes
        for n in notes:
            logger.info("%s: %s", group_path, n)
        run = commit_group(repo, result, overwrite=overwrite, verify_files=verify_files)
        timings["commit"] = round(time.time() - t0 - timings["open"], 1)
    except Exception as e:  # one bad group must not abort the run or lose the report
        logger.error("%s failed: %s: %s", group_path, type(e).__name__, e)
        run = GroupRun(group_path, "failed", len(files), 0.0, error=f"{type(e).__name__}: {e}")
    record_notes(run, notes)
    run.cpu_count = os.cpu_count()
    run.n_workers = workers
    run.timings = timings or None
    run.seconds = round(time.time() - t0, 1)
    return run


def report_writer(path: str | Path | None) -> Callable[[dict[str, Any]], None]:
    """A callback that appends one run record and rewrites the JSON report at ``path``.

    The report is complete up to the last finished group even if the run dies.
    """
    records: list[dict[str, Any]] = []

    def add(rec: dict[str, Any]) -> None:
        records.append(rec)
        if path:
            tmp = Path(f"{path}.tmp")
            tmp.write_text(json.dumps(records, indent=1))
            tmp.replace(path)

    return add


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--index", required=True, help="Parquet index from ismip7-index")
    ap.add_argument("--region", required=True, choices=["AIS", "GrIS"])
    ap.add_argument(
        "--groups",
        nargs="*",
        default=None,
        help="glob(s) on group paths, e.g. 'CESM2-WACCM/*/SDBN1/8000m'",
    )
    ap.add_argument(
        "--target",
        help="local:/path or s3://bucket/prefix (default: production repo for the region)",
    )
    ap.add_argument("--write-creds", help="source.coop sc_creds.json for S3 targets")
    ap.add_argument("--overwrite", action="store_true", help="rewrite groups that already exist")
    ap.add_argument(
        "--verify",
        type=int,
        default=0,
        metavar="N",
        help="after writing, compare sampled steps of N source files per variable; "
        "existing groups not yet verified are then rewritten",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="open each group's files in N processes (e.g. $(nproc) on a big machine, for "
        "the groups the fan-out defers as too big for Lambda)",
    )
    ap.add_argument("--max-groups", type=int, default=None, help="write at most N groups")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", help="JSON run report, rewritten as each group finishes")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    groups = load_groups(args.index, region=args.region, patterns=args.groups)
    print(f"{len(groups)} groups, {sum(len(v) for v in groups.values())} files", file=sys.stderr)
    if args.dry_run:
        if args.max_groups:
            groups = dict(list(groups.items())[: args.max_groups])
        for gp, files in groups.items():
            print(f"{gp}\t{len(files)} files\t{sum(f.size or 0 for f in files) / 1e9:.1f} GB")
        return 0

    target = (
        StoreTarget.parse(args.target) if args.target else StoreTarget.region_default(args.region)
    )
    try:
        check_target_region(target, args.region)
    except ValueError as e:
        ap.error(str(e))
    if args.write_creds and not target.is_local:
        creds = load_sc_creds(args.write_creds)
        try:  # fail now, not at the first commit
            check_credentials_lifetime(
                creds, datetime.now(UTC) + timedelta(minutes=20), source=args.write_creds
            )
        except CredentialsExpiringError as e:
            ap.error(str(e))
        target = StoreTarget(url=target.url, region=target.region, credentials=creds)
    repo = open_repository(target, create=True)
    plan = plan_groups(
        groups,
        existing_groups(repo),
        verify=args.verify,
        overwrite=args.overwrite,
        max_groups=args.max_groups,
    )
    ensure_hierarchy(repo, list(plan.write), region=args.region)
    report = report_writer(args.report)
    runs: list[GroupRun] = []
    for gp, (files, reason) in plan.skip.items():
        run = GroupRun(gp, "skipped", len(files), 0.0, reason=reason)
        runs.append(run)
        report(asdict(run))
        print(f"skip {gp} ({reason})", file=sys.stderr)
    for gp, (files, overwrite, reason) in plan.write.items():
        run = run_group(
            gp, files, repo, overwrite=overwrite, verify_files=args.verify, workers=args.workers
        )
        if reason != "new":
            run.reason = reason
        runs.append(run)
        report(asdict(run))
        print(
            f"{run.status:8s} {gp} {run.n_files} files {run.seconds}s {run.error or ''}",
            file=sys.stderr,
        )
    failed = [r for r in runs if r.status == "failed"]
    print(f"{len(runs)} groups: {len(failed)} failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
