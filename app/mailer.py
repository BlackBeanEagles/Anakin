"""The one rail that actually leaves this machine.

Three states, not two, because the middle one is the whole difference between
testing this and misusing it:

  DRY_RUN=true              nothing transmitted; written to outbox/ as text.
  MAIL_REDIRECT_TO=<addr>   really sent over SMTP, but to that address instead of
                            the department, with the intended recipient preserved
                            in the subject and an X-Persist-Intended-To header.
  neither set               sent to the department for real.

The redirect exists because proving the rail works and mailing a fabricated
grievance to a named Executive Director are one environment variable apart. Use it
for every test. Turn it off only when a real grievance from a real person, with
real reference numbers, is ready to go.
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

    # A real send, but not necessarily to the department. The redirect exists so the
    # send rail can be proven end to end without a fictional grievance reaching a
    # real officer - the message genuinely leaves over SMTP and genuinely arrives,
    # it just arrives somewhere accountable.
    redirected = bool(settings.mail_redirect_to)
    envelope_to = settings.mail_redirect_to if redirected else to
    envelope_cc = "" if redirected else cc

    if redirected:
        subject = f"[TEST -> {to}] {subject}"
        banner = [
            "*** REDIRECTED TEST MESSAGE ***",
            f"This would have been sent to: {to}",
        ]
        if cc:
            banner.append(f"Cc would have been: {cc}")
        banner += [
            "MAIL_REDIRECT_TO is set, so it came here instead.",
            "No department has received this.",
            "=" * 60,
            "",
            "",
        ]
        full = "\n".join(banner) + full

    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = envelope_to
    if envelope_cc:
        msg["Cc"] = envelope_cc
    if redirected:
        # Kept as a header too, so the routing decision is verifiable from the raw
        # message rather than only from a subject line a human might reformat.
        msg["X-Persist-Intended-To"] = to
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

    # The ledger must never claim a department was contacted when it was not. This
    # string ends up on the public case timeline, so it says exactly what happened.
    if redirected:
        return True, (f"Sent to {settings.mail_redirect_to} as a redirected test "
                      f"(intended recipient: {to}). The department was NOT contacted.")
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
