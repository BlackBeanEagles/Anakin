"""A local index over Wire's catalog, because `/resolve` alone is not good enough.

Persist needs this for one specific question it has to get right: *does a pre-built
action already exist for this site?* Answering "no" wrongly costs 25 credits on a
build nobody needed. Answering "yes" wrongly means filing a grievance blind.

`/resolve` is Anakin's own intent search. It is free and it is the right idea, but it
matches on text: ask it for "Indian government grievance portal" and it returns an
Indian sports shop, because that shop's description contains the word "portal". An
agent that binds whatever resolve returns first will confidently call the wrong tool.

So discovery here is three passes, and only the first one costs a network round trip
per query:

  1. resolve()      their search. Kept, because it knows about actions the catalog
                    listing does not spell out.
  2. local lexical  the full 991-site catalog is ~1MB and free to fetch, so it is
                    cached on disk and scored offline: domain, name, category and
                    description, weighted so a domain hit beats a description hit.
  3. expansion      for the best few sites, pull their full action list — also free.
                    This is what surfaces the specific action, since a site's own
                    listing is complete where resolve's top-10 is a sample.

The model does the final pick from that merged shortlist. Cheap search narrows;
expensive reasoning decides. Doing it the other way round is how you spend a model
call ranking a thousand irrelevant rows.
"""
import json
import logging
import re
import time

from . import anakin
from .config import settings

log = logging.getLogger("persist.catalog")

STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "by", "with",
    "from", "is", "are", "was", "were", "be", "get", "find", "show", "me", "my",
    "what", "which", "how", "any", "all", "list", "about", "that", "this", "it",
}


def _terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) > 2 and w not in STOPWORDS]


# ------------------------------------------------------------------ the cached index

def load(*, force: bool = False) -> list[dict]:
    """The whole catalog, cached on disk. Free either way; this just avoids
    re-downloading a megabyte on every intent in a multi-step run."""
    path = settings.catalog_cache
    if not force and path.exists():
        age = time.time() - path.stat().st_mtime
        if age < settings.catalog_ttl_seconds:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    sites = anakin.catalog()
    if sites:
        try:
            path.write_text(json.dumps(sites), encoding="utf-8")
        except OSError as exc:
            log.info("could not cache catalog: %s", exc)
    return sites


def covers(domain: str) -> list[dict]:
    """Does the catalog have this site? Definitive, unlike resolve().

    An empty list here is real evidence that nothing covers a domain — which is what
    justifies paying to build one. resolve() returning junk proves only that its
    search is fuzzy.
    """
    want = domain.lower().removeprefix("www.").strip()
    return [s for s in load() if want and want in (s.get("domain") or "").lower()]


def search_sites(query: str, *, limit: int = 6) -> list[dict]:
    """Score every site against the query offline. No network, no credits.

    The weighting is deliberately blunt: a term appearing in the domain or the site
    name is strong evidence, a term in the category is decent, and a term in the
    description is weak — descriptions are marketing prose and match almost anything.
    """
    terms = _terms(query)
    if not terms:
        return []

    scored = []
    for site in load():
        domain = (site.get("domain") or "").lower()
        name = (site.get("name") or "").lower()
        category = (site.get("category") or "").lower()
        blurb = (site.get("description") or "").lower()

        score = 0
        for t in terms:
            if t in domain:
                score += 6
            if t in name:
                score += 5
            if t in category:
                score += 3
            if t in blurb:
                score += 1
        if score:
            scored.append((score, site))

    scored.sort(key=lambda pair: (-pair[0], pair[1].get("slug") or ""))
    return [s for _, s in scored[:limit]]


# ------------------------------------------------------------------ candidate actions

def _normalise(action: dict, *, catalog_slug: str = "", domain: str = "") -> dict:
    """One shape for candidates, whichever endpoint they arrived from.

    resolve() and the per-catalog listing disagree about field names, and the params
    block is sometimes {required: [...], optional: [...]} and sometimes a flat list.
    Normalising here keeps that mess out of the prompt and out of the executor.
    """
    params = action.get("params") or action.get("parameters") or {}
    if isinstance(params, list):
        params = {"required": params, "optional": []}
    return {
        "action_id": action.get("action_id") or action.get("id") or "",
        "catalog": action.get("catalog") or catalog_slug,
        "domain": domain,
        "name": action.get("name") or "",
        "description": action.get("description") or "",
        "credits": int(action.get("credits") or 1),
        "auth_required": bool(action.get("auth_required")),
        "required": params.get("required") or [],
        "optional": params.get("optional") or [],
    }


def candidates(intent: str, *, limit: int | None = None) -> list[dict]:
    """Everything worth considering for one sub-goal, cheapest discovery first.

    Free throughout: resolve, the cached listing, and per-site action lists are all
    public endpoints. Nothing here spends a credit, which is the point — narrowing
    should be free so the budget goes on actually running the chosen action.
    """
    limit = limit or settings.max_candidates
    out: dict[str, dict] = {}

    for hit in anakin.resolve(intent, limit=10):
        norm = _normalise(hit)
        if norm["action_id"]:
            out[norm["action_id"]] = norm

    for site in search_sites(intent, limit=4):
        slug = site.get("slug") or ""
        if not slug:
            continue
        for action in anakin.catalog_actions(slug):
            norm = _normalise(action, catalog_slug=slug, domain=site.get("domain") or "")
            if norm["action_id"] and norm["action_id"] not in out:
                out[norm["action_id"]] = norm

    # Actions needing a connected account are last: they can only run through the
    # keyed flow with a credential this agent does not have.
    ranked = sorted(out.values(),
                    key=lambda a: (a["auth_required"], a["credits"]))
    return ranked[:limit]


def describe(action: dict) -> str:
    """One line per candidate for the picker prompt. Params matter as much as the
    description — half the time the parameter names say what a vague blurb does not."""
    req = ", ".join(p.get("name", "?") for p in action["required"]) or "none"
    opt = ", ".join(p.get("name", "?") for p in action["optional"][:6]) or "none"
    bits = [f"{action['action_id']}",
            f"site={action['domain'] or action['catalog'] or '?'}",
            f"cost={action['credits']}cr"]
    if action["auth_required"]:
        bits.append("NEEDS-LOGIN")
    line = "  ".join(bits)
    if action["description"]:
        line += f"\n      {action['description'][:180]}"
    line += f"\n      required: {req}\n      optional: {opt}"
    return line
