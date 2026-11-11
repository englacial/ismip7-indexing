"""Gridlook catalog generated from the saved listing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ismip7_helper.catalog import (
    build_catalog,
    dataset_url,
    format_resolution,
    main,
    normalise_store_url,
    preferred_variable,
)
from ismip7_helper.index import build_index, read_listing

LISTING = Path(__file__).parent / "data" / "ismip_keys_2026-10-31.txt.gz"
AIS_STORE = "https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais"


@pytest.fixture(scope="module")
def index_df():
    return build_index(read_listing(LISTING))


def _expected_groups(df, region: str) -> set[str]:
    rows = df[
        (df["record_type"] == "forcing")
        & (df["ok"])
        & (df["region"] == region)
        & (df["kind"].isin(["annual", "multiyear", "climatology"]))
    ]
    return set(rows["group_path"].dropna())


@pytest.mark.parametrize("region", ["AIS", "GrIS"])
def test_one_entry_per_group(index_df, region):
    cat = build_catalog(index_df, region=region, store_url=AIS_STORE)
    groups = _expected_groups(index_df, region)
    assert cat["type"] == "gridlook_catalog"
    levels = [d for d in cat["datasets"] if "levels" not in d["tags"]]
    families = [d for d in cat["datasets"] if "levels" in d["tags"]]
    assert len(levels) == len(groups) > 0
    urls = {d["url"] for d in levels}
    assert urls == {f"icechunk+{AIS_STORE}/{g}" for g in groups}
    # every family with several resolutions is one more entry, pointing at the family group
    parents = {g.rpartition("/")[0] for g in groups if g.rsplit("/", 1)[1].endswith("m")}
    assert {d["url"] for d in families} <= {f"icechunk+{AIS_STORE}/{p}" for p in parents}
    assert all(int(d["tags"]["levels"]) >= 2 for d in families)
    for d in cat["datasets"]:
        assert d["tags"]["region"] == region
        assert all(isinstance(v, str) for v in d["tags"].values())
        assert d["params"]["colormap"] == "viridis"


def test_known_group(index_df):
    cat = build_catalog(index_df, region="AIS", store_url=AIS_STORE + "/")
    by_url = {d["url"]: d for d in cat["datasets"]}
    d = by_url[f"icechunk+{AIS_STORE}/CESM2-WACCM/ssp585/SDBN1/2000m"]
    assert d["title"] == "CESM2-WACCM ssp585 SDBN1 2 km"
    assert d["tags"] == {
        "region": "AIS",
        "source": "CESM2-WACCM",
        "experiment": "ssp585",
        "product": "SDBN1",
        "resolution": "2 km",
        "kind": "annual",
    }
    assert d["params"] == {"varname": "acabf", "colormap": "viridis"}
    assert "2015-2300" in d["description"]
    assert "acabf, acabf-anomaly" in d["description"]
    assert "3146 files" in d["description"]

    ocean = by_url[f"icechunk+{AIS_STORE}/CESM2-WACCM/ssp585/ocean/8000m"]
    assert ocean["tags"]["resolution"] == "8 km"
    assert ocean["tags"]["kind"] == "multiyear"
    assert ocean["params"]["varname"] == "tf"

    ocx = by_url[f"icechunk+{AIS_STORE}/ocean/ocx/main"]
    assert ocx["tags"]["resolution"] == "8 km"


def test_sorted_and_deterministic(index_df):
    a = build_catalog(index_df, region="AIS", store_url=AIS_STORE, title="ISMIP7 AIS")
    b = build_catalog(index_df, region="AIS", store_url=AIS_STORE, title="ISMIP7 AIS")
    assert a["title"] == "ISMIP7 AIS"
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    keys = [(d["tags"]["source"], d["tags"]["experiment"]) for d in a["datasets"]]
    assert keys == sorted(keys)
    assert json.loads(json.dumps(a)) == a


def test_helpers():
    assert normalise_store_url(f" icechunk+{AIS_STORE}// ") == AIS_STORE
    assert dataset_url(AIS_STORE + "/", "/a/b/c") == f"icechunk+{AIS_STORE}/a/b/c"
    assert format_resolution(8000) == "8 km"
    assert format_resolution(1000) == "1 km"
    assert format_resolution(None) is None
    assert preferred_variable(["pr", "tas", "tf"]) == "tf"
    assert preferred_variable(["sa", "ct"]) == "ct"
    assert preferred_variable([]) is None


def test_cli_from_index_and_listing(index_df, tmp_path):
    parquet = tmp_path / "index.parquet"
    index_df.to_parquet(parquet, index=False)
    out = tmp_path / "catalog.json"
    rc = main(
        ["--index", str(parquet), "--region", "AIS", "--store-url", AIS_STORE, "--out", str(out)]
    )
    assert rc == 0
    cat = json.loads(out.read_text())
    assert cat == build_catalog(index_df, region="AIS", store_url=AIS_STORE)
    assert cat["title"] == "ISMIP7 AIS forcing"

    out2 = tmp_path / "gris.json"
    rc = main(
        [
            "--from-listing",
            str(LISTING),
            "--region",
            "GrIS",
            "--store-url",
            AIS_STORE.replace("-ais", "-gris"),
            "--out",
            str(out2),
            "--title",
            "GrIS",
        ]
    )
    assert rc == 0
    cat2 = json.loads(out2.read_text())
    assert cat2["title"] == "GrIS"
    assert len([d for d in cat2["datasets"] if "levels" not in d["tags"]]) == len(
        _expected_groups(index_df, "GrIS")
    )


def test_family_entry_lists_its_levels(index_df):
    cat = build_catalog(index_df, region="AIS", store_url=AIS_STORE)
    by_url = {d["url"]: d for d in cat["datasets"]}
    fam = by_url[f"icechunk+{AIS_STORE}/CESM2-WACCM/ssp585/SDBN1"]
    assert fam["title"] == "CESM2-WACCM ssp585 SDBN1 (2 km + 8 km)"
    assert fam["tags"]["resolution"] == "2 km + 8 km" and fam["tags"]["levels"] == "2"
    assert (
        fam["params"]["varname"]
        == by_url[f"icechunk+{AIS_STORE}/CESM2-WACCM/ssp585/SDBN1/2000m"]["params"]["varname"]
    )
    assert f"icechunk+{AIS_STORE}/ACCESS-CM2/historical/dEBM2" not in by_url  # one level only
