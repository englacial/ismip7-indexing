"""Prepare a self-contained smoke test for the Lambda image under the Runtime Interface Emulator.

    uv run python scripts/dev/rie_smoke.py <workdir>

Writes synthetic forcing files (four years, one variable, 32 km AIS grid) under
``<workdir>/src``, an empty local Icechunk repository with the group's parents
under ``<workdir>/target/repo``, and ``<workdir>/event.json`` addressing both at
the paths ``scripts/dev/rie_run.sh`` mounts them in the container (``/mnt/src``
and ``/mnt/target``). The handler then opens the files with its process pool,
commits the virtual group and verifies it against the files, i.e. every
dependency of the image is exercised without touching S3.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ismip7_helper.conventions import parse_key
from ismip7_helper.fanout import make_event
from ismip7_helper.store import StoreTarget, ensure_hierarchy, open_repository
from tests.conftest import AIS_KEY_FMT, write_forcing_file

SRC_MOUNT = "/mnt/src"
TARGET_MOUNT = "/mnt/target"


def main(argv: list[str]) -> int:
    work = Path(argv[1]).resolve()
    src, target = work / "src", work / "target"
    keys = [
        AIS_KEY_FMT.format(
            var="acabf", year=y, esm="CESM2-WACCM", exp="historical", product="SDBN1-32000m", ver=2
        )
        for y in (1850, 1851, 1852, 1853)
    ]
    files = []
    for i, k in enumerate(keys):
        write_forcing_file(src / k, year=1850 + i, variable="acabf", seed=i)
        files.append(parse_key(k, (src / k).stat().st_size).forcing)
    group_path = files[0].group_path
    assert group_path is not None
    ev = make_event(group_path, files, target=f"local:{TARGET_MOUNT}/repo", verify=1)
    ev["source_root"] = SRC_MOUNT
    # the repository is created here, on the host, at the path the container will see
    (target / "repo").mkdir(parents=True, exist_ok=True)
    st = StoreTarget.parse(
        f"local:{target / 'repo'}", local_containers={f"file://{SRC_MOUNT}/": SRC_MOUNT}
    )
    repo = open_repository(st, create=True)
    ensure_hierarchy(repo, [group_path], region="AIS")
    (work / "event.json").write_text(json.dumps(ev, indent=1))
    print(group_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
