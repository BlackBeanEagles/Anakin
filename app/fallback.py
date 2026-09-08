"""What Persist does when a connector cannot be forged.

The forge is the most interesting thing this project does and the least reliable.
A build can be refused outright (BLOCKED_WEBSITE — government portals may simply be
out of scope for their builder), fail during generation, hit the three-pending cap,
or still be running long after anyone stopped watching.

None of those may stop a grievance being pursued. So a failed build is not an error
path, it is a downgrade: the read falls back to the chain that existed before Wire
was involved at all — cache, then a direct fetch, then Anakin's proxy, then a real
headless browser — and the ladder carries on with whatever that yields, or with its
timer if it yields nothing.

This module's job is to make that downgrade *visible* rather than merely survivable.
When a build fails it goes and checks, immediately, whether the site can still be
read another way, and records the answer next to the failure. That turns a dead end
into a documented finding: "the connector could not be built, and here is exactly
how much of the site remains reachable without it."
"""
import logging

from . import web

log = logging.getLogger("persist.fallback")

# What a failure means for the operator, and whether it is worth trying again. Codes
# come from anakin.AnakinError.code so this never pattern-matches English.
MEANING = {
    "BLOCKED_WEBSITE": (
        "Anakin's builder will not scrape this site. This is a policy answer, not a "
        "transient one — retrying wastes credits.", False),
    "ACTION_EXISTS": (
        "Similar actions already exist for this domain. Use them, or rerun with "
        "--force if they genuinely do not serve the need.", False),
    "BUILD_LIMIT_REACHED": (
        "Three builds are already pending upstream. Wait for one to finish.", True),
    "NO_BUDGET": (
        "Not enough credits for a 25-credit build.", False),
    "NO_KEY": (
        "No ANAKIN_API_KEY configured.", False),
    "TRANSPORT": (
        "The request never reached Anakin. Worth one retry.", True),
    "BUILD_FAILED": (
        "The builder generated a scraper but it did not pass its own test against "
        "the live site. Credits are refunded automatically.", True),
    "TIMEOUT": (
        "Still building when we stopped waiting. Not a failure — check --status "
        "later; builds are asynchronous with no published SLA.", True),
}


def explain(code: str) -> tuple[str, bool]:
    """Plain meaning, and whether retrying could plausibly help."""
    return MEANING.get(code, ("Unrecognised failure.", True))


def probe(url: str, *, case_id: str = "") -> dict:
    """Can this site still be read without a Wire connector?

    Runs the ordinary fetch chain once, uncached, so the answer reflects reality now
    rather than something read an hour ago. Costs at most the credits a normal read
    would have cost, and usually zero — a page a plain GET can fetch never reaches a
    paid rung.
    """
    page = web.get(url, use_cache=False, case_id=case_id)
    body = page.short if page.ok else ""
    return {
        "readable": bool(page.ok and len(body) > 200),
        "via": page.via or "direct",
        "chars": len(body),
        "reason": page.reason,
        "url": url,
    }


def summarise(probe_result: dict) -> str:
    """One line for the ledger and the console."""
    if probe_result["readable"]:
        return (f"still readable via {probe_result['via']} "
                f"({probe_result['chars']} chars) — the ladder keeps its evidence")
    return (f"not readable either ({probe_result['reason'] or 'empty page'}) — "
            f"the ladder falls back to its timer")
