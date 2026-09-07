"""Check the setup before you waste an hour debugging the wrong thing.

    python scripts/doctor.py

Verifies: provider selection, API key present, a real round-trip to the model with a
structured response, database, taxonomy verification status, and the mail rails.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.agent.route import load_taxonomy  # noqa: E402
from app.config import ROOT as APP_ROOT, settings  # noqa: E402
from app.llm import LLMError, obj, structured  # noqa: E402

OK, WARN, BAD = "  [ok]  ", "  [warn]", "  [FAIL]"


def main() -> int:
    problems = 0
    print(f"\nPersist doctor\n{'-' * 60}")

    # ---- provider
    p = settings.provider
    print(f"{OK} provider: {p.label} ({p.key})")
    print(f"{OK} model:    {settings.model}")

    if p.kind == "mock":
        print(f"{WARN} mock mode — canned answers. Fine for UI work, never for a demo.")
    elif settings.key_missing:
        print(f"{BAD} {p.api_key_env} is not set. Free key: {p.console_url}")
        problems += 1
    else:
        print(f"{OK} {p.api_key_env} is set ({len(settings.api_key)} chars)")

    # ---- live round trip
    if p.kind != "mock" and not settings.key_missing:
        print(f"\n  calling {p.label} …")
        schema = obj({
            "ministry": {"type": "string"},
            "confident": {"type": "boolean"},
            "score": {"type": "number"},
        }, ["ministry", "confident", "score"])
        try:
            got = structured(
                system="You classify Indian government grievances. Answer only via the tool.",
                user="A Speed Post parcel with consignment EX123456789IN never arrived. "
                     "Which department owns this? Reply with ministry='Department of Posts', "
                     "confident=true, score=0.9",
                tool_name="classify",
                tool_description="Record the classification.",
                schema=schema,
                effort="low",
                max_tokens=1000,
            )
            print(f"{OK} round trip succeeded: {got}")
            if not isinstance(got.get("confident"), bool) or not isinstance(got.get("score"), (int, float)):
                print(f"{WARN} types came back loose; the coercion layer handled it")
        except LLMError as exc:
            print(f"{BAD} model call failed: {exc}")
            problems += 1

    # ---- database
    print()
    try:
        db.init()
        s = db.stats()
        print(f"{OK} database at {settings.db_path.name}: {s['total']} cases, "
              f"{s['filed']} filed, {s['resolved']} resolved")
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} database error: {exc}")
        problems += 1

    # ---- taxonomy
    tax = load_taxonomy()
    meta = tax["_meta"]
    n_min = len(tax["ministries"])
    n_cat = sum(len(m["categories"]) for m in tax["ministries"])

    if meta.get("categories_verified"):
        print(f"{OK} categories verified: {n_min} ministries, {n_cat} categories")
    else:
        print(f"{WARN} category names NOT verified ({n_min} ministries, {n_cat} categories).")
        print("         File one grievance manually on pgportal.gov.in, screenshot every")
        print("         dropdown, then set _meta.categories_verified=true.")

    kinds: dict[str, list[str]] = {}
    for m in tax["ministries"]:
        kinds.setdefault(m.get("email_kind", "unknown"), []).append(m["id"])

    if meta.get("officers_verified"):
        print(f"{OK} officer contacts from {meta.get('officers_source')} "
              f"(checked {meta.get('officers_checked_on')})")
    else:
        print(f"{BAD} officer contacts unverified")
        problems += 1

    print(f"{OK} {len(kinds.get('role', []))} durable role addresses: "
          f"{', '.join(kinds.get('role', [])) or 'none'}")
    if kinds.get("individual"):
        print(f"{WARN} {len(kinds['individual'])} named-individual addresses "
              f"({', '.join(kinds['individual'])}) — these rotate with the postholder.")
        print(f"         Re-check against {meta.get('officers_source')} before a long campaign.")
    if kinds.get("none"):
        print(f"{OK} {len(kinds['none'])} with no published address ({', '.join(kinds['none'])}) — "
              f"the email rung is skipped for these, not faked.")

    placeholders = [m["id"] for m in tax["ministries"]
                    if "VERIFY-ME" in (m.get("grievance_officer_email") or "")]
    if placeholders:
        print(f"{BAD} {len(placeholders)} placeholder addresses remain: {', '.join(placeholders)}")
        problems += 1

    # ---- mail rails
    print()
    if settings.dry_run:
        print(f"{OK} DRY_RUN on — nothing transmits; drafts go to outbox/")
    elif not settings.smtp_host:
        print(f"{BAD} DRY_RUN is off but SMTP_HOST is empty — sends will fail")
        problems += 1
    else:
        print(f"{WARN} DRY_RUN is OFF — real email will be sent via {settings.smtp_host}")

    print(f"{OK} inbound email: {'polling ' + settings.imap_host if settings.imap_enabled else 'disabled (paste replies in the console)'}")

    # ---- production readiness
    print(f"\n{'-' * 60}\nproduction readiness")
    prod = []
    if settings.public_base_url.startswith("https"):
        # only judge these once this looks like a real deployment
        if settings.time_scale != 1.0:
            prod.append(f"TIME_SCALE is {settings.time_scale}, not 1.0 — the agent would "
                        f"escalate to a Director within minutes of filing")
        if settings.console_password in ("persist", "changeme") or len(settings.console_password) < 16:
            prod.append("CONSOLE_PASSWORD is weak — anyone who guesses it can approve "
                        "correspondence sent in real people's names")
        if settings.data_dir == APP_ROOT:
            prod.append("PERSIST_DATA_DIR is unset — on a container filesystem every case "
                        "is lost on the next deploy")
        if settings.provider.kind == "mock":
            prod.append("LLM_PROVIDER=mock on a public URL")
        print(f"{OK} public URL: {settings.public_base_url}")
    else:
        print(f"{OK} local ({settings.public_base_url}) — production checks skipped")

    for issue in prod:
        print(f"{BAD} {issue}")
    problems += len(prod)
    if settings.public_base_url.startswith("https") and not prod:
        print(f"{OK} production settings look right")

    print(f"{'-' * 60}")
    print("all clear\n" if problems == 0 else f"{problems} blocking problem(s)\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
