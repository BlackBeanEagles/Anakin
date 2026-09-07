"""Seed a few realistic cases so you can watch the agent work immediately.

    python scripts/seed_demo.py

These are fictional but shaped like real intake text: rambling, out of order, with the
reference number buried mid-sentence. That is deliberate — it exercises the extractor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

CASES = [
    dict(
        citizen_name="Priya Raghavan",
        citizen_email="priya.demo@example.com",
        citizen_phone="",
        category="india_post",
        narrative_raw=(
            "I sent important college documents by Speed Post from Jayanagar post office "
            "Bangalore on 12th August to my sister in Patna. The consignment number is "
            "EX412876539IN. Tracking showed it reached Bangalore sorting hub on 14 August "
            "and then nothing, it has not updated at all since then. It is now more than a "
            "month. I called 1924 three times, first time they said wait 7 days, second time "
            "they said they will register a complaint but gave me no number, third time the "
            "call just disconnected. I also went to the post office and the counter person "
            "said they cannot do anything once it leaves. These are original transcripts and "
            "getting duplicates will cost me around 4000 rupees plus I will miss my sister's "
            "admission deadline. I just want them to either find it or compensate."
        ),
    ),
    dict(
        citizen_name="Arun Mehta",
        citizen_email="arun.demo@example.com",
        citizen_phone="",
        category="railways",
        narrative_raw=(
            "Booked a tatkal ticket on IRCTC on 3 September for train 12627 Bangalore to "
            "Chennai, PNR 6472891053. Money got deducted 1,845 rupees from my HDFC account "
            "but the ticket was never generated, the page just showed a failure error. IRCTC "
            "says transaction failed and bank has to refund, bank says IRCTC has the money. "
            "I filed a TDR on the IRCTC site on 4 September, reference number 100004829173, "
            "and it has been sitting as 'under process' ever since. Almost three weeks now. "
            "Nobody picks up 139. I want my 1845 rupees back."
        ),
    ),
    dict(
        citizen_name="Fatima Sheikh",
        citizen_email="fatima.demo@example.com",
        citizen_phone="",
        category="other",
        narrative_raw=(
            "There is a huge pothole outside my building on 4th cross road in Indiranagar "
            "and two scooters have already fallen. The BBMP people came once, looked at it, "
            "and left. I have complained on the BBMP app twice. Someone is going to get "
            "seriously hurt. Please help me escalate this to the government."
        ),
    ),
]


def main() -> None:
    db.init()
    for c in CASES:
        cid = db.create_case(**c)
        print(f"  created {cid}  {c['citizen_name']:<18} {c['category']}")
    print(
        "\nSeeded. Start the server (python run.py), open http://127.0.0.1:8000 and the\n"
        "agent will pick these up on its next tick.\n\n"
        "Watch for: the third case should be REFUSED — a municipal pothole is not a\n"
        "CPGRAMS matter, and refusing to misfile is the router doing its job."
    )


if __name__ == "__main__":
    main()
