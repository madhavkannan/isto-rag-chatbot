"""
Thin wrapper around bedrock-runtime's Converse API, with Bedrock Guardrails
attached to every call (layer 2 of the Story 3 defense — see guardrails.py
for layer 1 and tools.py for the load-bearing layer).
"""
import os

import boto3

_brt = boto3.client("bedrock-runtime")

MODEL_ID = os.environ["MODEL_ID"]
GUARDRAIL_ID = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VERSION = os.environ["GUARDRAIL_VERSION"]


def converse(messages: list[dict], system_prompt: str, tools: list[dict] | None = None) -> dict:
    kwargs = dict(
        modelId=MODEL_ID,
        system=[{"text": system_prompt}],
        messages=messages,
        # Low temperature: policy accuracy matters more than creativity here.
        inferenceConfig={"temperature": 0.15, "maxTokens": 900},
        guardrailConfig={
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION,
            "trace": "enabled",
        },
    )
    if tools:
        kwargs["toolConfig"] = {"tools": tools}
    return _brt.converse(**kwargs)
