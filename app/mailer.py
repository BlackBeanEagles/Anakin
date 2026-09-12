"""The one rail that actually leaves this machine.

Three states, not two, because the middle one is the whole difference between
testing this and misusing it:

  DRY_RUN=true              nothing transmitted; written to outbox/ as text.
  MAIL_REDIRECT_TO=<addr>   really sent, but to that address instead of
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

import httpx

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

    # A transport exists if EITHER rail is configured. Checking only the SMTP pair
    # here meant a host with just RESEND_API_KEY set - the whole point of the HTTP
    # rail - silently wrote to the outbox and reported "demonstration mode".
    has_http = bool(settings.resend_api_key)
    has_smtp = bool(settings.smtp_host and settings.smtp_password)

    if settings.dry_run or not (has_http or has_smtp):
        path = _to_outbox(to, cc, subject, full, case_id)
        if settings.dry_run:
            operator = "DRY_RUN is on"
        elif not (settings.smtp_host or settings.resend_api_key):
            operator = "no mail transport configured"
        else:
            # The half-configured case: a host is set but the password was never
            # pasted in. Without this the send attempt reaches smtplib, fails the
            # login, and logs a stack-flavoured error on every single rung.
            operator = f"SMTP_PASSWORD is empty for {settings.smtp_user or 'the SMTP user'}"

        # The returned string lands on the public case timeline, so it has to read
        # like a record rather than a config dump. An outbox filename and the name of
        # an environment variable are facts about this machine, not about the
        # grievance - they go to the operator's log instead.
        log.info("not sent (%s) — written to %s", operator, path.name)
        return False, "Prepared and recorded. Not transmitted — demonstration mode."

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

    ok, why = _transmit(msg, envelope_to, subject, full)
    if not ok:
        return False, why

    # The ledger must never claim a department was contacted when it was not. This
    # string ends up on the public case timeline, so it says exactly what happened.
    if redirected:
        return True, (f"Sent to {settings.mail_redirect_to} as a redirected test "
                      f"(intended recipient: {to}). The department was NOT contacted.")
    return True, f"Sent to {to}"


def _transmit(msg: EmailMessage, to: str, subject: str, body: str) -> tuple[bool, str]:
    """Hand the message to whichever transport this host actually allows.

    Render's free tier - and most PaaS free tiers - block outbound ports 25, 465 and
    587 to stop spam, so an SMTP send from the deployed instance fails with
    "Network is unreachable" no matter how correct the credentials are. The same
    credentials work perfectly from a laptop, which makes it a confusing failure to
    debug in production.

    So HTTP first when an API key is present: it goes over 443, which nobody blocks.
    SMTP stays as the fallback, because it needs no third party and works fine
    anywhere outbound mail ports are open.
    """
    if settings.resend_api_key:
        try:
            r = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}",
                         "Content-Type": "application/json"},
                json={"from": settings.mail_from_http or msg["From"],
                      "to": [to], "subject": subject, "text": body},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            log.warning("http mail transport failed: %s", exc)
            return False, f"Mail API unreachable: {exc}"
        if r.status_code >= 400:
            log.warning("http mail rejected: %s %s", r.status_code, r.text[:200])
            return False, f"Mail API rejected the message ({r.status_code})"
        return True, ""

    if not settings.smtp_host:
        return False, "no mail transport configured"

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except OSError as exc:
        # Errno 101 / 111 here is almost always the host blocking the port rather
        # than anything wrong with the message or the credentials. Say so, because
        # the obvious reading - "my password is wrong" - sends you the wrong way.
        log.warning("smtp send failed: %s", exc)
        return False, (f"SMTP unreachable ({exc}). If this host blocks outbound mail "
                       f"ports, set RESEND_API_KEY to send over HTTPS instead.")
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        log.exception("smtp send failed")
        return False, f"SMTP send failed: {exc}"
    return True, ""


def _to_outbox(to: str, cc: str, subject: str, body: str, case_id: str) -> Path:
    stamp = now().replace(":", "-").replace(".", "-")
    path = settings.outbox / f"{stamp}_{case_id}.txt"
    path.write_text(
        f"To: {to}\nCc: {cc}\nSubject: {subject}\nCase: {case_id}\n"
        f"{'-' * 60}\n{body}\n",
        encoding="utf-8",
    )
    return path
