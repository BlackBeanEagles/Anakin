"""A no-LLM baseline router, for the counterfactual.

The product's core claim is that reasoning about routing beats the obvious approach.
That claim is worth nothing unless you measure it against the obvious approach — so
this is it: keyword matching against the same taxonomy, no model, no reasoning.

It stands in for what a citizen skimming a dropdown does, and for what a naive
keyword system would do. Note what it structurally *cannot* do: refuse. It will
always name a central department, even for a pothole, because it has no notion of
scope. That failure mode is the point of the comparison.
"""
import re

from .route import load_taxonomy


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def baseline_route(narrative: str) -> dict:
    """Pick a ministry and category by keyword overlap alone."""
    tax = load_taxonomy()
    words = _tokens(narrative)
    low = narrative.lower()

    best_m, best_score = None, 0
    for m in tax["ministries"]:
        score = 0
        for kw in m["keywords"]:
            # multi-word keywords ("speed post") need a substring test
            if " " in kw:
                score += 2 if kw in low else 0
            elif kw in words:
                score += 1
        if score > best_score:
            best_m, best_score = m, score

    if best_m is None:
        # nothing matched: fall back to the first ministry, which is what a system
        # with no scope concept does — it files somewhere rather than declining
        best_m = tax["ministries"][0]

    best_c, best_c_score = best_m["categories"][0], 0
    for c in best_m["categories"]:
        hint_words = _tokens(c["name"] + " " + c["hint"])
        overlap = len(hint_words & words)
        if overlap > best_c_score:
            best_c, best_c_score = c, overlap

    return {
        "out_of_scope": False,          # structurally incapable of refusing
        "out_of_scope_reason": "",
        "ministry_id": best_m["id"],
        "ministry_name": best_m["name"],
        "category_id": best_c["id"],
        "category_name": best_c["name"],
        "confidence": 0.0,
        "rationale": f"keyword baseline: {best_score} ministry hits, {best_c_score} category hits",
        "alternates": [],
        "applicable_rule": "",
        "faster_route": "",     # a keyword matcher cannot know about better routes either
    }
