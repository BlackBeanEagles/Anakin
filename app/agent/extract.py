"""Step 1: a citizen's rambling narrative -> structured, dated, referenced facts.

Departments close vague grievances. This step is what makes the filing actionable:
it pins down dates, reference numbers, the amount at stake, and one unambiguous ask.
"""
from ..llm import obj, structured

SYSTEM = """You are an intake officer for a public-grievance filing service in India.

Citizens describe their problem emotionally and out of order. Your job is to convert
that into the structured facts a government department needs to action a grievance,
without inventing anything.

Rules:
- NEVER invent a date, reference number, or amount. If it is not in the narrative,
  leave the field empty and add it to missing_info.
- Reference numbers matter enormously: consignment/tracking numbers, PNR, UAN,
  transaction IDs, application numbers, booking IDs. Extract every one you see,
  verbatim, preserving case and punctuation.
- The `ask` must be a single, specific, achievable action by the department -
  "refund Rs 1,240 to the account used for booking", not "do something".
- missing_info should list only facts that would genuinely change whether the
  department can act. Do not pad it.
- Flag anything that looks like sensitive data the citizen should not have shared
  (full bank account numbers, card numbers, Aadhaar, passwords) in `sensitive_flags`
  so we can avoid echoing it into a filing."""


SCHEMA = obj(
    {
        "one_line_summary": {
            "type": "string",
            "description": "Neutral one-sentence summary, max 140 chars, safe to show publicly.",
        },
        "incident_date": {
            "type": "string",
            "description": "ISO date (YYYY-MM-DD) when the problem started, or empty string if not stated.",
        },
        "reference_numbers": {
            "type": "array",
            "description": "Every tracking/PNR/transaction/application number found, verbatim.",
            "items": obj(
                {
                    "kind": {"type": "string", "description": "e.g. consignment, PNR, transaction, UAN"},
                    "value": {"type": "string"},
                },
                ["kind", "value"],
            ),
        },
        "amount_inr": {
            "type": "number",
            "description": "Rupee amount at stake. 0 if none or not stated.",
        },
        "timeline": {
            "type": "array",
            "description": "Chronological events the citizen described.",
            "items": obj(
                {
                    "date": {"type": "string", "description": "ISO date or empty string"},
                    "event": {"type": "string"},
                },
                ["date", "event"],
            ),
        },
        "prior_attempts": {
            "type": "array",
            "description": "Steps the citizen already took (called helpline, emailed, visited office).",
            "items": {"type": "string"},
        },
        "ask": {
            "type": "string",
            "description": "The single specific remedy being demanded.",
        },
        "missing_info": {
            "type": "array",
            "description": "Facts we must ask the citizen for before filing. Empty if ready.",
            "items": {"type": "string"},
        },
        "sensitive_flags": {
            "type": "array",
            "description": "Sensitive data the citizen over-shared that we must not echo.",
            "items": {"type": "string"},
        },
        "ready_to_file": {
            "type": "boolean",
            "description": "True only if the grievance can be filed as-is without more facts.",
        },
    },
    [
        "one_line_summary", "incident_date", "reference_numbers", "amount_inr",
        "timeline", "prior_attempts", "ask", "missing_info", "sensitive_flags",
        "ready_to_file",
    ],
)


def extract_facts(narrative: str, category_hint: str = "") -> dict:
    user = f"Category the citizen selected: {category_hint or 'not specified'}\n\nNarrative:\n---\n{narrative}\n---"
    return structured(
        system=SYSTEM,
        user=user,
        tool_name="record_facts",
        tool_description="Record the structured facts extracted from the citizen's narrative.",
        schema=SCHEMA,
        effort="medium",
    )
