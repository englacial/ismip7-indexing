"""The ISMIP6 CRS fix on a local repo shaped like the ISMIP6 store."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
import zarr

SCRIPT = Path(__file__).parent.parent / "scripts" / "fix_ismip6_crs.py"
spec = importlib.util.spec_from_file_location("fix_ismip6_crs", SCRIPT)
fix = importlib.util.module_from_spec(spec)
sys.modules["fix_ismip6_crs"] = fix
spec.loader.exec_module(fix)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def ismip6_like_repo(tmp_path):
    import icechunk

    repo = icechunk.Repository.create(icechunk.local_filesystem_storage(str(tmp_path / "repo")))
    s = repo.writable_session("main")
    root = zarr.open_group(s.store, mode="w")
    for path, crs_name in (
        ("combined/JPL1_ISSM/exp05", "polar_stereographic"),
        ("state/AWI_PISM1/ctrl_proj_std", "mapping"),
        ("flux/DOE_MALI/exp13", None),
    ):
        g = root.create_group(path)
        g.create_array("x", shape=(4,), dtype="f8")[...] = np.arange(4.0)
        g.create_array("y", shape=(4,), dtype="f8")[...] = np.arange(4.0)
        g.create_array("time", shape=(2,), dtype="f8")[...] = [0.0, 365.0]
        for v in ("lithk", "acabf"):
            a = g.create_array(v, shape=(2, 4, 4), dtype="f4", chunks=(1, 4, 4))
            a[...] = 1.0
            a.attrs["units"] = "m"
        if crs_name:
            c = g.create_array(crs_name, shape=(), dtype="i4")
            c[...] = 0
            c.attrs.update(
                {"grid_mapping_name": "polar_stereographic", "latitude_of_projection_origin": -90.0}
            )
        g.attrs["coordinates"] = "time x y"
    s.commit("ismip6-like")
    return repo


def test_dry_run_counts_without_changing(ismip6_like_repo):
    stats = fix.run(ismip6_like_repo, apply=False)
    assert stats == {"groups": 3, "changed": 3, "created_crs": 1, "vars": 6}
    assert len(list(ismip6_like_repo.ancestry(branch="main"))) == 2  # root + one commit


def test_apply_adds_grid_mapping_and_crs(ismip6_like_repo):
    stats = fix.run(ismip6_like_repo, apply=True)
    assert stats["changed"] == 3 and "snapshot" in stats
    root = zarr.open_group(ismip6_like_repo.readonly_session("main").store, mode="r")
    g = root["combined/JPL1_ISSM/exp05"]
    assert g.attrs["grid_mapping"] == "polar_stereographic"
    assert g["lithk"].attrs["grid_mapping"] == "polar_stereographic"
    assert "3031" in g["polar_stereographic"].attrs["crs_wkt"]
    g2 = root["state/AWI_PISM1/ctrl_proj_std"]
    assert g2["acabf"].attrs["grid_mapping"] == "mapping"
    g3 = root["flux/DOE_MALI/exp13"]
    assert g3["crs"].attrs["grid_mapping_name"] == "polar_stereographic"
    assert g3["crs"].attrs["latitude_of_projection_origin"] == -90.0
    assert g3["lithk"].attrs["grid_mapping"] == "crs" and g3.attrs["grid_mapping"] == "crs"
    # data untouched, and a second run is a no-op
    assert float(g3["lithk"][0, 0, 0]) == 1.0
    again = fix.run(ismip6_like_repo, apply=False)
    assert again["changed"] == 0


def test_gridlook_lookup_order_is_satisfied(ismip6_like_repo):
    """gridlook: variable grid_mapping -> group grid_mapping -> 'crs'. All three now hold."""
    fix.run(ismip6_like_repo, apply=True)
    root = zarr.open_group(ismip6_like_repo.readonly_session("main").store, mode="r")
    for _path, g in fix.iter_groups(root):
        name = g.attrs["grid_mapping"]
        assert name in dict(g.members())
        assert all(g[v].attrs["grid_mapping"] == name for v in fix.plan_group(g)["vars"] or [])
