"""The escalation ladder.

This is the part that makes Persist an agent rather than a form-filler. Each rung has
its own audience, its own waiting period, and its own argument. The agent decides
which rung it is on and when to climb; a human approves every outbound action.

Rung 0  PREPARE   agent extracts, routes, drafts. Produces a ready-to-submit packet.
Rung 1  FILED     citizen submitted on CPGRAMS. Clock starts.
Rung 2  OFFICER   direct email to the department's Public Grievance Officer.
Rung 3  APPEAL    appeal to the Appellate Authority - argues the disposal was inadequate.
Rung 4  PHONE     call the regional office with a scripted ask.
Rung 5  DIRECTOR  escalate to the ministry's Director of Public Grievances.
Rung 6  EXHAUSTED ladder complete. Closed unresolved, and we say so publicly.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import formataddr

from . import db, dispatch, notify, watch, web
from .agent import (
    draft_appeal,
    draft_call_script,
    draft_grievance,
    draft_officer_email,
    extract_facts,
    parse_response,
    route_case,
)
from .agent.route import ministry as get_ministry
from .config import settings
from .llm import LLMError

log = logging.getLogger("persist.ladder")


@dataclass(frozen=True)
class Rung:
    n: int
    key: str           # the state name
    label: str
    channel: str
    wait_days: float   # how long to wait AFTER acting on this rung before climbing
    blurb: str
    action_kind: str = ""   # what the drafted artefact is called; defaults to key

    @property
    def kind(self) -> str:
        return self.action_kind or self.key


RUNGS: list[Rung] = [
    Rung(0, "prepare",  "Preparing packet",           "internal", 0,  "Agent extracting facts, routing, and drafting."),
    Rung(1, "filed",    "Filed on CPGRAMS",           "portal",   21, "Grievance registered. Department has 21 days.", action_kind="packet"),
    Rung(2, "officer",  "Emailed Grievance Officer",  "email",    7,  "Direct email to the department's Public Grievance Officer."),
    Rung(3, "appeal",   "Appeal to Appellate Authority", "email", 30, "Formal appeal arguing the disposal was inadequate."),
    Rung(4, "phone",    "Called regional office",     "phone",    3,  "Scripted call to the regional office."),
    Rung(5, "director", "Escalated to Director (PG)", "email",    30, "Escalation to the ministry's Director of Public Grievances."),
    Rung(6, "exhausted","Ladder exhausted",           "internal", 0,  "Every rung tried. Closed unresolved."),
]

# How long to wait for a citizen to supply missing facts before reminding them.
NUDGE_AFTER_DAYS = 3.0


def rung(n: int) -> Rung:
    return RUNGS[min(max(n, 0), len(RUNGS) - 1)]


def wait_until(days: float) -> str:
    """Apply TIME_SCALE so the full ladder can be demonstrated in minutes."""
    seconds = days * 86400 * settings.time_scale
    return (datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 5))).isoformat()


def _elapsed_days(iso: str | None) -> int:
    """Days the LADDER considers elapsed since `iso`.

    Two things this gets right that a naive wall-clock diff does not:

    1. The anchor must be the filing date, not `updated_at` — that column is
       rewritten on every save, so measuring from it reported ~0 days and every
       escalation email told a department "days elapsed since filing: 0".
    2. Under TIME_SCALE compression, wall-clock elapsed is also ~0. Dividing by the
       scale converts back to the nominal timeline, so drafts read correctly both in
       a compressed demo and in production (where TIME_SCALE=1.0 makes this exact).
    """
    if not iso:
        return 0
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return 0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    real_seconds = (datetime.now(timezone.utc) - then).total_seconds()
    scale = settings.time_scale or 1.0
    return max(int(real_seconds / (86400 * scale)), 0)


def _days_pending(case: dict) -> int:
    """How long this grievance has been with the department."""
    return _elapsed_days(case.get("filed_at") or case.get("created_at"))


# --------------------------------------------------------------- rung 0


def prepare(case: dict) -> None:
    """Extract -> route -> draft. Ends with a packet the citizen can submit."""
    cid = case["id"]
    db.log_event(cid, "agent", "Reading the narrative and extracting facts.")
    facts = extract_facts(case["narrative_raw"], case["category"])
    db.update_case(cid, facts_json=json.dumps(facts))
    db.log_event(cid, "facts", f"Extracted: {facts['one_line_summary']}")

    if facts["sensitive_flags"]:
        db.log_event(cid, "safety",
                     "Sensitive data detected in the narrative and excluded from all filings: "
                     + "; ".join(facts["sensitive_flags"]))

    # Scope is decided BEFORE asking for anything else, even when facts are missing.
    #
    # The order used to be the other way round, and it produced the exact discourtesy
    # this project exists to fix: a municipal pothole complaint was asked for the
    # pothole's GPS coordinates, and only once the citizen had gone and found them was
    # it told CPGRAMS is the wrong portal entirely. Whether a matter belongs here does
    # not depend on the detail that is missing - a pothole is a municipal question with
    # or without coordinates - so the question that can be answered now gets answered
    # now. It costs one routing call on cases that will end up asking anyway.
    db.log_event(cid, "agent", "Choosing the correct ministry and category.")
    routing = route_case(facts, case["narrative_raw"], case["category"])
    db.update_case(cid, routing_json=json.dumps(routing))

    if routing["out_of_scope"]:
        db.update_case(cid, status="closed_unresolved", next_action_at=None,
                       outcome="Out of scope for CPGRAMS: " + routing["out_of_scope_reason"])
        db.log_event(cid, "routing",
                     "NOT filing - CPGRAMS is the wrong mechanism. " + routing["out_of_scope_reason"])
        # Tell them. Declining silently is precisely the failure this project exists
        # to fix — a person who hears nothing cannot go anywhere else instead.
        notify.citizen(cid, "declined", reason=routing["out_of_scope_reason"])
        return

    # In scope, but we cannot file it yet. Now the question is worth asking, because
    # the answer will actually be used.
    if not facts["ready_to_file"] and facts["missing_info"]:
        db.update_case(cid, amount_claimed=facts["amount_inr"] or case["amount_claimed"])
        ask_citizen(cid, facts["missing_info"])
        return

    db.log_event(
        cid, "routing",
        f"Routed to {routing['ministry_name']} -> {routing['category_name']} "
        f"(confidence {routing['confidence']:.0%}). {routing['rationale']}",
    )
    if routing.get("faster_route"):
        db.log_event(cid, "routing", "Faster route available: " + routing["faster_route"])

    draft = draft_grievance(case, facts, routing)
    db.update_case(cid, draft_text=draft["body"],
                   amount_claimed=facts["amount_inr"] or case["amount_claimed"])

    m = get_ministry(routing["ministry_id"]) or {}
    action_id = db.create_action(
        cid, rung=1, channel="portal", kind="packet",
        recipient=f"{routing['ministry_name']} / {routing['category_name']}",
        subject=draft["subject"], content=draft["body"],
        status="pending_approval",
    )
    db.log_event(cid, "draft",
                 f"Grievance drafted ({draft['word_count']} words). Ask: {draft['the_ask']}. "
                 f"Helpline on file: {m.get('helpline') or 'n/a'}")
    # The agent takes this itself when it is allowed to; otherwise it queues and
    # says why. Either way the case does not stall silently.
    dispatch.offer(db.get_case(cid), action_id, routing)


# --------------------------------------------------------------- rung 2+


def climb(case: dict) -> None:
    """Waiting period elapsed - check reality, then go one rung higher if warranted."""
    cid = case["id"]

    # Look before escalating. Without this the agent is just a timer, and would
    # escalate to a Director on a grievance that was quietly resolved last week.
    observed = watch.check_case(case)
    if observed:
        p = observed["primary"]
        if p["closed"] and p["status_text"]:
            # The portal says it is disposed of. Assess that as a real reply rather
            # than climbing blind - it may be a genuine remedy, or a brush-off that
            # deserves an appeal instead of a chaser.
            db.log_event(cid, "observed",
                         "Portal shows this as closed. Assessing the disposal "
                         "rather than escalating on the timer.")
            record_response(
                cid, "[Read from " + p["source_url"] + "] " + p["status_text"])
            return
        if p["evidence"]:
            # Carry the live finding into the next draft as citable evidence.
            db.update_case(cid, observed_json=json.dumps(observed["primary"]))
    facts = db.jload(case["facts_json"], {})
    routing = db.jload(case["routing_json"], {})
    nxt = rung(case["rung"] + 1)

    # Skip email rungs for departments that publish no address. Stalling the case
    # there would be worse than climbing past it — some ministries genuinely list a
    # phone number only, and inventing an address is not an option.
    routing_preview = db.jload(case["routing_json"], {}) or {}
    m_preview = get_ministry(routing_preview.get("ministry_id", "")) or {}
    while (nxt.n < 6 and nxt.channel == "email"
           and not m_preview.get("grievance_officer_email")):
        db.log_event(cid, "escalate",
                     f"Rung {nxt.n} ({nxt.label}) skipped: "
                     f"{m_preview.get('name', 'this department')} publishes no grievance "
                     f"officer email. Climbing past it rather than inventing an address.")
        nxt = rung(nxt.n + 1)

    if nxt.n >= 6:
        db.update_case(cid, rung=6, status="closed_unresolved", next_action_at=None,
                       outcome="Ladder exhausted without resolution")
        db.log_event(cid, "exhausted",
                     "Every rung of the ladder has been tried without resolution. "
                     "Closing publicly as unresolved.")
        notify.citizen(cid, "exhausted")
        return

    waited = _days_pending(case)
    d, recipient = _draft_for(case, facts, routing, nxt)

    action_id = db.create_action(cid, rung=nxt.n, channel=nxt.channel, kind=nxt.kind,
                                 recipient=recipient, subject=d["subject"],
                                 content=d["body"], status="pending_approval")
    db.update_case(cid, rung=nxt.n)
    db.log_event(cid, "escalate",
                 f"No adequate response after {waited} days. Climbing to rung {nxt.n}: "
                 f"{nxt.label}.")
    dispatch.offer(db.get_case(cid), action_id, routing)
    notify.citizen(cid, "escalated", rung_label=nxt.label,
                   reason=f"no adequate response after {waited} days")


def ask_citizen(case_id: str, questions: list[str]) -> None:
    """We cannot file without more facts. Ask, then chase, then close honestly.

    Without this a blocked case sits in the database forever and the citizen never
    learns why nothing happened - the exact failure the whole project exists to fix.
    """
    case = db.get_case(case_id)
    if not case:
        return
    asked = int(case["info_asks"] or 0)

    if asked >= 2:
        db.update_case(case_id, status="closed_unresolved", next_action_at=None,
                       outcome="Closed - the facts needed to file were never provided")
        db.log_event(case_id, "blocked",
                     "Asked twice for the missing facts with no reply. Closing honestly "
                     "rather than filing a grievance that would be rejected.")
        notify.citizen(case_id, "abandoned")
        return

    db.update_case(case_id, status="needs_info", info_asks=asked + 1,
                   info_questions=json.dumps(questions),
                   next_action_at=wait_until(NUDGE_AFTER_DAYS))
    db.log_event(case_id, "blocked",
                 ("Cannot file yet - asked the citizen for: " if asked == 0
                  else "Still blocked - reminded the citizen about: ")
                 + "; ".join(questions))
    notify.citizen(case_id, "needs_info" if asked == 0 else "needs_info_reminder",
                   questions="\n".join(f"  - {q}" for q in questions))


def resume_with_more_info(case_id: str, extra: str) -> None:
    """The citizen answered. Fold their reply into the narrative and start over."""
    case = db.get_case(case_id)
    if not case:
        return
    db.update_case(
        case_id,
        narrative_raw=case["narrative_raw"] + "\n\n[Further detail from the citizen]\n" + extra,
        status="intake", info_asks=0, info_questions=None,
        outcome=None, next_action_at=db.now(),
    )
    db.log_event(case_id, "intake",
                 "Citizen supplied more detail. Re-reading the case from the top.")


def _addressee(m: dict) -> str:
    """Address with the officer's name attached, so the operator can see who this
    actually goes to before approving it.

    Built with formataddr rather than string concatenation: designations contain
    parentheses ("ADG (PG)"), which are RFC 5322 comment syntax — pasted in raw they
    are silently eaten, turning "ADG (PG)" into "ADG PG" in the sent header.
    """
    email = m.get("grievance_officer_email", "")
    if not email:
        return ""
    name = m.get("officer_name", "")
    desig = m.get("officer_designation", "")
    who = f"{name} ({desig})" if name and desig else name
    return formataddr((who, email)) if who else email


def _draft_for(case: dict, facts: dict, routing: dict, r: Rung) -> tuple[dict, str]:
    """Draft the correspondence for one specific rung. Returns (draft, recipient).

    Single dispatch point so climb() and redraft() cannot disagree about what a rung
    means - an earlier version fell through to the director branch when asked to
    redraft rung 1, and quietly escalated a case that had never been filed.
    """
    m = get_ministry(routing.get("ministry_id", "")) or {}
    waited = _days_pending(case)

    if r.key == "filed":
        return (draft_grievance(case, facts, routing),
                f"{routing.get('ministry_name', '')} / {routing.get('category_name', '')}")
    if r.key == "officer":
        return draft_officer_email(case, facts, routing, waited), _addressee(m)
    if r.key == "appeal":
        last = _last_response(case["id"]) or "No reply was received within the stipulated period."
        # A live observation is the strongest thing an appeal can carry: not "we think
        # nothing happened" but "your own tracking page still says this today".
        seen = db.jload(case.get("observed_json"), {}) or {}
        if seen.get("evidence"):
            last += (
                "\n\n[Observed on "
                + seen.get("source_url", "the department portal")
                + ": " + seen["evidence"] + "]")
        return draft_appeal(case, facts, routing, last), _addressee(m)
    if r.key == "phone":
        # the officer's direct line beats the public helpline for a status chase
        return (draft_call_script(case, facts, routing),
                m.get("officer_phone") or m.get("helpline", ""))
    if r.key == "director":
        d = draft_officer_email(case, facts, routing, waited)
        d["subject"] = "ESCALATION to Director (Public Grievances): " + d["subject"]
        return d, _addressee(m)

    raise ValueError(f"rung {r.n} ({r.key}) has no drafter")


def redraft(case_id: str, rung_n: int) -> None:
    """A human rejected a draft. Produce a fresh one for the SAME rung.

    Not a climb - the case has not advanced, it just needs different words.
    """
    case = db.get_case(case_id)
    if not case:
        return
    facts = db.jload(case["facts_json"], {})
    routing = db.jload(case["routing_json"], {})
    r = rung(rung_n)

    try:
        d, recipient = _draft_for(case, facts, routing, r)
    except ValueError as exc:
        db.log_event(case_id, "error", f"Cannot redraft: {exc}")
        return

    db.create_action(case_id, rung=r.n, channel=r.channel, kind=r.kind,
                     recipient=recipient, subject=d["subject"], content=d["body"],
                     status="pending_approval")
    db.update_case(case_id, rung=r.n, status="awaiting_approval", next_action_at=None)
    db.log_event(case_id, "redraft",
                 f"Rung {r.n} ({r.label}) redrafted after human rejection. "
                 f"Queued for approval again.")
    # Deliberately not offered to the agent. A person has just rejected this rung;
    # auto-sending the retry would be the agent overruling them.


def _last_response(case_id: str) -> str | None:
    for a in reversed(db.case_actions(case_id)):
        if a["response_raw"]:
            return a["response_raw"]
    return None


# --------------------------------------------------------------- transitions


def mark_filed(case_id: str, reg_no: str) -> None:
    """The citizen submitted on CPGRAMS and gave us the registration number.
    From here the agent owns the case."""
    r = rung(1)
    db.update_case(case_id, cpgrams_reg_no=reg_no.strip(), rung=1, filed_at=db.now(),
                   status="awaiting_response", next_action_at=wait_until(r.wait_days))
    db.log_event(case_id, "filed",
                 f"Registration number {reg_no.strip()} recorded. The agent now owns this case - "
                 f"it will track, chase, and escalate without further citizen action.")
    notify.citizen(case_id, "filed", reg_no=reg_no.strip())


def record_response(case_id: str, reply_text: str) -> dict:
    """A department replied. Decide whether it actually counts."""
    case = db.get_case(case_id)
    if not case:
        raise ValueError(f"unknown case {case_id}")
    facts = db.jload(case["facts_json"], {})

    actions = db.case_actions(case_id)
    if actions:
        db.update_action(actions[-1]["id"], response_raw=reply_text, response_at=db.now())

    assessment = parse_response(case, facts, reply_text)
    if actions:
        db.update_action(actions[-1]["id"], response_json=json.dumps(assessment))

    db.log_event(case_id, "response",
                 f"Department replied. Verdict: {assessment['verdict'].upper()}. "
                 f"{assessment['reasoning']}")

    if assessment["verdict"] == "resolved":
        db.update_case(case_id, status="resolved", next_action_at=None,
                       resolved_at=db.now(), outcome=assessment["reasoning"],
                       amount_recovered=assessment["amount_recovered_inr"])
        db.log_event(case_id, "resolved", "Case resolved. The citizen's ask was met.")
        notify.citizen(case_id, "resolved", outcome=assessment["reasoning"])
    elif assessment["verdict"] == "needs_info":
        db.update_case(case_id, status="needs_info", next_action_at=None)
        db.log_event(case_id, "blocked",
                     "Department requested more information: "
                     + "; ".join(assessment["info_requested"]))
    else:
        if assessment["amount_recovered_inr"]:
            db.update_case(case_id, amount_recovered=assessment["amount_recovered_inr"])
        db.update_case(case_id, status="awaiting_response", next_action_at=db.now())
        db.log_event(case_id, "unsatisfied",
                     "Reply did not deliver the ask. Unaddressed: "
                     + ("; ".join(assessment["unaddressed"]) or "the entire request")
                     + ". Escalating.")
    return assessment


# --------------------------------------------------------------- the tick


def advance(case: dict) -> None:
    """Move one case forward by exactly one step."""
    cid, status = case["id"], case["status"]
    try:
        if status == "intake":
            prepare(case)
        elif status == "awaiting_response":
            climb(case)
        elif status == "needs_info":
            # The citizen hasn't answered. Remind once, then close honestly.
            ask_citizen(cid, db.jload(case["info_questions"], []))
        else:
            db.update_case(cid, next_action_at=None)
    except LLMError as exc:
        # Retry on the next tick rather than dropping the case.
        db.log_event(cid, "error", f"Step failed, will retry: {exc}")
        db.update_case(cid, next_action_at=wait_until(0.5))
    except Exception as exc:  # noqa: BLE001 - a stuck case must not kill the loop
        log.exception("case %s failed", cid)
        db.log_event(cid, "error", f"Unexpected failure, will retry: {exc}")
        db.update_case(cid, next_action_at=wait_until(0.5))


def tick() -> int:
    """One pass over the work queue. Returns how many cases advanced."""
    due = db.due_cases()
    # one budget per pass, so a bug cannot turn into a flood of requests at a
    # government server no matter how long the queue is
    web.reset_tick_budget()
    for case in due:
        log.info("advancing %s (%s, rung %s)", case["id"], case["status"], case["rung"])
        advance(case)
    return len(due)
