"""Shared fixtures: synthetic ISMIP7-shaped NetCDF files on the small standard grids."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ismip7_helper.conventions import grid_registry

# Small standard grids keep synthetic files tiny: AIS 32 km is 191x191, GrIS 16 km is 106x181.
AIS_KEY_FMT = (
    "ismip/ismip7-ais-forcing/data/{esm}/{exp}/{product}/{var}/"
    "{var}_AIS_{esm}_{exp}_{product}_v{ver}_{year}.nc"
)
GRIS_KEY_FMT = (
    "ismip/ismip7-gris-forcing/data/{esm}/{exp}/{product}/{var}/"
    "{var}_GrIS_{esm}_{exp}_{product}_v{ver}_{year}.nc"
)
#: The fill value the real files use (NetCDF default for float).
NC_FILL = np.float32(9.96921e36)


def write_forcing_file(
    path: Path,
    *,
    region: str = "AIS",
    resolution_m: int = 32000,
    variable: str = "acabf",
    year: int = 1850,
    n_time: int = 12,
    time_units: str = "days since 1850-01-01 00:00:00",
    calendar: str = "standard",
    time_values: np.ndarray | None = None,
    crs_attrs: dict | None = None,
    crs_name: str = "crs",
    coord_dtype=np.int64,
    coord_offset_m: float = 0.0,
    extra_var: bool = False,
    chunks: tuple[int, ...] | None = None,
    seed: int = 0,
    time_dtype: str = "f8",
    fill_value: float = NC_FILL,
    missing_value: float | None = None,
    fill_cell: bool = False,
    write_latlon: bool = False,
    write_bounds: bool = False,
    write_xy: bool = True,
    coord_fill_nan: bool = False,
    y_descending: bool = False,
    global_attrs: dict | None = None,
    complevel: int = 1,
    n_z: int | None = None,
) -> Path:
    """Write a NetCDF4 file shaped like an ISMIP7 monthly forcing file.

    The options reproduce the variations seen in the real files (bucket plan
    section 1b): ``coord_dtype`` float32/int32/int64, x/y with ``_FillValue =
    NaN`` (``coord_fill_nan``), int64 time (``time_dtype="i8"``), 2-D lat/lon
    and bounds variables, a ``missing_value`` attribute. ``fill_cell`` writes
    the fill value at ``[0, 0, 0]``. ``n_z`` writes a 4-D ocean-shaped
    variable ``(time, z, y, x)`` with that many depth levels.
    """
    import netCDF4

    grid = grid_registry()[(region, resolution_m)]  # type: ignore[index]
    rng = np.random.default_rng(seed)
    if time_values is None:
        import cftime

        dates = [cftime.datetime(year, m, 15, calendar=calendar) for m in range(1, n_time + 1)]
        time_values = np.asarray(cftime.date2num(dates, time_units, calendar), dtype=np.float64)
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w", format="NETCDF4") as nc:
        nc.createDimension("time", None)
        nc.createDimension("y", grid.ny)
        nc.createDimension("x", grid.nx)
        if n_z:
            nc.createDimension("z", n_z)
            z = nc.createVariable("z", "f8", ("z",))
            z.units = "m"
            z.positive = "down"
            z[:] = np.arange(n_z, dtype="f8") * 60.0
        t = nc.createVariable("time", time_dtype, ("time",))
        t.units = time_units
        t.calendar = calendar
        t.standard_name = "time"
        t[:] = np.asarray(time_values).astype(time_dtype)
        if write_bounds:
            nc.createDimension("bnds", 2)
            t.bounds = "time_bnds"
            tb = nc.createVariable("time_bnds", time_dtype, ("time", "bnds"))
            tv = np.asarray(time_values, dtype="f8")
            tb[:] = np.stack([tv - 1, tv + 1], axis=1).astype(time_dtype)
        coord_fill = np.nan if coord_fill_nan else None
        if write_xy:
            x = nc.createVariable("x", coord_dtype, ("x",), fill_value=coord_fill)
            x.standard_name = "projection_x_coordinate"
            x.units = "meter"
            x[:] = (grid.x() + coord_offset_m).astype(coord_dtype)
            y = nc.createVariable("y", coord_dtype, ("y",), fill_value=coord_fill)
            y.standard_name = "projection_y_coordinate"
            y.units = "meter"
            yv = grid.y()[::-1] if y_descending else grid.y()
            y[:] = (yv + coord_offset_m).astype(coord_dtype)
            if write_bounds:
                for name, vals in (("x_bnds", grid.x()), ("y_bnds", grid.y())):
                    b = nc.createVariable(name, coord_dtype, (name[0], "bnds"))
                    h = grid.resolution_m / 2
                    b[:] = np.stack([vals - h, vals + h], axis=1).astype(coord_dtype)
        if write_latlon:
            for name in ("lat", "lon"):
                ll = nc.createVariable(name, "f8", ("y", "x"))
                ll.units = "degrees_north" if name == "lat" else "degrees_east"
                ll[:] = rng.uniform(-90, 90, (grid.ny, grid.nx))
        crs = nc.createVariable(crs_name, "i4")
        for k, v in (crs_attrs or grid.cf_grid_mapping_attrs()).items():
            if k != "crs_wkt":
                setattr(crs, k, v)
        dims = ("time", "z", "y", "x") if n_z else ("time", "y", "x")
        shape = (n_time, n_z, grid.ny, grid.nx) if n_z else (n_time, grid.ny, grid.nx)
        v = nc.createVariable(
            variable,
            "f4",
            dims,
            zlib=True,
            complevel=complevel,
            shuffle=True,
            chunksizes=chunks or (1, *shape[1:]),
            fill_value=np.float32(fill_value),
        )
        v.units = "kg m-2 s-1"
        v.standard_name = "land_ice_surface_specific_mass_balance_flux"
        v.grid_mapping = crs_name
        if write_latlon:
            v.coordinates = "lat lon"
        if missing_value is not None:
            v.missing_value = np.float32(missing_value)
        data = rng.standard_normal(shape).astype("f4")
        if fill_cell:
            data[(0,) * len(shape)] = fill_value
        v.set_auto_mask(False)
        v[:] = data
        if extra_var:
            e = nc.createVariable("extra", "f4", dims)
            e[:] = 1.0
        nc.title = f"{variable} synthetic"
        nc.history = "synthetic"
        nc.Conventions = "CF-1.6"
        for k, val in (global_attrs or {}).items():
            setattr(nc, k, val)
    return path


@pytest.fixture
def synthetic_root(tmp_path: Path) -> Path:
    return tmp_path / "bucket"


@pytest.fixture
def local_registry():
    from ismip7_helper.virtualize import local_registry

    return local_registry()
