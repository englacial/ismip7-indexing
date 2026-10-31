# Phase A adversarial review (clean-context Opus, 2026-10-31)

Reviewed commit f5077d0 (`ismip7_helper/virtualize.py`, `tests/test_virtualize.py`, `tests/conftest.py`). Findings marked "confirmed" were reproduced against synthetic NetCDF4 files; 1 and 2 were also written to a local Icechunk repo and read back with `xr.open_zarr`.

## Findings, most severe first

**1. virtualize.py:236-237. Dropping `_FillValue` means readers never mask fill values. Confirmed.**
VirtualiZarr keeps the fill value as a base64 `_FillValue` attribute (e.g. `'AAAAAAAAnkc='`). That is exactly the form xarray writes and decodes for Zarr v3. After `to_icechunk`, `xr.open_zarr` returns `9.96921e+36` unmasked; keeping the attribute, the same read returns `nan` with `encoding["_FillValue"]=9.969e36`. Fix: keep the attribute; add a round-trip test asserting NaN at a fill cell.

**2. virtualize.py:145-160. The stored `time` has no units or calendar. Confirmed.**
`units`/`calendar` are moved to `.encoding`, which xarray only honours for datetime64/cftime values; for float64 they are discarded on write. Fix: put them in `attrs`, drop from encoding; assert in a round-trip test.

**3. virtualize.py:411-422. The time-axis check is loose, and `xr.merge` pads to the union. Confirmed.**
`np.allclose(atol=0.5)` keeps `rtol=1e-5`, about 1.4 days near year 2100; `xr.merge` defaults to `join="outer"`. A 12-hour offset between two variables yields a 24-step axis with fill-chunks. Fix: `rtol=0` and small `atol` or exact equality; `xr.merge(..., join="exact")` or overwrite each dataset's `time` with the reference.

**4. virtualize.py:386-406. Non-monotonic, wrongly dated or gapped axes pass `concat_group`. Confirmed.**
Strict increase is checked per file only; only filename years are compared. A `_1851.nc` containing 1850 dates concatenates into a non-monotonic axis; 1850 + 1852 with no 1851 is accepted. Fix: check `diff(time) > 0` after concatenation, each file's dates within `[year_start, year_end]`, consecutive files contiguous (`b0 == a1 + 1`).

**5. virtualize.py:398. Files with different fill values are concatenated silently. Confirmed.**
VirtualiZarr checks dtype, codecs, chunk shape, not `fill_value`; the result takes the first file's. Fix: compare `metadata.fill_value` across files (NaN-aware) and raise.

**6. virtualize.py:236. Dropping `missing_value` loses masking when it differs from `_FillValue`. Confirmed.**
Fix: if present and different from the fill value, keep it as an attribute or refuse the file.

**7. virtualize.py:197-198. NaN source coordinates pass the 1 m grid check. Confirmed.**
`np.max(np.abs(src - ref))` is NaN and `NaN > 1.0` is False; real files carry x/y with `_FillValue=NaN`. Fix: require `np.all(np.isfinite(src))` first.

**8. virtualize.py:125-137. No calendar whitelist; some calendars fail with raw `ValueError`s. Confirmed.**
`all_leap`/`366_day` 29 Feb of a non-leap year and `360_day` 30 Feb raise `ValueError`; `julian` is silently relabelled Gregorian (12-day shift); `months since` in `360_day` is accepted; microseconds are dropped. Fix: accept only `{standard, gregorian, proleptic_gregorian, noleap, 365_day}` (lower-cased); pass `d.microsecond`; wrap cftime construction and raise `VirtualizeError`.

**9. virtualize.py:398 and 375. Library errors escape as non-`VirtualizeError` exceptions.**
Inconsistent chunk shapes between years, a partial chunk in a non-final file, different codecs, and the cftime errors all raise `ValueError`/`NotImplementedError`; `join="exact"` raises if `z` or `month` differ slightly (never normalised, unlike x/y). A Phase B loop catching `VirtualizeError` would crash. Fix: wrap `xr.concat` and `open_forcing_file` and re-raise as `VirtualizeError` with group, variable, years.

**10. virtualize.py:404 and 422. `combine_attrs="drop_conflicts"` silently discards provenance. Confirmed.**
`source_time_units` disappears when files use different epochs; `ismip7_file_version` disappears for a v2/v3 mix, which is not refused. Fix: refuse mixed versions within a variable or record them as a list; record per-file source time encodings in group attrs.

**11. virtualize.py:409.** The cross-variable time check is skipped when the first variable has no `time` dimension. Fix: take the reference from the first variable that has `time` and require all variables to agree on whether they have one.

**12. virtualize.py:190-191.** Missing `x`/`y` coordinate variables are not checked; the grid is assigned purely from shape. Fix: raise or note.

**13. Low severity.** The source-CRS note only looks at `crs`/`mapping` (`polar_stereographic`, `spatial_ref` are dropped by the parser first). `local_registry(root)` ignores `root`. `concat_group(opened=...)` never checks `opened` matches `files`. Removing `Comment` while keeping `history_of_appended_files`/`nco_openmp_thread_number` looks inverted and is case-sensitive. Written `time`/`x`/`y` get xarray's default `_FillValue: NaN`; set `encoding["_FillValue"]=None` on coordinates.

## Test gaps
- The calendar test cannot distinguish date-component from day-count reinterpretation (no leap day between epoch and date). Add noleap since 1850-01-16 with dates in 2100 (~60-day difference).
- `assert ds.attrs["ismip7_group"] if ... else True` is a tautology.
- Untested: dropping lat/lon/bounds (fixture never writes them); reversed axis; the GrIS CRS; float32 coords; coords with `_FillValue=NaN`; int64 time; climatology `month` files; MULTIYEAR ocean files with `z`; concatenating multi-step chunks; a non-multiple last file; `missing_value`; a Zarr round-trip.
- The fill-value test checks in-memory metadata only. `test_concat_group_rejects_time_mismatch` covers lengths only, not offsets.

## Checked and found sound
Hyphenated variable names; int64 noleap time; climatology `month` files; decadal ocean files with `z` (two decades concatenate to chunk grid (20,2,8,8)); wrong-hemisphere source CRS replaced and noted; VirtualiZarr refuses partial chunks except on the last input; `join="exact"` + `coords="minimal"` + `compat="override"` behave as intended; `assign_coords`/`normalize_grid` do not load or reorder data; attrs mutation does not leak to the caller; grid shapes unique per region and CF/WKT match EPSG:3031/3413; overlap and mixed-group refusal; year-order manifests; `_common_prefix`; non-monotonic/unitless time refused; nothing quadratic.

## Fold

Folded into `ismip7_helper/virtualize.py`, `ismip7_helper/store.py` and the tests (90 pass). Representative real files (AIS SDBN1/dEBM2/OCX 2 km and 8 km, AIS ocean decadal with `z`, OCX ocean, dEBM2 climatology, GrIS SDBN1 1/4 km, GrIS ocean tf) still open under the stricter checks.

- **1 accepted.** Data variables keep VirtualiZarr's base64 `_FillValue` attribute; an Icechunk round-trip test reads a fill cell back as NaN.
- **2 accepted.** `normalize_time` puts `units`/`calendar` in attrs and sets `encoding = {"_FillValue": None}`; `store.prepare_for_write` is now an idempotent safety net that only removes `_FillValue` from coordinate variables. The round trip checks that `time` decodes to datetime64.
- **3 accepted.** Variables must have exactly equal time axes (`np.array_equal`); `xr.merge(join="exact")`. Tested with a 12-hour offset.
- **4 accepted.** Each file's dates must lie within its filename years, consecutive files must be contiguous (`b0 == a1 + 1`), and the concatenated axis must increase strictly.
- **5 accepted.** Fill values (and `missing_value`) are compared across a variable's files, NaN-aware; a mismatch raises.
- **6 accepted with a change.** `missing_value` is dropped when it equals the array fill value (as in the real SDBN1 files), and kept with a note in `OpenedFile.notes` when it differs (xarray then masks both). A round-trip test checks the masking.
- **7 accepted.** x/y must be finite before the tolerance check.
- **8 accepted.** Calendar whitelist `{standard, gregorian, proleptic_gregorian, noleap, 365_day}` (lower-cased); microseconds kept; cftime errors re-raised as `VirtualizeError`.
- **9 accepted.** Parser errors, `xr.concat` and `xr.merge` failures are re-raised as `VirtualizeError` naming the key, or the group, variable and years.
- **10 accepted with a change.** Mixed file versions within one variable are refused (a sanity check; the bucket never mixes them). Different versions across variables are allowed: each data variable carries `ismip7_file_version`, and the group attribute is the single value or a `"var: vN"` list. `source_time_units`/`source_time_calendar` group attributes hold the single value, or a `"<file>: <value>"` list when they differ; they are removed from the merged `time` variable.
- **11 accepted.** The reference axis comes from the first variable that has `time`; all variables must agree on having one.
- **12 accepted.** A missing x or y coordinate variable raises (the grid cannot be verified).
- **13 accepted.** `local_registry()` drops its unused argument; `polar_stereographic`/`spatial_ref` are loaded and inspected like `crs`/`mapping`; tool-chatter removal is the documented, case-insensitive `NOISY_ATTRS` tuple (adds `history_of_appended_files`, `nco_openmp_thread_number`; `Comment` is kept as descriptive); coordinates get `encoding["_FillValue"] = None`; `concat_group(opened=...)` checks the keys match `files`.
- **Test gaps: accepted.** Added: noleap since 1850-01-16 with 2100 dates (plus a check that day-count reinterpretation is ~60 days off), rejected calendars, int64 and sub-second time, the tautology replaced, lat/lon/bounds dropped, reversed y, GrIS file with the southern CRS under `polar_stereographic`, float32 coords with NaN `_FillValue`, NaN in a coordinate, missing x/y, `missing_value` equal and different, dates outside the filename year, multi-step chunks with a short last file and a partial chunk in a non-final file, time offset between variables, a gap between years, mixed versions, overlap of multiyear files, differing fill values, differing source time units, `opened=` mismatch, a variable without time, and Icechunk round trips for fill masking, coordinate fill values and time decoding. Climatology `month` files and decadal ocean files with `z` are covered by the real-file checks above, not by synthetic tests.
