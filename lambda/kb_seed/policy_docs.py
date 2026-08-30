"""
Synthetic ISTO policy documents for Meridian State University (fictional
institution, fictional office). Pre-chunked for indexing — each entry is one
retrievable unit, small enough to embed and cite directly.
"""

POLICY_CHUNKS = [
    {
        "id": "endorsement-1",
        "doc": "Re-Entry Endorsement Policy",
        "text": (
            "Re-Entry Endorsement Policy: Before traveling outside the country, "
            "an international student must have a valid passport, a valid visa "
            "for re-entry, and a current travel/re-entry endorsement signed by "
            "ISTO within the last 12 months. The endorsement is only valid for "
            "travel that occurs before its printed expiry date."
        ),
    },
    {
        "id": "endorsement-2",
        "doc": "Re-Entry Endorsement Policy",
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
        "id": "course-load-1",
        "doc": "Minimum Course Load Policy",
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
        "id": "rcl-exemption-1",
        "doc": "Medical Reduced Course Load (RCL) Exemption",
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
