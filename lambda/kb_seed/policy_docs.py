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
            "load defined by their program. Falling below this minimum without "
            "prior ISTO authorization (e.g., for an approved reduced course "
            "load) can jeopardize status and any pending endorsement requests."
        ),
    },
    {
        "id": "work-hours-1",
        "doc": "On-Campus Work-Hour Cap Policy",
        "text": (
            "On-Campus Work-Hour Cap Policy: While classes are in session, "
            "international students on an on-campus work authorization may "
            "work up to 20 hours per week total, combined across all on-campus "
            "jobs. This cap is a hard limit tied to maintaining full-time "
            "student status — hours cannot be exceeded even temporarily, "
            "including during weeks with extra shifts or overtime offers."
        ),
    },
    {
        "id": "work-hours-2",
        "doc": "On-Campus Work-Hour Cap Policy",
        "text": (
            "Work-Hour Cap and Course Load Interaction: The 20-hour weekly cap "
            "applies regardless of a student's current course load, as long as "
            "they remain enrolled full-time. There is no partial exemption for "
            "students who are only slightly above the minimum required "
            "credits — the cap is the same 20 hours per week for everyone on "
            "on-campus work authorization."
        ),
    },
]
