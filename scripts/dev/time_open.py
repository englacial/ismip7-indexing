import time, xarray as xr
from ismip7_helper.store import StoreTarget, open_repository
D="/tmp/claude-0/-home-user-ismip7-indexing/c378e836-2097-52d9-9f11-06a02384e3a8/scratchpad/devstore"
for region in ("ais","gris"):
    repo=open_repository(StoreTarget(url=f"local:{D}/icechunk-{region}"), readonly=True)
    for engine in ("zarr","rustytree"):
        for attempt in range(2):
            s=repo.readonly_session("main"); t=time.time()
            try:
                dt=xr.open_datatree(s.store, engine=engine, consolidated=False) if engine=="zarr" else xr.open_datatree(s.store, engine=engine)
                n=sum(1 for _ in dt.subtree); leaf=[g for g in dt.subtree if g.data_vars][0]
                v=next(iter(leaf.data_vars)); t1=time.time()
                x=leaf[v].isel(time=0).values; t2=time.time()
                print(f"{region:4s} {engine:9s} run{attempt}: open {t1-t:.2f}s ({n} nodes), first timestep of {leaf.path}/{v} {t2-t1:.2f}s, shape {x.shape}", flush=True)
            except Exception as e:
                print(f"{region:4s} {engine:9s} run{attempt}: ERROR {type(e).__name__}: {str(e)[:200]}", flush=True)
