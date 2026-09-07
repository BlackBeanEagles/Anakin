"""Inbound email. Department replies arrive here and the agent assesses them itself.

This is what closes the loop: without it, a human has to notice a reply and paste it in.
With it, the agent can run unattended for a week - which is the whole claim.

Matching is by case id (PST-XXXX), which every outbound message carries in its footer
and subject line, so replies thread back to the right case automatically.
"""
import email
import imaplib
import logging
import re
from email.header import decode_header, make_header
from email.message import Message

from . import db
from .config import settings

log = logging.getLogger("persist.inbox")

CASE_RE = re.compile(r"\bPST-[A-Z0-9]{4}\b")
REG_RE = re.compile(r"\b[A-Z]{2,8}/[A-Z]/\d{4}/\d{4,10}\b")


def _decode(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001 - malformed headers are common in the wild
        return raw


def _body_text(msg: Message) -> str:
    """Prefer text/plain; fall back to stripping tags out of text/html."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:  # noqa: BLE001
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    except Exception:  # noqa: BLE001
        return str(msg.get_payload())


def _strip_quoted(text: str) -> str:
    """Drop the quoted original so the model assesses only the new reply."""
    cutoffs = [
        r"\n-{3,}\s*\n",                      # our own footer separator
        r"\nOn .{0,80}wrote:",                # gmail-style quote header
        r"\n>{1,}\s",                         # quoted lines
        r"\nFrom:.{0,120}\nSent:",            # outlook-style
    ]
    earliest = len(text)
    for pat in cutoffs:
        m = re.search(pat, text)
        if m and m.start() < earliest:
            earliest = m.start()
    return text[:earliest].strip()


def _is_citizen(case: dict, sender: str) -> bool:
    """Did this reply come from the person whose case it is, rather than a department?"""
    addr = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", sender or "")
    return bool(addr) and addr.group(0).lower() == (case["citizen_email"] or "").lower()


def _find_case(subject: str, body: str) -> str | None:
    for hay in (subject, body):
        m = CASE_RE.search(hay or "")
        if m:
            cid = m.group(0)
            if db.get_case(cid):
                return cid
    return None


def poll() -> int:
    """One pass over unread mail. Returns how many replies were ingested."""
    if not settings.imap_enabled:
        return 0

    from . import ladder  # imported late to avoid a circular import at module load

    ingested = 0
    try:
        conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        conn.login(settings.imap_user, settings.imap_password)
        conn.select(settings.imap_folder)
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            conn.logout()
            return 0

        for num in data[0].split():
            typ, raw = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            subject = _decode(msg.get("Subject"))
            sender = _decode(msg.get("From"))
            body = _strip_quoted(_body_text(msg))

            case_id = _find_case(subject, body)
            if not case_id:
                log.info("no case id in message from %s - leaving unread", sender)
                conn.store(num, "-FLAGS", "\\Seen")
                continue

            case = db.get_case(case_id)
            db.log_event(case_id, "inbound",
                         f"Reply received from {sender}. Subject: {subject}")

            # A citizen replying with their registration number starts the clock.
            if not case["cpgrams_reg_no"]:
                reg = REG_RE.search(body)
                if reg:
                    ladder.mark_filed(case_id, reg.group(0))
                    ingested += 1
                    continue

            # A blocked case whose citizen has answered goes back to the top.
            if case["status"] in ("needs_info", "closed_unresolved") and _is_citizen(case, sender):
                ladder.resume_with_more_info(case_id, body[:4000])
                ingested += 1
                continue

            try:
                ladder.record_response(case_id, body[:8000])
                ingested += 1
            except Exception as exc:  # noqa: BLE001 - one bad mail must not stop the poll
                log.exception("failed to assess reply for %s", case_id)
                db.log_event(case_id, "error", f"Could not assess inbound reply: {exc}")

        conn.logout()
    except imaplib.IMAP4.error as exc:
        log.error("IMAP error: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("inbox poll failed: %s", exc)

    return ingested
