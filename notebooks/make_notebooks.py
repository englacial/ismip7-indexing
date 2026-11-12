"""Generate the example notebooks (kept as code so they are reviewable and regenerable).

python notebooks/make_notebooks.py            # writes notebooks/*.ipynb
python notebooks/make_notebooks.py --execute  # also runs them (reads the public store)

The notebooks use only icechunk, zarr and xarray, no helper from this package,
so they double as the reader's guide to the published stores.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).parent

INTRO = """\
The ISMIP7 forcing datasets on [source.coop](https://source.coop/ismip) are about
285,000 NetCDF files (6 TB). The [englacial/ismip7](https://source.coop/englacial/ismip7)
Icechunk stores hold *virtual references* into those files (22 GB of metadata, no data
copied). One store per ice sheet, `icechunk-ais` and `icechunk-gris`, laid out as

    <ESM>/<experiment>/<product>/<resolution>m      e.g. CESM2-WACCM/ssp585/SDBN1/2000m

Each resolution group is one xarray Dataset: a normalised monthly (or annual) time axis
in `days since 1850-01-01`, the exact ISMIP7 grid in `x`/`y`, and a `crs` variable with
the EPSG code and WKT. The group above the resolutions (the *family*, e.g.
`CESM2-WACCM/ssp585/SDBN1`) carries an OME-NGFF `multiscales` attribute naming its
levels, so a multiscale-aware reader (gridlook) opens the family as one pyramid.

Reading needs no credentials. Everything below uses the standard tools, `icechunk`,
`zarr` and `xarray`; nothing from `ismip7_helper`.
"""

SETUP = """\
import os
import time

import icechunk
import xarray as xr
import zarr

REGION = os.environ.get("ISMIP7_NB_REGION", "AIS")  # or "GrIS"
BUCKET = "us-west-2.opendata.source.coop"
PREFIX = f"englacial/ismip7/icechunk-{REGION.lower()}"
# the bucket the virtual chunks point into; icechunk only follows references into
# containers you authorise explicitly
SOURCE = "s3://us-west-2.opendata.source.coop/ismip/"


def open_repo():
    storage = icechunk.s3_storage(bucket=BUCKET, prefix=PREFIX, region="us-west-2", anonymous=True)
    return icechunk.Repository.open(
        storage, authorize_virtual_chunk_access={SOURCE: icechunk.s3_anonymous_credentials()}
    )


def read(a, tries=3):
    \"\"\"Seconds to load an xarray object into memory (retrying transient S3 errors).\"\"\"
    for attempt in range(tries):
        t0 = time.time()
        try:
            a.load()
            return time.time() - t0
        except Exception:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            time.sleep(2)


repo = open_repo()
session = repo.readonly_session("main")
print("store:", f"s3://{BUCKET}/{PREFIX}", "| snapshot", session.snapshot_id)
"""

LIST_GROUPS = """\
root = zarr.open_group(session.store, mode="r")

families, levels = {}, []
for path, node in root.members(max_depth=None):
    if not isinstance(node, zarr.Group):
        continue
    if "multiscales" in node.attrs:
        families[path] = [d["path"] for d in node.attrs["multiscales"][0]["datasets"]]
    elif node.attrs.get("ismip7_group"):
        levels.append(path)
print(len(levels), "data groups,", len(families), "families")
for fam, lv in list(families.items())[:8]:
    print(f"  {fam}: {lv}")
FAMILY = os.environ.get("ISMIP7_NB_FAMILY", "CESM2-WACCM/historical/SDBN1")
GROUP = f"{FAMILY}/{families[FAMILY][-1]}"  # the coarsest level
print("using", GROUP)
"""

OPEN_FAMILY = """\
# a family as a DataTree: one child per resolution, finest first in `multiscales`
dt = xr.open_datatree(
    session.store, group=FAMILY, engine="zarr", consolidated=False, use_cftime=True
)
print(dt.attrs["multiscales"][0]["datasets"])
print({child: dict(dt[child].ds.sizes) for child in dt.children})
"""

OPEN_GROUP = """\
# one resolution as a Dataset (use_cftime: the series run to 2300, past datetime64[ns])
ds = xr.open_zarr(session.store, group=GROUP, consolidated=False, use_cftime=True)
ds
"""

GROUP_INFO = """\
print("time:", ds.time.values[0], "->", ds.time.values[-1], f"({ds.sizes['time']} steps)")
print("grid:", ds.attrs.get("ismip7_grid"), "| crs:", ds.crs.attrs.get("epsg_code"))
var = next(v for v in ds.data_vars if ds[v].ndim >= 3)
print(var, "chunks", ds[var].encoding.get("chunks"), "| actual_range", ds[var].attrs.get("actual_range"))
for n in ds.attrs.get("ismip7_notes", [])[:5]:
    print(" note:", n)
"""

PLOT = """\
import numpy as np

field = ds[var].isel(time=0)
secs = read(field)
print(f"{var} t=0 read in {secs:.2f}s; finite cells {int(np.isfinite(field).sum())}")
field.plot(x="x", y="y", robust=True, figsize=(6, 6));
"""

SUBSET = """\
n = min(12, ds.sizes["time"])
box = ds[var].isel(time=slice(0, n), x=slice(100, 300), y=slice(100, 300))
secs = read(box)
print(f"{n} steps x 200 x 200 cells read in {secs:.2f}s")
box.mean("time").plot(x="x", y="y", robust=True, figsize=(5, 5));
"""

TREE_FAMILY = """\
# the family is small and opens in a moment; an ESM or the whole store means one
# metadata read per group (hundreds), so scope the tree to what you need
t0 = time.time()
dt = xr.open_datatree(
    session.store, group=FAMILY, engine="zarr", consolidated=False, use_cftime=True
)
print(f"{FAMILY}: {len(list(dt.subtree))} nodes in {time.time() - t0:.1f}s")
for node in dt.subtree:
    if node.data_vars:
        print(" ", node.path, sorted(node.data_vars)[:6], "...")
"""

TREE_ESM = """\
ESM = FAMILY.split("/")[0]
t0 = time.time()
dt = xr.open_datatree(
    session.store, group=ESM, engine="zarr", consolidated=False, use_cftime=True
)
leaves = [n.path for n in dt.subtree if n.data_vars]
print(f"{ESM}: {len(leaves)} data groups in {time.time() - t0:.1f}s")
print(leaves[:12])
"""

RUSTY = """\
# Optional: rustytree (https://github.com/aladinor/rustytree) walks a Zarr v3 hierarchy
# in Rust, concurrently, as `engine="rustytree"`; install it from its repository
# (pip install git+https://github.com/aladinor/rustytree) to compare.
try:
    import rustytree  # noqa: F401
except ImportError:
    print("rustytree is not installed; skipping")
else:
    for engine in ("zarr", "rustytree"):
        t0 = time.time()
        dt = xr.open_datatree(session.store, group=ESM, engine=engine, use_cftime=True)
        print(f"{engine:9s}: {sum(1 for _ in dt.subtree)} nodes in {time.time() - t0:.1f}s")
"""

PROFILE = """\
import pandas as pd

results = []
for gp in levels[:10]:
    t0 = time.time()
    sess = open_repo().readonly_session("main")
    t_open = round(time.time() - t0, 2)
    t0 = time.time()
    d = xr.open_zarr(sess.store, group=gp, consolidated=False, use_cftime=True)
    t_group = round(time.time() - t0, 2)
    v = next(x for x in d.data_vars if d[x].ndim >= 3)
    t_first = read(d[v].isel(time=0))
    n = min(12, d.sizes["time"])
    t_year = read(d[v].isel(time=slice(0, n)))
    results.append(
        {
            "group": gp,
            "var": v,
            "steps": d.sizes["time"],
            "chunks": d[v].encoding.get("chunks"),
            "open": t_open,
            "group_open": t_group,
            "first_step": round(t_first, 2),
            "twelve_steps": round(t_year, 2),
        }
    )
pd.DataFrame(results)
"""

MANIFESTS = """\
sizes = sorted((m.size_bytes for m in repo.list_manifest_files(session.snapshot_id)), reverse=True)
print(
    f"{len(sizes)} manifests; total {sum(sizes) / 1e6:.1f} MB; "
    f"largest {sizes[0] / 1e6:.2f} MB; median {sizes[len(sizes) // 2] / 1e3:.0f} kB"
)
"""


def nb(title: str, cells: list[tuple[str, str]]) -> nbf.NotebookNode:
    n = nbf.v4.new_notebook()
    n.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    n.cells.append(nbf.v4.new_markdown_cell(f"# {title}\n\n{INTRO}"))
    for kind, src in cells:
        n.cells.append(nbf.v4.new_markdown_cell(src) if kind == "md" else nbf.v4.new_code_cell(src))
    return n


NB1 = nb(
    "Open and use the ISMIP7 store",
    [
        (
            "md",
            "## Open the repository\n\nThree things a reader must get right: the bucket and prefix, "
            "`anonymous=True` (otherwise icechunk looks for AWS credentials), and authorising the "
            "virtual-chunk container, since icechunk refuses to follow references into a bucket you "
            "have not allowed.",
        ),
        ("code", SETUP),
        (
            "md",
            "## What is in the store\n\nWalk the hierarchy once: family groups carry `multiscales`, "
            "data groups carry `ismip7_group`.",
        ),
        ("code", LIST_GROUPS),
        ("md", "## A family as a DataTree, a level as a Dataset"),
        ("code", OPEN_FAMILY),
        ("code", OPEN_GROUP),
        ("code", GROUP_INFO),
        (
            "md",
            "## Plot one month\n\nOnly the chunk(s) of that time step are fetched from the source file; "
            "the fill value is masked to NaN.",
        ),
        ("code", PLOT),
        (
            "md",
            "## Subset and reduce\n\nA spatial window and a mean over the first year; everything stays "
            "lazy until it is loaded.",
        ),
        ("code", SUBSET),
    ],
)

NB2 = nb(
    "Browse the hierarchy as a DataTree",
    [
        ("code", SETUP),
        ("code", LIST_GROUPS),
        (
            "md",
            "## Scope the tree\n\n`xr.open_datatree` reads the metadata of every group below the one "
            "you open, so open the family or the ESM you need rather than the whole store.",
        ),
        ("code", TREE_FAMILY),
        ("code", TREE_ESM),
        ("md", "## Optional: a faster walker"),
        ("code", RUSTY),
    ],
)

NB3 = nb(
    "Performance profile",
    [
        ("code", SETUP),
        ("code", LIST_GROUPS),
        (
            "md",
            "What a reader pays: opening the repository, opening one group, the first time step, and a "
            "full year; plus how big the manifests are (what a browser client downloads before the "
            "first step).",
        ),
        ("code", PROFILE),
        (
            "md",
            "## Manifest sizes\n\nIcechunk splits each array's manifest every 120 chunks along time; a "
            "client reads one split per time step it touches.",
        ),
        ("code", MANIFESTS),
    ],
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--execute", action="store_true", help="execute the notebooks in place (reads the store)"
    )
    args = ap.parse_args()
    out = {"01_open_and_use.ipynb": NB1, "02_datatree.ipynb": NB2, "03_profile.ipynb": NB3}
    for name, n in out.items():
        path = HERE / name
        if args.execute:
            from nbclient import NotebookClient

            NotebookClient(
                n, timeout=900, kernel_name="python3", resources={"metadata": {"path": str(HERE)}}
            ).execute()
        nbf.write(n, path)
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
