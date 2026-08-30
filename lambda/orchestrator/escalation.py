"""
Deterministic, code-owned decisions layered on top of tool results. The
model never self-reports a confidence or escalation field — these are hard
rules evaluated in Lambda after a tool call returns, per the design doc's
"compliance decisions aren't the model's to make" principle.

Story 1 (travel) evaluates three independent rules against real student
data, not a single flat "endorsement expiry" comparison:
  - Rule 1 (enrollment): standing full-time + physical-presence credit
    minimums — not trip-specific.
  - Rule 2 (attendance): day-by-day check of the requested trip against
    each course's actual delivery mode and session-level overrides.
  - Rule 3 (document): the re-entry signature's computed validity window
    against the trip's return date.
A trip can fail Rule 2 while Rule 3 is fine, or the reverse, or both at
once — they're reported separately, never collapsed into one flag.

Story 2 (course drop) reuses Rule 1's same two minimums to evaluate a
hypothetical: what would this student's standing look like if they
dropped one specific course? A health/hardship reason for rejecting the
suggested alternatives is inherently something no tool can verify — that
part stays a model judgment call — but whether the drop itself is
compliant, and what the real alternative courses are, is computed here,
never asserted by the model.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_SIGNATURE_VALIDITY_DAYS = {"OPT": 182}
_DEFAULT_SIGNATURE_VALIDITY_DAYS = 365
_WARNING_WINDOW_DAYS = 30
_DEADLINE_SEARCH_WINDOW_DAYS = 60


# ---------------------------------------------------------------------
# Rule 1 — standing enrollment compliance (not trip-specific)
# ---------------------------------------------------------------------
@dataclass
class EnrollmentStatus:
    total_ok: bool
    physical_presence_ok: bool

    @property
    def compliant(self) -> bool:
        return self.total_ok and self.physical_presence_ok


def evaluate_enrollment(record: dict) -> EnrollmentStatus:
    return EnrollmentStatus(
        total_ok=record["C_total"] >= record["M"],
        physical_presence_ok=record["C_inperson"] >= (record["M"] - 3),
    )


# ---------------------------------------------------------------------
# Rule 2 — day-by-day physical attendance check for a specific trip
# ---------------------------------------------------------------------
@dataclass
class DayResult:
    iso_date: str
    status: str  # "break" | "weekend" | "safe" | "conflict"
    label: str | None = None  # why it's safe (break name, remote session, ...)
    conflicts: list[str] = field(default_factory=list)  # course/assignment names, if status=="conflict"


def _date_in_break(d: date, academic_calendar: list[dict]) -> str | None:
    iso = d.isoformat()
    for b in academic_calendar:
        if b["start"] <= iso <= b["end"]:
            return b["label"]
    return None


def _evaluate_single_day(d: date, record: dict) -> DayResult:
    iso = d.isoformat()

    break_label = _date_in_break(d, record.get("academic_calendar", []))
    if break_label:
        return DayResult(iso, "break", label=break_label)
    if d.weekday() >= 5:
        return DayResult(iso, "safe", label="Weekend")

    weekday = _WEEKDAY_ABBR[d.weekday()]
    conflicts: list[str] = []
    remote_notes: list[str] = []

    for course in record.get("courses", []):
        session_dates = course.get("session_dates")
        meets_today = (iso in session_dates) if session_dates else (weekday in course.get("meeting_days", []))
        if not meets_today:
            continue

        mode = course["delivery_mode"]
        if mode == "online":
            continue  # never a physical-presence factor
        if mode == "in_person":
            conflicts.append(course["name"])
        elif mode == "hybrid":
            if iso in course.get("remote_session_dates", []):
                remote_notes.append(course["name"])
            else:
                conflicts.append(course["name"])

    for a in record.get("assignments", []):
        if a["date"] == iso and a.get("requires_physical_presence"):
            conflicts.append(a["name"])

    if conflicts:
        return DayResult(iso, "conflict", conflicts=conflicts)
    if remote_notes:
        return DayResult(iso, "safe", label=f"{remote_notes[0]} flagged remote")
    return DayResult(iso, "safe", label="No class scheduled")


def _date_range(start_iso: str, end_iso: str):
    d = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    while d <= end:
        yield d
        d += timedelta(days=1)


@dataclass
class AttendanceEvaluation:
    days: list[DayResult]
    compliant: bool
    conflict_dates: list[DayResult]
    recommended_return_date: str | None  # day before the first conflict, if any and still >= departure
    hard_deadline: str | None  # next required in-person day after a fully-compliant trip


def evaluate_attendance(record: dict, departure: str, return_date: str) -> AttendanceEvaluation:
    days = [_evaluate_single_day(d, record) for d in _date_range(departure, return_date)]
    conflict_days = [d for d in days if d.status == "conflict"]
    compliant = not conflict_days

    recommended_return_date = None
    if conflict_days:
        first_conflict = date.fromisoformat(conflict_days[0].iso_date)
        candidate = first_conflict - timedelta(days=1)
        if candidate >= date.fromisoformat(departure):
            recommended_return_date = candidate.isoformat()

    hard_deadline = None
    if compliant:
        cursor = date.fromisoformat(return_date) + timedelta(days=1)
        for _ in range(_DEADLINE_SEARCH_WINDOW_DAYS):
            if _evaluate_single_day(cursor, record).status == "conflict":
                hard_deadline = cursor.isoformat()
                break
            cursor += timedelta(days=1)

    return AttendanceEvaluation(
        days=days,
        compliant=compliant,
        conflict_dates=conflict_days,
        recommended_return_date=recommended_return_date,
        hard_deadline=hard_deadline,
    )


# ---------------------------------------------------------------------
# Rule 3 — re-entry signature validity for a specific trip
# ---------------------------------------------------------------------
@dataclass
class SignatureEvaluation:
    expiry: str
    status: str  # "ok" | "warning" | "expired"


def evaluate_signature(record: dict, return_date: str) -> SignatureEvaluation:
    signature_date = date.fromisoformat(record["travel_signature_date"])
    validity_days = _SIGNATURE_VALIDITY_DAYS.get(record.get("visa_status"), _DEFAULT_SIGNATURE_VALIDITY_DAYS)
    expiry = signature_date + timedelta(days=validity_days)
    days_to_expiry = (expiry - date.fromisoformat(return_date)).days

    if days_to_expiry < 0:
        status = "expired"
    elif days_to_expiry <= _WARNING_WINDOW_DAYS:
        status = "warning"
    else:
        status = "ok"

    return SignatureEvaluation(expiry=expiry.isoformat(), status=status)


# ---------------------------------------------------------------------
# Combined evaluation used by both check_travel_eligibility (read-only)
# and confirm_escalation (re-verifies before filing)
# ---------------------------------------------------------------------
@dataclass
class TravelEvaluation:
    enrollment: EnrollmentStatus
    attendance: AttendanceEvaluation
    signature: SignatureEvaluation

    @property
    def needs_escalation(self) -> bool:
        # Enrollment (Rule 1) isn't trip-specific and isn't what triggers an
        # escalation here — it's background context. Either an attendance
        # conflict or a non-valid signature is what actually requires ISTO.
        return (not self.attendance.compliant) or self.signature.status != "ok"


def evaluate_travel(record: dict, departure: str, return_date: str) -> TravelEvaluation:
    return TravelEvaluation(
        enrollment=evaluate_enrollment(record),
        attendance=evaluate_attendance(record, departure, return_date),
        signature=evaluate_signature(record, return_date),
    )


# ---------------------------------------------------------------------
# Story 2 — dropping a specific course against the same Rule 1 minimums
# ---------------------------------------------------------------------
@dataclass
class CourseDropEvaluation:
    course_name: str
    credits: int
    current_total: int
    current_inperson: int
    projected_total: int
    projected_inperson: int
    min_total: int
    min_inperson: int
    alternatives: list[dict]

    @property
    def meets_total_minimum(self) -> bool:
        return self.projected_total >= self.min_total

    @property
    def meets_physical_presence_minimum(self) -> bool:
        return self.projected_inperson >= self.min_inperson

    @property
    def compliant(self) -> bool:
        return self.meets_total_minimum and self.meets_physical_presence_minimum


def evaluate_course_drop(record: dict, course: dict) -> CourseDropEvaluation:
    credits = course["credits"]
    current_total = record["C_total"]
    current_inperson = record["C_inperson"]

    # Only in-person credits count against the physical-presence minimum —
    # dropping an online/hybrid course still reduces C_total, but leaves
    # C_inperson untouched.
    inperson_credits = credits if course["delivery_mode"] == "in_person" else 0

    return CourseDropEvaluation(
        course_name=course["name"],
        credits=credits,
        current_total=current_total,
        current_inperson=current_inperson,
        projected_total=current_total - credits,
        projected_inperson=current_inperson - inperson_credits,
        min_total=record["M"],
        min_inperson=record["M"] - 3,
        alternatives=course.get("alternative_courses", []),
    )
