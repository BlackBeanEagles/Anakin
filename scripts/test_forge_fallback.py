"""What happens when a build does not land.

    python scripts/test_forge_fallback.py        (needs LLM_PROVIDER=mock)

The forge is the most interesting thing this project does and the least reliable: a
build can be refused outright, fail during generation, hit the pending cap, or still
be running long after anyone stopped watching. None of that may take the project
down with it.

Every one of these paths is exercised offline, because the only other way to test
them is to spend 25 credits provoking a failure on purpose - and the two that matter
most, BLOCKED_WEBSITE and BUILD_FAILED, cannot be provoked on demand at all.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import anakin, db, fallback  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail and not ok else ''}")


class FakeResponse:
    """Just enough httpx.Response for build_request's branching."""

    def __init__(self, status_code: int, text: str = "{}"):
        self.status_code = status_code
        self.text = text

    def json(self):
        import json
        return json.loads(self.text)


def main() -> int:
    db.init()
    import forge  # noqa: E402  - imported late, after sys.path is set

    gap = forge.GAPS["cpgrams"]

    # ---- error codes ---------------------------------------------------
    # Every failure has to arrive as a code, not as English. Branching on message
    # text is how a demo ends up retrying something that will never succeed.
    import app.anakin as an
    original_post = an.httpx.post
    original_configured = an.configured
    original_afford = an._afford
    an.configured = lambda: True
    an._afford = lambda cost, **kw: True

    cases = [
        (409, '{"error":{"code":"ACTION_EXISTS"}}', "ACTION_EXISTS"),
        (429, "{}", "BUILD_LIMIT_REACHED"),
        (400, '{"error":{"code":"BLOCKED_WEBSITE"}}', "BLOCKED_WEBSITE"),
        (402, '{"error":{"code":"INSUFFICIENT_CREDITS"}}', "NO_BUDGET"),
        (500, '{"error":"boom"}', "HTTP_500"),
    ]
    for status, body, want in cases:
        an.httpx.post = lambda *a, s=status, b=body, **k: FakeResponse(s, b)
        try:
            an.build_request("https://example.gov.in", "goal")
            check(f"HTTP {status} raises", False, "no exception")
        except an.AnakinError as exc:
            check(f"HTTP {status} -> {want}", exc.code == want, f"got {exc.code}")

    an.httpx.post = original_post
    an.configured = original_configured
    an._afford = original_afford

    # A missing key must be its own code, not a generic error - it is the one
    # failure that is the operator's fault and instantly fixable.
    an.configured = lambda: False
    try:
        an.build_request("https://example.gov.in", "goal")
        check("missing key raises", False, "no exception")
    except an.AnakinError as exc:
        check("missing key -> NO_KEY", exc.code == "NO_KEY", f"got {exc.code}")
    an.configured = original_configured

    # ---- every emitted code has a human meaning -------------------------
    emitted = {"NO_KEY", "NO_BUDGET", "TRANSPORT", "ACTION_EXISTS",
               "BUILD_LIMIT_REACHED", "BLOCKED_WEBSITE", "BUILD_FAILED", "TIMEOUT"}
    missing = [c for c in emitted if c not in fallback.MEANING]
    check("every failure code has an explanation", not missing, str(missing))

    # Retry advice has to be right, because it is the difference between waiting a
    # minute and burning 25 credits on something that will never work.
    check("BLOCKED_WEBSITE is not retryable", fallback.explain("BLOCKED_WEBSITE")[1] is False)
    check("BUILD_LIMIT_REACHED is retryable", fallback.explain("BUILD_LIMIT_REACHED")[1] is True)
    check("unknown code degrades to retryable",
          fallback.explain("SOMETHING_NEW")[1] is True)

    # ---- the failure is recorded ---------------------------------------
    original_probe = fallback.probe
    fallback.probe = lambda url, **kw: {
        "readable": True, "via": "anakin+browser", "chars": 4200, "reason": "",
        "url": url}

    rc = forge._failed("test-blocked", gap, "BLOCKED_WEBSITE",
                       "site not supported", refunded=False)
    check("a survivable failure exits 0", rc == 0, f"exit {rc}")

    rows = {b["id"]: b for b in db.builds()}
    row = rows.get("test-blocked")
    check("failure is written to the builds table", row is not None)
    if row:
        check("failure records its code", row["code"] == "BLOCKED_WEBSITE",
              str(row["code"]))
        check("failure records the fallback that was found",
              "anakin+browser" in (row["fallback"] or ""), str(row["fallback"]))
        check("failed build is marked failed", row["status"] == "failed",
              str(row["status"]))

    # A timeout is not a failure. Marking it one would make the ledger claim a
    # build died when it may still be running, and would hide a 25-credit charge.
    forge._failed("test-timeout", gap, "TIMEOUT", "still building", refunded=False)
    row = {b["id"]: b for b in db.builds()}.get("test-timeout")
    check("a timeout stays pending, not failed",
          row is not None and row["status"] == "pending",
          str(row and row["status"]))

    # ---- an unreadable site is reported honestly ------------------------
    fallback.probe = lambda url, **kw: {
        "readable": False, "via": "", "chars": 0, "reason": "HTTP 403", "url": url}
    rc = forge._failed("test-dead", gap, "BUILD_FAILED", "generator gave up",
                       refunded=True)
    check("an unreadable site exits non-zero", rc == 1, f"exit {rc}")
    row = {b["id"]: b for b in db.builds()}.get("test-dead")
    check("refund is recorded", row is not None and row["refunded"] == 1)
    check("the dead end says so in the ledger",
          row is not None and "timer" in (row["fallback"] or ""), str(row and row["fallback"]))

    fallback.probe = original_probe

    # ---- summarise reads like a sentence a human wrote ------------------
    good = fallback.summarise({"readable": True, "via": "direct", "chars": 900,
                               "reason": "", "url": "x"})
    bad = fallback.summarise({"readable": False, "via": "", "chars": 0,
                              "reason": "captcha", "url": "x"})
    check("readable summary names the route", "direct" in good, good)
    check("unreadable summary names the fallback", "timer" in bad, bad)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
