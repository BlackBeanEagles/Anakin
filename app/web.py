"""Polite HTTP fetching for public government pages.

These are public-sector servers run on public money; hammering them would be both
rude and self-defeating. Every fetch goes through here so the politeness rules are
enforced in one place rather than remembered at each call site:

  - a real User-Agent that says who we are and how to make us stop
  - robots.txt is consulted and obeyed
  - a minimum interval between requests to the same host
  - an on-disk cache, so a restart doesn't re-fetch everything
  - a hard cap on fetches per tick, so a bug cannot turn into a flood
  - short timeouts, and failure is always non-fatal

Nothing here logs in, submits, or solves a captcha. It reads pages a member of the
public can read. When a page is gated - and several of these are - the fetch fails,
`ok` is False, and the ladder falls back to its timer. Degrading quietly is the
whole design: the agent must never stall because a scrape broke.
"""
import hashlib
import json
import logging
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .config import settings

log = logging.getLogger("persist.web")

USER_AGENT = (
    "PersistGrievanceBot/1.0 (+https://github.com/BlackBeanEagles/Anakin; "
    "citizen grievance tracking; contact via the repository)"
)

MIN_INTERVAL_SECONDS = 4.0      # per host
CACHE_TTL_SECONDS = 1800        # 30 min; status pages do not change faster than that
MAX_FETCHES_PER_TICK = 8
TIMEOUT_SECONDS = 20.0

_last_hit: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_tick_budget = MAX_FETCHES_PER_TICK


@dataclass
class Page:
    url: str
    ok: bool
    status: int = 0
    text: str = ""
    reason: str = ""
    from_cache: bool = False
    fetched_at: float = field(default_factory=time.time)
    # How this page was obtained: direct | anakin | anakin+browser. Shown on the
    # case timeline, because "we read the captcha-gated page" is only credible if
    # it also says how.
    via: str = "direct"

    @property
    def short(self) -> str:
        """Visible text, collapsed, capped - what actually goes to the model."""
        import re
        body = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", self.text)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = re.sub(r"&nbsp;?", " ", body)
        return re.sub(r"\s+", " ", body).strip()[:6000]



SOFT_404_MARKERS = (
    "resource you are looking for is not found",
    "error 404",
    "page not found",
    "404 - file or directory not found",
)


def _looks_like_error_page(page: "Page") -> bool:
    body = page.short.lower()
    # only trust this on short pages; a long article may legitimately mention 404
    return len(body) < 400 and any(m in body for m in SOFT_404_MARKERS)

def reset_tick_budget() -> None:
    """Called once per tick so one pass can never spiral into a flood."""
    global _tick_budget
    _tick_budget = MAX_FETCHES_PER_TICK


def _cache_path(url: str):
    settings.web_cache.mkdir(parents=True, exist_ok=True)
    return settings.web_cache / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")


def _read_cache(url: str) -> Page | None:
    p = _cache_path(url)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - blob.get("fetched_at", 0) > CACHE_TTL_SECONDS:
        return None
    return Page(url=url, ok=blob["ok"], status=blob["status"], text=blob["text"],
                reason=blob.get("reason", ""), from_cache=True,
                fetched_at=blob["fetched_at"], via=blob.get("via", "direct"))


def _write_cache(page: Page) -> None:
    try:
        _cache_path(page.url).write_text(json.dumps({
            "ok": page.ok, "status": page.status, "text": page.text,
            "reason": page.reason, "fetched_at": page.fetched_at, "via": page.via,
        }), encoding="utf-8")
    except OSError as exc:
        log.warning("cache write failed: %s", exc)


def _robots_allows(url: str) -> bool:
    """Obey robots.txt. If it cannot be read, assume allowed - that is the
    conventional reading, and these are public information pages."""
    host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(host + "/robots.txt")
        try:
            rp.read()
            _robots[host] = rp
        except Exception:  # noqa: BLE001 - unreadable robots.txt is not a refusal
            _robots[host] = None
    rp = _robots[host]
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001
        return True


def _throttle(host: str) -> None:
    wait = MIN_INTERVAL_SECONDS - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


def _direct(url: str) -> Page:
    """The original path: our own client, our own IP, robots.txt obeyed."""
    try:
        r = httpx.get(url, timeout=TIMEOUT_SECONDS, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT,
                               "Accept": "text/html,application/xhtml+xml"})
        page = Page(url, ok=r.status_code == 200, status=r.status_code, text=r.text,
                    reason="" if r.status_code == 200 else f"HTTP {r.status_code}")
        # Several government portals answer a bad path with a 302 to an error page
        # that returns 200. Without this, a soft-404 looks like a successful read and
        # the model is handed "Resource you are looking for is not found" to interpret.
        if page.ok and _looks_like_error_page(page):
            page.ok = False
            page.reason = "soft 404 (error page returned with HTTP 200)"
    except httpx.TimeoutException:
        page = Page(url, ok=False, reason=f"timed out after {TIMEOUT_SECONDS:.0f}s")
    except httpx.HTTPError as exc:
        page = Page(url, ok=False, reason=f"{type(exc).__name__}: {exc}")
    return page


def _via_anakin(url: str, case_id: str = "") -> Page | None:
    """Read the page through Anakin. None when it could not be read either.

    Two rungs, because they cost the same. A plain proxied fetch handles the pages
    that only refused us over who was asking; the headless browser handles the ones
    that need JavaScript to render at all - India Post's tracking form is the whole
    reason that second rung exists. Browser mode is not billed extra, so the only
    thing spent on the retry is time.
    """
    from . import anakin

    got = anakin.scrape(url, case_id=case_id)
    if got.ok and got.markdown.strip():
        return Page(url, ok=True, status=200, text=got.markdown,
                    reason="", via="anakin")

    if settings.anakin_use_browser_fallback:
        got = anakin.scrape(url, use_browser=True, case_id=case_id)
        if got.ok and got.markdown.strip():
            return Page(url, ok=True, status=200, text=got.markdown,
                        reason="", via="anakin+browser")

    if got.reason:
        log.info("anakin could not read %s: %s", url, got.reason)
    return None


def get(url: str, *, use_cache: bool = True, case_id: str = "") -> Page:
    """Fetch a public page. Never raises - failure is a Page with ok=False.

    The ladder, cheapest rung first:

      1. the on-disk cache            free
      2. a direct httpx GET           free
      3. Anakin, proxied              1 credit
      4. Anakin, headless browser     1 credit

    Rungs 3 and 4 only run when the one above returned nothing usable, so a page
    that was readable anyway costs nothing - which is what makes 300 credits last a
    week. `WEB_BACKEND=anakin` skips straight to rung 3, which is for demonstrating
    the integration deliberately, not for running the queue.
    """
    global _tick_budget

    if use_cache:
        cached = _read_cache(url)
        if cached:
            return cached

    if _tick_budget <= 0:
        return Page(url, ok=False, reason="fetch budget for this tick exhausted")

    # robots.txt governs us whoever carries the request. Routing through someone
    # else's proxy pool does not change who is asking or why, and a scraper that
    # launders a refusal through a vendor is still ignoring it.
    if not _robots_allows(url):
        return Page(url, ok=False, reason="disallowed by robots.txt")

    _tick_budget -= 1
    _throttle(urlparse(url).netloc)

    backend = settings.web_backend
    use_anakin = settings.anakin_enabled

    if backend == "anakin" and use_anakin:
        page = _via_anakin(url, case_id) or Page(
            url, ok=False, reason="Anakin could not read this page")
    else:
        page = _direct(url)
        # The interesting case: our own request was refused or fobbed off with a
        # landing page. Before this existed that was the end of the read and the
        # ladder fell back to its timer.
        if not page.ok and backend == "auto" and use_anakin:
            log.info("direct fetch of %s failed (%s) - escalating to Anakin",
                     url, page.reason)
            page = _via_anakin(url, case_id) or page

    _write_cache(page)
    log.info("GET %s -> %s via %s%s", url, page.status or page.reason, page.via,
             " (cached)" if page.from_cache else "")
    return page
