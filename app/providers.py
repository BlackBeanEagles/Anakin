"""Model providers.

Groq and Gemini both expose OpenAI-compatible endpoints, so one client class covers
both; Anthropic uses its own SDK. Switching providers is a one-line .env change - the
reasoning code in agent/ never knows which one is behind it.

Free tiers as of writing:
  groq    - generous free tier, very fast, good tool-calling on the 70B models
  gemini  - free tier via AI Studio, large context, solid instruction following
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    kind: str                 # "openai_compatible" | "anthropic" | "mock"
    base_url: str
    api_key_env: str
    default_model: str
    console_url: str
    notes: str = ""


PROVIDERS: dict[str, Provider] = {
    "groq": Provider(
        key="groq",
        label="Groq",
        kind="openai_compatible",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        default_model="llama-3.3-70b-versatile",
        console_url="https://console.groq.com/keys",
        notes="Fastest free option. If a model refuses tool calls, the JSON fallback catches it.",
    ),
    "gemini": Provider(
        key="gemini",
        label="Google Gemini",
        kind="openai_compatible",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
        default_model="gemini-2.0-flash",
        console_url="https://aistudio.google.com/apikey",
        notes="Large context; good at the routing step. Forced tool choice is flaky - fallback handles it.",
    ),
    "anthropic": Provider(
        key="anthropic",
        label="Anthropic",
        kind="anthropic",
        base_url="",
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-opus-5",
        console_url="https://console.anthropic.com/settings/keys",
        notes="Best reasoning quality, especially on the routing and appeal steps. Paid.",
    ),
    "mock": Provider(
        key="mock",
        label="Offline mock",
        kind="mock",
        base_url="",
        api_key_env="",
        default_model="none",
        console_url="",
        notes="Canned keyword responses. No key, no tokens. Never demo from this.",
    ),
}


def get(name: str) -> Provider:
    p = PROVIDERS.get((name or "").strip().lower())
    if not p:
        valid = ", ".join(PROVIDERS)
        raise ValueError(f"Unknown LLM_PROVIDER '{name}'. Choose one of: {valid}")
    return p
