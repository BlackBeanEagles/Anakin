"""Sanity checks for the schema-coercion layer.

    python scripts/test_coerce.py

This is the safety net that lets free-tier models drop fields, stringify numbers, and
wrap answers in prose without breaking the app. If these fail, the ladder will crash on
real traffic in ways that are painful to debug at 2am.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testenv  # noqa: E402  - seals the mail rail; must precede app imports

_testenv.assert_sealed()  # refuses to run if anything could transmit

from app.llm import _extract_json, coerce, obj  # noqa: E402

SCHEMA = obj(
    {
        "verdict": {"type": "string", "enum": ["resolved", "deflection"]},
        "amount": {"type": "number"},
        "flag": {"type": "boolean"},
        "items": {"type": "array", "items": {"type": "string"}},
        "refs": {
            "type": "array",
            "items": obj({"kind": {"type": "string"}, "value": {"type": "string"}},
                         ["kind", "value"]),
        },
    },
    ["verdict", "amount", "flag", "items", "refs"],
)

CHECKS = [
    ("missing fields filled",
     lambda: set(coerce(SCHEMA, {"verdict": "deflection"})) == set(SCHEMA["properties"])),
    ("rupee string to number",
     lambda: coerce(SCHEMA, {"amount": "Rs 1,845"})["amount"] == 1845.0),
    ("invalid enum snapped back",
     lambda: coerce(SCHEMA, {"verdict": "nonsense"})["verdict"] == "resolved"),
    ("string bool to bool",
     lambda: coerce(SCHEMA, {"flag": "true"})["flag"] is True),
    ("scalar promoted to array",
     lambda: coerce(SCHEMA, {"items": "one"})["items"] == ["one"]),
    ("nested object unwrapped",
     lambda: coerce(SCHEMA, {"result": {"verdict": "deflection", "amount": 5}})["verdict"] == "deflection"),
    ("non-dict input survives",
     lambda: coerce(SCHEMA, "garbage")["verdict"] == "resolved"),
    ("array of objects coerced",
     lambda: coerce(SCHEMA, {"refs": [{"kind": "PNR"}]})["refs"] == [{"kind": "PNR", "value": ""}]),
    ("fenced json extracted",
     lambda: _extract_json('here:\n```json\n{"a": 1}\n```\nhope that helps') == {"a": 1}),
    ("prose-wrapped json extracted",
     lambda: _extract_json('Sure! {"b": 2} done') == {"b": 2}),
    ("unparseable returns empty",
     lambda: _extract_json("no json here at all") == {}),
]


def main() -> int:
    failed = 0
    for name, check in CHECKS:
        try:
            ok = check()
        except Exception as exc:  # noqa: BLE001
            ok, name = False, f"{name} (raised {exc})"
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        failed += not ok
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
