"""Pydantic models for ISMIP7 file names, directory layouts, grids and the data request.

Sources
-------
Submission file names and directory structure:
  * https://www.ismip.org/research/ismip7 ("ISMIP7 filenaming and folder conventions")
  * https://github.com/ismip/ISM_SimulationChecker  (docs/user/checks.md, "1. Naming")
  * https://github.com/ismip/ismip7-scalar-processing (docs/user/file-conventions.md)
Data request and experiments:
  * ISM_SimulationChecker/isschecker/data/ISMIP7_variable_request.csv
  * ISM_SimulationChecker/isschecker/data/experiments_ismip7.csv
Grids:
  * ISM_SimulationChecker/isschecker/data/gdfs/gdf_ISMIP7_<region>_<res>m.txt
Time encoding:
  * https://github.com/ismip/ismip7-time-encoding
Forcing (input) layout: derived empirically from the source.coop listing, see
  ``parse.py`` for the pattern and the tests for the evidence.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Region = Literal["AIS", "GrIS"]

# A field of a submission filename: letters, digits and hyphens only. The
# protocol forbids underscores, dots and "special characters" in source_id and
# ism_id; the checker accepts [A-Za-z0-9-] for the ESM field as well.
_FIELD_RE = re.compile(r"^[A-Za-z0-9-]+$")
# Forcing filenames allow dots in the source name (RACMO2.3p2-ERA).
_FORCING_FIELD_RE = re.compile(r"^[A-Za-z0-9.-]+$")


class Severity(StrEnum):
    ERROR = "error"  # the file cannot be used as-is
    WARNING = "warning"  # usable, but departs from the convention
    INFO = "info"


class VariableType(StrEnum):
    """ST = state (snapshot at end of year), FL = flux (annual mean, mid-year stamp)."""

    ST = "ST"
    FL = "FL"


class FileFinding(BaseModel):
    """One thing wrong (or notable) about one key."""

    model_config = ConfigDict(frozen=True)

    key: str
    severity: Severity
    code: str
    message: str


# --------------------------------------------------------------------------- data request


class VariableSpec(BaseModel):
    """One row of ISMIP7_variable_request.csv."""

    model_config = ConfigDict(frozen=True)

    name: str
    long_name: str
    dims: tuple[str, ...]  # e.g. ("x", "y", "t")
    type: VariableType
    standard_name: str | None = None
    units: str
    mandatory: bool
    comment: str | None = None
    min_value_ais: float | None = None
    max_value_ais: float | None = None
    min_value_gris: float | None = None
    max_value_gris: float | None = None
    range_severity: Severity | None = None
    fill_policy: str | None = None
    margin_severity: Severity | None = None
    fill_severity: Severity | None = None

    @property
    def is_gridded(self) -> bool:
        return "x" in self.dims and "y" in self.dims

    @property
    def is_scalar(self) -> bool:
        return self.dims == ("t",)

    @property
    def has_time(self) -> bool:
        return "t" in self.dims

    def value_range(self, region: Region) -> tuple[float | None, float | None]:
        if region == "AIS":
            return self.min_value_ais, self.max_value_ais
        return self.min_value_gris, self.max_value_gris


class Experiment(BaseModel):
    """One row of experiments_ismip7.csv."""

    model_config = ConfigDict(frozen=True)

    name: str
    start_year_min: int
    start_year_max: int
    end_year: int
    duration: int  # -1 when the modeller chooses the start year

    @property
    def fixed_start(self) -> bool:
        return self.start_year_min == self.start_year_max

    def allows_years(self, start: int, end: int) -> bool:
        return self.start_year_min <= start <= self.start_year_max and end == self.end_year


# --------------------------------------------------------------------------- grids


class GridSpec(BaseModel):
    """A standard ISMIP7 grid, from a CDO grid description file (gdf_ISMIP7_*.txt).

    The grids are cell-centre registered: ``xfirst`` is the centre of the first
    column. Both polar stereographic projections use the WGS84 ellipsoid.
    """

    model_config = ConfigDict(frozen=True)

    region: Region
    resolution_m: int
    nx: int
    ny: int
    xfirst: float
    yfirst: float
    xinc: float
    yinc: float
    proj_params: str
    grid_mapping_name: str = "polar_stereographic"

    @property
    def epsg(self) -> int:
        return 3031 if self.region == "AIS" else 3413

    @property
    def shape(self) -> tuple[int, int]:
        """(ny, nx), the order of a (y, x) array."""
        return (self.ny, self.nx)

    def x(self) -> np.ndarray:
        return self.xfirst + self.xinc * np.arange(self.nx)

    def y(self) -> np.ndarray:
        return self.yfirst + self.yinc * np.arange(self.ny)

    def cf_grid_mapping_attrs(self) -> dict[str, str | float]:
        """CF grid_mapping attributes for a ``crs`` variable on this grid.

        These are the values to *write*, overriding whatever the source files
        carry (several forcing products carry the other hemisphere's values).
        """
        if self.region == "AIS":
            lat0, lat_ts, lon0 = -90.0, -71.0, 0.0
        else:
            lat0, lat_ts, lon0 = 90.0, 70.0, -45.0
        return {
            "grid_mapping_name": "polar_stereographic",
            "latitude_of_projection_origin": lat0,
            "standard_parallel": lat_ts,
            "straight_vertical_longitude_from_pole": lon0,
            "false_easting": 0.0,
            "false_northing": 0.0,
            "semi_major_axis": 6378137.0,
            "inverse_flattening": 298.257223563,
            "proj4": self.proj_params,
            "epsg_code": f"EPSG:{self.epsg}",
            "crs_wkt": self.crs_wkt(),
        }

    def crs_wkt(self) -> str:
        """WKT1 for EPSG:3031 / EPSG:3413, as proj4js and gridlook consume it."""
        if self.region == "AIS":
            return (
                'PROJCS["WGS 84 / Antarctic Polar Stereographic",'
                'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
                'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
                'PROJECTION["Polar_Stereographic"],PARAMETER["latitude_of_origin",-71],'
                'PARAMETER["central_meridian",0],PARAMETER["false_easting",0],'
                'PARAMETER["false_northing",0],UNIT["metre",1],AUTHORITY["EPSG","3031"]]'
            )
        return (
            'PROJCS["WGS 84 / NSIDC Sea Ice Polar Stereographic North",'
            'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
            'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
            'PROJECTION["Polar_Stereographic"],PARAMETER["latitude_of_origin",70],'
            'PARAMETER["central_meridian",-45],PARAMETER["false_easting",0],'
            'PARAMETER["false_northing",0],UNIT["metre",1],AUTHORITY["EPSG","3413"]]'
        )


# --------------------------------------------------------------------------- submissions

SET_IDS = ("CORE", "ESM", "PPE")

# Mandatory global attributes for a submission file. The filenames-and-conventions
# document lists all five; the checker (isschecker 0.9.0) enforces the first four.
REQUIRED_GLOBAL_ATTRS = ("group", "model", "contact_name", "contact_email", "crs")
# Submission files: float32 everywhere (data, masks, time), dims (time, [z,] y, x)
# with 1-D x/y, no lat/lon, time unlimited, "days since 1850-01-01", calendar
# "standard", default NetCDF4 _FillValue, no scale_factor/add_offset.
SUBMISSION_TIME_UNITS = "days since 1850-01-01"
SUBMISSION_CALENDAR = "standard"
_SET_LETTER = {"C": "CORE", "E": "ESM", "P": "PPE"}


class SubmissionFile(BaseModel):
    """One ISMIP7 model-output file, parsed from its ten-field name.

    ``<variable>_<region>_<group>_<model>_m<NNN>_<ESM>_f<NNN>_<experiment>_<set_counter>_<YYYY-YYYY>.nc``

    e.g. ``lithk_GrIS_VUW_PISM1_m001_CESM2-WACCM_f001_ctrl_C001_2015-2300.nc``

    Field rules follow the protocol page and the checker. Whether the variable
    exists in the data request and whether the years match the experiment are
    *not* enforced here (they need the registries); ``SubmissionFile.check()``
    does that and returns findings instead of raising, so one bad field does
    not hide the others.
    """

    model_config = ConfigDict(frozen=True)

    variable: str
    region: Region
    group: str
    model: str
    ism_member: Annotated[str, Field(pattern=r"^m\d{3}$")]
    esm: str
    forcing_member: Annotated[str, Field(pattern=r"^f\d{3}$")]
    experiment: str
    set_counter: Annotated[str, Field(pattern=r"^[CEP]\d{3}$")]
    year_start: int
    year_end: int
    filename: str

    @field_validator("variable", "group", "model", "esm", "experiment")
    @classmethod
    def _plain_field(cls, v: str) -> str:
        if not _FIELD_RE.match(v):
            raise ValueError(f"{v!r}: only letters, digits and hyphens are allowed")
        return v

    @model_validator(mode="after")
    def _years(self) -> SubmissionFile:
        if self.year_end < self.year_start:
            raise ValueError(f"year range {self.year_start}-{self.year_end} is reversed")
        return self

    @property
    def set_id(self) -> str:
        return _SET_LETTER[self.set_counter[0]]

    @property
    def expected_directory(self) -> str:
        """``<region>/<group>/<model>/<set_id>/<set_counter>``"""
        return f"{self.region}/{self.group}/{self.model}/{self.set_id}/{self.set_counter}"

    @property
    def run_id(self) -> str:
        """Everything but the variable: identifies one simulation."""
        return (
            f"{self.region}_{self.group}_{self.model}_{self.ism_member}_{self.esm}_"
            f"{self.forcing_member}_{self.experiment}_{self.set_counter}"
        )

    def check(
        self,
        variables: dict[str, VariableSpec] | None = None,
        experiments: dict[str, Experiment] | None = None,
        key: str | None = None,
    ) -> list[FileFinding]:
        """Convention findings that need the registries (variable list, experiments)."""
        key = key or self.filename
        out: list[FileFinding] = []
        if variables is not None and self.variable not in variables:
            from .registry import WEBSITE_ONLY_VARIABLES  # local import: registry imports models

            if self.variable in WEBSITE_ONLY_VARIABLES:
                out.append(
                    FileFinding(
                        key=key,
                        severity=Severity.WARNING,
                        code="website_only_variable",
                        message=(
                            f"'{self.variable}' is in the website data request but not in the "
                            "checker's ISMIP7_variable_request.csv; the checker will reject it"
                        ),
                    )
                )
            else:
                out.append(
                    FileFinding(
                        key=key,
                        severity=Severity.ERROR,
                        code="unknown_variable",
                        message=f"'{self.variable}' is not in the ISMIP7 data request",
                    )
                )
        if experiments is not None:
            exp = experiments.get(self.experiment)
            if exp is None:
                sev = Severity.ERROR
                hint = ""
                if self.experiment.lower() in experiments:
                    hint = f" (experiment names are lower case: '{self.experiment.lower()}')"
                out.append(
                    FileFinding(
                        key=key,
                        severity=sev,
                        code="unknown_experiment",
                        message=f"'{self.experiment}' is not an ISMIP7 experiment{hint}",
                    )
                )
            elif not exp.allows_years(self.year_start, self.year_end):
                out.append(
                    FileFinding(
                        key=key,
                        severity=Severity.ERROR,
                        code="year_range",
                        message=(
                            f"{self.year_start}-{self.year_end} is not allowed for "
                            f"{exp.name}: start in [{exp.start_year_min}, {exp.start_year_max}], "
                            f"end {exp.end_year}"
                        ),
                    )
                )
        return out


# --------------------------------------------------------------------------- forcing inputs


class ForcingKind(StrEnum):
    """What a forcing file holds, inferred from where it sits in the tree."""

    ANNUAL = "annual"  # <product>/<variable>/<var>_..._<YYYY>.nc, 12 monthly steps
    MULTIYEAR = (
        "multiyear"  # <product>/<variable>/<var>_..._<YYYY-YYYY>.nc (AIS ocean decades, OCX)
    )
    CLIMATOLOGY = "climatology"  # <product>/extra(s)/climatology/<variable>/...
    BIAS = "bias"  # <product>/extras/bias/<variable>/...
    FRACTURE = "fracture"  # <ESM>/<exp>/fracture/*.nc, bespoke names
    GRID = "grid"  # data/grid/...
    PARAMETERISATION = "parameterisation"  # data/parameterisations/...
    OTHER = "other"


class ForcingFile(BaseModel):
    """One file of the ISMIP7 *input* forcing datasets on source.coop.

    Layout (derived from the bucket, 2026-10-31)::

        <dataset>/data/<forcing_dir>/<experiment_dir>/<product>/<variable>/
            <variable>_<region>_<source>_<experiment>_<product>_v<N>_<YYYY[-YYYY]>.nc

    For ESM-driven forcing ``forcing_dir == source == <ESM name>`` and
    ``experiment_dir == experiment`` (historical, ssp585, ctrl ...).
    For the observational era the directory order flips: ``forcing_dir == "OCX"``
    and ``experiment_dir`` is the data source (RACMO2.3p2-ERA, EN4, ocean), while
    the filename still reads ``<source>_OCX``. Both are normalised here into
    ``source`` (the climate/reanalysis product) and ``experiment`` (lower-cased,
    so ``OCX`` -> ``ocx``, matching the checker's experiment list).

    ``product`` is the downscaling/ocean product plus resolution, e.g.
    ``SDBN1-2000m``, ``dEBM2-8000m``, ``GEMB-SDBN1-1000m``, ``ocean``,
    ``ocean-4000m``; ``product_model`` and ``resolution_m`` split it.
    """

    model_config = ConfigDict(frozen=True)

    dataset: Literal["ismip7-ais-forcing", "ismip7-gris-forcing"]
    region: Region
    kind: ForcingKind
    source: str  # CESM2-WACCM, MRI-ESM2-0, RACMO2.3p2-ERA, EN4, OCX (ocean pathways) ...
    experiment: str  # historical, ssp126, ..., ctrl, ocx
    product: str | None  # SDBN1-2000m, ocean, ocean-1000m, main/cold/warm/vary ...
    variable: str | None
    version: int | None
    year_start: int | None
    year_end: int | None
    subpath: str  # path inside the dataset after "data/"
    key: str
    size: int | None = None

    @field_validator("source", "experiment")
    @classmethod
    def _plain(cls, v: str) -> str:
        if not _FORCING_FIELD_RE.match(v):
            raise ValueError(f"{v!r}: unexpected characters")
        return v

    @model_validator(mode="after")
    def _region_matches_dataset(self) -> ForcingFile:
        expect = "AIS" if self.dataset.endswith("ais-forcing") else "GrIS"
        if self.region != expect:
            raise ValueError(f"region {self.region} in a {self.dataset} key")
        return self

    @property
    def product_model(self) -> str | None:
        """``SDBN1-2000m`` -> ``SDBN1``; ``GEMB-SDBN1-1000m`` -> ``GEMB-SDBN1``; ``ocean-4000m`` -> ``ocean``."""
        if self.product is None:
            return None
        m = re.match(r"^(.*?)-(\d+)m$", self.product)
        return m.group(1) if m else self.product

    @property
    def resolution_m(self) -> int | None:
        """Grid spacing encoded in the product name, if any (AIS ocean has none: it is 8 km)."""
        if self.product is None:
            return None
        m = re.match(r"^(.*?)-(\d+)m$", self.product)
        if m:
            return int(m.group(2))
        if self.product == "ocean" and self.region == "AIS":
            return 8000
        return None

    @property
    def group_path(self) -> str | None:
        """Where this file's data lands in the Icechunk store: ``<source>/<experiment>/<product>``."""
        if self.product is None:
            return None
        if self.kind in (ForcingKind.CLIMATOLOGY, ForcingKind.BIAS):
            return f"{self.kind.value}/{self.source}/{self.experiment}/{self.product}"
        return f"{self.source}/{self.experiment}/{self.product}"

    @property
    def is_monthly_year(self) -> bool:
        return self.kind == ForcingKind.ANNUAL


__all__ = [
    "SET_IDS",
    "Experiment",
    "FileFinding",
    "ForcingFile",
    "ForcingKind",
    "GridSpec",
    "Region",
    "Severity",
    "SubmissionFile",
    "VariableSpec",
    "VariableType",
]
