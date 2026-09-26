"""Object storage (Garage locally, AWS S3 in prod). Same code for both:

  S3_ENDPOINT_URL  set for Garage (http://localhost:3900), unset for AWS S3
  S3_BUCKET        platform-tasks-dev / platform-tasks-prod
  AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   (IRSA on EKS: no keys)
"""
import json
import os

import boto3
from botocore.config import Config

BUCKET = os.getenv("S3_BUCKET", "platform-tasks-dev")

_client = boto3.client(
  "s3",
  endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
  config=Config(
    s3={"addressing_style": "path"},             # http://host/bucket/key: no wildcard DNS locally
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=5,
    read_timeout=10,
  ),
)


def put_json(key, obj):
  """Write obj as JSON at key, return its s3:// URI. Deterministic keys: writing the
  same task twice overwrites, never duplicates."""
  _client.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj).encode(), ContentType="application/json")
  return f"s3://{BUCKET}/{key}"
