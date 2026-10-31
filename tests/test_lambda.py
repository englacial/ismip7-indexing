"""Phase C tests: the Lambda handler and the fan-out, run in-process on synthetic files."""

from __future__ import annotations

import json

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
        ' "Expiration": "2026-11-01T00:00:00Z"}}',
    )
    from ismip7_helper.lambda_handler import SecretCredentials, target_from_event

    t = target_from_event(ev)
    assert t.credentials["expires_after"].year == 2026 and "SK" not in repr(t)
    monkeypatch.delenv("ISMIP7_WRITE_CREDS_JSON")
    monkeypatch.setenv("ISMIP7_WRITE_CREDS_SECRET", "ismip7/sc_creds")
    t = target_from_event(ev)
    assert isinstance(t.get_credentials, SecretCredentials)


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
    assert workers() >= 1


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
    """End to end: index -> fan-out --local -> report, on synthetic files."""
    import pandas as pd

    from ismip7_helper.index import build_index

    monkeypatch.setenv("ISMIP7_WORKERS", "1")
    keys, urls = _make_group(synthetic_root)
    keys2, urls2 = _make_group(synthetic_root, exp="ssp585", years=(2015,), variables=("acabf",))
    index = tmp_path / "index.parquet"
    build_index([(k, 1000) for k in keys + keys2]).to_parquet(index, index=False)
    assert len(pd.read_parquet(index)) == 5

    # the local handler needs file:// urls and a local container; patch make_event via a wrapper
    import ismip7_helper.fanout as fanout

    orig = fanout.make_event

    def patched(group_path, files, **kw):
        ev = orig(group_path, files, **kw)
        ev["urls"] = {**urls, **urls2}
        ev["local_containers"] = {f"file://{synthetic_root}/": str(synthetic_root)}
        return ev

    monkeypatch.setattr(fanout, "make_event", patched)
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
            "--verify",
            "1",
            "--report",
            str(report),
        ]
    )
    assert rc == 0
    runs = json.loads(report.read_text())
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
