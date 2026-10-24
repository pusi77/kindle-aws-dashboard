# Security

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability"). Don't open a public issue.

## Threat model

The Kindle is a dumb client: it holds no AWS credentials, only a **dashboard read token**.

- The dashboard image is served by a Lambda Function URL with `AuthType: NONE`, so the endpoint is
  public. The Lambda accepts a request only if `Authorization: Bearer <token>` matches the token
  stored in SSM Parameter Store (SecureString), compared in constant time.
- What a leaked token gives: read access to the latest dashboard PNG (your AWS spend figures) and
  the ability to send fake battery readings. Nothing else: no AWS API access, no write access.
- Rotation: overwrite the SSM parameter, then update `DASH_TOKEN` in the Kindle's `local/env.sh`.
  The Lambda caches the token for at most 5 minutes.
- Abuse of the public endpoint (invocation cost) is limited by reserved concurrency and by the
  request being cheap: no rendering on request, only an S3 read.
- The renderer has read-only access to Cost Explorer, Budgets and CloudWatch, and write access to
  its own private S3 bucket (Block Public Access, SSE-S3, TLS enforced).
- The repository contains no account IDs, ARNs, endpoint URLs, tokens or real cost data. CI runs
  gitleaks on every push and pull request.
