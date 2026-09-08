"""Check taxonomy.json against reality, and say plainly what only a human can check.

`app/taxonomy.json` decides who a citizen's grievance gets sent to. It was written
from public sources and never verified, which makes it the highest-consequence
unchecked thing in the project: a wrong address does not fail loudly, it fails by
sending a real person's complaint into a void and reporting success.

What this can check on its own:

  - the address is syntactically real and not a placeholder
  - its domain resolves and accepts mail (an MX record exists)
  - the domain belongs to a government namespace rather than somewhere else
  - the officer's name still appears in the live nodal directory

What it cannot check, and does not pretend to:

  - whether a role address is still read by anyone
  - whether a named officer is still in that posting
  - whether the category ids match CPGRAMS's own dropdowns today

Those need one manual filing. This narrows that job to whatever it flags rather than
asking someone to re-check all twelve.

    python scripts/verify_taxonomy.py           full check
    python scripts/verify_taxonomy.py --offline skip the network
"""
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLACEHOLDER = ("example.com", "verify-me", "changeme", "todo", "test@", "@test")
GOV_SUFFIXES = ("gov.in", "nic.in", ".gov", "gov.uk")

problems: list[str] = []
warnings: list[str] = []
notes: list[str] = []


def accepts_mail(domain: str) -> bool | None:
    """Does anything accept mail for this domain? None when we cannot tell.

    MX first, and MX is the point: mail routing does not use A records, and several
    Indian government domains have no A record at all. An earlier version of this
    checked getaddrinfo and duly reported that gov.in and nic.in "cannot receive
    mail" - both of which route through mgovcloud.in and receive it perfectly well.
    Ten false alarms out of twelve ministries.

    A record is kept only as a fallback for when no resolver is available, and a
    domain is reported broken only when both say nothing.
    """
    try:
        out = subprocess.run(["nslookup", "-type=mx", domain],
                             capture_output=True, text=True, timeout=12).stdout.lower()
        if "mail exchanger" in out:
            return True
    except (OSError, subprocess.SubprocessError):
        pass  # no resolver here; fall through to the weaker check

    try:
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror:
        return False
    except Exception:  # noqa: BLE001 - a flaky resolver is not a finding
        return None


def main() -> int:
    offline = "--offline" in sys.argv
    taxonomy = json.loads((ROOT / "app" / "taxonomy.json").read_text(encoding="utf-8"))
    ministries = taxonomy["ministries"]

    print(f"Checking {len(ministries)} ministries in app/taxonomy.json\n")

    seen_ids: set[str] = set()
    seen_emails: dict[str, str] = {}
    domains: dict[str, bool | None] = {}

    for m in ministries:
        mid = m.get("id", "?")
        name = m.get("name", "?")
        email = (m.get("grievance_officer_email") or "").strip()
        kind = m.get("email_kind", "")

        if mid in seen_ids:
            problems.append(f"{mid}: duplicate ministry id")
        seen_ids.add(mid)

        if not m.get("categories"):
            problems.append(f"{mid}: no categories - nothing can route to it")
        if not m.get("keywords"):
            warnings.append(f"{mid}: no keywords - the router will rarely pick it")

        if kind == "none" or not email:
            notes.append(f"{mid} ({name}): no address on file, routes by portal only")
            continue

        if not EMAIL.match(email):
            problems.append(f"{mid}: '{email}' is not a valid address")
            continue
        if any(p in email.lower() for p in PLACEHOLDER):
            problems.append(f"{mid}: '{email}' is a placeholder, not a real address")
            continue

        # The same address on two ministries usually means one was copied and not
        # edited - which sends a grievance to the wrong department silently.
        if email in seen_emails:
            problems.append(f"{mid}: shares an address with {seen_emails[email]} ({email})")
        seen_emails[email] = mid

        domain = email.split("@")[1].lower()
        if not any(domain.endswith(s) or s in domain for s in GOV_SUFFIXES):
            warnings.append(f"{mid}: {domain} is not a government domain")

        if not offline and domain not in domains:
            domains[domain] = accepts_mail(domain)
        if domains.get(domain) is False:
            warnings.append(f"{mid}: no MX or A record found for {domain} - worth "
                            f"confirming by hand before trusting it")

        # A named individual rotates with the posting; a role address outlives them.
        # Both are usable, but only one needs re-checking every few months.
        if kind == "individual":
            notes.append(f"{mid}: addressed to {m.get('officer_name', 'a named officer')} "
                         f"- goes stale when they are transferred")

    # ---- the live directory ------------------------------------------------
    if not offline:
        print("Reading the live nodal officer directory...\n")
        try:
            from app import watch
            drift = watch.refresh_officer_directory()
            if not drift.get("ok"):
                warnings.append(f"nodal directory unreadable ({drift.get('reason')}) "
                                f"- addresses could not be cross-checked")
            else:
                for d in drift.get("drift", []):
                    warnings.append(
                        f"{d['ministry_id']}: {d['on_file']} does not appear in the "
                        f"live directory ({d.get('email_kind', '')})")
                if not drift.get("drift"):
                    print("  every address on file appears in the live directory\n")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"directory check failed: {exc}")

    # ---- report -------------------------------------------------------------
    for label, items in (("MUST FIX", problems), ("CHECK", warnings), ("NOTE", notes)):
        if items:
            print(f"{label}")
            for i in items:
                print(f"  {i}")
            print()

    print("What this cannot tell you, and a single manual filing can:")
    print("  1. whether a role address is still read by anyone")
    print("  2. whether the category ids still match the portal's own dropdowns")
    print("  3. whether a named officer is still in post")
    print()
    print(f"{len(problems)} must-fix, {len(warnings)} to check, "
          f"{len(notes)} noted, {len(ministries)} ministries")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
