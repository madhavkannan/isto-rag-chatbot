"""
The get_student_record tool definition and its executor.

Security boundary (Story 3): the input schema below takes NO student
identifier. The model cannot pass one in, strict-schema validation on the
Converse API rejects any attempt to add one, and execute_get_student_record()
below only ever accepts the student_id that the Lambda handler already
resolved from the verified Cognito JWT — never from model output or from the
raw user message. There is no parameter, and no code path, through which a
different student's id could reach this function.

The two optional travel-date fields exist so the model can report the dates
a student gives it conversationally (Story 1) without touching identity —
they scope the deterministic date comparison in escalation.py, not the
DynamoDB lookup key.
"""
import boto3
import os

TABLE_NAME = os.environ["TABLE_NAME"]
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(TABLE_NAME)

TOOL_SPECS = [
    {
        "toolSpec": {
            "name": "get_student_record",
            "description": (
                "Fetch the authenticated student's own SIS record: course "
                "load, minimum required credits, re-entry endorsement "
                "expiry, weekly work-hour cap, and hours already logged "
                "this week. Always scoped to the caller — it cannot be "
                "used to look up any other student. If the student has "
                "given you travel departure/return dates, include them so "
                "endorsement validity can be checked against the trip."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "travel_departure_date": {
                            "type": "string",
                            "pattern": r"^\d{4}-\d{2}-\d{2}$",
                            "description": "ISO-8601 date (YYYY-MM-DD) the student plans to depart, if known.",
                        },
                        "travel_return_date": {
                            "type": "string",
                            "pattern": r"^\d{4}-\d{2}-\d{2}$",
                            "description": "ISO-8601 date (YYYY-MM-DD) the student plans to return, if known.",
                        },
                    },
                    "additionalProperties": False,
                }
            },
            # Strict tool-use schema validation (GPT-5.6 Sol model card,
            # available via Converse on bedrock-runtime even though full
            # structured-output response formatting is not for this model
            # on this endpoint). This is what actually enforces "no
            # student-id parameter" at the schema level.
            "strict": True,
        }
    }
]


def execute_get_student_record(student_id: str, tool_input: dict) -> dict:
    resp = _table.get_item(Key={"student_id": student_id})
    record = resp.get("Item")
    if record is None:
        raise ValueError(f"No student record found for authenticated caller '{student_id}'")

    return {
        "course_load_credits": int(record["course_load_credits"]),
        "min_required_credits": int(record["min_required_credits"]),
        "endorsement_expiry": record["endorsement_expiry"],
        "work_hour_cap_weekly": int(record["work_hour_cap_weekly"]),
        "hours_logged_this_week": int(record["hours_logged_this_week"]),
        "travel_departure_date": tool_input.get("travel_departure_date"),
        "travel_return_date": tool_input.get("travel_return_date"),
    }
