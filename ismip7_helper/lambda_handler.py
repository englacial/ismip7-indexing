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

The repository and the group's parents must already exist: the fan-out
creates them (``store.ensure_hierarchy``) before invoking any worker, because
two workers that both create the same new node cannot rebase onto each other.
The handler only opens the repository and fails fast if it is missing.

Credentials for the target repository: the execution role does not grant
source.coop write access, so set ``ISMIP7_WRITE_CREDS_SECRET`` to a Secrets
Manager secret holding the ``sc_creds.json`` content (re-read whenever the
credentials expire, so rotating the secret keeps a long run going), or
``ISMIP7_WRITE_CREDS_JSON`` to the JSON itself (static). An S3 target with
neither set fails before any work. Reads of the public source bucket are
anonymous.

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
from .store import StoreTarget, open_repository
from .virtualize import VirtualizeError, concat_group, source_registry

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("ISMIP7_LOG_LEVEL", "INFO"))


class SecretCredentials:
    """Refreshable write credentials: re-read a Secrets Manager secret when they expire.

    Passed to ``icechunk.s3_storage(get_credentials=...)``; icechunk calls it
    for the first request and again after ``expires_after`` (the secret's
    ``Expiration``). A picklable object rather than a closure.
    """

    def __init__(self, secret_id: str) -> None:
        self.secret_id = secret_id

    def __repr__(self) -> str:
        return f"SecretCredentials({self.secret_id!r})"

    def __call__(self) -> Any:
        import boto3

        from .store import parse_sc_creds, static_credentials

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=self.secret_id)
        return static_credentials(
            parse_sc_creds(raw["SecretString"], source=f"secret {self.secret_id}")
        )


def _write_credentials() -> dict[str, Any]:
    """``StoreTarget`` keyword arguments for the configured write credentials (may be empty)."""
    from .store import parse_sc_creds

    raw = os.environ.get("ISMIP7_WRITE_CREDS_JSON")
    secret = os.environ.get("ISMIP7_WRITE_CREDS_SECRET")
    if raw:
        return {"credentials": parse_sc_creds(raw, source="ISMIP7_WRITE_CREDS_JSON")}
    if secret:
        return {"get_credentials": SecretCredentials(secret)}
    return {}


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


def target_from_event(event: dict[str, Any]) -> StoreTarget:
    """The event's target with the configured write credentials; S3 without any is refused."""
    target = StoreTarget.parse(
        event["target"],
        region=event.get("region", "us-west-2"),
        local_containers=event.get("local_containers", {}),
        **_write_credentials(),
    )
    if not target.has_write_credentials:
        raise RuntimeError(
            f"no write credentials for {target.url}: set ISMIP7_WRITE_CREDS_SECRET or "
            "ISMIP7_WRITE_CREDS_JSON (the execution role cannot write to source.coop)"
        )
    return target


def run_event(event: dict[str, Any], *, repo: Any = None, registry: Any = None) -> dict[str, Any]:
    """Process one event; ``repo``/``registry`` can be injected for tests."""
    import time

    from .pipeline import GroupRun, commit_group

    t0 = time.time()
    group_path = event["group_path"]
    try:
        files = _files_from_event(event)
        if {f.group_path for f in files} != {group_path}:
            raise VirtualizeError(f"keys do not all belong to {group_path}")
        if repo is None:
            repo = open_repository(target_from_event(event))  # never creates
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
        run = commit_group(
            repo,
            result,
            overwrite=bool(event.get("overwrite", False)),
            verify_files=int(event.get("verify", 0)),
        )
    except Exception as e:  # the fan-out must get a record back, not a stack trace
        logger.exception("%s failed", group_path)
        run = GroupRun(
            group_path, "failed", len(event.get("keys", [])), 0.0, error=f"{type(e).__name__}: {e}"
        )
    run.reason = event.get("reason")
    run.seconds = round(time.time() - t0, 1)
    return asdict(run)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """AWS Lambda handler."""
    if isinstance(event, str):
        event = json.loads(event)
    return run_event(event)


__all__ = ["SecretCredentials", "handler", "run_event", "target_from_event", "workers"]
