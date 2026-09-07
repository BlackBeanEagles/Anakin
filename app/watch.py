"""Check reality before escalating.

Without this the agent is a timer: it files, sleeps 21 days, and escalates whether or
not anything happened. That is both weaker and ruder than it needs to be - escalating
to a Director on a grievance that was quietly resolved last week helps nobody.

So before each climb the agent goes and looks: the CPGRAMS status page for the
registration number, and the carrier's own tracking page for the consignment or PNR.
Two things come back that the timer never had:

  1. A reason to act NOW rather than on day 21 (status already shows "closed").
  2. Evidence to cite in the appeal - "tracking still reads 'in transit' 41 days
     after booking" is a fact the department has to answer.

Reading is done by the model rather than CSS selectors. Government pages are
inconsistent and get redesigned; a model reading visible text survives that, and
degrades to "cannot tell" instead of silently matching the wrong element.

Several of these pages are captcha- or session-gated. That is expected. When a read
fails the ladder falls back to its timer, which is exactly the behaviour it had
before - live reading is an improvement on the floor, never a new dependency.
"""
import json
import logging

from . import db, web
from .config import settings
from .llm import LLMError, obj, structured

log = logging.getLogger("persist.watch")

SYSTEM = """You are reading a page from an Indian government or postal website on
behalf of a citizen tracking a grievance or a consignment.

Report only what the page actually says. These pages are frequently captcha-gated,
session-gated, or return a generic landing page instead of the record - in every one
of those cases `readable` is false. Never infer a status from an absence, and never
guess at a date that is not printed.

A page that shows a search form rather than a result is NOT readable.
A page that says "no records found" IS readable - that is a real answer."""

SCHEMA = obj(
    {
        "readable": {
            "type": "boolean",
            "description": "True only if this page actually shows the record we asked for.",
        },
        "why_not": {
            "type": "string",
            "description": "If not readable: captcha, login wall, search form, error page, etc. Empty otherwise.",
        },
        "status_text": {
            "type": "string",
            "description": "The status exactly as printed on the page. Empty if not readable.",
        },
        "closed": {
            "type": "boolean",
            "description": "Does the page say the grievance is disposed of, closed, or replied to?",
        },
        "still_pending": {
            "type": "boolean",
            "description": "Does the page show the matter as open, in transit, or under process?",
        },
        "last_event_date": {
            "type": "string",
            "description": "ISO date of the most recent event shown, or empty string.",
        },
        "evidence": {
            "type": "string",
            "description": (
                "One sentence a citizen could quote back to the department, using only "
                "what is printed here. Empty if nothing quotable."
            ),
        },
    },
    ["readable", "why_not", "status_text", "closed", "still_pending",
     "last_event_date", "evidence"],
)


def _read(url: str, what: str, reference: str) -> dict | None:
    """Fetch one page and have the model read it. None if it could not be read."""
    page = web.get(url)
    if not page.ok:
        log.info("watch: %s unreachable (%s)", url, page.reason)
        return None
    body = page.short
    if len(body) < 80:
        return None
    try:
        got = structured(
            system=SYSTEM,
            user=(f"We are tracking {what} with reference {reference}.\n"
                  f"Page: {url}\n\nVisible text:\n---\n{body}\n---"),
            tool_name="read_status_page",
            tool_description="Report what this page says about the reference.",
            schema=SCHEMA, effort="medium", max_tokens=2000,
        )
    except LLMError as exc:
        log.info("watch: model could not read %s (%s)", url, exc)
        return None
    got["source_url"] = url
    return got


def check_case(case: dict) -> dict | None:
    """Look at the live web for this case. None if nothing could be read.

    Cheap and safe by construction: the fetch layer caches, throttles per host, and
    caps fetches per tick, so calling this on every due case does not turn into a
    flood even with a full queue.
    """
    if not settings.web_reading_enabled or settings.provider.kind == "mock":
        return None

    facts = db.jload(case["facts_json"], {}) or {}
    findings: list[dict] = []

    # 1. the grievance itself
    if case.get("cpgrams_reg_no"):
        got = _read(settings.cpgrams_status_url, "a CPGRAMS grievance",
                    case["cpgrams_reg_no"])
        if got and got["readable"]:
            got["kind"] = "grievance_status"
            findings.append(got)

    # 2. the underlying consignment or booking
    for ref in (facts.get("reference_numbers") or [])[:1]:
        kind = (ref.get("kind") or "").lower()
        value = ref.get("value") or ""
        if not value:
            continue
        if "consign" in kind or "speed" in kind or "track" in kind:
            got = _read(settings.indiapost_track_url, "an India Post consignment", value)
            if got and got["readable"]:
                got["kind"] = "consignment"
                findings.append(got)

    if not findings:
        return None

    primary = findings[0]
    db.log_event(
        case["id"], "observed",
        f"Read {primary['source_url']} — {primary['status_text'] or 'status unclear'}"
        + (f" · {primary['evidence']}" if primary["evidence"] else ""),
    )
    return {"findings": findings, "primary": primary}


def refresh_officer_directory() -> dict:
    """Re-read the nodal officer directory.

    Six of our contacts belong to named individuals who rotate with the posting, so a
    static file goes stale. This reads the live directory and reports drift - it does
    NOT rewrite taxonomy.json on its own, because silently changing who a citizen's
    grievance gets sent to is not a decision an agent should make unsupervised.
    """
    from .agent.route import load_taxonomy

    page = web.get(settings.nodal_directory_url)
    if not page.ok:
        return {"ok": False, "reason": page.reason, "drift": []}

    # Search the FULL page, not page.short - that is capped at 6000 chars for the
    # model, and this directory is a long table. Using the capped text reported every
    # contact past the cutoff as missing, which is worse than not checking at all.
    body = page.text.lower()
    drift = []
    for m in load_taxonomy()["ministries"]:
        email = (m.get("grievance_officer_email") or "").strip()
        if not email:
            continue
        local = email.split("@")[0]
        # the directory obfuscates addresses as name[at]domain[dot]in
        if local and local.lower() not in body:
            drift.append({"ministry_id": m["id"], "on_file": email,
                          "email_kind": m.get("email_kind", "")})

    return {"ok": True, "reason": "", "drift": drift,
            "checked": settings.nodal_directory_url}
