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
  6. Log the exchange (CloudWatch) and return {reply, escalated, visual}.

`visual` carries the structured numbers behind whichever tool the model
called this turn (hours cap/logged/remaining, or trip/endorsement dates) so
the frontend can render a real meter/timeline from actual data instead of
parsing them back out of the model's prose. It's None when no tool was
called this turn (e.g. the model is still asking for travel dates).
"""
import json
import logging
from datetime import datetime, timezone

import bedrock_client
import escalation
import kb_retrieval
from guardrails import REFUSAL_MESSAGE, looks_like_injection
from prompts import build_system_prompt
from tools import (
    TOOL_SPECS,
    execute_check_travel_eligibility,
    execute_confirm_escalation,
    execute_get_student_record,
)

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
            return _response(200, {"reply": REFUSAL_MESSAGE, "escalated": False, "visual": None})

        reply, escalated, visual = _run_conversation(student_id, message, history)

        logger.info(json.dumps({"event": "chat_response", "student_id": student_id, "escalated": escalated}))
        return _response(200, {"reply": reply, "escalated": escalated, "visual": visual})

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


def _run_conversation(student_id: str, message: str, history: list[dict]) -> tuple[str, bool, dict | None]:
    policy_chunks = kb_retrieval.search_policy_chunks(message)
    system_prompt = build_system_prompt(policy_chunks, _today_iso())

    messages = list(history) + [{"role": "user", "content": [{"text": message}]}]

    response = bedrock_client.converse(messages, system_prompt, tools=TOOL_SPECS)
    escalated = False
    visual = None

    # Tool-calling loop — bounded. Claude's parallel tool use is on by
    # default, so a single turn can carry more than one toolUse block (e.g.
    # "use these dates AND file the case" is enough for the model to want
    # to re-check and confirm in one breath) — every toolUse in the
    # message needs a matching toolResult in the very next message, or the
    # next request is rejected outright.
    for _ in range(3):
        stop_reason = response["stopReason"]
        assistant_message = response["output"]["message"]
        messages.append(assistant_message)

        if stop_reason != "tool_use":
            break

        tool_uses = [b["toolUse"] for b in assistant_message["content"] if "toolUse" in b]
        result_blocks = []
        for tool_use in tool_uses:
            tool_result_content, tool_escalated, tool_visual = _execute_tool(
                student_id, tool_use["name"], tool_use["input"]
            )
            if tool_escalated:
                escalated = True
            visual = tool_visual  # if more than one, the last (most final) tool's visual wins
            result_blocks.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use["toolUseId"],
                        "content": [{"json": tool_result_content}],
                    }
                }
            )

        messages.append({"role": "user", "content": result_blocks})
        response = bedrock_client.converse(messages, system_prompt, tools=TOOL_SPECS)

    final_text = "".join(b.get("text", "") for b in response["output"]["message"]["content"])
    return final_text, escalated, visual


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _expiry_status_phrase(expiry: str, today: str) -> str:
    # Computed here rather than left for the model to infer from raw dates —
    # asking it to reliably reason "is 2026-01-15 before or after today" and
    # pick the right tense turned out not to be reliable in practice, so the
    # answer is just handed over ready-made instead.
    return f"already expired on {expiry}" if expiry < today else f"will expire on {expiry}"


def _days_as_dicts(days: list) -> list[dict]:
    return [{"date": d.iso_date, "status": d.status, "label": d.label, "conflicts": d.conflicts} for d in days]


def _trip_visual(departure: str, return_date: str, evaluation: "escalation.TravelEvaluation") -> dict:
    return {
        "type": "trip_attendance",
        "departure": departure,
        "return": return_date,
        "days": _days_as_dicts(evaluation.attendance.days),
        "compliant": evaluation.attendance.compliant,
        "recommendedReturn": evaluation.attendance.recommended_return_date,
        "hardDeadline": evaluation.attendance.hard_deadline,
        "signature": {"status": evaluation.signature.status, "expiry": evaluation.signature.expiry},
    }


def _execute_tool(student_id: str, name: str, tool_input: dict) -> tuple[dict, bool, dict]:
    """
    Runs one tool call and folds in its deterministic evaluation. Returns
    (tool_result_for_model, escalate, visual):
      - tool_result_for_model: what's sent back to the model as the tool
        result, including an "instruction" telling it what to do next. The
        escalation decision itself is made here in code, not reported by or
        delegated to the model.
      - escalate: the Lambda's own decision, never sent to the model as-is.
        check_travel_eligibility NEVER returns True here even when the trip
        isn't covered — it only flags that confirmation is needed. Only
        confirm_escalation, called after the student has explicitly agreed,
        can set this True.
      - visual: the structured numbers behind this tool call, for the
        frontend to render a real chart from — not something the model sees
        or influences.
    """
    if name == "get_student_record":
        record = execute_get_student_record(student_id, tool_input)
        summary = escalation.summarize_record(record)
        tool_result = {
            **record,
            "course_hours_this_week": summary.course_hours_this_week,
            "total_hours_used_this_week": summary.total_hours_used,
            "work_hours_remaining": summary.work_hours_remaining,
            "over_cap_by": summary.over_cap_by,
            "course_load_meets_minimum": summary.course_load_meets_minimum,
            "instruction": (
                "Break the answer into a short bulleted list: each course "
                "and its hours this week, then hours already worked, then "
                "the total against the cap. Use the precomputed totals "
                "above rather than adding the numbers yourself. If "
                "over_cap_by is greater than 0, say explicitly how many "
                "hours over the cap the student already is — don't just "
                "say 'at your limit', since they're actually past it."
            ),
        }
        visual = {
            "type": "work_hours",
            "cap": record["work_hour_cap_weekly"],
            "courses": record["courses"],
            "workHours": summary.work_hours_logged,
            "courseHours": summary.course_hours_this_week,
            "total": summary.total_hours_used,
            "remaining": summary.work_hours_remaining,
            "overBy": summary.over_cap_by,
        }
        return tool_result, False, visual

    if name == "check_travel_eligibility":
        record = execute_check_travel_eligibility(student_id, tool_input)
        departure = tool_input["travel_departure_date"]
        return_date = tool_input["travel_return_date"]
        evaluation = escalation.evaluate_travel(record, departure, return_date)
        today = _today_iso()
        signature_phrase = _expiry_status_phrase(evaluation.signature.expiry, today)

        tool_result = {
            "travel_departure_date": departure,
            "travel_return_date": return_date,
            "days": _days_as_dicts(evaluation.attendance.days),
            "attendance_compliant": evaluation.attendance.compliant,
            "recommended_return_date": evaluation.attendance.recommended_return_date,
            "hard_deadline": evaluation.attendance.hard_deadline,
            "signature_status": evaluation.signature.status,
            "signature_expiry": evaluation.signature.expiry,
            "signature_status_phrase": signature_phrase,
            "enrollment_compliant": evaluation.enrollment.compliant,
        }

        instructions = []
        if evaluation.attendance.compliant:
            instructions.append(
                "Attendance is fully compliant. Lead with a bulleted list of "
                "the relevant facts using the break/remote-session reasons in "
                "`days` (entries with status 'break' or a 'label' explaining "
                "why a day is safe), including the hard_deadline date as one "
                "of the bullets if it's set. Only after the bullets, close "
                "with one line confirming the trip works — never open with "
                "that verdict."
            )
        else:
            instructions.append(
                "Attendance is NOT compliant — do not escalate yet and do not "
                "call confirm_escalation on your own initiative. Lead with a "
                "bulleted list of the relevant facts from `days`: the "
                "specific conflicting dates and course/assignment names "
                "(status 'conflict'), plus any days that are safe because of "
                "a flagged remote session, the same way you would for a "
                "compliant trip. Only after the bullets, close with one line "
                "stating the trip does not work as requested — never open "
                "with that verdict."
            )
            if evaluation.attendance.recommended_return_date:
                instructions.append(
                    f"Mention the recommended compliant alternative: same "
                    f"departure date, return by "
                    f"{evaluation.attendance.recommended_return_date} instead."
                )

        if evaluation.signature.status != "ok":
            instructions.append(
                f"Re-entry signature status: {signature_phrase}. This needs an "
                "ISTO case regardless of which return date the student "
                "ultimately picks — say so explicitly, separately from the "
                "attendance question."
            )

        if evaluation.needs_escalation:
            if not evaluation.attendance.compliant and evaluation.signature.status != "ok":
                instructions.append(
                    "Both problems need ISTO, but as ONE case, not two. Ask the "
                    "student to choose: (a) take the recommended compliant "
                    "dates, so the case is just a signature renewal, or (b) "
                    "keep their original requested dates, so the same case "
                    "must also request an exception for the attendance "
                    "conflict — make clear that exception is ISTO's call, not "
                    "guaranteed. Wait for their choice, then call "
                    "confirm_escalation with whichever dates they settle on."
                )
            else:
                instructions.append(
                    "Ask directly whether they'd like this filed with ISTO. "
                    "Only call confirm_escalation after they clearly say yes, "
                    "using whichever dates they confirm."
                )
        if not evaluation.enrollment.compliant:
            instructions.append(
                "Separately, enrollment_compliant is false — flag this as a "
                "standing compliance issue independent of this trip."
            )
        tool_result["instruction"] = " ".join(instructions)

        return tool_result, False, _trip_visual(departure, return_date, evaluation)

    if name == "confirm_escalation":
        record = execute_confirm_escalation(student_id, tool_input)
        departure = tool_input["travel_departure_date"]
        return_date = tool_input["travel_return_date"]
        evaluation = escalation.evaluate_travel(record, departure, return_date)
        today = _today_iso()
        signature_phrase = _expiry_status_phrase(evaluation.signature.expiry, today)

        tool_result = {
            "travel_departure_date": departure,
            "travel_return_date": return_date,
            "days": _days_as_dicts(evaluation.attendance.days),
            "attendance_compliant": evaluation.attendance.compliant,
            "signature_status": evaluation.signature.status,
            "signature_expiry": evaluation.signature.expiry,
            "signature_status_phrase": signature_phrase,
        }

        if evaluation.needs_escalation:
            reasons = []
            if evaluation.signature.status != "ok":
                reasons.append(f"re-entry signature ({signature_phrase})")
            if not evaluation.attendance.compliant:
                conflicts = "; ".join(
                    f"{d.iso_date} ({', '.join(d.conflicts)})" for d in evaluation.attendance.conflict_dates
                )
                reasons.append(f"attendance exception needed for: {conflicts}")
            tool_result["instruction"] = (
                "This case is now filed with ISTO — ONE case covering: "
                + "; and ".join(reasons)
                + ". Tell the student plainly, then draft a short case summary "
                "addressed to an ISTO advisor covering every reason listed "
                "above. If an attendance exception is part of it, be clear "
                "that ISTO decides whether to grant it, not this assistant."
            )
            return tool_result, True, _trip_visual(departure, return_date, evaluation)

        # Re-check came back clean (e.g. the dates changed since the student
        # first asked) — nothing to file after all.
        tool_result["instruction"] = (
            "On re-check, this trip is fully compliant and the signature is "
            "valid — let the student know no case is needed after all."
        )
        return tool_result, False, _trip_visual(departure, return_date, evaluation)

    raise ValueError(f"unknown tool: {name}")


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
