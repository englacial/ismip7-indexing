# Running the virtualization on AWS Lambda

One Lambda invocation virtualizes and commits one store group. The fan-out
(`ismip7-fanout`) invokes the function once per group from a small EC2 box
or CloudShell (see Running) and collects the results. No Lithops: plain `boto3`.

## Sizing

One invocation has at most 900 s. Opening the files (reading each file's
HDF5 metadata and chunk index, then parsing it) dominates, and **it is bound
by S3, not CPU**: measured on Lambda (2026-11-05, in us-west-2 next to the
source bucket), six worker processes were busy 13% of the time, and 24 were
busy 7% of the time for the same total CPU. The handler runs
`ISMIP7_WORKERS` processes (template parameter `Workers`), independent of
the vCPU count. Each worker holds about 75 MB.

What the workers wait for depends on the read block size. A netCDF writer
that appends chunks as it goes leaves the chunk index scattered through the
file, and the parser fetches every block the index touches. With the
library's default 1 MiB blocks a finely chunked AIS 2 km file
(`CESM2-WACCM/ssp126/SDBN1-2000m`, chunked (1, 21, 3041), 1,740 chunks in
71 MB) cost 32 requests and 32 MB, and an ocean decade file 93 requests and
96 MB; a Lambda's network tops out near 110 MB/s, so the open phase of a
1,815-file 2 km group took 534 s and the 3,146-file ones could not finish.
Since 2026-11-06 the parser reads 32 KiB blocks (`HDF_READ_BLOCK_BYTES`):
the same files cost 34 requests and 1.2 MB, and 120 requests and 4.4 MB,
and the open phase is latency- and CPU-bound instead. Verification and
native-chunk writes read source files through a 1 MiB block cache
(`SOURCE_READ_BLOCK_BYTES`); the read-ahead reader they used before
refetched its buffer on every backward seek, which made one climatology
step take 250 s.

Measured on a 1,980-file GrIS 1 km group (`CanESM5/historical/dEBM2-1000m`
and siblings), `--verify 1` unless noted:

| Memory | vCPUs | Workers | Open | s/file | Commit + verify | Total | Peak memory | GB-s | Cost/group |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2,048 MB | 2 | 6 | 181 s | 0.091 | 13 s (no verify) | 197 s | 792 MB | 393 | $0.007 |
| 4,096 MB | 3 | 6 | 160 s | 0.081 | 14 s (no verify) | 177 s | 800 MB | 707 | $0.012 |
| 10,240 MB | 6 | 6 | 140 s | 0.071 | 13 s (no verify) | 154 s | 814 MB | 1,542 | $0.026 |
| 10,240 MB | 6 | 6 | 198 s | 0.100 | 27 s | 225 s | 807 MB | 2,250 | $0.037 |
| 10,240 MB | 6 | 24 | 81 s | 0.041 | 25 s | 106 s | 1,829 MB | 1,067 | $0.018 |

The AIS 8 km group `CESM2-WACCM/historical/SDBN1-8000m` (1,815 files) opened
at 0.059 s/file with 6 workers at 10 GB; the 1 km SDBN1 GrIS group with ten
times the chunks per file (786,060 references) opened at 0.149 s/file and
took 43 s to commit and verify, peaking at 1,060 MB.

**Production setting: 10,240 MB and 24 workers** (the template defaults).
The full-region run of 2026-11-06 at 2,048 MB and 8 workers verified 174
of 218 groups, but the chunk-heaviest quarter of the archive did not fit:
the AIS 2 km SDBN1 groups (up to 4 million references) and the GrIS 1 km
and 4 km ssp runs ran out of memory at 2,048 MB or of time; at 10 GB with
24 workers they peak at 3.3 GB and the 3,718-file GrIS groups take under
10 minutes. With 32 KiB read blocks the largest groups of the archive, the
three 3,146-file AIS 2 km SDBN1 ssp groups (3.98 million references each),
open in 304-314 s and commit and verify in 250-429 s: 554 s, 604 s and
740 s in all, so every group fits one invocation and the serial runner is
not needed. The whole archive costs about four dollars per full run at
10 GB; the 2 GB setting saves about three.

The fan-out estimates each group as `n_files × --seconds-per-file /
--speedup` and does **not** invoke groups estimated over
`--max-lambda-seconds` (default 700); such groups are recorded as `status:
"deferred"` and listed in `--big-groups`, with the serial-runner command to
run them on a big machine. With the measured in-region rates nothing is
deferred: pass `--seconds-per-file 0.1 --speedup 1` (a conservative wall
rate per file at 8 workers) and the largest group estimates at ~400 s.
Re-plan with `--dry-run` before a bulk run. Each `GroupRun` in the report
records `n_files`, `timings` (`open`, `open_cpu`, `open_worker_cpu`,
`open_utilization`, `commit`, `commit_cpu`), `cpu_count` and `n_workers`,
so every run re-measures this.

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

Whoever deploys needs the permissions in `infra/deployer-policy.json`
(scoped to the stack's own resources). The one statement likely to meet
resistance is `iam:CreateRole` and friends on the execution role. If an
administrator creates that role instead, the deployer policy shrinks to
`infra/deployer-policy-prepared-role.json`, which keeps only `iam:PassRole`
on that role. Either way the deployer runs `infra/deploy.sh`.

Path 1, the administrator attaches the all-in-one policy and the stack
creates the role:

```bash
aws iam put-user-policy --user-name <deployer> --policy-name ismip7-deployer \
    --policy-document file://infra/deployer-policy.json
# then, as the deployer:
AWS_PROFILE=<englacial> infra/deploy.sh
```

Path 2, the administrator creates the role (trust for Lambda, the basic
execution policy for its logs, and `GetSecretValue` on the credentials
secret: it reads nothing else) and attaches the smaller policy:

```bash
aws iam create-role --role-name ismip7-virtualize-execution \
    --assume-role-policy-document file://infra/execution-role-trust.json
aws iam attach-role-policy --role-name ismip7-virtualize-execution \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name ismip7-virtualize-execution \
    --policy-name read-credentials-secret --policy-document file://infra/execution-role-policy.json
aws iam put-user-policy --user-name <deployer> --policy-name ismip7-deployer \
    --policy-document file://infra/deployer-policy-prepared-role.json
# then, as the deployer:
EXECUTION_ROLE_ARN=arn:aws:iam::429435741471:role/ismip7-virtualize-execution AWS_PROFILE=<englacial> infra/deploy.sh
```

After either path, load the source.coop credentials into the secret and
run the fan-out (see Running):

```bash
aws secretsmanager put-secret-value --region us-west-2 --secret-id ismip7/sc_creds \
    --secret-string file://sc_creds.json
```

1. ECR repository: `aws ecr create-repository --repository-name ismip7-virtualize`.
2. Build and push the image. Use `buildx` with `--platform linux/amd64` (Docker on
   Apple silicon otherwise builds arm64, and `create-function` defaults to
   x86_64) and `--provenance=false` (without it buildx pushes an OCI index with
   attestations, which Lambda rejects). The build fails if `GIT_SHA` is not given.
   It installs the dependencies first, from `requirements-lambda.txt` exported
   from the lock file, so that 1 GB layer is cached and a code change only
   re-pushes the small layers after it (`infra/deploy.sh` runs the export):
   ```bash
   uv export --extra lambda --no-emit-project --no-hashes --no-dev -o requirements-lambda.txt
   docker buildx build --platform linux/amd64 --provenance=false \
       --build-arg GIT_SHA=$(git rev-parse HEAD) -f Dockerfile.lambda -t ismip7-virtualize --load .
   aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-west-2.amazonaws.com
   docker tag ismip7-virtualize:latest <acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest
   docker push <acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest
   ```
   The package's `lambda` extra (pandas, boto3) is part of that export.
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
     --memory-size 2048 --timeout 900 --ephemeral-storage Size=2048 \
     --environment Variables={ISMIP7_WRITE_CREDS_SECRET=ismip7/sc_creds,ISMIP7_WORKERS=8}
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

**Re-index before every pass.** The source files change under the store: on
2026-11-06 the provider replaced the 2300 files of four temperature variables
in the MRI-ESM2-0 ssp585 products with one-step files, after two of those
groups had been verified, and the stored references into the old bytes went
stale without any error. The index records each file's size and every group
records a digest of its keys and sizes (`ismip7_sources_sha256`), so a fan-out
run from a fresh index rewrites exactly the groups whose files changed
(`reason: "sources changed"` in the report) and skips the rest. Run
`ismip7-index` first, diff it against the previous index if you want to see
what moved, then fan out.

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
2,048 MB and 8 workers is about 400 GB-s per 2,000-file group (about
$0.007); the 218 groups, most of them smaller, come to a couple of dollars
per full run, and at 10 GB with 24 workers about four. The writes through
the data.source.coop proxy (a few hundred manifest and snapshot objects per
group) are free of charge on source.coop. Well inside the SOW's compute
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
