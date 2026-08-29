"""
RAG retrieval: embed the student's message with the OpenAI embeddings
endpoint (the one OpenAI Platform/API surface this demo uses outside of
Bedrock), then k-NN search it against the OpenSearch Serverless vector
collection seeded by lambda/kb_seed.
"""
import json
import os
import urllib.request

import boto3

from aoss_http import signed_request

_secrets = boto3.client("secretsmanager")
_secret_cache: dict | None = None

EMBEDDING_MODEL = os.environ["EMBEDDING_MODEL"]
OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"].rstrip("/")
KB_INDEX_NAME = os.environ["KB_INDEX_NAME"]


def _openai_api_key() -> str:
    global _secret_cache
    if _secret_cache is None:
        raw = _secrets.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"]
        _secret_cache = json.loads(raw)
    return _secret_cache["OPENAI_API_KEY"]


def embed(text: str) -> list[float]:
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


def search_policy_chunks(query: str, k: int = 3) -> list[str]:
    vector = embed(query)
    body = {
        "size": k,
        "query": {"knn": {"embedding": {"vector": vector, "k": k}}},
        "_source": ["text"],
    }
    result = signed_request("POST", f"{OPENSEARCH_ENDPOINT}/{KB_INDEX_NAME}/_search", body)
    hits = result.get("hits", {}).get("hits", [])
    return [h["_source"]["text"] for h in hits]
