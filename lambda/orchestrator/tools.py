"""
Tool definitions and executors: get_student_record (general lookup, no
params), check_travel_eligibility (Story 1, requires both travel dates,
read-only — never files an escalation itself), and confirm_escalation
(files the escalation, only after the student has explicitly agreed).

Security boundary (Story 3): neither schema below takes a student
identifier. The model cannot pass one in, strict-schema validation on the
Converse API rejects any attempt to add one, and the execute_* functions
below only ever accept the student_id that the Lambda handler already
resolved from the verified Cognito JWT — never from model output or from the
raw user message. There is no parameter, and no code path, through which a
different student's id could reach these functions.

check_travel_eligibility requires both travel dates rather than taking them
as optional fields on get_student_record: with strict:true, every property
must be listed as required (strict mode has no true "optional" field — an
unlisted-but-present optional property is not a valid strict schema), and a
travel-specific tool the model literally cannot invoke before it has both
dates is a real structural guarantee, not just a system-prompt request to
wait for them. Splitting it out also keeps get_student_record usable
unmodified for Story 2 (work hours), which has no travel dates at all.
"""
import boto3
import os

TABLE_NAME = os.environ["TABLE_NAME"]
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(TABLE_NAME)

_DATE_FIELD = {
    "type": "string",
    "pattern": r"^\d{4}-\d{2}-\d{2}$",
    "description": "ISO-8601 date (YYYY-MM-DD).",
}

TOOL_SPECS = [
    {
        "toolSpec": {
            "name": "get_student_record",
            "description": (
                "Fetch the authenticated student's own SIS record: total "
                "enrolled credits, the minimum full-time credit threshold, "
                "this week's course schedule (name and contact hours per "
                "course), the combined weekly hour cap, and work hours "
                "already logged this week. Always scoped to the caller — "
                "it cannot be used to look up any other student. Use this "
                "for anything that isn't a travel/endorsement question "
                "(e.g. work-hour headroom, which depends on both course "
                "hours and hours already worked); for travel questions use "
                "check_travel_eligibility instead once you have both dates."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            },
            "strict": True,
        }
    },
    {
        "toolSpec": {
            "name": "check_travel_eligibility",
            "description": (
                "Check whether the authenticated student's re-entry "
                "endorsement covers a specific trip. Requires both the "
                "planned departure and return dates — ask the student for "
                "both conversationally before calling this; the tool "
                "cannot be called with only one date or none. Read-only: "
                "this never files an escalation by itself, even if the "
                "trip isn't covered — use confirm_escalation for that, and "
                "only after the student has explicitly agreed."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "travel_departure_date": _DATE_FIELD,
                        "travel_return_date": _DATE_FIELD,
                    },
                    "required": ["travel_departure_date", "travel_return_date"],
                    "additionalProperties": False,
                }
            },
            "strict": True,
        }
    },
    {
        "toolSpec": {
            "name": "confirm_escalation",
            "description": (
                "Actually file the ISTO escalation for a travel/endorsement "
                "gap that check_travel_eligibility already surfaced. Only "
                "call this after the student has clearly said they want it "
                "escalated — never immediately after check_travel_eligibility "
                "on its own. Re-checks the same dates itself before filing, "
                "so it's safe even if called speculatively."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "travel_departure_date": _DATE_FIELD,
                        "travel_return_date": _DATE_FIELD,
                    },
                    "required": ["travel_departure_date", "travel_return_date"],
                    "additionalProperties": False,
                }
            },
            "strict": True,
        }
    },
]


def _fetch_record(student_id: str) -> dict:
    resp = _table.get_item(Key={"student_id": student_id})
    record = resp.get("Item")
    if record is None:
        raise ValueError(f"No student record found for authenticated caller '{student_id}'")
    return record


def _normalize_course(c: dict) -> dict:
    # DynamoDB numbers come back as Decimal; cast to int for JSON. Schedule
    # fields (delivery_mode/meeting_days/session_dates/remote_session_dates)
    # only matter for the Story 1 travel tools below — Story 2 (work hours)
    # only ever reads name/hours_this_week and ignores the rest.
    return {
        "name": c["name"],
        "hours_this_week": int(c["hours_this_week"]),
        "delivery_mode": c.get("delivery_mode"),
        "meeting_days": list(c.get("meeting_days", [])),
        "session_dates": list(c.get("session_dates", [])),
        "remote_session_dates": list(c.get("remote_session_dates", [])),
    }


def execute_get_student_record(student_id: str, _tool_input: dict) -> dict:
    record = _fetch_record(student_id)
    return {
        "C_total": int(record["C_total"]),
        "M": int(record["M"]),
        "work_hour_cap_weekly": int(record["work_hour_cap_weekly"]),
        "hours_logged_this_week": int(record["hours_logged_this_week"]),
        "courses": [_normalize_course(c) for c in record.get("courses", [])],
    }


def execute_check_travel_eligibility(student_id: str, tool_input: dict) -> dict:
    record = _fetch_record(student_id)
    return {
        "C_total": int(record["C_total"]),
        "C_inperson": int(record["C_inperson"]),
        "C_online": int(record["C_online"]),
        "M": int(record["M"]),
        "travel_signature_date": record["travel_signature_date"],
        "visa_status": record.get("visa_status", "F1"),
        "courses": [_normalize_course(c) for c in record.get("courses", [])],
        "assignments": list(record.get("assignments", [])),
        "academic_calendar": [dict(b) for b in record.get("academic_calendar", [])],
        # Guaranteed present: the schema's "required" makes this the only
        # way the model can reach this function at all.
        "travel_departure_date": tool_input["travel_departure_date"],
        "travel_return_date": tool_input["travel_return_date"],
    }


def execute_confirm_escalation(student_id: str, tool_input: dict) -> dict:
    # Same lookup as check_travel_eligibility — confirm_escalation
    # re-derives the record and re-evaluates from scratch rather than
    # trusting that whatever the model saw earlier in the conversation is
    # still accurate, so it's safe to call even if a confirmation arrives
    # out of order, or with different (e.g. shortened) dates than the
    # original check.
    return execute_check_travel_eligibility(student_id, tool_input)
