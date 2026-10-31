"""Phase A of the pipeline: turn ISMIP7 forcing files into virtual xarray datasets.

A *virtual* dataset holds chunk references (byte ranges in the source NetCDF
files on source.coop) instead of data, produced by VirtualiZarr's HDF5
parser. Coordinates are small and loaded for real so they can be normalised.

Per file, :func:`open_forcing_file` does what the ISMIP6 helpers did one by
one, but driven by the typed conventions:

* drop bounds and 2-D lat/lon (regenerable; the ISMIP7 conventions say output
  carries 1-D x/y only);
* re-encode time as ``days since 1850-01-01`` in the ``standard`` calendar,
  float64, whatever the source used (five calendar/epoch combinations exist);
* replace x/y with the exact standard-grid coordinates after checking the
  source agrees to within a metre, and attach a correct ``crs`` variable
  (several products carry the other hemisphere's projection attributes);
* record provenance (source key, version, product, original encodings).

:func:`concat_group` then concatenates one group's files along time and
merges its variables into one dataset, ready for Phase B to write.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np
import xarray as xr

from . import SOURCE_BUCKET, SOURCE_REGION
from .conventions import ForcingFile, ForcingKind, GridSpec, grid_for_shape, parse_key
from .conventions.models import SUBMISSION_CALENDAR, SUBMISSION_TIME_UNITS

logger = logging.getLogger(__name__)

SOURCE_URL = f"s3://{SOURCE_BUCKET}"

#: Variables loaded into memory (everything else stays virtual). The CRS
#: scalars are loaded so their (sometimes wrong) attributes can be inspected
#: before they are dropped and replaced.
LOADABLE = ("time", "x", "y", "z", "month", "time_bnds", "time_bounds", "crs", "mapping")
#: Variables dropped: regenerable bounds, 2-D lat/lon, source CRS scalars.
DROP = (
    "lat",
    "lon",
    "latitude",
    "longitude",
    "lat_bnds",
    "lon_bnds",
    "x_bnds",
    "y_bnds",
    "z_bnds",
    "time_bnds",
    "time_bounds",
    "nv",
    "nv4",
    "bnds",
    "crs",
    "mapping",
    "spatial_ref",
    "polar_stereographic",
)
CRS_VAR = "crs"
#: Tolerance for the source x/y to agree with the standard grid, in metres.
GRID_TOLERANCE_M = 1.0


class VirtualizeError(RuntimeError):
    """A file cannot be virtualised as an ISMIP7 forcing file."""


# --------------------------------------------------------------------------- registry


def source_registry(region: str = SOURCE_REGION):
    """An ObjectStoreRegistry for anonymous reads of the source.coop bucket."""
    import obstore
    from obspec_utils.registry import ObjectStoreRegistry

    store = obstore.store.from_url(SOURCE_URL, skip_signature=True, region=region)
    return ObjectStoreRegistry({SOURCE_URL: store})


def local_registry(root: str):
    """A registry for ``file://`` URLs under ``root`` (tests and local runs)."""
    import obstore
    from obspec_utils.registry import ObjectStoreRegistry

    return ObjectStoreRegistry({"file://": obstore.store.LocalStore()})


def key_to_url(key: str) -> str:
    return f"{SOURCE_URL}/{key}"


# --------------------------------------------------------------------------- time


def _decode_dates(values: np.ndarray, units: str, calendar: str):
    import cftime

    return cftime.num2date(values, units=units, calendar=calendar)


def normalize_time(ds: xr.Dataset, *, dim: str = "time") -> xr.Dataset:
    """Re-encode ``time`` as float64 ``days since 1850-01-01``, calendar ``standard``.

    The source dates are decoded with their own units and calendar, then the
    (year, month, day, hour, minute, second) components are re-interpreted in
    the standard calendar. For ``noleap``/``365_day`` sources this keeps the
    calendar date (a 1 July stays 1 July) rather than the day count, which is
    what the ISMIP7 time-encoding guidance does. The original encoding is kept
    in ``source_time_units`` / ``source_time_calendar`` attributes.
    """
    import cftime

    if dim not in ds.variables:
        return ds
    tvar = ds[dim]
    attrs = dict(tvar.attrs)
    units = attrs.get("units")
    calendar = attrs.get("calendar", "standard")
    if units is None:
        raise VirtualizeError(f"time variable has no units attribute: {attrs}")
    raw = np.asarray(tvar.values, dtype=np.float64)
    if raw.ndim != 1:
        raise VirtualizeError(f"time must be 1-D, got shape {raw.shape}")
    if not np.all(np.isfinite(raw)):
        raise VirtualizeError("time has non-finite values")

    dates = np.asarray(_decode_dates(raw, units, calendar)).ravel()
    std_dates = [
        cftime.DatetimeGregorian(d.year, d.month, d.day, d.hour, d.minute, d.second) for d in dates
    ]
    new_vals = np.asarray(
        cftime.date2num(std_dates, units=SUBMISSION_TIME_UNITS, calendar=SUBMISSION_CALENDAR),
        dtype=np.float64,
    )
    if len(new_vals) > 1 and not np.all(np.diff(new_vals) > 0):
        raise VirtualizeError("time is not strictly increasing after normalisation")

    new_attrs = {k: v for k, v in attrs.items() if k not in ("units", "calendar", "bounds")}
    new_attrs.update(
        {
            "standard_name": "time",
            "long_name": "time",
            "axis": "T",
            "source_time_units": units,
            "source_time_calendar": calendar,
        }
    )
    new_time = xr.Variable((dim,), new_vals, attrs=new_attrs)
    new_time.encoding = {
        "units": SUBMISSION_TIME_UNITS,
        "calendar": SUBMISSION_CALENDAR,
        "dtype": "float64",
    }
    return ds.assign_coords({dim: new_time})


# --------------------------------------------------------------------------- grid / crs


def _data_vars(ds: xr.Dataset) -> list[str]:
    return [v for v in ds.data_vars if ds[v].ndim >= 2]


def identify_grid(ds: xr.Dataset, region: str) -> GridSpec:
    """Find the standard grid a dataset's (y, x) dimensions correspond to."""
    if "x" not in ds.dims or "y" not in ds.dims:
        raise VirtualizeError(f"no x/y dimensions: {dict(ds.sizes)}")
    grid = grid_for_shape(region, ds.sizes["y"], ds.sizes["x"])  # type: ignore[arg-type]
    if grid is None:
        raise VirtualizeError(
            f"shape (y={ds.sizes['y']}, x={ds.sizes['x']}) is not a standard {region} grid"
        )
    return grid


def normalize_grid(ds: xr.Dataset, grid: GridSpec) -> xr.Dataset:
    """Replace x/y with the exact standard-grid coordinates and attach ``crs``.

    The source coordinates must agree with the standard grid to within
    :data:`GRID_TOLERANCE_M`; anything else is an error, not silently fixed.
    """
    gx, gy = grid.x(), grid.y()
    for name, ref in (("x", gx), ("y", gy)):
        if name in ds.variables:
            src = np.asarray(ds[name].values, dtype=np.float64)
            # A few products store y descending; tolerate that only if it is
            # the exact reverse, and record it.
            if src.shape != ref.shape:
                raise VirtualizeError(f"{name} has {src.shape}, grid expects {ref.shape}")
            if np.max(np.abs(src - ref)) > GRID_TOLERANCE_M:
                if np.max(np.abs(src[::-1] - ref)) <= GRID_TOLERANCE_M:
                    raise VirtualizeError(
                        f"{name} runs in the opposite direction to the standard grid; "
                        "flipping virtual chunks is not supported"
                    )
                raise VirtualizeError(
                    f"{name} differs from the {grid.region} {grid.resolution_m} m grid by up to "
                    f"{np.max(np.abs(src - ref)):.1f} m"
                )
    coords = {
        "x": xr.Variable(
            ("x",),
            gx,
            attrs={
                "standard_name": "projection_x_coordinate",
                "long_name": "x coordinate of projection",
                "units": "m",
                "axis": "X",
            },
        ),
        "y": xr.Variable(
            ("y",),
            gy,
            attrs={
                "standard_name": "projection_y_coordinate",
                "long_name": "y coordinate of projection",
                "units": "m",
                "axis": "Y",
            },
        ),
    }
    ds = ds.assign_coords(coords)
    crs = xr.Variable((), np.int32(0), attrs=grid.cf_grid_mapping_attrs())
    ds = ds.assign_coords({CRS_VAR: crs})
    for v in _data_vars(ds):
        ds[v].attrs["grid_mapping"] = CRS_VAR
        # The fill value lives in the Zarr array metadata; the attribute copies
        # VirtualiZarr leaves behind would be written as (base64) strings.
        for k in ("coordinates", "_FillValue", "missing_value"):
            ds[v].attrs.pop(k, None)
        ds[v].encoding.pop("coordinates", None)
    ds.attrs["grid_mapping"] = CRS_VAR
    ds.attrs["ismip7_grid"] = f"{grid.region} {grid.resolution_m} m (EPSG:{grid.epsg})"
    return ds


# --------------------------------------------------------------------------- per file


@dataclass
class OpenedFile:
    forcing: ForcingFile
    ds: xr.Dataset
    grid: GridSpec
    notes: list[str] = field(default_factory=list)


def open_forcing_file(
    key: str,
    registry,
    *,
    url: str | None = None,
    size: int | None = None,
) -> OpenedFile:
    """Open one forcing file virtually and normalise it.

    ``key`` is the bucket key (parsed for conventions); ``url`` overrides the
    location to read from (``file://...`` in tests). Raises
    :class:`VirtualizeError` for anything that cannot be made to conform.
    """
    from virtualizarr import open_virtual_dataset
    from virtualizarr.parsers import HDFParser

    res = parse_key(key, size)
    if res.forcing is None or not res.ok:
        raise VirtualizeError(f"{key}: {[f.message for f in res.findings]}")
    f = res.forcing
    if f.kind not in (ForcingKind.ANNUAL, ForcingKind.MULTIYEAR, ForcingKind.CLIMATOLOGY):
        raise VirtualizeError(f"{key}: kind {f.kind.value} is not handled by open_forcing_file")

    vds = open_virtual_dataset(
        url or key_to_url(key),
        registry=registry,
        parser=HDFParser(drop_variables=[v for v in DROP if v not in LOADABLE]),
        loadable_variables=list(LOADABLE),
        decode_times=False,
    )
    notes: list[str] = []
    source_crs = {
        v: dict(vds[v].attrs)
        for v in ("crs", "mapping", "polar_stereographic")
        if v in vds.variables
    }
    vds = vds.drop_vars([v for v in DROP if v in vds.variables])

    dvars = _data_vars(vds)
    if f.variable not in dvars:
        raise VirtualizeError(f"{key}: variable {f.variable!r} not in file, found {dvars}")
    extra = [v for v in dvars if v != f.variable]
    if extra:
        notes.append(f"dropped extra data variables {extra}")
        vds = vds.drop_vars(extra)

    grid = identify_grid(vds, f.region)
    if f.resolution_m is not None and grid.resolution_m != f.resolution_m:
        raise VirtualizeError(
            f"{key}: product says {f.resolution_m} m but the grid is {grid.resolution_m} m"
        )
    vds = normalize_grid(vds, grid)
    if source_crs:
        for name, attrs in source_crs.items():
            lat0 = attrs.get("latitude_of_projection_origin")
            if lat0 is not None and np.sign(float(lat0)) != (-1 if f.region == "AIS" else 1):
                notes.append(
                    f"source {name} had latitude_of_projection_origin={lat0} for {f.region}; replaced"
                )

    if f.kind == ForcingKind.CLIMATOLOGY and "month" in vds.dims and "time" not in vds.dims:
        pass  # month-indexed climatology: no time axis to normalise
    else:
        vds = normalize_time(vds)

    # Provenance. Keep the source's descriptive attrs, drop tool chatter.
    for noisy in ("CDI", "CDO", "NCO", "history", "netcdf", "_NCProperties", "Comment"):
        vds.attrs.pop(noisy, None)
    vds.attrs.update(
        {
            "source_key": key,
            "source_url": key_to_url(key),
            "ismip7_dataset": f.dataset,
            "ismip7_region": f.region,
            "ismip7_source": f.source,
            "ismip7_experiment": f.experiment,
            "ismip7_product": f.product or "",
            "ismip7_product_model": f.product_model or "",
            "ismip7_file_version": f"v{f.version}" if f.version is not None else "",
        }
    )
    return OpenedFile(forcing=f, ds=vds, grid=grid, notes=notes)


# --------------------------------------------------------------------------- per group


@dataclass
class GroupResult:
    group_path: str
    ds: xr.Dataset
    n_files: int
    notes: list[str]


def concat_group(
    files: Sequence[ForcingFile],
    registry,
    *,
    urls: dict[str, str] | None = None,
    opened: Iterable[OpenedFile] | None = None,
) -> GroupResult:
    """Concatenate one group's files along time and merge its variables.

    ``files`` are all the forcing files of one ``group_path`` (any order).
    Each variable's files are sorted by year and concatenated; the variables
    are then merged. Every variable must end up on the same time axis;
    otherwise :class:`VirtualizeError` is raised (the ISMIP7 products are
    complete, so a gap means a missing upload, not something to pad over).
    """
    files = list(files)
    if not files:
        raise VirtualizeError("no files")
    group_paths = {f.group_path for f in files}
    if len(group_paths) != 1:
        raise VirtualizeError(f"files span several groups: {sorted(group_paths)}")
    (group_path,) = group_paths

    if opened is None:
        opened = [
            open_forcing_file(f.key, registry, url=(urls or {}).get(f.key), size=f.size)
            for f in files
        ]
    opened = list(opened)
    notes = [f"{o.forcing.key}: {n}" for o in opened for n in o.notes]

    by_var: dict[str, list[OpenedFile]] = {}
    for o in opened:
        by_var.setdefault(o.forcing.variable or "", []).append(o)

    per_var: dict[str, xr.Dataset] = {}
    for var, items in by_var.items():
        items.sort(key=lambda o: (o.forcing.year_start or 0, o.forcing.year_end or 0))
        years = [(o.forcing.year_start, o.forcing.year_end) for o in items]
        for (a0, a1), (b0, b1) in pairwise(years):
            if b0 <= a1:
                raise VirtualizeError(
                    f"{group_path}/{var}: overlapping files {a0}-{a1} and {b0}-{b1}"
                )
        dss = [o.ds for o in items]
        if len(dss) == 1:
            per_var[var] = dss[0]
        else:
            per_var[var] = xr.concat(
                dss,
                dim="time",
                data_vars="minimal",
                coords="minimal",
                compat="override",
                combine_attrs="drop_conflicts",
                join="exact",
            )

    ref_var, ref_ds = next(iter(per_var.items()))
    if "time" in ref_ds.dims:
        ref_time = ref_ds["time"].values
        for var, ds in per_var.items():
            if (
                "time" not in ds.dims
                or len(ds["time"]) != len(ref_time)
                or not np.allclose(ds["time"].values, ref_time, atol=0.5)
            ):
                raise VirtualizeError(
                    f"{group_path}: time axis of {var} ({ds.sizes.get('time')}) differs from "
                    f"{ref_var} ({len(ref_time)})"
                )

    merged = xr.merge(per_var.values(), compat="override", combine_attrs="drop_conflicts")
    merged.attrs.pop("source_key", None)
    merged.attrs.pop("source_url", None)
    merged.attrs["ismip7_group"] = group_path
    merged.attrs["ismip7_n_source_files"] = len(opened)
    merged.attrs["source_keys_prefix"] = _common_prefix([o.forcing.key for o in opened])
    return GroupResult(group_path=group_path, ds=merged, n_files=len(opened), notes=notes)


def _common_prefix(keys: list[str]) -> str:
    import os

    return os.path.commonprefix(keys).rsplit("/", 1)[0] + "/" if keys else ""


__all__ = [
    "CRS_VAR",
    "DROP",
    "GRID_TOLERANCE_M",
    "LOADABLE",
    "GroupResult",
    "OpenedFile",
    "VirtualizeError",
    "concat_group",
    "identify_grid",
    "key_to_url",
    "local_registry",
    "normalize_grid",
    "normalize_time",
    "open_forcing_file",
    "source_registry",
]
