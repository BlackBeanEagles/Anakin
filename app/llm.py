"""One structured-reasoning call, whichever provider is configured.

Every reasoning step in this app returns structured data, so they all go through
`structured()`. Three layers of defence, because free-tier models drop fields:

  1. Native tool/function calling with the JSON schema.
  2. If the model returns prose instead of a tool call, retry in JSON mode with the
     schema inlined in the prompt.
  3. Coerce whatever comes back against the schema so required keys always exist.

Downstream code can therefore index results directly without defensive .get() chains.
"""
import json
import logging
import re
from typing import Any

from .config import settings
from .providers import Provider

log = logging.getLogger("persist.llm")

_client: Any = None


class LLMError(RuntimeError):
    pass


# ------------------------------------------------------------------ clients


def _get_client():
    global _client
    if _client is not None:
        return _client

    p: Provider = settings.provider
    key = settings.api_key

    if p.kind == "anthropic":
        import anthropic
        _client = anthropic.Anthropic(api_key=key or None)
    elif p.kind == "openai_compatible":
        from openai import OpenAI
        if not key:
            raise LLMError(
                f"{p.label} needs {p.api_key_env} set in .env. Get one free at {p.console_url}"
            )
        _client = OpenAI(api_key=key, base_url=p.base_url, timeout=120.0, max_retries=2)
    else:
        _client = None
    return _client


# ------------------------------------------------------------------ schema helpers


def obj(properties: dict, required: list[str]) -> dict:
    """Strict-mode object schema: every field required, no extras."""
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _blank_for(spec: dict) -> Any:
    t = spec.get("type")
    if t == "string":
        return spec.get("enum", [""])[0] if spec.get("enum") else ""
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    return None


def coerce(schema: dict, data: Any) -> dict:
    """Make `data` satisfy `schema` well enough for the app to run.

    Free-tier models omit fields, return numbers as strings, and occasionally wrap the
    answer in an extra layer. Fixing that here beats sprinkling .get() everywhere.
    """
    if not isinstance(data, dict):
        data = {}

    props: dict = schema.get("properties", {})

    # Some models nest the real answer one level down under a single key.
    if props and not (set(props) & set(data)) and len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict) and (set(props) & set(inner)):
            data = inner

    out: dict = {}
    for name, spec in props.items():
        val = data.get(name, None)
        t = spec.get("type")

        if val is None:
            out[name] = _blank_for(spec)
            continue

        if t in ("number", "integer"):
            try:
                num = float(re.sub(r"[^\d.\-]", "", str(val)) or 0)
                out[name] = int(num) if t == "integer" else num
            except (TypeError, ValueError):
                out[name] = 0
        elif t == "boolean":
            out[name] = val if isinstance(val, bool) else str(val).strip().lower() in {"true", "yes", "1"}
        elif t == "string":
            out[name] = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            if spec.get("enum") and out[name] not in spec["enum"]:
                out[name] = spec["enum"][0]
        elif t == "array":
            items = val if isinstance(val, list) else ([val] if val else [])
            item_spec = spec.get("items", {})
            if item_spec.get("type") == "object":
                out[name] = [coerce(item_spec, i) for i in items if isinstance(i, dict)]
            else:
                out[name] = [i if isinstance(i, str) else json.dumps(i, ensure_ascii=False)
                             for i in items]
        elif t == "object":
            out[name] = coerce(spec, val)
        else:
            out[name] = val
    return out


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a response that may be wrapped in prose or fences."""
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------------ the call


def structured(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    schema: dict,
    effort: str = "high",
    max_tokens: int = 8000,
) -> dict:
    """One reasoning step. Returns a dict guaranteed to match `schema`'s shape."""
    p: Provider = settings.provider

    if p.kind == "mock":
        from . import mock
        log.info("MOCK: %s", tool_name)
        return mock.respond(tool_name, user)

    if p.kind == "anthropic":
        raw = _call_anthropic(system, user, tool_name, tool_description, schema,
                              effort, max_tokens)
    else:
        raw = _call_openai_compatible(system, user, tool_name, tool_description, schema,
                                      max_tokens)

    return coerce(schema, raw)


def _call_anthropic(system, user, tool_name, tool_description, schema, effort, max_tokens) -> dict:
    import anthropic

    client = _get_client()
    tool = {"name": tool_name, "description": tool_description,
            "input_schema": schema, "strict": True}
    try:
        resp = client.messages.create(
            model=settings.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError as exc:
        raise LLMError("ANTHROPIC_API_KEY is missing or invalid - check your .env") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Rate limited; the tick will retry") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the Anthropic API") from exc

    if resp.stop_reason == "refusal":
        detail = getattr(resp.stop_details, "explanation", "") if resp.stop_details else ""
        raise LLMError(f"Model declined this request. {detail}")

    for block in resp.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise LLMError(f"No tool call returned (stop_reason={resp.stop_reason})")


def _call_openai_compatible(system, user, tool_name, tool_description, schema, max_tokens) -> dict:
    """Groq / Gemini. Try function calling, fall back to JSON mode."""
    import openai

    client = _get_client()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    tools = [{
        "type": "function",
        "function": {"name": tool_name, "description": tool_description, "parameters": schema},
    }]

    def _fail(exc: Exception) -> LLMError:
        if isinstance(exc, openai.AuthenticationError):
            p = settings.provider
            return LLMError(f"{p.api_key_env} is missing or invalid. Free key: {p.console_url}")
        if isinstance(exc, openai.RateLimitError):
            return LLMError("Rate limited by the provider's free tier; the tick will retry")
        if isinstance(exc, openai.APIStatusError):
            return LLMError(f"{settings.provider.label} error {exc.status_code}: {exc.message}")
        if isinstance(exc, openai.APIConnectionError):
            return LLMError(f"Could not reach {settings.provider.label}")
        return LLMError(str(exc))

    # --- attempt 1: function calling
    try:
        resp = client.chat.completions.create(
            model=settings.model,
            messages=messages,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": tool_name}},
            max_tokens=max_tokens,
            temperature=0.2,
        )
        calls = resp.choices[0].message.tool_calls
        if calls:
            return json.loads(calls[0].function.arguments)
        text = resp.choices[0].message.content or ""
        parsed = _extract_json(text)
        if parsed:
            return parsed
    except (openai.APIError, openai.APIConnectionError) as exc:
        log.warning("tool-call attempt failed on %s: %s", settings.provider.label, exc)
    except (json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.warning("tool-call response unusable: %s", exc)

    # --- attempt 2: JSON mode with the schema inlined
    json_system = (
        system
        + "\n\nRespond with a single JSON object and nothing else - no prose, no code "
          "fences. It must match this JSON Schema exactly, including every required "
          "field:\n"
        + json.dumps(schema, indent=2)
    )
    try:
        resp = client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "system", "content": json_system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=0.2,
        )
        parsed = _extract_json(resp.choices[0].message.content or "")
        if parsed:
            return parsed
        raise LLMError("Model returned no parseable JSON")
    except openai.BadRequestError:
        # Some models reject response_format; try once more without it.
        try:
            resp = client.chat.completions.create(
                model=settings.model,
                messages=[{"role": "system", "content": json_system},
                          {"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            parsed = _extract_json(resp.choices[0].message.content or "")
            if parsed:
                return parsed
            raise LLMError("Model returned no parseable JSON")
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc
