# ISMIP7 virtualization and viewer: findings and plan

Prepared 2026-10-31 against SOW No. 1 (draft, 80 h ceiling). Everything below about the data was measured directly from the public `us-west-2.opendata.source.coop` bucket (anonymous S3 listing plus HDF5 header reads of ~25 representative files); everything about conventions comes from the ISMIP7 protocol page and the ISMIP GitHub repositories cited inline. The protocol overview spreadsheet (AISFileVersion / GrISFileVersion tabs, status log) and the website's variable request have been fetched and cross-checked against the bucket (Section 1d). The cheat sheet (August 2026), the filenames-and-conventions document (June 2026, revised 27 August), the Globus manual and the two ocean focus-group documents have also been read; the forcing-file conventions in Section 1e come from the second of these.

## 1. What is on source.coop under `ismip/`

| Product | Objects | Size | What it is |
|---|---|---|---|
| `ismip7-ais-forcing` | 108,752 | 3.08 TB | Antarctic atmospheric (SMB) and ocean forcing, 1850–2300 |
| `ismip7-gris-forcing` | 176,351 | 2.90 TB | Greenland atmospheric, ocean and subglacial-discharge forcing |
| `ismip7-ais-observations` | 53 | 9.5 GB | Observations: one 9.1 GB NetCDF3 "MIPkit" file at 500 m / 1 km, BedMachine/IMBIE on the 8 km grid, calving shapefiles and CSVs |
| `ismip7-ais-melt-calibration` | 167 | 25 GB | Ocean-model and observational calibration targets for the AIS melt parameterisation (mixed CC-BY / CC-BY-NC) |
| `ismip7-uaf-pism` | 429 | 226 GB | **First model submission** (UAF PISM, Greenland, 11 config sets C001–C011), including the compliance-checker logs |

Everything is NetCDF (285,000 `.nc` files). Nothing is Zarr. There is no Icechunk store, no STAC, no index. Roughly 6 TB for the two forcing products together, versus 1.1 TB for the whole ISMIP6 AIS output archive we virtualized last time.

### 1a. Forcing layout (the Task 1 target)

Both forcing products share one layout:

```
<product>/data/<ESM>/<experiment>/<forcing-product>/<variable>/<variable>_<region>_<ESM>_<experiment>_<forcing-product>_v<N>_<YYYY>.nc
```

One file per calendar year, 12 monthly timesteps, one variable per file. Examples:

```
ismip7-ais-forcing/data/CESM2-WACCM/ssp585/SDBN1-2000m/acabf/acabf_AIS_CESM2-WACCM_ssp585_SDBN1-2000m_v2_2150.nc
ismip7-gris-forcing/data/MRI-ESM2-0/historical/dEBM2-1000m/tas/tas_GrIS_MRI-ESM2-0_historical_dEBM2-1000m_v1_1900.nc
```

**ESMs and experiments.** These are *input forcings*, not submissions: each ESM directory holds CMIP6 climate-model output that the ISMIP7 focus groups downscaled onto the ice-sheet grids. `CESM2-WACCM` and `MRI-ESM2-0` are the two "full" ESMs (historical 1850–2014; ssp126, ssp585, ssp534-over, ctrl 2015–2300; ssp370 and ssp245 2015–2100). `ACCESS-CM2`, `CanESM5`, `GFDL-ESM4`, `IPSL-CM6A-LR`, `MPI-ESM1-2-HR` have historical + ssp370 only, and only via dEBM2. `OCX` holds the observational-era forcing (RACMO2.3p2-ERA SMB 1979–2025, EN4 ocean for GrIS, and four AIS ocean "pathways" cold/main/vary/warm). This matches the experiment table shipped with the ISMIP7 compliance checker (`experiments_ismip7.csv`: historical, ssp126/245/370/534-over/585, ctrl, ocx).

**Forcing products (the third directory level).** Each is an SMB or ocean model at a resolution:

| Region | Products | Grid |
|---|---|---|
| AIS | `SDBN1-8000m`, `SDBN1-2000m`, `GEMB-SDBN1-8000m`, `GEMB-SDBN1-2000m`, `dEBM2-8000m` | EPSG:3031, 761×761 @ 8 km, 3041×3041 @ 2 km |
| AIS | `ocean` (thetao, so, tf; 30 z-levels, decadal files `_YYYY-YYYY`) | 761×761 @ 8 km × 30 levels |
| GrIS | `SDBN1-1000m/4000m`, `GEMB-SDBN1-1000m/4000m`, `dEBM2-1000m/4000m` | EPSG:3413, 1681×2881 @ 1 km, 421×721 @ 4 km |
| GrIS | `ocean-1000m`, `ocean-4000m` (tf, so) | same grids |

The grids match the ISMIP7 grid definition files in the checker (`gdf_ISMIP7_AIS_08000m.txt`, `gdf_ISMIP7_GrIS_01000m.txt`, etc.). SMB variables per product: `acabf`, `acabf-anomaly`, `pr`, `tas`, `tas-anomaly`, `ts`, `ts-anomaly`, `mrro`, `dacabfdz`, `dtsdz`, `dmrrodz` (plus `sgd` for Greenland, `dtasdz` for OCX). Each product also carries `extra/climatology/` (one file per variable, 1960–1989, dim `month` not `time`) and the AIS ocean has `extras/bias` and `extras/climatology`.

Side trees that do not follow the pattern: `<ESM>/<exp>/fracture/` (hydrofracture inputs: `lake_properties_*`, `ice_shelf_collapse_mask_*`, `excess_melt_*`; 350-year `year` dimension, no CRS variable), `data/grid/ocean/ISMIP7/8km-60m/` (the AIS ocean grid definition), and `data/parameterisations/fracture/` (surface roughness, NetCDF3).

File versions (`v1`, `v2`, `v3`) are consistent within a directory (checked: no directory mixes versions), so no deduplication is needed, but the version should become a group attribute.

### 1b. Internal structure and the problems a pipeline must absorb

Measured from HDF5 headers:

| File class | Data chunking | Compression | Notes |
|---|---|---|---|
| AIS 8 km SMB (SDBN1, dEBM2, GEMB) | `(1, 761, 761)` | gzip 5 + shuffle | ideal: one chunk per timestep |
| AIS 2 km SDBN1 | `(1, 21, 3041)` | gzip 1 + shuffle | **145 chunks per timestep**, ~1,740 refs per file |
| AIS 2 km climatology | `(1, 1521, 1521)` | none | 4 chunks per timestep |
| GrIS 1 km SMB | `(1, 1441, 841)` | gzip 5 | 4 chunks per timestep |
| GrIS 4 km | `(1, 721, 421)` | gzip 5 | one chunk per timestep |
| GrIS ocean 1 km tf | `(4, 961, 561)` | gzip 9 | chunks span 4 months |
| AIS ocean (thetao/so/tf) | `(1, 4, 96, 96)` | gzip 4 | **~5,100 chunks per 10-year file** |
| AIS OCX ocean | `(1, 15, 381, 381)` | gzip 9 | 1.7–2.8 GB files |
| Observations MIPkit | contiguous, NetCDF3 | none | 12161×12161 arrays, 9 GB single file |

Estimated virtual-chunk reference count: on the order of 50–80 million (the 2 km SDBN1 tree alone is ~28 M, the AIS ocean ~7 M). ISMIP6 was about 1 M. This is the main scaling risk and drives the store design in Section 3.

Metadata inconsistencies found in the sample (there will be more):

- **Wrong CRS attributes.** The AIS SDBN1-2000m files carry a `crs` variable with `latitude_of_projection_origin = 90`, `straight_vertical_longitude_from_pole = -45`, `standard_parallel = 70` (that is EPSG:3413, Greenland) on Antarctic data. The GrIS SDBN1-1000m files say `Polar Stereographic South EPSG3031` in their `grid` global attribute. These must be overridden per region in the store, and should be reported upstream (files were "Prepared for ISMIP7 by Devon Dunmire", source Brice Noël, ULiège).
- Several file families have **no CRS variable at all** (GrIS ocean tf, fracture, parameterisations) and some have no `grid_mapping` attribute on the data variable.
- **Calendars and epochs differ** across products: `standard` / `days since 1850-01-01` (SMB), `noleap` / `days since 1850-01-16` (GrIS ocean, sgd), `365_day` (AIS ocean), `proleptic_gregorian` (OCX ocean), `days since 1950-01-16` (EN4). Concatenating 165–286 yearly files per variable into one time axis needs the same normalization machinery as `ismip6_helper/time_utils.py`.
- Coordinate dtypes vary (`int64`, `int32`, `float32`, `float64` for x/y; `int64` or `float64` for time) and some x/y arrays carry `_FillValue = nan`.
- Units are inconsistent in spelling (`m`, `meter`, `meters`; `degC`, `deg_C`).
- Variable names with hyphens (`acabf-anomaly`, `tas-anomaly`) are legal in Zarr but awkward in xarray/CF; keep them as array names but expect tooling friction.

### 1d. Cross-check against the ISMIP7 protocol spreadsheet

The protocol overview spreadsheet linked from the protocol page (https://docs.google.com/spreadsheets/d/1bNXSkOgYxe96JX9o4bVNAhmpd0qal3UDehACXpRINgs, "Updated July 17", status log through the week of 4 October 2026) lists, per region, ESM, experiment and product, every forcing variable with its version and Globus path. Extracted into `ismip7_helper/conventions/data/protocol_inventory_2026-10.csv` (1,120 entries) and compared with the bucket listing (`tests/test_full_listing.py::test_bucket_matches_protocol_spreadsheet`):

- Every spreadsheet entry exists in the bucket, and every version number matches (v3 for GrIS SDBN1 `acabf`/`mrro` and derivatives, v2 for `ts`/`sgd`, v2 for AIS SDBN1, v1 for dEBM2, v3 for AIS ocean, v2 for GrIS ocean, v1 for OCX EN4). So the bucket is a faithful mirror of the Globus "official" inventory, not a stale copy.
- The bucket holds 417 (dataset, variable) combinations the spreadsheet does not itemise: the `pr`, `tas`, `tas-anomaly` and `extra/` entries it only mentions in prose; the `mrro-anomaly`/`pr-anomaly` fields in `ctrl`; the whole `ssp245` scenario; and the four AIS OCX ocean pathways (`main`, `cold`, `warm`, `vary`; the spreadsheet says "the one to use" is `main`).
- Globus keeps a trailing version directory (`.../SDBN1-1000m/acabf/v3/<file>`); source.coop folds it into the filename. Our parser already takes the version from the filename.
- The spreadsheet labels dEBM2 "ONLY USE IF INTERESTED IN SENSITIVITY": SDBN1 is the primary atmosphere product and dEBM2 the secondary downscaling. GEMB-SDBN1 is the MRI-ESM2-0 atmosphere product (status log: "file renaming for MRI-ESM2-0 atmosphere fields from SDBN1 to GEMB-SDBN1", week of 27 July). Store attrs should carry that.
- The CORE/ESM set counters are fixed: C001–C011 as in the UAF submission; E001/E002 are ssp534-over for CESM2-WACCM/MRI-ESM2-0, E003–E012 are historical+ssp370 for ACCESS-CM2, CanESM5, GFDL-ESM4, IPSL-CM6A-LR, MPI-ESM1-2-HR. That is why those five ESMs only have historical and ssp370 forcing.
- The website's variable request (sheet "ISM_2026") differs from the checker's CSV: it has eight variables the checker lacks (`pr`, `mrro`, `prra`, `snicem`, `snicefreez`, `sbl`, `tas` for models that compute their own SMB, and `tfbase`), and the checker has `thdrflf` where the website has `tfbase`. Files named with the website-only variables are a warning in our models, not an error, but the checker will refuse them until its CSV is updated. Worth raising with the checker maintainers.

### 1e. The written forcing-file conventions

"ISMIP7 filenames and conventions" (19 June 2026, revised 27 August 2026; PDF linked from the protocol page) has a section "Conventions for climate forcing files" that the bucket follows:

- NetCDF4, one main variable per file, filename starts with the variable name; files hold one year (possibly monthly) up to several years, chosen for distribution size.
- Directory levels for model, scenario, variant (downscaling or interpolation method, with resolution, e.g. `SDBN1-1000m` vs `SDBN1-4000m`), version; the filename collects the directory names in order, version second to last, year or year range last; no underscores inside a component; scenarios lower case; the ice sheet as `_GrIS_` / `_AIS_`.
- CMIP-derived climatology and bias go under `extras/`; observation-derived ones under `obs/` (the melt-calibration and observations products on source.coop are the `obs/`, `meltmip/` and `parameterisations/` trees of that layout).
- The ESM variant (`r1i1p1f1`) is in the metadata unless several members are used, in which case it goes in the filename. (None of the bucket's filenames carry it.)
- Filenames must be unique across the tree: "a file with the same filename anywhere in the directory structure has the same content". That is the property that makes virtual references safe to key by filename.

This also explains the one stray file in the bucket: `sa_CESM2-WACCM_climatology_r1i1p1f1_ismip8km_60m_1850_199501-201412.nc` is the ocean focus group's pre-convention naming (`<var>_<model>_climatology_r1i1p1f1_ismip8km_60m_...`, as listed in their draft protocol document) that was never renamed.

For submissions the same document adds, beyond what the checker enforces today: a mandatory global `crs` attribute (`epsg:3413` or `epsg:3031`), float32 for the time variable and for masks, dimension names exactly `time`, `x`, `y` with 1-D x/y and no lat/lon, output on the ISMIP6 grids at power-of-two kilometre resolutions (1 km finest for GrIS, 2 km for AIS). These are recorded as constants in `models.py` for the SOW 2 checker contribution.

The cheat sheet (August 2026) adds the experiment design that the store should expose as metadata: the control run uses a 2000–2029 climatology built from historical plus ssp126 per ESM and downscaling method, with fracture held at 2015; OCX uses ERA5-downscaled atmosphere (GrIS 1958–2025, AIS 1979–2025), EN4 ocean for GrIS (1950–2025) and the expert-judgement AIS ocean pathways (1950–2025) of which `main` is required for C011 and `cold`/`warm`/`vary` are sensitivity variants; the SMB anomaly reference period is 1960–1989.

### 1c. ISMIP7 submission (output) conventions

Authoritative sources, all in the `ismip` GitHub organization:

- **ISM_SimulationChecker** (`isschecker`, v0.5.1): the official ISMIP7 compliance checker, with the data request as CSV (`isschecker/data/ISMIP7_variable_request.csv`, `experiments_ismip7.csv`) and grid definitions (`isschecker/data/gdfs/`). Docs: https://ismip.github.io/ISM_SimulationChecker/ (`docs/user/checks.md`, `time-encoding.md`, `errors-and-warnings.md`).
- **ismip7-scalar-processing** `docs/user/file-conventions.md`: directory layout and the 10-field filename.
- **ismip7-interpolation**: regridding to the ISMIP7 grids; documents the same archive layout.
- **ismip7-time-encoding**: lookup tables for the time stamps.
- Protocol page: https://www.ismip.org/research/ismip7, section "ISMIP7 filenaming and folder conventions" (directory `<domain_id>/<source_id>/<ism_id>/<set_id>/<set_counter>/`, the ten-field filename, no underscores or dots in `source_id`/`ism_id`, `mNNN`/`fNNN` member ids, `Cnnn`/`Ennn`/`Pnnn` set counters), "Guidance for file preparation" (time in `days since 1850-01-01`, standard calendar; float32; default NetCDF4 `_FillValue`; masks 0 not missing; historical end not repeated at projection start) and the "ISMIP7 Forcing Dataset" note that the forcing is uploaded to Globus (the page predates the source.coop mirror; it lists far fewer ESM/scenario combinations than the bucket holds).

The rules, condensed:

```
<region>/<group>/<model>/<set: CORE|ESM|PPE>/<configid: C001|E001|P001>/
  <variable>_<region>_<group>_<model>_m<NNN>_<ESM>_f<NNN>_<experiment>_<configid>_<YYYY-YYYY>.nc
e.g. lithk_GrIS_VUW_PISM1_m001_CESM2-WACCM_f001_ctrl_C001_2015-2300.nc
```

- One variable per file; dims `(time, [z,] y, x)`; nothing else in the file except CF companions (bounds, grid_mapping, coordinates, cell_measures, ancillary_variables).
- 42 variables in the request (29 gridded `x,y,t`, one `x,y`, one `x,y,z,t` (litemp), 11 scalars). Each is typed **ST** (state, snapshot stamped Jan 1 of year N+1) or **FL** (flux, annual mean stamped Jul 1 of year N with bounds). Calendar `standard`, `days since 1850-01-01`. This is the same ST/FL split we encoded in `ismip_metadata/variables.yaml` for ISMIP6, so the `combined`/`state`/`flux` store design carries over directly.
- Units are compared via UDUNITS; `_FillValue` must be the NetCDF default for the dtype; variables are float32; no `scale_factor`/`add_offset`; no bare NaN.
- Required global attributes: `group`, `model`, `contact_name`, `contact_email`.
- Grids: AIS EPSG:3031 at 32/16/8/4/2 km, GrIS EPSG:3413 at 16/8/5/4/2/1 km; corners and equal x/y spacing are checked.
- Per-variable fill policy (`forbidden`, `no_ice`, `outside_domain`, ...) and cross-file consistency checks (masks sum, thickness vs mask, orog = base + lithk).

The UAF submission on source.coop (`ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C001..C011/`) follows this exactly: 38 files per config set plus `compliance_checker_log.txt`. The logs show only two errors per set (a non-standard `scalar_*` file, and one range violation) and that the checker version in use rejected `OCX` as an experiment name (fixed in later checker releases, which accept lower-case `ocx`). This is strong evidence that the community is already converging on checker-clean submissions.

## 2. Repository decision: new clean repo

Recommendation: **start a new repo `englacial/ismip7-indexing` (or `ismip7-virtualization`) and keep `ismip-indexing` frozen as the ISMIP6 record.** Reasons:

- The two datasets differ in kind. ISMIP6 was model *outputs* (one dir per model/experiment, all variables, one time range); ISMIP7 Task 1 is *inputs* (one file per year, five levels of hierarchy, 4-D ocean fields, 6 TB). The index parser, grouping logic, and store layout all change.
- `ismip-indexing` is explicitly named and documented as ISMIP6 (package `ismip6_helper`, `virtualize_ismip6/`, README, Binder). Retrofitting it would mean renaming everything anyway, and the SOW requires that the build code "can be regenerated and extended to the ISMIP7 outputs", which argues for a clean ISMIP7-shaped layout.
- The reusable pieces are small and well isolated: `time_utils.py` (479 lines), `grid_utils.py` (369), `merge_virtual.py` (602), `ignore_value.py`, the Lambda scaffolding (`Dockerfile.lithops`, `lithops_aws.yaml`, `lithops_aws.md`, the SOPS-encrypted CI credentials pattern), and the concurrent-commit-with-rebase write path. On Lithops: the ISMIP6 code does still use Lithops, but only as the launcher for AWS Lambda (`backend: aws_lambda`); the trouble was GCP Cloud Functions plus GCS rate limits on the Icechunk ref object, which is why it moved to Lambda on AWS (`CLOUD_SETUP.md`, `lithops_aws.md`). For ISMIP7 I would keep Lambda and drop Lithops in favour of a plain `boto3` invoke / `concurrent.futures` fan-out, which `lithops_aws.md` already sketches; it removes the custom runtime build step from CI and one dependency that has bitten us. Copy them in as an `ismip7_helper` package with attribution rather than importing the old repo.
- Two things do change technically and are easier in a fresh repo: move from Icechunk 1.x (ISMIP6 store is spec v1) to **Icechunk 2.x / spec v2** so that `rustytree` (requires icechunk>=2) and icechunk-js's v2 ref listing work; and pin `virtualizarr>=2.2` with the `HDFParser` + `ObjectStoreRegistry` path.

If you would rather not multiply repos, the fallback is a `v2`/`ismip7` top-level package inside `ismip-indexing`, but I would not do that: it conflates two data products in one README and one CI.

The viewer repo (`ismip-viewer`) can stay; see Task 2.

## 3. Task 1 plan: virtualize the ISMIP7 inputs (32–48 h)

### Store design

One Icechunk repo per region, written to an englacial prefix on source.coop (e.g. `englacial/ismip7/icechunk-ais/`, `englacial/ismip7/icechunk-gris/`), spec v2, with a virtual chunk container pointing at `s3://us-west-2.opendata.source.coop/ismip/` (region `us-west-2`, anonymous). Group hierarchy mirrors the source, flattened one level:

```
/<ESM>/<experiment>/<forcing-product>/       # one xarray Dataset per group
    acabf, pr, tas, ... (time, y, x) float32  # virtual, time concatenated across years
    time, x, y                                 # inline, normalized
    crs                                        # inline, correct per region, with crs_wkt
/<ESM>/<experiment>/ocean/                     # (time, z, y, x)
/OCX/...                                       # same shape
/climatology/<ESM>/<experiment>/<product>/     # month-indexed extras
/fracture/<ESM>/<experiment>/                  # non-standard side products
```

Two regions, two repos, because the grids differ and because a single repo with 100 M+ refs is harder to commit and to open in the browser. Group-level attrs record `source_product`, `version` (v1/v2/v3), `license`, and the ISMIP7 citation text from the product READMEs.

Store configuration specifics that matter at this scale:

- Set `ManifestSplittingConfig` so each array's manifest is split along time (e.g. every 240 timesteps), and raise `ManifestPreloadConfig.max_total_refs`. Otherwise the 2 km SDBN1 arrays produce single manifests of tens of MB that every client (including icechunk-js in the browser) must download before reading one timestep.
- Keep `virtual_chunk_location_compression` on (default); the paths are extremely repetitive and compress well.
- Write with `rebase_with=ConflictDetector()`; one commit per group, groups written in parallel from Lambda (the pattern in `lithops_aws.md`).

### Pipeline steps

1. **Index** (local, cheap): list the `ismip/` prefix (a 40 MB JSON of 285 k keys was produced in minutes), parse paths into a typed record (see Section 5 on the pydantic model), write Parquet. Reject/flag anything that does not parse; those become the `skip_list`.
2. **Virtualize per group on Lambda**: for each (ESM, experiment, product), open every yearly file with `virtualizarr` (`HDFParser`; kerchunk fallback for the NetCDF3 observation files), drop bounds variables, load coords inline, normalize time (reuse `time_utils`), override CRS per region (reuse and extend `grid_utils`), concatenate along time with `merge_virtual`, return a manifest.
3. **Write** to Icechunk with rebase, one group per commit; tag a release snapshot.
4. **Annotate** (`ignore_value`, `valid_min/max`) as in ISMIP6; the SMB fields are zero or fill outside the ice.
5. **Validate** the store: open every group with `xr.open_datatree(..., engine="rustytree")` and with stock `engine="zarr"`, compare a sample of chunks byte-for-byte against the source files (the `compare_local_remote_store.py` pattern).
6. **Notebooks**: open with icechunk + xarray, open with rustytree, subset one basin, compute an annual mean, time the cold-open and first-chunk latency per product class (that is the "performance profiling" deliverable). Add a Binder/CryoCloud environment.

Order of ingestion, so something useful exists early: AIS 8 km SMB (cheap, one chunk per step) → GrIS 4 km → GrIS 1 km → AIS ocean → AIS 2 km SDBN1 (the reference-count monster) → OCX, climatologies, fracture → observations.

### Risks and open questions for Task 1

- **Reference volume.** If the 2 km SDBN1 tree proves too heavy for browser clients, publish it in a separate repo (`icechunk-ais-2km`) so the viewer-facing store stays small. Decide after measuring the AIS 8 km repo.
- **Egress and Lambda cost**: header reads only, so cheap; ISMIP6 ran in ~6 h of Lambda. Expect roughly 10× the files; budget a few tens of dollars, well inside Line 3.
- **Upstream metadata fixes.** Report the swapped CRS attributes and the missing `grid_mapping`s to the ISMIP7 forcing group (via `ismip/ismip7-community` discussions) now, and record in the store attrs that the CRS was corrected at ingest.
- **The `ismip` bucket is not ours.** The store references objects by key; if the ISMIP team republishes a `v3` over a `v2`, our references go stale. Mitigation: record `ETag`/size per source file in the manifest metadata (icechunk `validateChecksums` in icechunk-js and `checksum` in Python can enforce it) and keep the index Parquet in the repo so a diff is one command.
- Source-coop write credentials: the SOW says the Organization may provision access to its own account; we need the englacial `sc_creds.json` equivalent and an AWS role for Lambda in the Astera account (the ISMIP6 role is `lithops-ismip6-executor`, account `429435741471`).

## 4. Task 2 plan: viewer (24–32 h)

### What gridlook can already do

gridlook 1.8 (Vue, MIT) opens Icechunk repos through icechunk-js 0.6 by URL, walks up the URL to find the repo root and treats the remainder as a group path, lists nodes, reads a `crs` variable (by `grid_mapping` attr, group attr, `spatial_ref`, or the literal name `crs`), and renders `polar_stereographic` grids through the curvilinear renderer with proj4. It supports catalogs (`gridlook_catalog` JSON with tags and per-dataset params), `varname`/`dimIndices_time` URL params, and static deployment. CORS to the data is the only hosting requirement, and the S3 regional path-style endpoint for the source.coop bucket already returns `Access-Control-Allow-Origin: *` with `Content-Range` exposed (verified). icechunk-js 0.6 reads the container's region from the repo config and addresses that endpoint directly, so the `fetchClient` rewrite to `data.source.coop` that `ismip-viewer` needed is no longer required.

### Why the ISMIP6 store does not render in gridlook today

Opening `combined/JPL1_ISSM/exp05` shows a `polar_stereographic` coordinate variable but **no `grid_mapping` attribute on any data variable** and no group-level `grid_mapping`, and the variable is not named `crs`. gridlook therefore falls through to "regular" and treats x/y as lon/lat. The fix is metadata only: one Python commit that sets `grid_mapping = "polar_stereographic"` on each data array (or the group), adds `crs_wkt`/`spatial_ref` to that variable, and optionally renames it `crs`. No chunk data changes. Open point to verify: whether icechunk 2.x writes to a spec-v1 repo in place or requires an upgrade step; if the latter, do the upgrade, since it also unlocks `listBranches()` over HTTP in icechunk-js.

### Recommended path

1. **Make both stores gridlook-native** (ISMIP7 by construction, ISMIP6 by the metadata commit above). Publish a gridlook catalog for each store. A gridlook catalog is a static JSON file (`"type": "gridlook_catalog"`) listing dataset URLs; gridlook's "Open dataset" dialog loads it and shows the entries with filter dropdowns built from each entry's `tags`, and a deployment can set it as its default catalog. One Icechunk group (e.g. `CESM2-WACCM/ssp585/SDBN1-2000m`) is one catalog entry, with URL `icechunk+https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais/CESM2-WACCM/ssp585/SDBN1-2000m`, tags `{region, ESM, experiment, product, resolution}` and `params` for the default variable and colormap. The catalog is generated from the index by a script in this repo and committed to the Pages site, so it is a few hundred entries per store and regenerates in seconds. This satisfies "deployed viewer loads the Task 1 store and renders each dataset's variables at native resolution" at 8 km, 4 km and 1 km. The 3041×3041 2 km grid is 9.2 M cells; gridlook's curvilinear path handles km-scale grids but should be measured early; if it is too slow, the 2 km products get a `gridtype=regular` override or wait for the multiscale work deferred to SOW 2.
2. **Deploy gridlook under englacial** as a fork with a default catalog, a title/skin change, and nothing else (the SOW's "minimal code changes"). Keep the fork rebasing cleanly on `d70-t/gridlook`.
3. **Keep `ismip-viewer` working** by pointing it at the ISMIP7 store with `store_url`, `group_path`, and the already-supported coordinate-derived grid config. Its hard-coded `DEFAULT_GRID` is the 761×761 AIS grid, but `discoverGridConfig` reads x/y from the store, so GrIS should work. Its hierarchy walker assumes `model/experiment/variable`; the ISMIP7 store is `ESM/experiment/product/variable`, so either pass `group_path=<ESM>/<exp>` and let `product` play the role of "experiment", or add one level to the walker (an afternoon). Upgrade it to icechunk-js 0.6 and drop the `fetchClient` rewrite. This is the "old viewer" path; I would treat it as best-effort, because the MyST/Quarto embed is its real value and that keeps working.
4. **icechunk-js**: we maintain it, so any gap found here (manifest-split handling for spec v2, large-manifest LRU sizing, dotted-bucket addressing) gets fixed there and released; both viewers pick it up.

## 5. The validator question (and pydantic)

Two separate things are being called "validator":

**A. Submission compliance checker.** This now exists and is official: `ismip/ISM_SimulationChecker` (Python, conda-forge, CSV-driven, six check categories, used by UAF in September). Building a competing one would be wasted effort and politically awkward. What the SOW's deferred "validator" item should become in SOW 2 is a **contribution to the checker**: a "cloud-readiness" check set (HDF5 chunk shape sane, no NetCDF3, compression declared, coordinate dtypes, time encoding exactly as specified, CRS present and correct) plus a `--format json` report, and possibly the submission-portal integration with Xylar. Those are small PRs, not a new tool, and they only pay off once submissions flow. **Defer.**

**B. A typed schema of the ISMIP7 naming and metadata conventions, in pydantic.** This I would **do now, as the first thing in the new repo**, because the Task 1 pipeline needs it anyway:

- The indexer must parse 285 k paths into (region, ESM, experiment, product, resolution, variable, version, year-or-range) and reject the ones that do not fit. A pydantic model (`ForcingFile`, `SubmissionFile`, `GridSpec`, `Experiment`, `Variable` with ST/FL and fill policy loaded from the checker's CSVs) gives that parser validation, nice error messages, JSON export for the catalog and the index website, and one place where the conventions live.
- The same models describe the output filename, so when model outputs arrive, the pipeline's index step already understands them. That is exactly the "front-loads the virtualization of the ISMIP7 outputs" argument from the original proposal, delivered at a fraction of the cost.
- It produces a byproduct worth sharing with the ISMIP7 team: a machine-readable report of every input file that breaks the pattern or carries inconsistent metadata (the CRS swap above would have been caught on day one).

Scope it to ~4–6 h inside Task 1: models, a `parse_path()` function, tests against the real listing, and a short "conventions report" notebook. Do not wrap it in a CLI, do not check file contents beyond headers, and do not call it "the validator" in the deliverables, so it does not look like scope creep against the SOW. If the ISMIP team likes it, the models can later move into the checker or `ismip7-community`.

## 6. Client-side Python access

`rustytree` (`rustytree-xarray` on PyPI, Apache-2, icechunk>=2, Zarr v3 only) is the right target for "efficiently load and access the datasets": a two-region store with hundreds of groups is exactly the many-group DataTree case it was built for (its own benchmark went from ~50 s to ~1–3 s on a 107-node repo). Plan to:

- Write the ISMIP7 store in spec v2 so rustytree can open it (the ISMIP6 store is v1 and will not open with rustytree until upgraded).
- Ship a thin `ismip7_helper.open_ais()` / `open_gris()` convenience that builds the icechunk storage + container credentials (the boilerplate from the ISMIP6 README) and returns a DataTree, with `group_filter="/CESM2-WACCM/ssp585/*"`-style selection.
- Profile `engine="rustytree"` against `engine="zarr"` in the notebooks; report cold-open time versus number of groups, since that is the number that grows with the dataset.
- File any rustytree issues upstream to `aladinor/rustytree`; manifest-split repos are a likely first one.

## 7. Suggested sequencing and hours

| Week | Work | Hours |
|---|---|---|
| 1 | New repo, pydantic conventions + indexer, Lambda runtime, AIS 8 km and GrIS 4 km groups in a dev store, measure manifest sizes | 16–20 |
| 2 | GrIS 1 km, AIS ocean, AIS 2 km, OCX, climatology, fracture; annotate; validation; notebooks + profiling; publish v1 tag | 16–24 |
| 2–3 | gridlook fork + catalog + Pages deploy; ISMIP6 store CRS metadata commit; ismip-viewer pointed at the new store; icechunk-js fixes if needed; README | 20–28 |
| 3 | Buffer: upstream reports, review comments | 4–8 |

Total 56–80 h, matching the SOW ranges. The observations and melt-calibration products are small and should be included in Task 1 for completeness (a few hours), but the 9 GB NetCDF3 MIPkit file needs the kerchunk path and will produce one huge virtual chunk per 2-D array; note it in the README as "available but not viewer-friendly" rather than fight it in this SOW.

## 8. Decisions so far and open items

- Repo: `englacial/ismip7-indexing` (created). Target prefix: `englacial/ismip7` on source.coop. Conventions models: done (see README and `docs/conventions_report.md`).
- Still needed: write credentials for `englacial/ismip7` and an AWS role in the Astera account for Lambda (or reimbursable compute). All protocol documents are now in hand.
- Upstream report of the CRS swap: to the forcing producers / ISMIP7 (GitHub discussions), with the conventions report attached; your call on timing.

## References

- ISMIP7 compliance checker: https://github.com/ismip/ISM_SimulationChecker (docs https://ismip.github.io/ISM_SimulationChecker/; files `isschecker/data/ISMIP7_variable_request.csv`, `experiments_ismip7.csv`, `data/gdfs/*.txt`, `docs/user/checks.md`, `docs/user/time-encoding.md`, `docs/user/errors-and-warnings.md`)
- ISMIP7 file and directory conventions: https://github.com/ismip/ismip7-scalar-processing (`docs/user/file-conventions.md`, README)
- ISMIP7 regridding and archive layout: https://github.com/ismip/ismip7-interpolation
- ISMIP7 time encoding: https://github.com/ismip/ismip7-time-encoding
- ISMIP7 Antarctic ocean forcing generator (defines the `ocean` product files): https://github.com/ismip/ismip7-antarctic-ocean-forcing (`docs/user/workflows.md`)
- ISMIP7 community scripts (cloud/source.coop folder): https://github.com/ismip/ismip7-community
- ISMIP7 protocol page (not fetched; from search): https://www.ismip.org/research/ismip7
- Basal melt calibration protocol preprint (mentions Globus distribution and the 8 km ocean grid): https://egusphere.copernicus.org/preprints/2026/egusphere-2026-5337/egusphere-2026-5337.pdf
- Product READMEs read from the bucket: `ismip/ismip7-ais-melt-calibration/data/README.md`, `ismip/ismip7-ais-observations/data/README.md` and sub-READMEs (licences and citations per directory)
- UAF PISM submission and checker logs: `s3://us-west-2.opendata.source.coop/ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C0xx/`
- Our prior work: https://github.com/englacial/ismip-indexing (`virtualize_ismip6/ICECHUNK_STORE.md`, `lithops_aws.md`), https://github.com/englacial/ismip-viewer, https://github.com/EarthyScience/icechunk-js (0.6.1)
- gridlook: https://github.com/d70-t/gridlook (1.8.1; `docs/grid-types.md`, `docs/catalogs.md`, `docs/data-sources.md`, `src/lib/data/icechunkStore.ts`)
- rustytree: https://github.com/aladinor/rustytree (0.5.2)
