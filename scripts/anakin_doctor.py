"""Find out what Anakin can actually do for this project, before building on it.

Four questions, in the order that matters. The first three are free, so run them as
often as you like. The fourth costs 25 of a 300-credit budget and is opt-in.

  1. Is the key live, and how much of the budget is already gone?
  2. Can Anakin read the pages a direct fetch cannot - the CPGRAMS status screen and
     the India Post tracking form? This is the whole reason the integration exists.
  3. Does the Wire catalog already cover pgportal.gov.in? (When last checked: no. If
     that has changed, use the existing action and skip question 4 entirely.)
  4. --build : ask Wire to build the missing CPGRAMS action and wait for it.

Question 4 is the one that decides the shape of the demo, and it has three answers:

  success          the action is live and public. Persist can call a connector that
                   did not exist when the run started, and so can everyone else.
  failed           credits refunded. The gap-detection and the request are still
                   real; the ladder falls back to Browser Sessions to file.
  BLOCKED_WEBSITE  government sites are out of scope for their builder. Move the
                   build moment to another site in the pipeline and keep going.

    python scripts/anakin_doctor.py
    python scripts/anakin_doctor.py --build
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import anakin, db  # noqa: E402
from app.config import settings  # noqa: E402

CPGRAMS_GOAL = (
    "Given a CPGRAMS grievance registration number, open the public status page and "
    "return the current status text, the department or ministry it sits with, the "
    "date of the most recent action, and the full list of dated status entries in "
    "the grievance history."
)

PAGES = [
    ("CPGRAMS status", settings.cpgrams_status_url),
    ("CPGRAMS nodal officer directory", settings.nodal_directory_url),
    ("India Post consignment tracking", settings.indiapost_track_url),
]


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> int:
    db.init()

    rule("1. Account")
    if not anakin.configured():
        print("  ANAKIN_API_KEY is not set. Put it in .env and rerun.")
        print("  Get one at https://anakin.io - the free tier is 300 credits.")
        return 1
    print(f"  key       ...{settings.anakin_api_key[-6:]}")
    print(f"  base url  {settings.anakin_base_url}")
    print(f"  budget    {anakin.spent()} spent / {settings.anakin_credit_budget} allowed")
    print(f"  exit node {settings.anakin_country}")

    rule("2. Can it read the pages we care about?  (1 credit each)")
    for label, url in PAGES:
        got = anakin.scrape(url, use_browser=True)
        if got.ok:
            body = " ".join(got.markdown.split())
            print(f"  [ok]   {label}: {len(got.markdown)} chars")
            print(f"         {body[:160]}...")
        else:
            print(f"  [fail] {label}: {got.reason}")

    rule("3. Does the catalog already cover pgportal.gov.in?")
    hits = anakin.covers("pgportal.gov.in")
    if hits:
        print("  Already covered - do NOT pay to build it:")
        for c in hits:
            print(f"    {c['slug']}  ({c.get('action_count', '?')} actions)  {c.get('description', '')[:80]}")
    else:
        print("  Nothing in the catalog for this domain.")
        print("  That is the gap. --build fills it.")

    rule("4. Existing build requests")
    reqs = anakin.build_requests()
    if not reqs:
        print("  none yet")
    for br in reqs:
        print(f"  {br.get('status', '?'):9} {br.get('domain', '?'):28} "
              f"{br.get('action_id') or ''}")

    if "--build" not in sys.argv:
        print("\nRerun with --build to request the CPGRAMS action (25 credits, "
              "refunded if the build fails).")
        return 0

    if hits:
        print("\nRefusing to build: the catalog already covers this domain.")
        return 0

    rule("5. Requesting the build  (25 credits)")
    print(f"  goal: {CPGRAMS_GOAL}\n")
    try:
        br = anakin.build_request(settings.portal_url, CPGRAMS_GOAL, visibility="public")
    except anakin.AnakinError as exc:
        print(f"  refused: {exc}")
        print("\n  If that says BLOCKED_WEBSITE, government portals are out of scope")
        print("  for their builder. Move the build moment to another site in the")
        print("  pipeline and keep the rest of the integration as it is.")
        return 1

    print(f"  submitted: {br.get('id')}  status={br.get('status')}  "
          f"charged={br.get('credits_charged')}")
    print("  waiting (polling every 15s, giving up after 15 minutes)...")

    done = anakin.await_build(br["id"])
    if done is None:
        print("\n  Still pending after 15 minutes. Not a failure - builds are async")
        print("  with no published SLA. Check again with: python scripts/anakin_doctor.py")
        return 0

    if (done.get("status") or "").lower() == "success":
        print(f"\n  BUILT: {done.get('action_id')}")
        print("  It is live in the public catalog. Run it with POST /v1/wire/task.")
        print("  This is the demo moment - a tool that did not exist an hour ago.")
        return 0

    print(f"\n  Build failed: {done.get('error_type', '')} {done.get('error', '')}")
    print("  Credits refunded. The gap detection and the request are still real;")
    print("  file through Browser Sessions instead.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
