"""Phase C: AWS Lambda entry point that virtualizes and writes one group.

One invocation = one store group (all files of one ``<source>/<experiment>/<product>``).
The handler opens the files in a process pool sized to the Lambda's vCPUs,
writes the group in its own Icechunk commit (rebasing on commits from other
workers) and optionally verifies it. It returns the ``GroupRun`` record as a
dict, which the fan-out collects into a run report.

Event::

    {
      "group_path": "CESM2-WACCM/historical/SDBN1-8000m",
      "keys": [["ismip/ismip7-ais-forcing/data/.../acabf_..._1850.nc", 2095405], ...],
      "target": "s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais",
      "region": "us-west-2",
      "overwrite": false,
      "verify": 1
    }

Credentials for the target repository come from the function's environment
(``AWS_ACCESS_KEY_ID`` etc. of the execution role do not grant source.coop
write access; set ``ISMIP7_WRITE_CREDS_SECRET`` to a Secrets Manager secret
holding the ``sc_creds.json`` content, or ``ISMIP7_WRITE_CREDS_JSON`` to the
JSON itself). Reads of the public source bucket are anonymous.

Locally the same handler runs in-process (see ``fanout.py --local``) with a
``local:`` target.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Any

from .conventions import ForcingFile, parse_key
from .store import StoreTarget, open_repository, verify_group, write_group
from .virtualize import VirtualizeError, concat_group, source_registry

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("ISMIP7_LOG_LEVEL", "INFO"))


def _write_credentials() -> dict[str, str] | None:
    raw = os.environ.get("ISMIP7_WRITE_CREDS_JSON")
    secret = os.environ.get("ISMIP7_WRITE_CREDS_SECRET")
    if not raw and secret:
        import boto3

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret)["SecretString"]
    if not raw:
        return None
    import tempfile

    from .store import load_sc_creds

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(raw)
        path = f.name
    try:
        return load_sc_creds(path)
    finally:
        os.unlink(path)


def _files_from_event(event: dict[str, Any]) -> list[ForcingFile]:
    files: list[ForcingFile] = []
    bad: list[str] = []
    for item in event["keys"]:
        key, size = (item[0], item[1]) if isinstance(item, list | tuple) else (item, None)
        res = parse_key(key, size)
        if res.forcing is None or not res.ok:
            bad.append(key)
        else:
            files.append(res.forcing)
    if bad:
        raise VirtualizeError(f"{len(bad)} keys do not parse as forcing files, e.g. {bad[0]}")
    return files


def workers() -> int:
    """Process-pool size: one per vCPU (Lambda scales vCPUs with memory; 10 GB = 6)."""
    env = os.environ.get("ISMIP7_WORKERS")
    return int(env) if env else max(1, os.cpu_count() or 1)


def run_event(event: dict[str, Any], *, repo: Any = None, registry: Any = None) -> dict[str, Any]:
    """Process one event; ``repo``/``registry`` can be injected for tests."""
    import time

    from .pipeline import GroupRun

    t0 = time.time()
    group_path = event["group_path"]
    try:
        files = _files_from_event(event)
        if {f.group_path for f in files} != {group_path}:
            raise VirtualizeError(f"keys do not all belong to {group_path}")
        if repo is None:
            target = StoreTarget.parse(
                event["target"],
                region=event.get("region", "us-west-2"),
                credentials=_write_credentials(),
                local_containers=event.get("local_containers", {}),
            )
            repo = open_repository(target, create=True)
        n = workers()
        urls = event.get("urls") or {}
        if registry is None:
            from .virtualize import local_registry

            registry = (
                local_registry()
                if urls and all(u.startswith("file://") for u in urls.values())
                else source_registry()
            )
        result = concat_group(
            files, registry, urls=urls or None, max_workers=n, use_processes=n > 1
        )
        for note in result.notes:
            logger.info("%s: %s", group_path, note)
        wr = write_group(repo, result, overwrite=bool(event.get("overwrite", False)))
        run = GroupRun(group_path, "written", wr.n_files, 0.0, wr.snapshot_id, wr.nrefs)
        verify = int(event.get("verify", 0))
        if verify:
            vr = verify_group(repo, result, n_files=verify)
            run.verify_mismatches = vr.mismatches
            run.status = "verified" if vr.ok else "failed"
            if not vr.ok:
                run.error = "verification mismatches"
    except Exception as e:  # the fan-out must get a record back, not a stack trace
        logger.exception("%s failed", group_path)
        run = GroupRun(
            group_path, "failed", len(event.get("keys", [])), 0.0, error=f"{type(e).__name__}: {e}"
        )
    run.seconds = round(time.time() - t0, 1)
    return asdict(run)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """AWS Lambda handler."""
    if isinstance(event, str):
        event = json.loads(event)
    return run_event(event)


__all__ = ["handler", "run_event", "workers"]
