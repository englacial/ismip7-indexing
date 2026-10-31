"""Conventions report: what is in the ``ismip/`` account and what breaks the pattern.

Usage::

    ismip7-conventions-report --index index.parquet > docs/conventions_report.md
    ismip7-conventions-report --from-listing tests/data/ismip_keys_2026-10-31.txt.gz
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .conventions.parse import parse_key
from .index import build_index, read_listing


def _gb(n) -> str:
    return f"{(n or 0) / 1e9:,.1f} GB"


def render(df) -> str:
    import pandas as pd

    out: list[str] = []
    w = out.append
    w("# ISMIP7 source.coop conventions report\n")
    w(f"{len(df):,} objects, {_gb(df['size'].sum())} in total.\n")

    w("## Datasets\n")
    w("| dataset | objects | size | parsed OK | errors | warnings |")
    w("|---|---:|---:|---:|---:|---:|")
    for ds, g in df.groupby("dataset", dropna=False):
        errs = sum(any(x.endswith(":error") for x in fl) for fl in g["findings"])
        warns = sum(any(x.endswith(":warning") for x in fl) for fl in g["findings"])
        w(
            f"| `{ds}` | {len(g):,} | {_gb(g['size'].sum())} | {int(g['ok'].sum()):,} | {errs:,} | {warns:,} |"
        )
    w("")

    forcing = df[df["record_type"] == "forcing"]
    if len(forcing):
        w("## Forcing datasets: sources, experiments and products\n")
        w("| region | source | experiment | product | kind | files | size | variables | years |")
        w("|---|---|---|---|---|---:|---:|---|---|")
        keys = ["region", "source", "experiment", "product", "kind"]
        for key, g in forcing.groupby(keys, dropna=False):
            vars_ = sorted(v for v in g["variable"].dropna().unique())
            vs = ", ".join(vars_) if len(vars_) <= 14 else f"{len(vars_)} variables"
            y0, y1 = g["year_start"].min(), g["year_end"].max()
            yrs = f"{y0}-{y1}" if pd.notna(y0) else ""
            cells = [str(k) for k in key]
            w(f"| {' | '.join(cells)} | {len(g):,} | {_gb(g['size'].sum())} | {vs} | {yrs} |")
        w("")
        w("### File versions per product\n")
        w("| region | product | versions | files |")
        w("|---|---|---|---:|")
        for (reg, prod), g in forcing.groupby(["region", "product"], dropna=False):
            vers = ", ".join(f"v{int(v)}" for v in sorted(g["version"].dropna().unique()))
            w(f"| {reg} | {prod} | {vers} | {len(g):,} |")
        w("")

    subs = df[df["record_type"] == "submission"]
    if len(subs):
        w("## Submissions\n")
        w("| run | files | size | variables |")
        w("|---|---:|---:|---:|")
        for run, g in subs.groupby("group_path"):
            w(f"| `{run}` | {len(g)} | {_gb(g['size'].sum())} | {g['variable'].nunique()} |")
        w("")

    w("## Findings by code\n")
    counts: Counter[str] = Counter()
    for fl in df["findings"]:
        counts.update(fl)
    w("| code | severity | count |")
    w("|---|---|---:|")
    for code_sev, n in counts.most_common():
        code, sev = code_sev.rsplit(":", 1)
        w(f"| `{code}` | {sev} | {n:,} |")
    w("")

    w("## Keys with errors\n")
    bad = df[~df["ok"]]
    if bad.empty:
        w("None.\n")
    else:
        for _, row in bad.head(200).iterrows():
            w(f"- `{row['key']}`: {', '.join(row['findings'])}")
        if len(bad) > 200:
            w(f"- ... and {len(bad) - 200:,} more")
        w("")

    w("## Example messages per finding code\n")
    seen: set[str] = set()
    for _, row in df[df["findings"].map(len) > 0].iterrows():
        res = parse_key(row["key"], row["size"])
        for f in res.findings:
            if f.code in seen:
                continue
            seen.add(f.code)
            w(f"- `{f.code}` ({f.severity.value}): {f.message}  \n  `{f.key}`")
    w("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--index", help="Parquet index from ismip7-index")
    g.add_argument("--from-listing", help="saved listing (key\\tsize per line)")
    args = ap.parse_args(argv)
    if args.index:
        import pandas as pd

        df = pd.read_parquet(args.index)
    else:
        df = build_index(read_listing(args.from_listing))
    sys.stdout.write(render(df))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
