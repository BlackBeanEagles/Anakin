"""Tests for the live-web layer.

The politeness rules and the failure behaviour matter more than the happy path here.
This code points at public-sector servers, and the ladder must never stall because a
page moved or a captcha appeared - degrading to the timer is the required behaviour,
not a fallback we tolerate.

Network tests are tolerant: if the machine is offline they report SKIP rather than
failing the suite.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testenv  # noqa: E402  - seals the mail rail; must precede app imports

_testenv.assert_sealed()  # refuses to run if anything could transmit

from app import db, watch, web  # noqa: E402
from app.config import settings  # noqa: E402

results: list[tuple[str, bool | None, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    results.append((name, ok, detail))
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print(f"  {tag}  {name}{('  — ' + detail) if detail and ok is False else ''}")


def main() -> int:
    db.init()   # the credit ledger lives here; the fetch layer reads it
    # ---------------- offline behaviour ----------------
    web.reset_tick_budget()

    page = web.Page("http://x", ok=True, status=200,
                    text="<html><body>Error 404 Resource you are looking for is not found</body></html>")
    check("soft 404 is recognised as an error page", web._looks_like_error_page(page))

    long_page = web.Page("http://x", ok=True, status=200,
                         text="<p>" + ("real content about a grievance. " * 60) + "error 404</p>")
    check("a long page merely mentioning 404 is not flagged",
          not web._looks_like_error_page(long_page))

    html = web.Page("http://x", ok=True, status=200,
                    text="<html><head><style>p{color:red}</style></head>"
                         "<body><script>var a=1</script><p>Status: Under&nbsp;process</p></body></html>")
    text = html.short
    check("script and style are stripped from readable text",
          "var a" not in text and "color:red" not in text and "Under process" in text,
          repr(text))

    # budget cap: a bug must not become a flood at a government server
    web._tick_budget = 0
    blocked = web.get("https://example.invalid/never", use_cache=False)
    check("fetch budget caps requests per tick",
          not blocked.ok and "budget" in blocked.reason)
    web.reset_tick_budget()

    check("a failed fetch is never fatal",
          web.get("https://this-host-does-not-exist.invalid/x", use_cache=False).ok is False)

    # the watcher must stay out of the way when it cannot help
    saved = settings.web_reading_enabled
    settings.web_reading_enabled = False
    check("watcher is a no-op when web reading is disabled",
          watch.check_case({"id": "X", "facts_json": "{}", "cpgrams_reg_no": "A/B/1"}) is None)
    settings.web_reading_enabled = saved

    check("watcher is a no-op under the mock provider",
          settings.provider.kind != "mock"
          or watch.check_case({"id": "X", "facts_json": "{}", "cpgrams_reg_no": "A/B/1"}) is None)

    # ---------------- live network ----------------
    web.reset_tick_budget()
    probe = web.get(settings.nodal_directory_url, use_cache=False)
    if not probe.ok:
        check("live: officer directory reachable", None, probe.reason)
        check("live: contacts still listed", None, "offline")
    else:
        check("live: officer directory reachable", True)
        r = watch.refresh_officer_directory()
        # Every contact was taken from this page, so drift should be zero. A non-zero
        # result here means either the directory changed (act on it) or the matching
        # broke (fix it) - both are worth failing for.
        check("live: every contact on file is still in the directory",
              r["ok"] and not r["drift"],
              f"drift={[d['ministry_id'] for d in r['drift']]}")

        gated = web.get(settings.cpgrams_status_url, use_cache=False)
        check("live: status page reachable (results are captcha-gated by design)",
              gated.ok, gated.reason)

    failed = [r for r in results if r[1] is False]
    skipped = [r for r in results if r[1] is None]
    print(f"\n{len(results) - len(failed) - len(skipped)}/{len(results)} passed"
          + (f", {len(skipped)} skipped (offline)" if skipped else "") + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
