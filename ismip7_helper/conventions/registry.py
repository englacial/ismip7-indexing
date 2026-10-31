"""Loaders for the vendored data request, experiment table and grid definitions.

All three come from https://github.com/ismip/ISM_SimulationChecker (the
checker is the reference implementation of the ISMIP7 conventions; the ISMIP7
protocol page says its spreadsheet "is the same as the one listed in a csv file
as part of the ISM_SimulationChecker"). ``data/PROVENANCE.txt`` records the
upstream commit they were copied from.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path

from .models import Experiment, GridSpec, Region, Severity, VariableSpec, VariableType

_DATA = resources.files(__package__) / "data"

# Variables the ISMIP7 website's data request lists but the checker's CSV does
# not (as of isschecker 0.9.0): the optional "own SMB" diagnostics for models
# that compute their own surface mass balance, and tfbase, which the checker
# spells thdrflf. Files named with these are not rejected by the conventions
# here, but the checker will refuse them until its CSV catches up.
# Source: https://docs.google.com/spreadsheets/d/17aWkDRZhhsJIS_YsUBcoQ9IatAoPChM1u9nyxp7Wdgk
# (the "ISMIP7_variable_request_website" sheet embedded in
# https://www.ismip.org/research/ismip7).
WEBSITE_ONLY_VARIABLES: frozenset[str] = frozenset(
    {"pr", "mrro", "prra", "snicem", "snicefreez", "sbl", "tas", "tfbase"}
)


def _read_csv(name: str, delimiter: str = ",") -> list[dict[str, str]]:
    with (_DATA / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


def _opt_float(s: str | None) -> float | None:
    s = (s or "").strip()
    return float(s) if s else None


def _opt_sev(s: str | None) -> Severity | None:
    s = (s or "").strip().lower()
    return Severity(s) if s else None


@lru_cache(maxsize=1)
def variable_registry() -> dict[str, VariableSpec]:
    """The ISMIP7 data request, keyed by variable short name."""
    out: dict[str, VariableSpec] = {}
    for row in _read_csv("ISMIP7_variable_request.csv"):
        name = row["Variable Name"].strip()
        out[name] = VariableSpec(
            name=name,
            long_name=row["long_name"].strip(),
            dims=tuple(d.strip() for d in row["Dim"].split(",")),
            type=VariableType(row["Type"].strip()),
            standard_name=(row.get("standard_name") or "").strip() or None,
            units=row["units"].strip(),
            mandatory=row["Mandatory (yes/no)"].strip().lower() == "yes",
            comment=(row.get("Comment") or "").strip() or None,
            min_value_ais=_opt_float(row.get("min_value_ais")),
            max_value_ais=_opt_float(row.get("max_value_ais")),
            min_value_gris=_opt_float(row.get("min_value_gris")),
            max_value_gris=_opt_float(row.get("max_value_gris")),
            range_severity=_opt_sev(row.get("range_severity")),
            fill_policy=(row.get("fill_policy") or "").strip() or None,
            margin_severity=_opt_sev(row.get("margin_severity")),
            fill_severity=_opt_sev(row.get("fill_severity")),
        )
    return out


@lru_cache(maxsize=1)
def experiment_registry() -> dict[str, Experiment]:
    """ISMIP7 experiments (lower-case names) with their allowed year ranges."""
    out: dict[str, Experiment] = {}
    for row in _read_csv("experiments_ismip7.csv", delimiter=";"):
        name = row["experiment"].strip()
        out[name] = Experiment(
            name=name,
            start_year_min=int(row["start_year_min"]),
            start_year_max=int(row["start_year_max"]),
            end_year=int(row["end_year"]),
            duration=int(row["duration"]),
        )
    return out


_GDF_NAME = re.compile(r"gdf_ISMIP7_(AIS|GrIS)_(\d+)m\.txt$")


def _parse_gdf(path: Path | resources.abc.Traversable) -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = (s.strip() for s in line.split("=", 1))
        vals[k] = v.strip('"')
    return vals


@lru_cache(maxsize=1)
def grid_registry() -> dict[tuple[Region, int], GridSpec]:
    """Standard ISMIP7 grids keyed by (region, resolution in metres)."""
    out: dict[tuple[Region, int], GridSpec] = {}
    for entry in (_DATA / "gdfs").iterdir():
        m = _GDF_NAME.search(entry.name)
        if not m:
            continue
        region, res = m.group(1), int(m.group(2))
        v = _parse_gdf(entry)
        out[(region, res)] = GridSpec(  # type: ignore[arg-type]
            region=region,
            resolution_m=res,
            nx=int(v["xsize"]),
            ny=int(v["ysize"]),
            xfirst=float(v["xfirst"]),
            yfirst=float(v["yfirst"]),
            xinc=float(v["xinc"]),
            yinc=float(v["yinc"]),
            proj_params=v["proj_params"],
            grid_mapping_name=v.get("grid_mapping_name", "polar_stereographic"),
        )
    return out


@lru_cache(maxsize=1)
def protocol_inventory() -> dict[tuple[str, str, str, str, str], str]:
    """Dataset versions the ISMIP7 protocol spreadsheet lists, keyed by
    (region, forcing_dir, experiment_dir, product, variable) -> version string.

    ``forcing_dir``/``experiment_dir`` are the raw Globus path segments, so the
    observational era reads ("GrIS", "OCX", "EN4", ...) as it does in the bucket.
    """
    out: dict[tuple[str, str, str, str, str], str] = {}
    for row in _read_csv("protocol_inventory_2026-10.csv"):
        out[
            (
                row["region"],
                row["forcing_dir"],
                row["experiment_dir"],
                row["product"],
                row["variable"],
            )
        ] = row["version"]
    return out


def grid_for_shape(region: Region, ny: int, nx: int) -> GridSpec | None:
    """Identify the standard grid from an array's (y, x) shape, or None."""
    for (reg, _), g in grid_registry().items():
        if reg == region and g.shape == (ny, nx):
            return g
    return None


__all__ = [
    "WEBSITE_ONLY_VARIABLES",
    "experiment_registry",
    "grid_for_shape",
    "grid_registry",
    "protocol_inventory",
    "variable_registry",
]
