"""Convenience openers for the published ISMIP7 Icechunk stores.

The boilerplate every reader needs (repository location, the anonymous
virtual-chunk container for the source bucket, read-only session) in one
call, for xarray's ``zarr`` engine and for ``rustytree``::

    from ismip7_helper.access import open_store, open_group

    dt = open_store("AIS")                                   # DataTree, engine="zarr"
    dt = open_store("AIS", engine="rustytree")               # same, via rustytree
    ds = open_group("AIS", "CESM2-WACCM/ssp585/SDBN1/8000m") # one level as a Dataset

``ISMIP7_STORE_<REGION>`` (e.g. ``ISMIP7_STORE_AIS=local:/path/icechunk-ais``)
overrides the production location, which is how the notebooks run against a
local dev store.
"""

from __future__ import annotations

import os
from typing import Any

import xarray as xr

from .store import StoreTarget, open_repository


def store_target(region: str) -> StoreTarget:
    region = {"ais": "AIS", "gris": "GrIS"}[region.lower()]
    override = os.environ.get(f"ISMIP7_STORE_{region.upper()}")
    return StoreTarget.parse(override) if override else StoreTarget.region_default(region)


def open_repo(region: str) -> Any:
    """Read-only icechunk Repository for a region's store."""
    return open_repository(store_target(region), readonly=True)


def open_session(region: str, ref: str = "main") -> Any:
    """Read-only icechunk session on a branch (default ``main``), tag or snapshot id."""
    repo = open_repo(region)
    if ref == "main" or ref in repo.list_branches():
        return repo.readonly_session(branch=ref)
    if ref in repo.list_tags():
        return repo.readonly_session(tag=ref)
    return repo.readonly_session(snapshot_id=ref)


def open_store(
    region: str,
    *,
    engine: str = "zarr",
    ref: str = "main",
    group_filter: str | None = None,
    **kw: Any,
) -> xr.DataTree:
    """Open a region's whole store as a DataTree with ``engine="zarr"`` or ``"rustytree"``."""
    session = open_session(region, ref)
    kw.setdefault("use_cftime", True)
    if engine == "rustytree":
        extra = {"group_filter": group_filter} if group_filter else {}
        return xr.open_datatree(session.store, engine="rustytree", **extra, **kw)
    if group_filter:
        raise ValueError("group_filter is only supported with engine='rustytree'")
    return xr.open_datatree(session.store, engine="zarr", consolidated=False, **kw)


def open_group(
    region: str, group_path: str, *, engine: str = "zarr", ref: str = "main", **kw: Any
) -> xr.Dataset:
    """Open one group (``<source>/<experiment>/<product>/<resolution>m``) as a Dataset.

    Time decodes to cftime by default: the series run to 2300, past datetime64[ns].
    """
    session = open_session(region, ref)
    kw.setdefault("use_cftime", True)
    if engine == "rustytree":
        return xr.open_dataset(session.store, engine="rustytree", group=group_path, **kw)
    return xr.open_zarr(session.store, group=group_path, consolidated=False, **kw)


def list_groups(region: str, ref: str = "main") -> list[str]:
    """Group paths holding data arrays, from the store listing."""
    from .store import existing_groups

    return sorted(existing_groups(open_repo(region), ref))


__all__ = ["list_groups", "open_group", "open_repo", "open_session", "open_store", "store_target"]
