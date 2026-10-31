"""Run the virtualization for a set of groups: index -> virtualize -> write -> verify.

Usage::

    # dry run: which groups would be built
    ismip7-virtualize --index index.parquet --region AIS --groups 'CESM2-WACCM/historical/*' --dry-run

    # local repo, one group, verify two files per variable afterwards
    ismip7-virtualize --index index.parquet --region AIS \\
        --groups CESM2-WACCM/historical/SDBN1-8000m --target local:/tmp/icechunk-ais --verify 2

    # production (credentials from sc_creds.json)
    ismip7-virtualize --index index.parquet --region GrIS --target s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-gris \\
        --write-creds sc_creds.json

Groups are processed one after another here; Phase C fans the same
``run_group`` out over Lambda.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .conventions import ForcingFile, ForcingKind, parse_key
from .store import (
    StoreTarget,
    existing_groups,
    load_sc_creds,
    open_repository,
    verify_group,
    write_group,
)
from .virtualize import VirtualizeError, concat_group, source_registry

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
        if gp is None:
            continue
        if patterns and not any(fnmatch.fnmatchcase(gp, p) for p in patterns):
            continue
        res = parse_key(key, None if size is None or size != size else int(size))
        if res.forcing is not None:
            groups.setdefault(gp, []).append(res.forcing)
    return dict(sorted(groups.items()))


@dataclass
class GroupRun:
    group_path: str
    status: str  # written | skipped | failed | verified | dry-run
    n_files: int
    seconds: float
    snapshot_id: str | None = None
    nrefs: int | None = None
    error: str | None = None
    verify_mismatches: list[str] | None = None


def run_group(
    group_path: str,
    files: list[ForcingFile],
    repo: Any,
    *,
    registry: Any = None,
    overwrite: bool = False,
    verify_files: int = 0,
) -> GroupRun:
    t0 = time.time()
    try:
        result = concat_group(files, registry or source_registry())
        for n in result.notes:
            logger.info("%s: %s", group_path, n)
        wr = write_group(repo, result, overwrite=overwrite)
        run = GroupRun(group_path, "written", wr.n_files, 0.0, wr.snapshot_id, wr.nrefs)
        if verify_files:
            vr = verify_group(repo, result, n_files=verify_files)
            run.verify_mismatches = vr.mismatches
            run.status = "verified" if vr.ok else "failed"
            if not vr.ok:
                run.error = "verification mismatches"
    except (VirtualizeError, ValueError) as e:
        logger.error("%s failed: %s", group_path, e)
        run = GroupRun(group_path, "failed", len(files), 0.0, error=str(e))
    run.seconds = round(time.time() - t0, 1)
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--index", required=True, help="Parquet index from ismip7-index")
    ap.add_argument("--region", required=True, choices=["AIS", "GrIS"])
    ap.add_argument(
        "--groups",
        nargs="*",
        default=None,
        help="glob(s) on group paths, e.g. 'CESM2-WACCM/*/SDBN1-8000m'",
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
        help="compare N source files per variable after writing",
    )
    ap.add_argument("--max-groups", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", help="write a JSON run report here")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    groups = load_groups(args.index, region=args.region, patterns=args.groups)
    if args.max_groups:
        groups = dict(list(groups.items())[: args.max_groups])
    print(f"{len(groups)} groups, {sum(len(v) for v in groups.values())} files", file=sys.stderr)
    if args.dry_run:
        for gp, files in groups.items():
            print(f"{gp}\t{len(files)} files\t{sum(f.size or 0 for f in files) / 1e9:.1f} GB")
        return 0

    target = (
        StoreTarget.parse(args.target) if args.target else StoreTarget.region_default(args.region)
    )
    if args.write_creds and not target.is_local:
        target = StoreTarget(
            url=target.url, region=target.region, credentials=load_sc_creds(args.write_creds)
        )
    repo = open_repository(target, create=True)
    done = existing_groups(repo)
    runs: list[GroupRun] = []
    for gp, files in groups.items():
        if gp in done and not args.overwrite:
            runs.append(GroupRun(gp, "skipped", len(files), 0.0))
            print(f"skip {gp} (exists)", file=sys.stderr)
            continue
        run = run_group(gp, files, repo, overwrite=args.overwrite, verify_files=args.verify)
        runs.append(run)
        print(
            f"{run.status:8s} {gp} {run.n_files} files {run.seconds}s {run.error or ''}",
            file=sys.stderr,
        )
    if args.report:
        Path(args.report).write_text(json.dumps([asdict(r) for r in runs], indent=1))
    failed = [r for r in runs if r.status == "failed"]
    print(f"{len(runs)} groups: {len(failed)} failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
