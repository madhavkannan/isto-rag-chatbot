"""
Deterministic, code-owned decisions layered on top of tool results. The
model never self-reports a confidence or escalation field — these are hard
rules evaluated in Lambda after a tool call returns, per the design doc's
"compliance decisions aren't the model's to make" principle.
"""
from dataclasses import dataclass


@dataclass
class RecordSummary:
    course_hours_this_week: int
    work_hours_logged: int
    total_hours_used: int
    work_hours_remaining: int
    over_cap_by: int
    course_load_meets_minimum: bool


def summarize_record(record: dict) -> RecordSummary:
    # The weekly cap is on combined course contact hours + on-campus work
    # hours, not work hours alone — course_load_credits (an academic-credit
    # count, used elsewhere for the minimum-credit check) isn't a hours
    # figure, so it doesn't belong in this sum. "courses" carries each
    # course's actual contact hours *for this week* instead, since a
    # fortnightly class's hours aren't the same every week — using a flat
    # per-credit average would misreport compliance on an off week.
    course_hours = sum(c["hours_this_week"] for c in record.get("courses", []))
    work_hours = record["hours_logged_this_week"]
    total = course_hours + work_hours
    cap = record["work_hour_cap_weekly"]

    return RecordSummary(
        course_hours_this_week=course_hours,
        work_hours_logged=work_hours,
        total_hours_used=total,
        work_hours_remaining=max(0, cap - total),
        over_cap_by=max(0, total - cap),
        course_load_meets_minimum=record["course_load_credits"] >= record["min_required_credits"],
    )


@dataclass
class TravelEvaluation:
    escalate: bool
    escalation_reason: str | None
    course_load_meets_minimum: bool


def evaluate_travel(record: dict) -> TravelEvaluation:
    course_load_meets_minimum = record["course_load_credits"] >= record["min_required_credits"]

    # Plain ISO-8601 strings (YYYY-MM-DD) sort correctly with a string
    # comparison, so no date parsing is needed.
    escalate = record["endorsement_expiry"] < record["travel_return_date"]
    escalation_reason = None
    if escalate:
        escalation_reason = (
            f"Re-entry endorsement expired {record['endorsement_expiry']}, "
            f"before the student's planned return date {record['travel_return_date']}. "
            "Re-entry endorsements require a physical DSO-equivalent "
            "signature, which this assistant cannot issue."
        )

    return TravelEvaluation(
        escalate=escalate,
        escalation_reason=escalation_reason,
        course_load_meets_minimum=course_load_meets_minimum,
    )
