# Notes

Raw material for the article: decisions, gotchas, numbers.

## Decisions

- **Region eu-west-1 (Ireland)**, not Milan: eu-south-1 is an opt-in region and was disabled on
  the account; enabling it wasn't worth it. Cost Explorer and Budgets are us-east-1 anyway.
- **CDK bootstrap** with the default execution policy (AdministratorAccess for CloudFormation).
  Narrowing it with `--cloudformation-execution-policies` is possible but a maintenance burden.
- **Function URL, not CloudFront.** Verified 2026-10-24 in eu-south-1 and eu-west-1: a
  `*.lambda-url.<region>.on.aws` endpoint negotiates TLS 1.2 with `ECDHE-RSA-AES128-GCM-SHA256` (supported by OpenSSL 1.0.2) and validates
  against the 2019 curl CA bundle. Chain: Amazon RSA 2048 S18/S20 → Amazon Root CA 1 → Starfield
  Services Root G2; both roots are in the 2019 bundle. Risk: the Function URL TLS policy isn't
  configurable; CloudFront with `TLSv1.2_2021` is the fallback if TLS 1.2 is ever dropped.
- **Refresh every 4 hours.** Cost Explorer data refreshes a few times a day, so the renderer runs at
  07, 11, 15, 19 (Europe/Rome) and the Kindle fetches a few minutes later
  (`REFRESH_SCHEDULE="5 7,11,15,19,23 * * *"`). No separate S3 cache: every render calls Cost
  Explorer, and `latest.json` keeps the data used.
- **Credits and refunds excluded** from all Cost Explorer figures (gross usage).
- **Management account**, so figures cover the whole organization.
- **No cost allocation tags.** The dashboard's own cost is dominated by its Cost Explorer API calls,
  which can't be tagged anyway: the footer shows the "AWS Cost Explorer" service line.
- **No "Kindle not seen" alarm** (simplicity). Only the battery alarm.
- **No CloudWatch metrics panel.** The default `AWS/Billing EstimatedCharges` needs billing alerts
  and repeats the cost numbers; the freed space went to the daily chart.
- **Budgets kept**: the status band reads the account's cost budgets (actual and forecast).
- **Python 3.14** (3.13 would trip cdk-nag AwsSolutions-L1).
- **SSM SecureString, not Secrets Manager**: $0 vs $0.40/month. CloudFormation can't create
  SecureStrings, so the token is put with the AWS CLI and the stack only references its name.
- **No KMS CMK on the SNS topic**: CloudWatch alarms can't publish to a topic encrypted with the
  AWS-managed `aws/sns` key, and a CMK costs $1/month.

## Tooling

- **TypeScript 7 broke ts-jest** (peer `typescript <7`). CDK tests use Node's built-in type
  stripping and `node:test`; `cdk.json` runs `node bin/app.ts`. No ts-node, no jest.
- **cdk-nag 3** plugs into CDK's native `Validations`: an unacknowledged finding makes
  `app.synth()` throw. Acknowledge with `Validations.of(x).acknowledge({ id: 'AwsSolutions::AwsSolutions-S1', reason })`.
- **Pillow for Lambda arm64 without Docker**: `uv pip install --python-platform aarch64-manylinux_2_28
  --python-version 3.14 --only-binary :all:` gives aarch64 `.so` files on an x86 Linux or a Mac.
  `manylinux_2_28` is fine because Lambda's Amazon Linux 2023 has glibc 2.34. Pillow 13 + tzdata = 24 MB.
- **tzdata bundled**: `zoneinfo` needs it; Lambda doesn't guarantee system tz data.
- GitHub Actions pinned by commit SHA.

## Numbers

| What | Value |
|---|---|
| Local render (4 fixtures, x86 laptop) | ~0.15 s each |
| PNG size | 26–44 KiB |
| Cost Explorer requests per render | 3 (GetCostAndUsage, GetCostForecast, GetAnomalies; no pagination at this account size) |
| Cost Explorer requests per day | 4 renders × 3 = 12 → ~360/month ≈ **$3.60/month** |
| Collect, real account, from a laptop in Italy | 2.7–3.4 s |
| Render, real data | 0.1 s, ~40 KiB |
| Renderer Lambda, real run (512 MB arm64) | 3.1 s (billed 3.8 s with cold start), 3 CE requests |
| Serve, Kindle-like request (TLS 1.2, 2019 CA bundle), cold | 1.15 s total, 41.6 KB; 401s in ~0.18 s |

## Stack

- **Account-agnostic stack** (`env: { region }` only, no lookups): the synthesized template has no
  account ID, and a test asserts there's no 12-digit number in it.
- **Public Function URL permissions**: CDK adds `lambda:InvokeFunctionUrl` (auth NONE) *and*
  `lambda:InvokeFunction` for `*` with `InvokedViaFunctionUrl: true` (required for Function URLs
  since late 2025). The condition stops direct `Invoke` calls from other accounts; a test asserts it.
- **No AWS managed policies**: each function gets its own role and log group (30 days), and the
  role only gets `logs:CreateLogStream`/`PutLogEvents` on that group. Avoids cdk-nag IAM4.
- **cdk-nag acknowledgements, all with reasons in code**: S1 (bucket access logs), SNS2 (topic
  KMS), IAM5 for Cost Explorer/GetMetricData `*`, `budget/*`, log streams, scheduler → renderer versions.
- **Bucket `RemovalPolicy.RETAIN`**: a destroy never fails on a non-empty bucket; delete it by
  hand. `autoDeleteObjects` would add a custom-resource Lambda just for teardown.
- **Renderer asset**: 25 MB unzipped (Pillow 13 + libs, tzdata, two Inter weights).
- **Retries**: Lambda async retries 0 for the renderer (a retry costs Cost Explorer calls),
  scheduler retries 2 within 1 h for failed invocations only.
- **Reserved concurrency**: renderer 1, serve 2 (account quota 1000).
- **SSM SecureString with `aws/ssm`**: no `kms:Decrypt` in the serve role; the AWS managed key's
  policy allows decryption via SSM. Confirmed by the first real request.
- **Token via stdin**: `make token` pipes `openssl rand -hex 32` into
  `aws ssm put-parameter --value file:///dev/stdin`, so it never shows up in `ps` or shell history.
- **Low-battery alarm cadence**: the metric only arrives at each fetch (every 4 h) and missing data
  is "not breaching", so while the battery is low the alarm goes OK after an hour and fires again
  at the next fetch: one email per fetch. Acceptable as a reminder.
- **First image after deploy**: nothing exists until the next scheduled slot; `make render` invokes
  the renderer once. Until then the serve Lambda answers 503. Without `s3:ListBucket` a missing
  object is `AccessDenied`, not `NoSuchKey`.
- **Bug found by the first real request**: the token cache was `("", 0.0)` with a TTL check on
  `time.monotonic()`, which starts near 0 in a new Lambda microVM. For the first 5 minutes of every
  execution environment the token was never fetched: the real token got 401, and `Bearer ` with an
  empty token would have matched. Unit tests stubbed `get_token()` and missed it. Fix: start
  expired, never accept an empty token, and a test through the real cache path with
  `monotonic()` pinned to 1.0.
- `make token` relies on the AWS CLI expanding `file:///dev/stdin`: verified with `--debug`.
- `cdk diff` on a new stack publishes the template to the bootstrap bucket and creates a
  read-only change set: harmless, but not purely local.

## Data gotchas

- **`GetCostForecast` with MONTHLY granularity and a period starting today returns the whole
  month** (actuals so far + forecast), not just the remaining days. Verified: it equals
  month-to-date + a DAILY forecast over the remaining days, and matches the Budgets forecast.
  Adding month-to-date to it would double count.
- **One `GetCostAndUsage` call covers 4 panels**: DAILY, grouped by SERVICE, from the 1st of last
  month to today → month to date, same point last month, the 14 daily bars, the top 5, and the
  "AWS Cost Explorer" line used as this dashboard's own cost.
- "Same point last month" = days 1..today's day number of last month; month to date includes
  today's partial day.
- Credits and refunds are excluded with `Not RECORD_TYPE in [Credit, Refund]`, on both cost and forecast.
- **A disabled opt-in region answers `InvalidClientTokenId`**, not AccessDenied: that's what
  `GetMetricData` in eu-south-1 returned while Milan was disabled on the account.
- Errors shown on the dashboard are API error codes only: messages can contain ARNs.

## Kindle side

- `dash.sh` runs `set -x` before sourcing `env.sh`: with `DEBUG=true`, a `DASH_TOKEN=` line would
  be traced into `dash.log`. Fix in the kindle-dash-pw3 PR: enable tracing after sourcing.
- Kindle `env.sh` for a 4-hour refresh: `INTERVAL_MINUTES=240` and
  `REFRESH_SCHEDULE="5 7,11,15,19,23 * * *"`. `dash.sh` shows `sleeping.png` whenever the next
  wake-up is more than `INTERVAL_MINUTES*60+60` s away, so `INTERVAL_MINUTES` must match the 4 h
  gap, and the 23:05 slot is the one that switches to the sleep screen.
- **"Kindle n/a" in the footer after the first deploy**: the renderer reads the battery metric at
  render time, and the first image is rendered before the Kindle has ever fetched. The footer
  always shows the *previous* fetch (render at :00, fetch at :05).
- First real fetch from the Kindle: 2026-10-25, TLS 1.2 handshake with the 2019 CA bundle, 200,
  battery metric published.
