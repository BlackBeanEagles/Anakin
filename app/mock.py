"""Offline stand-in for the model, enabled with MOCK_LLM=true.

Why this exists: the plumbing (ladder, approval gate, dashboard, escalation timing) is
most of the surface area, and you should be able to exercise all of it without an API
key and without spending tokens on every UI tweak. Set MOCK_LLM=false for real runs.

These responses are keyword-driven, not intelligent. Never demo from mock mode.
"""
import re


def narrative_only(user: str) -> str:
    """Prompts carry the taxonomy and the facts alongside the citizen's words.

    Keyword matching must see ONLY the citizen's words - the taxonomy itself lists
    "pothole" and "municipal" as out-of-scope examples, which would otherwise make
    every case look like a municipal one.
    """
    for marker in ("ORIGINAL NARRATIVE:", "Narrative:", "THE DEPARTMENT'S REPLY:"):
        if marker in user:
            return user.split(marker, 1)[1]
    return user


def _find(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.I)
    return m.group(0) if m else ""


def _amount(text: str) -> float:
    m = re.search(r"(?:rs\.?|rupees|₹)\s*([\d,]+)", text, re.I)
    if not m:
        m = re.search(r"([\d,]{3,})\s*rupees", text, re.I)
    return float(m.group(1).replace(",", "")) if m else 0.0


def _is_local_body(text: str) -> bool:
    return bool(re.search(r"pothole|bbmp|municipal|garbage|street ?light|water supply|corporation", text, re.I))


def facts(prompt: str) -> dict:
    user = narrative_only(prompt)
    refs = []
    for kind, pat in (
        ("consignment", r"\b[A-Z]{2}\d{9}IN\b"),
        ("PNR", r"\bPNR\s*:?\s*(\d{10})\b"),
        ("reference", r"\b\d{12}\b"),
    ):
        v = _find(pat, user)
        if v:
            refs.append({"kind": kind, "value": v.split()[-1]})

    local = _is_local_body(user)
    return {
        "one_line_summary": ("Pothole on a city road reported to the municipal body without action"
                             if local else "Service failure with a central government department"),
        "incident_date": "",
        "reference_numbers": refs,
        "amount_inr": _amount(user),
        "timeline": [{"date": "", "event": "Issue reported; no resolution received"}],
        "prior_attempts": ["Contacted the helpline", "Visited the office in person"],
        "ask": "Trace the matter and provide the remedy or compensation due",
        "missing_info": [],
        "sensitive_flags": [],
        "ready_to_file": True,
        "_mock": True,
    }


def routing(prompt: str) -> dict:
    user = narrative_only(prompt)
    if _is_local_body(user):
        return {
            "out_of_scope": True,
            "out_of_scope_reason": (
                "Road maintenance is a municipal function, not a central government one. "
                "CPGRAMS cannot action this. It belongs with the city corporation's own "
                "grievance portal or the ward engineer."
            ),
            "ministry_id": "", "ministry_name": "", "category_id": "", "category_name": "",
            "confidence": 0.93,
            "rationale": "MOCK: matched municipal keywords.",
            "alternates": [], "applicable_rule": "", "faster_route": "", "_mock": True,
        }
    railways = bool(re.search(r"irctc|pnr|train|tatkal|tdr|railway", user, re.I))
    if railways:
        return {
            "out_of_scope": False, "out_of_scope_reason": "",
            "ministry_id": "MOR", "ministry_name": "Ministry of Railways (Railway Board)",
            "category_id": "MOR-REFUND", "category_name": "Refund not received (TDR / cancellation)",
            "confidence": 0.88,
            "rationale": "MOCK: a PNR and a TDR reference put this with Railways, not the bank.",
            "alternates": [{"ministry_id": "DFS", "category_id": "DFS-TXN",
                            "why_not": "The debit is real but IRCTC holds the funds; the bank cannot reverse it."}],
            "applicable_rule": "", "faster_route": "", "_mock": True,
        }
    return {
        "out_of_scope": False, "out_of_scope_reason": "",
        "ministry_id": "DOPOS", "ministry_name": "Department of Posts",
        "category_id": "DOPOS-NONDEL", "category_name": "Non-delivery of article",
        "confidence": 0.91,
        "rationale": "MOCK: an EX…IN consignment number is an India Post Speed Post article.",
        "alternates": [{"ministry_id": "DOPOS", "category_id": "DOPOS-DELAY",
                        "why_not": "Tracking has stalled entirely rather than merely running late."}],
        "applicable_rule": "", "faster_route": "", "_mock": True,
    }


def draft(user: str) -> dict:
    body = (
        "To the Public Grievance Officer,\n\n"
        "This grievance concerns a service failure that remains unresolved despite prior contact\n"
        "through the published helpline and an in-person visit to the office concerned.\n\n"
        "[MOCK DRAFT — set MOCK_LLM=false and provide ANTHROPIC_API_KEY to generate the real\n"
        "grievance text. This placeholder exists so the ladder, approval gate, and dashboard\n"
        "can be exercised end to end without spending tokens.]\n\n"
        "I request that the matter be traced and the remedy due to me be provided, and that the\n"
        "name and designation of the officer handling this be communicated to me.\n\n"
        "Yours faithfully,"
    )
    return {
        "subject": "Grievance regarding unresolved service failure [MOCK]",
        "body": body, "word_count": len(body.split()),
        "cites": [], "the_ask": "Trace the matter and provide the remedy due", "_mock": True,
    }


def assessment(prompt: str) -> dict:
    user = narrative_only(prompt)
    forwarded = bool(re.search(r"forward|concerned office|under process|closed", user, re.I))
    if forwarded:
        return {
            "verdict": "deflection",
            "reasoning": "MOCK: the reply forwards or closes without addressing the ask.",
            "unaddressed": ["The remedy originally requested"],
            "commitments": [], "info_requested": [],
            "amount_recovered_inr": 0.0, "should_escalate": True, "_mock": True,
        }
    return {
        "verdict": "resolved",
        "reasoning": "MOCK: the reply states concrete completed action.",
        "unaddressed": [], "commitments": ["Refund processed"], "info_requested": [],
        "amount_recovered_inr": 0.0, "should_escalate": False, "_mock": True,
    }


DISPATCH = {
    "record_facts": facts,
    "record_routing": routing,
    "record_draft": draft,
    "record_assessment": assessment,
}


def respond(tool_name: str, user: str) -> dict:
    fn = DISPATCH.get(tool_name)
    if not fn:
        raise RuntimeError(f"mock has no handler for {tool_name}")
    return fn(user)
