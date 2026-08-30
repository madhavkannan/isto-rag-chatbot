"""
Combined guardrail + orchestrator Lambda for the ISTO Personalized Guidance
demo. FALLBACK BRANCH: calls OpenAI's Platform API directly (openai_client.py)
instead of through Bedrock bedrock-runtime — see openai_client.py's
docstring for why and what's traded off (no Bedrock Guardrails layer). One
function, several logical stages (see README for the stage breakdown if
asked live):

  1. Resolve the caller's identity from the verified Cognito JWT (never from
     the request body).
  2. Deterministic injection heuristic (guardrails.py) — fast refusal path.
  3. Retrieve policy chunks (kb_retrieval.py) — keyword-matched, no vector
     KB on this branch (demo modification, see kb_retrieval.py).
  4. Call the model (openai_client.py) with the Story 1 (travel) and
     Story 2 (course drop / Medical RCL) tools available.
  5. If the model calls a tool, execute it scoped to the authenticated
     caller only (tools.py), evaluate deterministic escalation rules
     (escalation.py), and call the model again for the final answer.
  6. Log the exchange (CloudWatch) and return {reply, escalated, visual}.

`visual` carries the structured numbers behind whichever tool the model
called this turn (trip/endorsement dates, or course-drop credit impact) so
the frontend can render a real chart from actual data instead of parsing
them back out of the model's prose. It's None when no tool was called this
turn (e.g. the model is still asking for travel dates).
"""
import json
import logging
from datetime import datetime, timezone

import escalation
import kb_retrieval
import openai_client
from guardrails import REFUSAL_MESSAGE, looks_like_injection
from prompts import build_system_prompt
from tools import (
    TOOL_SPECS,
    execute_check_course_drop_impact,
    execute_check_travel_eligibility,
    execute_confirm_escalation,
    execute_file_rcl_escalation,
    execute_list_my_courses,
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

    response = openai_client.converse(messages, system_prompt, tools=TOOL_SPECS)
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
        response = openai_client.converse(messages, system_prompt, tools=TOOL_SPECS)

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


def _trip_visual(
    departure: str, return_date: str, evaluation: "escalation.TravelEvaluation", courses: list[dict]
) -> dict:
    return {
        "type": "trip_attendance",
        "departure": departure,
        "return": return_date,
        # Shown above the calendar so it's clear at a glance which courses
        # can even factor into a physical-presence conflict — online
        # courses never can, hybrid ones only on their non-remote days.
        "courseModes": [{"name": c["name"], "deliveryMode": c["delivery_mode"]} for c in courses],
        "days": _days_as_dicts(evaluation.attendance.days),
        "compliant": evaluation.attendance.compliant,
        "recommendedReturn": evaluation.attendance.recommended_return_date,
        "hardDeadline": evaluation.attendance.hard_deadline,
        "signature": {"status": evaluation.signature.status, "expiry": evaluation.signature.expiry},
    }


def _course_drop_visual(evaluation: "escalation.CourseDropEvaluation") -> dict:
    return {
        "type": "course_drop",
        "courseName": evaluation.course_name,
        "credits": evaluation.credits,
        "total": {
            "current": evaluation.current_total,
            "projected": evaluation.projected_total,
            "min": evaluation.min_total,
            "meetsMinimum": evaluation.meets_total_minimum,
        },
        "inPerson": {
            "current": evaluation.current_inperson,
            "projected": evaluation.projected_inperson,
            "min": evaluation.min_inperson,
            "meetsMinimum": evaluation.meets_physical_presence_minimum,
        },
        "compliant": evaluation.compliant,
        "alternatives": evaluation.alternatives,
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
                "Attendance is fully compliant. Keep this brief — the "
                "calendar visual shown alongside your reply already gives "
                "the day-by-day breakdown (breaks, weekends, remote-"
                "flagged sessions), so do NOT re-list each day's status "
                "yourself. Just state in a line or two what you checked "
                "this trip against (the physical-presence/attendance "
                "requirement and the re-entry signature's validity), "
                "citing the relevant policy excerpt, then close with one "
                "line confirming the trip works, mentioning the "
                "hard_deadline date if it's set. Never open with that "
                "verdict."
            )
        else:
            instructions.append(
                "Attendance is NOT compliant — do not escalate yet and do "
                "not call confirm_escalation on your own initiative. Keep "
                "this brief — the calendar visual shown alongside your "
                "reply already gives the day-by-day breakdown and lists "
                "the specific conflicting dates/courses, so do NOT re-list "
                "each day or every conflict yourself. Just state in a line "
                "or two what you checked this trip against (the physical-"
                "presence/attendance requirement and the re-entry "
                "signature's validity), citing the relevant policy "
                "excerpt, then close with one line stating the trip does "
                "not work as requested. Never open with that verdict."
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
                    "Both problems need ISTO, but as ONE case, not two. "
                    "Present the choice as two separate, clearly labeled "
                    "bullets — '- **Option A:** ...' and '- **Option B:** "
                    "...' — never as a single run-on sentence. Option A is "
                    "the recommended compliant dates (case is just a "
                    "signature renewal). Option B is their original "
                    "requested dates (same case, but it also requests an "
                    "attendance exception — make clear that's ISTO's call, "
                    "not guaranteed). Wait for their choice, then call "
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

        return tool_result, False, _trip_visual(departure, return_date, evaluation, record["courses"])

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
            return tool_result, True, _trip_visual(departure, return_date, evaluation, record["courses"])

        # Re-check came back clean (e.g. the dates changed since the student
        # first asked) — nothing to file after all.
        tool_result["instruction"] = (
            "On re-check, this trip is fully compliant and the signature is "
            "valid — let the student know no case is needed after all."
        )
        return tool_result, False, _trip_visual(departure, return_date, evaluation, record["courses"])

    if name == "list_my_courses":
        record = execute_list_my_courses(student_id, tool_input)
        tool_result = {
            "courses": record["courses"],
            "instruction": (
                "Do NOT list the courses yourself — a selectable list is "
                "shown alongside your reply, so re-listing them in prose "
                "would just be duplicated. Just briefly ask which course "
                "they'd like to drop. Do not call check_course_drop_impact "
                "until they name one."
            ),
        }
        return tool_result, False, {"type": "course_list", "courses": record["courses"]}

    if name == "check_course_drop_impact":
        record = execute_check_course_drop_impact(student_id, tool_input)
        if record.get("error") == "course_not_found":
            return (
                {
                    "error": "course_not_found",
                    "available_courses": record["available_courses"],
                    "instruction": (
                        "That course name didn't match anything on the "
                        "student's schedule. List their actual course "
                        "names from `available_courses` and ask which one "
                        "they mean."
                    ),
                },
                False,
                None,
            )

        evaluation = escalation.evaluate_course_drop(record, record["course"])
        tool_result = {
            "course_name": evaluation.course_name,
            "credits": evaluation.credits,
            "current_total_credits": evaluation.current_total,
            "current_inperson_credits": evaluation.current_inperson,
            "projected_total_credits": evaluation.projected_total,
            "projected_inperson_credits": evaluation.projected_inperson,
            "min_total_credits": evaluation.min_total,
            "min_inperson_credits": evaluation.min_inperson,
            "meets_total_minimum": evaluation.meets_total_minimum,
            "meets_physical_presence_minimum": evaluation.meets_physical_presence_minimum,
            "alternative_courses": evaluation.alternatives,
        }

        if evaluation.compliant:
            tool_result["instruction"] = (
                "Lead with the relevant policy facts as bullets (the "
                "minimum total credit requirement and the physical-"
                "presence minimum), then the specific before/after numbers "
                "for both counts as bullets. Only after that, close with "
                "one line confirming the drop is fine — never open with "
                "that verdict. No Reduced Course Load (RCL) or escalation "
                "is needed here."
            )
        else:
            tool_result["instruction"] = (
                "Lead with the relevant policy facts as bullets (the "
                "minimum total credit requirement and the physical-"
                "presence minimum), then the specific before/after numbers "
                "for both counts as bullets — call out which one(s) fail. "
                "Only after that, close with one line stating this drop "
                "is not compliant on its own. If `alternative_courses` is "
                "non-empty, name them as options the student could swap "
                "into instead, and ask if they'd like to. Do not mention "
                "Reduced Course Load (RCL) or file_rcl_escalation yet "
                "unless the student says they can't take any of the "
                "alternatives — wait for that before explaining the RCL "
                "path."
            )

        return tool_result, False, _course_drop_visual(evaluation)

    if name == "file_rcl_escalation":
        record = execute_file_rcl_escalation(student_id, tool_input)
        if record.get("error") == "course_not_found":
            return (
                {
                    "error": "course_not_found",
                    "available_courses": record["available_courses"],
                    "instruction": (
                        "That course name didn't match anything on the "
                        "student's schedule. List their actual course "
                        "names from `available_courses` and ask which one "
                        "they mean."
                    ),
                },
                False,
                None,
            )

        evaluation = escalation.evaluate_course_drop(record, record["course"])
        ticket = {
            "ticket_type": "URGENT_RCL_ESCALATION",
            "routing_queue": "Immigration_Medical_Exemptions",
            "student_id": student_id,
            "risk_level": "CRITICAL_STATUS_VIOLATION_PENDING",
            "course_name": evaluation.course_name,
            "context_summary": (
                f"Student attempted to drop {evaluation.course_name}, "
                f"which would bring total credits to "
                f"{evaluation.projected_total} (minimum "
                f"{evaluation.min_total}) and in-person credits to "
                f"{evaluation.projected_inperson} (minimum "
                f"{evaluation.min_inperson}). Student rejected course "
                "substitutions citing severe health issues. Escalated to "
                "initiate the Medical RCL workflow."
            ),
        }

        tool_result = {
            **ticket,
            "instruction": (
                "This is now filed with a human International Student "
                "Advisor as an urgent Medical Reduced Course Load (RCL) "
                "case. Tell the student plainly that it's filed, that an "
                "advisor will review their profile and send a secure link "
                "to upload documentation, and that their visa status and "
                "work authorization stay fully protected while this is "
                "pending."
            ),
        }
        return tool_result, True, {"type": "rcl_ticket", **ticket}

    raise ValueError(f"unknown tool: {name}")


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
