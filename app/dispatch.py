"""Sending an approved action — one path, whether a human or the agent approved it.

This logic used to live inside the console's approve handler, which meant the agent
had no way to send anything at all: every rung queued itself as `pending_approval`
and waited for a click. Defensible as a safety choice, but it made the product
something that drafts and asks rather than something that acts.

So the dispatch lives here, callable from both, and `may_auto_approve` decides which
rungs the agent may take on its own.

The split is by consequence, not convenience. Rungs 0-2 are the ordinary machinery of
a grievance - prepare the packet, file it, write to the grievance officer - and a
citizen who asked for help has consented to those. Rung 3 and up are appeals to an
Appellate Authority and escalations to a Director: correspondence that names senior
officials and carries weight, where a person should look first.

THE HARD GATE
-------------
The agent may never autonomously put mail in front of a real official. Auto-approval
requires that outbound mail is either not transmitted at all (DRY_RUN) or redirected
to an address the operator owns (MAIL_REDIRECT_TO). Reaching a real department is a
decision a human makes, by clicking, every time.

That is not a configuration default that can be flipped by accident - it is checked
here on every action, and there is deliberately no setting to disable it. Autonomy
is demonstrated against a redirect; consequence stays manual.
"""
import logging

from . import db, mailer
from .config import settings

log = logging.getLogger("persist.dispatch")


PLACEHOLDERS = ("VERIFY-ME", "EXAMPLE.COM", "CHANGEME", "TODO")


def sendable(address: str | None) -> bool:
    """Is this a real address we are willing to put a citizen's grievance behind?

    Guards the three ways a bad address gets here: an empty one, a department's
    display name where an address should be, and a placeholder left in the taxonomy.
    Sending to any of them is worse than refusing - the first two bounce, the third
    reaches a stranger.

    An earlier version of this checked only for an "@" and let EXAMPLE.COM through,
    which the console's own check had caught for months. Moving dispatch into a
    shared module silently dropped that guard until a test noticed.
    """
    a = (address or "").strip()
    if not a or "@" not in a or a.startswith("@") or a.endswith("@"):
        return False
    return not any(bad in a.upper() for bad in PLACEHOLDERS)


def reaches_a_real_recipient() -> bool:
    """Would an email sent right now actually land at the address on the action?

    False when DRY_RUN is on (written to outbox instead), when no SMTP host is
    configured (nothing can leave), or when MAIL_REDIRECT_TO is set (it goes to the
    operator). True only when a message would genuinely arrive at a department.
    """
    if settings.dry_run or not settings.smtp_host:
        return False
    return not settings.mail_redirect_to


def may_auto_approve(case: dict, action: dict, routing: dict | None = None) -> tuple[bool, str]:
    """Can the agent send this one itself? Returns (allowed, reason it may not).

    Every gate is a reason a human should look first, and most have cost one before:
    a low-confidence route sends a citizen's complaint to the wrong department, and a
    blank address silently misroutes to another branch entirely.
    """
    if settings.auto_approve_through_rung < 0:
        return False, "auto-approval is switched off"

    # The hard gate, checked before anything else that could be relaxed.
    if reaches_a_real_recipient():
        return False, ("this would reach a real department — a human approves "
                       "anything that leaves for an official")

    if action["rung"] > settings.auto_approve_through_rung:
        return False, (f"rung {action['rung']} is an appeal or higher — "
                       f"a human approves those")

    if action["channel"] == "phone":
        return False, "a person has to place the call"

    if action["channel"] == "email" and not sendable(action["recipient"]):
        return False, f"no usable address ({action['recipient'] or 'blank'})"

    # A route the router itself is unsure about is exactly the one worth a human
    # glance. The 52-case eval measured mean confidence 0.92, and 0.95 when it was
    # *wrong* - so this floor is a blunt instrument, and deliberately a low one.
    confidence = float((routing or {}).get("confidence") or 0)
    if routing and confidence < settings.auto_approve_min_confidence:
        return False, (f"routing confidence {confidence:.0%} is below the "
                       f"{settings.auto_approve_min_confidence:.0%} floor")

    return True, ""


def send_action(action_id: int, *, by: str = "human") -> str:
    """Send one approved action. Returns a human-readable detail for the timeline.

    `by` is recorded rather than inferred later. A public ledger that cannot say
    whether a person or the agent sent a letter is not a record.
    """
    from . import ladder  # local import: ladder imports this module for auto-approval

    action = db.get_action(action_id)
    if not action or action["status"] != "pending_approval":
        return "not pending"

    cid = action["case_id"]
    case = db.get_case(cid)
    r = ladder.rung(action["rung"])
    channel = action["channel"]
    actor = "the agent" if by == "agent" else "a human reviewer"

    db.update_action(action_id, status="approved", approved_at=db.now())

    if channel == "email":
        # An email with no address must NOT fall through to another branch. It used
        # to, and got logged as "call script approved" - a silent misroute whenever a
        # ministry had no grievance-officer address on file.
        if not sendable(action["recipient"]):
            db.update_action(action_id, status="pending_approval", approved_at=None)
            db.log_event(cid, "error",
                         f"Cannot send rung {action['rung']}: no usable address "
                         f"({action['recipient'] or 'blank'}).")
            return "no usable address"

        _, detail = mailer.send(
            to=action["recipient"], subject=action["subject"],
            body=action["content"], case_id=cid, cc=case["citizen_email"],
        )
        db.update_action(action_id, status="sent", sent_at=db.now())
        db.log_event(cid, "sent",
                     f"Rung {action['rung']} email approved by {actor} and dispatched. {detail}")
        db.update_case(cid, status="awaiting_response",
                       next_action_at=ladder.wait_until(r.wait_days))
        return detail

    if channel == "portal":
        # The packet goes to the citizen; they perform the one credentialed keystroke.
        if case["citizen_email"]:
            _, detail = mailer.send(
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
            detail = "No email on file — deliver the packet over WhatsApp."
        db.update_action(action_id, status="sent", sent_at=db.now())
        db.log_event(cid, "packet",
                     f"Packet approved by {actor} for the citizen's one credentialed "
                     f"submission step. {detail}")
        db.update_case(cid, status="awaiting_submission", next_action_at=None)
        return detail

    if channel == "phone":
        db.update_action(action_id, status="sent", sent_at=db.now())
        db.log_event(cid, "call",
                     f"Call script approved by {actor}. Place the call, then log the outcome.")
        db.update_case(cid, status="awaiting_response",
                       next_action_at=ladder.wait_until(r.wait_days))
        return "call script ready"

    db.update_action(action_id, status="pending_approval", approved_at=None)
    db.log_event(cid, "error", f"Unknown channel '{channel}' — not sent.")
    return f"unknown channel {channel}"


def offer(case: dict, action_id: int, routing: dict | None = None) -> bool:
    """Send it if the agent is allowed to, otherwise leave it queued for a human.

    Called wherever the ladder creates an action. Returns True if it went out.
    """
    action = db.get_action(action_id)
    if not action:
        return False

    allowed, why = may_auto_approve(case, action, routing)
    if not allowed:
        db.update_case(case["id"], status="awaiting_approval", next_action_at=None)
        db.log_event(case["id"], "queued",
                     f"Rung {action['rung']} queued for human approval — {why}.")
        return False

    log.info("auto-approving %s rung %s", case["id"], action["rung"])
    send_action(action_id, by="agent")
    return True
