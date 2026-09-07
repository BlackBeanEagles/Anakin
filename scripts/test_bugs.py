"""Regression tests for bugs that have actually shipped and been fixed.

    python scripts/test_bugs.py        (needs LLM_PROVIDER=mock)

Each test names the bug it guards. They are here because every one of these was
silent — nothing crashed, the wrong thing just quietly happened — which is the kind
that survives a demo and then bites in front of judges.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, ladder, whatsapp  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import _rate_limited, _recent_submissions, _session_token, _authed  # noqa: E402

POST = ("Speed post EX314159265IN booked on 4 August from Pune to Delhi has never "
        "arrived. Called 1924 twice and visited the office once. Rs 5,000 of documents.")
POTHOLE = ("There is a huge pothole outside my building on 3rd main road and two "
           "scooters have already fallen. BBMP came once, looked, and left.")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail and not ok else ''}")


def new_case(narrative: str, cat: str = "india_post", **kw) -> str:
    return db.create_case(kw.pop("name", "Bug Test"), kw.pop("email", "bug@example.com"),
                          kw.pop("phone", ""), cat, narrative, **kw)


def main() -> int:
    if settings.provider.kind != "mock":
        print("Run with LLM_PROVIDER=mock.")
        return 2
    db.init()

    # ---------------------------------------------------------------------
    # BUG: an email action with no usable recipient fell through the channel
    # dispatch and was logged as "call script approved" — a silent misroute for
    # any ministry with no grievance-officer address on file.
    # ---------------------------------------------------------------------
    cid = new_case(POST)
    ladder.advance(db.get_case(cid))
    aid = db.create_action(cid, rung=2, channel="email", kind="officer",
                           recipient="VERIFY-ME@indiapost.gov.in",
                           subject="s", content="c", status="pending_approval")
    from app.main import approve
    approve(aid, session=_session_token())
    a = db.get_action(aid)
    events = " ".join(e["detail"] for e in db.case_events(cid))
    check("placeholder email address is refused, not sent",
          a["status"] == "pending_approval" and "no usable address" in events,
          f"status={a['status']}")
    check("refused email is not mislabelled as a phone call",
          "Call script approved" not in events)

    aid2 = db.create_action(cid, rung=2, channel="email", kind="officer", recipient="",
                            subject="s", content="c", status="pending_approval")
    approve(aid2, session=_session_token())
    check("blank email address is refused too",
          db.get_action(aid2)["status"] == "pending_approval")

    # ---------------------------------------------------------------------
    # BUG: a case refused as out-of-scope was closed without telling the citizen.
    # Hearing nothing is the exact failure this project exists to fix.
    # ---------------------------------------------------------------------
    cid2 = new_case(POTHOLE, cat="other")
    ladder.advance(db.get_case(cid2))
    c2 = db.get_case(cid2)
    ev2 = [e["kind"] for e in db.case_events(cid2)]
    check("out-of-scope case is closed", c2["status"] == "closed_unresolved")
    check("out-of-scope citizen is actually told", "notify" in ev2, f"events={ev2}")
    check("out-of-scope reason is recorded on the case",
          bool(c2["outcome"]) and len(c2["outcome"]) > len("Out of scope for CPGRAMS"))

    # ---------------------------------------------------------------------
    # BUG: any case matching a phone number was treated as live, so a closed case
    # swallowed every future message from that number.
    # ---------------------------------------------------------------------
    phone = "whatsapp:+919812345678"
    whatsapp.handle(phone, POST)
    whatsapp.handle(phone, "YES")
    first = whatsapp._open_case_for("+919812345678")
    db.update_case(first["id"], status="resolved", next_action_at=None)

    reply = whatsapp.handle(phone, "Different problem entirely: my IRCTC refund for PNR "
                                   "1234509876 has not come back after three weeks.")
    live = whatsapp._open_case_for("+919812345678")
    check("a resolved case does not swallow the next WhatsApp message",
          live is not None and live["id"] != first["id"],
          f"still routed to {first['id']}")
    check("the new WhatsApp case asks for consent again",
          live is not None and live["status"] == "needs_consent")
    check("consent is never assumed from a message",
          live is not None and not live["consent_at"])

    # ---------------------------------------------------------------------
    # BUG: the intake rate limiter never evicted expired IPs, so the dict grew for
    # every visitor across a week-long run.
    # ---------------------------------------------------------------------
    _recent_submissions.clear()
    for i in range(50):
        _rate_limited(f"10.0.0.{i}", limit=3, window=1)
    grew = len(_recent_submissions)
    time.sleep(1.1)
    _rate_limited("10.0.1.1", limit=3, window=1)
    check("rate limiter evicts expired IPs",
          len(_recent_submissions) < grew, f"{grew} -> {len(_recent_submissions)}")
    check("rate limiter still actually limits",
          all(_rate_limited("10.9.9.9", limit=3, window=60) is False for _ in range(3))
          and _rate_limited("10.9.9.9", limit=3, window=60) is True)

    # ---------------------------------------------------------------------
    # BUG: "days elapsed since filing" was measured from updated_at, which is
    # rewritten on every save — so escalation emails said 0 days.
    # ---------------------------------------------------------------------
    from datetime import datetime, timedelta, timezone
    cid3 = new_case(POST)
    past = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    db.update_case(cid3, filed_at=past, cpgrams_reg_no="DOPOS/E/2026/1", rung=1)
    db.update_case(cid3, outcome="touched")          # bump updated_at
    c3 = db.get_case(cid3)
    check("days-pending survives an unrelated write",
          ladder._days_pending(c3) > 0,
          f"got {ladder._days_pending(c3)} days")

    # ---------------------------------------------------------------------
    # BUG: reopening a blocked case left the stale blocking questions attached.
    # ---------------------------------------------------------------------
    cid4 = new_case(POST)
    ladder.ask_citizen(cid4, ["What is the tracking number?"])
    ladder.resume_with_more_info(cid4, "The tracking number is EX111222333IN.")
    c4 = db.get_case(cid4)
    check("reopening clears stale blocking questions",
          not c4["info_questions"] and c4["info_asks"] == 0 and c4["status"] == "intake")

    # ---------------------------------------------------------------------
    # Taxonomy contacts: no placeholders, no invented addresses, and a department
    # with no published address must SKIP the email rung rather than stall.
    # ---------------------------------------------------------------------
    from app.agent.route import load_taxonomy
    from app.main import _sendable
    tax = load_taxonomy()
    mins = tax["ministries"]

    check("no VERIFY-ME placeholders remain",
          not [m["id"] for m in mins if "VERIFY-ME" in (m.get("grievance_officer_email") or "")])
    check("officer contacts are marked verified with a source",
          tax["_meta"].get("officers_verified") and tax["_meta"].get("officers_source"))
    check("every ministry declares an email_kind",
          all(m.get("email_kind") in ("role", "individual", "none") for m in mins),
          str([m["id"] for m in mins if m.get("email_kind") not in ("role", "individual", "none")]))
    check("declared addresses are actually sendable",
          all(_sendable(m["grievance_officer_email"])
              for m in mins if m["email_kind"] != "none"))
    check("'none' ministries really have no address",
          all(not m["grievance_officer_email"] for m in mins if m["email_kind"] == "none"))

    mea = next(m for m in mins if m["id"] == "MEA")   # phone listed, no email
    cid5 = new_case("My passport file BN1234567890123 applied 2 July is still stuck at police "
                    "verification and I have a job abroad next month.", cat="passport")
    db.update_case(cid5, routing_json=__import__("json").dumps({
        "out_of_scope": False, "ministry_id": "MEA", "ministry_name": mea["name"],
        "category_id": "MEA-PASSPORT", "category_name": "Passport issuance delay",
        "confidence": 0.9, "rationale": "test", "alternates": [], "applicable_rule": "",
        "out_of_scope_reason": "",
    }), facts_json="{}", rung=1, status="awaiting_response",
        cpgrams_reg_no="MEA/E/2026/1", filed_at=db.now())
    ladder.climb(db.get_case(cid5))
    after = db.get_case(cid5)
    queued = [a for a in db.case_actions(cid5) if a["status"] == "pending_approval"]
    ev5 = " ".join(e["detail"] for e in db.case_events(cid5))
    check("a department with no email skips the email rung",
          "skipped" in ev5 and after["rung"] > 2, f"rung={after['rung']}")
    check("it lands on a rung it can actually action",
          bool(queued) and queued[-1]["channel"] != "email",
          f"channel={queued[-1]['channel'] if queued else 'none'}")

    # ---------------------------------------------------------------------
    # BUG: officer designations contain parentheses ("ADG (PG)"), which are RFC 5322
    # comment syntax — concatenated into a To: header raw, they are silently eaten.
    # ---------------------------------------------------------------------
    from email.utils import parseaddr
    posts = next(m for m in mins if m["id"] == "DOPOS")
    addr = ladder._addressee(posts)
    display, email = parseaddr(addr)
    check("addressee survives an RFC round trip",
          email == posts["grievance_officer_email"]
          and posts["officer_designation"] in display,
          f"{addr!r} -> {display!r}")
    check("addressee with no email is empty, not a bare name",
          ladder._addressee({"officer_name": "X", "grievance_officer_email": ""}) == "")
    check("addressee is still sendable",
          _sendable(addr))

    # ---------------------------------------------------------------------
    # The per-ministry notes must reach the router, or the "faster route" advice
    # (EPFiGMS, CPENGRAMS, AirSewa, RBI ombudsman) is invisible to everyone.
    # ---------------------------------------------------------------------
    from app.agent.route import _taxonomy_for_prompt
    prompt = _taxonomy_for_prompt()
    check("ministry notes reach the routing prompt",
          "EPFiGMS" in prompt and "NOTE:" in prompt)
    check("dedicated portals reach the routing prompt",
          "DEDICATED PORTAL" in prompt)

    # ---------------------------------------------------------------------
    # Session tokens must not be the password.
    # ---------------------------------------------------------------------
    check("session cookie is not the console password",
          _session_token() != settings.console_password and _authed(_session_token())
          and not _authed(settings.console_password))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
