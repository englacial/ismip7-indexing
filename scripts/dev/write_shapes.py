import time, pandas as pd
from ismip7_helper.conventions import parse_key
from ismip7_helper.virtualize import source_registry, concat_group
from ismip7_helper.store import StoreTarget, open_repository, write_group, verify_group, ensure_hierarchy
D="/tmp/claude-0/-home-user-ismip7-indexing/c378e836-2097-52d9-9f11-06a02384e3a8/scratchpad/devstore"
if __name__ == "__main__":
    df=pd.read_parquet(D+"/index.parquet")
    cases={
     "GrIS": df[(df.group_path=="CESM2-WACCM/historical/SDBN1-1000m") & df.variable.isin(["acabf","tas"]) & df.year_start.between(1850,1852)],
     "AIS":  df[(df.group_path=="CESM2-WACCM/historical/ocean") & df.variable.isin(["tf"]) & df.year_start.between(1850,1869)],
    }
    for region, sel in cases.items():
        files=[parse_key(k,int(s)).forcing for k,s in zip(sel.key, sel["size"])]
        t=time.time(); g=concat_group(files, source_registry(), max_workers=4, use_processes=True)
        print(f"{region} {files[0].group_path}: {len(files)} files virtualized in {time.time()-t:.1f}s -> {dict(g.ds.sizes)}; refs {g.ds.vz.nrefs()}; notes {len(g.notes)}", flush=True)
        repo=open_repository(StoreTarget(url=f"local:{D}/icechunk-{region.lower()}"), create=True)
        ensure_hierarchy(repo, [g.group_path], "main", region=region)
        wr=write_group(repo, g, overwrite=True); print("  committed", wr.snapshot_id, "refs", wr.nrefs, flush=True)
        t=time.time(); vr=verify_group(repo, g, n_files=1); print(f"  verify {time.time()-t:.1f}s ok={vr.ok} {vr.mismatches[:1]}", flush=True)
