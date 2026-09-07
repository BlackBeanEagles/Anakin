"""Runtime configuration. Everything reads from .env; sane defaults are safe defaults."""
import os
from pathlib import Path  # noqa: F401  (used for db_path)

from dotenv import load_dotenv

from . import providers

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # ---- model provider ----
    # groq | gemini | anthropic | mock
    provider_name = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    provider = providers.get(provider_name)

    # Model override; falls back to the provider's sensible default.
    model = os.getenv("LLM_MODEL", "").strip() or provider.default_model
    api_key = os.getenv(provider.api_key_env, "").strip() if provider.api_key_env else ""

    # ---- safety ----
    # When true, no email ever leaves the machine. Written to outbox/ instead.
    dry_run = _bool("DRY_RUN", True)

    # ---- demo time compression ----
    # Real ladder waits are expressed in days; multiply by this to get the actual wait.
    # 0.0001 turns 21 days into ~3 minutes.
    time_scale = float(os.getenv("TIME_SCALE", "0.0001"))
    tick_seconds = int(os.getenv("TICK_SECONDS", "20"))

    console_password = os.getenv("CONSOLE_PASSWORD", "changeme")

    # ---- outbound email ----
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", "")

    # ---- inbound email (department replies land here) ----
    imap_host = os.getenv("IMAP_HOST", "")
    imap_port = int(os.getenv("IMAP_PORT", "993") or 993)
    imap_user = os.getenv("IMAP_USER", "")
    imap_password = os.getenv("IMAP_PASSWORD", "")
    imap_folder = os.getenv("IMAP_FOLDER", "INBOX")
    imap_poll_seconds = int(os.getenv("IMAP_POLL_SECONDS", "120"))

    # ---- WhatsApp intake (Twilio) ----
    # Without this the webhook refuses every request rather than trusting unsigned ones.
    twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")

    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

    # Everything that must survive a redeploy lives under DATA_DIR. In production this
    # is a mounted volume; without it the container's filesystem is ephemeral and every
    # case vanishes on the next deploy — which would destroy the whole point of a
    # ledger that accumulates.
    data_dir = Path(os.getenv("PERSIST_DATA_DIR") or ROOT)

    # PERSIST_DB lets the test runner point at a throwaway database so a check run
    # can never touch real cases.
    db_path = Path(os.getenv("PERSIST_DB") or (data_dir / "persist.db"))
    outbox = Path(os.getenv("PERSIST_OUTBOX") or (data_dir / "outbox"))
    web_cache = Path(os.getenv("PERSIST_WEB_CACHE") or (data_dir / "webcache"))

    # Filing route. CPGRAMS requires a citizen-owned account, so the agent prepares the
    # packet and the citizen performs the one credentialed keystroke. We never ask for,
    # store, or use anyone's portal password.
    portal_name = "CPGRAMS"
    portal_url = "https://pgportal.gov.in"

    # Public pages the watcher reads. Government sites move; keeping these here
    # means a broken source is a config edit, not a code change.
    cpgrams_status_url = os.getenv(
        "CPGRAMS_STATUS_URL", "https://pgportal.gov.in/Status/Index")
    nodal_directory_url = os.getenv(
        "NODAL_DIRECTORY_URL", "https://pgportal.gov.in/Home/NodalPgOfficers")
    indiapost_track_url = os.getenv(
        "INDIAPOST_TRACK_URL", "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx")
    web_reading_enabled = _bool("WEB_READING", True)

    @property
    def mock_llm(self) -> bool:
        return self.provider.kind == "mock"

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.twilio_auth_token)

    @property
    def imap_enabled(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)

    @property
    def key_missing(self) -> bool:
        return self.provider.kind != "mock" and not self.api_key


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.outbox.mkdir(parents=True, exist_ok=True)
