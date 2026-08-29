"""
CloudFormation custom resource: creates the k-NN vector index on the
OpenSearch Serverless collection and loads the synthetic ISTO policy
documents into it, embedding each chunk with the OpenAI embeddings endpoint.

Deletion is a no-op — this is a throwaway demo collection, torn down with
the rest of the stack; there's nothing else worth unwinding first.
"""
import json
import os
import time
import urllib.error
import urllib.request

import boto3

from aoss_http import signed_request
from policy_docs import POLICY_CHUNKS

EMBEDDING_MODEL = os.environ["EMBEDDING_MODEL"]
EMBEDDING_DIMENSION = 1536  # text-embedding-3-small

_secrets = boto3.client("secretsmanager")
_secret_cache = None


def handler(event, context):
    try:
        if event["RequestType"] in ("Create", "Update"):
            endpoint = event["ResourceProperties"]["CollectionEndpoint"].rstrip("/")
            index_name = event["ResourceProperties"]["IndexName"]
            _ensure_index(endpoint, index_name)
            _load_documents(endpoint, index_name)
        _send(event, context, "SUCCESS")
    except Exception as e:
        _send(event, context, "FAILED", reason=str(e))


def _ensure_index(endpoint: str, index_name: str) -> None:
    mapping = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": EMBEDDING_DIMENSION,
                    "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"},
                },
                "text": {"type": "text"},
                "doc": {"type": "keyword"},
            }
        },
    }

    last_error = None
    for attempt in range(6):
        try:
            signed_request("PUT", f"{endpoint}/{index_name}", mapping)
            return
        except RuntimeError as e:
            last_error = e
            if "resource_already_exists_exception" in str(e).lower():
                return
            time.sleep(10 * (attempt + 1))
    raise last_error


def _load_documents(endpoint: str, index_name: str) -> None:
    for chunk in POLICY_CHUNKS:
        vector = _embed(chunk["text"])
        body = {"embedding": vector, "text": chunk["text"], "doc": chunk["doc"]}
        signed_request("PUT", f"{endpoint}/{index_name}/_doc/{chunk['id']}", body)


def _embed(text: str) -> list[float]:
    body = json.dumps({"model": EMBEDDING_MODEL, "input": text}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_openai_api_key()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read())
    return payload["data"][0]["embedding"]


def _openai_api_key() -> str:
    global _secret_cache
    if _secret_cache is None:
        raw = _secrets.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"]
        _secret_cache = json.loads(raw)["OPENAI_API_KEY"]
    return _secret_cache


def _send(event, context, status, reason=None, data=None):
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch log stream {context.log_stream_name}",
            "PhysicalResourceId": event.get("PhysicalResourceId") or context.log_stream_name,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "Data": data or {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        event["ResponseURL"], data=body, method="PUT", headers={"Content-Type": ""}
    )
    urllib.request.urlopen(req)
