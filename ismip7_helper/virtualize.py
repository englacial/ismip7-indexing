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

import functools
import logging
import os
import pickle
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

# How the HDF5 parser reads a source file: in fixed blocks of this many bytes,
# keeping at most HDF_READ_CACHE_BYTES of them. The parser only reads the file's
# metadata and chunk index, but a netCDF writer that appended chunks as it went
# leaves that index scattered through the file, so every block it lands in is
# fetched whole. With the library default of 1 MiB blocks a finely chunked
# 2 km AIS file (1,740 chunks, 71 MB) costs 32 requests and 32 MB; at 32 KiB it
# is the same 34 requests for 1.2 MB, which turns a Lambda's open phase from
# bandwidth-bound (about 110 MB/s) into latency-bound. Coarsely chunked files
# read a few hundred KB either way.
HDF_READ_BLOCK_BYTES = 32 * 1024
HDF_READ_CACHE_BYTES = 64 * 1024 * 1024
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


def _block_reader(store, path: str):
    """The parser's file reader with :data:`HDF_READ_BLOCK_BYTES` blocks."""
    from obspec_utils.readers import BlockStoreReader

    return BlockStoreReader(
        store,
        path,
        block_size=HDF_READ_BLOCK_BYTES,
        max_cached_blocks=HDF_READ_CACHE_BYTES // HDF_READ_BLOCK_BYTES,
    )


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
            parser=HDFParser(
                drop_variables=[v for v in DROP if v not in LOADABLE],
                reader_factory=_block_reader,
            ),
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


@dataclass(frozen=True)
class NativeTail:
    """Steps of a virtual array that must be stored natively (see :func:`_placeholder_tails`)."""

    var: str
    start: int  # first index along axis 0
    n: int  # steps
    url: str  # the source file holding them
    key: str


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
    #: Trailing steps whose source file is chunked differently from the series:
    #: the writer copies them into the store as native chunks (the only data
    #: copied; under 1% of those series).
    native_tails: list[NativeTail] = field(default_factory=list)
    #: CPU seconds (user + system) spent by the worker *processes* opening the
    #: files, summed over the workers; None when the files were opened in
    #: this process (threads), whose CPU time ``getrusage(RUSAGE_SELF)`` sees.
    worker_cpu_seconds: float | None = None


def _uniform_or_list(pairs: list[tuple[str, str]]) -> str | list[str]:
    """The single value if all agree, else ``["<label>: <value>", ...]``."""
    values = {v for _, v in pairs}
    if len(values) == 1:
        return next(iter(values))
    return [f"{label}: {v}" for label, v in pairs]


#: A file's own fill value is listed as ``missing_value`` only when it cannot be
#: data: at least this large in magnitude (the netCDF default, 9.97e36, is).
MIN_LISTABLE_FILL = 1e30


def _decoded_fill_attr(v: xr.DataArray):
    """The CF ``_FillValue`` attribute as a number (VirtualiZarr keeps it base64)."""
    raw = v.attrs.get("_FillValue")
    if raw is None:
        return None
    if isinstance(raw, str | bytes):
        from xarray.backends.zarr import FillValueCoder

        return FillValueCoder.decode(raw, v.dtype)
    return raw


def _majority(values: list, dtype) -> tuple[int, int]:
    """Index of the most common value (NaN-aware, earliest on a tie) and its count."""
    counts = [sum(_same_value(v, w, dtype) for w in values) for v in values]
    i = max(range(len(values)), key=lambda i: (counts[i], -i))
    return i, counts[i]


def _spread_annual_stand_ins(label: str, items: list[OpenedFile], var: str) -> list[str]:
    """Spread a one-step file that stands in for a year over the series' months.

    MRI-ESM2-0 ssp585 ends its temperature fields with a 2300 file of one
    step, "Year 2300 computed as average of years 2290-2299", where every
    other file of the monthly series has twelve. Rather than drop the year
    or copy anything, the file's single row of chunks is referenced by all
    twelve monthly chunk keys of that year, so the year reads flat and the
    note says so. The stamps are the neighbouring file's months moved to
    that year. Only monthly series are handled; anything else is left for
    the time-axis check to reject.
    """
    import calendar
    from dataclasses import replace

    import cftime
    from virtualizarr.manifests import ChunkManifest, ManifestArray

    notes: list[str] = []
    if len(items) < 2:
        return notes

    def span(o: OpenedFile) -> int:
        y0 = o.forcing.year_start
        return 0 if y0 is None else (o.forcing.year_end or y0) - y0 + 1

    rates = [int(o.ds[var].shape[0]) / span(o) for o in items if span(o) > 0]
    if not rates or max(set(rates), key=rates.count) != 12:
        return notes
    for i, o in enumerate(items):
        if span(o) != 1 or int(o.ds[var].shape[0]) != 1:
            continue
        name = os.path.basename(o.forcing.key)
        marr = o.ds[var].data
        if not isinstance(marr, ManifestArray) or int(marr.metadata.chunks[0]) != 1:
            raise VirtualizeError(
                f"{label}: {name} has one step and cannot be spread over the year"
            )
        sibling = items[i - 1] if i else items[i + 1]
        sib_time = np.asarray(sibling.ds["time"].values, dtype=np.float64)
        if sib_time.shape != (12,):
            raise VirtualizeError(f"{label}: {name} has one step and its neighbour is not monthly")
        others = [v for v in o.ds.variables if v not in (var, "time") and "time" in o.ds[v].dims]
        if others:
            raise VirtualizeError(f"{label}: {name} has one step; cannot spread {others} with it")
        year = int(o.forcing.year_start)  # type: ignore[arg-type]
        stamps = np.asarray(
            cftime.date2num(
                [
                    cftime.DatetimeGregorian(
                        year,
                        d.month,
                        min(d.day, calendar.monthrange(year, d.month)[1]),
                        d.hour,
                        d.minute,
                        d.second,
                    )
                    for d in cftime.num2date(sib_time, SUBMISSION_TIME_UNITS, SUBMISSION_CALENDAR)
                ],
                SUBMISSION_TIME_UNITS,
                SUBMISSION_CALENDAR,
            ),
            dtype=np.float64,
        )
        grid = (12, *marr.manifest.shape_chunk_grid[1:])
        paths = np.full(grid, "", dtype=np.dtypes.StringDType())
        offsets = np.zeros(grid, dtype=np.uint64)
        lengths = np.zeros(grid, dtype=np.uint64)
        for key, e in marr.manifest.dict().items():
            rest = tuple(int(k) for k in key.split("."))[1:]
            for m in range(12):
                paths[(m, *rest)] = e["path"]
                offsets[(m, *rest)] = e["offset"]
                lengths[(m, *rest)] = e["length"]
        spread = ManifestArray(
            metadata=replace(marr.metadata, shape=(12, *marr.shape[1:])),
            chunkmanifest=ChunkManifest.from_arrays(paths=paths, offsets=offsets, lengths=lengths),
        )
        old_var, old_time = o.ds[var], o.ds["time"]
        ds = o.ds.drop_vars([var, "time"])
        ds = ds.assign({var: xr.Variable(old_var.dims, spread, attrs=dict(old_var.attrs))})
        ds = ds.assign_coords(
            time=xr.Variable(
                ("time",), stamps, attrs=dict(old_time.attrs), encoding=dict(old_time.encoding)
            )
        )
        comment = ds.attrs.pop("comment", None)
        o.ds = ds
        why = (
            f" ({comment.splitlines()[-1].strip()})" if isinstance(comment, str) and comment else ""
        )
        notes.append(
            f"{var}: {name}: one step standing in for {year}{why}; its chunks are referenced "
            f"by all 12 monthly steps of that year, which reads flat"
        )
    return notes


def _unify_fill_values(label: str, items: list[OpenedFile], var: str) -> list[str]:
    """Give the series one fill value; list a file's different one as ``missing_value``.

    Two fill values can differ between the files of a series. The Zarr fill
    value (HDF5's storage fill, what a missing chunk reads as): the series
    takes the most common one, and a file with another is accepted when every
    one of its chunks is present, since nothing then reads as its fill. The
    CF ``_FillValue`` attribute (what xarray masks to NaN): the series keeps
    the most common one and a file's different value is listed in
    ``missing_value``, which xarray masks as well (NaN needs no listing).
    A minority value small enough to be data (:data:`MIN_LISTABLE_FILL`) is
    refused rather than masked. The real archive switches between NaN and
    the netCDF default 9.97e36 in single files (GrIS ``sgd`` and ``tf``) and
    between storage fills 0 and 9.97e36 (``tas-anomaly``).
    """
    from dataclasses import replace

    from virtualizarr.manifests import ManifestArray

    notes: list[str] = []
    dtype = items[0].ds[var].dtype
    names = [os.path.basename(o.forcing.key) for o in items]

    fills = [_array_fill_value(o.ds[var]) for o in items]
    ref_i, _ = _majority(fills, dtype)
    ref_fill = fills[ref_i]
    for o, name, fill in zip(items, names, fills, strict=True):
        if _same_value(fill, ref_fill, dtype):
            continue
        marr = o.ds[var].data
        if not isinstance(marr, ManifestArray):
            raise VirtualizeError(f"{label}: fill value {fill} in {name} differs from {ref_fill}")
        if any(not e["path"] for e in marr.manifest.dict().values()):
            raise VirtualizeError(
                f"{label}: fill value {fill} in {name} differs from {ref_fill} in "
                f"{names[ref_i]} and the file has missing chunks that would read as it"
            )
        ds = o.ds.copy()
        ds[var] = ds[var].copy(
            data=ManifestArray(
                metadata=replace(marr.metadata, fill_value=ref_fill), chunkmanifest=marr.manifest
            )
        )
        o.ds = ds
        notes.append(
            f"{var}: {name}: storage fill value {fill} where the series uses {ref_fill}; "
            f"every chunk is present, so nothing reads as it"
        )

    attrs = [_decoded_fill_attr(o.ds[var]) for o in items]
    ref_i, _ = _majority(attrs, dtype)
    ref_attr = attrs[ref_i]
    listed: list[float] = []
    declared = [
        {float(m) for m in np.atleast_1d(np.asarray(o.ds[var].attrs.get("missing_value", [])))}
        for o in items
    ]
    for name, mvs in zip(names, declared, strict=True):
        for m in mvs:
            if all(m in d for d in declared) or abs(m) >= MIN_LISTABLE_FILL:
                listed.append(m)
            else:
                raise VirtualizeError(
                    f"{label}: missing_value {m} in {name} is not declared by every file; "
                    f"a value that small could be data, so it is not masked"
                )
    for name, value in zip(names, attrs, strict=True):
        if value is None or _same_value(value, ref_attr, dtype) or np.isnan(float(value)):
            continue
        if abs(float(value)) < MIN_LISTABLE_FILL:
            raise VirtualizeError(
                f"{label}: _FillValue {value} in {name} differs from {ref_attr} in "
                f"{names[ref_i]}; a value that small could be data, so it is not masked"
            )
        listed.append(float(value))
        notes.append(
            f"{var}: {name}: _FillValue {value} where the series uses {ref_attr}; "
            f"listed as missing_value, which xarray masks too"
        )
    listed = sorted({m for m in listed if not np.isnan(m) and not _same_value(m, ref_attr, dtype)})
    ref_raw = items[ref_i].ds[var].attrs.get("_FillValue")
    for o in items:
        a = o.ds[var].attrs
        if ref_raw is None:
            a.pop("_FillValue", None)
        else:
            a["_FillValue"] = ref_raw
        if listed:
            a["missing_value"] = listed[0] if len(listed) == 1 else listed
        else:
            a.pop("missing_value", None)
    return notes


def _codec_dicts(codecs, *, drop_level: bool) -> list[dict]:
    out = []
    for c in codecs:
        d = c.to_dict()
        if drop_level and d.get("name") == "numcodecs.zlib":
            d = {
                **d,
                "configuration": {k: v for k, v in d["configuration"].items() if k != "level"},
            }
        out.append(d)
    return out


def _zlib_level(codecs) -> int | None:
    for c in codecs:
        d = c.to_dict()
        if d.get("name") == "numcodecs.zlib":
            return d["configuration"].get("level")
    return None


def _normalize_codecs(label: str, items: list[OpenedFile], var: str) -> list[str]:
    """Re-label zlib codecs that differ from the first file's only in level.

    Decoding a zlib stream does not depend on the level it was written at,
    so files of one series written at levels 1 and 5 (GrIS ``dtsdz`` in the
    CESM2-WACCM ssp runs) are one array; ManifestArray refuses to
    concatenate differing codecs, so the metadata is rewritten to the first
    file's. Any other codec difference is left for the concatenation to reject.
    """
    from dataclasses import replace

    from virtualizarr.manifests import ManifestArray

    ref = items[0].ds[var].data
    if not isinstance(ref, ManifestArray):
        return []
    ref_codecs = ref.metadata.codecs
    ref_dicts = _codec_dicts(ref_codecs, drop_level=True)
    notes = []
    for o in items[1:]:
        marr = o.ds[var].data
        if not isinstance(marr, ManifestArray) or marr.metadata.codecs == ref_codecs:
            continue
        if _codec_dicts(marr.metadata.codecs, drop_level=True) != ref_dicts:
            continue
        new = ManifestArray(
            metadata=replace(marr.metadata, codecs=ref_codecs), chunkmanifest=marr.manifest
        )
        ds = o.ds.copy()
        ds[var] = ds[var].copy(data=new)
        o.ds = ds
        notes.append(
            f"{var}: {os.path.basename(o.forcing.key)}: zlib level "
            f"{_zlib_level(marr.metadata.codecs)} re-labelled as level "
            f"{_zlib_level(ref_codecs)} (decoding does not depend on it)"
        )
    return notes


def _placeholder_tails(
    label: str, items: list[OpenedFile], var: str
) -> tuple[list[NativeTail], list[str]]:
    """Swap files chunked unlike the series for placeholders to fill natively.

    A file of a series can be chunked differently from the rest (the
    writer's auto-chunking follows the file's shape: the five-year 2010-2014
    AIS ocean files, the two-month 2026 EN4 file, CanESM5's one-year
    1860-1860 ocean file in the middle of its series). A Zarr array has one
    chunk grid, and these files are gzip-chunked, so their chunks cannot be
    re-cut by reference. Instead each file chunked unlike the majority
    becomes a placeholder with the series' chunk grid and no sources, and a
    :class:`NativeTail` tells the writer to copy its steps into the store
    as native chunks. A file that would start inside a chunk of that grid
    is left for the concatenation to reject.
    """
    from dataclasses import replace

    from virtualizarr.manifests import ChunkManifest, ManifestArray

    arrays = [o.ds[var].data for o in items]
    if not all(isinstance(a, ManifestArray) for a in arrays):
        return [], []
    shapes = [tuple(a.metadata.chunks) for a in arrays]
    # the series' grid: the most common chunk shape, the earliest on a tie
    ref_chunks = max(dict.fromkeys(shapes), key=shapes.count)
    ref = arrays[shapes.index(ref_chunks)]
    odd = [i for i, c in enumerate(shapes) if c != ref_chunks]
    if not odd:
        return [], []
    starts = np.cumsum([0, *(int(a.shape[0]) for a in arrays)])
    if any(starts[i] % ref_chunks[0] for i in odd):
        return [], []  # a placeholder would begin inside a chunk
    tails, notes = [], []
    for i in odd:
        o = items[i]
        marr = arrays[i]
        start = int(starts[i])
        grid = tuple(-(-n // c) for n, c in zip(marr.shape, ref_chunks, strict=True))
        placeholder = ManifestArray(
            metadata=replace(ref.metadata, shape=tuple(marr.shape)),
            chunkmanifest=ChunkManifest.from_arrays(
                paths=np.full(grid, "", dtype=np.dtypes.StringDType()),
                offsets=np.zeros(grid, dtype=np.uint64),
                lengths=np.zeros(grid, dtype=np.uint64),
            ),
        )
        ds = o.ds.copy()
        ds[var] = ds[var].copy(data=placeholder)
        o.ds = ds
        n = int(marr.shape[0])
        # the URL the file was opened from (local in tests), as its chunks record it
        url = next((e["path"] for e in marr.manifest.dict().values() if e["path"]), None)
        if url is None:
            raise VirtualizeError(f"{label}: {o.forcing.key} has no chunks to copy")
        tails.append(NativeTail(var, start, n, url, o.forcing.key))
        notes.append(
            f"{var}: {os.path.basename(o.forcing.key)}: chunked {tuple(marr.metadata.chunks)} "
            f"where the series is chunked {ref_chunks}; its {n} step(s) from index {start} "
            f"are stored natively, re-chunked"
        )
    return tails, notes


def _concat_variable(
    group_path: str, var: str, items: list[OpenedFile]
) -> tuple[xr.Dataset, list[str], list[NativeTail]]:
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
        return items[0].ds, [], []
    notes = _spread_annual_stand_ins(label, items, var)
    notes.extend(_unify_fill_values(label, items, var))
    notes.extend(_normalize_codecs(label, items, var))
    tails, tail_notes = _placeholder_tails(label, items, var)
    notes.extend(tail_notes)
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
    return ds, notes, tails


def concat_group(
    files: Sequence[ForcingFile],
    registry=None,
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
    Python; threads give about 2x, six processes about 3.9x). A 1,800-file
    group takes about 1.2 s per file serially.

    The process pool is plain ``Process`` + ``Pipe`` workers (no semaphores,
    so it runs where ``/dev/shm`` is missing, as on Lambda); see
    :func:`_open_with_processes`. Each process builds its own registry for
    the URLs it reads, so ``registry`` cannot be combined with
    ``use_processes`` (a :class:`ValueError`). With ``registry=None`` the
    thread and serial paths use the same per-process cached registries
    (source bucket, or ``file://`` when the URL is local). If the processes
    cannot be started at all, the files are opened in threads instead, with
    a warning.

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

    if use_processes and registry is not None:
        raise ValueError(
            "concat_group: a registry cannot be passed to worker processes; "
            "use registry=None with use_processes=True (each process builds its own)"
        )
    worker_cpu: float | None = None
    if opened is None:
        url_of = urls or {}

        def _open(f: ForcingFile) -> OpenedFile:
            url = url_of.get(f.key)
            reg = registry if registry is not None else _cached_registry(_is_local_url(url))
            return open_forcing_file(f.key, reg, url=url, size=f.size)

        if max_workers > 1 and use_processes:
            try:
                opened, worker_cpu = _open_with_processes(
                    [(f.key, f.size, url_of.get(f.key)) for f in files], max_workers
                )
            except PoolStartError as e:
                logger.warning(
                    "%s: cannot start worker processes (%s); opening %d files in %d threads",
                    group_path,
                    e,
                    len(files),
                    max_workers,
                )
        if opened is None and max_workers > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                opened = list(pool.map(_open, files))
        elif opened is None:
            opened = [_open(f) for f in files]
    opened = list(opened)
    if {o.forcing.key for o in opened} != {f.key for f in files} or len(opened) != len(files):
        raise VirtualizeError(f"{group_path}: opened files do not match the files given")
    notes = [f"{o.forcing.key}: {n}" for o in opened for n in o.notes]

    by_var: dict[str, list[OpenedFile]] = {}
    for o in opened:
        by_var.setdefault(o.forcing.variable or "", []).append(o)

    per_var: dict[str, xr.Dataset] = {}
    native_tails: list[NativeTail] = []
    for var, items in by_var.items():
        per_var[var], var_notes, var_tails = _concat_variable(group_path, var, items)
        notes.extend(var_notes)
        native_tails.extend(var_tails)
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
    axes: dict[str, list[str]] = {}
    if with_time:
        # Variables of one product can sit on different time axes: the monthly
        # SMB fields and the *annual* gradient fields (dacabfdz, dtsdz, dmrrodz)
        # share a group. Each distinct axis becomes its own dimension: the
        # monthly one keeps ``time``, an annual one is ``time_annual``, anything
        # else ``time_<steps per year>``. Axes of the same length whose stamps
        # are within TIME_AXIS_TOLERANCE_DAYS of each other are one axis with
        # a skewed encoding (GrIS ``sgd`` files stamp 16 Jan / 14 Feb in a
        # noleap calendar where their siblings stamp the 15th): the variables
        # on the smaller side take the stamps of the larger, and the shift is
        # recorded. Larger same-frequency mismatches are defects.
        groups_by_axis: dict[tuple, list[str]] = {}
        for var in with_time:
            groups_by_axis.setdefault(tuple(per_var[var]["time"].values.tolist()), []).append(var)
        axis_notes = _align_time_axes(groups_by_axis, per_var)
        names: dict[tuple, str] = {}
        for key in sorted(groups_by_axis, key=len, reverse=True):
            n_years = len(set(time_years(np.asarray(key))))
            steps_per_year = round(len(key) / max(n_years, 1))
            name = (
                "time"
                if steps_per_year == 12
                else "time_annual"
                if steps_per_year == 1
                else f"time_{steps_per_year}"
            )
            clash = next((k for k, v in names.items() if v == name), None)
            if (
                clash is not None
                and _is_prefix(key, clash)
                and len(clash) - len(key) <= steps_per_year
            ):
                # the same axis, cut short by at most one file: ``dmrrodz`` ends
                # at 2299 where its siblings end at 2300. Pad the short variables
                # with missing chunks (read as the fill value) and record it. A
                # larger shortfall is a defect.
                axis_notes.extend(_pad_to_axis(per_var, groups_by_axis[key], key, clash))
                groups_by_axis[clash].extend(groups_by_axis.pop(key))
                continue
            if clash is not None:
                a, b = groups_by_axis[key][0], groups_by_axis[clash][0]
                t, ref = np.asarray(key), np.asarray(clash)
                detail = (
                    f"{len(t)} vs {len(ref)} steps"
                    if len(t) != len(ref)
                    else f"up to {np.max(np.abs(t - ref)):g} days apart"
                )
                raise VirtualizeError(f"{group_path}: time axis of {a} differs from {b} ({detail})")
            names[key] = name
        notes.extend(axis_notes)
        if "time" not in names.values():
            # no monthly axis: the most populous one is plain ``time``
            first = max(names, key=len)
            names[first] = "time"
        for key, name in names.items():
            axes[name] = groups_by_axis[key]
            if name == "time":
                continue
            for var in groups_by_axis[key]:
                ds = per_var[var]
                attrs = dict(ds["time"].attrs)
                attrs["long_name"] = f"{name.replace('_', ' ')} axis"
                per_var[var] = ds.rename({"time": name}).assign_coords(
                    {name: (name, ds["time"].values, attrs)}
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
            # per-file values live in the group attrs; the merged axes have no one source
            for axis in axes:
                merged[axis].attrs.pop(name, None)
        if len(axes) > 1:
            merged.attrs["ismip7_time_axes"] = [f"{a}: {', '.join(v)}" for a, v in axes.items()]
        if axis_notes:
            merged.attrs["ismip7_time_axis_notes"] = axis_notes
    return GroupResult(
        group_path=group_path,
        ds=merged,
        n_files=len(opened),
        notes=notes,
        sources=sorted(((f.key, f.size) for f in files), key=lambda p: p[0]),
        opened_at=opened_at,
        native_tails=native_tails,
        worker_cpu_seconds=worker_cpu,
    )


#: Monthly stamps this close (days) are the same axis with a skewed encoding.
TIME_AXIS_TOLERANCE_DAYS = 2.0


def _is_prefix(short: tuple, long: tuple) -> bool:
    return len(short) < len(long) and np.array_equal(
        np.asarray(short), np.asarray(long[: len(short)])
    )


def _pad_to_axis(
    per_var: dict[str, xr.Dataset], variables: list[str], short: tuple, long: tuple
) -> list[str]:
    """Extend ``variables`` from the ``short`` time axis to ``long`` with missing chunks.

    Virtual arrays get chunks with no source (the store reads them as the
    fill value); loaded variables along ``time`` (bounds) get NaN rows. The
    padded part must start on a chunk boundary, which holds for the one-step
    time chunks of the forcing files.
    """
    from dataclasses import replace

    from virtualizarr.manifests import ChunkManifest, ManifestArray

    k = len(long) - len(short)
    missing_years = sorted(set(time_years(np.asarray(long[len(short) :]))))
    notes = []
    for var in variables:
        ds = per_var[var]
        out = {}
        for name, v in ds.variables.items():
            if v.dims[:1] != ("time",) or name == "time":
                continue
            if isinstance(v.data, ManifestArray):
                marr = v.data
                tchunk = marr.metadata.chunks[0]
                if tchunk != 1 or len(short) % tchunk:
                    raise VirtualizeError(
                        f"{var}: cannot pad a time axis chunked by {tchunk} steps"
                    )
                grid = (k, *marr.manifest.shape_chunk_grid[1:])
                pad = ManifestArray(
                    metadata=replace(marr.metadata, shape=(k, *marr.shape[1:])),
                    chunkmanifest=ChunkManifest.from_arrays(
                        paths=np.full(grid, "", dtype=np.dtypes.StringDType()),
                        offsets=np.zeros(grid, dtype=np.uint64),
                        lengths=np.zeros(grid, dtype=np.uint64),
                    ),
                )
                data = np.concatenate([marr, pad], axis=0)
            else:
                fill = np.full((k, *v.shape[1:]), np.nan, dtype=np.result_type(v.dtype, np.float32))
                data = np.concatenate([np.asarray(v.values), fill], axis=0)
            out[name] = (v.dims, data, dict(v.attrs))
        time_attrs = dict(ds["time"].attrs)
        padded = ds.drop_vars([*out, "time"]).assign(out)
        per_var[var] = padded.assign_coords(time=("time", np.asarray(long), time_attrs))
        notes.append(
            f"{var}: {k} missing step(s) at the end of its time axis "
            f"({', '.join(map(str, missing_years))}); padded, read as the fill value"
        )
    return notes


def _align_time_axes(
    groups_by_axis: dict[tuple, list[str]], per_var: dict[str, xr.Dataset]
) -> list[str]:
    """Merge same-length axes within :data:`TIME_AXIS_TOLERANCE_DAYS` of each other.

    The axis carrying the most variables (then the first by variable name) is
    the reference; the others' variables are re-stamped with its values and
    moved onto it in ``groups_by_axis``. Returns one note per moved variable.
    """
    notes: list[str] = []
    ordered = sorted(groups_by_axis, key=lambda k: (-len(groups_by_axis[k]), groups_by_axis[k][0]))
    refs: list[tuple] = []
    for key in ordered:
        t = np.asarray(key)
        ref = next(
            (
                r
                for r in refs
                if len(r) == len(key)
                and np.max(np.abs(t - np.asarray(r))) <= TIME_AXIS_TOLERANCE_DAYS
            ),
            None,
        )
        if ref is None:
            refs.append(key)
            continue
        shift = float(np.max(np.abs(t - np.asarray(ref))))
        anchor = groups_by_axis[ref][0]
        for var in groups_by_axis.pop(key):
            ds = per_var[var]
            per_var[var] = ds.assign_coords(time=("time", np.asarray(ref), dict(ds["time"].attrs)))
            groups_by_axis[ref].append(var)
            notes.append(
                f"{var}: time stamps up to {shift:g} days from those of {anchor}; "
                f"aligned to the {anchor} axis"
            )
    return notes


def _is_local_url(url: str | None) -> bool:
    return bool(url and url.startswith("file://"))


@functools.cache
def _cached_registry(local: bool):
    """One registry (and so one object-store client) per process and kind of URL."""
    return local_registry() if local else source_registry()


def _open_in_process(args: tuple[str, int | None, str | None]) -> OpenedFile:
    """Open one file in a worker process with that process's cached registry."""
    key, size, url = args
    return open_forcing_file(key, _cached_registry(_is_local_url(url)), url=url, size=size)


class PoolStartError(OSError):
    """The worker processes could not be started (the caller falls back to threads)."""


def _process_cpu_seconds() -> float:
    """User plus system CPU seconds of this process (all its threads)."""
    import resource

    me = resource.getrusage(resource.RUSAGE_SELF)
    return me.ru_utime + me.ru_stime


def _pool_worker(conn, items: list[tuple[str, int | None, str | None]]) -> None:
    """Worker process: open each item, send one pickled ``(status, value)`` per file,
    then one ``("cpu", seconds)`` with its own CPU time."""
    try:
        for item in items:
            try:
                msg = ("ok", _open_in_process(item))
            except Exception as e:
                msg = ("err", e)
            try:
                data = pickle.dumps(msg)
            except Exception as e:  # an exception that does not pickle
                err = msg[1] if msg[0] == "err" else e
                data = pickle.dumps(
                    ("err", VirtualizeError(f"{item[0]}: {type(err).__name__}: {err}"))
                )
            conn.send_bytes(data)
        conn.send_bytes(pickle.dumps(("cpu", _process_cpu_seconds())))
    finally:
        conn.close()


def _open_with_processes(
    items: list[tuple[str, int | None, str | None]], n: int
) -> tuple[list[OpenedFile], float]:
    """Open ``(key, size, url)`` items in ``n`` worker processes without semaphores.

    Returns the opened files in the order of ``items`` and the workers' summed
    CPU seconds. (``getrusage(RUSAGE_CHILDREN)`` in the caller would miss
    them: forkserver workers are children of the server, not of the caller.)

    ``ProcessPoolExecutor`` builds its queues on POSIX semaphores, which need
    a writable ``/dev/shm``; Lambda has none. Here each of ``n`` forkserver
    processes gets a slice of the items and the write end of its own
    one-way ``Pipe``, and sends one pickled ``("ok", OpenedFile)`` or
    ``("err", exception)`` message per file, then ``("cpu", seconds)``; the
    parent waits on the read ends with ``multiprocessing.connection.wait``. The first error stops the
    other workers and is raised as :class:`VirtualizeError`; a worker that
    dies (killed, out of memory) without sending all its results is an error
    too. Results come back in the order of ``items``.

    forkserver, not fork: the caller may already run icechunk/obstore
    runtime threads (a warm Lambda, an opened repository), and a forked
    child of a threaded process can deadlock. The server preloads this
    module, so each worker starts without re-importing xarray and friends.
    On macOS the preloaded forkserver's children crash (fork safety of the
    Rust runtime threads), so ``spawn`` is used there instead.
    Raises :class:`PoolStartError` when the processes cannot be started.
    """
    import multiprocessing
    import sys
    from multiprocessing.connection import wait

    n = max(1, min(n, len(items)))
    if sys.platform == "darwin":
        # macOS: a forkserver that has preloaded this module holds icechunk /
        # obstore runtime threads, and its forked children crash (SIGSEGV).
        # spawn starts clean interpreters; each re-imports the module (~1 s).
        ctx = multiprocessing.get_context("spawn")
    else:
        ctx = multiprocessing.get_context("forkserver")
        ctx.set_forkserver_preload(["ismip7_helper.virtualize"])
    procs: list = []
    readers: dict = {}  # read end -> its worker process
    try:
        try:
            for i in range(n):
                r, w = ctx.Pipe(duplex=False)
                p = ctx.Process(
                    target=_pool_worker,
                    args=(w, items[i::n]),
                    name=f"ismip7-open-{i}",
                    daemon=True,
                )
                readers[r] = p
                try:
                    p.start()
                finally:
                    w.close()
                procs.append(p)
        except OSError as e:
            raise PoolStartError(f"{type(e).__name__}: {e}") from e

        by_key: dict[str, OpenedFile] = {}
        worker_cpu = 0.0
        first_error: BaseException | None = None
        pending = dict(readers)
        while pending and first_error is None:
            for r in wait(list(pending)):
                try:
                    status, value = pickle.loads(r.recv_bytes())
                except EOFError:  # the worker closed its end: done (or dead)
                    del pending[r]
                    r.close()
                    continue
                if status == "ok":
                    by_key[value.forcing.key] = value
                elif status == "cpu":
                    worker_cpu += float(value)
                else:
                    first_error = value
                    break
        if first_error is not None:
            if isinstance(first_error, VirtualizeError):
                raise first_error
            raise VirtualizeError(f"{type(first_error).__name__}: {first_error}") from first_error
        for p in procs:
            p.join()
        missing = [key for key, _, _ in items if key not in by_key]
        if missing:
            codes = sorted({p.exitcode for p in procs if p.exitcode})
            raise VirtualizeError(
                f"{len(missing)} files not opened (worker exit codes {codes}), e.g. {missing[0]}"
            )
        return [by_key[key] for key, _, _ in items], worker_cpu
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join(timeout=5)
        for r in readers:
            r.close()


def _common_prefix(keys: list[str]) -> str:
    """The deepest directory common to ``keys`` (with its trailing slash); ``""`` for none."""
    if not keys:
        return ""
    return os.path.commonpath([os.path.dirname(k) for k in keys]) + "/"


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
    "PoolStartError",
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
