"""
Demo modification: no vector knowledge base on this branch — no
OpenSearch domain, no embeddings call for retrieval. Policy chunks are
keyword-matched from a small static list bundled directly in this Lambda.
This trades real semantic search for a much simpler, much faster-to-stand-up
deployment; it's a deliberate scope cut for the demo, not a stand-in for
what a production RAG design would look like.

Retrieval is a simple keyword-overlap match against the student's message
— good enough to route "can I travel" to the endorsement chunks and "drop
a class" to the course-load/RCL chunks.
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
]

_WORD_RE = re.compile(r"[a-z]+")


def search_policy_chunks(query: str, k: int = 3) -> list[str]:
    words = set(_WORD_RE.findall(query.lower()))
    scored = [(len(c["keywords"] & words), c["text"]) for c in _POLICY_CHUNKS]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    top = [text for score, text in scored if score > 0][:k]
    return top or [c["text"] for c in _POLICY_CHUNKS[:k]]
