"""Phase C tests: the Lambda handler and the fan-out, run in-process on synthetic files."""

from __future__ import annotations

import json
import os

import pytest

from ismip7_helper.conventions import parse_key
from ismip7_helper.fanout import main as fanout_main
from ismip7_helper.fanout import make_event, run_many
from ismip7_helper.lambda_handler import run_event, workers
from ismip7_helper.store import (
    VERIFIED_ATTR,
    StoreTarget,
    ensure_hierarchy,
    existing_groups,
    open_repository,
)
from tests.conftest import AIS_KEY_FMT, write_forcing_file

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _make_group(root, exp="historical", years=(1850, 1851), variables=("acabf", "tas")):
    keys = [
        AIS_KEY_FMT.format(var=v, year=y, esm="CESM2-WACCM", exp=exp, product="SDBN1-32000m", ver=2)
        for v in variables
        for y in years
    ]
    urls = {}
    for i, k in enumerate(keys):
        p = root / k
        write_forcing_file(
            p, year=int(k[-7:-3]), variable=k.rsplit("/", 1)[1].split("_")[0], seed=i
        )
        urls[k] = f"file://{p}"
    return keys, urls


def _event(root, tmp_path, keys, urls, **kw):
    files = [parse_key(k, 1000).forcing for k in keys]
    ev = make_event(files[0].group_path, files, target=f"local:{tmp_path / 'repo'}", **kw)
    ev["urls"] = urls
    ev["local_containers"] = {f"file://{root}/": str(root)}
    return ev


def _prepare(ev):
    """What the fan-out does before invoking workers: create the repo and the parents."""
    target = StoreTarget.parse(ev["target"], local_containers=ev["local_containers"])
    repo = open_repository(target, create=True)
    ensure_hierarchy(repo, [ev["group_path"]])
    return repo


def test_run_event_writes_and_verifies(synthetic_root, tmp_path, monkeypatch):
    monkeypatch.setenv("ISMIP7_WORKERS", "2")
    keys, urls = _make_group(synthetic_root)
    ev = _event(synthetic_root, tmp_path, keys, urls, verify=1)
    _prepare(ev)
    out = run_event(ev)
    assert out["status"] == "verified" and out["n_files"] == 4 and out["nrefs"] == 48
    assert out["verify_mismatches"] == [] and out["error"] is None
    assert out["committed"] and not out["deleted"] and out["sources_sha256"]
    assert json.dumps(out)  # serialisable, as a Lambda return must be
    repo = open_repository(StoreTarget.parse(ev["target"]), readonly=True)
    found = existing_groups(repo)
    assert list(found) == ["CESM2-WACCM/historical/SDBN1-32000m"]
    assert found["CESM2-WACCM/historical/SDBN1-32000m"][VERIFIED_ATTR] == out["snapshot_id"]


def test_run_event_deletes_a_group_that_fails_verification(synthetic_root, tmp_path, monkeypatch):
    import ismip7_helper.store as store

    monkeypatch.setenv("ISMIP7_WORKERS", "1")
    keys, urls = _make_group(synthetic_root)
    ev = _event(synthetic_root, tmp_path, keys, urls, verify=1)
    repo = _prepare(ev)
    wrong = write_forcing_file(synthetic_root / "wrong.nc", year=1850, seed=77)
    real_open = store.open_source
    monkeypatch.setattr(store, "open_source", lambda url: real_open(f"file://{wrong}"))
    out = run_event(ev)
    assert out["status"] == "failed" and out["error"] == "verification mismatches"
    assert out["committed"] and out["deleted"] and out["verify_mismatches"]
    assert existing_groups(repo) == {}
    monkeypatch.setattr(store, "open_source", real_open)
    assert run_event(ev)["status"] == "verified"  # a re-run redoes it


def test_run_event_needs_an_existing_repo(synthetic_root, tmp_path):
    keys, urls = _make_group(synthetic_root)
    out = run_event(_event(synthetic_root, tmp_path, keys, urls))
    assert out["status"] == "failed" and "RepositoryNotFoundError" in out["error"]
    assert not (tmp_path / "repo").exists()  # the handler never creates


def test_run_event_refuses_s3_without_write_credentials(monkeypatch):
    monkeypatch.delenv("ISMIP7_WRITE_CREDS_JSON", raising=False)
    monkeypatch.delenv("ISMIP7_WRITE_CREDS_SECRET", raising=False)
    key = AIS_KEY_FMT.format(
        var="acabf", year=1850, esm="CESM2-WACCM", exp="historical", product="SDBN1-32000m", ver=2
    )
    ev = {
        "group_path": "CESM2-WACCM/historical/SDBN1-32000m",
        "keys": [[key, 1]],
        "target": "s3://bucket/englacial/ismip7/icechunk-ais",
    }
    out = run_event(ev)
    assert out["status"] == "failed" and "no write credentials" in out["error"]
    monkeypatch.setenv(
        "ISMIP7_WRITE_CREDS_JSON",
        '{"Credentials": {"AccessKeyId": "AK", "SecretAccessKey": "SK",'
        ' "Expiration": "2099-11-01T00:00:00Z"}}',
    )
    from ismip7_helper.lambda_handler import target_from_event

    t = target_from_event(ev)
    assert t.credentials["expires_after"].year == 2099 and "SK" not in repr(t)


def test_run_event_reports_failure_instead_of_raising(synthetic_root, tmp_path):
    keys, urls = _make_group(synthetic_root, years=(1850, 1852))  # gap
    ev = _event(synthetic_root, tmp_path, keys, urls)
    _prepare(ev)
    out = run_event(ev)
    assert out["status"] == "failed" and "VirtualizeError" in out["error"]
    ev["keys"] = [["ismip/ismip7-ais-forcing/data/junk.nc", 1]]
    out = run_event(ev)
    assert out["status"] == "failed" and "do not parse" in out["error"]
    ev = _event(synthetic_root, tmp_path, keys, urls)
    ev["group_path"] = "CESM2-WACCM/ssp585/SDBN1-32000m"
    assert "do not all belong" in run_event(ev)["error"]


def test_run_event_refuses_existing_group_without_overwrite(synthetic_root, tmp_path, monkeypatch):
    monkeypatch.setenv("ISMIP7_WORKERS", "1")
    keys, urls = _make_group(synthetic_root)
    ev = _event(synthetic_root, tmp_path, keys, urls)
    _prepare(ev)
    assert run_event(ev)["status"] == "written"
    again = run_event(ev)
    assert again["status"] == "failed" and "ContainsGroupError" in again["error"]
    ev["overwrite"] = True
    assert run_event(ev)["status"] == "written"


def test_workers_env(monkeypatch):
    monkeypatch.setenv("ISMIP7_WORKERS", "3")
    assert workers() == 3
    monkeypatch.delenv("ISMIP7_WORKERS")
    assert workers() == max(1, os.cpu_count() or 1)


def test_run_many_collects_results_and_transport_errors():
    events = [{"group_path": f"g{i}", "keys": [["k", 1]]} for i in range(5)]

    def invoke(ev):
        if ev["group_path"] == "g3":
            raise ConnectionError("boom")
        return {"group_path": ev["group_path"], "status": "written", "n_files": 1, "seconds": 0.1}

    seen = []
    out = run_many(events, invoke, concurrency=3, on_result=seen.append)
    assert [r["group_path"] for r in out] == ["g0", "g1", "g2", "g3", "g4"]
    assert out[3]["status"] == "failed" and "ConnectionError" in out[3]["error"]
    assert len(seen) == 5
    out1 = run_many(events[:2], invoke, concurrency=1)
    assert len(out1) == 2


def test_fanout_local_cli(synthetic_root, tmp_path, monkeypatch):
    """End to end: index -> fan-out --local --source-root -> report, on synthetic files.

    The events are the real ``make_event`` shape (``source_root``) and the
    handler opens the files in its process pool.
    """
    import pandas as pd

    from ismip7_helper.index import build_index

    monkeypatch.setenv("ISMIP7_WORKERS", "2")
    keys, _ = _make_group(synthetic_root)
    keys2, _ = _make_group(synthetic_root, exp="ssp585", years=(2015,), variables=("acabf",))
    index = tmp_path / "index.parquet"
    build_index([(k, 1000) for k in keys + keys2]).to_parquet(index, index=False)
    assert len(pd.read_parquet(index)) == 5
    report = tmp_path / "run.json"
    rc = fanout_main(
        [
            "--index",
            str(index),
            "--region",
            "AIS",
            "--target",
            f"local:{tmp_path / 'repo'}",
            "--local",
            "--source-root",
            str(synthetic_root),
            "--verify",
            "1",
            "--report",
            str(report),
        ]
    )
    assert rc == 0
    runs = json.loads(report.read_text())
    assert {r["n_workers"] for r in runs} == {2} and all(r["cpu_count"] for r in runs)
    assert {r["group_path"]: r["status"] for r in runs} == {
        "CESM2-WACCM/historical/SDBN1-32000m": "verified",
        "CESM2-WACCM/ssp585/SDBN1-32000m": "verified",
    }

    # second run, for real (no --dry-run): both are verified and current, so both skip
    def run_again(*extra):
        rc = fanout_main(
            [
                "--index",
                str(index),
                "--region",
                "AIS",
                "--target",
                f"local:{tmp_path / 'repo'}",
                "--local",
                "--source-root",
                str(synthetic_root),
                "--report",
                str(report),
                *extra,
            ]
        )
        return rc, {
            r["group_path"]: (r["status"], r.get("reason")) for r in json.loads(report.read_text())
        }

    n_commits = len(
        list(
            open_repository(StoreTarget.parse(f"local:{tmp_path / 'repo'}")).ancestry(branch="main")
        )
    )
    rc, got = run_again("--verify", "1")
    assert rc == 0 and got == {
        "CESM2-WACCM/historical/SDBN1-32000m": ("skipped", "verified"),
        "CESM2-WACCM/ssp585/SDBN1-32000m": ("skipped", "verified"),
    }
    repo = open_repository(StoreTarget.parse(f"local:{tmp_path / 'repo'}"))
    assert len(list(repo.ancestry(branch="main"))) == n_commits  # nothing written

    # a republished file (new size in the index) makes its group stale: it is rewritten
    build_index([(k, 2000 if k == keys2[0] else 1000) for k in keys + keys2]).to_parquet(
        index, index=False
    )
    rc, got = run_again("--verify", "1")
    assert rc == 0 and got == {
        "CESM2-WACCM/historical/SDBN1-32000m": ("skipped", "verified"),
        "CESM2-WACCM/ssp585/SDBN1-32000m": ("verified", "sources changed"),
    }


def test_fanout_refuses_the_other_regions_repo(tmp_path):
    from ismip7_helper.index import build_index

    index = tmp_path / "index.parquet"
    build_index([]).to_parquet(index, index=False)
    with pytest.raises(SystemExit):
        fanout_main(
            [
                "--index",
                str(index),
                "--region",
                "GrIS",
                "--target",
                "s3://b/x/icechunk-ais",
                "--dry-run",
            ]
        )


def test_fanout_dry_run_prints_events(tmp_path, capsys):
    from ismip7_helper.index import build_index

    keys = [
        AIS_KEY_FMT.format(
            var="acabf", year=y, esm="CESM2-WACCM", exp="ctrl", product="SDBN1-8000m", ver=2
        )
        for y in (2015, 2016)
    ]
    index = tmp_path / "index.parquet"
    build_index([(k, 5) for k in keys]).to_parquet(index, index=False)
    rc = fanout_main(["--index", str(index), "--region", "AIS", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CESM2-WACCM/ctrl/SDBN1-8000m\t2 files" in out


# --------------------------------------------------------------------------- Phase C review


class _Ctx:
    """A stand-in for the Lambda context."""

    def __init__(self, ms):
        self.ms = ms

    def get_remaining_time_in_millis(self):
        return self.ms


def _s3_event():
    key = AIS_KEY_FMT.format(
        var="acabf", year=1850, esm="CESM2-WACCM", exp="historical", product="SDBN1-32000m", ver=2
    )
    return {
        "group_path": "CESM2-WACCM/historical/SDBN1-32000m",
        "keys": [[key, 1]],
        "target": "s3://bucket/englacial/ismip7/icechunk-ais",
    }


def _creds_json(expires):
    return json.dumps(
        {
            "Credentials": {
                "AccessKeyId": "AK",
                "SecretAccessKey": "SK",
                "SessionToken": "TOK",
                "Expiration": expires.isoformat(),
            }
        }
    )


def test_secret_credentials_with_a_mocked_secret(monkeypatch):
    import pickle
    from datetime import UTC, datetime, timedelta

    import boto3
    import icechunk

    from ismip7_helper.lambda_handler import SecretCredentials, target_from_event
    from ismip7_helper.store import CredentialsExpiringError

    now = datetime.now(UTC)

    class FakeSecrets:
        def __init__(self):
            self.secret = _creds_json(now + timedelta(hours=1))
            self.calls = []

        def get_secret_value(self, SecretId):
            self.calls.append(SecretId)
            return {"SecretString": self.secret}

    fake = FakeSecrets()
    monkeypatch.setattr(boto3, "client", lambda name, **kw: fake)
    monkeypatch.delenv("ISMIP7_WRITE_CREDS_JSON", raising=False)
    monkeypatch.setenv("ISMIP7_WRITE_CREDS_SECRET", "ismip7/sc_creds")
    ev = _s3_event()

    t = target_from_event(ev)
    assert isinstance(t.get_credentials, SecretCredentials) and fake.calls == ["ismip7/sc_creds"]
    assert isinstance(t.get_credentials(), icechunk.S3StaticCredentials)
    assert pickle.loads(pickle.dumps(t.get_credentials)).secret_id == "ismip7/sc_creds"
    assert "TOK" not in repr(t)

    # valid now, but not until the invocation's deadline: refused up front
    with pytest.raises(CredentialsExpiringError, match="expire at"):
        target_from_event(ev, needed_until=now + timedelta(hours=2))
    out = run_event(ev, context=_Ctx(2 * 3600 * 1000))
    assert out["status"] == "failed" and "CredentialsExpiringError" in out["error"]

    fake.secret = _creds_json(now - timedelta(minutes=5))
    with pytest.raises(CredentialsExpiringError, match="expired at"):
        target_from_event(ev)

    # the JSON and the secret are exclusive
    monkeypatch.setenv("ISMIP7_WRITE_CREDS_JSON", _creds_json(now + timedelta(hours=1)))
    out = run_event(ev)
    assert out["status"] == "failed" and "set only one" in out["error"]


def test_lambda_invoker_parses_payloads_and_function_errors():
    import io

    import boto3
    from botocore.response import StreamingBody
    from botocore.stub import ANY, Stubber

    from ismip7_helper.fanout import lambda_invoker

    real = lambda_invoker("ismip7-virtualize", "us-west-2", concurrency=40).client.meta.config
    assert real.read_timeout == 960 and real.tcp_keepalive and real.max_pool_connections == 40
    assert real.retries["total_max_attempts"] == 1  # max_attempts 0: no retry

    client = boto3.client(
        "lambda", region_name="us-west-2", aws_access_key_id="a", aws_secret_access_key="b"
    )
    stub = Stubber(client)

    def body(raw):
        raw = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
        return StreamingBody(io.BytesIO(raw), len(raw))

    params = {"FunctionName": "f", "InvocationType": "RequestResponse", "Payload": ANY}
    ok = {"group_path": "g", "status": "verified", "n_files": 2, "seconds": 3.0}
    stub.add_response("invoke", {"StatusCode": 200, "Payload": body(ok)}, params)
    stub.add_response(
        "invoke",
        {
            "StatusCode": 200,
            "FunctionError": "Unhandled",
            "Payload": body({"errorType": "Sandbox.Timedout", "errorMessage": "timed out"}),
        },
        params,
    )
    stub.add_response("invoke", {"StatusCode": 200, "Payload": body(b"not json")}, params)
    stub.add_client_error("invoke", "TooManyRequestsException", http_status_code=429)
    invoke = lambda_invoker("f", client=client)
    ev = {"group_path": "g", "keys": [["k1", 1], ["k2", 2]]}
    with stub:
        assert invoke(ev) == ok
        r = invoke(ev)
        assert r["status"] == "failed" and r["n_files"] == 2
        assert r["error"] == "lambda Unhandled: Sandbox.Timedout: timed out"
        r = invoke(ev)
        assert r["status"] == "failed" and "unexpected lambda payload: not json" in r["error"]
        # a client error becomes a failed record in run_many
        (r,) = run_many([ev], invoke, concurrency=2)
        assert r["status"] == "failed" and "TooManyRequests" in r["error"]
        stub.assert_no_pending_responses()


def test_run_many_ctrl_c_starts_no_more_and_records_in_flight():
    import _thread
    import threading

    release = threading.Event()
    started = []
    lock = threading.Lock()

    def invoke(ev):
        with lock:
            started.append(ev["group_path"])
            n = len(started)
        if n == 4:
            _thread.interrupt_main()  # Ctrl-C while four groups run
        release.wait(10)
        return {"group_path": ev["group_path"], "status": "written", "n_files": 1, "seconds": 0}

    events = [{"group_path": f"g{i:02d}", "keys": [["k", 1]]} for i in range(40)]
    seen = []
    try:
        with pytest.raises(KeyboardInterrupt):
            run_many(events, invoke, concurrency=4, on_result=seen.append)
    finally:
        release.set()
    assert len(started) == 4
    assert sorted(r["group_path"] for r in seen) == sorted(started)
    assert {r["status"] for r in seen} == {"in-flight"}


def _two_group_index(root, tmp_path):
    from ismip7_helper.index import build_index

    keys, _ = _make_group(root)
    keys2, _ = _make_group(root, exp="ssp585", years=(2015,), variables=("acabf",))
    index = tmp_path / "index.parquet"
    build_index([(k, 1000) for k in keys + keys2]).to_parquet(index, index=False)
    return index


HIST = "CESM2-WACCM/historical/SDBN1-32000m"
SSP = "CESM2-WACCM/ssp585/SDBN1-32000m"


def _local_fanout(index, root, repo, report, *extra):
    return fanout_main(
        [
            "--index",
            str(index),
            "--region",
            "AIS",
            "--target",
            f"local:{repo}",
            "--local",
            "--source-root",
            str(root),
            "--report",
            str(report),
            *extra,
        ]
    )


def test_fanout_ctrl_c_writes_the_report(synthetic_root, tmp_path, monkeypatch):
    import ismip7_helper.fanout as fanout

    index = _two_group_index(synthetic_root, tmp_path)

    def invoke(ev):
        if ev["group_path"] == SSP:
            raise KeyboardInterrupt
        return {"group_path": ev["group_path"], "status": "written", "n_files": 4, "seconds": 1}

    monkeypatch.setattr(fanout, "local_invoker", lambda: invoke)
    report = tmp_path / "run.json"
    assert _local_fanout(index, synthetic_root, tmp_path / "repo", report) == 130
    got = {r["group_path"]: r["status"] for r in json.loads(report.read_text())}
    assert got == {HIST: "written", SSP: "interrupted"}


def test_fanout_max_groups_counts_groups_to_write(synthetic_root, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ISMIP7_WORKERS", "1")
    index = _two_group_index(synthetic_root, tmp_path)
    repo, report = tmp_path / "repo", tmp_path / "run.json"
    status = lambda: {r["group_path"]: r["status"] for r in json.loads(report.read_text())}  # noqa: E731

    assert _local_fanout(index, synthetic_root, repo, report, "--max-groups", "1") == 0
    assert status() == {HIST: "written"}
    capsys.readouterr()
    # the dry run plans against the repository too: the next group to write is SSP
    assert _local_fanout(index, synthetic_root, repo, report, "--max-groups", "1", "--dry-run") == 0
    out = capsys.readouterr().out
    assert SSP in out and HIST not in out
    assert _local_fanout(index, synthetic_root, repo, report, "--max-groups", "1") == 0
    assert status() == {HIST: "skipped", SSP: "written"}


def test_fanout_lambda_mode_defers_big_groups(synthetic_root, tmp_path, monkeypatch, capsys):
    """An S3 target: credentials checked, big groups deferred, the rest invoked."""
    from datetime import UTC, datetime, timedelta

    import ismip7_helper.fanout as fanout

    monkeypatch.setenv("ISMIP7_WORKERS", "1")
    index = _two_group_index(synthetic_root, tmp_path)
    local = StoreTarget.parse(
        f"local:{tmp_path / 'repo'}",
        local_containers={f"file://{synthetic_root}/": str(synthetic_root)},
    )
    monkeypatch.setattr(fanout, "open_repository", lambda st, **kw: open_repository(local, **kw))
    events, regions = [], []

    def fake_invoker(name, region, *, concurrency):
        regions.append((name, region, concurrency))

        def invoke(ev):
            events.append(ev)
            return run_event({**ev, "target": local.url, "source_root": str(synthetic_root)})

        return invoke

    monkeypatch.setattr(fanout, "lambda_invoker", fake_invoker)
    creds = tmp_path / "sc_creds.json"
    report, big = tmp_path / "run.json", tmp_path / "big.txt"
    args = [
        "--index",
        str(index),
        "--region",
        "AIS",
        "--target",
        "s3://bucket/englacial/ismip7/icechunk-ais",
        "--write-creds",
        str(creds),
        "--function",
        "ismip7-virtualize",
        "--aws-region",
        "eu-west-1",
        "--target-region",
        "us-east-1",
        "--concurrency",
        "2",
        "--seconds-per-file",
        "100",
        "--speedup",
        "1",
        "--max-lambda-seconds",
        "200",
        "--big-groups",
        str(big),
        "--report",
        str(report),
    ]
    creds.write_text(_creds_json(datetime.now(UTC) + timedelta(minutes=5)))
    with pytest.raises(SystemExit):  # the fan-out needs them for 20 minutes
        fanout_main(args)
    assert "expire at" in capsys.readouterr().err
    creds.write_text(_creds_json(datetime.now(UTC) + timedelta(hours=1)))
    assert fanout_main(args) == 0
    runs = {r["group_path"]: r for r in json.loads(report.read_text())}
    assert runs[HIST]["status"] == "deferred" and "400 s" in runs[HIST]["reason"]
    assert runs[SSP]["status"] == "written"
    assert regions == [("ismip7-virtualize", "eu-west-1", 2)]
    assert [e["group_path"] for e in events] == [SSP] and events[0]["region"] == "us-east-1"
    text = big.read_text()
    assert HIST in text and "ismip7-virtualize" in text and "--workers $(nproc)" in text
    assert "ismip7-virtualize" in capsys.readouterr().err


def test_fanout_refuses_unworkable_combinations(tmp_path, capsys):
    from ismip7_helper.index import build_index

    index = tmp_path / "index.parquet"
    build_index([]).to_parquet(index, index=False)
    base = ["--index", str(index), "--region", "AIS"]
    for extra, msg in [
        (["--target", f"local:{tmp_path}/r", "--function", "f"], "Lambda workers cannot write"),
        (["--target", f"local:{tmp_path}/r", "--local", "--function", "f"], "exclusive"),
        (["--source-root", str(tmp_path), "--function", "f"], "--source-root needs --local"),
        (["--target", "s3://b/x/icechunk-ais", "--function", "f"], "--write-creds is required"),
    ]:
        with pytest.raises(SystemExit):
            fanout_main(base + extra)
        assert msg in capsys.readouterr().err


def test_run_event_reports_malformed_events():
    out = run_event({"keys": [["k", 1]], "target": "local:/nowhere"})
    assert out["status"] == "failed" and out["group_path"] == "?"
    assert "no group_path" in out["error"] and out["n_files"] == 1
    out = run_event({"group_path": HIST, "keys": [], "target": "local:/nowhere"})
    assert out["status"] == "failed" and "no keys" in out["error"]
    from ismip7_helper.lambda_handler import handler

    out = handler("not json")
    assert out["status"] == "failed" and "JSON object" in out["error"]


def test_run_event_stops_before_the_write_near_the_deadline(synthetic_root, tmp_path, monkeypatch):
    monkeypatch.setenv("ISMIP7_WORKERS", "1")
    keys, urls = _make_group(synthetic_root)
    ev = _event(synthetic_root, tmp_path, keys, urls)
    repo = _prepare(ev)
    out = run_event(ev, context=_Ctx(60_000))
    assert out["status"] == "failed" and out["error"] == "deadline" and not out["committed"]
    assert out["timings"]["open"] >= 0 and out["timings"]["remaining_after_open"] <= 60
    assert existing_groups(repo) == {}
    out = run_event(ev, context=_Ctx(600_000))
    assert out["status"] == "written" and set(out["timings"]) >= {"open", "commit"}


def test_handler_logs_notes_at_info_under_a_preinstalled_root_handler(
    synthetic_root, tmp_path, monkeypatch
):
    """The Lambda runtime installs a root handler at WARNING before importing us."""
    import logging

    from ismip7_helper.lambda_handler import handler

    monkeypatch.setenv("ISMIP7_WORKERS", "1")
    monkeypatch.delenv("ISMIP7_LOG_LEVEL", raising=False)
    key = AIS_KEY_FMT.format(
        var="acabf", year=1850, esm="CESM2-WACCM", exp="historical", product="SDBN1-32000m", ver=2
    )
    write_forcing_file(synthetic_root / key, year=1850, extra_var=True)
    files = [parse_key(key, 1000).forcing]
    ev = make_event(
        HIST, files, target=f"local:{tmp_path / 'repo'}", source_root=str(synthetic_root)
    )
    repo = open_repository(
        StoreTarget.parse(
            ev["target"], local_containers={f"file://{synthetic_root}/": str(synthetic_root)}
        ),
        create=True,
    )
    ensure_hierarchy(repo, [HIST])

    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    root.handlers = [Collect()]
    root.setLevel(logging.WARNING)
    try:
        out = handler(json.dumps(ev))
    finally:
        root.handlers, level = saved
        root.setLevel(level)
    assert out["status"] == "written", out["error"]
    assert out["n_notes"] == 1 and "dropped extra data variables" in out["notes_head"][0]
    assert any(
        r.levelno == logging.INFO and "dropped extra data variables" in r.getMessage()
        for r in records
    )


def test_run_event_reports_cpu_utilization(synthetic_root, tmp_path, monkeypatch):
    monkeypatch.setenv("ISMIP7_WORKERS", "2")
    keys, urls = _make_group(synthetic_root)
    ev = _event(synthetic_root, tmp_path, keys, urls, verify=0)
    out = run_event(ev)
    assert out["status"] == "written"
    t = out["timings"]
    assert t["open_cpu"] >= 0 and 0 <= t["open_utilization"] <= 1.5  # >1 only if children exceed n
    assert "commit_cpu" in t and t["commit_cpu"] >= 0
