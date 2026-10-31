"""Parse source.coop keys into :class:`ForcingFile` / :class:`SubmissionFile` records.

The forcing layout follows "ISMIP7 filenames and conventions" (19 June 2026,
revised 27 August 2026; linked from https://www.ismip.org/research/ismip7),
section "Conventions for climate forcing files": one main variable per file,
the filename collects the directory names separated by underscores, version
second to last, year or year range last, no underscores inside a component,
scenarios lower case, resolution in the variant (product) level, extras/ for
CMIP-derived climatology and bias. The regular expressions below encode that
as the bucket actually realises it (listing of 2026-10-31, 285k keys) and
``tests/test_full_listing.py`` keeps them honest. Anything that does not
fit is returned with a finding rather than raised, so a full-bucket parse
yields a report instead of a traceback.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from .models import (
    Experiment,
    FileFinding,
    ForcingFile,
    ForcingKind,
    Severity,
    SubmissionFile,
    VariableSpec,
)
from .registry import experiment_registry, variable_registry

FORCING_DATASETS = {"ismip7-ais-forcing": "AIS", "ismip7-gris-forcing": "GrIS"}

# ESM names seen in the bucket; all are CMIP6 source_ids. Anything else is
# reported as INFO so a typo or a new model is noticed, not rejected.
KNOWN_ESMS = frozenset(
    {
        "ACCESS-CM2",
        "CESM2-WACCM",
        "CanESM5",
        "GFDL-ESM4",
        "IPSL-CM6A-LR",
        "MPI-ESM1-2-HR",
        "MRI-ESM2-0",
        "UKESM1-0-LL",
    }
)

# Observational-era sources that sit under OCX/.
KNOWN_OCX_SOURCES = frozenset({"RACMO2.3p2-ERA", "EN4", "ocean"})

# Forcing variables seen in the bucket, with what they are. The ISMIP7
# protocol overview spreadsheet (AISFileVersion / GrISFileVersion tabs, see
# registry.protocol_inventory) lists the same set; it is reproduced here so a
# new or misspelt variable is noticed at parse time.
KNOWN_FORCING_VARIABLES: dict[str, str] = {
    "acabf": "surface mass balance flux (kg m-2 s-1)",
    "acabf-anomaly": "SMB anomaly relative to the 1960-1989 climatology",
    "pr": "precipitation",
    "tas": "near-surface air temperature",
    "tas-anomaly": "tas anomaly relative to the 1960-1989 climatology",
    "ts": "surface temperature",
    "ts-anomaly": "ts anomaly relative to the 1960-1989 climatology",
    "mrro": "runoff",
    "mrro-anomaly": "runoff anomaly relative to the 1960-1989 climatology (ctrl only)",
    "pr-anomaly": "precipitation anomaly relative to the 1960-1989 climatology (ctrl only)",
    "snm": "snow melt (climatology only)",
    "snowf": "snowfall (climatology only)",
    "sgd": "basin subglacial discharge (GrIS)",
    "dacabfdz": "vertical SMB gradient (lapse rate)",
    "dtsdz": "vertical ts gradient",
    "dtasdz": "vertical tas gradient",
    "dmrrodz": "vertical runoff gradient",
    "thetao": "sea water potential temperature (3-D ocean)",
    "so": "sea water salinity (3-D ocean)",
    "tf": "thermal forcing (ocean)",
    "ct": "conservative temperature (ocean extras)",
    "sa": "absolute salinity (ocean extras)",
}

_NC_RE = re.compile(
    r"^(?P<variable>[A-Za-z0-9-]+)_(?P<region>AIS|GrIS)_(?P<source>[A-Za-z0-9.-]+)_"
    r"(?P<experiment>[A-Za-z0-9-]+)_(?P<product>[A-Za-z0-9-]+)(?:_extras?_(?P<extra>climatology|bias))?"
    r"_v(?P<version>\d+)_(?P<y0>\d{4})(?:-(?P<y1>\d{4}))?\.nc$"
)

_SUBMISSION_RE = re.compile(
    r"^(?P<variable>[A-Za-z0-9-]+)_(?P<region>AIS|GrIS)_(?P<group>[A-Za-z0-9-]+)_"
    r"(?P<model>[A-Za-z0-9-]+)_(?P<ism_member>m\d{3})_(?P<esm>[A-Za-z0-9-]+)_"
    r"(?P<forcing_member>f\d{3})_(?P<experiment>[A-Za-z0-9-]+)_(?P<set_counter>[CEP]\d{3})_"
    r"(?P<y0>\d{4})-(?P<y1>\d{4})\.nc$"
)


@dataclass
class ParseResult:
    key: str
    dataset: str | None
    size: int | None = None
    forcing: ForcingFile | None = None
    submission: SubmissionFile | None = None
    findings: list[FileFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == Severity.ERROR for f in self.findings)

    @property
    def record(self) -> ForcingFile | SubmissionFile | None:
        return self.forcing or self.submission

    def add(self, severity: Severity, code: str, message: str) -> None:
        self.findings.append(
            FileFinding(key=self.key, severity=severity, code=code, message=message)
        )


def split_key(key: str) -> tuple[str | None, str | None, str]:
    """``ismip/<dataset>/<rest>`` -> (account, dataset, rest). Leading ``s3://bucket/`` is tolerated."""
    k = re.sub(r"^s3://[^/]+/", "", key)
    parts = k.split("/", 2)
    if len(parts) < 3:
        return (parts[0] if parts else None, parts[1] if len(parts) > 1 else None, "")
    return parts[0], parts[1], parts[2]


# --------------------------------------------------------------------------- forcing


def parse_forcing_key(key: str, size: int | None = None) -> ParseResult:
    _, dataset, rest = split_key(key)
    res = ParseResult(key=key, dataset=dataset, size=size)
    if dataset not in FORCING_DATASETS:
        res.add(Severity.ERROR, "not_forcing", f"{dataset!r} is not a forcing dataset")
        return res
    region = FORCING_DATASETS[dataset]

    if not rest.startswith("data/"):
        res.add(Severity.WARNING, "outside_data", "key is not under data/")
        return res
    subpath = rest[len("data/") :]
    parts = subpath.split("/")
    filename = parts[-1]
    dirs = parts[:-1]

    def make(**kw) -> ForcingFile | None:
        try:
            return ForcingFile(
                dataset=dataset, region=region, subpath=subpath, key=key, size=size, **kw
            )
        except ValidationError as e:
            res.add(Severity.ERROR, "invalid_record", str(e).splitlines()[0])
            return None

    # Static side trees ------------------------------------------------------
    if not dirs:
        res.add(Severity.WARNING, "loose_file", "file directly under data/")
        return res
    if dirs[0] == "grid":
        res.forcing = make(
            kind=ForcingKind.GRID,
            source="grid",
            experiment="static",
            product="/".join(dirs[1:]) or None,
            variable=None,
            version=_version_in(filename),
            year_start=None,
            year_end=None,
        )
        return res
    if dirs[0] == "parameterisations":
        res.forcing = make(
            kind=ForcingKind.PARAMETERISATION,
            source="parameterisations",
            experiment="static",
            product="/".join(dirs[1:]) or None,
            variable=filename.split("_")[0] if filename.endswith(".nc") else None,
            version=None,
            year_start=None,
            year_end=None,
        )
        if not filename.endswith(".nc"):
            res.add(Severity.INFO, "non_data_file", f"{filename} is not a NetCDF file")
        return res

    if len(dirs) < 3:
        res.add(
            Severity.ERROR,
            "short_path",
            f"expected <source>/<experiment>/<product>/..., got {subpath}",
        )
        return res

    forcing_dir, experiment_dir, product_dir = dirs[0], dirs[1], dirs[2]
    rest_dirs = dirs[3:]

    # Normalise the OCX inversion: data/OCX/<source>/<product>/ vs <ESM>/<exp>/<product>/
    if forcing_dir == "OCX":
        source, experiment = experiment_dir, "ocx"
        if source not in KNOWN_OCX_SOURCES:
            res.add(Severity.INFO, "unknown_ocx_source", f"OCX source {source!r} not seen before")
    else:
        source, experiment = forcing_dir, experiment_dir
        if source not in KNOWN_ESMS:
            res.add(Severity.INFO, "unknown_esm", f"ESM {source!r} not in the known CMIP6 list")
        if experiment not in experiment_registry():
            res.add(
                Severity.WARNING,
                "unknown_experiment",
                f"experiment dir {experiment!r} is not in experiments_ismip7.csv",
            )

    # Hydrofracture side products: bespoke names, no convention to check.
    if product_dir == "fracture":
        res.forcing = make(
            kind=ForcingKind.FRACTURE,
            source=source,
            experiment=experiment,
            product="fracture",
            variable=_fracture_variable(filename),
            version=None,
            year_start=None,
            year_end=None,
        )
        return res

    kind = ForcingKind.ANNUAL
    variable_dir: str | None = None
    if rest_dirs and rest_dirs[0] in ("extra", "extras"):
        if len(rest_dirs) < 2 or rest_dirs[1] not in ("climatology", "bias"):
            res.add(Severity.WARNING, "unknown_extra", f"unrecognised extra/ layout: {subpath}")
        else:
            kind = ForcingKind.CLIMATOLOGY if rest_dirs[1] == "climatology" else ForcingKind.BIAS
        variable_dir = rest_dirs[2] if len(rest_dirs) > 2 else None
    elif rest_dirs:
        variable_dir = rest_dirs[0]
        if len(rest_dirs) > 1:
            res.add(Severity.WARNING, "deep_path", f"unexpected extra directory levels: {subpath}")

    if not filename.endswith(".nc"):
        res.forcing = make(
            kind=ForcingKind.OTHER,
            source=source,
            experiment=experiment,
            product=product_dir,
            variable=variable_dir,
            version=None,
            year_start=None,
            year_end=None,
        )
        res.add(Severity.INFO, "non_data_file", f"{filename} is not a NetCDF file")
        return res

    m = _NC_RE.match(filename)
    if not m:
        res.add(
            Severity.ERROR,
            "bad_filename",
            f"{filename} does not match <var>_<region>_<source>_<exp>_<product>_v<N>_<YYYY[-YYYY]>.nc",
        )
        return res
    g = m.groupdict()
    y0 = int(g["y0"])
    y1 = int(g["y1"]) if g["y1"] else None
    if y1 is not None and kind == ForcingKind.ANNUAL:
        kind = ForcingKind.MULTIYEAR

    # AIS OCX ocean pathways: data/OCX/ocean/<main|cold|warm|vary>/so_AIS_OCX_ocean_main_v2_1950-2025.nc
    if forcing_dir == "OCX" and experiment_dir == "ocean":
        fname_source, fname_exp, fname_prod = g["experiment"], g["source"].lower(), g["product"]
    else:
        fname_source, fname_exp, fname_prod = g["source"], g["experiment"].lower(), g["product"]

    res.forcing = make(
        kind=kind,
        source=source,
        experiment=experiment,
        product=product_dir,
        variable=g["variable"],
        version=int(g["version"]),
        year_start=y0,
        year_end=y1 if y1 is not None else y0,
    )
    if res.forcing is None:
        return res

    # Cross-checks: the filename must agree with the directory it sits in.
    if g["region"] != region:
        res.add(
            Severity.ERROR, "region_mismatch", f"filename says {g['region']}, dataset is {region}"
        )
    if fname_source != source:
        res.add(
            Severity.WARNING,
            "source_mismatch",
            f"filename source {fname_source!r} != directory {source!r}",
        )
    if fname_exp != experiment:
        res.add(
            Severity.WARNING,
            "experiment_mismatch",
            f"filename experiment {fname_exp!r} != directory {experiment!r}",
        )
    if fname_prod != product_dir:
        res.add(
            Severity.WARNING,
            "product_mismatch",
            f"filename product {fname_prod!r} != directory {product_dir!r}",
        )
    if variable_dir is not None and g["variable"] != variable_dir:
        res.add(
            Severity.WARNING,
            "variable_mismatch",
            f"filename variable {g['variable']!r} != directory {variable_dir!r}",
        )
    if (g["extra"] == "bias") != (kind == ForcingKind.BIAS):
        res.add(
            Severity.WARNING,
            "extra_mismatch",
            "filename 'extras_bias' token disagrees with directory",
        )
    if g["variable"] not in KNOWN_FORCING_VARIABLES:
        res.add(
            Severity.INFO,
            "unknown_forcing_variable",
            f"{g['variable']!r} not in the known forcing variable list",
        )
    if kind == ForcingKind.CLIMATOLOGY and y1 is None:
        res.add(Severity.WARNING, "climatology_years", "climatology file without a YYYY-YYYY range")
    return res


def _version_in(filename: str) -> int | None:
    m = re.search(r"_v(\d+)(?:\.\d+)?\.nc$", filename)
    return int(m.group(1)) if m else None


def _fracture_variable(filename: str) -> str | None:
    # lake_properties_cesm2waccm_ssp585_ismip7_8km-v2.1.nc -> lake_properties
    m = re.match(
        r"^(lake_properties|ice_shelf_collapse_mask|excess_melt_fac_depletion|excess_melt)_",
        filename,
    )
    if m:
        return m.group(1)
    return filename.rsplit(".", 1)[0] if filename.endswith(".nc") else None


# --------------------------------------------------------------------------- submissions


def parse_submission_filename(filename: str) -> SubmissionFile | None:
    m = _SUBMISSION_RE.match(filename)
    if not m:
        return None
    g = m.groupdict()
    return SubmissionFile(
        variable=g["variable"],
        region=g["region"],  # type: ignore[arg-type]
        group=g["group"],
        model=g["model"],
        ism_member=g["ism_member"],
        esm=g["esm"],
        forcing_member=g["forcing_member"],
        experiment=g["experiment"],
        set_counter=g["set_counter"],
        year_start=int(g["y0"]),
        year_end=int(g["y1"]),
        filename=filename,
    )


def parse_submission_path(
    path: str,
    size: int | None = None,
    variables: dict[str, VariableSpec] | None = None,
    experiments: dict[str, Experiment] | None = None,
) -> ParseResult:
    """Parse ``.../<region>/<group>/<model>/<set>/<counter>/<file>`` wherever it sits.

    Registries default to the vendored ones; pass ``{}`` to skip those checks.
    """
    if variables is None:
        variables = variable_registry()
    if experiments is None:
        experiments = experiment_registry()
    _, dataset, _ = split_key(path)
    res = ParseResult(key=path, dataset=dataset, size=size)
    filename = posixpath.basename(path)
    if not filename.endswith(".nc"):
        res.add(Severity.INFO, "non_data_file", f"{filename} is not a NetCDF file")
        return res
    try:
        sub = parse_submission_filename(filename)
    except ValidationError as e:
        res.add(Severity.ERROR, "invalid_record", str(e).splitlines()[0])
        return res
    if sub is None:
        res.add(
            Severity.ERROR,
            "bad_filename",
            f"{filename} is not <var>_<region>_<group>_<model>_mNNN_<ESM>_fNNN_<exp>_[CEP]NNN_<YYYY-YYYY>.nc",
        )
        return res
    res.submission = sub
    res.findings.extend(sub.check(variables, experiments, key=path))
    parent = posixpath.dirname(path)
    if not parent.endswith(sub.expected_directory):
        res.add(
            Severity.WARNING,
            "directory_mismatch",
            f"expected to sit under {sub.expected_directory}/, found {parent}/",
        )
    return res


# --------------------------------------------------------------------------- dispatch


def parse_key(key: str, size: int | None = None) -> ParseResult:
    """Parse any key under the ``ismip/`` account; never raises."""
    _, dataset, rest = split_key(key)
    if dataset in FORCING_DATASETS:
        return parse_forcing_key(key, size)
    if rest.startswith("submitted/") or _SUBMISSION_RE.match(posixpath.basename(key)):
        return parse_submission_path(key, size)
    res = ParseResult(key=key, dataset=dataset, size=size)
    res.add(
        Severity.INFO, "unmodelled_dataset", f"no convention model for {dataset!r}; recorded as-is"
    )
    return res


__all__ = [
    "FORCING_DATASETS",
    "KNOWN_ESMS",
    "KNOWN_FORCING_VARIABLES",
    "KNOWN_OCX_SOURCES",
    "ParseResult",
    "parse_forcing_key",
    "parse_key",
    "parse_submission_filename",
    "parse_submission_path",
    "split_key",
]
