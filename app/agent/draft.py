"""Step 3: draft the actual text for whichever rung of the ladder we are on.

Each rung has a different audience and a different argument:
  rung 1  the grievance itself, filed on the portal by the citizen
  rung 2  a direct email to the department's public grievance officer
  rung 3  an appeal, whose argument is that the *disposal* was inadequate
  rung 4  a phone script for the regional office
"""
import json

from ..llm import obj, structured

_SHARED_RULES = """
Style rules for all output:
- Formal Indian official-correspondence register. Plain, unemotional, factual.
- Lead with the reference number and date. Officials triage on those.
- Every claim must trace to a fact you were given. Invent nothing - no dates, no
  rule numbers, no amounts, no service standards you were not told about.
- Never include bank account numbers, card numbers, Aadhaar, or passwords, even if
  they appear in the source facts.
- End with exactly one specific, actionable request.
- No threats, no legal posturing, no accusations of corruption. Firm and correct wins.
- Do not use markdown formatting - this is plain-text correspondence.
"""

_DRAFT_SCHEMA = obj(
    {
        "subject": {"type": "string", "description": "Subject line, under 120 chars."},
        "body": {"type": "string", "description": "The full plain-text body."},
        "word_count": {"type": "integer"},
        "cites": {
            "type": "array",
            "description": "Reference numbers and dates cited in the body.",
            "items": {"type": "string"},
        },
        "the_ask": {"type": "string", "description": "The single action requested."},
    },
    ["subject", "body", "word_count", "cites", "the_ask"],
)


def _ctx(case: dict, facts: dict, routing: dict) -> str:
    return (
        f"CITIZEN: {case['citizen_name']}\n"
        f"CASE ID: {case['id']}\n"
        f"CPGRAMS REGISTRATION NO: {case.get('cpgrams_reg_no') or 'not yet filed'}\n"
        f"ROUTED TO: {routing.get('ministry_name')} / {routing.get('category_name')}\n"
        f"APPLICABLE RULE (may be empty): {routing.get('applicable_rule') or 'none identified'}\n\n"
        f"FACTS:\n{json.dumps(facts, indent=2, ensure_ascii=False)}"
    )


def draft_grievance(case: dict, facts: dict, routing: dict) -> dict:
    system = f"""You draft grievances for filing on India's CPGRAMS portal.

CPGRAMS bodies must be compact - aim for 150-250 words. Officials read hundreds a day.
Structure: what was promised, what happened, what has already been tried, what is
requested. Dates and reference numbers in the first two lines.
{_SHARED_RULES}"""
    return structured(
        system=system,
        user=_ctx(case, facts, routing) + "\n\nDraft the grievance body.",
        tool_name="record_draft",
        tool_description="Record the drafted grievance text.",
        schema=_DRAFT_SCHEMA,
        effort="high",
    )


def draft_officer_email(case: dict, facts: dict, routing: dict, days_waiting: int) -> dict:
    system = f"""You draft emails to a department's Public Grievance Officer in India.

This is a parallel push while a CPGRAMS grievance sits unactioned. Reference the
registration number prominently. Be courteous, note the elapsed time factually, and
ask for a status update and an officer name. 120-180 words.
{_SHARED_RULES}"""
    user = (
        _ctx(case, facts, routing)
        + f"\n\nDays elapsed since filing: {days_waiting}\n\nDraft the email to the grievance officer."
    )
    return structured(
        system=system, user=user,
        tool_name="record_draft",
        tool_description="Record the drafted officer email.",
        schema=_DRAFT_SCHEMA, effort="medium",
    )


def draft_appeal(case: dict, facts: dict, routing: dict, disposal_text: str) -> dict:
    system = f"""You draft appeals to the Appellate Authority under India's CPGRAMS system.

CRITICAL: an appeal does NOT restate the original grievance. Its argument is that the
DISPOSAL was inadequate. Quote the disposal, identify precisely what it failed to
address, and show the original ask remains unmet. 180-280 words.
{_SHARED_RULES}"""
    user = (
        _ctx(case, facts, routing)
        + f"\n\nTHE DEPARTMENT'S DISPOSAL / REPLY:\n---\n{disposal_text}\n---\n\n"
        "Draft the appeal. Attack the inadequacy of this disposal specifically."
    )
    return structured(
        system=system, user=user,
        tool_name="record_draft",
        tool_description="Record the drafted appeal.",
        schema=_DRAFT_SCHEMA, effort="high",
    )


def draft_call_script(case: dict, facts: dict, routing: dict) -> dict:
    system = f"""You write phone scripts for calling an Indian government regional office
about a pending grievance.

Output a script the caller follows verbatim: a 2-sentence opening that leads with the
registration number, the three facts to state, three likely deflections with the
response to each, and the specific thing to obtain before hanging up (an officer name,
a reference, or a committed date). Keep it under 250 words.
{_SHARED_RULES}"""
    return structured(
        system=system,
        user=_ctx(case, facts, routing) + "\n\nWrite the call script.",
        tool_name="record_draft",
        tool_description="Record the call script.",
        schema=_DRAFT_SCHEMA, effort="medium",
    )
