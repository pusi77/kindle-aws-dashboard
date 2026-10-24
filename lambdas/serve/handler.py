"""Serve latest.png to the Kindle behind a Lambda Function URL (auth type NONE).

The only credential is a bearer token compared in constant time with the SSM SecureString
parameter. Nothing is rendered here: one S3 read per request, so the Kindle's Wi-Fi is on briefly.
The token is cached for TOKEN_TTL seconds, so a rotation takes effect within that time.
"""

import base64
import hmac
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

TOKEN_TTL = 300
_s3 = boto3.client("s3")
_ssm = boto3.client("ssm")
_token = ("", float("-inf"))  # (value, fetched at): expired until the first fetch


def get_token() -> str:
    global _token
    if time.monotonic() - _token[1] > TOKEN_TTL:
        p = _ssm.get_parameter(Name=os.environ["TOKEN_PARAMETER"], WithDecryption=True)
        _token = (p["Parameter"]["Value"], time.monotonic())
    return _token[0]


def get_png() -> bytes:
    return _s3.get_object(Bucket=os.environ["BUCKET"], Key="latest.png")["Body"].read()


def battery_metric(value: int) -> None:
    """CloudWatch Embedded Metric Format: a log line that CloudWatch turns into a metric."""
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": "KindleDashboard",
                            "Dimensions": [[]],
                            "Metrics": [{"Name": "Battery", "Unit": "Percent"}],
                        }
                    ],
                },
                "Battery": value,
            }
        )
    )


def handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method") != "GET":
        return {"statusCode": 405}
    headers = event.get("headers") or {}  # Function URLs lower-case header names
    token = get_token()
    supplied = headers.get("authorization", "").encode()
    # Fail closed: an empty token must never make "Bearer " a valid credential.
    if not token or not hmac.compare_digest(supplied, f"Bearer {token}".encode()):
        print("unauthorized request")  # never log the header itself
        return {"statusCode": 401}

    battery = headers.get("x-kindle-battery", "")
    if battery.isdigit() and 0 <= int(battery) <= 100:
        battery_metric(int(battery))

    try:
        png = get_png()
    except ClientError as e:
        # Nothing rendered yet. Without s3:ListBucket a missing key is AccessDenied, not NoSuchKey.
        if e.response["Error"]["Code"] not in ("NoSuchKey", "AccessDenied"):
            raise
        return {"statusCode": 503}  # the Kindle keeps its current image
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "image/png", "Cache-Control": "no-store"},
        "body": base64.b64encode(png).decode(),
        "isBase64Encoded": True,
    }
