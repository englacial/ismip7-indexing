"""Gridlook catalog: one entry per Icechunk forcing group, generated from the index.

Usage::

    ismip7-catalog --index index.parquet --region AIS --store-url URL --out catalog.json
    ismip7-catalog --from-listing tests/data/ismip_keys_2026-10-31.txt.gz --region GrIS \\
        --store-url URL --out catalog.json

A gridlook catalog is a static JSON file (``"type": "gridlook_catalog"``) that
gridlook's "Open dataset" dialog lists with filter dropdowns built from each
entry's ``tags`` (see ``docs/plan.md`` section 4). Each entry points at one
group of the region's Icechunk repo, ``icechunk+<store_url>/<group_path>``, and
carries the default variable and colormap in ``params``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .index import build_index, read_listing

#: Forcing kinds that land in the Icechunk store as browsable groups.
CATALOG_KINDS: frozenset[str] = frozenset({"annual", "multiyear", "climatology"})

#: Variables to show first when a dataset opens, in order of preference.
PREFERRED_VARIABLES: tuple[str, ...] = ("acabf", "tf", "tas", "so", "thetao", "pr")

DEFAULT_COLORMAP = "viridis"

#: The AIS ocean products (``ocean``, and the OCX ``main/cold/warm/vary``) sit on the 8 km grid.
AIS_OCEAN_RESOLUTION_M = 8000


def normalise_store_url(store_url: str) -> str:
    """Strip whitespace, a leading ``icechunk+`` and trailing slashes from a repo URL."""
    url = store_url.strip()
    if url.startswith("icechunk+"):
        url = url[len("icechunk+") :]
    url = url.rstrip("/")
    if not url:
        raise ValueError("store_url is empty")
    return url


def dataset_url(store_url: str, group_path: str) -> str:
    """``icechunk+<repo root>/<group path>`` as gridlook expects it."""
    return f"icechunk+{normalise_store_url(store_url)}/{group_path.strip('/')}"


def format_resolution(resolution_m: int | None) -> str | None:
    """``8000`` -> ``"8 km"``, ``500`` -> ``"0.5 km"``; ``None`` stays ``None``."""
    if resolution_m is None:
        return None
    km = resolution_m / 1000
    return f"{km:g} km"


def preferred_variable(variables: list[str]) -> str | None:
    """First of :data:`PREFERRED_VARIABLES` present, else the alphabetically first variable."""
    present = set(variables)
    for v in PREFERRED_VARIABLES:
        if v in present:
            return v
    return min(present) if present else None


def _scalar(value: Any) -> Any:
    """``pd.NA``/NaN -> ``None``; numpy scalars -> Python scalars."""
    import pandas as pd

    if value is None or (not isinstance(value, str | list) and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def _first(g, col: str) -> Any:
    vals = g[col].dropna().unique() if col in g else []
    return _scalar(sorted(vals)[0]) if len(vals) else None


def select_forcing_groups(index_df, region: str):
    """Rows of ``index_df`` that become catalog entries for ``region`` (case-insensitive)."""
    df = index_df
    mask = (
        (df["record_type"] == "forcing")
        & df["ok"].astype(bool)
        & (df["region"].astype("string").str.lower() == region.lower())
        & df["kind"].isin(sorted(CATALOG_KINDS))
        & df["group_path"].notna()
    )
    return df[mask.fillna(False)]


def _entry(group_path: str, g, region: str, store_url: str) -> dict[str, Any]:
    source = _first(g, "source") or ""
    experiment = _first(g, "experiment") or ""
    product = _first(g, "product") or ""
    product_model = _first(g, "product_model") or product
    kind = _first(g, "kind") or ""
    resolution_m = _first(g, "resolution_m")
    if resolution_m is None and region.upper() == "AIS" and "ocean" in (source, product_model):
        resolution_m = AIS_OCEAN_RESOLUTION_M
    resolution = format_resolution(None if resolution_m is None else int(resolution_m))

    variables = sorted(str(v) for v in g["variable"].dropna().unique())
    y0, y1 = _scalar(g["year_start"].min()), _scalar(g["year_end"].max())

    title_parts = [source, experiment, product_model]
    if resolution:
        title_parts.append(resolution)
    title = " ".join(p for p in title_parts if p)
    if kind == "climatology":
        title += " (climatology)"

    tags = {
        "region": region,
        "source": source,
        "experiment": experiment,
        "product": product_model,
    }
    if resolution:
        tags["resolution"] = resolution
    tags["kind"] = kind

    n_files = len(g)
    desc = [f"Variables: {', '.join(variables) if variables else 'none'}"]
    if y0 is not None:
        desc.append(f"years {y0}-{y1 if y1 is not None else y0}")
    desc.append(f"{n_files} file{'s' if n_files != 1 else ''}")

    entry: dict[str, Any] = {
        "title": title,
        "url": dataset_url(store_url, group_path),
        "tags": {k: str(v) for k, v in tags.items()},
        "description": "; ".join(desc) + ".",
    }
    params: dict[str, str] = {}
    var = preferred_variable(variables)
    if var is not None:
        params["varname"] = var
    params["colormap"] = DEFAULT_COLORMAP
    entry["params"] = params
    return entry


def build_catalog(
    index_df, *, region: str, store_url: str, title: str | None = None
) -> dict[str, Any]:
    """Build a ``gridlook_catalog`` dict for one region's Icechunk repo at ``store_url``.

    One dataset per ``group_path`` among the region's OK forcing rows of kind
    annual, multiyear or climatology, sorted by (source, experiment, product)
    with kind and group path as tie-breakers so the output is deterministic.
    """
    rows = select_forcing_groups(index_df, region)
    present = sorted(rows["region"].dropna().unique())
    region_name = str(present[0]) if present else region
    sort_key: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    for group_path, g in rows.groupby("group_path", sort=True):
        e = _entry(str(group_path), g, region_name, store_url)
        t = e["tags"]
        sort_key.append(
            (
                (
                    t["source"],
                    t["experiment"],
                    _first(g, "product") or "",
                    t["kind"],
                    str(group_path),
                ),
                e,
            )
        )
    sort_key.sort(key=lambda kv: kv[0])
    datasets = [e for _, e in sort_key]
    datasets.extend(_family_entries(datasets, store_url))
    # a family entry sorts just before its levels (its path is their prefix)
    root = normalise_store_url(store_url)
    datasets.sort(
        key=lambda d: (
            d["tags"]["source"],
            d["tags"]["experiment"],
            d["url"].removeprefix(f"icechunk+{root}/"),
        )
    )
    return {
        "type": "gridlook_catalog",
        "title": title or f"ISMIP7 {region_name} forcing",
        "datasets": datasets,
    }


def _family_entries(datasets: list[dict[str, Any]], store_url: str) -> list[dict[str, Any]]:
    """One entry per family group with several resolutions: the pyramid a viewer opens as levels.

    A family is the parent of level groups named ``<metres>m``
    (``CESM2-WACCM/ssp585/SDBN1`` over ``.../SDBN1/2000m`` and ``.../SDBN1/8000m``);
    it carries the ``multiscales`` attribute written by
    :func:`~.store.annotate_multiscales`. Its entry keeps the levels' tags with
    ``resolution`` listing every level, and the first level's params.
    """
    root = normalise_store_url(store_url)
    families: dict[str, list[dict[str, Any]]] = {}
    for d in datasets:
        path = d["url"].removeprefix(f"icechunk+{root}/")
        parent, _, leaf = path.rpartition("/")
        if parent and re.fullmatch(r"\d+m", leaf):
            families.setdefault(parent, []).append(d)
    out = []
    for family, levels in families.items():
        if len(levels) < 2:
            continue
        levels.sort(key=lambda d: int(d["url"].rsplit("/", 1)[1][:-1]))
        first = levels[0]
        resolutions = [d["tags"].get("resolution", "") for d in levels]
        title = (
            re.sub(r" \S+ (k?m)$", "", first["title"])
            if first["tags"].get("resolution")
            else first["title"]
        )
        out.append(
            {
                "title": f"{title} ({' + '.join(resolutions)})",
                "url": f"icechunk+{root}/{family}",
                "tags": {
                    **first["tags"],
                    "resolution": " + ".join(resolutions),
                    "levels": str(len(levels)),
                },
                "description": f"All {len(levels)} resolutions of this product as one multi-resolution "
                f"dataset ({', '.join(resolutions)}); the viewer picks the level from the zoom. "
                + first["description"],
                "params": dict(first["params"]),
            }
        )
    return out


def dumps(catalog: dict[str, Any]) -> str:
    """Stable JSON text for a catalog (two-space indent, trailing newline)."""
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--index", help="Parquet index from ismip7-index")
    g.add_argument("--from-listing", help="saved listing (key\\tsize per line)")
    ap.add_argument("--region", required=True, help="AIS or GrIS")
    ap.add_argument("--store-url", required=True, help="Icechunk repo root URL for the region")
    ap.add_argument("--out", required=True, help="catalog JSON file to write")
    ap.add_argument("--title", help="catalog title (default: 'ISMIP7 <region> forcing')")
    args = ap.parse_args(argv)
    if args.index:
        import pandas as pd

        df = pd.read_parquet(args.index)
    else:
        df = build_index(read_listing(args.from_listing))
    cat = build_catalog(df, region=args.region, store_url=args.store_url, title=args.title)
    Path(args.out).write_text(dumps(cat), encoding="utf-8")
    print(f"{len(cat['datasets'])} datasets -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
