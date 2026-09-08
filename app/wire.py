"""Reading a site through a Wire action instead of scraping it.

This is the half of the merge that makes the forge worth doing. Building a connector
for pgportal.gov.in achieves nothing if the watcher goes on fetching the same
captcha-gated HTML it always did, so this is the path from a tool in the toolbox to
a fact about a case.

Why it is better than scraping, and not merely different:

  A scrape returns a page. The model then reads that page, which costs a call, and
  degrades the day the portal is redesigned - the text moves and the reasoning has
  to cope. A Wire action returns *fields*: status, ministry, a dated history. There
  is nothing to interpret, so there is nothing to misinterpret.

  Zero Touch runs read-only actions with no key and no credits. A connector that
  exists is therefore usually cheaper than the scrape it replaces, not dearer.

The lookup deliberately prefers connectors this project had built. They were
specified for exactly this question, where a catalogued action was written for
somebody else's.

Everything here fails soft. No tool, an unrunnable tool, a result that does not
contain what was asked for - all of them return None, and `watch.py` carries on down
the chain it used before any of this existed.
"""
import logging
import re

from . import anakin, db

log = logging.getLogger("persist.wire")

# Parameter names a connector might use for the same thing. Wire actions are
# generated from English descriptions, so the builder's choice of name is not
# knowable in advance - `registration_number`, `reg_no` and `grievance_id` are all
# plausible outputs for the identifier we hold. Binding on meaning rather than on an
# exact string is what keeps this working against a tool nobody has seen yet.
SYNONYMS = {
    "registration_number": (
        "registration", "reg_no", "regno", "grievance", "complaint", "reference",
        "docket", "acknowledgement", "ack"),
    "consignment_number": (
        "consignment", "article", "tracking", "awb", "barcode", "item_number"),
}

# Values that are never an identifier, whatever a parameter is called.
_JUNK = {"", "none", "null", "n/a", "-"}


def find_tool(domain: str) -> dict | None:
    """The best connector held for a domain, or None.

    Built connectors win over catalogued ones: this project specified them for this
    exact question. Among equals, the one with the better hit rate wins - a tool
    that has answered before is better evidence than a tool that merely exists.
    """
    want = (domain or "").lower().removeprefix("www.").strip()
    if not want:
        return None
    held = [t for t in db.tools() if want in (t.get("domain") or "").lower()]
    if not held:
        return None
    held.sort(key=lambda t: (
        0 if t["origin"] == "built" else 1,
        -(t["wins"] / t["uses"] if t["uses"] else 0.5),
    ))
    return held[0]


def _schema_params(tool: dict) -> list[dict]:
    """The action's parameters, whichever shape they were stored in.

    A tool learned from the catalog carries {required: [...], optional: [...]}; one
    that arrived from a build request carries whatever the build response held. Both
    end up here, so both shapes are handled rather than assumed.
    """
    schema = db.jload(tool.get("schema_json"), {}) or {}
    params = schema.get("params") or schema.get("parameters") or {}
    if isinstance(params, list):
        return params
    out = list(params.get("required") or [])
    out.extend(params.get("optional") or [])
    if not out:
        # Build responses sometimes describe the action rather than its inputs. An
        # empty list is honest: bind_params will then send only what it is sure of.
        for key in ("required", "inputs", "arguments"):
            maybe = schema.get(key)
            if isinstance(maybe, list):
                out.extend(maybe)
    return [p for p in out if isinstance(p, dict)]


def bind_params(tool: dict, values: dict) -> dict:
    """Fill an action's parameters from what we know about a case.

    Only names that plausibly mean one of our values are filled. A parameter we
    cannot match is left out entirely rather than guessed at - an action called with
    an invented reference number returns a confident answer about the wrong record,
    which is worse than not calling it.
    """
    known = {k: str(v).strip() for k, v in values.items()
             if str(v).strip().lower() not in _JUNK}
    if not known:
        return {}

    params = _schema_params(tool)
    if not params:
        # No schema to bind against. Send the identifiers under their canonical
        # names and let the action ignore what it does not want.
        return dict(known)

    bound: dict = {}
    for spec in params:
        name = (spec.get("name") or "").lower()
        if not name:
            continue
        for canonical, words in SYNONYMS.items():
            if canonical not in known:
                continue
            if name == canonical or any(w in name for w in words):
                bound[spec["name"]] = known[canonical]
                break
    return bound


def read(domain: str, values: dict, *, case_id: str = "") -> dict | None:
    """Read a site through its connector. None when that was not possible.

    Free path first, always: Zero Touch runs read-only actions with no key and no
    credits, so a connector usually costs less than the scrape it replaces. The
    keyed flow is only reached for actions that need an account.
    """
    tool = find_tool(domain)
    if not tool:
        return None

    params = bind_params(tool, values)
    if not params:
        log.info("wire: %s holds a tool but nothing to call it with", domain)
        return None

    action_id = tool["action_id"]
    got = anakin.zero_touch(action_id, params)
    route = "zero_touch"

    if got is None and anakin.configured():
        got = anakin.task(action_id, params,
                          credits=int(tool.get("credits") or 1), case_id=case_id)
        route = "wire_task"

    if got is None:
        db.score_tool(action_id, False)
        log.info("wire: %s did not run", action_id)
        return None

    payload = got.get("result") if isinstance(got, dict) else None
    if payload is None:
        payload = got

    # A tool that runs but returns nothing is a miss, not a hit. Scoring it as a win
    # would let a broken connector keep winning the lookup in find_tool forever.
    useful = bool(payload) and len(str(payload)) > 40
    db.score_tool(action_id, useful)
    if not useful:
        return None

    if case_id:
        db.log_event(case_id, "observed",
                     f"Read {domain} through {action_id} "
                     f"({'free' if route == 'zero_touch' else 'keyed'})")

    return {"action_id": action_id, "route": route, "domain": domain,
            "origin": tool["origin"], "data": payload}


def summarise(result: dict, limit: int = 400) -> str:
    """A line for the case timeline. Structured data needs no model to read it."""
    data = result["data"]
    if isinstance(data, dict):
        parts = [f"{k}: {v}" for k, v in data.items()
                 if isinstance(v, (str, int, float)) and str(v).strip()]
        text = "; ".join(parts) or str(data)
    else:
        text = str(data)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]
