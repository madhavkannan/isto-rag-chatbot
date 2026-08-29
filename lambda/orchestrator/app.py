"""
Combined guardrail + orchestrator Lambda for the ISTO Personalized Guidance
demo. One function, several logical stages (see README for the stage
breakdown if asked live):

  1. Resolve the caller's identity from the verified Cognito JWT (never from
     the request body).
  2. Deterministic injection heuristic (guardrails.py) — fast refusal path.
  3. RAG: embed the message, retrieve policy chunks (kb_retrieval.py).
  4. Call the model via Bedrock Converse (bedrock_client.py), with Bedrock
     Guardrails attached and the get_student_record / check_travel_eligibility
     tools available.
  5. If the model calls a tool, execute it scoped to the authenticated
     caller only (tools.py), evaluate deterministic escalation rules
     (escalation.py), and call the model again for the final answer.
  6. Log the exchange (CloudWatch) and return {reply, escalated}.
"""
import json
import logging

import bedrock_client
import escalation
import kb_retrieval
from guardrails import REFUSAL_MESSAGE, looks_like_injection
from prompts import build_system_prompt
from tools import TOOL_SPECS, execute_check_travel_eligibility, execute_get_student_record

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    try:
        student_id = _resolve_student_id(event)
        body = json.loads(event.get("body") or "{}")
        message = (body.get("message") or "").strip()
        history = body.get("conversation_history") or []

        if not message:
            return _response(400, {"error": "message is required"})

        logger.info(json.dumps({"event": "chat_request", "student_id": student_id, "message": message}))

        if looks_like_injection(message):
            logger.warning(json.dumps({"event": "injection_heuristic_triggered", "student_id": student_id, "message": message}))
            return _response(200, {"reply": REFUSAL_MESSAGE, "escalated": False})

        reply, escalated = _run_conversation(student_id, message, history)

        logger.info(json.dumps({"event": "chat_response", "student_id": student_id, "escalated": escalated}))
        return _response(200, {"reply": reply, "escalated": escalated})

    except PermissionError as e:
        logger.warning(json.dumps({"event": "auth_error", "error": str(e)}))
        return _response(401, {"error": "unauthorized"})
    except Exception:
        logger.exception("unhandled error in orchestrator")
        return _response(500, {"error": "internal error"})


def _resolve_student_id(event) -> str:
    """
    The ONLY place a student id is ever established. Sourced from the
    Cognito-JWT claims that API Gateway's authorizer already verified —
    never from the request body or from anything the model outputs. This is
    the Story 3 structural boundary: neither tool (tools.py) accepts an id
    parameter at all, so whatever this function returns is the only
    identity the rest of the request can ever act on.
    """
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        return claims["cognito:username"]
    except (KeyError, TypeError) as e:
        raise PermissionError("missing or invalid JWT claims") from e


def _run_conversation(student_id: str, message: str, history: list[dict]) -> tuple[str, bool]:
    policy_chunks = kb_retrieval.search_policy_chunks(message)
    system_prompt = build_system_prompt(policy_chunks)

    messages = list(history) + [{"role": "user", "content": [{"text": message}]}]

    response = bedrock_client.converse(messages, system_prompt, tools=TOOL_SPECS)
    escalated = False

    # Tool-calling loop — bounded, since a single well-scoped tool can only
    # meaningfully be called once per turn in this demo.
    for _ in range(3):
        stop_reason = response["stopReason"]
        assistant_message = response["output"]["message"]
        messages.append(assistant_message)

        if stop_reason != "tool_use":
            break

        tool_use = next(b["toolUse"] for b in assistant_message["content"] if "toolUse" in b)
        tool_result_content = _execute_tool(student_id, tool_use["name"], tool_use["input"])
        if tool_result_content.pop("_escalate", False):
            escalated = True
            tool_result_content["escalation_instruction"] = (
                "This case MUST be escalated to ISTO — tell the student that "
                "plainly, then include a short case summary addressed to an "
                "ISTO advisor covering: the expired endorsement date, the "
                "planned return date, and that course load meets the "
                "minimum so re-issuance is likely (ISTO's job here is "
                "confirmation, not investigation)."
            )

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": tool_use["toolUseId"],
                            "content": [{"json": tool_result_content}],
                        }
                    }
                ],
            }
        )

        response = bedrock_client.converse(messages, system_prompt, tools=TOOL_SPECS)

    final_text = "".join(b.get("text", "") for b in response["output"]["message"]["content"])
    return final_text, escalated


def _execute_tool(student_id: str, name: str, tool_input: dict) -> dict:
    """
    Runs one tool call and folds in its deterministic evaluation. Returns the
    dict to send back to the model as the tool result, plus an internal
    "_escalate" key (popped by the caller, never sent to the model as a
    field for it to act on) — the escalation decision itself is made here in
    code, not reported by or delegated to the model.
    """
    if name == "get_student_record":
        record = execute_get_student_record(student_id, tool_input)
        summary = escalation.summarize_record(record)
        return {
            **record,
            "work_hours_remaining": summary.work_hours_remaining,
            "course_load_meets_minimum": summary.course_load_meets_minimum,
        }

    if name == "check_travel_eligibility":
        record = execute_check_travel_eligibility(student_id, tool_input)
        evaluation = escalation.evaluate_travel(record)
        return {
            **record,
            "course_load_meets_minimum": evaluation.course_load_meets_minimum,
            "_escalate": evaluation.escalate,
        }

    raise ValueError(f"unknown tool: {name}")


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
