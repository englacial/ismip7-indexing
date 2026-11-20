# ISMIP7 forcing on source.coop: findings for the producers

Found while virtualizing the `ismip/ismip7-ais-forcing` and `ismip/ismip7-gris-forcing`
datasets (285,104 files, 6 TB) into Icechunk stores. Every item was re-checked against the
files as they were on 2026-11-12 by reading them directly; the files have not changed since
2026-11-06 (sizes and ETags compared again on 2026-11-19). Items are ordered by how much
they affect a user of the files. Where the files say which pipeline wrote them (`comment`,
`source`, `institution`, `title`, `history` attributes), that pipeline is named so each item
can be routed.

## 1. Every AIS atmosphere and SMB file carries the Greenland projection

The `crs` variable in the AIS dEBM2, SDBN1 and GEMB-SDBN1 files (8 km and 2 km, all
ESMs; 59,640 files across 43 of the 47 non-ocean, non-climatology AIS groups) says

```
latitude_of_projection_origin = 90.0
standard_parallel = 70.0
straight_vertical_longitude_from_pole = -45.0
```

That is EPSG:3413, the Greenland grid. The data are on EPSG:3031 (-90, -71, 0), which is
what the `x`/`y` coordinates and the global `grid` attribute describe. Any tool that trusts
the grid mapping places Antarctica over the Arctic. The ocean files are correct, and so are
the ctrl runs (all v2, from 2015 on): their crs reads -90, -71, 0. So the bug is in the
historical and ssp batches of the xesmf regridding step named in the files' `comment`, and
the right settings already exist in that step. The stores replace the `crs` variable with
EPSG:3031.

Ask: rewrite the `crs` variable in place.

## 2. Grid and mask artefacts

Two things the regridding and masking chain did to the fields. They have different fixes,
so they are listed separately under one heading.

### 2a. Noise at the pole in the CESM2-WACCM and CanESM5 forcing

A disc about 120 km across at the pole where values jump from cell to cell. Measured at
step 1900 as the mean neighbour difference within 60 km of the pole against 100 to
1000 km away:

| product | variable | near | elsewhere | ratio | largest jump at the pole |
|---|---|---|---|---|---|
| CESM2-WACCM SDBN1 2 km | tas | 1.0 K | 0.07 K | 14.6 | 18.9 K |
| CESM2-WACCM SDBN1 8 km | tas | 1.7 K | 0.23 K | 7.1 | 17.8 K |
| CanESM5 dEBM2 8 km | acabf | | | 4.2 | |
| CESM2-WACCM SDBN1 2 km | acabf | | | 2.3 | 35 times the typical gradient |
| CESM2-WACCM SDBN1 8 km | pr | | | 1.8 | |
| MRI-ESM2-0, ACCESS-CM2, IPSL-CM6A-LR, RACMO2.3p2-ERA | tas, acabf | | | 0.1 to 1.6 | |

It is strongest in CESM2-WACCM tas and passes into every variable derived from it, which
is how it reaches the 2 km acabf. CanESM5 has it at a quarter of the strength. The other
ESMs and the RACMO reanalysis are clean or nearly so, which says the chain copes when the
source grid behaves. It is present at every time step, and the downscaling to 2 km sharpens
it rather than smoothing it. It sits on the plateau, so it enters every budget integrated
over interior Antarctica. (Regridding step.)

Ask: regrid the polar cap of those ESMs with a method that handles the convergence of
their lat/lon grid, or mask the disc.

### 2b. Ice-free ground is written as zero data, not as missing

In the 2 km SDBN1 products (checked on CESM2-WACCM historical, step 1900) 206,361 cells
are exactly 0 at every time step, in patches along the mountain ranges, while `pr` is
normal in the same cells. That is the ice-free mask of the downscaling: rock and nunataks,
where SMB is forced to zero. The 8 km products do not show it, since an 8 km cell there is
mostly ice.

The lapse-rate products dacabfdz, dmrrodz and dtsdz at 8 km have the same thing on a
larger scale: fill on the far ocean, but literal 0.0 on 155,003 near-coast and shelf cells
where acabf in the same product is fill, while 12% of cells inside the acabf mask are also
exactly 0.0.

In both cases a reader cannot tell ice-free ground from a zero balance, and in the second
the mask differs between variables of one product. A further ~21,000 cells of the 2 km
SMB hold values of 1e-12 to 1e-11 instead of exact zeros, the same cells every step:
leakage from neighbouring ice cells during regridding across the mask edge. They are
harmless to budgets but they are neither zero nor fill. (SDBN1 downscaling, per the files'
`source` attribute.)

Ask: write ice-free ground as the fill value, or publish the mask as a variable, and use
one mask across the variables of a product.

## 3. The 2300 files of four MRI-ESM2-0 ssp585 variables were replaced with annual means

On 2026-11-06, with no announcement and under the same file names, the monthly 2300 files
of tas, tas-anomaly, ts and ts-anomaly in MRI-ESM2-0/ssp585/GEMB-SDBN1 (AIS 8 km and 2 km,
GrIS 4 km and 1 km, 16 files) became one-step files with the comment "Year 2300 computed
as average of years 2290-2299". Every other variable and every other year is monthly, so
the series end with eleven missing months. The stores point the twelve monthly chunks of
2300 at the single annual value and say so in `ismip7_notes`. (Regridding step; the files'
`history` shows the 2300 fields were made from the 2290 inputs with `ncatted` on
2026-06-15.)

Ask: the versions uploaded before 2026-11-06 had twelve monthly steps for 2300; were those
wrong, or replaced by mistake? And announce or version replacements of published files,
since anyone who indexed them before that date holds dangling references.

## 4. sgd time stamps use a different origin and calendar

GrIS sgd files use `days since YYYY-01-16` on a `noleap`/`365_day` calendar with offsets
0, 29, 59, giving the 16th, the 14th and the 16th. Every other variable uses
`days since 1850-01-01` on the `standard` calendar at mid-month (the 15th). All 62 GrIS
groups with sgd. The store writes its own normalised time axis per group, so sgd sits on
acabf's there; raw readers see a one-day skew. (The subglacial-discharge pipeline named in
the files' `title`; these are the only files in the archive with their own calendar and
origin.)

Ask: write sgd on the common time axis.

## 5. A few files switch the fill value from NaN to 9.97e36

Three GrIS files declare `_FillValue = 9.97e36` where every other file of their series
declares NaN:

| file | series |
|---|---|
| `sgd_GrIS_CESM2-WACCM_ssp126_SDBN1-4000m_v2_2233.nc` | 2015 to 2300, NaN elsewhere |
| `sgd_GrIS_CESM2-WACCM_ssp534-over_SDBN1-4000m_v2_2202.nc` | 2015 to 2300, NaN elsewhere |
| `tf_GrIS_CESM2-WACCM_ssp534-over_ocean-4000m_v2_2289.nc` | 2015 to 2300, NaN elsewhere |

Each is one year in the middle of a series, written with the netCDF default fill instead
of the NaN its neighbours use. A reader who concatenates the raw files under one fill value
masks NaN and keeps 9.97e36 as data for those twelve months; xarray's `open_mfdataset` is
safe because it decodes each file by its own attribute, but anything that stacks the arrays
by hand, or any Zarr array, which has exactly one fill value, is not. The store keeps the
series' fill value, NaN, and records the odd file's value as `missing_value`, which xarray
masks as well (verified: the 2233 steps read back with no 9.97e36 cells); a minority fill
below 1e30 would be refused rather than masked. (The sgd pipeline and the GSFC ocean
product; both files passed through the same `cdo remap` run in July 2026 per their
`history`, which looks like where the fill changed.)

Ask: one fill convention per series. Either value is fine; NaN matches the rest.

## 6. HDF5 storage fill of 0.0 on the MRI-ESM2-0 anomaly files

acabf-anomaly, tas-anomaly and ts-anomaly from MRI-ESM2-0 (8,728 files, both regions) have
`_FillValue = 9.97e36` but an HDF5 dataset fill value of 0.0. Harmless as long as every
chunk is written, which the stores verify on build, but any unwritten chunk would read as
valid zeros rather than as missing. The other ESMs set both to 9.97e36. The four replaced
2300 files of item 3 (GrIS 1 km and 4 km tas-anomaly and ts-anomaly) have it the other way
round: storage fill 9.97e36 where their series uses 0.0. (Regridding step.)

## 7. Inconsistent chunking for a minority of the files

Icechunk and VirtualiZarr allow one chunk grid per array (per variable), and the forcing
files are mostly consistent; where they are not:

- AIS historical ocean `*_2010-2014.nc` of all six ESMs, plus CanESM5 `1860-1860` and
  `2011-2014`: chunked (1, 2, 96, 96) where the series is (1, 4, 96, 96).
- GrIS CESM2-WACCM 1000 m dtsdz 2300 files (ssp534-over and ssp585, SDBN1 and dEBM2):
  chunked (1, 1441, 841) at zlib 5 where the series is (1, 38, 1681) at zlib 1.
- GrIS EN4 ocx 1000 m so/tf 2026: chunked (1, 1441, 841) where the series is (4, 961, 561).

The steps from the odd files are re-chunked to the series' grid and written into the store
as native chunks so the full series opens; that copies the data, 2.7 GB on 22.5 GB of store
(12%), nearly all of it the AIS ocean tails. A different compression level alone costs
nothing (the chunk is relabelled). (Ocean tails: the ocean pipeline; dtsdz 2300: the
regridding step; EN4: the GSFC product per its `institution`.)

Ask: rewrite the tail files with the series' chunking.

## 8. Layout, naming and copied attributes

- `sa_CESM2-WACCM_climatology_r1i1p1f1_ismip8km_60m_1850_199501-201412.nc` under
  `ocean/extras/climatology` does not follow the file naming convention, and the climatology
  files have no time axis.
- EN4 lives at `data/OCX/EN4/ocean-1000m` with source and experiment swapped relative to
  every other product (`data/<ESM>/<experiment>/...`).
- Every GrIS sgd file carries a `title` naming CESM2-WACCM as its source, whatever the ESM.
- The dEBM2 files carry the SDBN1 product in their `source` attribute, so the provenance
  attributes are copied rather than set per product.

## Resolved since the first pass

- The dmrrodz 2300 file for GrIS MRI-ESM2-0/ssp126/dEBM2-4000m, missing in October, was
  added on 2026-11-06.
