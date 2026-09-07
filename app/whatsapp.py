"""WhatsApp intake (Twilio-compatible webhook).

Nobody in India fills in a web form to complain about a parcel — they message. This
is the same intake pipeline reached over WhatsApp, as a short conversation:

    citizen  →  describes the problem
    agent    →  asks for explicit consent (never assumed from a message)
    citizen  →  YES
    agent    →  runs the normal pipeline; later messages append to the case

Consent is the important part. A message is not authorisation to act on someone's
behalf with a government department, so the bot asks in plain words and records the
timestamp only on an explicit yes.

TO CONNECT (needs your account — nothing here transmits until you do):
  1. Twilio console → Messaging → WhatsApp sandbox (or a live sender).
  2. Set the inbound webhook to  https://<your-host>/webhook/whatsapp  (POST).
  3. Put TWILIO_AUTH_TOKEN in .env so signatures are verified.
Without TWILIO_AUTH_TOKEN the endpoint refuses every request rather than accepting
unauthenticated ones.
"""
import base64
import hashlib
import hmac
import logging
import re

from . import db
from .config import settings

log = logging.getLogger("persist.whatsapp")

CONSENT_YES = re.compile(r"^\s*(yes|y|haan|haa|ha|ok|okay|agree|i agree|confirm)\b", re.I)
CONSENT_NO = re.compile(r"^\s*(no|n|nahi|stop|cancel)\b", re.I)

ASK_CONSENT = (
    "Thanks — I've got that.\n\n"
    "Before I act on your behalf I need your say-so. If you reply YES:\n\n"
    "• I'll work out which government department this belongs to and draft a proper "
    "grievance\n"
    "• *You* submit it on the portal with your own account — I will never ask for your "
    "password\n"
    "• After that I track it and escalate if nobody answers, for as long as it takes\n"
    "• A human reviews every message before it's sent\n\n"
    "It's free. Reply YES to go ahead, or NO to stop here."
)

DECLINED = "No problem — I haven't done anything. Message again any time if you change your mind."

STARTED = (
    "Done — case {case_id} is open.\n\n"
    "I'm reading it now and working out the right department. I'll message you when "
    "there's something to do or something changes. You don't need to chase me.\n\n"
    "Track it here: {url}/case/{case_id}"
)

ALREADY = (
    "I've added that to case {case_id}.\n\n"
    "Current status: {status}. Track it at {url}/case/{case_id}"
)


def verify_signature(url: str, params: dict[str, str], signature: str) -> bool:
    """Twilio's scheme: HMAC-SHA1 over the URL plus sorted key+value pairs."""
    token = settings.twilio_auth_token
    if not token:
        return False
    payload = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def _normalise(phone: str) -> str:
    return re.sub(r"^whatsapp:", "", (phone or "")).strip()


#: a case in one of these states is finished — a new message from that number is a
#: NEW problem, not more detail on an old one. Without this check, someone whose
#: parcel case closed months ago could never report anything again; their message
#: would be appended to the dead case and silently go nowhere.
FINISHED = {"resolved", "closed_unresolved"}


def _open_case_for(phone: str) -> dict | None:
    if not phone:
        return None
    for c in db.list_cases():          # newest first
        if c["citizen_phone"] == phone and c["status"] not in FINISHED:
            return c
    return None


def _recently_closed_for(phone: str) -> dict | None:
    """A just-closed case they might be replying to (e.g. supplying missing facts)."""
    for c in db.list_cases():
        if c["citizen_phone"] == phone and c["status"] in FINISHED:
            return c
    return None


def handle(from_number: str, body: str, profile_name: str = "") -> str:
    """Process one inbound message. Returns the reply text."""
    phone = _normalise(from_number)
    text = (body or "").strip()
    if not text:
        return "I didn't catch that — could you describe what went wrong?"

    case = _open_case_for(phone)

    # ---- no live case: either they're answering a closed one, or it's a new problem
    if case is None:
        closed = _recently_closed_for(phone)
        if closed and closed["status"] == "closed_unresolved" and len(text.split()) >= 8:
            from . import ladder
            ladder.resume_with_more_info(closed["id"], text)
            return (f"Thanks — that's enough to reopen case {closed['id']}. "
                    f"I'm looking at it again now.")

        if len(text.split()) < 8:
            return ("Tell me a bit more about what went wrong — include any tracking "
                    "number, PNR or dates you have, and roughly when it happened.")
        case_id = db.create_case(
            citizen_name=profile_name.strip() or "WhatsApp user",
            citizen_email="",
            citizen_phone=phone,
            category="other",
            narrative_raw=text,
            is_public=True,
        )
        # Park it until consent arrives — the tick must not pick it up yet.
        db.update_case(case_id, status="needs_consent", consent_at=None, next_action_at=None)
        db.log_event(case_id, "intake", "Opened over WhatsApp. Awaiting explicit consent.")
        return ASK_CONSENT

    # ---- awaiting consent
    if case["status"] == "needs_consent":
        if CONSENT_YES.match(text):
            db.update_case(case["id"], consent_at=db.now(), status="intake",
                           next_action_at=db.now())
            db.log_event(case["id"], "consent",
                         "Explicit consent given over WhatsApp. Agent starting.")
            return STARTED.format(case_id=case["id"], url=settings.public_base_url)
        if CONSENT_NO.match(text):
            db.update_case(case["id"], status="closed_unresolved", next_action_at=None,
                           outcome="Citizen declined at consent")
            db.log_event(case["id"], "consent", "Citizen declined. Nothing was done.")
            return DECLINED
        # anything else: treat as more detail, re-ask
        db.update_case(case["id"], narrative_raw=case["narrative_raw"] + "\n\n" + text)
        return "Noted, I've added that.\n\n" + ASK_CONSENT

    # ---- existing case: more detail, or an answer to a blocking question
    from . import ladder
    if case["status"] == "needs_info":
        ladder.resume_with_more_info(case["id"], text)
    else:
        db.update_case(case["id"], narrative_raw=case["narrative_raw"] + "\n\n[via WhatsApp] " + text)
        db.log_event(case["id"], "inbound", "Citizen sent more detail over WhatsApp.")

    return ALREADY.format(case_id=case["id"],
                          status=db.get_case(case["id"])["status"].replace("_", " "),
                          url=settings.public_base_url)


def twiml(message: str) -> str:
    """Twilio expects TwiML back on the webhook response."""
    safe = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
