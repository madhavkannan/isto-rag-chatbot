"""
Deterministic, code-owned decisions layered on top of the tool result. The
model never self-reports a confidence or escalation field — these are hard
rules evaluated in Lambda after get_student_record returns, per the design
doc's "compliance decisions aren't the model's to make" principle.
"""
from dataclasses import dataclass


@dataclass
class Evaluation:
    escalate: bool
    escalation_reason: str | None
    work_hours_remaining: int
    course_load_meets_minimum: bool


def evaluate(record: dict) -> Evaluation:
    course_load_meets_minimum = record["course_load_credits"] >= record["min_required_credits"]
    work_hours_remaining = max(0, record["work_hour_cap_weekly"] - record["hours_logged_this_week"])

    escalate = False
    escalation_reason = None
    return_date = record.get("travel_return_date")
    if return_date:
        # Plain ISO-8601 strings (YYYY-MM-DD) sort correctly with a string
        # comparison, so no date parsing is needed.
        if record["endorsement_expiry"] < return_date:
            escalate = True
            escalation_reason = (
                f"Re-entry endorsement expired {record['endorsement_expiry']}, "
                f"before the student's planned return date {return_date}. "
                "Re-entry endorsements require a physical DSO-equivalent "
                "signature, which this assistant cannot issue."
            )

    return Evaluation(
        escalate=escalate,
        escalation_reason=escalation_reason,
        work_hours_remaining=work_hours_remaining,
        course_load_meets_minimum=course_load_meets_minimum,
    )
