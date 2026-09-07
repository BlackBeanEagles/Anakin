"""Status updates to the citizen whose case it is.

Separate from the approval-gated department correspondence: these go to the person who
asked for help, about their own case, under the consent they gave at intake. They still
respect DRY_RUN, so nothing is transmitted while that is on.
"""
import logging

from . import db, mailer

log = logging.getLogger("persist.notify")

TEMPLATES = {
    "filed": (
        "Your grievance is now registered",
        "Your grievance has been registered as {reg_no}.\n\n"
        "From here we take over. We will watch for the department's response, and if it\n"
        "does not come - or comes back as a non-answer - we escalate automatically:\n"
        "the grievance officer, then a formal appeal, then the regional office by phone,\n"
        "then the Director of Public Grievances.\n\n"
        "You do not need to do anything else. We will write when something changes.",
    ),
    "declined": (
        "We can't take this one — here's where it does belong",
        "We've read your case, and we have to be straight with you: this isn't something\n"
        "the central government grievance portal can act on.\n\n{reason}\n\n"
        "We're telling you rather than filing it anyway, because a grievance sent to the\n"
        "wrong authority sits for weeks and then gets closed without anyone looking at it.\n"
        "You'd have lost a month and be no further forward.\n\n"
        "If you think we've got this wrong, reply and tell us why — we'll look again.",
    ),
    "needs_info": (
        "We need a few more details before we can file",
        "We've read your case, but we can't file it yet - a grievance missing these\n"
        "details tends to get closed without action, which wastes weeks.\n\n"
        "Please reply to this email with:\n\n{questions}\n\n"
        "Just reply in your own words. We'll take it from there.",
    ),
    "needs_info_reminder": (
        "Still waiting on a few details",
        "We wrote a few days ago asking for some details so we can file your grievance,\n"
        "and haven't heard back. We still need:\n\n{questions}\n\n"
        "If we don't hear from you we'll close the case - not because we don't want to\n"
        "help, but because filing without these would waste your time.",
    ),
    "abandoned": (
        "Closing your case for now",
        "We asked twice for the details we needed to file your grievance and haven't been\n"
        "able to get them, so we're closing this case.\n\n"
        "This isn't final - reply to this email any time with the missing details and\n"
        "we'll pick it straight back up.",
    ),
    "escalated": (
        "We've escalated your grievance",
        "The department did not resolve your grievance, so we have moved it up a level.\n\n"
        "Stage: {rung_label}\n"
        "Why: {reason}\n\n"
        "You do not need to do anything. We will keep going.",
    ),
    "resolved": (
        "Your grievance has been resolved",
        "Good news - your grievance has been resolved.\n\n{outcome}\n\n"
        "If this does not match what actually happened, reply to this email and we will\n"
        "reopen it.",
    ),
    "exhausted": (
        "We've run out of escalation routes",
        "We have now tried every rung available to us on this grievance and the department\n"
        "has not delivered a remedy.\n\n"
        "We are closing it as unresolved and saying so publicly on the case page. The\n"
        "remaining routes are outside what we can do for you: the consumer commission, or\n"
        "legal advice.\n\nWe are sorry we could not get further.",
    ),
}


def citizen(case_id: str, kind: str, **fields) -> None:
    case = db.get_case(case_id)
    if not case:
        return
    tpl = TEMPLATES.get(kind)
    if not tpl:
        return
    if not case["citizen_email"]:
        # WhatsApp-only cases have no email address; the reply goes back over
        # WhatsApp instead, so there is nothing to send here.
        db.log_event(case_id, "notify", f"Status changed ({kind}); no email on file.")
        return

    subject, body = tpl
    try:
        body = body.format(**fields)
    except KeyError as exc:
        log.warning("notify template %s missing field %s", kind, exc)
        return

    sent, detail = mailer.send(
        to=case["citizen_email"],
        subject=f"[{case_id}] {subject}",
        body=f"Dear {case['citizen_name']},\n\n{body}",
        case_id=case_id,
    )
    db.log_event(case_id, "notify", f"Citizen updated ({kind}). {detail}")
