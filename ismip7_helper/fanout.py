"""Phase C: fan a set of groups out over AWS Lambda (or in-process for tests).

Usage::

    # production: invoke the Lambda function once per group, 40 at a time
    ismip7-fanout --index index.parquet --region AIS --groups 'CESM2-WACCM/*' \\
        --target s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais \\
        --function ismip7-virtualize --concurrency 40 --verify 1 --report run-ais.json

    # local: run the same handler in this process against a local repo
    ismip7-fanout --index index.parquet --region AIS --groups CESM2-WACCM/historical/SDBN1-8000m \\
        --target local:/tmp/icechunk-ais --local --report run.json

Each group is one event (see ``lambda_handler``). Results are collected
into a JSON report (a list of ``GroupRun`` dicts). Groups that already exist
in the repository are skipped unless ``--overwrite``. A failed group can be
re-run by passing its path to ``--groups``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .conventions import ForcingFile
from .pipeline import load_groups
from .store import StoreTarget, existing_groups, load_sc_creds, open_repository

logger = logging.getLogger(__name__)


def make_event(
    group_path: str,
    files: list[ForcingFile],
    *,
    target: str,
    region: str = "us-west-2",
    overwrite: bool = False,
    verify: int = 0,
) -> dict[str, Any]:
    return {
        "group_path": group_path,
        "keys": [[f.key, f.size] for f in sorted(files, key=lambda f: f.key)],
        "target": target,
        "region": region,
        "overwrite": overwrite,
        "verify": verify,
    }


def lambda_invoker(function_name: str, region: str = "us-west-2") -> Callable[[dict], dict]:
    """Return a callable that invokes the Lambda synchronously and returns its result dict."""
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "lambda",
        region_name=region,
        config=Config(read_timeout=900, connect_timeout=30, retries={"max_attempts": 0}),
    )

    def invoke(event: dict) -> dict:
        resp = client.invoke(
            FunctionName=function_name, InvocationType="RequestResponse", Payload=json.dumps(event)
        )
        payload = json.loads(resp["Payload"].read())
        if resp.get("FunctionError"):
            return {
                "group_path": event["group_path"],
                "status": "failed",
                "n_files": len(event["keys"]),
                "seconds": 0.0,
                "error": f"lambda {resp['FunctionError']}: {json.dumps(payload)[:500]}",
            }
        return payload

    return invoke


def local_invoker() -> Callable[[dict], dict]:
    from .lambda_handler import run_event

    return run_event


def run_many(
    events: Iterable[dict],
    invoke: Callable[[dict], dict],
    *,
    concurrency: int = 1,
    on_result: Callable[[dict], None] | None = None,
) -> list[dict]:
    events = list(events)
    results: list[dict] = []
    if concurrency <= 1:
        for ev in events:
            r = invoke(ev)
            results.append(r)
            if on_result:
                on_result(r)
        return results
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(invoke, ev): ev for ev in events}
        for fut in as_completed(futures):
            ev = futures[fut]
            try:
                r = fut.result()
            except Exception as e:  # transport-level failure: record it, keep going
                r = {
                    "group_path": ev["group_path"],
                    "status": "failed",
                    "n_files": len(ev["keys"]),
                    "seconds": 0.0,
                    "error": f"{type(e).__name__}: {e}",
                }
            results.append(r)
            if on_result:
                on_result(r)
    return sorted(results, key=lambda r: r["group_path"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--index", required=True)
    ap.add_argument("--region", required=True, choices=["AIS", "GrIS"])
    ap.add_argument("--groups", nargs="*", default=None, help="glob(s) on group paths")
    ap.add_argument("--target", help="local:/path or s3://bucket/prefix (default: production repo)")
    ap.add_argument("--write-creds", help="sc_creds.json, used here only to list existing groups")
    ap.add_argument("--function", help="Lambda function name (omit with --local)")
    ap.add_argument("--aws-region", default="us-west-2")
    ap.add_argument("--local", action="store_true", help="run the handler in this process")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--verify", type=int, default=0, metavar="N")
    ap.add_argument("--max-groups", type=int)
    ap.add_argument("--dry-run", action="store_true", help="print the events and exit")
    ap.add_argument("--report", help="JSON report path")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    target = args.target or StoreTarget.region_default(args.region).url
    groups = load_groups(args.index, region=args.region, patterns=args.groups)
    if args.max_groups:
        groups = dict(list(groups.items())[: args.max_groups])

    if not args.dry_run and not args.overwrite:
        st = StoreTarget.parse(target)
        if args.write_creds and not st.is_local:
            st = StoreTarget(
                url=st.url, region=st.region, credentials=load_sc_creds(args.write_creds)
            )
        try:
            done = existing_groups(open_repository(st, readonly=True))
        except Exception as e:  # repository does not exist yet
            logger.info("no existing repository (%s)", e)
            done = set()
        skipped = [g for g in groups if g in done]
        groups = {g: f for g, f in groups.items() if g not in done}
        if skipped:
            print(f"skipping {len(skipped)} existing groups", file=sys.stderr)

    events = [
        make_event(
            g,
            f,
            target=target,
            region=args.aws_region,
            overwrite=args.overwrite,
            verify=args.verify,
        )
        for g, f in groups.items()
    ]
    print(f"{len(events)} groups, {sum(len(e['keys']) for e in events)} files", file=sys.stderr)
    if args.dry_run:
        for e in events:
            print(f"{e['group_path']}\t{len(e['keys'])} files")
        return 0
    if args.local:
        invoke = local_invoker()
        concurrency = 1  # the handler already uses a process pool
    else:
        if not args.function:
            ap.error("--function is required unless --local")
        invoke = lambda_invoker(args.function, args.aws_region)
        concurrency = args.concurrency

    def report_line(r: dict) -> None:
        print(
            f"{r['status']:8s} {r['group_path']} {r.get('n_files')} files {r.get('seconds')}s {r.get('error') or ''}",
            file=sys.stderr,
        )

    results = run_many(events, invoke, concurrency=concurrency, on_result=report_line)
    if args.report:
        Path(args.report).write_text(json.dumps(results, indent=1))
    failed = [r for r in results if r["status"] == "failed"]
    print(f"{len(results)} groups: {len(failed)} failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
