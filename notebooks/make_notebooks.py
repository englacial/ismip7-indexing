"""Generate the example notebooks (kept as code so they are reviewable and regenerable).

python notebooks/make_notebooks.py          # writes notebooks/*.ipynb
ISMIP7_STORE_AIS=local:/path/icechunk-ais python notebooks/make_notebooks.py --execute
"""

from __future__ import annotations

import argparse
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).parent

INTRO = """\
The ISMIP7 forcing datasets on [source.coop](https://source.coop/ismip) are ~285,000
NetCDF files. The [englacial/ismip7](https://source.coop/englacial/ismip7) Icechunk
stores hold *virtual references* into those files: no data is copied, every group
(`<ESM>/<experiment>/<product>`) is one xarray Dataset with a normalised time axis,
exact standard-grid coordinates and a correct `crs`. Reading is anonymous.

Set `ISMIP7_STORE_AIS` / `ISMIP7_STORE_GRIS` to `local:/path/to/repo` to run these
notebooks against a local dev store.
"""

SETUP = """\
import os
import time

from ismip7_helper.access import list_groups, store_target


def read(a, tries=3):
    \"\"\"Load a lazy array and return the seconds taken; retry on transient object-store errors.\"\"\"
    for attempt in range(tries):
        t0 = time.time()
        try:
            a.load()
            return round(time.time() - t0, 2)
        except Exception as e:
            if attempt == tries - 1:
                raise
            print("retry after", type(e).__name__)


REGION = os.environ.get("ISMIP7_NB_REGION", "AIS")
print("store:", store_target(REGION).url)
groups = list_groups(REGION)
print(len(groups), "groups; first:", groups[:5])
GROUP = os.environ.get("ISMIP7_NB_GROUP", groups[0])
"""

OPEN_GROUP = """\
from ismip7_helper.access import open_group

ds = open_group(REGION, GROUP)
ds
"""

GROUP_INFO = """\
print(ds.attrs.get("ismip7_grid"), "|", ds["crs"].attrs["epsg_code"])
print("time:", str(ds.time.values[0])[:10], "->", str(ds.time.values[-1])[:10], dict(ds.sizes))
"""

PLOT = """\
import numpy as np

var = next(v for v in ds.data_vars if ds[v].ndim >= 3)
field = ds[var].isel(time=0)
secs = read(field)
print(f"{var} t=0 read in {secs:.2f}s; finite cells {int(np.isfinite(field).sum())}")
field.plot(x="x", y="y", robust=True, figsize=(6, 6));
"""

SUBSET = """\
win = ds[var].isel(time=slice(0, 12)).sel(x=slice(None, None, 4), y=slice(None, None, 4))
annual = win.mean("time").compute()
print(annual.shape, float(np.nanmean(annual)))
"""

TREE = """\
from ismip7_helper.access import open_store

t0 = time.time()
dt = open_store(REGION)
print(f"{sum(1 for _ in dt.subtree)} nodes in {time.time() - t0:.2f}s")
print([g.path for g in dt.subtree if g.data_vars][:10])
"""

RUSTY_TIMING = """\
from ismip7_helper.access import open_store

timings = {}
for engine in ("zarr", "rustytree"):
    for run in range(2):
        t0 = time.time()
        tree = open_store(REGION, engine=engine)
        dt_open = time.time() - t0
        leaf = next(g for g in tree.subtree if g.data_vars)
        var = next(iter(leaf.data_vars))
        dt_read = read(leaf[var].isel(time=0))
        timings[(engine, run)] = (dt_open, dt_read)
        print(f"{engine:9s} run{run}: open {dt_open:.2f}s, first timestep {dt_read:.2f}s ({leaf.path}/{var})")
"""

RUSTY_FILTER = """\
pattern = "/" + GROUP.split("/")[0] + "/*/*"
sub = open_store(REGION, engine="rustytree", group_filter=pattern)
print(pattern, "->", [g.path for g in sub.subtree if g.data_vars][:10])
"""

RUSTY_GROUP = """\
from ismip7_helper.access import open_group

ds = open_group(REGION, GROUP, engine="rustytree")
ds
"""

PROFILE = """\
import pandas as pd
import xarray as xr

from ismip7_helper.access import open_repo

results = []
for gp in groups[:10]:
    t0 = time.time()
    sess = open_repo(REGION).readonly_session(branch="main")
    t_open = round(time.time() - t0, 2)
    t0 = time.time()
    ds = xr.open_zarr(sess.store, group=gp, consolidated=False)
    t_group = round(time.time() - t0, 2)
    var = next(v for v in ds.data_vars if ds[v].ndim >= 3)
    t_first = read(ds[var].isel(time=0))
    n = min(12, ds.sizes["time"])
    t_year = read(ds[var].isel(time=slice(0, n)))
    results.append(
        {
            "group": gp,
            "var": var,
            "steps": ds.sizes["time"],
            "chunks": ds[var].encoding.get("chunks"),
            "open": t_open,
            "group_open": t_group,
            "first_step": t_first,
            "twelve_steps": t_year,
        }
    )
pd.DataFrame(results)
"""

MANIFESTS = """\
repo = open_repo(REGION)
sess = repo.readonly_session(branch="main")
sizes = sorted((m.size_bytes for m in repo.list_manifest_files(sess.snapshot_id)), reverse=True)
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
        ("code", SETUP),
        (
            "md",
            "## One group as a Dataset\n\nTime is `days since 1850-01-01` (standard calendar) decoded to "
            "datetime64; `x`/`y` are the exact ISMIP7 grid; `crs` carries the EPSG code and WKT.",
        ),
        ("code", OPEN_GROUP),
        ("code", GROUP_INFO),
        (
            "md",
            "## Plot one month\n\nOnly the chunk for that timestep is fetched from the source file "
            "(fill value is masked to NaN).",
        ),
        ("code", PLOT),
        (
            "md",
            "## Subset and reduce\n\nA spatial window and an annual mean over the first year; everything "
            "stays lazy until `.compute()`.",
        ),
        ("code", SUBSET),
        (
            "md",
            "## The whole store as a DataTree\n\n`open_store` walks every group; with many groups prefer "
            "`engine='rustytree'` (next notebook).",
        ),
        ("code", TREE),
    ],
)

NB2 = nb(
    "Fast opening with rustytree",
    [
        ("code", SETUP),
        (
            "md",
            "[rustytree](https://github.com/aladinor/rustytree) walks the Zarr v3 hierarchy in Rust, "
            "concurrently, and plugs into xarray as `engine='rustytree'`. The ISMIP7 stores are Icechunk "
            "spec v2 with time-split manifests, which it reads directly.",
        ),
        ("code", RUSTY_TIMING),
        (
            "md",
            "## Filter groups while opening\n\n`group_filter` (rustytree only) returns a tree with just the "
            "matching groups, e.g. one ESM's scenarios.",
        ),
        ("code", RUSTY_FILTER),
        ("code", RUSTY_GROUP),
    ],
)

NB3 = nb(
    "Performance profile",
    [
        ("code", SETUP),
        (
            "md",
            "What a reader pays: cold open of the repository, opening one group, first chunk, and a "
            "full-year read; plus how big the manifests are (what icechunk-js in a browser must download "
            "before the first timestep).",
        ),
        ("code", PROFILE),
        (
            "md",
            "## Manifest sizes\n\nIcechunk splits each array's manifest every 120 chunks along time; a "
            "client reads one split per timestep it touches.",
        ),
        ("code", MANIFESTS),
    ],
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--execute", action="store_true", help="execute the notebooks in place (needs a store)"
    )
    args = ap.parse_args()
    out = {"01_open_and_use.ipynb": NB1, "02_rustytree.ipynb": NB2, "03_profile.ipynb": NB3}
    for name, n in out.items():
        path = HERE / name
        if args.execute:
            from nbclient import NotebookClient

            NotebookClient(
                n, timeout=900, kernel_name="python3", resources={"metadata": {"path": str(HERE)}}
            ).execute()
        nbf.write(n, path)
        print("wrote", path, "(executed)" if args.execute else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
