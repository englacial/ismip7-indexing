"""Phase C: fan a set of groups out over AWS Lambda (or in-process for tests).

Usage::

    # production: invoke the Lambda function once per group, 40 at a time
    ismip7-fanout --index index.parquet --region AIS --groups 'CESM2-WACCM/*' \\
        --target s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais \\
        --write-creds sc_creds.json --function ismip7-virtualize --concurrency 40 --verify 1

    # local: run the same handler in this process against a local repo, reading
    # the source files from a local mirror of the bucket keys
    ismip7-fanout --index index.parquet --region AIS --groups CESM2-WACCM/historical/SDBN1/8000m \\
        --target local:/tmp/icechunk-ais --local --report run.json

Each group is one event (see ``lambda_handler``). Before any worker runs,
the fan-out creates the repository if needed and every parent group of the
selected groups in one commit (``store.ensure_hierarchy``), so concurrent
workers only add leaf groups; this needs the write credentials here too
(``--write-creds``, required for an S3 target and checked to be valid for
at least ``FANOUT_CREDS_MINUTES`` more minutes). Results are collected into
a JSON report (a list of ``GroupRun`` dicts, default
``run-<region>-<UTC time>.json``), rewritten as each group finishes.
Existing groups are skipped only when they were built from the same files
(sources digest) and, with ``--verify``, passed verification; otherwise
they are rewritten. A failed group is re-run by running again.

Groups too big for one Lambda invocation (estimated ``n_files *
--seconds-per-file / --speedup`` over ``--max-lambda-seconds``) are not
invoked: they are recorded as ``deferred``, listed in ``--big-groups``, and
the command to run them with ``ismip7-virtualize --workers`` on a big
machine is printed.

Ctrl-C stops the fan-out from starting more groups; the groups already
invoked keep running on Lambda (up to 15 minutes) and are recorded as
``in-flight`` in the report. Wait for them before re-running.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import shlex
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .conventions import ForcingFile
from .pipeline import GroupRun, load_groups, plan_groups, report_writer
from .store import (
    CredentialsExpiringError,
    RepositoryNotFoundError,
    StoreTarget,
    check_credentials_lifetime,
    check_target_region,
    ensure_hierarchy,
    existing_groups,
    load_sc_creds,
    open_repository,
)

logger = logging.getLogger(__name__)

#: The fan-out's own write credentials must stay valid at least this long.
FANOUT_CREDS_MINUTES = 20
#: Defaults for the per-group time estimate (measured locally; re-measure on Lambda).
SECONDS_PER_FILE = 1.3
SPEEDUP = 3.9
MAX_LAMBDA_SECONDS = 700


def make_event(
    group_path: str,
    files: list[ForcingFile],
    *,
    target: str,
    region: str = "us-west-2",
    overwrite: bool = False,
    verify: int = 0,
    reason: str | None = None,
    source_root: str | None = None,
) -> dict[str, Any]:
    """The handler's event for one group (keys sorted, so a retry has the same digest)."""
    ev = {
        "group_path": group_path,
        "keys": [[f.key, f.size] for f in sorted(files, key=lambda f: f.key)],
        "target": target,
        "region": region,
        "overwrite": overwrite,
        "verify": verify,
    }
    if reason:
        ev["reason"] = reason
    if source_root:
        ev["source_root"] = str(source_root)
    return ev


def _record(ev: dict, status: str, *, error: str | None = None) -> dict[str, Any]:
    return asdict(
        GroupRun(ev.get("group_path", "?"), status, len(ev.get("keys") or []), 0.0, error=error)
    )


def _parse_response(event: dict, resp: dict) -> dict[str, Any]:
    """A ``GroupRun`` dict from a Lambda ``invoke`` response, or a failure record."""
    raw = resp["Payload"].read()
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    text = raw[:500].decode(errors="replace") if isinstance(raw, bytes) else str(raw)[:500]
    if resp.get("FunctionError"):
        if isinstance(payload, dict) and ("errorType" in payload or "errorMessage" in payload):
            detail = f"{payload.get('errorType')}: {payload.get('errorMessage')}"
        else:
            detail = text
        return _record(event, "failed", error=f"lambda {resp['FunctionError']}: {detail}"[:1000])
    if not isinstance(payload, dict) or "status" not in payload or "group_path" not in payload:
        return _record(event, "failed", error=f"unexpected lambda payload: {text}")
    return payload


def lambda_invoker(
    function_name: str,
    region: str = "us-west-2",
    *,
    concurrency: int = 10,
    client: Any = None,
) -> Callable[[dict], dict]:
    """Return a callable that invokes the Lambda synchronously and returns its result dict.

    The read timeout is longer than the 900 s function timeout plus cold
    start, TCP keepalive keeps NATs from dropping the idle connection, the
    pool has a connection per thread, and boto3 retries stay off (a retry
    would start a second concurrent invocation of the same group).
    ``client`` can be injected (tests).
    """
    if client is None:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "lambda",
            region_name=region,
            config=Config(
                read_timeout=960,
                connect_timeout=30,
                tcp_keepalive=True,
                max_pool_connections=max(1, concurrency),
                retries={"max_attempts": 0},
            ),
        )

    def invoke(event: dict) -> dict:
        resp = client.invoke(
            FunctionName=function_name, InvocationType="RequestResponse", Payload=json.dumps(event)
        )
        return _parse_response(event, resp)

    invoke.client = client  # type: ignore[attr-defined]
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
    """Invoke every event, ``concurrency`` at a time; return the records sorted by group.

    A transport-level exception becomes a ``failed`` record. ``on_result``
    runs on the calling thread. On ``KeyboardInterrupt`` no further event is
    started; the events still running are recorded (through ``on_result``)
    as ``in-flight`` (``interrupted`` when running serially in this
    process), and the ``KeyboardInterrupt`` is re-raised. The worker threads
    are daemons, so the process can exit without waiting for them.
    """
    events = list(events)
    results: list[dict] = []

    def record(r: dict) -> None:
        results.append(r)
        if on_result:
            on_result(r)

    def call(ev: dict) -> dict:
        try:
            return invoke(ev)
        except Exception as e:  # transport-level failure: record it, keep going
            return _record(ev, "failed", error=f"{type(e).__name__}: {e}")

    if concurrency <= 1:
        for ev in events:
            try:
                r = call(ev)
            except KeyboardInterrupt:
                record(_record(ev, "interrupted", error="interrupted by Ctrl-C"))
                raise
            record(r)
        return sorted(results, key=lambda r: r["group_path"])

    todo: queue.SimpleQueue = queue.SimpleQueue()
    for ev in events:
        todo.put(ev)
    done: queue.SimpleQueue = queue.SimpleQueue()
    lock = threading.Lock()
    stop = False
    running: dict[int, dict] = {}

    def work() -> None:
        while True:
            with lock:
                if stop:
                    return
                try:
                    ev = todo.get_nowait()
                except queue.Empty:
                    return
                running[id(ev)] = ev
            r = call(ev)
            with lock:
                running.pop(id(ev), None)
                done.put(r)

    threads = [
        threading.Thread(target=work, name=f"fanout-{i}", daemon=True)
        for i in range(min(concurrency, len(events)))
    ]
    try:
        for t in threads:  # inside the try: Ctrl-C can arrive while they start
            t.start()
        n_done = 0
        while n_done < len(events):
            try:
                r = done.get(timeout=0.2)
            except queue.Empty:
                continue
            n_done += 1
            record(r)
    except KeyboardInterrupt:
        with lock:
            stop = True
            in_flight = list(running.values())
            finished = []
            while True:
                try:
                    finished.append(done.get_nowait())
                except queue.Empty:
                    break
        for r in finished:
            record(r)
        for ev in in_flight:
            record(
                _record(
                    ev,
                    "in-flight",
                    error="fan-out interrupted while this group ran; it may still commit",
                )
            )
        raise
    return sorted(results, key=lambda r: r["group_path"])


def estimate_seconds(n_files: int, seconds_per_file: float, speedup: float) -> float:
    """Estimated time to open a group's files on one Lambda (the bulk of its run)."""
    return n_files * seconds_per_file / max(speedup, 1e-9)


def _virtualize_command(args: argparse.Namespace, target: str, groups: list[str]) -> str:
    cmd = [
        "ismip7-virtualize",
        "--index",
        args.index,
        "--region",
        args.region,
        "--target",
        target,
    ]
    if args.write_creds:
        cmd += ["--write-creds", args.write_creds]
    if args.verify:
        cmd += ["--verify", str(args.verify)]
    if args.overwrite:
        cmd.append("--overwrite")
    return shlex.join(cmd) + " --workers $(nproc) --groups " + shlex.join(groups)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--index", required=True)
    ap.add_argument("--region", required=True, choices=["AIS", "GrIS"])
    ap.add_argument("--groups", nargs="*", default=None, help="glob(s) on group paths")
    ap.add_argument("--target", help="local:/path or s3://bucket/prefix (default: production repo)")
    ap.add_argument(
        "--write-creds",
        help="sc_creds.json, required for S3 targets: the fan-out creates the repository and "
        "parent groups and lists existing groups (workers use their own configured secret)",
    )
    ap.add_argument("--function", help="Lambda function name (omit with --local)")
    ap.add_argument("--aws-region", default="us-west-2", help="region of the Lambda function")
    ap.add_argument(
        "--target-region", default="us-west-2", help="region of the target repository's bucket"
    )
    ap.add_argument("--local", action="store_true", help="run the handler in this process")
    ap.add_argument(
        "--source-root",
        help="with --local: read every key from file://DIR/<key> (a local mirror of the bucket)",
    )
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--verify", type=int, default=0, metavar="N")
    ap.add_argument(
        "--max-groups", type=int, help="write at most N groups (after skipping and deferring)"
    )
    ap.add_argument(
        "--seconds-per-file",
        type=float,
        default=SECONDS_PER_FILE,
        help="per-file open time on one vCPU, for the group estimate (measure it on Lambda)",
    )
    ap.add_argument(
        "--speedup",
        type=float,
        default=SPEEDUP,
        help="speedup of the Lambda's process pool over one vCPU",
    )
    ap.add_argument(
        "--max-lambda-seconds",
        type=float,
        default=MAX_LAMBDA_SECONDS,
        help="groups estimated longer than this are deferred to ismip7-virtualize --workers",
    )
    ap.add_argument("--big-groups", help="file to list the deferred groups in")
    ap.add_argument("--dry-run", action="store_true", help="print the events and exit")
    ap.add_argument("--report", help="JSON report path (default: run-<region>-<UTC time>.json)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    target = args.target or StoreTarget.region_default(args.region).url
    try:
        check_target_region(target, args.region)
    except ValueError as e:
        ap.error(str(e))
    if args.local and args.function:
        ap.error("--local and --function are exclusive")
    if not args.dry_run and not args.local and not args.function:
        ap.error("--function is required unless --local")
    if args.source_root and not args.local:
        ap.error("--source-root needs --local (Lambda workers cannot read this machine's files)")
    st = StoreTarget.parse(
        target,
        region=args.target_region,
        local_containers=(
            {f"file://{args.source_root.rstrip('/')}/": args.source_root.rstrip("/")}
            if args.source_root
            else {}
        ),
    )
    if st.is_local and not args.local and not args.dry_run:
        ap.error(f"{target} is on this machine; Lambda workers cannot write it (use --local)")
    if not st.is_local:
        if args.write_creds:
            creds = load_sc_creds(args.write_creds)
            try:
                check_credentials_lifetime(
                    creds,
                    datetime.now(UTC) + timedelta(minutes=FANOUT_CREDS_MINUTES),
                    source=args.write_creds,
                )
            except CredentialsExpiringError as e:
                ap.error(str(e))
            st = replace(st, credentials=creds)
        elif not args.dry_run:
            ap.error(
                f"{target}: --write-creds is required for an S3 target (without it the fan-out "
                "would write with this machine's AWS credentials, which cannot write to "
                "source.coop)"
            )

    groups = load_groups(args.index, region=args.region, patterns=args.groups)
    repo = None
    if args.dry_run:  # read-only: plan against the repository when it can be read
        existing: dict[str, dict[str, Any]] = {}
        if st.is_local or st.credentials:
            try:
                existing = existing_groups(open_repository(st, readonly=True))
            except RepositoryNotFoundError:
                pass
        else:
            print(
                "no --write-creds: existing groups not checked, every group counts as new",
                file=sys.stderr,
            )
    else:
        repo = open_repository(st, create=True)
        existing = existing_groups(repo)
    plan = plan_groups(groups, existing, verify=args.verify, overwrite=args.overwrite)
    if plan.skip:
        print(f"skipping {len(plan.skip)} existing groups", file=sys.stderr)
    skipped = [
        asdict(GroupRun(g, "skipped", len(f), 0.0, reason=reason))
        for g, (f, reason) in plan.skip.items()
    ]

    write = dict(plan.write)
    estimates = {
        g: estimate_seconds(len(f), args.seconds_per_file, args.speedup)
        for g, (f, _, _) in write.items()
    }
    deferred: dict[str, tuple[list[ForcingFile], float]] = {}
    if not args.local:  # the 900 s limit is Lambda's
        for g in list(write):
            if estimates[g] > args.max_lambda_seconds:
                deferred[g] = (write.pop(g)[0], estimates[g])
    if args.max_groups:
        write = dict(list(write.items())[: args.max_groups])
    deferred_records = [
        asdict(
            GroupRun(
                g,
                "deferred",
                len(f),
                0.0,
                reason=f"estimated {est:.0f} s > --max-lambda-seconds {args.max_lambda_seconds:g}",
            )
        )
        for g, (f, est) in deferred.items()
    ]

    events = [
        make_event(
            g,
            f,
            target=target,
            region=args.target_region,
            overwrite=ow,
            verify=args.verify,
            reason=None if reason == "new" else reason,
            source_root=args.source_root,
        )
        for g, (f, ow, reason) in write.items()
    ]
    print(f"{len(events)} groups, {sum(len(e['keys']) for e in events)} files", file=sys.stderr)

    if deferred:
        big = args.big_groups or (
            None if args.dry_run else f"big-groups-{args.region.lower()}-{stamp}.txt"
        )
        cmd = _virtualize_command(args, target, sorted(deferred))
        print(
            f"{len(deferred)} groups deferred as too big for one Lambda "
            f"(> {args.max_lambda_seconds:g} s estimated); run them on a big machine with:\n"
            f"  {cmd}",
            file=sys.stderr,
        )
        if big:
            lines = [f"# {cmd}"] + [
                f"{g}\t{len(f)} files\t~{est:.0f} s" for g, (f, est) in sorted(deferred.items())
            ]
            Path(big).write_text("\n".join(lines) + "\n")
            print(f"deferred groups listed in {big}", file=sys.stderr)

    if args.dry_run:
        for e in events:
            g = e["group_path"]
            print(f"{g}\t{len(e['keys'])} files\t~{estimates[g]:.0f} s")
        for g, (f, est) in deferred.items():
            print(f"{g}\t{len(f)} files\t~{est:.0f} s\tdeferred")
        return 0

    assert repo is not None
    ensure_hierarchy(repo, list(write), region=args.region)
    if args.local:
        invoke = local_invoker()
        concurrency = 1  # the handler already uses a process pool
    else:
        invoke = lambda_invoker(args.function, args.aws_region, concurrency=args.concurrency)
        concurrency = args.concurrency

    report = args.report or f"run-{args.region.lower()}-{stamp}.json"
    print(f"report: {report}", file=sys.stderr)
    write_report = report_writer(report)
    records: list[dict] = []

    def add(r: dict) -> None:
        records.append(r)
        write_report(r)

    for r in skipped + deferred_records:
        add(r)

    def on_result(r: dict) -> None:
        print(
            f"{r['status']:8s} {r['group_path']} {r.get('n_files')} files {r.get('seconds')}s "
            f"{r.get('error') or ''}",
            file=sys.stderr,
        )
        add(r)

    interrupted = False
    try:
        run_many(events, invoke, concurrency=concurrency, on_result=on_result)
    except KeyboardInterrupt:
        interrupted = True
    results = sorted(records, key=lambda r: r["group_path"])
    Path(report).write_text(json.dumps(results, indent=1))  # final, sorted
    failed = [r for r in results if r["status"] == "failed"]
    if interrupted:
        in_flight = [r["group_path"] for r in results if r["status"] == "in-flight"]
        print(
            f"interrupted: {len(in_flight)} groups were still running and may run for up to "
            "15 minutes more (recorded as in-flight); wait before re-running: "
            + ", ".join(in_flight),
            file=sys.stderr,
        )
        return 130
    print(f"{len(results)} groups: {len(failed)} failed, {len(deferred)} deferred", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
