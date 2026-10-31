"""List the ``ismip/`` account on source.coop and parse every key into a table.

Usage::

    ismip7-index --out index.parquet                    # live listing (anonymous S3)
    ismip7-index --from-listing keys.txt.gz --out x.parquet   # offline, from a saved listing

The table has one row per object with the parsed fields flattened, plus a
``findings`` column (list of ``code:severity``) so the report and the
virtualization pipeline can filter on it. Listing 285k keys takes about a
minute; parsing them takes a few seconds.
"""

from __future__ import annotations

import argparse
import gzip
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import SOURCE_ACCOUNT, SOURCE_BUCKET, SOURCE_REGION
from .conventions.parse import ParseResult, parse_key


def list_bucket(prefix: str = f"{SOURCE_ACCOUNT}/") -> Iterator[tuple[str, int]]:
    """Yield (key, size) for every object under ``prefix`` (anonymous)."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    s3 = boto3.client("s3", region_name=SOURCE_REGION, config=Config(signature_version=UNSIGNED))
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=SOURCE_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"], obj["Size"]


def read_listing(path: str | Path) -> Iterator[tuple[str, int | None]]:
    """Read a saved listing: one ``key[\\tsize]`` per line, optionally gzipped."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[operator]
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            key, _, size = line.partition("\t")
            yield key, (int(size) if size else None)


def parse_all(items: Iterable[tuple[str, int | None]]) -> Iterator[ParseResult]:
    for key, size in items:
        yield parse_key(key, size)


def to_records(results: Iterable[ParseResult]) -> Iterator[dict]:
    for r in results:
        row: dict = {
            "key": r.key,
            "size": r.size,
            "dataset": r.dataset,
            "ok": r.ok,
            "findings": [f"{f.code}:{f.severity.value}" for f in r.findings],
            "record_type": None,
        }
        if r.forcing is not None:
            f = r.forcing
            row.update(
                record_type="forcing",
                region=f.region,
                kind=f.kind.value,
                source=f.source,
                experiment=f.experiment,
                product=f.product,
                product_model=f.product_model,
                resolution_m=f.resolution_m,
                variable=f.variable,
                version=f.version,
                year_start=f.year_start,
                year_end=f.year_end,
                group_path=f.group_path,
            )
        elif r.submission is not None:
            s = r.submission
            row.update(
                record_type="submission",
                region=s.region,
                group=s.group,
                model=s.model,
                ism_member=s.ism_member,
                esm=s.esm,
                forcing_member=s.forcing_member,
                experiment=s.experiment,
                set_counter=s.set_counter,
                variable=s.variable,
                year_start=s.year_start,
                year_end=s.year_end,
                group_path=s.run_id,
            )
        yield row


def build_index(items: Iterable[tuple[str, int | None]]):
    """Return a pandas DataFrame of parsed keys."""
    import pandas as pd

    df = pd.DataFrame.from_records(list(to_records(parse_all(items))))
    for col in ("size", "resolution_m", "version", "year_start", "year_end"):
        if col in df:
            df[col] = df[col].astype("Int64")
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="Parquet file to write")
    ap.add_argument("--prefix", default=f"{SOURCE_ACCOUNT}/", help="bucket prefix to list")
    ap.add_argument("--from-listing", help="read keys from a saved listing instead of S3")
    ap.add_argument(
        "--save-listing", help="also save the raw listing (key\\tsize per line, .gz ok)"
    )
    args = ap.parse_args(argv)

    items = list(read_listing(args.from_listing) if args.from_listing else list_bucket(args.prefix))
    if args.save_listing:
        p = Path(args.save_listing)
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "wt", encoding="utf-8") as f:  # type: ignore[operator]
            for k, s in items:
                f.write(f"{k}\t{'' if s is None else s}\n")
    df = build_index(items)
    df.to_parquet(args.out, index=False)
    n_err = int((~df["ok"]).sum())
    print(f"{len(df)} keys -> {args.out}; {n_err} with errors", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
