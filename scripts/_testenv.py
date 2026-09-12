"""Make a test run incapable of sending email. Import this first, before app code.

Every test script walks fixture cases through the ladder, and the ladder dispatches.
That was harmless while DRY_RUN defaulted to true - drafts went to outbox/ and
nothing left the machine. The moment .env was switched to DRY_RUN=false to prove the
send rail worked, a single `python scripts/check.py` delivered fourteen real emails:
the full ladder for ladder@example.com, the ask/remind/close cycle for
info@example.com, and every regression fixture in between.

Nobody was harmed - MAIL_REDIRECT_TO pointed them all at the operator's own inbox -
but that was luck rather than design. Without the redirect they would have gone to
the addresses in the fixtures, and the next fixture to carry a real department's
address would have filed a fabricated grievance.

So the guard does not depend on .env being right:

  dry_run      forced on, so mailer writes to the outbox and returns
  smtp_host    cleared, so even if something flips dry_run there is nowhere to send
  outbox       pointed at a temp directory, so a test run leaves no litter

Import it at the top of every test script, before `from app import ...`.
"""
import os
import tempfile
from pathlib import Path

# Set before app.config is imported, so the Settings class reads these.
os.environ["DRY_RUN"] = "true"
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["MAIL_REDIRECT_TO"] = ""
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault(
    "PERSIST_OUTBOX", str(Path(tempfile.gettempdir()) / "persist-test-outbox"))


def assert_sealed() -> None:
    """Belt and braces: verify after import that nothing can transmit.

    Called by each test script after it imports app.config. A guard that is only
    set and never checked is a guard that silently stops working the day someone
    reorders an import.
    """
    from app.config import settings

    problems = []
    if not settings.dry_run:
        problems.append("DRY_RUN is off")
    if settings.smtp_host:
        problems.append(f"SMTP_HOST is set to {settings.smtp_host!r}")
    if problems:
        raise SystemExit(
            "REFUSING TO RUN TESTS - they would send real email: "
            + "; ".join(problems)
            + "\nImport scripts/_testenv.py before any app module."
        )
