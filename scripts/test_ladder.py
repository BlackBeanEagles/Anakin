"""Walk a case through every rung to the exhausted state, and test rejection.

    python scripts/test_ladder.py

Rungs 3-5 and the reject path are the ones you will never hit by hand during a demo,
which is exactly why they break. Run this after touching ladder.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testenv  # noqa: E402  - seals the mail rail; must precede app imports

_testenv.assert_sealed()  # refuses to run if anything could transmit

from app import db, ladder  # noqa: E402
from app.config import settings  # noqa: E402

DEFLECTION = ("Your grievance has been forwarded to the concerned office for necessary "
              "action. The grievance is closed at this level.")

NARRATIVE = (
    "Speed Post consignment EX999888777IN booked on 1 August from Bangalore to Delhi has "
    "not moved since 3 August. Called 1924 twice, visited the post office once. The "
    "documents inside cost about Rs 3000 to replace. I want it traced or compensated."
)


def approve_all_pending() -> int:
    """Simulate the operator clicking approve on everything queued."""
    n = 0
    for a in db.pending_actions():
        db.update_action(a["id"], status="sent", sent_at=db.now())
        r = ladder.rung(a["rung"])
        db.update_case(a["case_id"], status="awaiting_response",
                       next_action_at=ladder.wait_until(r.wait_days))
        n += 1
    return n


def main() -> int:
    # This walk exercises the human-approval path: queue, approve, reject, redraft.
    # Auto-approval is a different path and is tested in test_bugs, so pin it off
    # here rather than letting whatever is in .env decide what this walk measures.
    settings.auto_approve_through_rung = -1

    if settings.provider.kind != "mock":
        print("Run this with LLM_PROVIDER=mock — it makes many calls.")
        return 2

    db.init()
    failures = []

    # ---------------------------------------------------------- full ladder
    print("\n=== full ladder walk ===")
    cid = db.create_case("Ladder Test", "ladder@example.com", "", "india_post", NARRATIVE)
    ladder.advance(db.get_case(cid))                 # rung 0: prepare
    approve_all_pending()                            # packet approved
    ladder.mark_filed(cid, "DOPOS/E/2026/9999999")   # rung 1

    seen = []
    for step in range(12):
        case = db.get_case(cid)
        seen.append((case["rung"], case["status"]))
        if case["status"] in ("resolved", "closed_unresolved"):
            break
        if case["status"] == "awaiting_approval":
            approve_all_pending()
            continue
        try:
            ladder.record_response(cid, DEFLECTION)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"record_response at rung {case['rung']}: {exc}")
            break
        db.update_case(cid, next_action_at=db.now())
        ladder.tick()

    final = db.get_case(cid)
    reached = max(r for r, _ in seen)
    print(f"  rungs reached: {sorted({r for r, _ in seen})}")
    print(f"  final: rung {final['rung']} / {final['status']}")

    for a in db.case_actions(cid):
        print(f"    rung {a['rung']:<2} {a['kind']:<9} {a['channel']:<7} -> "
              f"{a['recipient'] or '(none)'}")

    if final["status"] != "closed_unresolved":
        failures.append(f"ladder did not reach exhausted; ended {final['status']} rung {final['rung']}")
    if reached < 5:
        failures.append(f"never reached rung 5 (max was {reached})")

    kinds = [a["kind"] for a in db.case_actions(cid)]
    for expected in ("packet", "officer", "appeal", "phone", "director"):
        if expected not in kinds:
            failures.append(f"rung kind '{expected}' was never drafted (got {kinds})")

    # ---------------------------------------------------------- reject path
    print("\n=== reject path ===")
    cid2 = db.create_case("Reject Test", "reject@example.com", "", "india_post", NARRATIVE)
    ladder.advance(db.get_case(cid2))
    pend = db.pending_actions()
    target = [a for a in pend if a["case_id"] == cid2]
    if not target:
        failures.append("reject test: nothing queued after prepare")
    else:
        a = target[0]
        print(f"  rejecting rung {a['rung']} {a['kind']}")
        db.update_action(a["id"], status="rejected")
        ladder.redraft(cid2, a["rung"])          # the real reject path
        after = db.get_case(cid2)
        redrafted = [x for x in db.case_actions(cid2) if x["status"] == "pending_approval"]
        print(f"  after reject: rung {after['rung']} / {after['status']}")
        if redrafted:
            print(f"  redrafted as: rung {redrafted[0]['rung']} {redrafted[0]['kind']}")
            if redrafted[0]["kind"] != "packet":
                failures.append(
                    f"rejecting a packet redrafted a '{redrafted[0]['kind']}' instead of a packet"
                )
        else:
            failures.append("reject produced no redraft")

    # ---------------------------------------------------------- needs_info
    print("\n=== needs_info: ask -> remind -> close ===")
    cid3 = db.create_case("Info Test", "info@example.com", "", "other",
                          "Something went wrong somewhere and nobody is helping me at all please help")
    ladder.ask_citizen(cid3, ["The tracking or reference number", "The date it happened"])
    s1 = db.get_case(cid3)
    print(f"  after 1st ask: status={s1['status']} asks={s1['info_asks']} "
          f"due={'yes' if s1['next_action_at'] else 'NO'}")
    if not s1["next_action_at"]:
        failures.append("needs_info case has no next_action_at - it would sit forever")
    # It should be scheduled for the future (the nudge window), not due right now, but
    # it must not be filtered out of the queue by status the way finished cases are.
    db.update_case(cid3, next_action_at=db.now())
    if cid3 not in [c["id"] for c in db.due_cases()]:
        failures.append("needs_info status is excluded from the tick queue")

    ladder.advance(db.get_case(cid3))          # the reminder
    s2 = db.get_case(cid3)
    print(f"  after reminder: status={s2['status']} asks={s2['info_asks']}")

    ladder.advance(db.get_case(cid3))          # give up honestly
    s3 = db.get_case(cid3)
    print(f"  after 2 asks:  status={s3['status']} outcome={s3['outcome']}")
    if s3["status"] != "closed_unresolved":
        failures.append(f"blocked case never closes; ended {s3['status']}")

    # and the citizen can revive it by replying
    ladder.resume_with_more_info(cid3, "Sorry! The consignment number is EX111222333IN, sent on 4 August.")
    s4 = db.get_case(cid3)
    print(f"  after citizen replies: status={s4['status']} asks={s4['info_asks']}")
    if s4["status"] != "intake":
        failures.append("a citizen reply on a blocked case does not reopen it")

    # ---------------------------------------------------------- report
    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} PROBLEM(S):")
        for f in failures:
            print(f"  - {f}")
    else:
        print("no problems found")
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
