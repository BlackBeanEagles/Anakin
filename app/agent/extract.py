"""Step 1: a citizen's rambling narrative -> structured, dated, referenced facts.

Departments close vague grievances. This step is what makes the filing actionable:
it pins down dates, reference numbers, the amount at stake, and one unambiguous ask.
"""
import re
from datetime import date, datetime

from ..llm import obj, structured

SYSTEM = """You are an intake officer for a public-grievance filing service in India.

Citizens describe their problem emotionally and out of order. Your job is to convert
that into the structured facts a government department needs to action a grievance,
without inventing anything.

Rules:
- NEVER invent a date, reference number, or amount. If it is not in the narrative,
  leave the field empty and add it to missing_info.
- A day and month with no year is not missing information. People write "12 August"
  about something that happened weeks ago, and the year is obvious from the fact
  that they are complaining now. Resolve it to the most recent 12 August that has
  already passed and carry on. Never ask a citizen what year they mean.
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


BARE_DATE = (
    re.compile(r"^(\d{1,2})[-/ ]([A-Za-z]{3,})$"),      # 12 August
    re.compile(r"^([A-Za-z]{3,})[-/ ](\d{1,2})$"),      # August 12
)


def _infer_year(facts: dict, today: date | None = None) -> dict:
    """Fill in a year the citizen did not write, and stop asking for it.

    A real complaint said "12 August", with tracking stopping on "14 August", filed
    in September. The extractor left incident_date empty and put "Year of the
    consignment dispatch" in missing_info - which blocked the filing, and once the
    reminder cycle ran, closed the case as abandoned. Nobody was ever going to
    answer a question that obvious, and they should not have been asked it.

    So a bare day-and-month resolves to the most recent one that has already
    passed, and any request for a year is dropped. The assumption is recorded
    rather than hidden: it goes in `assumptions`, where the drafter can cite it.
    """
    today = today or date.today()
    raw = (facts.get("incident_date") or "").strip()

    if raw and not re.search(r"\d{4}", raw):
        for pattern in BARE_DATE:
            m = pattern.match(raw)
            if not m:
                continue
            a, b = m.groups()
            day, month_name = (a, b) if a.isdigit() else (b, a)
            try:
                month = datetime.strptime(month_name[:3].title(), "%b").month
                guess = date(today.year, month, int(day))
                if guess > today:            # a date in the future means last year
                    guess = date(today.year - 1, month, int(day))
            except ValueError:
                break
            facts["incident_date"] = guess.isoformat()
            facts.setdefault("assumptions", []).append(
                f"Year not stated; read '{raw}' as {guess.isoformat()} — the most "
                f"recent one before the complaint was made.")
            break

    # Whether or not a date was recovered, never hold a filing hostage to a year.
    asks = facts.get("missing_info") or []
    kept = [q for q in asks if "year" not in q.lower()]
    if len(kept) != len(asks):
        facts.setdefault("assumptions", []).append(
            "Dropped a request for the year — it is inferable from when the "
            "complaint was made.")
    facts["missing_info"] = kept
    return facts


def extract_facts(narrative: str, category_hint: str = "") -> dict:
    user = f"Category the citizen selected: {category_hint or 'not specified'}\n\nNarrative:\n---\n{narrative}\n---"
    got = structured(
        system=SYSTEM,
        user=user,
        tool_name="record_facts",
        tool_description="Record the structured facts extracted from the citizen's narrative.",
        schema=SCHEMA,
        effort="medium",
    )
    return _infer_year(got)
