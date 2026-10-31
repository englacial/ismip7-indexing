# ISMIP7 source.coop conventions report

285,752 objects, 6,245.5 GB in total.

## Datasets

| dataset | objects | size | parsed OK | errors | warnings |
|---|---:|---:|---:|---:|---:|
| `ismip7-ais-forcing` | 108,752 | 3,084.4 GB | 108,751 | 1 | 0 |
| `ismip7-ais-melt-calibration` | 167 | 25.2 GB | 167 | 0 | 0 |
| `ismip7-ais-observations` | 53 | 9.5 GB | 53 | 0 | 0 |
| `ismip7-gris-forcing` | 176,351 | 2,900.4 GB | 176,351 | 0 | 0 |
| `ismip7-uaf-pism` | 429 | 226.0 GB | 381 | 48 | 0 |

## Forcing datasets: sources, experiments and products

| region | source | experiment | product | kind | files | size | variables | years |
|---|---|---|---|---|---:|---:|---|---|
| AIS | ACCESS-CM2 | historical | dEBM2-8000m | annual | 1,815 | 8.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | ACCESS-CM2 | historical | dEBM2-8000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | ACCESS-CM2 | historical | ocean | multiyear | 51 | 17.6 GB | so, tf, thetao | 1850-2014 |
| AIS | ACCESS-CM2 | ssp370 | dEBM2-8000m | annual | 946 | 4.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | ACCESS-CM2 | ssp370 | fracture | fracture | 4 | 1.3 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | ACCESS-CM2 | ssp370 | ocean | multiyear | 27 | 9.1 GB | so, tf, thetao | 2015-2100 |
| AIS | CESM2-WACCM | ctrl | SDBN1-2000m | annual | 3,718 | 237.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ctrl | SDBN1-8000m | annual | 3,718 | 18.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ctrl | ocean | multiyear | 87 | 31.3 GB | so, tf, thetao | 2015-2300 |
| AIS | CESM2-WACCM | historical | SDBN1-2000m | annual | 1,815 | 126.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | CESM2-WACCM | historical | SDBN1-2000m | climatology | 5 | 2.2 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | CESM2-WACCM | historical | SDBN1-8000m | annual | 1,815 | 19.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | CESM2-WACCM | historical | SDBN1-8000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | CESM2-WACCM | historical | dEBM2-8000m | annual | 1,815 | 8.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | CESM2-WACCM | historical | ocean | bias | 2 | 0.3 GB | ct, sa | 1995-2024 |
| AIS | CESM2-WACCM | historical | ocean | climatology | 2 | 0.2 GB | ct, sa | 1995-2024 |
| AIS | CESM2-WACCM | historical | ocean | multiyear | 51 | 17.4 GB | so, tf, thetao | 1850-2014 |
| AIS | CESM2-WACCM | ssp126 | SDBN1-2000m | annual | 3,146 | 224.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp126 | SDBN1-8000m | annual | 3,718 | 49.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp126 | dEBM2-8000m | annual | 3,146 | 14.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp126 | fracture | fracture | 4 | 3.0 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | CESM2-WACCM | ssp126 | ocean | multiyear | 87 | 29.8 GB | so, tf, thetao | 2015-2299 |
| AIS | CESM2-WACCM | ssp245 | dEBM2-8000m | annual | 688 | 4.2 GB | acabf, acabf-anomaly, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | CESM2-WACCM | ssp370 | SDBN1-2000m | annual | 946 | 95.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | CESM2-WACCM | ssp370 | SDBN1-8000m | annual | 946 | 10.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | CESM2-WACCM | ssp370 | dEBM2-8000m | annual | 946 | 4.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | CESM2-WACCM | ssp370 | fracture | fracture | 4 | 1.3 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | CESM2-WACCM | ssp370 | ocean | multiyear | 27 | 9.0 GB | so, tf, thetao | 2015-2100 |
| AIS | CESM2-WACCM | ssp534-over | SDBN1-2000m | annual | 3,146 | 225.0 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp534-over | SDBN1-8000m | annual | 3,146 | 33.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp534-over | dEBM2-8000m | annual | 3,146 | 14.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp534-over | fracture | fracture | 4 | 3.0 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | CESM2-WACCM | ssp534-over | ocean | multiyear | 87 | 29.7 GB | so, tf, thetao | 2015-2299 |
| AIS | CESM2-WACCM | ssp585 | SDBN1-2000m | annual | 3,146 | 225.0 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp585 | SDBN1-8000m | annual | 3,146 | 33.8 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp585 | dEBM2-8000m | annual | 3,146 | 14.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | CESM2-WACCM | ssp585 | fracture | fracture | 4 | 3.0 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | CESM2-WACCM | ssp585 | ocean | multiyear | 87 | 29.5 GB | so, tf, thetao | 2015-2299 |
| AIS | CanESM5 | historical | dEBM2-8000m | annual | 1,815 | 8.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | CanESM5 | historical | dEBM2-8000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | CanESM5 | historical | ocean | multiyear | 54 | 17.7 GB | so, tf, thetao | 1850-2014 |
| AIS | CanESM5 | ssp370 | dEBM2-8000m | annual | 946 | 4.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | CanESM5 | ssp370 | fracture | fracture | 4 | 1.3 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | CanESM5 | ssp370 | ocean | multiyear | 27 | 9.1 GB | so, tf, thetao | 2015-2100 |
| AIS | GFDL-ESM4 | historical | dEBM2-8000m | annual | 1,815 | 8.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | GFDL-ESM4 | historical | dEBM2-8000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | GFDL-ESM4 | historical | ocean | multiyear | 51 | 17.8 GB | so, tf, thetao | 1850-2014 |
| AIS | GFDL-ESM4 | ssp370 | dEBM2-8000m | annual | 946 | 4.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | GFDL-ESM4 | ssp370 | fracture | fracture | 3 | 0.6 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask |  |
| AIS | GFDL-ESM4 | ssp370 | ocean | multiyear | 27 | 9.2 GB | so, tf, thetao | 2015-2100 |
| AIS | IPSL-CM6A-LR | historical | dEBM2-8000m | annual | 1,815 | 8.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | IPSL-CM6A-LR | historical | dEBM2-8000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | IPSL-CM6A-LR | historical | ocean | multiyear | 51 | 17.4 GB | so, tf, thetao | 1850-2014 |
| AIS | IPSL-CM6A-LR | ssp370 | dEBM2-8000m | annual | 946 | 4.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | IPSL-CM6A-LR | ssp370 | fracture | fracture | 4 | 1.3 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | IPSL-CM6A-LR | ssp370 | ocean | multiyear | 27 | 9.0 GB | so, tf, thetao | 2015-2100 |
| AIS | MPI-ESM1-2-HR | historical | dEBM2-8000m | annual | 1,815 | 8.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | MPI-ESM1-2-HR | historical | dEBM2-8000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | MPI-ESM1-2-HR | historical | ocean | multiyear | 99 | 20.4 GB | so, tf, thetao | 1850-2014 |
| AIS | MPI-ESM1-2-HR | ssp370 | dEBM2-8000m | annual | 946 | 4.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | MPI-ESM1-2-HR | ssp370 | fracture | fracture | 3 | 0.6 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask |  |
| AIS | MPI-ESM1-2-HR | ssp370 | ocean | multiyear | 54 | 10.6 GB | so, tf, thetao | 2015-2100 |
| AIS | MRI-ESM2-0 | ctrl | GEMB-SDBN1-2000m | annual | 3,718 | 219.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ctrl | GEMB-SDBN1-8000m | annual | 3,718 | 16.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ctrl | ocean | multiyear | 87 | 31.3 GB | so, tf, thetao | 2015-2300 |
| AIS | MRI-ESM2-0 | historical | GEMB-SDBN1-2000m | annual | 1,980 | 117.8 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | MRI-ESM2-0 | historical | GEMB-SDBN1-2000m | climatology | 5 | 0.4 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | MRI-ESM2-0 | historical | GEMB-SDBN1-8000m | annual | 1,980 | 8.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | MRI-ESM2-0 | historical | GEMB-SDBN1-8000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| AIS | MRI-ESM2-0 | historical | dEBM2-8000m | annual | 1,980 | 8.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| AIS | MRI-ESM2-0 | historical | ocean | multiyear | 51 | 17.5 GB | so, tf, thetao | 1850-2014 |
| AIS | MRI-ESM2-0 | ssp126 | GEMB-SDBN1-2000m | annual | 3,432 | 204.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp126 | GEMB-SDBN1-8000m | annual | 3,432 | 15.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp126 | dEBM2-8000m | annual | 3,432 | 14.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp126 | fracture | fracture | 4 | 3.0 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | MRI-ESM2-0 | ssp126 | ocean | bias | 2 | 0.3 GB | ct, sa | 1995-2024 |
| AIS | MRI-ESM2-0 | ssp126 | ocean | climatology | 2 | 0.3 GB | ct, sa | 1995-2024 |
| AIS | MRI-ESM2-0 | ssp126 | ocean | multiyear | 87 | 29.9 GB | so, tf, thetao | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp370 | GEMB-SDBN1-2000m | annual | 1,032 | 61.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | MRI-ESM2-0 | ssp370 | GEMB-SDBN1-8000m | annual | 1,032 | 4.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | MRI-ESM2-0 | ssp370 | dEBM2-8000m | annual | 1,032 | 4.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| AIS | MRI-ESM2-0 | ssp370 | fracture | fracture | 4 | 1.3 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | MRI-ESM2-0 | ssp370 | ocean | bias | 2 | 0.3 GB | ct, sa | 1995-2024 |
| AIS | MRI-ESM2-0 | ssp370 | ocean | climatology | 2 | 0.3 GB | ct, sa | 1995-2024 |
| AIS | MRI-ESM2-0 | ssp370 | ocean | multiyear | 27 | 9.0 GB | so, tf, thetao | 2015-2100 |
| AIS | MRI-ESM2-0 | ssp534-over | GEMB-SDBN1-2000m | annual | 3,432 | 204.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp534-over | GEMB-SDBN1-8000m | annual | 3,432 | 15.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp534-over | dEBM2-8000m | annual | 3,432 | 14.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp534-over | fracture | fracture | 4 | 3.0 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | MRI-ESM2-0 | ssp534-over | ocean | multiyear | 87 | 29.9 GB | so, tf, thetao | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp585 | GEMB-SDBN1-2000m | annual | 3,432 | 207.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp585 | GEMB-SDBN1-8000m | annual | 3,431 | 15.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp585 | dEBM2-8000m | annual | 3,431 | 14.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| AIS | MRI-ESM2-0 | ssp585 | fracture | fracture | 4 | 3.0 GB | excess_melt_fac_depletion, ice_shelf_collapse_mask, lake_properties |  |
| AIS | MRI-ESM2-0 | ssp585 | ocean | bias | 2 | 0.3 GB | ct, sa | 1995-2024 |
| AIS | MRI-ESM2-0 | ssp585 | ocean | climatology | 2 | 0.3 GB | ct, sa | 1995-2024 |
| AIS | MRI-ESM2-0 | ssp585 | ocean | multiyear | 87 | 29.6 GB | so, tf, thetao | 2015-2300 |
| AIS | RACMO2.3p2-ERA | ocx | SDBN1-2000m | annual | 423 | 20.4 GB | acabf, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, ts | 1979-2025 |
| AIS | RACMO2.3p2-ERA | ocx | SDBN1-8000m | annual | 423 | 1.5 GB | acabf, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, tas, ts | 1979-2025 |
| AIS | grid | static | ocean/ISMIP7/8km-60m | grid | 1 | 0.0 GB |  |  |
| AIS | ocean | ocx | cold | multiyear | 3 | 7.3 GB | so, tf, thetao | 1950-2025 |
| AIS | ocean | ocx | main | multiyear | 3 | 7.2 GB | so, tf, thetao | 1950-2025 |
| AIS | ocean | ocx | vary | multiyear | 3 | 7.2 GB | so, tf, thetao | 1950-2025 |
| AIS | ocean | ocx | warm | multiyear | 3 | 7.2 GB | so, tf, thetao | 1950-2025 |
| AIS | parameterisations | static | fracture | parameterisation | 5 | 0.0 GB | ant |  |
| GrIS | ACCESS-CM2 | historical | dEBM2-1000m | annual | 1,980 | 40.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | ACCESS-CM2 | historical | dEBM2-1000m | climatology | 5 | 0.1 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | ACCESS-CM2 | historical | dEBM2-4000m | annual | 1,980 | 6.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | ACCESS-CM2 | historical | dEBM2-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | ACCESS-CM2 | historical | ocean-1000m | annual | 330 | 4.5 GB | so, tf | 1850-2014 |
| GrIS | ACCESS-CM2 | historical | ocean-4000m | annual | 330 | 2.6 GB | so, tf | 1850-2014 |
| GrIS | ACCESS-CM2 | ssp370 | dEBM2-1000m | annual | 1,032 | 21.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | ACCESS-CM2 | ssp370 | dEBM2-4000m | annual | 1,032 | 3.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | ACCESS-CM2 | ssp370 | ocean-1000m | annual | 172 | 2.4 GB | so, tf | 2015-2100 |
| GrIS | ACCESS-CM2 | ssp370 | ocean-4000m | annual | 172 | 1.4 GB | so, tf | 2015-2100 |
| GrIS | CESM2-WACCM | ctrl | SDBN1-1000m | annual | 4,004 | 162.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ctrl | SDBN1-4000m | annual | 4,004 | 12.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ctrl | ocean-1000m | annual | 572 | 7.9 GB | so, tf | 2015-2300 |
| GrIS | CESM2-WACCM | ctrl | ocean-4000m | annual | 572 | 2.3 GB | so, tf | 2015-2300 |
| GrIS | CESM2-WACCM | historical | SDBN1-1000m | annual | 1,980 | 81.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | CESM2-WACCM | historical | SDBN1-1000m | climatology | 7 | 1.3 GB | acabf, mrro, pr, snm, snowf, tas, ts | 1960-1989 |
| GrIS | CESM2-WACCM | historical | SDBN1-4000m | annual | 1,980 | 9.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | CESM2-WACCM | historical | SDBN1-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | CESM2-WACCM | historical | dEBM2-1000m | annual | 1,980 | 67.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | CESM2-WACCM | historical | dEBM2-1000m | climatology | 5 | 0.2 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | CESM2-WACCM | historical | dEBM2-4000m | annual | 1,980 | 6.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | CESM2-WACCM | historical | dEBM2-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | CESM2-WACCM | historical | ocean-1000m | annual | 330 | 4.5 GB | so, tf | 1850-2014 |
| GrIS | CESM2-WACCM | historical | ocean-4000m | annual | 330 | 1.4 GB | so, tf | 1850-2014 |
| GrIS | CESM2-WACCM | ssp126 | SDBN1-1000m | annual | 3,432 | 143.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp126 | SDBN1-4000m | annual | 3,432 | 16.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp126 | dEBM2-1000m | annual | 3,432 | 117.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp126 | dEBM2-4000m | annual | 3,432 | 10.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp126 | ocean-1000m | annual | 572 | 8.1 GB | so, tf | 2015-2300 |
| GrIS | CESM2-WACCM | ssp126 | ocean-4000m | annual | 572 | 2.5 GB | so, tf | 2015-2300 |
| GrIS | CESM2-WACCM | ssp245 | dEBM2-1000m | annual | 688 | 33.8 GB | acabf, acabf-anomaly, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CESM2-WACCM | ssp245 | dEBM2-4000m | annual | 688 | 2.6 GB | acabf, acabf-anomaly, mrro, pr, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CESM2-WACCM | ssp370 | SDBN1-1000m | annual | 1,032 | 43.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CESM2-WACCM | ssp370 | SDBN1-4000m | annual | 1,032 | 5.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CESM2-WACCM | ssp370 | dEBM2-1000m | annual | 1,032 | 35.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CESM2-WACCM | ssp370 | dEBM2-4000m | annual | 1,032 | 3.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CESM2-WACCM | ssp370 | ocean-1000m | annual | 172 | 2.4 GB | so, tf | 2015-2100 |
| GrIS | CESM2-WACCM | ssp370 | ocean-4000m | annual | 172 | 0.7 GB | so, tf | 2015-2100 |
| GrIS | CESM2-WACCM | ssp534-over | SDBN1-1000m | annual | 3,432 | 143.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp534-over | SDBN1-4000m | annual | 3,432 | 17.0 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp534-over | dEBM2-1000m | annual | 3,432 | 97.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp534-over | dEBM2-4000m | annual | 3,432 | 10.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp534-over | ocean-1000m | annual | 572 | 8.1 GB | so, tf | 2015-2300 |
| GrIS | CESM2-WACCM | ssp534-over | ocean-4000m | annual | 572 | 2.5 GB | so, tf | 2015-2300 |
| GrIS | CESM2-WACCM | ssp585 | SDBN1-1000m | annual | 3,432 | 145.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp585 | SDBN1-4000m | annual | 3,432 | 17.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp585 | dEBM2-1000m | annual | 3,432 | 123.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp585 | dEBM2-4000m | annual | 3,432 | 11.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | CESM2-WACCM | ssp585 | ocean-1000m | annual | 572 | 8.3 GB | so, tf | 2015-2300 |
| GrIS | CESM2-WACCM | ssp585 | ocean-4000m | annual | 572 | 2.5 GB | so, tf | 2015-2300 |
| GrIS | CanESM5 | historical | dEBM2-1000m | annual | 1,980 | 39.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | CanESM5 | historical | dEBM2-1000m | climatology | 5 | 0.1 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | CanESM5 | historical | dEBM2-4000m | annual | 1,980 | 6.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | CanESM5 | historical | dEBM2-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | CanESM5 | historical | ocean-1000m | annual | 330 | 4.5 GB | so, tf | 1850-2014 |
| GrIS | CanESM5 | historical | ocean-4000m | annual | 330 | 2.6 GB | so, tf | 1850-2014 |
| GrIS | CanESM5 | ssp370 | dEBM2-1000m | annual | 1,032 | 21.0 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CanESM5 | ssp370 | dEBM2-4000m | annual | 1,032 | 3.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | CanESM5 | ssp370 | ocean-1000m | annual | 172 | 2.4 GB | so, tf | 2015-2100 |
| GrIS | CanESM5 | ssp370 | ocean-4000m | annual | 172 | 1.4 GB | so, tf | 2015-2100 |
| GrIS | EN4 | ocx | ocean-1000m | annual | 154 | 2.1 GB | so, tf | 1950-2026 |
| GrIS | EN4 | ocx | ocean-4000m | annual | 154 | 0.6 GB | so, tf | 1950-2026 |
| GrIS | GFDL-ESM4 | historical | dEBM2-1000m | annual | 1,980 | 40.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | GFDL-ESM4 | historical | dEBM2-1000m | climatology | 5 | 0.1 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | GFDL-ESM4 | historical | dEBM2-4000m | annual | 1,980 | 6.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | GFDL-ESM4 | historical | dEBM2-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | GFDL-ESM4 | historical | ocean-1000m | annual | 330 | 4.6 GB | so, tf | 1850-2014 |
| GrIS | GFDL-ESM4 | historical | ocean-4000m | annual | 330 | 2.6 GB | so, tf | 1850-2014 |
| GrIS | GFDL-ESM4 | ssp370 | dEBM2-1000m | annual | 1,032 | 21.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | GFDL-ESM4 | ssp370 | dEBM2-4000m | annual | 1,032 | 3.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | GFDL-ESM4 | ssp370 | ocean-1000m | annual | 172 | 2.4 GB | so, tf | 2015-2100 |
| GrIS | GFDL-ESM4 | ssp370 | ocean-4000m | annual | 172 | 1.4 GB | so, tf | 2015-2100 |
| GrIS | IPSL-CM6A-LR | historical | dEBM2-1000m | annual | 1,980 | 40.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | IPSL-CM6A-LR | historical | dEBM2-1000m | climatology | 5 | 0.1 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | IPSL-CM6A-LR | historical | dEBM2-4000m | annual | 1,980 | 6.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | IPSL-CM6A-LR | historical | dEBM2-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | IPSL-CM6A-LR | historical | ocean-1000m | annual | 330 | 4.5 GB | so, tf | 1850-2014 |
| GrIS | IPSL-CM6A-LR | historical | ocean-4000m | annual | 330 | 2.6 GB | so, tf | 1850-2014 |
| GrIS | IPSL-CM6A-LR | ssp370 | dEBM2-1000m | annual | 1,032 | 21.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | IPSL-CM6A-LR | ssp370 | dEBM2-4000m | annual | 1,032 | 3.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | IPSL-CM6A-LR | ssp370 | ocean-1000m | annual | 172 | 2.4 GB | so, tf | 2015-2100 |
| GrIS | IPSL-CM6A-LR | ssp370 | ocean-4000m | annual | 172 | 1.4 GB | so, tf | 2015-2100 |
| GrIS | MPI-ESM1-2-HR | historical | dEBM2-1000m | annual | 1,980 | 41.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | MPI-ESM1-2-HR | historical | dEBM2-1000m | climatology | 5 | 0.1 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | MPI-ESM1-2-HR | historical | dEBM2-4000m | annual | 1,980 | 6.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | MPI-ESM1-2-HR | historical | dEBM2-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | MPI-ESM1-2-HR | historical | ocean-1000m | annual | 330 | 4.5 GB | so, tf | 1850-2014 |
| GrIS | MPI-ESM1-2-HR | historical | ocean-4000m | annual | 330 | 2.6 GB | so, tf | 1850-2014 |
| GrIS | MPI-ESM1-2-HR | ssp370 | dEBM2-1000m | annual | 1,032 | 21.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | MPI-ESM1-2-HR | ssp370 | dEBM2-4000m | annual | 1,032 | 3.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | MPI-ESM1-2-HR | ssp370 | ocean-1000m | annual | 172 | 2.4 GB | so, tf | 2015-2100 |
| GrIS | MPI-ESM1-2-HR | ssp370 | ocean-4000m | annual | 172 | 1.4 GB | so, tf | 2015-2100 |
| GrIS | MRI-ESM2-0 | ctrl | GEMB-SDBN1-1000m | annual | 4,004 | 152.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ctrl | GEMB-SDBN1-4000m | annual | 4,004 | 11.9 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtsdz, mrro, mrro-anomaly, pr, pr-anomaly, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ctrl | ocean-1000m | annual | 572 | 8.0 GB | so, tf | 2015-2300 |
| GrIS | MRI-ESM2-0 | ctrl | ocean-4000m | annual | 572 | 2.3 GB | so, tf | 2015-2300 |
| GrIS | MRI-ESM2-0 | historical | GEMB-SDBN1-1000m | annual | 2,145 | 76.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | MRI-ESM2-0 | historical | GEMB-SDBN1-1000m | climatology | 5 | 0.3 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | MRI-ESM2-0 | historical | GEMB-SDBN1-4000m | annual | 2,145 | 6.0 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | MRI-ESM2-0 | historical | GEMB-SDBN1-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | MRI-ESM2-0 | historical | dEBM2-1000m | annual | 2,145 | 41.1 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | MRI-ESM2-0 | historical | dEBM2-1000m | climatology | 5 | 0.1 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | MRI-ESM2-0 | historical | dEBM2-4000m | annual | 2,145 | 6.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 1850-2014 |
| GrIS | MRI-ESM2-0 | historical | dEBM2-4000m | climatology | 5 | 0.0 GB | acabf, mrro, pr, tas, ts | 1960-1989 |
| GrIS | MRI-ESM2-0 | historical | ocean-1000m | annual | 330 | 4.6 GB | so, tf | 1850-2014 |
| GrIS | MRI-ESM2-0 | historical | ocean-4000m | annual | 330 | 1.4 GB | so, tf | 1850-2014 |
| GrIS | MRI-ESM2-0 | ssp126 | GEMB-SDBN1-1000m | annual | 3,718 | 132.8 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp126 | GEMB-SDBN1-4000m | annual | 3,718 | 10.5 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp126 | dEBM2-1000m | annual | 3,718 | 71.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp126 | dEBM2-4000m | annual | 3,717 | 10.8 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp126 | ocean-1000m | annual | 572 | 8.2 GB | so, tf | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp126 | ocean-4000m | annual | 572 | 2.5 GB | so, tf | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp370 | GEMB-SDBN1-1000m | annual | 1,118 | 40.3 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | MRI-ESM2-0 | ssp370 | GEMB-SDBN1-4000m | annual | 1,118 | 3.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | MRI-ESM2-0 | ssp370 | dEBM2-1000m | annual | 1,118 | 21.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | MRI-ESM2-0 | ssp370 | dEBM2-4000m | annual | 1,118 | 3.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2100 |
| GrIS | MRI-ESM2-0 | ssp370 | ocean-1000m | annual | 172 | 2.5 GB | so, tf | 2015-2100 |
| GrIS | MRI-ESM2-0 | ssp370 | ocean-4000m | annual | 172 | 0.7 GB | so, tf | 2015-2100 |
| GrIS | MRI-ESM2-0 | ssp534-over | GEMB-SDBN1-1000m | annual | 3,718 | 133.0 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp534-over | GEMB-SDBN1-4000m | annual | 3,718 | 11.4 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp534-over | dEBM2-1000m | annual | 3,718 | 71.7 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp534-over | dEBM2-4000m | annual | 3,718 | 10.8 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp534-over | ocean-1000m | annual | 572 | 8.2 GB | so, tf | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp534-over | ocean-4000m | annual | 572 | 2.5 GB | so, tf | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp585 | GEMB-SDBN1-1000m | annual | 3,718 | 135.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp585 | GEMB-SDBN1-4000m | annual | 3,718 | 10.6 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp585 | dEBM2-1000m | annual | 3,718 | 72.2 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp585 | dEBM2-4000m | annual | 3,718 | 11.0 GB | acabf, acabf-anomaly, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, tas-anomaly, ts, ts-anomaly | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp585 | ocean-1000m | annual | 572 | 8.4 GB | so, tf | 2015-2300 |
| GrIS | MRI-ESM2-0 | ssp585 | ocean-4000m | annual | 572 | 2.5 GB | so, tf | 2015-2300 |
| GrIS | RACMO2.3p2-ERA | ocx | SDBN1-1000m | annual | 680 | 17.8 GB | acabf, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, ts | 1958-2025 |
| GrIS | RACMO2.3p2-ERA | ocx | SDBN1-4000m | annual | 680 | 1.5 GB | acabf, dacabfdz, dmrrodz, dtasdz, dtsdz, mrro, pr, sgd, tas, ts | 1958-2025 |

### File versions per product

| region | product | versions | files |
|---|---|---|---:|
| AIS | GEMB-SDBN1-2000m | v2 | 17,031 |
| AIS | GEMB-SDBN1-8000m | v2 | 17,030 |
| AIS | SDBN1-2000m | v1, v2 | 16,345 |
| AIS | SDBN1-8000m | v1, v2 | 16,917 |
| AIS | cold | v2 | 3 |
| AIS | dEBM2-8000m | v1 | 40,024 |
| AIS | fracture |  | 55 |
| AIS | main | v2 | 3 |
| AIS | ocean | v3 | 1,336 |
| AIS | ocean/ISMIP7/8km-60m | v3 | 1 |
| AIS | vary | v2 | 3 |
| AIS | warm | v2 | 3 |
| GrIS | GEMB-SDBN1-1000m | v1, v2 | 18,426 |
| GrIS | GEMB-SDBN1-4000m | v1, v2 | 18,426 |
| GrIS | SDBN1-1000m | v1, v2, v3 | 17,999 |
| GrIS | SDBN1-4000m | v1, v2, v3 | 17,997 |
| GrIS | dEBM2-1000m | v1 | 43,508 |
| GrIS | dEBM2-4000m | v1 | 43,507 |
| GrIS | ocean-1000m | v1, v2 | 8,244 |
| GrIS | ocean-4000m | v1, v2 | 8,244 |

## Submissions

| run | files | size | variables |
|---|---:|---:|---:|
| `GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ctrl_C009` | 38 | 33.0 GB | 38 |
| `GrIS_UAF_PISM_m001_CESM2-WACCM_f001_historical_C001` | 38 | 3.5 GB | 38 |
| `GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp126_C005` | 38 | 33.0 GB | 38 |
| `GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp370_C003` | 38 | 9.9 GB | 38 |
| `GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp585_C007` | 38 | 31.2 GB | 38 |
| `GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ctrl_C010` | 38 | 32.9 GB | 38 |
| `GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_historical_C002` | 38 | 3.5 GB | 38 |
| `GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ssp126_C006` | 38 | 33.0 GB | 38 |
| `GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ssp370_C004` | 38 | 9.9 GB | 38 |
| `GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ssp585_C008` | 38 | 32.1 GB | 38 |
| `GrIS_UAF_PISM_m001_OCX_f001_OCX_C011` | 38 | 4.1 GB | 38 |

## Findings by code

| code | severity | count |
|---|---|---:|
| `unmodelled_dataset` | info | 220 |
| `unknown_experiment` | error | 38 |
| `non_data_file` | info | 12 |
| `unknown_variable` | error | 11 |
| `bad_filename` | error | 1 |

## Keys with errors

- `ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/ocean/extras/climatology/sa/sa_CESM2-WACCM_climatology_r1i1p1f1_ismip8km_60m_1850_199501-201412.nc`: bad_filename:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C001/scalar_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_historical_C001_1985-2014.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C002/scalar_GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_historical_C002_1985-2014.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C003/scalar_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp370_C003_2015-2100.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C004/scalar_GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ssp370_C004_2015-2100.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C005/scalar_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp126_C005_2015-2300.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C006/scalar_GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ssp126_C006_2015-2300.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C007/scalar_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ssp585_C007_2015-2300.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C008/scalar_GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ssp585_C008_2015-2300.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C009/scalar_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_ctrl_C009_2015-2300.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C010/scalar_GrIS_UAF_PISM_m001_MRI-ESM2-0_f001_ctrl_C010_2015-2300.nc`: unknown_variable:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/acabf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/base_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/dlithkdt_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/hfgeoubed_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/iareafl_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/iareagr_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/libmassbffl_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/libmassbfgr_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/licalvf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/lifmassbf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/ligroundf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/lim_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/limnsw_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/litempbotfl_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/litempbotgr_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/litemptop_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/lithk_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/orog_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/scalar_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_variable:error, unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/sftflf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/sftgif_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/sftgrf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/strbasemag_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/tendacabf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/tendlibmassbffl_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/tendlibmassbfgr_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/tendlicalvf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/tendlifmassbf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/tendligroundf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/topg_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/xvelbase_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/xvelmean_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/xvelsurf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/yvelbase_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/yvelmean_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/yvelsurf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/zvelbase_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error
- `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/zvelsurf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`: unknown_experiment:error

## Example messages per finding code

- `bad_filename` (error): sa_CESM2-WACCM_climatology_r1i1p1f1_ismip8km_60m_1850_199501-201412.nc does not match <var>_<region>_<source>_<exp>_<product>_v<N>_<YYYY[-YYYY]>.nc  
  `ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/ocean/extras/climatology/sa/sa_CESM2-WACCM_climatology_r1i1p1f1_ismip8km_60m_1850_199501-201412.nc`
- `non_data_file` (info): README is not a NetCDF file  
  `ismip/ismip7-ais-forcing/data/parameterisations/fracture/README`
- `unmodelled_dataset` (info): no convention model for 'ismip7-ais-melt-calibration'; recorded as-is  
  `ismip/ismip7-ais-melt-calibration/data/LICENSES/CC-BY-4.0.txt`
- `unknown_variable` (error): 'scalar' is not in the ISMIP7 data request  
  `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C001/scalar_GrIS_UAF_PISM_m001_CESM2-WACCM_f001_historical_C001_1985-2014.nc`
- `unknown_experiment` (error): 'OCX' is not an ISMIP7 experiment (experiment names are lower case: 'ocx')  
  `ismip/ismip7-uaf-pism/submitted/GrIS/UAF/PISM/CORE/C011/acabf_GrIS_UAF_PISM_m001_OCX_f001_OCX_C011_1990-2024.nc`
