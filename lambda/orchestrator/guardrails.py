"""
Layer 1 of the Story 3 defense: a deterministic pre-filter for obvious
prompt-injection / impersonation attempts, independent of model behavior.

This is intentionally a blunt heuristic, not the security boundary — it just
lets the demo show an instant, deterministic refusal instead of depending on
the model to behave correctly on any given take. Bedrock Guardrails (attached
to the Converse call itself) is layer 2. The system prompt is layer 3. The
tool schema having no student-id parameter (see tools.py) is layer 4 and the
only one that is actually load-bearing: even if layers 1-3 all failed, there
is no code path that lets this session read another student's record.
"""
import re

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"disregard (all |any )?(previous|prior|above) instructions",
    r"you are now",
    r"assume (i am|i'm|you are)",
    r"pretend (to be|you are|i am)",
    r"act as (a different|another) student",
    r"reveal your (system prompt|instructions)",
    r"tell me (about )?(student|user) [a-z0-9_-]+('s)?",
    r"another student('s)?",
    r"someone else('s)?",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

REFUSAL_MESSAGE = (
    "I can only help with your own student information — I'm not able to "
    "look up or discuss another student's record."
)


def looks_like_injection(message: str) -> bool:
    return any(p.search(message) for p in _COMPILED)
