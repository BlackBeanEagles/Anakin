"""Anakin.io — the web-reading layer, and the reason a captcha is no longer the end.

Persist reads two kinds of page that a plain HTTP GET is bad at: CPGRAMS status
screens, which are session- and captcha-gated, and India Post tracking, which is a
JS-rendered ASP.NET form. `web.py` degrades politely when those fail, which is honest
but leaves the ladder running on a timer with no evidence to cite.

Anakin closes that gap. It is a web-data API: residential proxy routing, a headless
browser on demand, and Wire — roughly five thousand pre-built actions across 991
sites, each one a real function with a parameter schema and a published credit price.

Three things live here:

  scrape()        one page, through their proxy pool, optionally in a real browser.
                  This is what web.py escalates to when a direct fetch is refused.

  resolve()       ask the catalog "is there already an action for this intent?" and
                  get back action ids, their parameter schemas, and what they cost.
                  Free, keyless, and the closest thing to runtime tool discovery.

  build_request() when the catalog comes back empty — as it does for pgportal.gov.in,
                  which nothing in the catalog covers — describe the site in English
                  and their builder writes the scraper, tests it against the live
                  site, and publishes it. The agent ends the run holding a tool that
                  did not exist when the run began.

Credits are the constraint that shapes all of it. The free tier is 300; a scrape is
1, a build is 25. So every call is metered into the `credits` table before it goes
out, the budget is refused rather than exceeded, and the free paths — cache, direct
fetch, Zero Touch — are always tried before a paid one. The ledger is public for the
same reason the case ledger is: a number you can audit is worth more than a claim.
"""
import json
import logging
import sqlite3
import time

import httpx

from .config import settings

log = logging.getLogger("persist.anakin")

TIMEOUT_SECONDS = 120.0          # /scrape holds the connection open up to ~90s
BUILD_POLL_SECONDS = 15.0
BUILD_COST = 25                  # published price; refunded automatically on failure


class AnakinError(RuntimeError):
    """A call did not come back usable. Never fatal — callers fall back.

    `code` carries the machine-readable reason so callers can branch on it rather
    than pattern-matching English. That matters most for builds: BLOCKED_WEBSITE
    means stop asking, BUILD_LIMIT_REACHED means come back in a few minutes, and
    ACTION_EXISTS means go and use what is already there. Treating all three as
    "it broke" is how a demo dies on the wrong one.
    """

    def __init__(self, message: str, code: str = "ERROR"):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ availability

def configured() -> bool:
    """True when a key is present. Without one, only the free paths work."""
    return bool(settings.anakin_api_key)


def _headers() -> dict:
    return {"X-API-Key": settings.anakin_api_key, "Content-Type": "application/json"}


def _url(path: str) -> str:
    return settings.anakin_base_url.rstrip("/") + path


# ------------------------------------------------------------------ the ledger

def spent() -> int:
    """Credits committed so far. Reads the ledger rather than a counter in memory, so
    a restart mid-hackathon cannot quietly reset the budget."""
    from . import db
    with db.connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(credits), 0) AS n FROM credits").fetchone()
    return int(row["n"] or 0)


def remaining() -> int:
    return max(0, settings.anakin_credit_budget - spent())


def _meter(endpoint: str, credits: int, detail: str, *, case_id: str = "",
           ok: bool = True) -> None:
    from . import db
    try:
        _write_meter(db, endpoint, credits, detail, case_id, ok)
    except sqlite3.Error as exc:
        # A lost ledger row is bad; a crashed read is worse. Log loudly and let the
        # fetch finish - the budget check above already failed closed if it mattered.
        log.error("anakin: could not record %d credits for %s: %s",
                  credits, endpoint, exc)


def _write_meter(db, endpoint: str, credits: int, detail: str, case_id: str,
                 ok: bool) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO credits (ts, case_id, endpoint, detail, credits, ok) "
            "VALUES (?,?,?,?,?,?)",
            (db.now(), case_id or None, endpoint, detail[:400], credits, int(ok)),
        )


def case_spent(case_id: str) -> int:
    from . import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(credits), 0) AS n FROM credits WHERE case_id = ?",
            (case_id,)).fetchone()
    return int(row["n"] or 0)


def _afford(cost: int, *, case_id: str = "") -> bool:
    """Two ceilings, and both have to hold.

    Fails closed. If the ledger cannot be read at all - an uninitialised database, a
    schema older than the credits table, a locked file - this refuses rather than
    assuming the budget is intact. Spending money you cannot account for is the one
    outcome worse than not reading a page, and until a key was configured this path
    was simply never reached: the first real use of it crashed the whole fetch layer
    on a missing table, which is exactly the failure the ladder is built to avoid.

    The global one protects the week. The per-case one protects it from a single
    stubborn grievance: a case that gets watched every tick for six days would
    quietly consume the entire budget on its own, and the first anyone would know
    is a demo that cannot read anything.
    """
    if cost <= 0:
        return True
    try:
        left = remaining()
        used_here = case_spent(case_id) if case_id else 0
    except sqlite3.Error as exc:
        log.warning("anakin: cannot read the credit ledger (%s) - refusing the call", exc)
        return False

    if left < cost:
        log.warning("anakin: refusing %d-credit call, only %d left of %d",
                    cost, left, settings.anakin_credit_budget)
        return False
    if case_id and used_here + cost > settings.anakin_case_cap:
        log.info("anakin: case %s has used its %d-credit share",
                 case_id, settings.anakin_case_cap)
        return False
    return True


def ledger(limit: int = 100) -> list[dict]:
    """Recent spend, newest first — rendered on the public ledger page."""
    from . import db
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM credits ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ scraping

class Scrape:
    __slots__ = ("ok", "markdown", "html", "json", "reason", "cached", "credits")

    def __init__(self, ok, markdown="", html="", json_=None, reason="",
                 cached=False, credits=0):
        self.ok, self.markdown, self.html = ok, markdown, html
        self.json, self.reason = json_, reason
        self.cached, self.credits = cached, credits


def scrape(url: str, *, use_browser: bool = False, generate_json: bool = False,
           country: str = "", case_id: str = "") -> Scrape:
    """Read one page through Anakin. Never raises.

    `useBrowser` costs no extra credits — their pricing charges per URL, not per
    engine — so the only reason not to reach for it immediately is latency. AI JSON
    extraction does cost +2, which is why the model in watch.py keeps doing the
    reading: that reasoning is already paid for.
    """
    if not configured():
        return Scrape(False, reason="no ANAKIN_API_KEY configured")

    cost = 1 + (2 if generate_json else 0)
    if not _afford(cost, case_id=case_id):
        return Scrape(False, reason=f"credit budget exhausted "
                                    f"({spent()}/{settings.anakin_credit_budget})")

    body = {
        "url": url,
        "country": country or settings.anakin_country,
        "useBrowser": use_browser,
        "generateJson": generate_json,
    }
    try:
        r = httpx.post(_url("/v1/url-scraper/scrape"), headers=_headers(),
                       json=body, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        _meter("url-scraper", 0, f"{url} — transport error: {exc}",
               case_id=case_id, ok=False)
        return Scrape(False, reason=f"{type(exc).__name__}: {exc}")

    if r.status_code == 402:
        _meter("url-scraper", 0, f"{url} — out of credits upstream",
               case_id=case_id, ok=False)
        return Scrape(False, reason="Anakin reports insufficient credits")
    if r.status_code >= 400:
        _meter("url-scraper", 0, f"{url} — HTTP {r.status_code}",
               case_id=case_id, ok=False)
        return Scrape(False, reason=f"HTTP {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except json.JSONDecodeError:
        return Scrape(False, reason="response was not JSON")

    # A 202 means the scrape ran past the wait budget and wants polling. Credits are
    # already committed at that point, so meter it honestly and fall back for this
    # tick; their cache will serve it when the watcher comes round again.
    status = (data.get("status") or "").lower()
    if status != "completed":
        _meter("url-scraper", cost, f"{url} — status={status or 'unknown'}",
               case_id=case_id, ok=False)
        return Scrape(False, reason=f"scrape not completed (status={status or 'unknown'})")

    cached = bool(data.get("cached"))
    # A cache hit is served without charge on their side; don't bill ourselves for it.
    charged = 0 if cached else cost
    md = data.get("markdown") or ""
    _meter("url-scraper", charged,
           f"{url} — {len(md)} chars"
           + (" (cached)" if cached else "")
           + (" +browser" if use_browser else ""),
           case_id=case_id)
    return Scrape(True, markdown=md, html=data.get("html") or "",
                  json_=data.get("generatedJson"), cached=cached, credits=charged)


# ------------------------------------------------------------------ Wire discovery

def resolve(intent: str, *, limit: int = 8) -> list[dict]:
    """Ask the catalog which pre-built actions match an intent.

    Free and keyless — discovery is public. Each result carries the action_id, its
    required and optional parameters with defaults, and its credit price, which is
    everything needed to bind it as a tool at runtime.

    Worth knowing before trusting it: this is a fuzzy text match, not a semantic one.
    Asking for the Indian grievance portal returns an Indian sports shop, because
    both descriptions contain the word "portal". Callers must rerank.
    """
    try:
        r = httpx.get(_url("/v1/wire/resolve"),
                      params={"q": intent, "limit": limit}, timeout=30.0)
        r.raise_for_status()
        return r.json().get("results") or []
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.info("anakin: resolve(%r) failed: %s", intent, exc)
        return []


def catalog() -> list[dict]:
    """The whole published catalog: every site, its category, its action count.

    Free and keyless. catalog.py caches this to disk — a megabyte re-fetched per
    lookup would make discovery slower than the scraping it is meant to avoid.
    """
    try:
        r = httpx.get(_url("/v1/wire/catalog"), timeout=90.0)
        r.raise_for_status()
        return r.json().get("catalog") or []
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.info("anakin: catalog fetch failed: %s", exc)
        return []


def catalog_actions(slug: str) -> list[dict]:
    """One site's full action list with parameter schemas. Free.

    The honest way to see what a site can do: resolve() only ever shows its top few
    guesses, while a site's own listing is complete.
    """
    try:
        r = httpx.get(_url(f"/v1/wire/catalog/{slug}"), timeout=45.0)
        r.raise_for_status()
        data = r.json()
        return data.get("actions") or (data.get("catalog") or {}).get("actions") or []
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.info("anakin: catalog/%s failed: %s", slug, exc)
        return []


def covers(domain: str) -> list[dict]:
    """Does the catalog already have a site? Definitive, unlike resolve().

    resolve() guesses; this filters the published catalog on the domain, so an empty
    list is real evidence that a build request is warranted rather than a search that
    happened to miss. That distinction is what stops the agent spending 25 credits
    on a gap that was never there.
    """
    from . import catalog as _catalog
    want = domain.lower().removeprefix("www.").strip()
    return [c for c in _catalog.load() if want and want in (c.get("domain") or "").lower()]


def task(action_id: str, params: dict, *, credits: int = 1, case_id: str = "",
         credential_id: str = "") -> dict | None:
    """Run a Wire action through the keyed, asynchronous flow and wait for the job.

    Only reached when Zero Touch will not serve — write actions, and anything that
    needs a connected account.
    """
    if not configured() or not _afford(credits, case_id=case_id):
        return None
    body: dict = {"action_id": action_id, "params": params}
    if credential_id:
        body["credential_id"] = credential_id
    try:
        r = httpx.post(_url("/v1/wire/task"), headers=_headers(), json=body, timeout=60.0)
    except httpx.HTTPError as exc:
        _meter("wire/task", 0, f"{action_id} — transport: {exc}", case_id=case_id, ok=False)
        return None
    if r.status_code >= 400:
        _meter("wire/task", 0, f"{action_id} — HTTP {r.status_code}", case_id=case_id, ok=False)
        return None
    payload = r.json() or {}
    job_id = payload.get("job_id") or payload.get("id")
    if not job_id:
        return None
    _meter("wire/task", credits, action_id, case_id=case_id)
    return job(job_id)


def job(job_id: str, *, timeout_seconds: float = 180.0) -> dict | None:
    """Poll one Wire job to a terminal state."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            r = httpx.get(_url(f"/v1/wire/jobs/{job_id}"), headers=_headers(), timeout=30.0)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            log.info("anakin: job %s poll failed: %s", job_id, exc)
            return None
        state = (data.get("status") or "").lower()
        if state in {"completed", "success", "succeeded"}:
            return data
        if state in {"failed", "error", "cancelled"}:
            return None
        time.sleep(2.0)
    return None


def zero_touch(action_id: str, params: dict | None = None) -> dict | None:
    """Run a read-only Wire action with no key and no credits (Zero Touch).

    Tried before any paid path for exactly that reason. Write actions and
    account-connected runs are not available here — those need the keyed task flow.
    """
    try:
        r = httpx.post(_url("/v1/wire-run"),
                       json={"action_id": action_id, "params": params or {}},
                       headers={"Content-Type": "application/json"}, timeout=90.0)
        if r.status_code >= 400:
            log.info("anakin: zero-touch %s -> HTTP %s", action_id, r.status_code)
            return None
        _meter("wire-run", 0, f"{action_id} — zero touch (free)")
        return r.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.info("anakin: zero-touch %s failed: %s", action_id, exc)
        return None


# ------------------------------------------------------------------ Wire building

def build_request(website_url: str, goal: str, *, visibility: str = "public",
                  force: bool = False, case_id: str = "") -> dict:
    """Ask Wire to build an action that does not exist yet.

    This is the one call that changes something on Anakin's side rather than just
    reading it, and at 25 credits it is the most expensive thing Persist can do — a
    twelfth of the entire free tier per attempt. It is never invoked automatically
    from the ladder: a human runs it, or the console asks first.

    Credits are refunded when a build fails, so the real cost is only paid on
    success. Concurrency is capped upstream at three pending builds.

    visibility="public" publishes the action into the shared catalog: the site
    becomes readable by every other Anakin user, not just this account.
    """
    if not configured():
        raise AnakinError("no ANAKIN_API_KEY configured", "NO_KEY")
    if not _afford(BUILD_COST):
        raise AnakinError(
            f"budget exhausted: {spent()}/{settings.anakin_credit_budget} used",
            "NO_BUDGET")

    payload = {"website_url": website_url, "goal": goal, "visibility": visibility}
    if force:
        payload["force"] = True
    try:
        r = httpx.post(_url("/v1/wire/build-request"), headers=_headers(),
                       json=payload, timeout=60.0)
    except httpx.HTTPError as exc:
        raise AnakinError(f"transport error: {exc}", "TRANSPORT") from exc

    if r.status_code == 409:
        # Something similar already exists. Not a failure — it means we should be
        # using the existing action rather than paying to duplicate it.
        raise AnakinError(f"ACTION_EXISTS: {r.text[:400]}", "ACTION_EXISTS")
    if r.status_code == 429:
        raise AnakinError("three builds already pending", "BUILD_LIMIT_REACHED")
    if r.status_code >= 400:
        # The upstream body names the reason; BLOCKED_WEBSITE is the one that
        # decides whether a government portal is buildable at all, so it is lifted
        # out rather than buried in an HTTP status.
        body = r.text[:400]
        code = "BLOCKED_WEBSITE" if "BLOCKED_WEBSITE" in body.upper() else (
            "NO_BUDGET" if r.status_code == 402 else f"HTTP_{r.status_code}")
        raise AnakinError(f"HTTP {r.status_code}: {body}", code)

    br = (r.json() or {}).get("build_request") or {}
    charged = int(br.get("credits_charged") or BUILD_COST)
    _meter("build-request", charged, f"{website_url} — {goal[:160]}", case_id=case_id)
    return br


def build_requests() -> list[dict]:
    """Every build this account has asked for, with status and action_id once live."""
    if not configured():
        return []
    try:
        r = httpx.get(_url("/v1/wire/build-requests"), headers=_headers(), timeout=30.0)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.info("anakin: listing build requests failed: %s", exc)
        return []
    if isinstance(data, list):
        return data
    return data.get("build_requests") or data.get("results") or []


def await_build(build_id: str, *, timeout_seconds: float = 900.0) -> dict | None:
    """Block until a build lands. Returns the finished record, or None on timeout.

    No completion time is published, so the timeout is a guess and the caller has to
    survive it being wrong. On failure the record carries `error` and the 25 credits
    come back on their own — mirrored into our ledger so the running total stays true.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for br in build_requests():
            if br.get("id") != build_id:
                continue
            state = (br.get("status") or "").lower()
            if state in {"success", "failed"}:
                if state == "failed":
                    _meter("build-request", -BUILD_COST,
                           f"refund — build {build_id} failed: "
                           f"{str(br.get('error') or '')[:200]}", ok=False)
                return br
        time.sleep(BUILD_POLL_SECONDS)
    return None
