"""Typed models of the ISMIP7 conventions.

Two families of files are modelled:

* :class:`ForcingFile` -- the input forcing datasets the ISMIP7 focus groups
  publish under ``ismip/ismip7-ais-forcing`` and ``ismip/ismip7-gris-forcing``,
  following "ISMIP7 filenames and conventions" (section "Conventions for
  climate forcing files"; PDF linked from the protocol page). The pattern is
  checked against the full bucket listing of 2026-10-31 (285k keys) and
  against the protocol spreadsheet's dataset inventory in the tests.
* :class:`SubmissionFile` -- model output as submitted to ISMIP7. This one is
  specified: the ISMIP7 protocol page (https://www.ismip.org/research/ismip7,
  section "ISMIP7 filenaming and folder conventions") and the compliance
  checker (https://github.com/ismip/ISM_SimulationChecker, docs/user/checks.md).

The data request (variables, experiments) and the standard grids are vendored
from the checker in ``data/``; see ``data/PROVENANCE.txt``.
"""

from .models import (
    Experiment,
    FileFinding,
    ForcingFile,
    ForcingKind,
    GridSpec,
    Region,
    Severity,
    SubmissionFile,
    VariableSpec,
    VariableType,
)
from .parse import ParseResult, parse_key, parse_submission_path
from .registry import (
    WEBSITE_ONLY_VARIABLES,
    experiment_registry,
    grid_for_shape,
    grid_registry,
    protocol_inventory,
    variable_registry,
)

__all__ = [
    "WEBSITE_ONLY_VARIABLES",
    "Experiment",
    "FileFinding",
    "ForcingFile",
    "ForcingKind",
    "GridSpec",
    "ParseResult",
    "Region",
    "Severity",
    "SubmissionFile",
    "VariableSpec",
    "VariableType",
    "experiment_registry",
    "grid_for_shape",
    "grid_registry",
    "parse_key",
    "parse_submission_path",
    "protocol_inventory",
    "variable_registry",
]
