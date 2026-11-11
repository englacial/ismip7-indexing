"""Write ``multiscales`` on the family groups of a store (serial step after a fan-out).

    ismip7-multiscales --region AIS --write-creds sc_creds.json            # production repo
    ismip7-multiscales --region AIS --target local:/tmp/icechunk-ais       # a local repo
    ismip7-multiscales --region AIS --write-creds sc_creds.json --dry-run  # list the families

A family group (``CESM2-WACCM/ssp585/SDBN1``) is the parent of the level groups
``.../SDBN1/2000m`` and ``.../SDBN1/8000m`` that the fan-out writes. The fan-out
workers never touch it (shared parents do not rebase), so this writes every
family's attribute in one commit afterwards. See :func:`~.store.annotate_multiscales`.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from .store import (
    StoreTarget,
    annotate_multiscales,
    check_target_region,
    load_sc_creds,
    open_repository,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--region", required=True, choices=["AIS", "GrIS"])
    ap.add_argument("--target", help="local:/path or s3://bucket/prefix (default: production repo)")
    ap.add_argument("--target-region", default="us-west-2", help="the target bucket's AWS region")
    ap.add_argument("--write-creds", help="source.coop credentials JSON (S3 targets)")
    ap.add_argument("--dry-run", action="store_true", help="list the families, write nothing")
    args = ap.parse_args(argv)
    target = args.target or StoreTarget.region_default(args.region).url
    try:
        check_target_region(target, args.region)
    except ValueError as e:
        ap.error(str(e))
    st = StoreTarget.parse(target, region=args.target_region)
    if not st.is_local:
        if not args.write_creds and not args.dry_run:
            ap.error(f"{target}: --write-creds is required for an S3 target")
        if args.write_creds:
            st = replace(st, credentials=load_sc_creds(args.write_creds))
    repo = open_repository(st, readonly=args.dry_run)
    snapshot, families = annotate_multiscales(repo, dry_run=args.dry_run)
    for family, levels in families.items():
        print(f"{family}: {', '.join(leaf for _, leaf in levels)}", file=sys.stderr)
    print(
        f"{len(families)} families; "
        + (
            f"committed {snapshot}"
            if snapshot
            else "nothing to write"
            if not args.dry_run
            else "dry run"
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
