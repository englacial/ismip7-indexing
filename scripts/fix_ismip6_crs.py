"""Make the ISMIP6 Icechunk store (englacial/ismip6/icechunk-ais) renderable by gridlook.

gridlook finds a dataset's CRS through the data variable's ``grid_mapping``
attribute, the parent group's ``grid_mapping`` attribute, a ``spatial_ref``
entry in ``coordinates``, or an array literally named ``crs``. The ISMIP6
store has a projection scalar in 1,137 of its 1,317 groups (named
``polar_stereographic``, ``crs`` or ``mapping``) but no ``grid_mapping``
attribute anywhere, and 180 groups have no projection scalar at all, so
gridlook treats x/y as lon/lat.

This script adds, per group, a group attribute ``grid_mapping = <name>`` and
the same attribute on every data variable, pointing at the existing
projection scalar; where there is none it creates a ``crs`` scalar with the
EPSG:3031 attributes and WKT from ``GridSpec``. Chunk data is untouched; one
commit.

Usage::

    python scripts/fix_ismip6_crs.py --dry-run                 # read-only report
    python scripts/fix_ismip6_crs.py --apply --write-creds sc_creds.json

Note: the ISMIP6 repository is Icechunk spec v1. icechunk 2.x opens it;
whether a commit keeps it v1 or requires an upgrade is checked by
``--dry-run`` (it prints the spec version) and by the local test in
``tests/test_fix_ismip6_crs.py``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import numpy as np
import zarr

from ismip7_helper.conventions import grid_registry

BUCKET = "us-west-2.opendata.source.coop"
PREFIX = "englacial/ismip6/icechunk-ais"
SOURCE_PREFIX = f"s3://{BUCKET}/englacial/ismip6/"
CRS_NAMES = ("crs", "polar_stereographic", "mapping", "spatial_ref")
COORD_NAMES = {"x", "y", "time", "lat", "lon", "time_bnds", "time_bounds"}


def open_repo(credentials: dict[str, str] | None = None) -> Any:
    import icechunk

    kw: dict[str, Any] = {"bucket": BUCKET, "prefix": PREFIX, "region": "us-west-2"}
    kw.update(credentials or {"anonymous": True})
    storage = icechunk.s3_storage(**kw)
    creds = icechunk.containers_credentials({SOURCE_PREFIX: icechunk.s3_anonymous_credentials()})
    return icechunk.Repository.open(storage=storage, authorize_virtual_chunk_access=creds)


def plan_group(group: zarr.Group, region: str = "AIS") -> dict[str, Any]:
    """What to change in one group: {'crs_name': ..., 'create_crs': bool, 'vars': [...]}."""
    members = dict(group.members())
    crs_name = next((n for n in CRS_NAMES if n in members), None)
    data_vars = [
        n
        for n, a in members.items()
        if isinstance(a, zarr.Array) and a.ndim >= 2 and n not in COORD_NAMES and n not in CRS_NAMES
    ]
    todo = [n for n in data_vars if members[n].attrs.get("grid_mapping") != (crs_name or "crs")]
    return {
        "crs_name": crs_name or "crs",
        "create_crs": crs_name is None,
        "vars": todo,
        "group_attr": group.attrs.get("grid_mapping") != (crs_name or "crs"),
    }


def apply_group(group: zarr.Group, plan: dict[str, Any], region: str = "AIS") -> None:
    name = plan["crs_name"]
    if plan["create_crs"]:
        attrs = grid_registry()[(region, 8000)].cf_grid_mapping_attrs()  # type: ignore[index]
        arr = group.create_array(name, shape=(), dtype="int32", fill_value=0)
        arr[...] = np.int32(0)
        arr.attrs.update(attrs)
    else:
        arr = group[name]
        if "crs_wkt" not in arr.attrs:
            arr.attrs["crs_wkt"] = grid_registry()[(region, 8000)].crs_wkt()  # type: ignore[index]
    for v in plan["vars"]:
        group[v].attrs["grid_mapping"] = name
    if plan["group_attr"]:
        group.attrs["grid_mapping"] = name


def iter_groups(root: zarr.Group):
    """Yield (path, group) for every leaf group holding arrays (depth-first)."""
    stack = [("", root)]
    while stack:
        path, g = stack.pop()
        members = dict(g.members())
        if any(isinstance(a, zarr.Array) for a in members.values()):
            yield path, g
        for n, child in members.items():
            if isinstance(child, zarr.Group):
                stack.append((f"{path}/{n}" if path else n, child))


def run(repo: Any, *, apply: bool, branch: str = "main", region: str = "AIS") -> dict[str, int]:
    import icechunk

    session = repo.writable_session(branch) if apply else repo.readonly_session(branch)
    root = zarr.open_group(session.store, mode="r+" if apply else "r")
    stats = {"groups": 0, "changed": 0, "created_crs": 0, "vars": 0}
    for _path, g in iter_groups(root):
        stats["groups"] += 1
        plan = plan_group(g, region)
        if not plan["vars"] and not plan["group_attr"] and not plan["create_crs"]:
            continue
        stats["changed"] += 1
        stats["created_crs"] += int(plan["create_crs"])
        stats["vars"] += len(plan["vars"])
        if apply:
            apply_group(g, plan, region)
    if apply and stats["changed"]:
        snap = session.commit(
            f"Add grid_mapping attributes and crs scalars for viewers ({stats['changed']} groups)",
            rebase_with=icechunk.ConflictDetector(),
        )
        stats["snapshot"] = snap  # type: ignore[assignment]
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--write-creds", help="source.coop sc_creds.json (with --apply)")
    args = ap.parse_args(argv)
    creds = None
    if args.apply:
        if not args.write_creds:
            ap.error("--apply needs --write-creds")
        from ismip7_helper.store import load_sc_creds

        creds = load_sc_creds(args.write_creds)
    repo = open_repo(creds)
    print("spec version:", getattr(repo, "spec_version", "?"), file=sys.stderr)
    stats = run(repo, apply=args.apply)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
