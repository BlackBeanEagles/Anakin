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

Where a Wire connector exists for the site, it is used first: it returns fields
rather than a page, costs nothing through Zero Touch, and survives the redesigns
that break scrapers. Everything below is what happens when there is no connector -
which, for a government portal nothing in a 991-site catalog covers, is the normal
case until one is forged.

Reading is done by the model rather than CSS selectors. Government pages are
inconsistent and get redesigned; a model reading visible text survives that, and
degrades to "cannot tell" instead of silently matching the wrong element.

Several of these pages are captcha- or session-gated. That is expected. When a read
fails the ladder falls back to its timer, which is exactly the behaviour it had
before - live reading is an improvement on the floor, never a new dependency.
"""
import json
import logging

from . import db, web, wire
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


def _read(url: str, what: str, reference: str, case_id: str = "") -> dict | None:
    """Fetch one page and have the model read it. None if it could not be read."""
    page = web.get(url, case_id=case_id)
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
    # How the page was obtained matters to the record. "Read the CPGRAMS status
    # page" and "read it through a headless browser after the direct request was
    # refused" are different claims, and the appeal quotes this evidence.
    got["via"] = page.via
    return got


def _via_wire(domain: str, values: dict, source_url: str, *,
              case_id: str = "") -> dict | None:
    """Try the connector before the scraper. None when there isn't one, or it missed.

    This is the rung the forge exists to create. Where a connector is held for a
    domain, it is strictly better than fetching the page: it costs nothing through
    Zero Touch, it survives a redesign that would move the text a scraper depends
    on, and it returns fields rather than prose - so the model is not asked to
    interpret anything and cannot misinterpret it.

    The return is shaped exactly like `_read`'s so the caller cannot tell which rung
    answered. That symmetry is the point: the connector is an upgrade to how a page
    gets read, never a second code path the ladder has to know about.
    """
    got = wire.read(domain, values, case_id=case_id)
    if not got:
        return None

    data = got["data"]
    flat = wire.summarise(got, limit=1200)
    lowered = flat.lower()

    # Structured output needs no model to read it, but it does need interpreting
    # against our own vocabulary - "disposed of" and "closed" mean the same thing to
    # a department and different things to a dict.
    closed = any(w in lowered for w in
                 ("disposed", "closed", "resolved", "replied", "delivered"))
    pending = any(w in lowered for w in
                  ("pending", "under process", "in transit", "open", "receipt"))

    status_text = ""
    if isinstance(data, dict):
        for key in ("status", "current_status", "status_text", "state"):
            if data.get(key):
                status_text = str(data[key])
                break
    status_text = status_text or flat[:200]

    return {
        "readable": True,
        "why_not": "",
        "status_text": status_text,
        "closed": closed,
        "still_pending": pending and not closed,
        "last_event_date": str((data or {}).get("last_event_date", ""))
                           if isinstance(data, dict) else "",
        "evidence": flat[:300],
        "source_url": source_url,
        "via": f"wire:{got['action_id']}",
    }


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
        got = _via_wire("pgportal.gov.in",
                        {"registration_number": case["cpgrams_reg_no"]},
                        settings.cpgrams_status_url, case_id=case["id"])
        if got is None:
            got = _read(settings.cpgrams_status_url, "a CPGRAMS grievance",
                        case["cpgrams_reg_no"], case_id=case["id"])
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
            got = _via_wire("indiapost.gov.in", {"consignment_number": value},
                            settings.indiapost_track_url, case_id=case["id"])
            if got is None:
                got = _read(settings.indiapost_track_url, "an India Post consignment",
                            value, case_id=case["id"])
            if got and got["readable"]:
                got["kind"] = "consignment"
                findings.append(got)

    if not findings:
        return None

    primary = findings[0]
    db.log_event(
        case["id"], "observed",
        f"Read {primary['source_url']} via {primary.get('via', 'direct')} — "
        f"{primary['status_text'] or 'status unclear'}"
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
