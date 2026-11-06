# Running the virtualization on AWS Lambda

One Lambda invocation virtualizes and commits one store group. The fan-out
(`ismip7-fanout`) invokes the function once per group from a small EC2 box
or CloudShell (see Running) and collects the results. No Lithops: plain `boto3`.

## Sizing

One invocation has at most 900 s. Opening the files (HDF5 header parsing,
CPU-bound) dominates; the handler runs one worker process per vCPU (6 at
10,240 MB). Measured locally, six processes give about **3.9x** over one,
not 6x. Estimated open time per group = files × seconds per file / 3.9:

| Product class | Files per group | Time per file (1 vCPU, local) | Estimated open time | Where it runs |
|---|---|---|---|---|
| 8 km / 4 km SMB (11 variables), hist | 1,815 | ~1.2 s | ~560 s | Lambda |
| 8 km / 4 km SMB, ssp | 3,146 | ~1.2 s | ~970 s | deferred to a big machine |
| 2 km / 1 km SMB, hist | 1,815 | ~1.5 s (to measure) | ~700 s | borderline |
| 2 km / 1 km SMB, ssp | 3,146 | ~1.5 s (to measure) | ~1,210 s | deferred to a big machine |
| AIS ocean (3 variables, decadal) | 51 / 87 | ~20 s | ~260 / ~450 s | Lambda |

Concatenation, the commit and verification come on top, so the fan-out
keeps a margin. It estimates each group as `n_files × --seconds-per-file /
--speedup` (defaults 1.3 and 3.9) and does **not** invoke groups estimated over
`--max-lambda-seconds` (default 700). Those groups are recorded as
`status: "deferred"` in the report and listed in `--big-groups` (default
`big-groups-<region>-<UTC time>.txt`), and the fan-out prints the command
that runs them on a big machine with the serial runner and a process pool:

```bash
ismip7-virtualize --index index.parquet --region AIS --target s3://... --write-creds sc_creds.json \
    --verify 1 --workers $(nproc) --groups <the deferred group paths>
```

On a 32-vCPU EC2 instance (e.g. `c7i.8xlarge`, at the same ~65% process
efficiency) a 3,146-file group takes about 3,146 × 1.3 s / 21 ≈ 200 s.

The estimate uses one per-file figure for every group. The ocean files take
~20 s each, so their estimate at the default 1.3 s is far too low, but those
groups are small enough to fit anyway.

**The per-file seconds are local measurements. They must be measured on
Lambda in the first run.** Each `GroupRun` in the report records `n_files`,
`timings.open`, `cpu_count` and `n_workers`. From the first group,
`timings.open / n_files` is the effective seconds per file at the Lambda's
parallelism: pass that as `--seconds-per-file` with `--speedup 1`, or
`timings.open × 3.9 / n_files` with the default speedup. Re-plan with the
measured figure (`--dry-run`) before the bulk run.

As a backstop, the handler reads the Lambda context. If fewer than 90 s
remain after opening the files, it returns `status: failed, error:
"deadline"` with its timings rather than starting a commit it cannot
finish. Lambda would otherwise kill it with `Sandbox.Timedout`. Such a group
needs a lower `--max-lambda-seconds` or a big machine.

## One-time setup (Astera account 429435741471, us-west-2)

The resources below are also declared in `infra/lambda-stack.yaml`
(CloudFormation: ECR repository, execution role, Secrets Manager secret,
the function, its log group) and created or updated by `infra/deploy.sh`,
which also builds and pushes the image. The manual steps that follow are
what the stack does, for reference.

1. ECR repository: `aws ecr create-repository --repository-name ismip7-virtualize`.
2. Build and push the image. Use `buildx` with `--platform linux/amd64` (Docker on
   Apple silicon otherwise builds arm64, and `create-function` defaults to
   x86_64) and `--provenance=false` (without it buildx pushes an OCI index with
   attestations, which Lambda rejects). The build fails if `GIT_SHA` is not given:
   ```bash
   docker buildx build --platform linux/amd64 --provenance=false \
       --build-arg GIT_SHA=$(git rev-parse HEAD) -f Dockerfile.lambda -t ismip7-virtualize --load .
   aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-west-2.amazonaws.com
   docker tag ismip7-virtualize:latest <acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest
   docker push <acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest
   ```
   The image installs the package with its `lambda` extra (pandas, boto3).
   pyarrow is not needed: the handler reads no Parquet. `.dockerignore` keeps
   everything but the package out of the build context.

   Before the first deploy, run the image under the Lambda Runtime Interface
   Emulator (built into the base image) as a non-root user with no writable
   `/dev/shm`, the way Lambda runs it:
   ```bash
   docker run --rm -p 9000:8080 --user 1000:1000 --read-only --tmpfs /tmp \
       --mount type=tmpfs,destination=/dev/shm,tmpfs-mode=0555 ismip7-virtualize
   curl -s localhost:9000/2015-03-31/functions/function/invocations -d @event.json
   ```
   (Not yet run: the base image could not be pulled here. `event.json` is one
   group's event against a scratch repository the fan-out created.) The
   handler opens files in plain `Process` + `Pipe` workers:
   `ProcessPoolExecutor` needs POSIX semaphores in `/dev/shm`, which Lambda
   does not have.
3. Execution role: `AWSLambdaBasicExecutionRole` plus `secretsmanager:GetSecretValue`
   on the write-credentials secret. Reads of the public source bucket are
   anonymous and need no IAM permission. (The ISMIP6 role was
   `lithops-ismip6-executor`.)
4. Write credentials for `englacial/ismip7` on source.coop: store the
   `sc_creds.json` content in Secrets Manager as `ismip7/sc_creds` and set
   `ISMIP7_WRITE_CREDS_SECRET=ismip7/sc_creds` on the function. (For a quick
   test, `ISMIP7_WRITE_CREDS_JSON` with the JSON itself also works, as static
   credentials. Set only one: with both set, every invocation fails.) Both the
   flat `sc_creds.json` shape and the STS-style `{"Credentials": {...}}`
   wrapper are accepted. A function with an S3 target and neither variable set
   fails at once rather than at commit time (the execution role cannot write
   to source.coop).

   Source.coop credentials are temporary. The handler reads the credentials
   once at the start and **refuses them when they expire before the
   invocation's deadline** (now + the remaining time from the Lambda context),
   naming the expiry. This turns a failure at commit, after all the work,
   into an immediate one. The fan-out does the same with `--write-creds`
   before invoking anything: it needs them for at least 20 more minutes.

   With the secret, the handler also passes icechunk a refresh callback, which
   re-reads the secret when the credentials' `Expiration` passes. **The secret
   must carry `Expiration`**: without it icechunk never re-reads it. Refreshing
   only helps if the secret holds new credentials by then, so a run longer
   than the credential lifetime needs the secret rotated before the
   credentials expire (put fresh `sc_creds.json` content into
   `ismip7/sc_creds`; new invocations pick it up at once). The fan-out itself
   also writes (it creates the repository and the parent groups), so pass it a
   current `--write-creds`; an S3 target without it is refused.
5. Function:
   ```bash
   aws lambda create-function --function-name ismip7-virtualize \
     --package-type Image --code ImageUri=<acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest \
     --role arn:aws:iam::<acct>:role/ismip7-virtualize \
     --memory-size 10240 --timeout 900 --ephemeral-storage Size=2048 \
     --environment Variables={ISMIP7_WRITE_CREDS_SECRET=ismip7/sc_creds}
   aws lambda put-function-concurrency --function-name ismip7-virtualize --reserved-concurrent-executions 250
   ```

## Verified under the Runtime Interface Emulator (2026-11-01)

The image built from `Dockerfile.lambda` (1.19 GB, linux/amd64) was run under
the Lambda Runtime Interface Emulator as uid 1000 with `/dev/shm` mounted
read-only (the conditions that broke the earlier process pool), and invoked
with a real six-file event against a mounted local target repository:
init 0.7 s; four pipe-connected worker processes opened the files in 4.1 s
(reads of the public bucket through the sandbox proxy); commit 5.0 s;
verification passed; INFO notes reached the log; the result record carried
`cpu_count`, `n_workers` and `timings`. `scripts/dev/rie_run.sh` repeats this.
The per-file time inside the container was about 0.7 s with four workers,
measured from a sandbox far from us-west-2; the in-region figure is still to
be measured on the first real invocation and fed back as `--seconds-per-file`.

## Running

Run the fan-out from an EC2 instance or AWS CloudShell in us-west-2 rather
than a laptop. Each invocation is a synchronous HTTPS request held open for
up to 15 minutes. Home routers and NATs drop idle connections (AWS NAT
Gateway after 350 s), and a dropped connection loses the result of a group
that may well have committed. The invoker sets TCP keepalive, a 960 s read
timeout (above the function timeout plus cold start), one pooled connection
per thread, and no boto3 retries (a retry would start a second concurrent
invocation of the same group).

```bash
ismip7-index --out index.parquet --save-listing listing-$(date +%F).txt.gz
ismip7-fanout --index index.parquet --region AIS --groups 'CESM2-WACCM/historical/SDBN1-8000m' \
    --function ismip7-virtualize --write-creds sc_creds.json --verify 1 --report run-ais-1.json  # one group first
# measure seconds per file from that report (see Sizing), then plan and run the rest:
ismip7-fanout --index index.parquet --region AIS --function ismip7-virtualize --write-creds sc_creds.json \
    --concurrency 100 --verify 1 --seconds-per-file <measured> --dry-run
ismip7-fanout --index index.parquet --region AIS --function ismip7-virtualize --write-creds sc_creds.json \
    --concurrency 100 --verify 1 --seconds-per-file <measured>
# in-region every group fits one invocation (largest: 4,004 GrIS 1 km files, ~12 min), so nothing is
# deferred; --concurrency 100 staggers the commits through the data.source.coop proxy while the
# function's reserved concurrency (250) would allow a whole region at once
```

The report defaults to `run-<region>-<UTC time>.json`, so a re-run does not
overwrite the previous one. `--aws-region` is the Lambda function's region and
`--target-region` the target bucket's (both default `us-west-2`). A `local:`
target cannot be combined with `--function` (the workers could not reach
it). `--max-groups N` writes the first N groups that would be written, after
skipping existing groups and deferring big ones, in a real run and in a
`--dry-run` alike. A dry run reads the repository when it can (a local
target, or `--write-creds`) to know what would be skipped.

**Ctrl-C** stops the fan-out from invoking further groups. The groups
already invoked keep running on Lambda for up to 15 minutes. They are
recorded as `status: "in-flight"` in the report, and the fan-out lists them
and exits with status 130. Wait for them to finish before re-running:
a re-run that rewrites a group still being written conflicts with it.

Before invoking any worker, the fan-out creates the repository if needed
and, in one commit, the root and every parent group (`<ESM>`,
`<ESM>/<experiment>`, `climatology/...`) of the groups it is about to write
(`store.ensure_hierarchy`). This is required, not an optimisation: two
sessions that both create the same new node (the root of a fresh
repository, or a shared `<ESM>` group) cannot be rebased onto each other
(`NewNodeConflictsWithExistingNode`), and the fan-out sends groups in sorted
order, so concurrent workers almost always share a parent. With the parents
in place, each worker's commit only adds its own leaf group, and
`rebase_with=ConflictDetector()` rebases it over the other workers' commits;
the ref update is a compare-and-swap on S3, which handles the contention. As
a backstop, `write_group` re-stages a commit whose only conflicts are
concurrent creations of its ancestors. The handler only opens the repository
and fails if it does not exist. Two workers writing the *same* group still
conflict, which the fan-out never does.

Each group is written in one commit; with `--verify N` the handler then
compares sampled timesteps of N source files per variable (ranged reads,
never whole objects) against the snapshot just written and either sets the
group attribute `ismip7_verified` (a small follow-up commit) or deletes the
group again, so the store never carries a group known to be bad. The report
(rewritten as each group finishes) records `committed` and `deleted` per group,
plus `n_notes` and the first five normalisation notes (`notes_head`; all of
them go to CloudWatch at INFO), `cpu_count`, `n_workers` and `timings`.

A re-run skips a group only when its `ismip7_sources_sha256` attribute (over
the sorted source `(key, size)` pairs) matches the index and, with
`--verify`, it carries `ismip7_verified`; other existing groups are
rewritten, with the reason in the report. `--overwrite` rewrites everything
selected. Release snapshots are tagged by hand with `store.tag_release(repo, name)`.

## Cost

Header reads only: a few GB of egress for the whole archive. Lambda at
10 GB for ~1,500 groups averaging 5 minutes is about 1,500 × 300 s × 10 GB ×
$0.0000166667/GB-s ≈ $75. The deferred ssp SMB groups run on one EC2
instance for a few hours instead (a few dollars). S3 PUTs for ~60k
manifest/snapshot objects cost a few dollars. Well inside the SOW's compute
line.

## Local equivalent

```bash
ismip7-fanout --index index.parquet --region AIS --groups CESM2-WACCM/historical/SDBN1-8000m \
    --target local:/tmp/icechunk-ais --local --verify 1 --report run.json
```

runs the same handler in-process with its process pool; the store is written
to local disk with virtual chunks pointing at the public bucket. Groups are
not deferred with `--local` (there is no 900 s limit). With
`--source-root DIR` the handler reads every key from `file://DIR/<key>` (a
local mirror of the bucket), and the repository gets a virtual chunk
container for `DIR`. The tests run the fan-out end to end this way.
