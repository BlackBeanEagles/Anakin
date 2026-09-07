"""Step 2: pick the correct Ministry -> Organisation -> Category.

This is the product's core claim. A grievance filed against the wrong department is
closed weeks later as "not related to this department" and the citizen starts over.
The router must also be willing to say "CPGRAMS is the wrong mechanism entirely".
"""
import json
from functools import lru_cache
from pathlib import Path

from ..llm import obj, structured

TAXONOMY_PATH = Path(__file__).parent.parent / "taxonomy.json"

SYSTEM = """You route citizen grievances to the correct authority on India's CPGRAMS
portal (pgportal.gov.in).

You are given the full ministry/department taxonomy. Choose exactly one ministry and
one category from it.

Hard rules:
- CPGRAMS covers CENTRAL government only. If the grievance belongs to a state or
  municipal body, a court, or is an RTI request, set out_of_scope=true and explain
  where it actually belongs. Misfiling wastes the citizen 30 days - refusing to file
  is the correct and valuable answer.
- Choose the most SPECIFIC category that fits. A generic category invites a generic
  brush-off.
- confidence reflects your genuine certainty. Below 0.7 means a human should look
  before this is filed. Do not inflate it.
- rationale must name the concrete detail that decided it (a consignment number
  format, the word "PNR", the named service), not restate the category.
- alternates lists the runner-up choices you seriously considered, so a human
  reviewing a low-confidence routing can see the real options."""


SCHEMA = obj(
    {
        "out_of_scope": {
            "type": "boolean",
            "description": "True if CPGRAMS is the wrong mechanism for this grievance.",
        },
        "out_of_scope_reason": {
            "type": "string",
            "description": "If out_of_scope, where this actually belongs. Empty otherwise.",
        },
        "ministry_id": {"type": "string", "description": "Ministry id from the taxonomy, or empty if out of scope."},
        "ministry_name": {"type": "string"},
        "category_id": {"type": "string", "description": "Category id from the taxonomy, or empty if out of scope."},
        "category_name": {"type": "string"},
        "confidence": {
            "type": "number",
            "description": "0.0 to 1.0. Genuine certainty that this routing is correct.",
        },
        "rationale": {
            "type": "string",
            "description": "The specific detail in the facts that decided this routing.",
        },
        "alternates": {
            "type": "array",
            "description": "Runner-up routings seriously considered.",
            "items": obj(
                {
                    "ministry_id": {"type": "string"},
                    "category_id": {"type": "string"},
                    "why_not": {"type": "string"},
                },
                ["ministry_id", "category_id", "why_not"],
            ),
        },
        "applicable_rule": {
            "type": "string",
            "description": (
                "The service standard, citizen charter clause, or policy the department "
                "is failing to meet, if you are confident one applies. Empty if unsure - "
                "do NOT invent a rule, section, or act number."
            ),
        },
        "faster_route": {
            "type": "string",
            "description": (
                "If the taxonomy NOTE or DEDICATED PORTAL for the chosen ministry names a "
                "system that would resolve this faster than CPGRAMS (EPFiGMS for provident "
                "fund, CPENGRAMS for pensions, AirSewa for airlines, the RBI ombudsman for "
                "banks), say so in one sentence naming it. Empty string if none applies. "
                "Telling the citizen about a faster route costs us nothing and saves them "
                "weeks."
            ),
        },
    },
    [
        "out_of_scope", "out_of_scope_reason", "ministry_id", "ministry_name",
        "category_id", "category_name", "confidence", "rationale", "alternates",
        "applicable_rule", "faster_route",
    ],
)


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def _taxonomy_for_prompt() -> str:
    tax = load_taxonomy()
    lines = []
    for m in tax["ministries"]:
        lines.append(f"\n[{m['id']}] {m['name']}  (under {m['parent']})")
        lines.append(f"  signals: {', '.join(m['keywords'])}")
        # These notes carry the most useful fact in the file — that several of these
        # departments run a dedicated system that is faster than CPGRAMS. The router
        # has to see them to be able to say so.
        if m.get("note"):
            lines.append(f"  NOTE: {m['note']}")
        if m.get("alternate_portal"):
            lines.append(f"  DEDICATED PORTAL: {m['alternate_portal']}")
        for c in m["categories"]:
            lines.append(f"    - [{c['id']}] {c['name']}: {c['hint']}")
    oos = tax["out_of_scope"]
    lines.append(f"\nOUT OF SCOPE FOR CPGRAMS ({oos['note']}):")
    lines.extend(f"  - {e}" for e in oos["examples"])
    return "\n".join(lines)


def route_case(facts: dict, narrative: str, category_hint: str = "") -> dict:
    user = (
        f"TAXONOMY:\n{_taxonomy_for_prompt()}\n\n"
        f"CITIZEN-SELECTED CATEGORY (a hint only, may be wrong): {category_hint or 'none'}\n\n"
        f"EXTRACTED FACTS:\n{json.dumps(facts, indent=2, ensure_ascii=False)}\n\n"
        f"ORIGINAL NARRATIVE:\n---\n{narrative}\n---"
    )
    return structured(
        system=SYSTEM,
        user=user,
        tool_name="record_routing",
        tool_description="Record the chosen authority and category for this grievance.",
        schema=SCHEMA,
        effort="high",  # the hard reasoning step - worth the tokens
    )


def ministry(ministry_id: str) -> dict | None:
    for m in load_taxonomy()["ministries"]:
        if m["id"] == ministry_id:
            return m
    return None
