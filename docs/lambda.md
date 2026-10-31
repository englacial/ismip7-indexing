# Running the virtualization on AWS Lambda

One Lambda invocation virtualizes and commits one store group. The fan-out
(`ismip7-fanout`) invokes the function once per group from a laptop or a
small EC2 box and collects the results. No Lithops: plain `boto3`.

## Sizing

| Product class | Files per group | Time per file (1 vCPU) | Group time at 6 vCPUs |
|---|---|---|---|
| 8 km / 4 km SMB (11 variables) | 1,815 (hist) / 3,146 (ssp) | ~1.2 s | ~6 / ~11 min |
| 2 km / 1 km SMB | same counts, larger headers | ~1.5 s (to measure) | ~8 / ~13 min |
| AIS ocean (3 variables, decadal) | 51 / 87 | ~20 s | ~3 / ~5 min |

The ssp runs of the 2 km and 1 km SMB products are close to the 15-minute
limit. If a group times out, split it: run the fan-out with `--max-files`
(to add) or raise the per-group parallelism by giving the function 10 GB.
Measured on 2026-10-31 on 4 cores: 24 files in 9.3 s with a process pool.

## One-time setup (Astera account, us-west-2)

1. ECR repository: `aws ecr create-repository --repository-name ismip7-virtualize`.
2. Build and push the image:
   ```bash
   docker build -f Dockerfile.lambda -t ismip7-virtualize .
   aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-west-2.amazonaws.com
   docker tag ismip7-virtualize:latest <acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest
   docker push <acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest
   ```
3. Execution role: `AWSLambdaBasicExecutionRole` plus `secretsmanager:GetSecretValue`
   on the write-credentials secret. Reads of the public source bucket are
   anonymous and need no IAM permission. (The ISMIP6 role was
   `lithops-ismip6-executor`.)
4. Write credentials for `englacial/ismip7` on source.coop: store the
   `sc_creds.json` content in Secrets Manager as `ismip7/sc_creds` and set
   `ISMIP7_WRITE_CREDS_SECRET=ismip7/sc_creds` on the function. (For a quick
   test, `ISMIP7_WRITE_CREDS_JSON` with the JSON itself also works.) Source.coop
   credentials are temporary; rotate the secret before a long run.
5. Function:
   ```bash
   aws lambda create-function --function-name ismip7-virtualize \
     --package-type Image --code ImageUri=<acct>.dkr.ecr.us-west-2.amazonaws.com/ismip7-virtualize:latest \
     --role arn:aws:iam::<acct>:role/ismip7-virtualize \
     --memory-size 10240 --timeout 900 --ephemeral-storage Size=2048 \
     --environment Variables={ISMIP7_WRITE_CREDS_SECRET=ismip7/sc_creds}
   aws lambda put-function-concurrency --function-name ismip7-virtualize --reserved-concurrent-executions 50
   ```

## Running

```bash
ismip7-index --out index.parquet --save-listing listing-$(date +%F).txt.gz
ismip7-fanout --index index.parquet --region AIS --groups 'CESM2-WACCM/historical/SDBN1-8000m' \
    --function ismip7-virtualize --verify 1 --report run-ais-1.json        # one group first
ismip7-fanout --index index.parquet --region AIS --function ismip7-virtualize \
    --concurrency 40 --verify 1 --report run-ais.json                        # everything else
```

Groups already in the repository are skipped; `--overwrite` rewrites them.
Each group is its own commit with `rebase_with=ConflictDetector()`, so
concurrent workers never conflict (they touch disjoint paths); the ref update
is a compare-and-swap on S3, which handles the contention.

## Cost

Header reads only: a few GB of egress for the whole archive. Lambda at
10 GB for ~1,500 groups averaging 5 minutes is about 1,500 × 300 s × 10 GB ×
$0.0000166667/GB-s ≈ $75; S3 PUTs for ~60k manifest/snapshot objects a few
dollars. Well inside the SOW's compute line.

## Local equivalent

```bash
ismip7-fanout --index index.parquet --region AIS --groups CESM2-WACCM/historical/SDBN1-8000m \
    --target local:/tmp/icechunk-ais --local --verify 1 --report run.json
```

runs the same handler in-process with a process pool; the store is written
to local disk with virtual chunks pointing at the public bucket.
