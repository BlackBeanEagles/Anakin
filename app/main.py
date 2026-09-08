"""Persist - web surface.

Three faces:
  /          public dashboard - the moat artifact. Every case, every action, live.
  /file      citizen intake
  /console   the approval gate. Nothing outbound happens without a click here.
"""
import hashlib
import hmac
import logging
import secrets
import time
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Cookie, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, inbox, ladder, mailer, watch, web, whatsapp
from .config import ROOT, settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("persist")

scheduler = BackgroundScheduler(timezone="UTC")
_SESSION_SALT = secrets.token_bytes(32)


def _tick_job() -> None:
    try:
        n = ladder.tick()
        if n:
            log.info("tick advanced %d case(s)", n)
    except Exception:  # noqa: BLE001 - the loop must survive anything
        log.exception("tick failed")


def _inbox_job() -> None:
    try:
        n = inbox.poll()
        if n:
            log.info("ingested %d inbound repl(ies)", n)
    except Exception:  # noqa: BLE001
        log.exception("inbox poll failed")


def _directory_job() -> None:
    try:
        web.reset_tick_budget()
        result = watch.refresh_officer_directory()
        if not result["ok"]:
            log.warning("officer directory unreachable: %s", result["reason"])
        elif result["drift"]:
            for d in result["drift"]:
                log.warning("officer contact may be stale: %s (%s, %s)",
                            d["ministry_id"], d["on_file"], d["email_kind"])
        else:
            log.info("officer directory checked - all contacts still listed")
    except Exception:  # noqa: BLE001
        log.exception("directory check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    scheduler.add_job(_tick_job, "interval", seconds=settings.tick_seconds,
                      id="tick", max_instances=1, coalesce=True)
    if settings.imap_enabled:
        scheduler.add_job(_inbox_job, "interval", seconds=settings.imap_poll_seconds,
                          id="inbox", max_instances=1, coalesce=True)
    if settings.web_reading_enabled:
        # Six of our officer contacts belong to named individuals who rotate with the
        # posting. Checking daily catches that drift before a grievance is sent to
        # someone who left.
        scheduler.add_job(_directory_job, "interval", hours=24,
                          id="directory", max_instances=1, coalesce=True)
    scheduler.start()

    log.info("Persist up. provider=%s model=%s dry_run=%s time_scale=%s tick=%ss",
             settings.provider.label, settings.model, settings.dry_run,
             settings.time_scale, settings.tick_seconds)
    if settings.provider.kind == "mock":
        log.warning("LLM_PROVIDER=mock - canned responses only. Set a real provider in .env.")
    elif settings.key_missing:
        log.error("%s is not set. Get a free key at %s",
                  settings.provider.api_key_env, settings.provider.console_url)
    if settings.dry_run:
        log.info("DRY_RUN is ON - no email will leave this machine. Outbox: %s", settings.outbox)
    log.info("Inbound email: %s", "polling" if settings.imap_enabled else "disabled (paste replies in the console)")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Persist", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
# Every template needs the absolute base URL for its social card, and threading it
# through each handler would guarantee one gets missed.
templates.env.globals["base_url"] = settings.public_base_url.rstrip("/")
templates.env.globals["settings"] = settings
templates.env.globals["RUNGS"] = ladder.RUNGS
templates.env.filters["fromjson"] = lambda raw: db.jload(raw, {})


_recent_submissions: dict[str, list[float]] = {}


def _rate_limited(ip: str, limit: int = 3, window: int = 900) -> bool:
    """Crude per-IP throttle on intake.

    The form is public and every submission costs real model tokens, so an open
    endpoint is a wallet-drain vector as soon as the URL is shared. In-memory is fine
    for one process; move it to the database if you ever run more than one.
    """
    now_ts = time.time()

    # Evict expired IPs. This process is meant to run for a week; without eviction the
    # dict grows for every visitor that ever hit the form and never shrinks.
    for other, times in list(_recent_submissions.items()):
        if not [t for t in times if now_ts - t < window]:
            del _recent_submissions[other]

    hits = [t for t in _recent_submissions.get(ip, []) if now_ts - t < window]
    if len(hits) >= limit:
        _recent_submissions[ip] = hits
        return True
    hits.append(now_ts)
    _recent_submissions[ip] = hits
    return False


def _session_token() -> str:
    """A cookie value derived from the password rather than the password itself.

    Salted per process, so tokens die with a restart and a stolen cookie never hands
    anyone the console password in plaintext.
    """
    return hmac.new(_SESSION_SALT, settings.console_password.encode(), hashlib.sha256).hexdigest()


def _authed(session: str | None) -> bool:
    return bool(session) and hmac.compare_digest(session, _session_token())


# ------------------------------------------------------------------ public


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "stats": db.stats(),
        "cases": db.list_cases(public_only=True),
        "events": db.recent_events(30),
    })


@app.get("/file", response_class=HTMLResponse)
def intake_form(request: Request):
    return templates.TemplateResponse(request, "intake.html", {"error": None})


@app.post("/file")
def intake_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    category: str = Form(...),
    narrative: str = Form(...),
    consent: str = Form(""),
    is_public: str = Form("yes"),
):
    client_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                 or (request.client.host if request.client else "unknown"))
    if _rate_limited(client_ip):
        return templates.TemplateResponse(request, "intake.html", {
            "error": "That's a few cases in quick succession from the same place. "
                     "Give it fifteen minutes, or email us directly.",
        }, status_code=429)

    if not consent:
        return templates.TemplateResponse(request, "intake.html", {
            "error": "We cannot act for you without your explicit consent.",
        }, status_code=400)
    if len(narrative.strip()) < 40:
        return templates.TemplateResponse(request, "intake.html", {
            "error": "Please describe what happened in a bit more detail (at least a few sentences).",
        }, status_code=400)

    case_id = db.create_case(
        citizen_name=name.strip(), citizen_email=email.strip(), citizen_phone=phone.strip(),
        category=category, narrative_raw=narrative.strip(),
        is_public=(is_public == "yes"),
    )
    return RedirectResponse(f"/case/{case_id}", status_code=303)


@app.get("/case/{case_id}", response_class=HTMLResponse)
def case_page(request: Request, case_id: str, session: str | None = Cookie(None)):
    case = db.get_case(case_id)
    if not case:
        return HTMLResponse("<h1>404</h1><p>No such case.</p>", status_code=404)
    # A citizen who unticked "show on the public ledger" meant it. Only the operator
    # (and anyone they share with) sees a private case.
    if not case["is_public"] and not _authed(session):
        return HTMLResponse(
            "<h1>Private case</h1><p>The person who filed this asked for it to stay off "
            "the public ledger.</p>", status_code=404,
        )
    return templates.TemplateResponse(request, "case.html", {
        "case": case,
        "facts": db.jload(case["facts_json"], {}),
        "routing": db.jload(case["routing_json"], {}),
        "observed": db.jload(case["observed_json"], {}),
        "actions": db.case_actions(case_id),
        "events": db.case_events(case_id),
        "rung": ladder.rung(case["rung"]),
        "authed": _authed(session),
    })


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Twilio WhatsApp inbound. Refuses everything unless the signature verifies."""
    form = dict((await request.form()).items())
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")

    if not settings.whatsapp_enabled:
        log.warning("whatsapp webhook hit but TWILIO_AUTH_TOKEN is unset - refusing")
        return Response(status_code=503, content="WhatsApp intake is not configured")

    if not whatsapp.verify_signature(str(request.url), params, signature):
        log.warning("whatsapp webhook signature check failed")
        return Response(status_code=403, content="bad signature")

    reply = whatsapp.handle(
        from_number=params.get("From", ""),
        body=params.get("Body", ""),
        profile_name=params.get("ProfileName", ""),
    )
    return Response(content=whatsapp.twiml(reply), media_type="application/xml")


MIN_REPLIES_FOR_RATE = 3
TAXONOMY_SOURCE = "https://pgportal.gov.in/Home/NodalPgOfficers"


def _sendable(address: str | None) -> bool:
    """Is this a real address we are willing to put a citizen's grievance behind?

    Guards the two ways a bad address gets here: an empty one, and a placeholder
    left in the taxonomy. Sending to either would be worse than refusing — one
    bounces, the other reaches a stranger.
    """
    a = (address or "").strip()
    if not a or "@" not in a or a.startswith("@") or a.endswith("@"):
        return False
    return not any(bad in a.upper() for bad in ("VERIFY-ME", "EXAMPLE.COM", "CHANGEME", "TODO"))


@app.get("/share", response_class=HTMLResponse)
def share(request: Request, session: str | None = Cookie(None)):
    """Sourcing kit: the link, a QR for it, and copy that is honest about what this is.

    Behind the console password because it is a tool for whoever is running this, not
    a page for a citizen. The QR it renders is public in the only sense that matters -
    it points at /file, which anyone can open.
    """
    if not _authed(session):
        return RedirectResponse("/console", status_code=303)
    from . import share as kit

    url = f"{settings.public_base_url.rstrip('/')}/file"
    return templates.TemplateResponse(request, "share.html", {
        "url": url,
        "qr": kit.qr_svg(url),
        "channels": [(name, body.format(url=url), why)
                     for name, body, why in kit.CHANNELS],
        "local": "127.0.0.1" in url or "localhost" in url,
    })


@app.get("/og-card", response_class=HTMLResponse)
def og_card(request: Request):
    """The social preview image, as a page to screenshot at 1200x630.

    Rendering it as HTML rather than shipping a PNG means it restyles with the rest
    of the site instead of quietly going out of date, and needs no image library.
    """
    from . import share as kit
    return templates.TemplateResponse(request, "og_card.html", {
        "w": kit.OG_WIDTH, "h": kit.OG_HEIGHT,
        "board": db.scoreboard(),
    })


@app.get("/toolbox", response_class=HTMLResponse)
def toolbox(request: Request):
    """What it cost to watch the web, and what had to be built to do it.

    Public for the same reason the case ledger is. A project that claims it reads
    captcha-gated government pages should show the receipts: which connector was
    used, what each read cost, and which connector did not exist until this project
    paid to have it made.
    """
    from . import anakin
    return templates.TemplateResponse(request, "toolbox.html", {
        "forged": db.tools("built"),
        "found": [t for t in db.tools() if t["origin"] != "built"],
        "builds": db.builds(20),
        "ledger": anakin.ledger(60),
        "spent": anakin.spent(),
        "budget": settings.anakin_credit_budget,
        "build_cost": anakin.BUILD_COST,
    })


@app.get("/scoreboard", response_class=HTMLResponse)
def scoreboard(request: Request):
    return templates.TemplateResponse(request, "scoreboard.html", {
        "board": db.scoreboard(),
        "min_replies": MIN_REPLIES_FOR_RATE,
    })


@app.get("/api/ledger")
def api_ledger():
    """The ledger as JSON, so the win rate is auditable by anyone, not just claimed."""
    cases = db.list_cases(public_only=True)
    return JSONResponse({
        "stats": db.stats(),
        "scoreboard": db.scoreboard(),
        "cases": [{
            "id": c["id"],
            "summary": (db.jload(c["facts_json"], {}) or {}).get("one_line_summary", ""),
            "status": c["status"],
            "rung": c["rung"],
            "ministry": (db.jload(c["routing_json"], {}) or {}).get("ministry_name", ""),
            "category": (db.jload(c["routing_json"], {}) or {}).get("category_name", ""),
            "registration_no": c["cpgrams_reg_no"],
            "amount_claimed": c["amount_claimed"],
            "amount_recovered": c["amount_recovered"],
            "opened": c["created_at"],
            "resolved": c["resolved_at"],
            "outcome": c["outcome"],
        } for c in cases],
    })


@app.get("/health")
def health():
    return JSONResponse({
        "ok": True,
        "provider": settings.provider.key,
        "model": settings.model,
        "key_configured": not settings.key_missing,
        "dry_run": settings.dry_run,
        "inbound_email": settings.imap_enabled,
        "time_scale": settings.time_scale,
        **db.stats(),
    })


# ------------------------------------------------------------------ console


@app.get("/console", response_class=HTMLResponse)
def console(request: Request, session: str | None = Cookie(None)):
    if not _authed(session):
        return templates.TemplateResponse(request, "login.html", {"error": None})
    return templates.TemplateResponse(request, "console.html", {
        "pending": db.pending_actions(),
        "cases": db.list_cases(),
        "stats": db.stats(),
    })


@app.post("/console/login")
def console_login(request: Request, password: str = Form(...)):
    if not hmac.compare_digest(password, settings.console_password):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Wrong password.",
        }, status_code=401)
    resp = RedirectResponse("/console", status_code=303)
    resp.set_cookie("session", _session_token(), httponly=True, samesite="lax",
                    secure=settings.public_base_url.startswith("https"), max_age=86400)
    return resp


@app.post("/console/approve/{action_id}")
def approve(action_id: int, session: str | None = Cookie(None)):
    if not _authed(session):
        return RedirectResponse("/console", status_code=303)

    action = db.get_action(action_id)
    if not action or action["status"] != "pending_approval":
        return RedirectResponse("/console", status_code=303)

    cid = action["case_id"]
    case = db.get_case(cid)
    db.update_action(action_id, status="approved", approved_at=db.now())

    channel = action["channel"]
    r = ladder.rung(action["rung"])

    if channel == "email":
        # An email with no address must NOT fall through to another branch. It used
        # to, and got logged as "call script approved" — a silent misroute whenever a
        # ministry had no grievance-officer address on file.
        if not _sendable(action["recipient"]):
            db.update_action(action_id, status="pending_approval", approved_at=None)
            db.log_event(cid, "error",
                         f"Cannot send rung {action['rung']}: no usable address "
                         f"({action['recipient'] or 'blank'}). Set grievance_officer_email "
                         f"in taxonomy.json from {TAXONOMY_SOURCE}, then approve again.")
            return RedirectResponse("/console", status_code=303)

        sent, detail = mailer.send(
            to=action["recipient"], subject=action["subject"],
            body=action["content"], case_id=cid, cc=case["citizen_email"],
        )
        db.update_action(action_id, status="sent", sent_at=db.now())
        db.log_event(cid, "sent", f"Rung {action['rung']} email approved and dispatched. {detail}")
        db.update_case(cid, status="awaiting_response", next_action_at=ladder.wait_until(r.wait_days))

    elif channel == "portal":
        # The packet goes to the citizen; they perform the one credentialed keystroke.
        if case["citizen_email"]:
            sent, detail = mailer.send(
                to=case["citizen_email"],
                subject=f"[{cid}] Your grievance is ready to submit",
                body=(
                    f"Dear {case['citizen_name']},\n\n"
                    f"Your grievance has been prepared and routed to:\n"
                    f"  {action['recipient']}\n\n"
                    f"Submit it at {settings.portal_url} using your own account, then reply to this\n"
                    f"email with the registration number. From that point we take over: tracking,\n"
                    f"chasing, and escalating without any further action from you.\n\n"
                    f"{'=' * 60}\n{action['content']}\n{'=' * 60}\n"
                ),
                case_id=cid,
            )
        else:
            # WhatsApp-only case: there is no address to email the packet to.
            detail = "No email on file — deliver the packet over WhatsApp."
        db.update_action(action_id, status="sent", sent_at=db.now())
        db.log_event(cid, "packet",
                     f"Packet approved for the citizen's one credentialed submission step. {detail}")
        db.update_case(cid, status="awaiting_submission", next_action_at=None)

    elif channel == "phone":
        db.update_action(action_id, status="sent", sent_at=db.now())
        db.log_event(cid, "call", "Call script approved. Place the call, then log the outcome.")
        db.update_case(cid, status="awaiting_response", next_action_at=ladder.wait_until(r.wait_days))

    else:
        db.update_action(action_id, status="pending_approval", approved_at=None)
        db.log_event(cid, "error", f"Unknown channel '{channel}' — not sent.")

    return RedirectResponse("/console", status_code=303)


@app.post("/console/reject/{action_id}")
def reject(action_id: int, session: str | None = Cookie(None)):
    if not _authed(session):
        return RedirectResponse("/console", status_code=303)
    action = db.get_action(action_id)
    if action and action["status"] == "pending_approval":
        db.update_action(action_id, status="rejected")
        db.log_event(action["case_id"], "rejected",
                     f"Rung {action['rung']} draft rejected by a human reviewer. Redrafting.")
        # Redraft the SAME rung. Do not climb - the case has not advanced.
        ladder.redraft(action["case_id"], action["rung"])
    return RedirectResponse("/console", status_code=303)


@app.post("/console/filed/{case_id}")
def record_filed(case_id: str, reg_no: str = Form(...), session: str | None = Cookie(None)):
    if not _authed(session):
        return RedirectResponse("/console", status_code=303)
    ladder.mark_filed(case_id, reg_no)
    return RedirectResponse(f"/case/{case_id}", status_code=303)


@app.post("/console/response/{case_id}")
def record_reply(case_id: str, reply: str = Form(...), session: str | None = Cookie(None)):
    if not _authed(session):
        return RedirectResponse("/console", status_code=303)
    try:
        ladder.record_response(case_id, reply)
    except Exception as exc:  # noqa: BLE001
        db.log_event(case_id, "error", f"Could not assess reply: {exc}")
    return RedirectResponse(f"/case/{case_id}", status_code=303)


@app.post("/console/tick")
def force_tick(session: str | None = Cookie(None)):
    if not _authed(session):
        return RedirectResponse("/console", status_code=303)
    n = ladder.tick()
    log.info("manual tick advanced %d case(s)", n)
    return RedirectResponse("/console", status_code=303)


@app.post("/console/nudge/{case_id}")
def nudge(case_id: str, session: str | None = Cookie(None)):
    """Make ONE waiting case due immediately - for testing the ladder without waiting."""
    if not _authed(session):
        return RedirectResponse("/console", status_code=303)
    case = db.get_case(case_id)
    if case:
        db.update_case(case_id, next_action_at=db.now())
        # advance only this case; ladder.tick() would sweep every due case, which is
        # a surprising side effect for a button labelled "advance this one"
        ladder.advance(db.get_case(case_id))
    return RedirectResponse(f"/case/{case_id}", status_code=303)
