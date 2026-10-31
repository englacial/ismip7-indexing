# Phase B adversarial review (clean-context Opus, 2026-10-31)

Reviewed commit f92df01 (`ismip7_helper/store.py`, `ismip7_helper/pipeline.py`, `tests/test_store.py`) against icechunk 2.3.1, VirtualiZarr 2.8.0, zarr 3.4.2, xarray 2026.9.0. "Confirmed" means reproduced with a script.

## Findings, most severe first

**1. store.py:237-252. Concurrent commits fail whenever two workers create the same new parent group, including the root on a fresh repo. Confirmed.**
`to_icechunk` creates missing parent groups (`/`, `/<ESM>`, `/<ESM>/<exp>`) in each session; a fresh repo has no root node. Two sessions creating the same new node give `NewNodeConflictsWithExistingNode`; `ConflictDetector` and `BasicConflictSolver` both fail, and `rebase_tries` cannot help. Six threads on a fresh repo under two ESMs: one commits, five raise `RebaseFailedError`. Different products under an existing `<ESM>/<exp>` commit fine; different ESMs commit fine once the root exists. The fan-out sends groups in sorted order, so concurrent workers almost always share a new parent; the first production wave would mostly fail. docs/lambda.md's "concurrent workers never conflict" is false. Fix: before fanning out, create the repo and every parent group of the selected groups in one commit (`ensure_hierarchy(repo, group_paths)` from `fanout.main`/`pipeline.main`; checked to make concurrent writes rebase cleanly). Backstop: in `write_group`, on `RebaseFailedError` whose conflicts are all `NewNodeConflictsWithExistingNode` on ancestor groups, re-stage in a fresh session and commit again.

**2. store.py:175-182. Repository creation is a check-then-act race, called from every Lambda. Confirmed.**
`Repository.exists` then `create` is not atomic. Eight threads on one storage: 1 created, 3 opened, 3 `ConflictError`, 1 `InvalidInputError: repositories can only be created in clean prefixes`. Fix: create once in `fanout.main` (with the hierarchy from 1); workers only open. If `create=True` stays, catch `IcechunkError` from `create` and fall back to `open`.

**3. store.py:263-264, pipeline.py:163-164. `existing_groups` crashes on a new repository. Confirmed.**
A fresh repo has no root group; `zarr.open_group(mode="r")` raises `GroupNotFoundError`, so `ismip7-virtualize` dies before writing anything on a new target. `fanout.main` hides it with a bare `except Exception`, which also hides expired credentials. Fix: return `set()` on `GroupNotFoundError`; creating the root in the creation commit also removes it.

**4. pipeline.py:67. `load_groups` crashes when any file size is missing. Confirmed.**
`size` is nullable `Int64`; a missing size is `pd.NA`, and `size != size` raises `TypeError: boolean value of NA is ambiguous`. Fix: `None if pd.isna(size) else int(size)`, plus a test.

**5. store.py:342-355. `verify_group` fails every month-indexed climatology group. Confirmed.**
Climatology groups have `(month, y, x)` and no `time`; `isel(time=...)` raises and the bare except records a mismatch, so with `--verify 1` every climatology group is reported failed but stays committed. Fix: index by axis 0 (`stored[var][{stored[var].dims[0]: slice(t0, t0 + n)}]`); test a climatology group.

**6. pipeline.py:101-106, 166-170, 179-181. A failed verification leaves a committed bad group that later runs skip, and the next run exits 0.**
`status="failed"` is set after the commit with no rollback or mark; `existing_groups` counts any group with arrays as done; `status` cannot distinguish "not committed" from "committed, failed verification"; same in `lambda_handler.run_event`. Verification also reads the branch head rather than the snapshot just written. Fix: mark verification state in the group (e.g. `ismip7_verified` attr) or commit a delete on failure; have `existing_groups` return attrs so the runner re-runs unverified groups; add `committed: bool` to `GroupRun`; verify with `repo.readonly_session(snapshot_id=...)`.

**7. store.py:286-309, 350-355. Verification downloads and decodes whole source files; ocean files will not fit a Lambda.**
`_fetch_s3` pulls the whole object into memory, writes it to `/tmp`, decodes the full variable, then the stored side decodes the same again. AIS OCX ocean files are 1.7–2.8 GB; the copy alone exceeds the 2,048 MB ephemeral storage. Fix: verify a sample of timesteps via ranged reads (h5py/netCDF4 over an obstore or fsspec file-like) and compare `src[t]` against `stored[var][t0 + k]` for one or two random `k` per file.

**8. pipeline.py:107, 177-178. `run_group` catches only `VirtualizeError`/`ValueError`; the report is written only at the end.**
`RebaseFailedError`/`ConflictError` derive from `IcechunkError`, not `ValueError`; credential expiry, S3 5xx or `OSError` abort the whole serial run and `--report` is never written. `verify_group`'s `open_group` is outside its per-file try. Fix: catch `Exception` (as the handler does) recording `type(e).__name__`; append each `GroupRun` to the report as it finishes.

**9. store.py:241-252, 259-275. Re-runs cannot tell stale groups from current ones; history cannot answer "which snapshot has group X at version N".**
Skipping is by name only, so republished upstream files are skipped. Commit metadata lacks file versions, an overwrite flag, a digest of the (key, size) list, package/git version, and no tag is made. Fix: add `ismip7_file_version`, `operation`, `sources_sha256` over sorted (key, size), package version and git SHA to the commit metadata; store the digest as a group attr and skip only on a match; tag release snapshots.

**10. store.py:240. Virtual refs are stamped with the commit time, not the header-read time.**
`to_icechunk` without `last_updated_at` stamps `now()+1s` at write; a file republished at the same key between header read and commit (up to ~13 min in a Lambda) is read back silently wrong. Fix: record a timestamp before `concat_group` (or the per-file `LastModified` minimum), carry it in `GroupResult`, pass `last_updated_at`.

**11. store.py:56, 152-163. Write credentials leak in reprs and are never refreshed.**
`StoreTarget` repr shows the secret and session token (fix: `field(repr=False)`). Static temporary credentials with no `expires_after`/`get_credentials` refresh die mid-run. With no secret configured, `storage_for` silently falls back to `from_env` (the Lambda role, which cannot write) and fails only at commit: fail fast in the handler. `load_sc_creds` does not handle an STS-style nested `{"Credentials": {...}}`.

**12. store.py:32-38, 130-139. The split unit is chunks, not timesteps; preload setting does nothing for data arrays. Low, confirmed.**
With 4-step chunks `split=12` gives one manifest, `split=3` gives four; rename to `MANIFEST_SPLIT_CHUNKS` or divide by the chunk length. Config is persisted at create and returned by a config-less open, but `preload_if=None` only matches CF coordinate names under 1,000 refs, so `max_total_refs=200_000` has no effect on data arrays; drop it or disable preload. icechunk-js ignores it.

**13. store.py:341-348. Verification uses private manifest state and only the first spatial chunk of each row. Low.** A missing or inlined first chunk becomes a bogus file. Fix: first non-empty, non-inlined path along the row.

**14. store.py:204-206. `prepare_for_write` silently stamps a default time unit. Low.** A unitless `time` should raise, not be relabelled. Otherwise a correct idempotent no-op on `concat_group` output; `write_group` does not mutate `result.ds`.

**15. store.py:259-275. `existing_groups` is slow and coarse. Low.** 1,500 groups × 6 arrays took 8–9 s on local disk via `members()` versus 1.4 s with `store.list()` filtering `*/zarr.json`. Returning attrs would serve 6 and 9.

**16. pipeline.py:148-149, 156-158. CLI selection and target checks. Low.** `--max-groups` is applied before the skip check; `--region AIS` with a GrIS target is not refused; README does not say readers must pass the exact `authorize_virtual_chunk_access` key with trailing slash.

## Test gaps
- `test_two_groups_commit_independently` commits sequentially so cannot see 1: add staged concurrent sessions (shared new parent; same group twice; overwrite beside a sibling).
- No test of `existing_groups` on a fresh repo (3), `load_groups` (4), `run_group`/`pipeline.main`, a climatology group through write and verify (5), or "verify failed then re-run" (6).
- Nothing exercises the S3 container (round trip uses `file://`); add a test writing `key_to_url` refs into a repo with only the S3 container and assert the prefix match.
- Config persistence and multi-manifest splitting untested; commit metadata untested; credentials-out-of-repr untested.
- Phase C: `test_fanout_local_cli`'s "second run skips both" uses `--dry-run`, which bypasses the skip logic.

## Checked and found sound
`to_icechunk(mode="w")` deletes and recreates the group (no stale arrays). Overwrite concurrent with a sibling write rebases cleanly. Two concurrent overwrites, or two `w-` writes, of the same group are detected (`DeleteOfUpdatedArray` / new-node conflict). `w-` on an existing group raises `ContainsGroupError` (a `ValueError`). Writes under existing parents and different ESMs under an existing root rebase cleanly. Config (container, splitting, preload) is persisted at create; a second `create` on a non-empty prefix is refused. The anonymous container prefix matches `key_to_url` exactly, trailing slash included. `s3_storage` keyword names match 2.3; `_fetch_s3` derives the key correctly. Verification reads through icechunk's virtual refs with the source codecs; NaN-equality with fill masking works; the chunk-index × chunk-length offset is correct. `prepare_for_write` is idempotent. `load_groups` uses `fnmatchcase` and sorts deterministically. Exit code is 1 when any group failed.

## Fold

Folded into `ismip7_helper/store.py`, `pipeline.py`, `lambda_handler.py`, `fanout.py`, `virtualize.py`, the tests (129 pass; new `tests/test_pipeline.py`) and the docs (`docs/lambda.md`, `docs/plan.md`, `README.md`, `Dockerfile.lambda`).

- **1 accepted.** New `store.ensure_hierarchy(repo, group_paths, branch)` creates the root and every ancestor group in one commit (idempotent; records `ismip7_region` on a new root and refuses a mismatch). `pipeline.main` and `fanout.main` call it before any worker. `write_group` keeps a backstop: a `RebaseFailedError` whose conflicts are all `NewNodeConflictsWithExistingNode` on ancestors is re-staged in a fresh session (up to 3 times). docs/lambda.md now explains why the hierarchy is needed. Tests: staged sessions (a shared new parent conflicts without the hierarchy and rebases with it; the same group twice conflicts; an overwrite beside a sibling rebases) and six threads on a fresh repo, with and without the hierarchy.
- **2 accepted.** The runners create the repository; the Lambda handler only opens it, and a missing repository raises `RepositoryNotFoundError` with a clear message. `open_repository(create=True)` falls back to `open` if a concurrent `create` wins.
- **3 accepted.** `existing_groups` lists the store and returns `{}` on a fresh repository. The fan-out's bare `except Exception` is gone.
- **4 accepted.** `load_groups` uses `pd.isna(size)`. Tested with all sizes null and with some null.
- **5 accepted.** Verification indexes along axis 0 (`time` or `month`). Added a climatology group test that goes through write and verify.
- **6 accepted with the author's design.** Verification reads `readonly_session(snapshot_id=<write>)`. On success a follow-up commit sets `ismip7_verified = <snapshot id>`; on failure the group is deleted in a commit. `GroupRun` has `committed` and `deleted`. `existing_groups` returns `{path: attrs}`. A run with `--verify` rewrites existing groups that are not verified; a run with `--verify 0` skips any current group. Shared by both runners via `pipeline.commit_group`/`plan_groups`.
- **7 accepted.** `verify_group` samples `n_steps` random steps (default 1) of each sampled file. It reads them with h5py through a small `io.RawIOBase` over `obstore.open_reader` (ranged GETs; also used for `file://` via `LocalStore`) and decodes them like xarray (fill/missing_value to NaN, scale/offset). Whole objects are never downloaded. A test checks that the bytes read are under a third of the file.
- **8 accepted.** `run_group` catches `Exception` and records `type(e).__name__`. `verify_group` catches failures to open the stored group. Both runners rewrite the JSON report after each group (`report_writer`).
- **9 accepted.** Commit metadata now carries `operation`, `ismip7_file_version`, `sources_sha256` (sha256 over the sorted `(key, size)` pairs), `headers_read_at`, `ismip7_helper_version`, and `ismip7_helper_git_sha` when known (from `ISMIP7_GIT_SHA`, which the Dockerfile sets from a build arg, or from `git rev-parse` in a checkout). The digest is also the group attribute `ismip7_sources_sha256`. Runners skip a group only when the digest matches the index (and, with `--verify`, the group is verified); otherwise they rewrite it with the reason recorded. Added a `tag_release(repo, name)` helper; nothing is tagged automatically.
- **10 accepted with a change.** `concat_group` records `datetime.now(UTC)` before opening files as `GroupResult.opened_at` (or takes `opened_at=` when `opened=` is passed), and `write_group` passes it as `last_updated_at`. The change: the stamp is rounded *up* to a whole second, because icechunk stores whole seconds and would otherwise refuse a file modified earlier in the same second (confirmed with local mtimes). `GroupResult` also carries `sources`.
- **11 accepted.** `credentials` (and the new `get_credentials`) are `repr=False`. `load_sc_creds`/`parse_sc_creds` accept the STS `{"Credentials": {...}}` wrapper and turn `Expiration` into `expires_after`. The handler fails fast on an S3 target with no write credentials configured. With `ISMIP7_WRITE_CREDS_SECRET` it passes icechunk a picklable `SecretCredentials` refresh callback that re-reads the secret (`s3_storage(get_credentials=...)`). docs/lambda.md says a run longer than the credential lifetime needs the secret rotated.
- **12 accepted.** Renamed to `MANIFEST_SPLIT_CHUNKS = 120`, with a comment that the unit is chunks along time. The `repository_config` keyword is now `split_chunks`. The preload setting is dropped. Tests cover config persistence (including a config-less open) and multi-manifest splitting.
- **13 accepted.** The file of each chunk row is its first non-empty entry, read through `ChunkManifest.get_entry`, not `_paths`.
- **14 accepted.** `prepare_for_write` raises `ValueError` when `time` has no units. A missing calendar still defaults to the CF default.
- **15 accepted.** `existing_groups` does one `store.list()` plus concurrent reads of the `zarr.json` of non-leaf nodes, and keeps groups carrying `ismip7_group`. It takes 1.8 s on the reviewer's 1,500 × 6 layout, against 8.3 s before.
- **16 accepted.** `--max-groups` caps the groups to write, after skipping. A target named `icechunk-<other region>` is refused by both CLIs (`check_target_region`), and a repository whose root records another region is refused by `ensure_hierarchy`. The README shows the exact `authorize_virtual_chunk_access` key, trailing slash included.
- **Test gaps: accepted.** Added: staged concurrent sessions and parallel threads (1); `existing_groups` on a fresh repo (3); `load_groups` with null sizes (4); a climatology group through write and verify (5); `pipeline.main` with a failed verification, then a re-run, plus unverified groups redone by a verifying run and a non-`ValueError` failure recorded (6, 8); writes with only the S3 container, asserting every stored location starts with the container prefix and that a reference outside it is refused; config persistence and multi-manifest splitting; commit metadata and tagging; credentials out of repr; the handler's fail-fast cases. In `tests/test_lambda.py` the "second run skips" check now runs for real (no `--dry-run`): the second run commits nothing, and a changed size in the index makes its group be rewritten.
- **Also changed (found while folding).** `concat_group`'s process pool uses a `forkserver` context. The handler opens the repository (icechunk runtime threads) before forking the pool, and a forked child deadlocked in the suite; a warm Lambda container is exposed to the same thing.
