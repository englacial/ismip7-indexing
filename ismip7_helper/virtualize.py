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
merges its variables into one dataset.

The result is in the state ``to_icechunk`` writes as is: loaded variables go
through ``xarray.to_zarr`` (which only applies CF time encoding to datetime
values), so ``time`` carries its ``units``/``calendar`` as attributes and
every coordinate has ``encoding["_FillValue"] = None``; virtual data
variables keep the base64 ``_FillValue`` attribute VirtualiZarr sets, which
is how xarray stores and decodes the CF fill value for Zarr v3.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
SOURCE_CRS_VARS = ("crs", "mapping", "polar_stereographic", "spatial_ref")
LOADABLE = ("time", "x", "y", "z", "month", "time_bnds", "time_bounds", *SOURCE_CRS_VARS)
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
    *SOURCE_CRS_VARS,
)
#: Global attributes removed from each file: tool chatter that says nothing
#: about the data (matched case-insensitively). Descriptive attributes such as
#: ``comment``/``Comment``, ``title`` or ``source`` are kept.
NOISY_ATTRS = (
    "CDI",
    "CDO",
    "NCO",
    "history",
    "history_of_appended_files",
    "nco_openmp_thread_number",
    "netcdf",
    "_NCProperties",
)
#: Coordinate variables written inline; CF discourages ``_FillValue`` on them.
COORD_VARS = ("time", "x", "y", "z", "month", "crs")
CRS_VAR = "crs"
#: Tolerance for the source x/y to agree with the standard grid, in metres.
GRID_TOLERANCE_M = 1.0
#: Source calendars accepted by :func:`normalize_time` (lower-cased). These are
#: the ones whose calendar dates all exist in the standard calendar; others
#: (``360_day``, ``all_leap``/``366_day``, ``julian``) would need a decision
#: about non-existent dates or a day shift, and do not occur in ISMIP7.
ACCEPTED_CALENDARS = frozenset(
    {"standard", "gregorian", "proleptic_gregorian", "noleap", "365_day"}
)


class VirtualizeError(RuntimeError):
    """A file cannot be virtualised as an ISMIP7 forcing file."""


# --------------------------------------------------------------------------- registry


def source_registry(region: str = SOURCE_REGION):
    """An ObjectStoreRegistry for anonymous reads of the source.coop bucket."""
    import obstore
    from obspec_utils.registry import ObjectStoreRegistry

    store = obstore.store.from_url(SOURCE_URL, skip_signature=True, region=region)
    return ObjectStoreRegistry({SOURCE_URL: store})


def local_registry():
    """A registry for absolute ``file://`` URLs (tests and local runs)."""
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
    (year, month, day, hour, minute, second, microsecond) components are
    re-interpreted in the standard calendar. For ``noleap``/``365_day``
    sources this keeps the calendar date (a 1 July stays 1 July) rather than
    the day count, which is what the ISMIP7 time-encoding guidance does. Only
    the calendars in :data:`ACCEPTED_CALENDARS` are accepted.

    The new ``units``/``calendar`` are attributes (what ``to_icechunk`` writes),
    and the original encoding is kept in ``source_time_units`` /
    ``source_time_calendar``. Idempotent.
    """
    import cftime

    if dim not in ds.variables:
        return ds
    tvar = ds[dim]
    attrs = dict(tvar.attrs)
    units = attrs.get("units")
    calendar = str(attrs.get("calendar", "standard")).lower()
    if units is None:
        raise VirtualizeError(f"time variable has no units attribute: {attrs}")
    if calendar not in ACCEPTED_CALENDARS:
        raise VirtualizeError(
            f"time calendar {calendar!r} is not one of {sorted(ACCEPTED_CALENDARS)}"
        )
    raw = np.asarray(tvar.values, dtype=np.float64)
    if raw.ndim != 1:
        raise VirtualizeError(f"time must be 1-D, got shape {raw.shape}")
    if not np.all(np.isfinite(raw)):
        raise VirtualizeError("time has non-finite values")

    try:
        dates = np.asarray(_decode_dates(raw, units, calendar)).ravel()
        std_dates = [
            cftime.DatetimeGregorian(
                d.year, d.month, d.day, d.hour, d.minute, d.second, d.microsecond
            )
            for d in dates
        ]
        new_vals = np.asarray(
            cftime.date2num(std_dates, units=SUBMISSION_TIME_UNITS, calendar=SUBMISSION_CALENDAR),
            dtype=np.float64,
        )
    except (ValueError, TypeError, OverflowError) as e:
        raise VirtualizeError(
            f"cannot decode time ({units!r}, {calendar!r}): {type(e).__name__}: {e}"
        ) from e
    if len(new_vals) > 1 and not np.all(np.diff(new_vals) > 0):
        raise VirtualizeError("time is not strictly increasing after normalisation")

    new_attrs = {k: v for k, v in attrs.items() if k not in ("units", "calendar", "bounds")}
    new_attrs.setdefault("source_time_units", units)
    new_attrs.setdefault("source_time_calendar", calendar)
    new_attrs.update(
        {
            "standard_name": "time",
            "long_name": "time",
            "axis": "T",
            "units": SUBMISSION_TIME_UNITS,
            "calendar": SUBMISSION_CALENDAR,
        }
    )
    new_time = xr.Variable((dim,), new_vals, attrs=new_attrs)
    new_time.encoding = {"_FillValue": None}
    return ds.assign_coords({dim: new_time})


def time_years(values: np.ndarray) -> np.ndarray:
    """Calendar years of normalised time values (``days since 1850-01-01``, standard)."""
    import cftime

    dates = cftime.num2date(
        np.asarray(values, dtype=np.float64), SUBMISSION_TIME_UNITS, SUBMISSION_CALENDAR
    )
    return np.array([d.year for d in np.atleast_1d(dates)], dtype=np.int64)


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


def _coord(dim: str, values: np.ndarray, attrs: dict) -> xr.Variable:
    v = xr.Variable((dim,), values, attrs=attrs)
    v.encoding = {"_FillValue": None}
    return v


def normalize_grid(ds: xr.Dataset, grid: GridSpec) -> xr.Dataset:
    """Replace x/y with the exact standard-grid coordinates and attach ``crs``.

    The source must have x and y coordinate variables, all finite, agreeing
    with the standard grid to within :data:`GRID_TOLERANCE_M`; anything else
    is an error, not silently fixed.
    """
    gx, gy = grid.x(), grid.y()
    for name, ref in (("x", gx), ("y", gy)):
        if name not in ds.variables:
            raise VirtualizeError(
                f"no {name} coordinate variable; cannot verify the {grid.region} "
                f"{grid.resolution_m} m grid"
            )
        src = np.asarray(ds[name].values, dtype=np.float64)
        if src.shape != ref.shape:
            raise VirtualizeError(f"{name} has {src.shape}, grid expects {ref.shape}")
        if not np.all(np.isfinite(src)):
            raise VirtualizeError(f"{name} has {int(np.sum(~np.isfinite(src)))} non-finite values")
        if np.max(np.abs(src - ref)) > GRID_TOLERANCE_M:
            # A few products store y descending; refuse rather than flip
            # (flipping virtual chunks is not possible).
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
        "x": _coord(
            "x",
            gx,
            {
                "standard_name": "projection_x_coordinate",
                "long_name": "x coordinate of projection",
                "units": "m",
                "axis": "X",
            },
        ),
        "y": _coord(
            "y",
            gy,
            {
                "standard_name": "projection_y_coordinate",
                "long_name": "y coordinate of projection",
                "units": "m",
                "axis": "Y",
            },
        ),
    }
    ds = ds.assign_coords(coords)
    crs = xr.Variable((), np.int32(0), attrs=grid.cf_grid_mapping_attrs())
    crs.encoding = {"_FillValue": None}
    ds = ds.assign_coords({CRS_VAR: crs})
    for v in _data_vars(ds):
        ds[v].attrs["grid_mapping"] = CRS_VAR
        # lat/lon are dropped, so the source's ``coordinates`` no longer holds.
        # ``_FillValue`` (VirtualiZarr's base64 form) is kept: it is what makes
        # readers mask fill cells.
        ds[v].attrs.pop("coordinates", None)
        ds[v].encoding.pop("coordinates", None)
    ds.attrs["grid_mapping"] = CRS_VAR
    ds.attrs["ismip7_grid"] = f"{grid.region} {grid.resolution_m} m (EPSG:{grid.epsg})"
    return ds


def _array_fill_value(var: xr.DataArray):
    """The Zarr fill value of a virtual variable (``None`` if not virtual)."""
    meta = getattr(var.data, "metadata", None)
    return None if meta is None else meta.fill_value


def _same_value(a, b, dtype) -> bool:
    """NaN-aware equality after casting both to ``dtype``."""
    if a is None or b is None:
        return a is None and b is None
    a = np.atleast_1d(np.asarray(a)).astype(dtype)
    b = np.atleast_1d(np.asarray(b)).astype(dtype)
    if a.shape != b.shape:
        return False
    if np.issubdtype(np.dtype(dtype), np.floating):
        return bool(np.all((a == b) | (np.isnan(a) & np.isnan(b))))
    return bool(np.all(a == b))


def _reconcile_missing_value(ds: xr.Dataset, notes: list[str]) -> xr.Dataset:
    """Drop ``missing_value`` where it equals the fill value; keep (and note) otherwise.

    The real files carry ``missing_value == _FillValue`` (9.96921e36), which
    is redundant. A different ``missing_value`` marks more cells to mask, so it
    stays as an attribute (xarray masks both on read).
    """
    for v in _data_vars(ds):
        if "missing_value" not in ds[v].attrs:
            continue
        mv = ds[v].attrs["missing_value"]
        fill = _array_fill_value(ds[v])
        values = np.atleast_1d(np.asarray(mv))
        if fill is not None and all(_same_value(m, fill, ds[v].dtype) for m in values):
            del ds[v].attrs["missing_value"]
        else:
            notes.append(f"{v}: kept missing_value={mv} (differs from the fill value {fill})")
    return ds


def _drop_noisy_attrs(attrs: dict) -> None:
    noisy = {a.lower() for a in NOISY_ATTRS}
    for k in [k for k in attrs if k.lower() in noisy]:
        del attrs[k]


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
    :class:`VirtualizeError` for anything that cannot be made to conform,
    including errors from the HDF5 parser.
    """
    from virtualizarr import open_virtual_dataset
    from virtualizarr.parsers import HDFParser

    res = parse_key(key, size)
    if res.forcing is None or not res.ok:
        raise VirtualizeError(f"{key}: {[f.message for f in res.findings]}")
    f = res.forcing
    if f.kind not in (ForcingKind.ANNUAL, ForcingKind.MULTIYEAR, ForcingKind.CLIMATOLOGY):
        raise VirtualizeError(f"{key}: kind {f.kind.value} is not handled by open_forcing_file")

    try:
        vds = open_virtual_dataset(
            url or key_to_url(key),
            registry=registry,
            parser=HDFParser(drop_variables=[v for v in DROP if v not in LOADABLE]),
            loadable_variables=list(LOADABLE),
            decode_times=False,
        )
    except Exception as e:
        raise VirtualizeError(f"{key}: cannot open: {type(e).__name__}: {e}") from e
    try:
        return _normalize_file(key, f, vds)
    except VirtualizeError as e:
        raise VirtualizeError(f"{key}: {e}") from e


def _normalize_file(key: str, f: ForcingFile, vds: xr.Dataset) -> OpenedFile:
    notes: list[str] = []
    source_crs = {v: dict(vds[v].attrs) for v in SOURCE_CRS_VARS if v in vds.variables}
    vds = vds.drop_vars([v for v in DROP if v in vds.variables])

    dvars = _data_vars(vds)
    if f.variable not in dvars:
        raise VirtualizeError(f"variable {f.variable!r} not in file, found {dvars}")
    extra = [v for v in dvars if v != f.variable]
    if extra:
        notes.append(f"dropped extra data variables {extra}")
        vds = vds.drop_vars(extra)

    grid = identify_grid(vds, f.region)
    if f.resolution_m is not None and grid.resolution_m != f.resolution_m:
        raise VirtualizeError(
            f"product says {f.resolution_m} m but the grid is {grid.resolution_m} m"
        )
    vds = normalize_grid(vds, grid)
    for name, attrs in source_crs.items():
        lat0 = attrs.get("latitude_of_projection_origin")
        if lat0 is not None and np.sign(float(lat0)) != (-1 if f.region == "AIS" else 1):
            notes.append(
                f"source {name} had latitude_of_projection_origin={lat0} for {f.region}; replaced"
            )
    vds = _reconcile_missing_value(vds, notes)

    if f.kind == ForcingKind.CLIMATOLOGY and "month" in vds.dims and "time" not in vds.dims:
        pass  # month-indexed climatology: no time axis to normalise
    else:
        vds = normalize_time(vds)
    if "time" in vds.dims and f.kind in (ForcingKind.ANNUAL, ForcingKind.MULTIYEAR):
        y0, y1 = f.year_start, f.year_end if f.year_end is not None else f.year_start
        years = time_years(vds["time"].values)
        if y0 is not None and (years.min() < y0 or years.max() > y1):
            raise VirtualizeError(
                f"dates span {years.min()}-{years.max()}, the filename says {y0}-{y1}"
            )
    for c in COORD_VARS:
        if c in vds.variables:
            for k in ("_FillValue", "missing_value"):
                vds[c].attrs.pop(k, None)
            vds[c].encoding["_FillValue"] = None

    # Provenance. Keep the source's descriptive attrs, drop tool chatter.
    _drop_noisy_attrs(vds.attrs)
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
    #: ``(key, size)`` of every source file, for the store's sources digest.
    sources: list[tuple[str, int | None]] = field(default_factory=list)
    #: When the headers were read (UTC, taken before opening the files); the
    #: store stamps the virtual references with it.
    opened_at: datetime | None = None


def _uniform_or_list(pairs: list[tuple[str, str]]) -> str | list[str]:
    """The single value if all agree, else ``["<label>: <value>", ...]``."""
    values = {v for _, v in pairs}
    if len(values) == 1:
        return next(iter(values))
    return [f"{label}: {v}" for label, v in pairs]


def _check_fill_values(label: str, items: list[OpenedFile], var: str) -> None:
    ref = items[0].ds[var]
    ref_fill = _array_fill_value(ref)
    ref_mv = ref.attrs.get("missing_value")
    for o in items[1:]:
        v = o.ds[var]
        fill = _array_fill_value(v)
        if not _same_value(fill, ref_fill, ref.dtype):
            raise VirtualizeError(
                f"{label}: fill value {fill} in {os.path.basename(o.forcing.key)} differs from "
                f"{ref_fill} in {os.path.basename(items[0].forcing.key)}"
            )
        mv = v.attrs.get("missing_value")
        if not _same_value(mv, ref_mv, ref.dtype):
            raise VirtualizeError(
                f"{label}: missing_value {mv} in {os.path.basename(o.forcing.key)} differs from "
                f"{ref_mv} in {os.path.basename(items[0].forcing.key)}"
            )


def _concat_variable(group_path: str, var: str, items: list[OpenedFile]) -> xr.Dataset:
    label = f"{group_path}/{var}"
    items.sort(key=lambda o: (o.forcing.year_start or 0, o.forcing.year_end or 0))
    versions = sorted({o.forcing.version for o in items}, key=lambda v: (v is None, v))
    if len(versions) > 1:
        raise VirtualizeError(f"{label}: mixed file versions {versions}")
    years = [(o.forcing.year_start, o.forcing.year_end or o.forcing.year_start) for o in items]
    for (a0, a1), (b0, b1) in pairwise(years):
        if a1 is None or b0 is None:
            raise VirtualizeError(f"{label}: several files without years cannot be ordered")
        if b0 <= a1:
            raise VirtualizeError(f"{label}: overlapping files {a0}-{a1} and {b0}-{b1}")
        if b0 != a1 + 1:
            raise VirtualizeError(f"{label}: gap between files {a0}-{a1} and {b0}-{b1}")
    if len(items) == 1:
        return items[0].ds
    _check_fill_values(label, items, var)
    try:
        ds = xr.concat(
            [o.ds for o in items],
            dim="time",
            data_vars="minimal",
            coords="minimal",
            compat="override",
            combine_attrs="drop_conflicts",
            join="exact",
        )
    except Exception as e:
        raise VirtualizeError(
            f"{label}: cannot concatenate years {years[0][0]}-{years[-1][1]}: "
            f"{type(e).__name__}: {e}"
        ) from e
    t = ds["time"].values
    if len(t) > 1 and not np.all(np.diff(t) > 0):
        raise VirtualizeError(f"{label}: time is not strictly increasing across files")
    return ds


def concat_group(
    files: Sequence[ForcingFile],
    registry,
    *,
    urls: dict[str, str] | None = None,
    opened: Iterable[OpenedFile] | None = None,
    max_workers: int = 1,
    use_processes: bool = False,
    opened_at: datetime | None = None,
) -> GroupResult:
    """Concatenate one group's files along time and merge its variables.

    Files are opened in a pool of ``max_workers``: threads by default, or
    processes with ``use_processes`` (HDF header parsing is CPU-bound in
    Python; threads give about 2x, processes scale with cores). A 1,800-file
    group takes about 1.2 s per file serially.

    ``files`` are all the forcing files of one ``group_path`` (any order).
    Each variable's files must share one version and be contiguous in years;
    they are sorted by year and concatenated, then the variables are merged.
    Every variable must end up on exactly the same time axis (or none have
    one); otherwise :class:`VirtualizeError` is raised (the ISMIP7 products
    are complete, so a gap means a missing upload, not something to pad over).

    Group attributes record the source time encodings and file versions: a
    single value when uniform, else a list of ``"<file or variable>: <value>"``.

    ``GroupResult.opened_at`` is the time just before the files were opened
    (or ``opened_at`` when ``opened`` was read earlier by the caller).
    """
    if opened_at is None and opened is None:
        opened_at = datetime.now(UTC)
    files = list(files)
    if not files:
        raise VirtualizeError("no files")
    group_paths = {f.group_path for f in files}
    if len(group_paths) != 1:
        raise VirtualizeError(f"files span several groups: {sorted(group_paths)}")
    (group_path,) = group_paths

    if opened is None:

        def _open(f: ForcingFile) -> OpenedFile:
            return open_forcing_file(f.key, registry, url=(urls or {}).get(f.key), size=f.size)

        if max_workers > 1 and use_processes:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            # processes cannot share the registry object; each builds its own.
            # forkserver, not fork: the caller may already run icechunk/obstore
            # runtime threads (a warm Lambda, an opened repository), and a
            # forked child of a threaded process can deadlock.
            ctx = multiprocessing.get_context("forkserver")
            with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as pool:
                opened = list(
                    pool.map(
                        _open_in_process, [(f.key, f.size, (urls or {}).get(f.key)) for f in files]
                    )
                )
        elif max_workers > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                opened = list(pool.map(_open, files))
        else:
            opened = [_open(f) for f in files]
    opened = list(opened)
    if {o.forcing.key for o in opened} != {f.key for f in files} or len(opened) != len(files):
        raise VirtualizeError(f"{group_path}: opened files do not match the files given")
    notes = [f"{o.forcing.key}: {n}" for o in opened for n in o.notes]

    by_var: dict[str, list[OpenedFile]] = {}
    for o in opened:
        by_var.setdefault(o.forcing.variable or "", []).append(o)

    per_var = {var: _concat_variable(group_path, var, items) for var, items in by_var.items()}
    for var in per_var:
        # a shallow copy, so the attribute does not leak into the caller's OpenedFile
        per_var[var] = ds = per_var[var].copy()
        ds[var].attrs["ismip7_file_version"] = by_var[var][0].ds.attrs["ismip7_file_version"]

    with_time = [var for var, ds in per_var.items() if "time" in ds.dims]
    if with_time and len(with_time) != len(per_var):
        raise VirtualizeError(
            f"{group_path}: only some variables have a time axis: {sorted(with_time)} "
            f"of {sorted(per_var)}"
        )
    if with_time:
        ref_var = with_time[0]
        ref_time = per_var[ref_var]["time"].values
        for var, ds in per_var.items():
            t = ds["time"].values
            if not np.array_equal(t, ref_time):
                detail = (
                    f"{len(t)} vs {len(ref_time)} steps"
                    if len(t) != len(ref_time)
                    else f"up to {np.max(np.abs(t - ref_time)):g} days apart"
                )
                raise VirtualizeError(
                    f"{group_path}: time axis of {var} differs from {ref_var} ({detail})"
                )

    try:
        merged = xr.merge(
            per_var.values(), compat="override", join="exact", combine_attrs="drop_conflicts"
        )
    except Exception as e:
        raise VirtualizeError(
            f"{group_path}: cannot merge variables {sorted(per_var)}: {type(e).__name__}: {e}"
        ) from e

    ordered = [o for var in per_var for o in by_var[var]]
    merged.attrs.pop("source_key", None)
    merged.attrs.pop("source_url", None)
    merged.attrs["ismip7_group"] = group_path
    merged.attrs["ismip7_n_source_files"] = len(opened)
    merged.attrs["source_keys_prefix"] = _common_prefix([o.forcing.key for o in opened])
    merged.attrs["ismip7_file_version"] = _uniform_or_list(
        [(var, per_var[var][var].attrs["ismip7_file_version"]) for var in per_var]
    )
    if with_time:
        for name in ("source_time_units", "source_time_calendar"):
            merged.attrs[name] = _uniform_or_list(
                [(os.path.basename(o.forcing.key), o.ds["time"].attrs[name]) for o in ordered]
            )
            # per-file values live in the group attrs; the merged axis has no one source
            merged["time"].attrs.pop(name, None)
    return GroupResult(
        group_path=group_path,
        ds=merged,
        n_files=len(opened),
        notes=notes,
        sources=sorted(((f.key, f.size) for f in files), key=lambda p: p[0]),
        opened_at=opened_at,
    )


def _open_in_process(args: tuple[str, int | None, str | None]) -> OpenedFile:
    """Worker for process pools: opens one file with a fresh registry (source or local)."""
    key, size, url = args
    registry = local_registry() if url and url.startswith("file://") else source_registry()
    return open_forcing_file(key, registry, url=url, size=size)


def _common_prefix(keys: list[str]) -> str:
    return os.path.commonprefix(keys).rsplit("/", 1)[0] + "/" if keys else ""


__all__ = [
    "ACCEPTED_CALENDARS",
    "COORD_VARS",
    "CRS_VAR",
    "DROP",
    "GRID_TOLERANCE_M",
    "LOADABLE",
    "NOISY_ATTRS",
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
    "time_years",
]
