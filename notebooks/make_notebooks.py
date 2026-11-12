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

REGION = os.environ.get("ISMIP7_NB_REGION", "AIS")  # or "GrIS"
BUCKET = "us-west-2.opendata.source.coop"
PREFIX = f"englacial/ismip7/icechunk-{REGION.lower()}"
# the bucket the virtual chunks point into; icechunk only follows references into
# containers you authorise explicitly
SOURCE = "s3://us-west-2.opendata.source.coop/ismip/"


# the series run to 2300, past datetime64[ns]: decode time with cftime
TIME = xr.coders.CFDatetimeCoder(use_cftime=True)


def read_time(a, tries=3):
    \"\"\"Seconds to load an xarray object into memory (retrying transient S3 errors).\"\"\"
    for attempt in range(tries):
        t0 = time.time()
        try:
            a.load()
            return time.time() - t0
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2)


storage = icechunk.s3_storage(bucket=BUCKET, prefix=PREFIX, region="us-west-2", anonymous=True)
containers = icechunk.containers_credentials({SOURCE: icechunk.s3_anonymous_credentials()})
repo = icechunk.Repository.open(storage, authorize_virtual_chunk_access=containers)
session = repo.readonly_session("main")
print("store:", f"s3://{BUCKET}/{PREFIX}", "| snapshot", session.snapshot_id)
"""

LIST_GROUPS = """\
import zarr

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
    session.store, group=FAMILY, engine="zarr", consolidated=False, decode_times=TIME
)
print(dt.attrs["multiscales"][0]["datasets"])
print({child: dict(dt[child].ds.sizes) for child in dt.children})
"""

OPEN_GROUP = """\
# one resolution as a Dataset
ds = xr.open_zarr(session.store, group=GROUP, consolidated=False, decode_times=TIME)
ds
"""

GROUP_INFO = """\
var = next(v for v in ds.data_vars if ds[v].ndim >= 3)
tdim = ds[var].dims[0]  # `time`, or `time_annual` for the annual products
print(tdim, ds[tdim].values[0], "->", ds[tdim].values[-1], f"({ds.sizes[tdim]} steps)")
print("grid:", ds.attrs.get("ismip7_grid"), "| crs:", ds.crs.attrs.get("epsg_code"))
print(var, "chunks", ds[var].encoding.get("chunks"))
print("actual_range", ds[var].attrs.get("actual_range"))
for n in ds.attrs.get("ismip7_notes", [])[:5]:
    print(" note:", n)
"""

PLOT = """\
import numpy as np

field = ds[var].isel({tdim: 0})
secs = read_time(field)
print(f"{var} t=0 read in {secs:.2f}s; finite cells {int(np.isfinite(field).sum())}")
field.plot(x="x", y="y", robust=True, figsize=(6, 6));
"""

SUBSET = """\
n = min(12, ds.sizes[tdim])
box = ds[var].isel({tdim: slice(0, n), "x": slice(100, 300), "y": slice(100, 300)})
secs = read_time(box)
print(f"{n} steps x 200 x 200 cells read in {secs:.2f}s")
box.mean(tdim).plot(x="x", y="y", robust=True, figsize=(5, 5));
"""

ENGINE = """\
# rustytree (https://github.com/aladinor/rustytree, `pip install rustytree-xarray`) walks the
# hierarchy concurrently in Rust and reads arrays through its own Rust path, so a whole
# store opens in seconds; the zarr engine does the same work one group at a time
if "rustytree" in xr.backends.list_engines():
    TREE = {"engine": "rustytree"}
else:
    print("rustytree is not installed: using the zarr engine (slower on large trees)")
    TREE = {"engine": "zarr", "consolidated": False}
"""

TREE_STORE = """\
# the whole store as one lazy tree: metadata only, nothing read until asked
t0 = time.time()
tree = xr.open_datatree(session.store, decode_times=TIME, **TREE)
data = [n for n in tree.subtree if n.data_vars]
families = {
    n.path.strip("/"): [d["path"] for d in n.attrs["multiscales"][0]["datasets"]]
    for n in tree.subtree
    if "multiscales" in n.attrs
}
print(
    f"{REGION}: {len(data)} data groups in {len(families)} families, "
    f"{tree.nbytes / 1e12:.1f} TB virtual, opened in {time.time() - t0:.1f}s"
)
for fam, lv in list(families.items())[:8]:
    print(f"  {fam}: {lv}")
"""

TREE_FAMILY = """\
# a family: one child per resolution, finest first in `multiscales`
FAMILY = os.environ.get("ISMIP7_NB_FAMILY", "CESM2-WACCM/historical/SDBN1")
family = tree[FAMILY]
print(family.attrs["multiscales"][0]["datasets"])
print({child: dict(family[child].ds.sizes) for child in family.children})
"""

TREE_GROUP = """\
# one resolution as a Dataset (the coarsest level of the family)
GROUP = f"{FAMILY}/{families[FAMILY][-1]}"
ds = tree[GROUP].to_dataset()
ds
"""

TREE_BOTH = """\
# both ice sheets as one tree: AIS and GrIS are separate Icechunk repositories, so open
# each and nest them
trees = {}
for region in ("AIS", "GrIS"):
    prefix = f"englacial/ismip7/icechunk-{region.lower()}"
    stor = icechunk.s3_storage(bucket=BUCKET, prefix=prefix, region="us-west-2", anonymous=True)
    sess = icechunk.Repository.open(stor, authorize_virtual_chunk_access=containers)
    trees[region] = xr.open_datatree(sess.readonly_session("main").store, decode_times=TIME, **TREE)
both = xr.DataTree.from_dict(trees)
n_data = {r: sum(1 for n in t.subtree if n.data_vars) for r, t in trees.items()}
print(sum(1 for _ in both.subtree), "nodes;", n_data)
print(both["GrIS/CESM2-WACCM/historical/SDBN1"].attrs["multiscales"][0]["datasets"])
"""

TREE_FILTER = """\
# scope a walk with a glob over full group paths (leading slash): e.g. every historical
# 8 km group of this store (rustytree only; the zarr engine ignores group_filter)
if TREE["engine"] == "rustytree":
    t0 = time.time()
    hist8 = xr.open_datatree(
        session.store, group_filter="/*/historical/*/8000m", decode_times=TIME, **TREE
    )
    print([n.path for n in hist8.subtree if n.data_vars], f"in {time.time() - t0:.1f}s")
"""

PROFILE = """\
import pandas as pd

# the "year" read is a 200 x 200 cell window so the notebook stays small enough for
# Binder (a full year of a 2 km field is 444 MB, of the 3-D ocean fields 834 MB)
ENGINES = ["zarr"]
try:
    import rustytree  # noqa: F401

    ENGINES.append("rustytree")
except ImportError:
    pass

results = []
for gp in levels[:10]:
    for engine in ENGINES:
        t0 = time.time()
        r = icechunk.Repository.open(storage, authorize_virtual_chunk_access=containers)
        sess = r.readonly_session("main")
        t_open = round(time.time() - t0, 2)
        t0 = time.time()
        if engine == "zarr":
            d = xr.open_zarr(sess.store, group=gp, consolidated=False, decode_times=TIME)
        else:
            d = xr.open_dataset(sess, group=gp, engine=engine, decode_times=TIME)
        t_group = round(time.time() - t0, 2)
        v = next(x for x in d.data_vars if d[x].ndim >= 3)
        tdim = d[v].dims[0]
        t_first = read_time(d[v].isel({tdim: 0}))
        n = min(12, d.sizes[tdim])
        window = {tdim: slice(0, n), "x": slice(100, 300), "y": slice(100, 300)}
        t_year = read_time(d[v].isel(window))
        results.append(
            {
                "group": gp,
                "engine": engine,
                "var": v,
                "steps": d.sizes[tdim],
                "chunks": d[v].encoding.get("chunks"),
                "open": t_open,
                "group_open": t_group,
                "first_step": round(t_first, 2),
                "year_window": round(t_year, 2),
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
        n.cells.append(
            nbf.v4.new_markdown_cell(src)
            if kind == "md"
            else nbf.v4.new_code_cell(src.rstrip("\n"))
        )
    return n


NB1 = nb(
    "Open and use the ISMIP7 store",
    [
        (
            "md",
            "## Open the repository\n\nThree things a reader must get right: the bucket and prefix, "
            "`anonymous=True` (otherwise icechunk looks for AWS credentials), and authorising the "
            "virtual-chunk container (wrapped by `containers_credentials`), since icechunk refuses to "
            "follow references into a bucket you have not allowed.",
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
    "ISMIP7 forcing as a DataTree",
    [
        (
            "md",
            "## Open the repository\n\nThree things a reader must get right: the bucket and prefix, "
            "`anonymous=True` (otherwise icechunk looks for AWS credentials), and authorising the "
            "virtual-chunk container (wrapped by `containers_credentials`), since icechunk refuses to "
            "follow references into a bucket you have not allowed.",
        ),
        ("code", SETUP),
        ("code", ENGINE),
        (
            "md",
            "## The whole store as one tree\n\nGroups are `ESM/experiment/family/resolution`; a family "
            "group carries OME-NGFF `multiscales` naming its resolution levels, finest first.",
        ),
        ("code", TREE_STORE),
        ("md", "## A family, then one resolution as a Dataset"),
        ("code", TREE_FAMILY),
        ("code", TREE_GROUP),
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
        ("md", "## Both ice sheets in one tree"),
        ("code", TREE_BOTH),
        ("md", "## Scope a walk with a glob"),
        ("code", TREE_FILTER),
    ],
)

NB3 = nb(
    "Performance profile",
    [
        ("code", SETUP),
        ("code", LIST_GROUPS),
        (
            "md",
            "What a reader pays: opening the repository, opening one group, the first time step of the "
            "first 3-D variable, and a year of a 200 x 200 window; with the zarr engine and, when it is "
            "installed, [rustytree](https://github.com/aladinor/rustytree), whose arrays read through "
            "its own Rust path (zarrs + icechunk) rather than Python zarr. Then how big the manifests "
            "are (what a browser client downloads before the first step).",
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
