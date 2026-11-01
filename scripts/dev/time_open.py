"""Time opening the local dev stores with engine="zarr" and engine="rustytree".

Usage: python scripts/dev/time_open.py DEVSTORE_DIR  (holds icechunk-ais/ and icechunk-gris/)
"""

import sys
import time

import xarray as xr

from ismip7_helper.store import StoreTarget, open_repository


def main(root: str) -> None:
    for region in ("ais", "gris"):
        repo = open_repository(StoreTarget(url=f"local:{root}/icechunk-{region}"), readonly=True)
        for engine in ("zarr", "rustytree"):
            for attempt in range(2):
                s = repo.readonly_session("main")
                t = time.time()
                try:
                    kw = {"consolidated": False} if engine == "zarr" else {}
                    dt = xr.open_datatree(s.store, engine=engine, **kw)
                    n = sum(1 for _ in dt.subtree)
                    leaf = next(g for g in dt.subtree if g.data_vars)
                    v = next(iter(leaf.data_vars))
                    t1 = time.time()
                    x = leaf[v].isel(time=0).values
                    t2 = time.time()
                    print(
                        f"{region:4s} {engine:9s} run{attempt}: open {t1 - t:.2f}s ({n} nodes), "
                        f"first timestep of {leaf.path}/{v} {t2 - t1:.2f}s, shape {x.shape}",
                        flush=True,
                    )
                except Exception as e:
                    print(
                        f"{region:4s} {engine:9s} run{attempt}: ERROR {type(e).__name__}: "
                        f"{str(e)[:200]}",
                        flush=True,
                    )


if __name__ == "__main__":
    main(sys.argv[1])
