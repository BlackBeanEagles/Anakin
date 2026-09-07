"""The one rail that actually leaves this machine.

DRY_RUN defaults to true: emails are written to outbox/ as .eml-ish text files and
nothing is transmitted. Flip DRY_RUN=false in .env only when you intend to contact
real departments on behalf of real people.
"""
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from .config import settings
from .db import now

log = logging.getLogger("persist.mail")


def send(to: str, subject: str, body: str, case_id: str, cc: str = "") -> tuple[bool, str]:
    """Returns (sent_for_real, human_readable_detail)."""
    footer = (
        "\n\n---\n"
        f"Sent by Persist on behalf of the complainant, with their recorded consent.\n"
        f"Case reference: {case_id}\n"
        f"Public case record: {settings.public_base_url}/case/{case_id}\n"
        "Replies to this address are read and actioned."
    )
    full = body + footer

    if settings.dry_run or not settings.smtp_host:
        path = _to_outbox(to, cc, subject, full, case_id)
        reason = "DRY_RUN is on" if settings.dry_run else "no SMTP host configured"
        return False, f"Not sent ({reason}). Written to {path.name}"

    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg.set_content(full)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        log.exception("smtp send failed")
        return False, f"SMTP send failed: {exc}"

    return True, f"Sent to {to}"


def _to_outbox(to: str, cc: str, subject: str, body: str, case_id: str) -> Path:
    stamp = now().replace(":", "-").replace(".", "-")
    path = settings.outbox / f"{stamp}_{case_id}.txt"
    path.write_text(
        f"To: {to}\nCc: {cc}\nSubject: {subject}\nCase: {case_id}\n"
        f"{'-' * 60}\n{body}\n",
        encoding="utf-8",
    )
    return path
