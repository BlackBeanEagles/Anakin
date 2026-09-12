"""The path from a forged connector to a fact about a case.

    python scripts/test_wire.py        (needs LLM_PROVIDER=mock)

This is the half of the merge that was missing until now: building a connector for
pgportal.gov.in achieves nothing if the watcher goes on scraping the same page it
always did. These tests guard the join.

The parameter binder gets the most attention here, because it is the only part that
has to work against a tool nobody has seen. Wire generates actions from English
descriptions, so whether the identifier arrives as `registration_number`, `reg_no`
or `grievance_id` is not knowable when this code is written - only that all three
mean the same thing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testenv  # noqa: E402  - seals the mail rail; must precede app imports

_testenv.assert_sealed()  # refuses to run if anything could transmit

from app import db, watch, wire  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail and not ok else ''}")


def tool(action_id: str, *, origin="built", domain="pgportal.gov.in",
         uses=0, wins=0, schema='{"params":{"required":[{"name":"reg_no"}]}}'):
    return {"action_id": action_id, "origin": origin, "domain": domain,
            "credits": 1, "uses": uses, "wins": wins, "schema_json": schema}


def main() -> int:
    db.init()
    REG = "DOPPW/E/2026/0012345"

    # ---- the binder -----------------------------------------------------
    check("synonym in the middle of a name binds",
          wire.bind_params(tool("a"), {"registration_number": REG}) == {"reg_no": REG})

    check("an exact canonical name binds",
          wire.bind_params(
              tool("a", schema='{"params":{"required":[{"name":"registration_number"}]}}'),
              {"registration_number": REG}) == {"registration_number": REG})

    check("a flat parameter list binds",
          wire.bind_params(tool("a", schema='{"params":[{"name":"grievance_id"}]}'),
                           {"registration_number": REG}) == {"grievance_id": REG})

    # The one that matters most. An action called with a value that does not belong
    # to it returns a confident answer about the wrong record - worse than silence.
    check("a mismatched value is never bound",
          wire.bind_params(tool("a"), {"consignment_number": "EX314159265IN"}) == {})

    check("empty and placeholder values are dropped",
          wire.bind_params(tool("a"), {"registration_number": "  "}) == {}
          and wire.bind_params(tool("a"), {"registration_number": "N/A"}) == {})

    check("optional parameters we cannot fill are left out",
          "lang" not in wire.bind_params(
              tool("a", schema='{"params":{"required":[{"name":"reg_no"}],'
                               '"optional":[{"name":"lang"}]}}'),
              {"registration_number": REG}))

    # No schema is not the same as no parameters: send what we are sure of and let
    # the action ignore the rest.
    check("a schema-less tool still gets its identifier",
          wire.bind_params(tool("a", schema="{}"), {"registration_number": REG})
          == {"registration_number": REG})

    # ---- tool selection -------------------------------------------------
    original_tools = db.tools
    db.tools = lambda origin="": [
        tool("catalogued", origin="catalog", uses=10, wins=10),
        tool("forged", origin="built", uses=1, wins=0),
    ]
    picked = wire.find_tool("pgportal.gov.in")
    check("a forged connector outranks a catalogued one",
          picked and picked["action_id"] == "forged", str(picked and picked["action_id"]))

    db.tools = lambda origin="": [
        tool("reliable", origin="catalog", uses=10, wins=9),
        tool("flaky", origin="catalog", uses=10, wins=1),
    ]
    picked = wire.find_tool("pgportal.gov.in")
    check("among equals the one that actually works wins",
          picked and picked["action_id"] == "reliable", str(picked and picked["action_id"]))

    check("www. and casing do not defeat the lookup",
          wire.find_tool("WWW.PgPortal.gov.in") is not None)
    check("an uncovered domain returns nothing",
          wire.find_tool("example.com") is None)
    check("an empty domain returns nothing", wire.find_tool("") is None)

    # ---- running it -----------------------------------------------------
    db.tools = lambda origin="": [tool("act_status")]
    scored: list[tuple[str, bool]] = []
    original_score = db.score_tool
    db.score_tool = lambda aid, won: scored.append((aid, won))

    import app.wire as w
    original_zt = w.anakin.zero_touch

    w.anakin.zero_touch = lambda aid, params: {
        "result": {"status": "Under process at Ministry of Railways",
                   "last_event_date": "2026-08-30"}}
    got = wire.read("pgportal.gov.in", {"registration_number": REG})
    check("a good read comes back", got is not None and got["route"] == "zero_touch")
    check("the free route is used first", got and got["route"] == "zero_touch")
    check("a hit is scored a win", scored and scored[-1] == ("act_status", True))

    # A tool that runs but returns nothing is a miss. Scoring it a win would let a
    # broken connector keep winning find_tool forever.
    scored.clear()
    w.anakin.zero_touch = lambda aid, params: {"result": {}}
    check("an empty result is not a finding",
          wire.read("pgportal.gov.in", {"registration_number": REG}) is None)
    check("an empty result is scored a miss",
          scored and scored[-1] == ("act_status", False))

    scored.clear()
    w.anakin.zero_touch = lambda aid, params: None
    original_configured = w.anakin.configured
    w.anakin.configured = lambda: False
    check("an unrunnable tool degrades to None",
          wire.read("pgportal.gov.in", {"registration_number": REG}) is None)
    w.anakin.configured = original_configured

    # Nothing to call it with must not produce a call at all.
    called: list = []
    w.anakin.zero_touch = lambda aid, params: called.append(aid)
    check("no identifier means no call",
          wire.read("pgportal.gov.in", {"registration_number": ""}) is None
          and not called)

    # ---- the shape the watcher expects ---------------------------------
    w.anakin.zero_touch = lambda aid, params: {
        "result": {"status": "Grievance disposed of", "last_event_date": "2026-09-01"}}
    got = watch._via_wire("pgportal.gov.in", {"registration_number": REG},
                          "https://pgportal.gov.in/Status/Index")
    expected = {"readable", "why_not", "status_text", "closed", "still_pending",
                "last_event_date", "evidence", "source_url", "via"}
    check("the connector answer has the same shape as a scraped one",
          got is not None and expected <= set(got), str(got and sorted(got)))
    check("a disposed grievance reads as closed", got and got["closed"] is True)
    check("closed is not also pending", got and got["still_pending"] is False)
    check("the trail records which connector answered",
          got and got["via"] == "wire:act_status", str(got and got["via"]))

    w.anakin.zero_touch = lambda aid, params: {
        "result": {"status": "Pending with Department of Posts"}}
    got = watch._via_wire("pgportal.gov.in", {"registration_number": REG}, "u")
    check("a pending grievance reads as pending",
          got and got["still_pending"] is True and got["closed"] is False)

    w.anakin.zero_touch = original_zt
    db.tools = original_tools
    db.score_tool = original_score

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
