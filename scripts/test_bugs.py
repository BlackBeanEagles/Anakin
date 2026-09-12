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

    # ---------------------------------------------------------------------
    # Shipped bug: an out-of-scope case was asked for more detail before anyone
    # checked whether it belonged here at all. A municipal pothole complaint was
    # asked for the pothole's GPS coordinates, and only after the citizen went and
    # found them was it told CPGRAMS is the wrong portal. Scope does not depend on
    # the missing detail, so it must be decided first.
    import app.ladder as L
    calls: list[str] = []
    orig_extract, orig_route, orig_ask = L.extract_facts, L.route_case, L.ask_citizen

    L.extract_facts = lambda *a, **k: (calls.append("extract") or {
        "one_line_summary": "pothole", "sensitive_flags": [], "ready_to_file": False,
        "missing_info": ["exact GPS coordinates"], "amount_inr": 0,
        "reference_numbers": []})
    L.route_case = lambda *a, **k: (calls.append("route") or {
        "out_of_scope": True, "out_of_scope_reason": "municipal, not central",
        "ministry_id": "", "ministry_name": "", "category_id": "",
        "category_name": "", "confidence": 0.9, "rationale": ""})
    L.ask_citizen = lambda *a, **k: calls.append("ask")

    cid = db.create_case(citizen_name="Pothole Tester", citizen_email="p@example.com",
                         citizen_phone="", category="other",
                         narrative_raw="Pothole on my road, BBMP ignored it.")
    L.prepare(db.get_case(cid))

    check("scope is decided before the citizen is asked for anything",
          calls == ["extract", "route"], f"call order was {calls}")
    check("an out-of-scope case is never asked for missing detail",
          "ask" not in calls, str(calls))
    check("an out-of-scope case is closed, not left open",
          db.get_case(cid)["status"] == "closed_unresolved",
          db.get_case(cid)["status"])

    # ... and the in-scope case must still get its question asked.
    calls.clear()
    L.route_case = lambda *a, **k: (calls.append("route") or {
        "out_of_scope": False, "out_of_scope_reason": "", "ministry_id": "DOPOS",
        "ministry_name": "Department of Posts", "category_id": "DOPOS-NONDEL",
        "category_name": "Non-delivery", "confidence": 0.9, "rationale": "r"})
    cid2 = db.create_case(citizen_name="Parcel Tester", citizen_email="q@example.com",
                          citizen_phone="", category="india_post",
                          narrative_raw="Parcel never arrived.")
    L.prepare(db.get_case(cid2))
    check("an in-scope case is still asked for what is missing", "ask" in calls, str(calls))

    L.extract_facts, L.route_case, L.ask_citizen = orig_extract, orig_route, orig_ask

    # ---------------------------------------------------------------------
    # Shipped bug, found by the 52-case eval: the router returned ministry "MOPP"
    # - a plausible-looking code for the pension department that is not in the
    # taxonomy - while getting the category (DPPW-LC) exactly right. Nothing
    # validated it, so the case would have looked up an officer for a ministry that
    # does not exist, found none, and silently lost its email rung.
    from app.agent.route import repair

    r = repair({"out_of_scope": False, "ministry_id": "MOPP",
                "ministry_name": "Ministry of Pension", "category_id": "DPPW-LC",
                "category_name": "Life cert", "confidence": 0.95})
    check("an invented ministry is recovered from its category",
          r["ministry_id"] == "DPPW", r["ministry_id"])
    check("the recovered ministry gets its real name",
          r["ministry_name"].startswith("Department of Pension"), r["ministry_name"])
    check("the repair is recorded, not silent", bool(r.get("repaired")))

    r = repair({"out_of_scope": False, "ministry_id": "DOPOS", "ministry_name": "x",
                "category_id": "MOR-REFUND", "category_name": "y", "confidence": 0.9})
    check("a category from another ministry is dropped, not guessed at",
          r["category_id"] == "" and r["ministry_id"] == "DOPOS",
          f"{r['ministry_id']}/{r['category_id']}")

    r = repair({"out_of_scope": False, "ministry_id": "NOPE", "ministry_name": "x",
                "category_id": "ALSO-NOPE", "category_name": "y", "confidence": 0.95})
    check("an unrecoverable route is emptied and loses its confidence",
          r["ministry_id"] == "" and r["confidence"] <= 0.3,
          f"{r['ministry_id']!r} conf={r['confidence']}")

    r = repair({"out_of_scope": True, "ministry_id": "MOR", "ministry_name": "x",
                "category_id": "MOR-REFUND", "category_name": "y", "confidence": 0.9})
    check("an out-of-scope route carries no ministry or category",
          r["ministry_id"] == "" and r["category_id"] == "")

    # A paraphrased department name must never reach a letterhead.
    r = repair({"out_of_scope": False, "ministry_id": "MOR",
                "ministry_name": "Indian Railways Ministry Dept", "category_id": "MOR-REFUND",
                "category_name": "refunds and stuff", "confidence": 0.9})
    check("names are overwritten from the taxonomy, not trusted",
          "Railway" in r["ministry_name"] and r["category_name"] != "refunds and stuff",
          f"{r['ministry_name']} / {r['category_name']}")

    # ---------------------------------------------------------------------
    # The redirect rail. The demo cases are fictional and the taxonomy points at
    # real named officers, so the gap between "prove the send works" and "mail a
    # fabricated grievance to an Executive Director" is one env var. These guard
    # that the redirect actually redirects, and - just as important - that the
    # ledger never claims a department was contacted when it was not.
    import app.mailer as mailer

    sent_msgs = []

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): sent_msgs.append(msg)

    real_smtp, real_dry = mailer.smtplib.SMTP, settings.dry_run
    real_host, real_redirect = settings.smtp_host, settings.mail_redirect_to
    real_pw = settings.smtp_password
    mailer.smtplib.SMTP = _FakeSMTP
    settings.dry_run = False
    settings.smtp_host = "smtp.test"
    # A host without a password is now treated as half-configured and written to the
    # outbox instead of attempted, so a send test has to supply both.
    settings.smtp_password = "app-password"

    try:
        settings.mail_redirect_to = "me@mine.test"
        ok, detail = mailer.send("edpg@rb.railnet.gov.in", "Appeal", "Body", "PST-TEST")
        m = sent_msgs[-1]
        check("a redirected send actually sends", ok is True)
        check("the envelope goes to the redirect address, not the officer",
              m["To"] == "me@mine.test", m["To"])
        check("the intended recipient survives in a header",
              m["X-Persist-Intended-To"] == "edpg@rb.railnet.gov.in")
        check("the subject says it is a test and names the real target",
              "TEST" in m["Subject"] and "rb.railnet.gov.in" in m["Subject"], m["Subject"])
        check("the body warns nobody was contacted",
              "REDIRECTED" in m.get_content() and "NOT" in detail.upper())
        check("the ledger line does not claim the department was contacted",
              "edpg@rb.railnet.gov.in" not in detail.split("intended recipient")[0],
              detail)

        settings.mail_redirect_to = ""
        ok, detail = mailer.send("edpg@rb.railnet.gov.in", "Appeal", "Body", "PST-TEST")
        m = sent_msgs[-1]
        check("with the redirect off, mail goes to the department",
              m["To"] == "edpg@rb.railnet.gov.in" and "X-Persist-Intended-To" not in m)
        check("an un-redirected send says so plainly",
              detail == "Sent to edpg@rb.railnet.gov.in", detail)

        settings.dry_run = True
        ok, detail = mailer.send("edpg@rb.railnet.gov.in", "Appeal", "Body", "PST-TEST")
        check("DRY_RUN still beats everything",
              ok is False and "Not transmitted" in detail, detail)

        # Half-configured: a host pasted in but the password never filled. Without
        # this the send reaches smtplib and fails the login on every single rung.
        settings.dry_run, settings.smtp_password = False, ""
        ok, detail = mailer.send("edpg@rb.railnet.gov.in", "Appeal", "Body", "PST-TEST")
        check("a missing SMTP password does not reach smtplib",
              ok is False and "Not transmitted" in detail, detail)
        # ...and the public timeline never learns the name of an env var or a file.
        check("the ledger line carries no configuration detail",
              "SMTP_PASSWORD" not in detail and ".txt" not in detail, detail)
    finally:
        mailer.smtplib.SMTP = real_smtp
        settings.dry_run, settings.smtp_host = real_dry, real_host
        settings.mail_redirect_to, settings.smtp_password = real_redirect, real_pw

    # ---------------------------------------------------------------------
    # Auto-approval. The hackathon brief asks for an agent that acts "without a
    # human clicking every button", and every rung used to wait for a click. These
    # guard the line between useful autonomy and mailing a fabricated grievance to a
    # named Executive Director.
    from app import dispatch

    real = dict(dry=settings.dry_run, host=settings.smtp_host,
                redirect=settings.mail_redirect_to,
                rung=settings.auto_approve_through_rung,
                conf=settings.auto_approve_min_confidence)
    try:
        settings.auto_approve_through_rung = 2
        settings.auto_approve_min_confidence = 0.75
        good = {"confidence": 0.95}
        email2 = {"rung": 2, "channel": "email", "recipient": "x@ministry.gov.in"}

        # The hard gate: live SMTP, no redirect, DRY_RUN off = a real department.
        settings.dry_run, settings.smtp_host, settings.mail_redirect_to = (
            False, "smtp.test", "")
        ok, why = dispatch.may_auto_approve({}, email2, good)
        check("the agent will not autonomously mail a real department",
              ok is False and "real department" in why, why)

        # ...and no setting can turn that gate off.
        settings.auto_approve_through_rung = 99
        ok, _ = dispatch.may_auto_approve({}, email2, good)
        check("no rung setting overrides the real-recipient gate", ok is False)
        settings.auto_approve_through_rung = 2

        # With mail redirected, the same action is allowed - that is the point.
        settings.mail_redirect_to = "me@mine.test"
        ok, why = dispatch.may_auto_approve({}, email2, good)
        check("with mail redirected, the agent may act on its own", ok is True, why)

        # Rung ceiling: appeals and above stay human even while redirected.
        ok, why = dispatch.may_auto_approve(
            {}, {"rung": 3, "channel": "email", "recipient": "x@y.gov.in"}, good)
        check("an appeal is never auto-approved", ok is False and "appeal" in why, why)

        # Confidence floor.
        ok, why = dispatch.may_auto_approve({}, email2, {"confidence": 0.4})
        check("a low-confidence route waits for a human",
              ok is False and "confidence" in why, why)

        # A blank address must not be auto-sent - this silently misrouted before.
        ok, why = dispatch.may_auto_approve(
            {}, {"rung": 2, "channel": "email", "recipient": "Department of Posts"}, good)
        check("a department name is not an address", ok is False and "address" in why, why)

        # Phone always needs a person.
        ok, why = dispatch.may_auto_approve(
            {}, {"rung": 2, "channel": "phone", "recipient": "1924"}, good)
        check("nobody auto-places a phone call", ok is False, why)

        # Off switch.
        settings.auto_approve_through_rung = -1
        ok, why = dispatch.may_auto_approve({}, email2, good)
        check("auto-approval is off by default and can be switched off",
              ok is False and "switched off" in why, why)

        # DRY_RUN alone is enough to make the gate pass.
        settings.auto_approve_through_rung = 2
        settings.dry_run, settings.mail_redirect_to = True, ""
        ok, why = dispatch.may_auto_approve({}, email2, good)
        check("under DRY_RUN the agent may drive the ladder", ok is True, why)
    finally:
        settings.dry_run, settings.smtp_host = real["dry"], real["host"]
        settings.mail_redirect_to = real["redirect"]
        settings.auto_approve_through_rung = real["rung"]
        settings.auto_approve_min_confidence = real["conf"]

    # ---------------------------------------------------------------------
    # Two bugs a real test filing found, both of which closed a genuine case.
    #
    # The narrative said "12 August", tracking stopped "14 August", filed in
    # September. The extractor left the date empty and asked "Year of the
    # consignment dispatch" - blocking the filing on a question nobody would
    # bother to answer. Then TIME_SCALE compressed the ask/remind/close cycle
    # into 51 seconds, so the citizen got three emails and a closed case before
    # they could read the first one.
    from datetime import date as _date
    from app.agent.extract import _infer_year

    t = _date(2026, 9, 12)
    f = _infer_year({"incident_date": "12 August",
                     "missing_info": ["Year of the consignment dispatch"]}, today=t)
    check("a bare day-and-month gets the obvious year",
          f["incident_date"] == "2026-08-12", f["incident_date"])
    check("nobody is asked what year they meant", f["missing_info"] == [],
          str(f["missing_info"]))
    check("the inferred year is recorded, not hidden",
          any("Year not stated" in a for a in f.get("assumptions", [])))

    # A month later in the year than today has to mean last year, not the future.
    f = _infer_year({"incident_date": "12 December", "missing_info": []}, today=t)
    check("a future-looking date resolves to last year",
          f["incident_date"] == "2025-12-12", f["incident_date"])

    # A real date and unrelated questions must survive untouched.
    f = _infer_year({"incident_date": "2026-08-12",
                     "missing_info": ["Exact address of the post office"]}, today=t)
    check("a complete date is left alone",
          f["incident_date"] == "2026-08-12" and len(f["missing_info"]) == 1)

    f = _infer_year({"incident_date": "32 Bogember", "missing_info": []}, today=t)
    check("unparseable dates are not mangled", f["incident_date"] == "32 Bogember")

    # The wait floor. A department is not watching its inbox; a citizen who was
    # just asked a question is.
    real_scale = settings.time_scale
    try:
        settings.time_scale = 0.0001
        dept = ladder.wait_until(21)
        cit = ladder.wait_until(ladder.NUDGE_AFTER_DAYS, waiting_on_citizen=True)
        now = db.now()
        dept_gap = (datetime.fromisoformat(dept) - datetime.fromisoformat(now)).total_seconds()
        cit_gap = (datetime.fromisoformat(cit) - datetime.fromisoformat(now)).total_seconds()
        check("a department wait still compresses for the demo", dept_gap < 600,
              f"{dept_gap:.0f}s")
        check("a citizen is never chased faster than real time allows",
              cit_gap >= ladder.MIN_CITIZEN_WAIT_SECONDS, f"{cit_gap:.0f}s")
    finally:
        settings.time_scale = real_scale

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
