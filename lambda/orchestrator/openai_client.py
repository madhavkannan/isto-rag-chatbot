"""
Fallback chat client: calls OpenAI's Platform API (Chat Completions)
directly instead of through Bedrock bedrock-runtime. Use this in place of
bedrock_client.py if Bedrock model access isn't enabled on the deploying
AWS account in time for the demo (some accounts need an AWS Support case
before Bedrock will invoke any model at all — see README).

Exposes the same converse(messages, system_prompt, tools) interface and the
same Bedrock-Converse-shaped {stopReason, output} return value as
bedrock_client.converse, translating to/from OpenAI's Chat Completions
message/tool-call format at this module's boundary. app.py, tools.py,
escalation.py, kb_retrieval.py etc. don't need to know or care which client
is wired up — only app.py's import line changes.

Trade-off versus the Bedrock path: no Bedrock Guardrails layer (AWS-native
content/PII filtering on the model call itself). The other three Story 3
defenses are unaffected: guardrails.py's heuristic pre-filter, the system
prompt, and every tool's schema (still no student-id parameter, still
strict — strict tool-use is in fact a first-class, native OpenAI Chat
Completions feature, not something stretched onto Bedrock).
"""
import json
import os
import urllib.request

import boto3

CHAT_MODEL = os.environ["MODEL_ID"]

_secrets = boto3.client("secretsmanager")
_secret_cache = None


def converse(messages: list[dict], system_prompt: str, tools: list[dict] | None = None) -> dict:
    body = {
        "model": CHAT_MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + _to_openai_messages(messages),
        "temperature": 0.15,
        "max_completion_tokens": 900,
        # gpt-5.6-sol is a reasoning model — /v1/chat/completions rejects
        # function tools outright unless reasoning is explicitly turned
        # off ("Function tools with reasoning_effort are not supported...
        # set reasoning_effort to 'none'"). Not just a workaround: this
        # app is single-turn lookup-and-narrate, not multi-step reasoning,
        # so "none" is also the semantically right setting here.
        "reasoning_effort": "none",
    }
    if tools:
        body["tools"] = _to_openai_tools(tools)

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {_openai_api_key()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())

    return _to_converse_response(payload)


def _openai_api_key() -> str:
    global _secret_cache
    if _secret_cache is None:
        raw = _secrets.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"]
        _secret_cache = json.loads(raw)["OPENAI_API_KEY"]
    return _secret_cache


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Bedrock Converse message blocks -> OpenAI Chat Completions messages."""
    out = []
    for m in messages:
        blocks = m["content"]
        tool_calls = [b["toolUse"] for b in blocks if "toolUse" in b]
        tool_results = [b["toolResult"] for b in blocks if "toolResult" in b]
        text = "".join(b.get("text", "") for b in blocks if "text" in b)

        if tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": tc["toolUseId"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])},
                        }
                        for tc in tool_calls
                    ],
                }
            )
        elif tool_results:
            for tr in tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr["toolUseId"],
                        "content": json.dumps(tr["content"][0]["json"]),
                    }
                )
        else:
            out.append({"role": m["role"], "content": text})
    return out


def _to_openai_tools(tool_specs: list[dict]) -> list[dict]:
    """Bedrock toolSpec entries -> OpenAI function-tool entries."""
    out = []
    for spec in tool_specs:
        ts = spec["toolSpec"]
        out.append(
            {
                "type": "function",
                "function": {
                    "name": ts["name"],
                    "description": ts["description"],
                    "parameters": ts["inputSchema"]["json"],
                    "strict": ts.get("strict", False),
                },
            }
        )
    return out


def _to_converse_response(payload: dict) -> dict:
    """OpenAI Chat Completions response -> Bedrock Converse-shaped dict."""
    choice = payload["choices"][0]
    message = choice["message"]

    content_blocks = []
    if message.get("content"):
        content_blocks.append({"text": message["content"]})
    for tc in message.get("tool_calls") or []:
        content_blocks.append(
            {
                "toolUse": {
                    "toolUseId": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"]["arguments"]),
                }
            }
        )

    stop_reason = "tool_use" if choice["finish_reason"] == "tool_calls" else "end_turn"
    return {
        "stopReason": stop_reason,
        "output": {"message": {"role": "assistant", "content": content_blocks}},
    }
