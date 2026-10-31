# Dev-store viewer spike

Scripts used to check the viewers against a local Icechunk repository whose
virtual chunks point at the public source.coop bucket (no credentials needed).

1. Build a small real-data store (e.g. three years of two variables of one group):
   ```python
   from ismip7_helper.virtualize import source_registry, concat_group
   from ismip7_helper.store import StoreTarget, open_repository, write_group

   # files: a subset of ForcingFile records from the index
   g = concat_group(files, source_registry())
   repo = open_repository(StoreTarget(url="local:/path/devstore/icechunk-ais"), create=True)
   write_group(repo, g)
   ```
2. Serve it with CORS and Range support: `python serve_cors.py /path/devstore 8765`
3. Read it with icechunk-js in Node (needs a built icechunk-js checkout on the module path):
   `node read_with_icechunk_js.mjs http://127.0.0.1:8765/icechunk-ais CESM2-WACCM/historical/SDBN1-8000m acabf`
4. Open the hosted gridlook on it in headless Chromium and take a screenshot:
   `python gridlook_headless.py "icechunk+http://127.0.0.1:8765/icechunk-ais/CESM2-WACCM/historical/SDBN1-8000m::varname=acabf" out.png`

Result on 2026-10-31 (icechunk-js 0.6.1, gridlook 1.8.1): the spec-v2 repo with
time-split manifests opens, the polar stereographic grid is detected from the
`crs` variable, time decodes, fill is masked, and a virtual chunk is fetched
from the bucket by range request in under a second. See
`docs/images/gridlook-devstore-acabf.png`.
