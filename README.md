# ISMIP7 indexing and virtualization

Tools to index and virtualize the [ISMIP7](https://www.ismip.org/research/ismip7)
datasets published on [source.coop](https://source.coop/ismip) into cloud-native
[Icechunk](https://icechunk.io/) stores, following the
[ISMIP6 virtualization](https://github.com/englacial/ismip-indexing).

Status: **conventions and indexing only.** The virtualization pipeline, the
Icechunk stores under `s3://us-west-2.opendata.source.coop/englacial/ismip7/`
and the notebooks come next; see [docs/plan.md](docs/plan.md).

We are not associated with ISMIP. These are tools for publicly available data.

## What is in the bucket

`s3://us-west-2.opendata.source.coop/ismip/` (public, anonymous read; listing of 2026-10-31):

| dataset | objects | size | content |
|---|---:|---:|---|
| `ismip7-ais-forcing` | 108,752 | 3.1 TB | Antarctic SMB and ocean forcing, 1850–2300 |
| `ismip7-gris-forcing` | 176,351 | 2.9 TB | Greenland SMB, ocean and subglacial-discharge forcing |
| `ismip7-ais-observations` | 53 | 9.5 GB | MIPkit observations, BedMachine, calving data |
| `ismip7-ais-melt-calibration` | 167 | 25 GB | Melt-parameterisation calibration targets |
| `ismip7-uaf-pism` | 429 | 226 GB | First model submission (UAF PISM, Greenland) |

[docs/conventions_report.md](docs/conventions_report.md) breaks this down by
source, experiment, product and variable, and lists everything that does not
follow the naming pattern.

## Conventions

`ismip7_helper.conventions` holds typed (pydantic) models of the ISMIP7 file
conventions:

- `ForcingFile`: the input forcing datasets, per the "Conventions for climate
  forcing files" section of the ISMIP7 filenames-and-conventions document
  (linked from the protocol page) as realised in the bucket:
  `data/<ESM>/<experiment>/<product>/<variable>/<variable>_<region>_<ESM>_<experiment>_<product>_v<N>_<YYYY>.nc`,
  one file per year with 12 monthly steps. `product` is the SMB or ocean model
  plus resolution (`SDBN1-2000m`, `dEBM2-8000m`, `GEMB-SDBN1-1000m`, `ocean`,
  `ocean-4000m`). The observational era sits under `data/OCX/<source>/...` and
  is normalised to `experiment="ocx"`.
- `SubmissionFile`: model output as submitted, per the
  [protocol page](https://www.ismip.org/research/ismip7) and the
  [ISM_SimulationChecker](https://github.com/ismip/ISM_SimulationChecker):
  `<region>/<group>/<model>/<set>/<counter>/<variable>_<region>_<group>_<model>_mNNN_<ESM>_fNNN_<experiment>_[CEP]NNN_<YYYY-YYYY>.nc`.
- `VariableSpec`, `Experiment`, `GridSpec`: the ISMIP7 data request,
  experiment year ranges and standard grids, vendored from the checker
  (`ismip7_helper/conventions/data/`, see `PROVENANCE.txt`). `GridSpec` also
  provides the *correct* CF `grid_mapping` attributes and WKT per region, since
  several forcing products carry the other hemisphere's CRS.

```python
from ismip7_helper.conventions import parse_key, grid_registry

r = parse_key("ismip/ismip7-ais-forcing/data/CESM2-WACCM/ssp585/SDBN1-2000m/acabf/"
              "acabf_AIS_CESM2-WACCM_ssp585_SDBN1-2000m_v2_2150.nc")
r.forcing.source, r.forcing.experiment, r.forcing.product_model, r.forcing.resolution_m
# ('CESM2-WACCM', 'ssp585', 'SDBN1', 2000)
r.forcing.group_path        # 'CESM2-WACCM/ssp585/SDBN1/2000m'  (where it lands in the store)
r.findings                  # [] -- mismatches between name and directory would be listed here

grid_registry()[("AIS", 2000)].cf_grid_mapping_attrs()["crs_wkt"]
```

Parsing never raises: every key yields a `ParseResult` with a list of
`FileFinding`s (error / warning / info), so a whole-bucket parse produces a
report rather than a traceback.

## Reading the stores from Python

```python
from ismip7_helper.access import open_group, open_store, list_groups

ds = open_group("AIS", "CESM2-WACCM/ssp585/SDBN1/8000m")   # one group, xarray Dataset
dt = open_store("GrIS", engine="rustytree")               # whole store as a DataTree
```

`notebooks/` has executable examples (open and use, rustytree, performance
profile); regenerate them with `python notebooks/make_notebooks.py` and run
them against a local dev store with `ISMIP7_STORE_AIS=local:/path/icechunk-ais
python notebooks/make_notebooks.py --execute`.

## Index and report

```bash
uv sync --extra dev
uv run ismip7-index --out index.parquet --save-listing listing.txt.gz       # lists the bucket (~1 min)
uv run ismip7-conventions-report --index index.parquet > docs/conventions_report.md
uv run pytest                                                             # includes a full-listing parse (~7 s)
```

`tests/data/ismip_keys_2026-10-31.txt.gz` is the listing the models were
derived from; `tests/test_full_listing.py` asserts the conventions hold on it.

## Reading the stores

The stores hold virtual references into the public `ismip` bucket. Icechunk
only follows them when the reader authorizes the virtual chunk container,
and the key must be exactly the container prefix, trailing slash included:

```python
import icechunk, xarray as xr

storage = icechunk.s3_storage(
    bucket="us-west-2.opendata.source.coop", prefix="englacial/ismip7/icechunk-ais",
    region="us-west-2", anonymous=True,
)
repo = icechunk.Repository.open(
    storage,
    authorize_virtual_chunk_access=icechunk.containers_credentials(
        {"s3://us-west-2.opendata.source.coop/ismip/": icechunk.s3_anonymous_credentials()}
    ),
)
ds = xr.open_zarr(repo.readonly_session("main").store,
                  group="CESM2-WACCM/historical/SDBN1/8000m", consolidated=False)
```

## References

- ISMIP7 protocol: https://www.ismip.org/research/ismip7 (links the cheat sheet,
  the filenames-and-conventions document, the protocol overview spreadsheet and the Globus manual)
- Compliance checker and data request: https://github.com/ismip/ISM_SimulationChecker
- File conventions: https://github.com/ismip/ismip7-scalar-processing (docs/user/file-conventions.md)
- Time encoding: https://github.com/ismip/ismip7-time-encoding
- Antarctic ocean forcing generator: https://github.com/ismip/ismip7-antarctic-ocean-forcing
- ISMIP6 virtualization (predecessor): https://github.com/englacial/ismip-indexing

## License

MIT, Copyright (c) 2026 Englacial.
