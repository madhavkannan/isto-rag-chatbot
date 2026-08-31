"""
Demo modification: no vector knowledge base on this branch — no
OpenSearch domain, no embeddings call for retrieval. Policy chunks are
keyword-matched from a small static list bundled directly in this Lambda.
This trades real semantic search for a much simpler, much faster-to-stand-up
deployment; it's a deliberate scope cut for the demo, not a stand-in for
what a production RAG design would look like.

Retrieval is a simple keyword-overlap match against the student's message
— good enough to route "can I travel" to the endorsement chunks, "drop a
class" to the course-load/RCL chunks, and "OPT vs CPT" to the practical-
training chunks below.

The OPT/CPT chunks back the one flow in this demo with no tool call at
all (see prompts.py) — general policy questions are answered straight
from retrieval, no deterministic Lambda verdict involved, unlike Story 1
(travel) and Story 2 (course drop), where a tool result always backs the
answer.
"""
import re

_POLICY_CHUNKS = [
    {
        "keywords": {"travel", "endorsement", "passport", "visa", "reentry", "trip", "abroad", "return", "departure", "leave", "holiday", "holidays"},
        "text": (
            "Re-Entry Endorsement Policy: Before traveling outside the country, "
            "an international student must have a valid passport, a valid visa "
            "for re-entry, and a current travel/re-entry endorsement signed by "
            "ISTO within the last 12 months. The endorsement is only valid for "
            "travel that occurs before its printed expiry date."
        ),
    },
    {
        "keywords": {"endorsement", "expired", "expire", "expiry", "reissue", "reissuance", "sign", "signature", "renew", "renewal"},
        "text": (
            "Endorsement Re-Issuance: If a student's endorsement will be "
            "expired at the time of their planned return, ISTO can issue a new "
            "one, but re-issuance requires an in-person or notarized physical "
            "signature from an ISTO advisor — it cannot be completed through "
            "self-service or an automated system. Re-issuance is only granted "
            "if the student is maintaining at least the minimum full-time "
            "course load at the time of the request."
        ),
    },
    {
        "keywords": {"course", "credit", "credits", "load", "enrolled", "enrollment", "fulltime", "status", "drop", "dropping", "withdraw", "withdrawing"},
        "text": (
            "Minimum Course Load Policy: To maintain valid immigration status, "
            "international students must be enrolled in at least 12 credit "
            "hours per semester (undergraduate) or the equivalent full-time "
            "load defined by their program, and at least 9 of those credits "
            "(undergraduate) must be in-person — no more than 3 credits of "
            "online coursework count toward the minimum. Falling below either "
            "threshold without prior ISTO authorization (e.g., an approved "
            "reduced course load) can jeopardize status and any pending "
            "endorsement requests."
        ),
    },
    {
        "keywords": {"drop", "dropping", "withdraw", "withdrawing", "medical", "health", "illness", "sick", "rcl", "reduced", "exemption", "advisor"},
        "text": (
            "Medical Reduced Course Load (RCL) Exemption: A student who needs "
            "to drop below the minimum full-time credit or physical-presence "
            "requirement for a documented medical or psychological condition "
            "may request an RCL exemption. This is the only way to drop below "
            "the minimum without triggering an automatic status violation, and "
            "it requires review and approval by a human International Student "
            "Advisor — it cannot be self-service or automated. Supporting "
            "documentation must come from a licensed physician or psychologist "
            "and confirm the condition prevents full-time enrollment for a "
            "specific, limited period."
        ),
    },
    {
        "keywords": {"opt", "optional", "practical", "training", "employment", "authorization", "graduate", "graduating", "graduation", "degree", "stem", "months", "work"},
        "text": (
            "Optional Practical Training (OPT): F-1 students may apply for "
            "OPT, temporary employment authorization directly related to "
            "their field of study. Standard OPT provides up to 12 months of "
            "authorization total per degree level, usable before or after "
            "completing the program — any pre-completion OPT used counts "
            "against that same 12-month total. Students with a STEM-"
            "designated degree may apply for a one-time 24-month extension. "
            "Accumulating 12 months or more of full-time Curricular Practical "
            "Training (CPT) at a given degree level eliminates OPT eligibility "
            "for that degree entirely."
        ),
    },
    {
        "keywords": {"cpt", "curricular", "practical", "training", "internship", "coop", "employment", "authorization", "months", "fulltime", "parttime", "enrolled", "work"},
        "text": (
            "Curricular Practical Training (CPT): F-1 students may apply for "
            "CPT, employment authorization integrated directly into the "
            "academic curriculum (for example, a required internship or "
            "co-op tied to a specific course), usable only while actively "
            "enrolled. CPT itself has no fixed total time limit, but "
            "accumulating 12 months or more of full-time CPT (more than 20 "
            "hours per week) at a given degree level eliminates that "
            "student's eligibility for OPT at the same degree level. "
            "Part-time CPT (20 hours per week or less) does not count "
            "toward this 12-month threshold."
        ),
    },
]

_WORD_RE = re.compile(r"[a-z]+")


def search_policy_chunks(query: str, k: int = 3) -> list[str]:
    words = set(_WORD_RE.findall(query.lower()))
    scored = [(len(c["keywords"] & words), c["text"]) for c in _POLICY_CHUNKS]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    top = [text for score, text in scored if score > 0][:k]
    return top or [c["text"] for c in _POLICY_CHUNKS[:k]]
