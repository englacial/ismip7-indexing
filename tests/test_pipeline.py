"""Phase B runner tests: index -> plan -> write -> verify -> report, on synthetic files."""

from __future__ import annotations

import json

import pytest

import ismip7_helper.pipeline as pipeline
import ismip7_helper.store as store
from ismip7_helper.index import build_index
from ismip7_helper.pipeline import group_digest, load_groups, plan_groups
from ismip7_helper.store import VERIFIED_ATTR, StoreTarget, existing_groups, open_repository
from ismip7_helper.virtualize import concat_group, local_registry
from tests.conftest import AIS_KEY_FMT, write_forcing_file

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

HIST = "CESM2-WACCM/historical/SDBN1/32000m"
SSP = "CESM2-WACCM/ssp585/SDBN1/32000m"


def _key(exp, year, var="acabf"):
    return AIS_KEY_FMT.format(
        var=var, year=year, esm="CESM2-WACCM", exp=exp, product="SDBN1-32000m", ver=2
    )


def test_load_groups_with_missing_sizes(tmp_path):
    keys = [_key("historical", y) for y in (1850, 1851, 1852)]
    index = tmp_path / "index.parquet"
    build_index(list(zip(keys, [100, None, 300], strict=True))).to_parquet(index, index=False)
    groups = load_groups(index, region="AIS")
    assert list(groups) == [HIST]
    assert sorted(f.size or 0 for f in groups[HIST]) == [0, 100, 300]
    build_index([(k, None) for k in keys]).to_parquet(index, index=False)
    assert all(f.size is None for f in load_groups(index, region="AIS")[HIST])


def test_plan_groups():
    from ismip7_helper.conventions import parse_key

    files = {HIST: [parse_key(_key("historical", 1850), 5).forcing]}
    digest = group_digest(files[HIST])
    assert plan_groups(files, {}).write[HIST][1:] == (False, "new")
    current = {HIST: {store.SOURCES_ATTR: digest}}
    assert plan_groups(files, current).skip[HIST][1] == "exists"
    assert plan_groups(files, current, verify=1).write[HIST][1:] == (True, "not verified")
    verified = {HIST: {store.SOURCES_ATTR: digest, VERIFIED_ATTR: "SNAP"}}
    assert plan_groups(files, verified, verify=1).skip[HIST][1] == "verified"
    assert plan_groups(files, verified, overwrite=True).write[HIST][1:] == (True, "--overwrite")
    stale = {HIST: {store.SOURCES_ATTR: "0" * 64, VERIFIED_ATTR: "SNAP"}}
    assert plan_groups(files, stale, verify=1).write[HIST][1:] == (True, "sources changed")


@pytest.fixture
def local_run(synthetic_root, tmp_path, monkeypatch):
    """``pipeline.main`` against a local repo whose chunks live on local disk."""
    keys = [_key("historical", y) for y in (1850, 1851)] + [_key("ssp585", 2015)]
    urls = {}
    for i, k in enumerate(keys):
        p = synthetic_root / k
        write_forcing_file(p, year=int(k[-7:-3]), seed=i)
        urls[k] = f"file://{p}"
    index = tmp_path / "index.parquet"
    build_index([(k, 1000) for k in keys]).to_parquet(index, index=False)
    containers = {f"file://{synthetic_root}/": str(synthetic_root)}

    class LocalTarget(StoreTarget):
        @classmethod
        def parse(cls, url, **kw):
            return StoreTarget(url=url, local_containers=containers, **kw)

    monkeypatch.setattr(pipeline, "StoreTarget", LocalTarget)
    monkeypatch.setattr(
        pipeline,
        "concat_group",
        lambda files, registry=None, **kw: concat_group(
            files, None if kw.get("use_processes") else local_registry(), urls=urls, **kw
        ),
    )
    target = f"local:{tmp_path / 'repo'}"
    report = tmp_path / "run.json"

    def run(*extra):
        rc = pipeline.main(
            [
                "--index",
                str(index),
                "--region",
                "AIS",
                "--target",
                target,
                "--report",
                str(report),
                *extra,
            ]
        )
        recs = json.loads(report.read_text())
        return rc, {r["group_path"]: r for r in recs}

    repo = lambda: open_repository(StoreTarget(url=target, local_containers=containers))  # noqa: E731
    return run, repo, synthetic_root


def test_pipeline_fail_verification_then_rerun(local_run, monkeypatch):
    run, repo, root = local_run
    wrong = write_forcing_file(root / "wrong.nc", year=2015, seed=99)
    real_open = store.open_source
    monkeypatch.setattr(
        store,
        "open_source",
        lambda url: real_open(f"file://{wrong}" if "ssp585" in url else url),
    )
    rc, runs = run("--verify", "1")
    assert rc == 1
    assert runs[HIST]["status"] == "verified" and runs[HIST]["committed"]
    assert runs[SSP]["status"] == "failed" and runs[SSP]["committed"] and runs[SSP]["deleted"]
    found = existing_groups(repo())
    assert list(found) == [HIST] and found[HIST][VERIFIED_ATTR] == runs[HIST]["snapshot_id"]

    # the bad group was removed, so a re-run redoes it (and only it); --max-groups
    # counts groups to write, after skipping
    monkeypatch.setattr(store, "open_source", real_open)
    rc, runs = run("--verify", "1", "--max-groups", "1")
    assert rc == 0
    assert (runs[HIST]["status"], runs[HIST]["reason"]) == ("skipped", "verified")
    assert runs[SSP]["status"] == "verified" and not runs[SSP]["deleted"]

    rc, runs = run("--verify", "1")
    assert rc == 0 and {r["status"] for r in runs.values()} == {"skipped"}


def test_pipeline_unverified_groups_are_redone_by_a_verifying_run(local_run):
    run, _, _ = local_run
    rc, runs = run()
    assert rc == 0 and {r["status"] for r in runs.values()} == {"written"}
    rc, runs = run()  # --verify 0: any current group is skipped
    assert {(r["status"], r["reason"]) for r in runs.values()} == {("skipped", "exists")}
    rc, runs = run("--verify", "1")
    assert rc == 0
    assert {(r["status"], r["reason"]) for r in runs.values()} == {("verified", "not verified")}


def test_pipeline_records_failures_of_any_kind(local_run, monkeypatch):
    """A non-ValueError failure (e.g. a storage error) is recorded, the run continues."""
    run, _, _ = local_run
    real_write = pipeline.write_group

    def flaky(repo, result, **kw):
        if result.group_path == HIST:
            raise OSError("S3 503")
        return real_write(repo, result, **kw)

    monkeypatch.setattr(pipeline, "write_group", flaky)
    rc, runs = run()
    assert rc == 1
    assert runs[HIST]["status"] == "failed" and "OSError: S3 503" in runs[HIST]["error"]
    assert not runs[HIST]["committed"] and runs[SSP]["status"] == "written"


def test_pipeline_refuses_the_other_regions_repo(tmp_path):
    index = tmp_path / "index.parquet"
    build_index([(_key("historical", 1850), 1)]).to_parquet(index, index=False)
    with pytest.raises(SystemExit):
        pipeline.main(
            [
                "--index",
                str(index),
                "--region",
                "GrIS",
                "--target",
                f"local:{tmp_path}/icechunk-ais",
            ]
        )


def test_pipeline_workers_open_files_in_processes(local_run):
    run, _, _ = local_run
    rc, runs = run("--workers", "2")
    assert rc == 0 and {r["status"] for r in runs.values()} == {"written"}
    assert {r["n_workers"] for r in runs.values()} == {2}
    assert all(r["cpu_count"] and set(r["timings"]) == {"open", "commit"} for r in runs.values())
