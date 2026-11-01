"""Virtualize, write and verify a slice of the GrIS 1 km SMB and AIS ocean groups locally.

Usage: python scripts/dev/write_shapes.py DEVSTORE_DIR  (holds index.parquet; the stores
are written to DEVSTORE_DIR/icechunk-<region>)
"""

import sys
import time

import pandas as pd

from ismip7_helper.conventions import parse_key
from ismip7_helper.store import (
    StoreTarget,
    ensure_hierarchy,
    open_repository,
    verify_group,
    write_group,
)
from ismip7_helper.virtualize import concat_group


def main(root: str) -> None:
    df = pd.read_parquet(f"{root}/index.parquet")
    cases = {
        "GrIS": df[
            (df.group_path == "CESM2-WACCM/historical/SDBN1-1000m")
            & df.variable.isin(["acabf", "tas"])
            & df.year_start.between(1850, 1852)
        ],
        "AIS": df[
            (df.group_path == "CESM2-WACCM/historical/ocean")
            & df.variable.isin(["tf"])
            & df.year_start.between(1850, 1869)
        ],
    }
    for region, sel in cases.items():
        files = [parse_key(k, int(s)).forcing for k, s in zip(sel.key, sel["size"], strict=True)]
        t = time.time()
        g = concat_group(files, max_workers=4, use_processes=True)
        print(
            f"{region} {files[0].group_path}: {len(files)} files virtualized in "
            f"{time.time() - t:.1f}s -> {dict(g.ds.sizes)}; refs {g.ds.vz.nrefs()}; "
            f"notes {len(g.notes)}",
            flush=True,
        )
        repo = open_repository(
            StoreTarget(url=f"local:{root}/icechunk-{region.lower()}"), create=True
        )
        ensure_hierarchy(repo, [g.group_path], "main", region=region)
        wr = write_group(repo, g, overwrite=True)
        print("  committed", wr.snapshot_id, "refs", wr.nrefs, flush=True)
        t = time.time()
        vr = verify_group(repo, g, n_files=1)
        print(f"  verify {time.time() - t:.1f}s ok={vr.ok} {vr.mismatches[:1]}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
