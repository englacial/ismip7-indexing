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
    coord_dtype=np.int64,
    coord_offset_m: float = 0.0,
    extra_var: bool = False,
    chunks: tuple[int, int, int] | None = None,
    seed: int = 0,
) -> Path:
    """Write a NetCDF4 file shaped like an ISMIP7 monthly forcing file."""
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
        t = nc.createVariable("time", "f8", ("time",))
        t.units = time_units
        t.calendar = calendar
        t.standard_name = "time"
        t[:] = time_values
        x = nc.createVariable("x", coord_dtype, ("x",))
        x.standard_name = "projection_x_coordinate"
        x.units = "meter"
        x[:] = (grid.x() + coord_offset_m).astype(coord_dtype)
        y = nc.createVariable("y", coord_dtype, ("y",))
        y.standard_name = "projection_y_coordinate"
        y.units = "meter"
        y[:] = (grid.y() + coord_offset_m).astype(coord_dtype)
        crs = nc.createVariable("crs", "i4")
        for k, v in (crs_attrs or grid.cf_grid_mapping_attrs()).items():
            if k != "crs_wkt":
                setattr(crs, k, v)
        v = nc.createVariable(
            variable,
            "f4",
            ("time", "y", "x"),
            zlib=True,
            complevel=1,
            shuffle=True,
            chunksizes=chunks or (1, grid.ny, grid.nx),
            fill_value=np.float32(9.96921e36),
        )
        v.units = "kg m-2 s-1"
        v.standard_name = "land_ice_surface_specific_mass_balance_flux"
        v.grid_mapping = "crs"
        v[:] = rng.standard_normal((n_time, grid.ny, grid.nx)).astype("f4")
        if extra_var:
            e = nc.createVariable("extra", "f4", ("time", "y", "x"))
            e[:] = 1.0
        nc.title = f"{variable} synthetic"
        nc.history = "synthetic"
        nc.Conventions = "CF-1.6"
    return path


@pytest.fixture
def synthetic_root(tmp_path: Path) -> Path:
    return tmp_path / "bucket"


@pytest.fixture
def local_registry():
    from ismip7_helper.virtualize import local_registry

    return local_registry("/")
