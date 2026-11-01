"""Phase C: AWS Lambda entry point that virtualizes and writes one group.

One invocation = one store group (all files of one ``<source>/<experiment>/<product>``).
The handler opens the files in worker processes, one per vCPU (plain
``Process`` + ``Pipe``: Lambda has no ``/dev/shm``, so ``ProcessPoolExecutor``
cannot run there), writes the group in its own Icechunk commit (rebasing on
commits from other workers) and optionally verifies it. It returns the
``GroupRun`` record as a dict, which the fan-out collects into a run report.

Event::

    {
      "group_path": "CESM2-WACCM/historical/SDBN1-8000m",
      "keys": [["ismip/ismip7-ais-forcing/data/.../acabf_..._1850.nc", 2095405], ...],
      "target": "s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais",
      "region": "us-west-2",
      "overwrite": false,
      "verify": 1
    }

For local runs (``ismip7-fanout --local --source-root DIR``) the event may
also carry ``"source_root": DIR``: every key is then read from
``file://DIR/<key>`` and the repository gets a virtual chunk container for
that directory. (Tests may also pass explicit ``urls`` and
``local_containers`` mappings.)

The repository and the group's parents must already exist: the fan-out
creates them (``store.ensure_hierarchy``) before invoking any worker, because
two workers that both create the same new node cannot rebase onto each other.
The handler only opens the repository and fails fast if it is missing.

Deadline: given the Lambda ``context``, the handler refuses write
credentials that expire before the invocation's deadline, and after opening
the files it does not start the write when fewer than
``DEADLINE_MARGIN_S`` seconds remain; it returns ``status: failed, error:
"deadline"`` with the timings instead of being killed mid-commit.

Credentials for the target repository: the execution role does not grant
source.coop write access, so set exactly one of ``ISMIP7_WRITE_CREDS_SECRET``
(a Secrets Manager secret holding the ``sc_creds.json`` content; icechunk
re-reads it when the credentials' ``Expiration`` passes, so rotating the
secret keeps a long run going; without an ``Expiration`` it is never
re-read) or ``ISMIP7_WRITE_CREDS_JSON`` (the JSON itself, static). An S3
target with neither set, or with both, fails before any work. Reads of the
public source bucket are anonymous.

Logging: the Lambda runtime installs a root handler before importing this
module, so ``handler()`` sets the root level from ``ISMIP7_LOG_LEVEL``
(default INFO) on every call; importing the module changes no logging.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from .conventions import ForcingFile, parse_key
from .store import StoreTarget, check_credentials_lifetime, open_repository
from .virtualize import VirtualizeError, concat_group

logger = logging.getLogger(__name__)

#: After opening the files, the write is not started with less time left than this.
DEADLINE_MARGIN_S = 90


class SecretCredentials:
    """Refreshable write credentials: re-read a Secrets Manager secret when they expire.

    Passed to ``icechunk.s3_storage(get_credentials=...)``; icechunk calls it
    for the first request and again after ``expires_after`` (the secret's
    ``Expiration``; a secret without one is never re-read). A picklable
    object rather than a closure.
    """

    def __init__(self, secret_id: str) -> None:
        self.secret_id = secret_id

    def __repr__(self) -> str:
        return f"SecretCredentials({self.secret_id!r})"

    def fetch(self) -> dict[str, Any]:
        """The secret's credentials as a :func:`~.store.parse_sc_creds` mapping."""
        import boto3

        from .store import parse_sc_creds

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=self.secret_id)
        return parse_sc_creds(raw["SecretString"], source=f"secret {self.secret_id}")

    def __call__(self) -> Any:
        from .store import static_credentials

        return static_credentials(self.fetch())


def _write_credentials(needed_until: datetime | None = None) -> dict[str, Any]:
    """``StoreTarget`` keyword arguments for the configured write credentials (may be empty).

    The credentials are read once here and refused (``CredentialsExpiringError``)
    when they expire before ``needed_until`` (default: now).
    """
    from .store import parse_sc_creds

    raw = os.environ.get("ISMIP7_WRITE_CREDS_JSON")
    secret = os.environ.get("ISMIP7_WRITE_CREDS_SECRET")
    if raw and secret:
        raise RuntimeError(
            "both ISMIP7_WRITE_CREDS_JSON and ISMIP7_WRITE_CREDS_SECRET are set; set only one"
        )
    until = needed_until or datetime.now(UTC)
    if raw:
        creds = parse_sc_creds(raw, source="ISMIP7_WRITE_CREDS_JSON")
        check_credentials_lifetime(creds, until, source="ISMIP7_WRITE_CREDS_JSON")
        return {"credentials": creds}
    if secret:
        getter = SecretCredentials(secret)
        creds = getter.fetch()
        check_credentials_lifetime(creds, until, source=f"secret {secret}")
        if "expires_after" not in creds:
            logger.warning(
                "secret %s has no Expiration: its credentials are never refreshed", secret
            )
        return {"get_credentials": getter}
    return {}


def _files_from_event(event: dict[str, Any]) -> list[ForcingFile]:
    if not event.get("keys"):
        raise VirtualizeError("event has no keys")
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


def _source_root(event: dict[str, Any]) -> str | None:
    root = event.get("source_root")
    return str(root).rstrip("/") if root else None


def _urls(event: dict[str, Any]) -> dict[str, str]:
    """Where to read each key: ``source_root`` and/or explicit ``urls`` (default: the bucket)."""
    root = _source_root(event)
    urls: dict[str, str] = {}
    if root:
        for item in event["keys"]:
            key = item[0] if isinstance(item, list | tuple) else item
            urls[key] = f"file://{root}/{key}"
    urls.update(event.get("urls") or {})
    return urls


def _local_containers(event: dict[str, Any]) -> dict[str, str]:
    root = _source_root(event)
    out = {f"file://{root}/": root} if root else {}
    out.update(event.get("local_containers") or {})
    return out


def workers() -> int:
    """Pool size: one process per vCPU (Lambda scales vCPUs with memory; 10 GB = 6)."""
    env = os.environ.get("ISMIP7_WORKERS")
    return int(env) if env else max(1, os.cpu_count() or 1)


def target_from_event(
    event: dict[str, Any], *, needed_until: datetime | None = None
) -> StoreTarget:
    """The event's target with the configured write credentials.

    An S3 target without credentials, or with credentials that expire before
    ``needed_until`` (default: now), is refused.
    """
    target = StoreTarget.parse(
        event["target"],
        region=event.get("region", "us-west-2"),
        local_containers=_local_containers(event),
        **({} if event["target"].startswith("local:") else _write_credentials(needed_until)),
    )
    if not target.has_write_credentials:
        raise RuntimeError(
            f"no write credentials for {target.url}: set ISMIP7_WRITE_CREDS_SECRET or "
            "ISMIP7_WRITE_CREDS_JSON (the execution role cannot write to source.coop)"
        )
    return target


def _remaining_s(context: Any) -> float | None:
    if context is None:
        return None
    return context.get_remaining_time_in_millis() / 1000


def run_event(
    event: dict[str, Any], *, repo: Any = None, registry: Any = None, context: Any = None
) -> dict[str, Any]:
    """Process one event and return its ``GroupRun`` dict; never raises.

    ``repo``/``registry`` can be injected for tests (an injected registry
    opens the files in threads, since it cannot cross into processes).
    ``context`` is the Lambda context, for the deadline checks.
    """
    from .pipeline import GroupRun, commit_group, record_notes

    t0 = time.time()
    group_path = event.get("group_path", "?") if isinstance(event, dict) else "?"
    n = workers()
    notes: list[str] = []
    timings: dict[str, float] = {}
    n_keys = len(event.get("keys") or []) if isinstance(event, dict) else 0
    try:
        if not isinstance(event, dict):
            raise ValueError(f"event must be a JSON object, got {type(event).__name__}")
        if "group_path" not in event:
            raise ValueError(f"event has no group_path (keys: {sorted(event)})")
        files = _files_from_event(event)
        if {f.group_path for f in files} != {group_path}:
            raise VirtualizeError(f"keys do not all belong to {group_path}")
        if repo is None:
            left = _remaining_s(context)
            until = datetime.now(UTC) + timedelta(seconds=left or 0)
            repo = open_repository(target_from_event(event, needed_until=until))  # never creates
        urls = _urls(event)
        result = concat_group(
            files,
            registry,
            urls=urls or None,
            max_workers=n,
            use_processes=n > 1 and registry is None,
        )
        timings["open"] = round(time.time() - t0, 1)
        notes = result.notes
        for note in notes:
            logger.info("%s: %s", group_path, note)
        left = _remaining_s(context)
        if left is not None:
            timings["remaining_after_open"] = round(left, 1)
        if left is not None and left < DEADLINE_MARGIN_S:
            logger.error(
                "%s: %.0f s left after opening %d files in %.0f s; not starting the write",
                group_path,
                left,
                len(files),
                timings["open"],
            )
            run = GroupRun(group_path, "failed", len(files), 0.0, error="deadline")
        else:
            run = commit_group(
                repo,
                result,
                overwrite=bool(event.get("overwrite", False)),
                verify_files=int(event.get("verify", 0)),
            )
            timings["commit"] = round(time.time() - t0 - timings["open"], 1)
    except Exception as e:  # the fan-out must get a record back, not a stack trace
        logger.exception("%s failed", group_path)
        run = GroupRun(group_path, "failed", n_keys, 0.0, error=f"{type(e).__name__}: {e}")
    record_notes(run, notes)
    run.reason = event.get("reason") if isinstance(event, dict) else None
    run.cpu_count = os.cpu_count()
    run.n_workers = n
    run.timings = timings or None
    run.seconds = round(time.time() - t0, 1)
    return asdict(run)


def configure_logging() -> None:
    """Root level from ``ISMIP7_LOG_LEVEL`` (default INFO), keeping any installed handler."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
    root.setLevel(os.environ.get("ISMIP7_LOG_LEVEL", "INFO").upper())


def handler(event: Any, context: Any = None) -> dict[str, Any]:
    """AWS Lambda handler."""
    configure_logging()
    if isinstance(event, str | bytes):
        try:
            event = json.loads(event)
        except ValueError:
            pass  # run_event reports it
    return run_event(event, context=context)


__all__ = [
    "DEADLINE_MARGIN_S",
    "SecretCredentials",
    "configure_logging",
    "handler",
    "run_event",
    "target_from_event",
    "workers",
]
