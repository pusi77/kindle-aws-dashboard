"""Renderer Lambda, run by EventBridge Scheduler: collect, render, write latest.png and .json."""

import json
import logging
import os

import boto3

from collect import Collector, collect
from render import render

logging.getLogger().setLevel(logging.INFO)
_s3 = boto3.client("s3")


def handler(event, context):
    data = collect(Collector())
    png = render(data)
    bucket = os.environ["BUCKET"]
    _s3.put_object(
        Bucket=bucket,
        Key="latest.json",
        Body=json.dumps(data).encode(),
        ContentType="application/json",
    )
    _s3.put_object(Bucket=bucket, Key="latest.png", Body=png, ContentType="image/png")
    summary = {"bytes": len(png), "ce_requests": data["ce_requests"], "errors": data["errors"]}
    logging.info("rendered %s", summary)
    return summary
