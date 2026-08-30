"""
Tool definitions and executors for two independent flows, each split into
a read-only check and a confirm-after-agreement action:
  - Story 1 (travel): check_travel_eligibility / confirm_escalation.
  - Story 2 (course drop / Medical RCL): check_course_drop_impact /
    file_rcl_escalation.

Security boundary (Story 3): none of the schemas below take a student
identifier. The model cannot pass one in, strict-schema validation on the
Converse API rejects any attempt to add one, and the execute_* functions
below only ever accept the student_id that the Lambda handler already
resolved from the verified Cognito JWT — never from model output or from the
raw user message. There is no parameter, and no code path, through which a
different student's id could reach these functions.

Every tool requires all of its fields rather than treating any as
optional: with strict:true, every property must be listed as required
(strict mode has no true "optional" field — an unlisted-but-present
optional property is not a valid strict schema).
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
            "name": "check_course_drop_impact",
            "description": (
                "Check whether dropping a specific course from the "
                "authenticated student's own schedule would violate their "
                "minimum full-time credit or physical-presence credit "
                "requirement. Read-only — never files anything by itself, "
                "even if the drop would violate a minimum. If it would, "
                "returns real alternative courses from the student's own "
                "schedule data for you to suggest before any escalation."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "course_name": {
                            "type": "string",
                            "description": "The course the student wants to drop, as they referred to it.",
                        },
                    },
                    "required": ["course_name"],
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
    {
        "toolSpec": {
            "name": "file_rcl_escalation",
            "description": (
                "File an urgent Medical Reduced Course Load (RCL) "
                "escalation ticket with a human International Student "
                "Advisor, for a course drop that check_course_drop_impact "
                "already found would violate a minimum. Only call this "
                "after the student has explicitly agreed to escalate — "
                "never speculatively or immediately after "
                "check_course_drop_impact on its own. Re-checks the same "
                "course itself before filing, so it's safe even if called "
                "out of order."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "course_name": {
                            "type": "string",
                            "description": "The course being dropped, matching what was checked earlier.",
                        },
                    },
                    "required": ["course_name"],
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
    # only matter for the Story 1 travel tools; credits/alternative_courses
    # only matter for the Story 2 course-drop tools below — every course
    # carries all of them regardless, since a course can appear in either
    # narrative (e.g. User B's Organic Chemistry Lecture is both the Story 1
    # travel conflict and the Story 2 drop target).
    return {
        "name": c["name"],
        "delivery_mode": c.get("delivery_mode"),
        "meeting_days": list(c.get("meeting_days", [])),
        "session_dates": list(c.get("session_dates", [])),
        "remote_session_dates": list(c.get("remote_session_dates", [])),
        "credits": int(c.get("credits", 0)),
        "alternative_courses": [
            {
                "name": a["name"],
                "delivery_mode": a.get("delivery_mode"),
                "credits": int(a.get("credits", 0)),
            }
            for a in c.get("alternative_courses", [])
        ],
    }


def _find_course(record: dict, course_name: str) -> dict | None:
    # Simple case-insensitive/substring match rather than requiring an
    # exact string — the model passes whatever the student called the
    # course by, which won't always be a byte-for-byte match against the
    # SIS record's canonical name.
    target = course_name.strip().lower()
    for c in record.get("courses", []):
        name = c["name"].lower()
        if name == target or target in name or name in target:
            return c
    return None


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


def execute_check_course_drop_impact(student_id: str, tool_input: dict) -> dict:
    record = _fetch_record(student_id)
    course = _find_course(record, tool_input["course_name"])
    if course is None:
        return {
            "error": "course_not_found",
            "available_courses": [c["name"] for c in record.get("courses", [])],
        }
    return {
        "C_total": int(record["C_total"]),
        "C_inperson": int(record["C_inperson"]),
        "M": int(record["M"]),
        "course": _normalize_course(course),
    }


def execute_file_rcl_escalation(student_id: str, tool_input: dict) -> dict:
    # Same re-derive-from-scratch pattern as execute_confirm_escalation.
    return execute_check_course_drop_impact(student_id, tool_input)
