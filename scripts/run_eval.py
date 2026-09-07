"""Measure routing accuracy - the number your submission should lead with.

    python scripts/run_eval.py                  # all cases
    python scripts/run_eval.py --limit 5        # quick smoke test
    python scripts/run_eval.py --json out.json  # machine-readable

Reports three things separately, because they fail differently:
  ministry accuracy      did it pick the right department?
  category accuracy      did it pick the right specific category?
  scope accuracy         did it correctly refuse the ones CPGRAMS cannot action?

Scope accuracy matters most. Misfiling a state matter as a central one costs a citizen
three weeks and teaches them the system does not work.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import extract_facts, route_case  # noqa: E402
from app.agent.baseline import baseline_route  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import LLMError  # noqa: E402

CASES = ROOT / "evals" / "routing_cases.json"


def score(routing: dict, case: dict) -> tuple[bool, bool, bool]:
    """(scope_ok, ministry_ok, category_ok) for one routing against its label."""
    got_oos = bool(routing["out_of_scope"])
    want_oos = bool(case["expect_out_of_scope"])
    scope_ok = got_oos == want_oos
    min_ok = scope_ok and (got_oos or routing["ministry_id"] == case["expect_ministry"])
    cat_ok = scope_ok and (got_oos or routing["category_id"] == case["expect_category"])
    return scope_ok, min_ok, cat_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", type=str, default="")
    ap.add_argument("--skip-extract", action="store_true",
                    help="Route from the raw narrative only - halves the cost, slightly lower accuracy.")
    ap.add_argument("--baseline-only", action="store_true",
                    help="Run only the no-LLM keyword baseline. Free, no API key needed.")
    args = ap.parse_args()

    if settings.provider.kind == "mock" and not args.baseline_only:
        print("Refusing to run: LLM_PROVIDER=mock produces canned answers.\n"
              "Set a real provider in .env, or pass --baseline-only to measure the\n"
              "keyword baseline on its own (no key needed).")
        return 2

    data = json.loads(CASES.read_text(encoding="utf-8"))
    cases = data["cases"][: args.limit] if args.limit else data["cases"]

    print(f"\nRouting eval — {settings.provider.label} / {settings.model}")
    print(f"{len(cases)} cases\n")
    print(f"{'case':<34} {'ministry':<10} {'category':<20} {'scope':<7} conf")
    print("-" * 84)

    results, t0 = [], time.time()
    for c in cases:
        # the counterfactual: what a keyword matcher would have filed
        base = baseline_route(c["narrative"])
        b_scope, b_min, b_cat = score(base, c)

        if args.baseline_only:
            print(f"{c['id']:<34} {'PASS' if b_min else 'FAIL':<4} {base['ministry_id']:<5} "
                  f"{'PASS' if b_cat else 'FAIL':<4} {base['category_id']:<15} "
                  f"{'PASS' if b_scope else 'FAIL':<7} —")
            results.append({"id": c["id"], "scope_ok": False, "ministry_ok": False,
                            "category_ok": False, "confidence": 0.0,
                            "baseline": {"scope_ok": b_scope, "ministry_ok": b_min,
                                         "category_ok": b_cat}})
            continue

        try:
            facts = ({} if args.skip_extract else extract_facts(c["narrative"]))
            routing = route_case(facts, c["narrative"])
        except LLMError as exc:
            print(f"{c['id']:<34} ERROR: {exc}")
            results.append({"id": c["id"], "error": str(exc)})
            continue

        scope_ok, min_ok, cat_ok = score(routing, c)

        def mark(ok: bool) -> str:
            return "PASS" if ok else "FAIL"

        got_m = routing["ministry_id"] or "-"
        got_c = routing["category_id"] or "-"
        # trailing marker flags cases the agent got right that the baseline missed —
        # those are the ones that justify the model
        edge = "  <-- baseline missed this" if (cat_ok and not b_cat) else ""
        print(f"{c['id']:<34} {mark(min_ok):<4} {got_m:<5} {mark(cat_ok):<4} {got_c:<15} "
              f"{mark(scope_ok):<7} {routing['confidence']:.2f}{edge}")
        if not (min_ok and cat_ok and scope_ok):
            print(f"{'':<34} wanted {c['expect_ministry'] or 'OUT-OF-SCOPE'}"
                  f"/{c['expect_category'] or '-'} — {routing['rationale'][:90]}")

        results.append({
            "id": c["id"], "scope_ok": scope_ok, "ministry_ok": min_ok,
            "category_ok": cat_ok, "confidence": routing["confidence"],
            "got_ministry": routing["ministry_id"], "got_category": routing["category_id"],
            "want_ministry": c["expect_ministry"], "want_category": c["expect_category"],
            "rationale": routing["rationale"],
            "baseline": {"scope_ok": b_scope, "ministry_ok": b_min, "category_ok": b_cat,
                         "got_ministry": base["ministry_id"], "got_category": base["category_id"]},
        })

    ok = [r for r in results if "error" not in r]
    n = len(ok) or 1
    summary = {
        "provider": settings.provider.label,
        "model": settings.model,
        "cases": len(results),
        "errors": len(results) - len(ok),
        "scope_accuracy": round(100 * sum(r["scope_ok"] for r in ok) / n, 1),
        "ministry_accuracy": round(100 * sum(r["ministry_ok"] for r in ok) / n, 1),
        "category_accuracy": round(100 * sum(r["category_ok"] for r in ok) / n, 1),
        "mean_confidence": round(sum(r["confidence"] for r in ok) / n, 2),
        "seconds": round(time.time() - t0, 1),
    }

    b = [r["baseline"] for r in ok if "baseline" in r]
    bn = len(b) or 1
    summary["baseline"] = {
        "scope_accuracy": round(100 * sum(x["scope_ok"] for x in b) / bn, 1),
        "ministry_accuracy": round(100 * sum(x["ministry_ok"] for x in b) / bn, 1),
        "category_accuracy": round(100 * sum(x["category_ok"] for x in b) / bn, 1),
    }

    print("-" * 84)
    if args.baseline_only:
        print("  KEYWORD BASELINE ONLY (no model)")
        print(f"  scope     {summary['baseline']['scope_accuracy']}%")
        print(f"  ministry  {summary['baseline']['ministry_accuracy']}%")
        print(f"  category  {summary['baseline']['category_accuracy']}%\n")
        if args.json:
            Path(args.json).write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
            print(f"  wrote {args.json}\n")
        return 0

    print(f"{'':<12}{'agent':>10}{'keyword baseline':>20}{'delta':>10}")
    for k, label in (("scope_accuracy", "scope"), ("ministry_accuracy", "ministry"),
                     ("category_accuracy", "category")):
        a, bb = summary[k], summary["baseline"][k]
        print(f"  {label:<10}{a:>9.1f}%{bb:>19.1f}%{a - bb:>+9.1f}")
    print(f"\n  mean confidence {summary['mean_confidence']}  ·  {summary['seconds']}s"
          f"  ·  {summary['errors']} errors")

    # The counterfactual line for the submission: how many real citizens the naive
    # approach would have sent to the wrong desk.
    misfiled = sum(1 for r in ok if not r["baseline"]["category_ok"])
    print(f"\n  COUNTERFACTUAL: {misfiled} of {len(ok)} grievances routed the obvious way "
          f"would have\n  landed in the wrong place — closed weeks later as "
          f"'not related to this department'.\n")

    # Calibration: low confidence should correlate with being wrong. If it doesn't,
    # the confidence number is decorative and you should not cite it.
    wrong = [r for r in ok if not r["category_ok"]]
    if wrong:
        avg_wrong = sum(r["confidence"] for r in wrong) / len(wrong)
        print(f"  mean confidence when wrong: {avg_wrong:.2f} "
              f"(should be clearly below {summary['mean_confidence']})\n")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8"
        )
        print(f"  wrote {args.json}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
