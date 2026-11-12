"""Per-variable colour ranges: CF ``actual_range`` sampled along the time axis.

The source files carry no range metadata (``_FillValue``, ``missing_value``,
``units``, ``standard_name``, ``long_name``, ``grid_mapping`` only), so a
viewer that colours one time slice at a time recomputes its colour range per
slice and the colour bar drifts as the user scrubs. gridlook seeds a fixed
colour range from a variable's CF ``actual_range`` attribute (``[low,
high]`` in the variable's units). The true range of a 300-year monthly 2 km
series would cost reading the whole archive; ``n`` steps spread evenly over
the axis (first and last included; :data:`DEFAULT_RANGE_STEPS`) are read
instead, the fill value, NaN and ``missing_value`` are masked, and the min
and max of what remains become ``actual_range``. The companion attribute
``ismip7_actual_range`` says how the range was sampled, so a reader knows it
is an estimate (a viewer clips values outside it, which is fine for
colouring). Nothing is padded.

Two read paths: at write time (:func:`estimate_ranges`) the sampled steps
are read from the source files with the ranged h5py reads verification uses,
and in backfill mode (:func:`estimate_stored_ranges`, ``ismip7-fanout
--ranges``) through the store's virtual references. An ocean variable is
4-D (``time, z, y, x``): a sampled step is the whole column. Each resolution
level of a multiscales family samples its own range (the attribute is per
array).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import numpy as np
import xarray as xr

from .store import (
    _conflict_pause,
    _file_offsets,
    decoded_steps,
    open_group,
    open_source,
    provenance,
)
from .virtualize import GroupResult, NativeTail, VirtualizeError

logger = logging.getLogger(__name__)

#: CF attribute a viewer seeds its colour range from: ``[low, high]``.
ACTUAL_RANGE_ATTR = "actual_range"
#: Companion attribute: how the range was sampled (set even when no range could be).
RANGE_NOTE_ATTR = "ismip7_actual_range"
#: Time steps sampled per variable (fewer when the axis is shorter).
DEFAULT_RANGE_STEPS = 16
#: Source files read concurrently when sampling at write time (S3-latency bound).
RANGE_READ_WORKERS = 4


def range_axis(dims: Sequence[str] | None) -> str | None:
    """The axis an array is sampled along: ``time``, ``time_annual``, ``time_<n>`` or ``month``
    as the first of at least three dimensions; ``None`` for anything else (coordinates, bounds)."""
    if not dims or len(dims) < 3:
        return None
    d = dims[0]
    return d if d == "month" or d == "time" or d.startswith("time_") else None


def sample_indices(n: int, n_steps: int = DEFAULT_RANGE_STEPS) -> list[int]:
    """``min(n_steps, n)`` indices spread evenly over ``range(n)``, the first and last included."""
    if n <= 0:
        return []
    k = min(max(int(n_steps), 1), n)
    return sorted(set(np.round(np.linspace(0, n - 1, k)).astype(int).tolist()))


def _finite_range(a: Any) -> tuple[float, float] | None:
    """Min and max of the finite values of ``a`` (NaN and inf ignored), or ``None`` if there are none."""
    a = np.asarray(a)
    if a.dtype.kind == "f":
        keep = np.isfinite(a)
        if not keep.any():
            return None
        v = a[keep]
    elif a.dtype.kind in "iu" and a.size:
        v = a
    else:
        return None
    return float(v.min()), float(v.max())


def _widen(
    acc: tuple[float, float] | None, new: tuple[float, float] | None
) -> tuple[float, float] | None:
    if acc is None:
        return new
    if new is None:
        return acc
    return min(acc[0], new[0]), max(acc[1], new[1])


@dataclass
class VarRange:
    """The sampled range of one array."""

    var: str
    axis: str
    n_axis: int
    #: the indices that were meant to be sampled
    sampled: list[int]
    #: how many of them were read (fewer when the time budget ran out)
    n_read: int = 0
    low: float | None = None
    high: float | None = None
    note: str = ""
    seconds: float = 0.0

    @property
    def attrs(self) -> dict[str, Any]:
        """The attributes to set on the array (no ``actual_range`` without finite values)."""
        out: dict[str, Any] = {}
        if self.low is not None and self.high is not None:
            out[ACTUAL_RANGE_ATTR] = [self.low, self.high]
        out[RANGE_NOTE_ATTR] = self.note
        return out


def _finish(vr: VarRange, rng: tuple[float, float] | None, *, through_store: bool) -> VarRange:
    how = f"{vr.n_read} of {vr.n_axis} {vr.axis} steps"
    short = "" if vr.n_read == len(vr.sampled) else " (time budget)"
    via = " through the store" if through_store else ""
    if rng is None:
        vr.note = f"no finite values in {how} sampled{via}{short}"
    else:
        vr.low, vr.high = rng
        vr.note = f"sampled from {how}{via}{short}"
    return vr


@dataclass
class RangeResult:
    group_path: str
    ranges: list[VarRange] = field(default_factory=list)
    #: variables that could not be sampled (errors, budget) and why
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    n_steps: int = DEFAULT_RANGE_STEPS

    @property
    def attrs(self) -> dict[str, dict[str, Any]]:
        return {r.var: r.attrs for r in self.ranges}

    @property
    def n_ranged(self) -> int:
        return sum(1 for r in self.ranges if r.low is not None)

    def summary(self) -> str:
        """One line for the group's notes and the run record."""
        if not self.ranges:
            return "colour ranges: no time-dependent variables sampled" + (
                f" ({'; '.join(self.notes)})" if self.notes else ""
            )
        n_read = sorted({r.n_read for r in self.ranges})
        reads = f"{n_read[0]}" if len(n_read) == 1 else f"{n_read[0]}-{n_read[-1]}"
        return (
            f"colour ranges: {self.n_ranged} of {len(self.ranges)} variables ranged from "
            f"{reads} sampled steps in {self.seconds:.1f} s"
            + (f"; {'; '.join(self.notes)}" if self.notes else "")
        )


def apply_ranges(ds: xr.Dataset, result: RangeResult) -> None:
    """Set the sampled attributes on ``ds``'s arrays (in place)."""
    for r in result.ranges:
        if r.var in ds.variables:
            ds[r.var].attrs.update(r.attrs)


# --------------------------------------------------------------------------- source files


def _source_reads(
    marr: Any, tails: list[NativeTail], steps: list[int]
) -> list[tuple[str, int] | None]:
    """``(source url, step within the file)`` for each sampled index, or ``None`` for a
    padded step (a missing chunk, which reads as the fill value: nothing to sample).

    A step stored natively (a :class:`NativeTail`) is read from its source
    file like any other. A one-step stand-in file referenced by twelve
    monthly indices has only its step 0; the step within the file is
    clamped when read.
    """
    manifest = marr.manifest
    grid = manifest.shape_chunk_grid
    chunk0 = int(marr.metadata.chunks[0])
    first = dict(_file_offsets(marr))
    out: list[tuple[str, int] | None] = []
    for k in steps:
        tail = next((t for t in tails if t.start <= k < t.start + t.n), None)
        if tail is not None:
            out.append((tail.url, k - tail.start))
            continue
        c = k // chunk0
        path = ""
        for rest in np.ndindex(*grid[1:]):
            entry = manifest.get_entry((c, *rest))
            if entry and entry["path"]:
                path = entry["path"]
                break
        out.append((path, k - first[path]) if path else None)
    return out


def _read_file_range(
    opener: Callable[[str], BinaryIO], url: str, var: str, steps: list[int]
) -> tuple[tuple[float, float] | None, int]:
    """Range over ``steps`` of ``var`` in one source file; returns it and the steps read."""
    import h5py

    with opener(url) as fh, h5py.File(fh, "r") as h:
        dset = h[var]
        n = int(dset.shape[0])
        acc = None
        for slab in decoded_steps(dset, sorted({min(k, n - 1) for k in steps})):
            acc = _widen(acc, _finite_range(slab))
    return acc, len(steps)


def estimate_ranges(
    result: GroupResult,
    *,
    n_steps: int = DEFAULT_RANGE_STEPS,
    opener: Callable[[str], BinaryIO] | None = None,
    time_budget_s: float | None = None,
    max_workers: int = RANGE_READ_WORKERS,
) -> RangeResult:
    """Sample the range of every time-dependent variable of a concatenated group from its source files.

    For each variable, ``n_steps`` indices spread over its axis are mapped to
    the source file and step that hold them (through the chunk manifest and
    the native tails) and read with ranged h5py reads, ``max_workers`` files
    at a time. With ``time_budget_s`` the sampling stops when the budget is
    used up: variables already sampled keep the range of the steps read so
    far (the note says so), the rest get nothing and are listed in
    ``notes``. A variable whose read fails is noted, never raised.
    ``opener`` replaces :func:`~.store.open_source` in tests.
    """
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.monotonic()
    deadline = t0 + time_budget_s if time_budget_s is not None else None
    out = RangeResult(result.group_path, n_steps=n_steps)
    open_ = opener or open_source
    for var in result.ds.data_vars:
        da = result.ds[var]
        axis = range_axis(da.dims)
        marr = da.data
        if axis is None or not hasattr(marr, "manifest"):
            continue
        if deadline is not None and time.monotonic() >= deadline:
            out.notes.append(f"{var}: not sampled, the {time_budget_s:.0f} s budget is used up")
            continue
        n = int(da.shape[0])
        vr = VarRange(var, axis, n, sample_indices(n, n_steps))
        started = time.monotonic()
        try:
            tails = [t for t in result.native_tails if t.var == var]
            by_url: dict[str, list[int]] = {}
            n_padded = 0
            for read in _source_reads(marr, tails, vr.sampled):
                if read is None:
                    n_padded += 1
                else:
                    by_url.setdefault(read[0], []).append(read[1])
            acc = None
            vr.n_read = n_padded  # a padded step is the fill value: sampled, nothing to see
            jobs = [(open_, url, var, steps) for url, steps in by_url.items()]
            width = max(1, max_workers)
            with ThreadPoolExecutor(max_workers=width) as pool:
                for i in range(0, len(jobs), width):
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    for rng, n_read in pool.map(
                        _read_file_range, *zip(*jobs[i : i + width], strict=True)
                    ):
                        acc = _widen(acc, rng)
                        vr.n_read += n_read
        except Exception as e:  # best effort: a range is a hint for colouring
            logger.exception("%s: %s: colour range sampling failed", result.group_path, var)
            out.notes.append(f"{var}: not sampled: {type(e).__name__}: {e}")
            continue
        vr.seconds = round(time.monotonic() - started, 1)
        out.ranges.append(_finish(vr, acc, through_store=False))
        logger.info("%s: %s: %s in %.1f s", result.group_path, var, vr.note, vr.seconds)
    out.seconds = round(time.monotonic() - t0, 1)
    return out


# --------------------------------------------------------------------------- the store


def estimate_stored_ranges(
    repo: Any,
    group_path: str,
    *,
    n_steps: int = DEFAULT_RANGE_STEPS,
    branch: str = "main",
    time_budget_s: float | None = None,
) -> RangeResult:
    """Sample the range of every time-dependent array of a stored group through the store.

    The same sampling as :func:`estimate_ranges`, reading the steps through
    the repository's virtual chunk container (the arrays decode through
    xarray, so the fill value and ``missing_value`` are already NaN).
    """
    t0 = time.monotonic()
    deadline = t0 + time_budget_s if time_budget_s is not None else None
    out = RangeResult(group_path, n_steps=n_steps)
    ds = open_group(repo, group_path, branch, decode_times=False)
    for var in ds.data_vars:
        da = ds[var]
        axis = range_axis(da.dims)
        if axis is None:
            continue
        if deadline is not None and time.monotonic() >= deadline:
            out.notes.append(f"{var}: not sampled, the {time_budget_s:.0f} s budget is used up")
            continue
        n = int(da.sizes[axis])
        vr = VarRange(var, axis, n, sample_indices(n, n_steps))
        started = time.monotonic()
        acc = None
        try:
            for k in vr.sampled:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                acc = _widen(acc, _finite_range(da.isel({axis: k}).values))
                vr.n_read += 1
        except Exception as e:
            logger.exception("%s: %s: colour range sampling failed", group_path, var)
            out.notes.append(f"{var}: not sampled: {type(e).__name__}: {e}")
            continue
        vr.seconds = round(time.monotonic() - started, 1)
        out.ranges.append(_finish(vr, acc, through_store=True))
        logger.info("%s: %s: %s in %.1f s", group_path, var, vr.note, vr.seconds)
    out.seconds = round(time.monotonic() - t0, 1)
    return out


@dataclass
class RangeState:
    """Which arrays of a stored group can be ranged, and which already are."""

    arrays: list[str]
    ranged: list[str]
    attrs: dict[str, Any] = field(default_factory=dict)  # the group's own attributes

    @property
    def complete(self) -> bool:
        return bool(self.arrays) and set(self.ranged) >= set(self.arrays)


def range_states(
    repo: Any, group_paths: Iterable[str], branch: str = "main"
) -> dict[str, RangeState]:
    """Per group: its time-dependent arrays and those carrying ``ismip7_actual_range``.

    Reads one ``zarr.json`` per group and per array (no chunk, no manifest).
    A path that is not a group raises :class:`~.virtualize.VirtualizeError`.
    """
    import asyncio

    from zarr.core.buffer import default_buffer_prototype
    from zarr.core.sync import sync

    store = repo.readonly_session(branch).store
    paths = list(group_paths)

    async def _meta(path: str) -> dict[str, Any] | None:
        buf = await store.get(f"{path}/zarr.json", prototype=default_buffer_prototype())
        return None if buf is None else json.loads(buf.to_bytes())

    async def _one(gp: str) -> RangeState:
        meta = await _meta(gp)
        if meta is None or meta.get("node_type") != "group":
            raise VirtualizeError(f"{gp}: no such group in the repository")
        names = [n.rstrip("/") async for n in store.list_dir(gp)]
        children = await asyncio.gather(*(_meta(f"{gp}/{n}") for n in names if n != "zarr.json"))
        arrays, ranged = [], []
        for name, child in zip([n for n in names if n != "zarr.json"], children, strict=True):
            if not child or child.get("node_type") != "array":
                continue
            if range_axis(child.get("dimension_names")) is None:
                continue
            arrays.append(name)
            if RANGE_NOTE_ATTR in (child.get("attributes") or {}):
                ranged.append(name)
        return RangeState(sorted(arrays), sorted(ranged), meta.get("attributes") or {})

    async def _all() -> dict[str, RangeState]:
        states = await asyncio.gather(*(_one(gp) for gp in paths))
        return dict(zip(paths, states, strict=True))

    return sync(_all())


def plan_ranges(
    groups: Iterable[str],
    states: dict[str, RangeState],
    *,
    overwrite: bool = False,
    patterns: list[str] | None = None,
    max_groups: int | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Which stored groups to (re)range: ``(to do, {skipped: reason})``.

    A group is skipped when every time-dependent array already carries the
    range note (unless ``overwrite``) or when it has no such array.
    """
    todo: list[str] = []
    skipped: dict[str, str] = {}
    for gp in sorted(groups):
        if patterns and not any(fnmatch.fnmatchcase(gp, p) for p in patterns):
            continue
        state = states[gp]
        if not state.arrays:
            skipped[gp] = "no time-dependent arrays"
        elif state.complete and not overwrite:
            skipped[gp] = "ranged"
        else:
            todo.append(gp)
    if max_groups:
        todo = todo[:max_groups]
    return todo, skipped


def range_group(
    repo: Any,
    group_path: str,
    *,
    n_steps: int = DEFAULT_RANGE_STEPS,
    branch: str = "main",
    time_budget_s: float | None = None,
    rebase_tries: int = 200,
    restage_tries: int = 8,
) -> tuple[str | None, RangeResult]:
    """Backfill: sample a stored group through the store and commit the attributes once.

    Returns the snapshot id (``None`` when no array could be sampled, or
    when the attributes are already exactly these) and the sampling result. The commit only touches the group's own arrays, so
    it rebases over concurrent commits to other groups
    (``ConflictDetector``) and is re-staged after losing its rebase races.
    """
    import icechunk
    import zarr

    rr = estimate_stored_ranges(
        repo, group_path, n_steps=n_steps, branch=branch, time_budget_s=time_budget_s
    )
    if not rr.ranges:
        return None, rr
    for attempt in range(restage_tries + 1):
        session = repo.writable_session(branch)
        g = zarr.open_group(session.store, path=group_path, mode="r+")
        for r in rr.ranges:
            g[r.var].update_attributes(r.attrs)
        if not session.has_uncommitted_changes:
            return None, rr
        try:
            snap = session.commit(
                f"colour ranges on {group_path} ({len(rr.ranges)} arrays)",
                metadata={
                    "operation": "estimate_ranges",
                    "group": group_path,
                    "arrays": [r.var for r in rr.ranges],
                    "n_steps": n_steps,
                    **provenance(),
                },
                rebase_with=icechunk.ConflictDetector(),
                rebase_tries=rebase_tries,
            )
            logger.info("%s: colour ranges committed as %s", group_path, snap)
            return snap, rr
        except icechunk.ConflictError:
            if attempt >= restage_tries:
                raise
            _conflict_pause(group_path, attempt)
    raise AssertionError("unreachable")


__all__ = [
    "ACTUAL_RANGE_ATTR",
    "DEFAULT_RANGE_STEPS",
    "RANGE_NOTE_ATTR",
    "RangeResult",
    "RangeState",
    "VarRange",
    "apply_ranges",
    "estimate_ranges",
    "estimate_stored_ranges",
    "plan_ranges",
    "range_axis",
    "range_group",
    "range_states",
    "sample_indices",
]
