"""Parse the full saved bucket listing (2026-10-31) and assert the conventions hold.

This is the evidence behind the forcing-file pattern in ``parse.py``. When the
ISMIP team adds products, refresh the listing with
``ismip7-index --save-listing tests/data/ismip_keys_<date>.txt.gz --out /tmp/x.parquet``
and update the expectations here.
"""

from collections import Counter
from pathlib import Path

import pytest

from ismip7_helper.conventions import ForcingKind, Severity, protocol_inventory
from ismip7_helper.index import parse_all, read_listing

LISTING = Path(__file__).parent / "data" / "ismip_keys_2026-10-31.txt.gz"


@pytest.fixture(scope="module")
def results():
    return list(parse_all(read_listing(LISTING)))


def test_everything_is_classified(results):
    assert len(results) == 285_752
    by_dataset = Counter(r.dataset for r in results)
    assert by_dataset == {
        "ismip7-ais-forcing": 108_752,
        "ismip7-gris-forcing": 176_351,
        "ismip7-uaf-pism": 429,
        "ismip7-ais-melt-calibration": 167,
        "ismip7-ais-observations": 53,
    }


def test_forcing_files_all_parse(results):
    forcing = [r for r in results if r.dataset in ("ismip7-ais-forcing", "ismip7-gris-forcing")]
    errors = [r for r in forcing if not r.ok]
    # Exactly one stray file: an intermediate ocean-climatology product uploaded
    # under its working name instead of the <var>_<region>_... convention.
    stray = (
        "ismip/ismip7-ais-forcing/data/CESM2-WACCM/historical/ocean/extras/climatology/sa/"
        "sa_CESM2-WACCM_climatology_r1i1p1f1_ismip8km_60m_1850_199501-201412.nc"
    )
    assert [r.key for r in errors] == [stray]
    parsed = [r for r in forcing if r.forcing is not None]
    assert len(parsed) == len(forcing) - 1


def test_forcing_name_directory_agreement(results):
    """Every forcing filename agrees with the directory it sits in."""
    mismatch = [
        r for r in results if r.forcing and any(f.code.endswith("_mismatch") for f in r.findings)
    ]
    assert mismatch == [], [(r.key, [f.message for f in r.findings]) for r in mismatch[:10]]


def test_forcing_kinds_and_counts(results):
    kinds = Counter(r.forcing.kind for r in results if r.forcing)
    assert kinds[ForcingKind.ANNUAL] > 280_000
    assert kinds[ForcingKind.MULTIYEAR] == 1_320 + 12  # AIS ocean decades + OCX ocean pathways
    assert kinds[ForcingKind.CLIMATOLOGY] > 0 and kinds[ForcingKind.BIAS] == 8
    assert kinds[ForcingKind.FRACTURE] == 50 and kinds[ForcingKind.GRID] == 1
    assert kinds[ForcingKind.PARAMETERISATION] == 5


def test_versions_are_uniform_within_a_directory(results):
    """No directory mixes v1/v2/v3 files, so no dedup is needed when concatenating."""
    per_dir: dict[str, set[int]] = {}
    for r in results:
        f = r.forcing
        if f and f.version is not None and f.kind in (ForcingKind.ANNUAL, ForcingKind.MULTIYEAR):
            per_dir.setdefault(r.key.rsplit("/", 1)[0], set()).add(f.version)
    mixed = {d: v for d, v in per_dir.items() if len(v) > 1}
    assert mixed == {}


def test_only_expected_soft_findings(results):
    """INFO/WARNING codes present in the real listing; add here deliberately."""
    codes = Counter(f.code for r in results for f in r.findings if f.severity != Severity.ERROR)
    assert set(codes) == {"unmodelled_dataset", "non_data_file"}


def test_uaf_submission_parses(results):
    subs = [r for r in results if r.submission]
    assert len(subs) == 418
    runs = Counter(r.submission.run_id for r in subs)
    assert len(runs) == 11 and set(runs.values()) == {38}
    errs = Counter(f.code for r in subs for f in r.findings if f.severity == Severity.ERROR)
    # scalar_*.nc files are not in the data request, and C011 is named 'OCX'
    assert errs == {"unknown_variable": 11, "unknown_experiment": 38}


def test_bucket_matches_protocol_spreadsheet(results):
    """Every dataset/version the ISMIP7 protocol spreadsheet lists is in the bucket, same version."""
    bucket: dict[tuple[str, str, str, str, str], set[str]] = {}
    for r in results:
        f = r.forcing
        if (
            f is None
            or f.version is None
            or f.kind not in (ForcingKind.ANNUAL, ForcingKind.MULTIYEAR)
        ):
            continue
        if f.experiment == "ocx":
            key = (f.region, "OCX", f.source, f.product, f.variable)
        else:
            key = (f.region, f.source, f.experiment, f.product, f.variable)
        bucket.setdefault(key, set()).add(str(f.version))
    missing = {k: v for k, v in protocol_inventory().items() if k not in bucket}
    assert missing == {}
    wrong = {k: (v, bucket[k]) for k, v in protocol_inventory().items() if v not in bucket[k]}
    assert wrong == {}
