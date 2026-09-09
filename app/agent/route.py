"""Step 2: pick the correct Ministry -> Organisation -> Category.

This is the product's core claim. A grievance filed against the wrong department is
closed weeks later as "not related to this department" and the citizen starts over.
The router must also be willing to say "CPGRAMS is the wrong mechanism entirely".
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from ..llm import obj, structured

log = logging.getLogger("persist.route")

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
            # ministry_name/category_name are here because models want to emit them
            # anyway. Under strict schemas Groq rejects the whole tool call for an
            # unexpected key, forcing a slow JSON-mode retry - cheaper to accept the
            # fields than to fight for their absence.
            "items": obj(
                {
                    "ministry_id": {"type": "string"},
                    "ministry_name": {"type": "string"},
                    "category_id": {"type": "string"},
                    "category_name": {"type": "string"},
                    "why_not": {"type": "string"},
                },
                ["ministry_id", "ministry_name", "category_id", "category_name", "why_not"],
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
    got = structured(
        system=SYSTEM,
        user=user,
        tool_name="record_routing",
        tool_description="Record the chosen authority and category for this grievance.",
        schema=SCHEMA,
        effort="high",  # the hard reasoning step - worth the tokens
    )
    return repair(got)


def repair(routing: dict) -> dict:
    """Make the model's answer refer to things that exist.

    The router once returned ministry `MOPP` - a plausible-looking code for the
    pension department that is not in the taxonomy - while getting the category
    (DPPW-LC) exactly right. Nothing checked, so the case would have gone on to look
    up an officer for a ministry that does not exist, found none, and quietly lost
    its email rung. A wrong answer that announces itself is fine; this one did not.

    Two deterministic repairs, no second model call:

      1. An unknown ministry with a known category is recoverable, because the
         category namespace already names its owner - DPPW-LC can only belong to
         DPPW. This is the MOPP case, and it fixes it exactly.
      2. A category that does not belong to the chosen ministry is dropped rather
         than guessed at. Filing under the wrong category inside the right ministry
         is a recoverable annoyance; inventing one is not.

    Names are always overwritten from the taxonomy, so a paraphrased department name
    can never reach a letterhead.
    """
    tax = load_taxonomy()
    by_ministry = {m["id"]: m for m in tax["ministries"]}
    owner_of = {c["id"]: m["id"] for m in tax["ministries"] for c in m["categories"]}
    name_of = {c["id"]: c["name"] for m in tax["ministries"] for c in m["categories"]}

    if routing.get("out_of_scope"):
        routing["ministry_id"] = ""
        routing["category_id"] = ""
        return routing

    mid = (routing.get("ministry_id") or "").strip().upper()
    cid = (routing.get("category_id") or "").strip().upper()

    if mid not in by_ministry:
        recovered = owner_of.get(cid, "")
        if recovered:
            log.warning("route: unknown ministry %r repaired to %s via category %s",
                        mid, recovered, cid)
            routing["repaired"] = f"ministry {mid or '(empty)'} -> {recovered}"
            mid = recovered
        else:
            log.warning("route: unknown ministry %r and category %r - cannot repair",
                        mid, cid)
            routing["repaired"] = f"unknown ministry {mid or '(empty)'}, not recoverable"
            routing["confidence"] = min(float(routing.get("confidence") or 0), 0.3)

    if cid and owner_of.get(cid) != mid:
        log.warning("route: category %r does not belong to %s - dropping it", cid, mid)
        routing["repaired"] = (routing.get("repaired", "") +
                               f"; dropped category {cid}").lstrip("; ")
        cid = ""

    routing["ministry_id"] = mid if mid in by_ministry else ""
    routing["category_id"] = cid
    # Never let a paraphrased name reach a letterhead.
    if mid in by_ministry:
        routing["ministry_name"] = by_ministry[mid]["name"]
    if cid:
        routing["category_name"] = name_of[cid]
    return routing


def ministry(ministry_id: str) -> dict | None:
    for m in load_taxonomy()["ministries"]:
        if m["id"] == ministry_id:
            return m
    return None
