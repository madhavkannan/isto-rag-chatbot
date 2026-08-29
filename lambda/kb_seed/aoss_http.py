"""
Minimal SigV4-signed HTTP client for the OpenSearch Serverless data plane.
No extra dependencies (no opensearch-py, no requests) — botocore ships with
the Lambda Python runtime, so this only needs stdlib + botocore.

(Duplicated from lambda/orchestrator/aoss_http.py — kept as a plain copy
rather than a shared layer, since this is a small demo and a Lambda layer
would be more moving parts than the ~25 lines it saves.)
"""
import json
import os
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

_session = boto3.Session()
_region = _session.region_name or os.environ.get("AWS_REGION")


def signed_request(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    creds = _session.get_credentials()
    if creds is None:
        raise RuntimeError("No AWS credentials available to sign OpenSearch Serverless request")
    frozen = creds.get_frozen_credentials()

    aws_request = AWSRequest(method=method, url=url, data=data, headers={"Content-Type": "application/json"})
    SigV4Auth(frozen, "aoss", _region).add_auth(aws_request)

    req = urllib.request.Request(url, data=data, method=method, headers=dict(aws_request.headers))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenSearch Serverless {method} {url} failed: {e.code} {e.read().decode()}") from e
