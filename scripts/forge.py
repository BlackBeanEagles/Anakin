"""Forge a Wire connector for a site nothing covers yet.

Deliberately a separate command rather than a step in the ladder, for two reasons
that are both fatal if ignored:

  Time.   A build is asynchronous and can take many minutes. `ladder.tick()` runs on
          a 20-second scheduler and sweeps every due case in one pass, so a blocking
          build inside it would freeze the entire queue behind one grievance.

  Money.  A build costs 25 credits of a 300-credit tier. Forging per case would
          bankrupt the project by the third one. A connector is built ONCE and every
          case afterwards uses it for free - the first case funds the tool, the rest
          inherit it.

That constraint turns out to be the better story anyway: not "watch my agent call an
API", but "nothing could read India's national grievance portal, so this built the
connector and published it, and now every case - mine and everyone else's - can."

    python scripts/forge.py                 show the gaps, spend nothing
    python scripts/forge.py --build cpgrams build the CPGRAMS connector
    python scripts/forge.py --build post --force
                                            India Post is listed but does not track
                                            consignments; --force builds anyway
    python scripts/forge.py --status        what has been built, and what it cost
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import anakin, catalog, db, fallback  # noqa: E402
from app.config import settings  # noqa: E402

# The two reads Persist depends on, and which nothing in the catalog serves. Both
# were confirmed absent from all 991 published sites - the India Post catalog exists
# but covers philately and Gangajal products, not consignment tracking.
GAPS = {
    "cpgrams": {
        "domain": "pgportal.gov.in",
        "url": "https://pgportal.gov.in",
        "why": "India's national grievance portal. Every case Persist files lives here.",
        "probe": "check the status of a government grievance by registration number",
        "goal": (
            "Given a CPGRAMS grievance registration number, open the public status "
            "page and return the current status text, the ministry or department the "
            "grievance sits with, the date of the most recent action, and the full "
            "list of dated entries in the grievance history."
        ),
    },
    "post": {
        "domain": "indiapost.gov.in",
        "url": "https://www.indiapost.gov.in",
        "why": "Consignment tracking - the evidence an appeal quotes back at a department.",
        "probe": "track an India Post consignment by number and get its movement history",
        "goal": (
            "Given an India Post consignment or article number, open the consignment "
            "tracking page and return the current status, the booking date, the "
            "destination office, and every dated tracking event with its location."
        ),
    },
}


def show_gap(key: str, gap: dict) -> bool:
    """Print the evidence for one gap. Returns True if it is real."""
    print(f"\n{key}  —  {gap['domain']}")
    print(f"  {gap['why']}")

    covered = catalog.covers(gap["domain"])
    if covered:
        print("  IN THE CATALOG already:")
        for c in covered:
            print(f"    {c['slug']}  ({c.get('action_count')} actions)  "
                  f"{(c.get('description') or '')[:90]}")
        # Being listed is not the same as being served. India Post is in the catalog
        # for philately products; none of that tracks a consignment.
        print("  (listed is not the same as served - check the actions above cover the need)")

    found = catalog.candidates(gap["probe"], limit=4)
    print(f"  what discovery returns for \"{gap['probe']}\":")
    if not found:
        print("    nothing at all")
    for f in found:
        print(f"    {f['action_id'][:60]:62} {f['domain'] or f['catalog'] or '?'}")
    return not covered


def status() -> int:
    db.init()
    forged = db.tools("built")
    print(f"credits: {anakin.spent()} spent of {settings.anakin_credit_budget}\n")
    if not forged:
        print("nothing forged yet")
    for t in forged:
        print(f"  {t['action_id']}")
        print(f"    {t['domain']}  built {t['first_seen'][:16]}  "
              f"funded by {t['funded_by'] or 'no case'}  {t['wins']}/{t['uses']} useful")

    pending = [b for b in anakin.build_requests()
               if (b.get("status") or "").lower() not in {"success", "failed"}]
    for b in pending:
        print(f"  pending: {b.get('domain')}  {b.get('status')}")
    return 0


def build(key: str) -> int:
    gap = GAPS.get(key)
    if not gap:
        print(f"unknown target '{key}'. Choose one of: {', '.join(GAPS)}")
        return 1

    db.init()
    if not anakin.configured():
        print("ANAKIN_API_KEY is not set - a build needs a key. Free tier is 300 credits.")
        return 1

    listed = not show_gap(key, gap)
    forcing = "--force" in sys.argv

    # A domain being listed is not the same as the need being served. India Post is
    # in the catalog with six actions, none of which track a consignment - they sell
    # commemorative stamps and Gangajal. Refusing on the domain alone would make this
    # useless for exactly the half-covered sites where a build is most warranted, so
    # a listed domain needs --force, which also sets force=true upstream to get past
    # the 409 ACTION_EXISTS check.
    if listed and not forcing:
        print("\nThe catalog lists this domain. If the actions above genuinely cover")
        print("the need, use them - a duplicate build wastes 25 credits.")
        print("If they do not (six philately actions do not track a parcel), rerun")
        print("with --force to build anyway.")
        return 1

    if anakin.remaining() < anakin.BUILD_COST:
        print(f"\nNot enough budget: {anakin.remaining()} left, "
              f"{anakin.BUILD_COST} needed.")
        return 1

    print(f"\n  spec: {gap['goal']}")
    print(f"\n  This costs {anakin.BUILD_COST} credits (refunded if the build fails),")
    print(f"  and publishes the connector publicly - anyone using Anakin gets it.")
    if input("  Type 'build' to go ahead: ").strip().lower() != "build":
        print("  cancelled, nothing spent")
        return 0

    try:
        br = anakin.build_request(gap["url"], gap["goal"], visibility="public",
                                  force=forcing)
    except anakin.AnakinError as exc:
        # Refused before anything was charged - no build id exists yet, so the
        # attempt is recorded under a local one rather than going unrecorded.
        return _failed(f"local-{db.now()[:19]}-{key}", gap, exc.code, str(exc),
                       refunded=False)

    print(f"\n  submitted {br.get('id')} — status {br.get('status')}, "
          f"{br.get('credits_charged')} credits held")
    print("  waiting (up to 15 minutes; safe to Ctrl-C and check --status later)")

    # Recorded before the wait, not after: a Ctrl-C or a crash during those fifteen
    # minutes must still leave a trace of what was asked for and what it cost.
    db.record_build(br["id"], gap["domain"], gap["goal"], status="pending",
                    credits=int(br.get("credits_charged") or anakin.BUILD_COST))

    done = anakin.await_build(br["id"])
    if done is None:
        return _failed(br["id"], gap, "TIMEOUT",
                       "still building when we stopped waiting", refunded=False)

    if (done.get("status") or "").lower() != "success":
        return _failed(br["id"], gap, "BUILD_FAILED",
                       f"{done.get('error_type', '')} {done.get('error', '')}".strip(),
                       refunded=True)

    # A build can report success, create the catalog entry, charge full price and
    # publish nothing callable - which is exactly what happened on pgportal.gov.in.
    # An empty action list is not a success no matter what the status field says.
    action_id = done.get("action_id") or ""
    if not action_id:
        for a in anakin.catalog_actions(gap["domain"].replace(".", "-")):
            action_id = a.get("action_id") or a.get("id") or ""
            if action_id:
                break
    if not action_id:
        return _failed(br["id"], gap, "NO_ACTION_PRODUCED",
                       "status=success but the catalog entry has no actions",
                       refunded=False)

    db.remember_tool(action_id, origin="built", domain=gap["domain"],
                     build_id=br["id"], schema=done)
    db.finish_build(br["id"], status="success", action_id=action_id)
    print(f"\n  BUILT: {action_id}")
    print("  Live in the public catalog. Every case from here on uses it for free.")
    return 0


def _failed(build_id: str, gap: dict, code: str, error: str, *,
            refunded: bool) -> int:
    """Record a failed forge, then prove the project survives it.

    The point is not to apologise gracefully. A build that cannot happen only
    matters if it stops Persist reading the site, so the honest move is to go and
    check - right now, uncached - whether the site is still reachable by the
    ordinary chain, and print the answer either way.

    A dead end that has been measured is a finding. An unmeasured one is a broken
    demo waiting for the worst possible moment.
    """
    meaning, retryable = fallback.explain(code)
    print(f"\n  build did not land — {code}")
    if error:
        print(f"  {error}")
    print(f"  {meaning}")
    if refunded:
        print(f"  {anakin.BUILD_COST} credits refunded automatically.")
    print(f"  {'Worth retrying.' if retryable else 'Retrying will not help.'}")

    print("\n  checking whether the site is readable without a connector...")
    probe = fallback.probe(gap["url"])
    line = fallback.summarise(probe)
    print(f"  {line}")

    # TIMEOUT is not failure - the build may still land - so it stays pending.
    state = "pending" if code == "TIMEOUT" else "failed"
    db.record_build(build_id, gap["domain"], gap["goal"], status=state,
                    code=code, error=error)
    db.finish_build(build_id, status=state, error=error, code=code,
                    refunded=refunded, fallback=line)

    if probe["readable"]:
        print("\n  Persist keeps working. The connector would have been better -")
        print("  its absence costs evidence quality, not the case.")
        return 0
    print("\n  Not readable by any route right now. The ladder falls back to its")
    print("  timer, which is the behaviour it had before Wire was involved at all.")
    return 1


def main(argv: list[str]) -> int:
    args = argv[1:]
    if "--status" in args:
        return status()
    if "--build" in args:
        i = args.index("--build")
        if i + 1 >= len(args):
            print(f"--build needs a target: {', '.join(GAPS)}")
            return 1
        return build(args[i + 1])

    db.init()
    print("Gaps Persist depends on. Nothing here spends a credit.")
    for key, gap in GAPS.items():
        show_gap(key, gap)
    print(f"\ncredits: {anakin.spent()} of {settings.anakin_credit_budget} used, "
          f"{anakin.remaining()} left  (a build costs {anakin.BUILD_COST})")
    print("Build one with:  python scripts/forge.py --build cpgrams")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
