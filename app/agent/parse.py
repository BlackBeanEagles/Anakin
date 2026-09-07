"""Step 4: read what the department actually said and decide whether it counts.

Departments close grievances without solving them constantly - "the matter has been
forwarded to the concerned office" is a disposal, not a resolution. Detecting that
difference is what makes escalation meaningful.
"""
import json

from ..llm import obj, structured

SYSTEM = """You assess a government department's reply to a citizen's grievance in India.

The critical judgement: did the reply actually deliver the remedy the citizen asked
for, or is it a procedural non-answer dressed as a closure?

Verdict definitions:
- resolved:    The specific ask was granted, or the department states concrete
               completed action that delivers it.
- partial:     Some of the ask was met, or a firm dated commitment was made.
- deflection:  Closed or replied without addressing the ask. Includes "forwarded to
               concerned office", "matter is under process", "you may contact X",
               and closures citing no reason. This is the most common outcome.
- needs_info:  The department asked the citizen for more information.
- rejected:    Explicitly refused, with or without a reason.

Be strict. Acknowledging receipt is not resolving. Forwarding is not resolving.
If the citizen would still have the same problem tomorrow, it is not resolved."""


SCHEMA = obj(
    {
        "verdict": {
            "type": "string",
            "enum": ["resolved", "partial", "deflection", "needs_info", "rejected"],
        },
        "reasoning": {
            "type": "string",
            "description": "Why this verdict. Quote the deciding phrase from the reply.",
        },
        "unaddressed": {
            "type": "array",
            "description": "Parts of the original ask the reply left untouched.",
            "items": {"type": "string"},
        },
        "commitments": {
            "type": "array",
            "description": "Any dated or specific promise the department made.",
            "items": {"type": "string"},
        },
        "info_requested": {
            "type": "array",
            "description": "If needs_info, exactly what they asked the citizen for.",
            "items": {"type": "string"},
        },
        "amount_recovered_inr": {
            "type": "number",
            "description": "Rupees actually credited/refunded per the reply. 0 if none.",
        },
        "should_escalate": {
            "type": "boolean",
            "description": "True if the agent should climb to the next rung.",
        },
    },
    [
        "verdict", "reasoning", "unaddressed", "commitments", "info_requested",
        "amount_recovered_inr", "should_escalate",
    ],
)


def parse_response(case: dict, facts: dict, reply_text: str) -> dict:
    user = (
        f"THE CITIZEN'S ORIGINAL ASK: {facts.get('ask', 'unknown')}\n"
        f"REFERENCE NUMBERS: {json.dumps(facts.get('reference_numbers', []))}\n"
        f"CPGRAMS REG NO: {case.get('cpgrams_reg_no') or 'n/a'}\n\n"
        f"THE DEPARTMENT'S REPLY:\n---\n{reply_text}\n---\n\n"
        "Assess this reply."
    )
    return structured(
        system=SYSTEM, user=user,
        tool_name="record_assessment",
        tool_description="Record the assessment of the department's reply.",
        schema=SCHEMA, effort="high",
    )
