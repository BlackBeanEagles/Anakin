"""Add adversarial cases to the routing eval.

The original 22 cases were written by the same hand that built the router, and it
scores 100% on ministry against them. That number is weaker evidence than it looks:
test data authored alongside the system inherits the system's assumptions about how a
complaint is phrased.

These 30 are written to break it. The difficulty is in the *language*, never in the
label - a case whose correct answer is genuinely arguable teaches nothing, it just
punishes the router for the author's indecision. Every case below has one defensible
answer; what varies is how hard the narrative makes it to find.

Six ways they are made hard, drawn from how people actually write complaints on public
forums: backstory before the problem, amounts included but reference numbers omitted,
the wrong authority named confidently, Hindi/English code-mixing, formal "Respected
Sir" register, and emotional registers that bury the facts.

    python scripts/expand_eval.py            add them
    python scripts/expand_eval.py --dry-run  show what would be added
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "evals" / "routing_cases.json"

NEW = [
    # ---- the citizen names the wrong authority, confidently ------------------
    dict(id="hard-wrong-authority-1", expect_ministry="DFS", expect_category="DFS-TXN",
         expect_out_of_scope=False,
         narrative="This is a railway problem so please forward to Railways. On 2 August "
                   "Rs 3,400 was debited from my SBI account for a ticket that never "
                   "got booked. Railways says the money never reached them and the bank "
                   "keeps saying wait 7 working days. It has been five weeks."),
    dict(id="hard-wrong-authority-2", expect_ministry="MOCA", expect_category="MOCA-BAG",
         expect_out_of_scope=False,
         narrative="Complaint against the airport authority. My suitcase did not come out "
                   "at Hyderabad after the IndiGo flight from Dubai on 19 August. The "
                   "airline counter gave me a file reference and said 21 days. Nothing "
                   "since. Rs 40,000 of goods inside."),

    # ---- state vs central: the trap that matters most ------------------------
    dict(id="hard-oos-state-transport", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="KSRTC bus conductor refused to give me change of Rs 30 and behaved "
                   "very rudely with an elderly passenger on the Mysore route. Bus number "
                   "and time I have noted. This kind of thing should not happen in public "
                   "transport."),
    dict(id="hard-oos-state-ration", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="Our ration card has not been updated for two years and the fair price "
                   "shop dealer says my mother's name is deleted from the list. We are not "
                   "getting rice since January. The taluk office keeps sending us back."),
    dict(id="hard-oos-state-university", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="Respected Sir, my convocation degree certificate from Bangalore "
                   "University has not been issued even after 14 months of applying and "
                   "paying the fee of Rs 1,200. I need it for a job offer which I may now "
                   "lose. Kindly do the needful urgently."),
    dict(id="hard-oos-state-water", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="Paani nahi aa raha hai for the last 11 days in our whole street. BWSSB "
                   "tanker comes only if we pay extra to the driver. Elderly people are "
                   "suffering. Please take action against the local board office."),

    # ---- private company, no central regulator -------------------------------
    dict(id="hard-oos-private-courier", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="Blue Dart lost my package containing a laptop worth Rs 62,000 sent from "
                   "Pune to Noida. Their customer care closed the ticket saying delivered "
                   "but I never received it and there is no signature proof. Waybill number "
                   "is with me."),

    # ---- code-mixed, facts buried in the middle ------------------------------
    dict(id="hard-codemix-lpg", expect_ministry="MOPNG", expect_category="MOPNG-SUBSIDY",
         expect_out_of_scope=False,
         narrative="Sir maine gas cylinder book kiya tha aur delivery bhi ho gayi lekin "
                   "subsidy amount ab tak account mein nahi aaya. Pehle har baar 8 din mein "
                   "aa jata tha. Ab teen refill ho gaye, koi subsidy nahi. Bank passbook "
                   "check kiya, kuch nahi hai. Aadhaar linked hai already."),
    dict(id="hard-codemix-epf", expect_ministry="EPFO", expect_category="EPFO-CLAIM",
         expect_out_of_scope=False,
         narrative="Maine PF withdrawal ke liye online claim daala tha last month, form 19 "
                   "and 10C dono. Claim reject ho gaya without any proper reason, sirf "
                   "likha hai 'documents not sufficient'. Meri company band ho chuki hai to "
                   "employer se attestation kaise karaun. Rs 1.8 lakh ka amount hai."),

    # ---- formal register, problem stated last --------------------------------
    dict(id="hard-formal-pension", expect_ministry="DPPW", expect_category="DPPW-FAMILY",
         expect_out_of_scope=False,
         narrative="Respected Sir/Madam, With due respect I beg to state that my late "
                   "husband was a retired employee who expired on 14 March. I submitted all "
                   "the required documents including the death certificate and the "
                   "descriptive roll to the office in April. Despite my repeated visits and "
                   "several representations, the family pension has not been sanctioned "
                   "till date. I am 71 years old and have no other source of income. Kindly "
                   "do the needful at the earliest."),
    dict(id="hard-formal-passport", expect_ministry="MEA", expect_category="MEA-PASSPORT",
         expect_out_of_scope=False,
         narrative="Respected Authority, I had applied for re-issue of my passport in the "
                   "month of June under normal category at the Seva Kendra. Police "
                   "verification was completed and marked clear on the portal itself. "
                   "However the status has been showing 'under review at RPO' for more than "
                   "nine weeks now without any movement. My employment visa processing is "
                   "held up on account of this."),

    # ---- angry, facts present but scattered ----------------------------------
    dict(id="hard-angry-tax", expect_ministry="CBDT", expect_category="CBDT-REFUND",
         expect_out_of_scope=False,
         narrative="This is absolutely ridiculous!! Filed my ITR on time in July, got the "
                   "intimation saying refund of Rs 47,320 is due, and NOTHING has come. "
                   "Bank account is pre-validated, I checked three times. Every year the "
                   "same drama. The helpline just disconnects. Do you people even work? I "
                   "am a salaried person paying tax honestly and this is what I get."),
    dict(id="hard-angry-telecom", expect_ministry="DOT", expect_category="DOT-BILL",
         expect_out_of_scope=False,
         narrative="Complete cheating by the operator. My plan is Rs 399 monthly but they "
                   "have charged me Rs 1,247 this month for so-called international roaming "
                   "which I NEVER activated and NEVER travelled anywhere. Customer care "
                   "raised a ticket and closed it saying charges are valid. Nobody explains "
                   "anything. I have the bill screenshot."),

    # ---- very short: does it still route? ------------------------------------
    dict(id="hard-short-post", expect_ministry="DOPOS", expect_category="DOPOS-MO",
         expect_out_of_scope=False,
         narrative="Money order sent 3 weeks back to my son, still not paid to him. "
                   "Receipt is with me."),
    dict(id="hard-short-rail", expect_ministry="MOR", expect_category="MOR-CATER",
         expect_out_of_scope=False,
         narrative="Pantry charged me Rs 70 for a water bottle on train 12658. No bill "
                   "given."),
    dict(id="hard-short-epf", expect_ministry="EPFO", expect_category="EPFO-TRANSFER",
         expect_out_of_scope=False,
         narrative="Changed jobs in May, PF from old company still not transferred to new "
                   "UAN."),

    # ---- long, mostly irrelevant backstory -----------------------------------
    dict(id="hard-rambling-gas", expect_ministry="MOPNG", expect_category="MOPNG-DIST",
         expect_out_of_scope=False,
         narrative="I have been living in this locality since 1998 when my father was "
                   "posted here and we have always taken our gas connection from the same "
                   "agency near the market. Earlier the service was very good, the delivery "
                   "boy would come on time and even carry the cylinder up. After the agency "
                   "changed hands about two years ago everything has gone down. Now the "
                   "point is this - they refuse to deliver unless we pay Rs 100 extra over "
                   "the receipt amount, and when I asked for a bill the manager shouted at "
                   "me in front of other customers. Other people in my building have the "
                   "same experience but nobody wants to complain."),
    dict(id="hard-rambling-bank", expect_ministry="DFS", expect_category="DFS-CHARGE",
         expect_out_of_scope=False,
         narrative="I opened this savings account in 2011 when I joined my first job and I "
                   "have never had any issue, always maintained good balance, even took a "
                   "car loan from the same branch which I repaid fully before time. My "
                   "problem is that since April they have been deducting some amount every "
                   "month, sometimes Rs 236, sometimes Rs 590, showing as service charges "
                   "and SMS charges and I don't know what all. Total about Rs 2,800 has "
                   "gone. When I went to the branch the manager said it is as per "
                   "guidelines and gave me no paper."),

    # ---- missing the one detail that blocks filing ---------------------------
    dict(id="hard-missing-year", expect_ministry="DOPOS", expect_category="DOPOS-NONDEL",
         expect_out_of_scope=False,
         narrative="Registered post sent on 6 February from Coimbatore to Ranchi has never "
                   "been delivered. The tracking shows it left the sorting office and then "
                   "stops. I have complained at the counter twice."),

    # ---- correct ministry, easily confused category --------------------------
    dict(id="hard-cat-rail-access", expect_ministry="MOR", expect_category="MOR-ACCESS",
         expect_out_of_scope=False,
         narrative="I am a wheelchair user and had booked the reserved coach seat with "
                   "disability concession. At Vijayawada there was no ramp and no wheelchair "
                   "available despite booking assistance in advance. Two coolies lifted me "
                   "which was humiliating and unsafe."),
    dict(id="hard-cat-post-office", expect_ministry="DOPOS", expect_category="DOPOS-OFFICE",
         expect_out_of_scope=False,
         narrative="The sub post office in our area opens whenever the staff feel like, "
                   "usually 11 am instead of 9, and shuts for two hours at lunch. Senior "
                   "citizens stand outside waiting. The counter clerk is often not at the "
                   "seat even during working hours."),
    dict(id="hard-cat-tds", expect_ministry="CBDT", expect_category="CBDT-TDS",
         expect_out_of_scope=False,
         narrative="My employer deducted TDS of Rs 88,000 for the financial year but it is "
                   "not reflecting in my Form 26AS, only Rs 31,000 is showing. I have the "
                   "salary slips showing the full deduction. Because of this my return is "
                   "showing a demand instead of a refund."),
    dict(id="hard-cat-ecom", expect_ministry="DOCA", expect_category="DOCA-ECOM",
         expect_out_of_scope=False,
         narrative="Ordered a mixer grinder on an online marketplace during the sale, paid "
                   "Rs 4,299 by UPI. They delivered an empty box with a brick inside. "
                   "Return request was rejected twice saying the product was verified at "
                   "dispatch. I have the unboxing video."),
    dict(id="hard-cat-warranty", expect_ministry="DOCA", expect_category="DOCA-WARRANTY",
         expect_out_of_scope=False,
         narrative="Bought a refrigerator with 10 year compressor warranty. Compressor "
                   "failed in the third year. Service centre says the warranty is void "
                   "because I did not get the annual servicing done, which was never "
                   "mentioned anywhere in the warranty card."),
    dict(id="hard-cat-mnp", expect_ministry="DOT", expect_category="DOT-MNP",
         expect_out_of_scope=False,
         narrative="I raised a porting request with the UPC code more than a month ago. The "
                   "new operator says the request keeps getting rejected by the existing "
                   "operator citing dues, but I have zero outstanding and my bill is paid "
                   "till date. Now the UPC has expired twice."),
    dict(id="hard-cat-lc", expect_ministry="DPPW", expect_category="DPPW-LC",
         expect_out_of_scope=False,
         narrative="My father is 84 and bedridden. We submitted the Jeevan Pramaan digital "
                   "life certificate through the bank in November and got the acknowledgement. "
                   "The pension has still been stopped from December saying life certificate "
                   "not received. The bank says it is at the department end."),

    # ---- in scope but reads out of scope, and the reverse --------------------
    dict(id="hard-looks-oos-mohua", expect_ministry="MOHUA", expect_category="MOHUA-GEN",
         expect_out_of_scope=False,
         narrative="I was sanctioned a house under the central urban housing scheme and the "
                   "first instalment came in 2023. The second and third instalments have "
                   "never been released although the construction was verified and "
                   "photographed by the officials. The house is half built and I have taken "
                   "a private loan to survive."),
    dict(id="hard-looks-central-oos", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="The government hospital in our district has no functioning MRI machine "
                   "for the past eight months and patients are being told to go to private "
                   "centres and pay Rs 8,000. This is a government facility funded by "
                   "public money and somebody must answer."),

    # ---- mechanism confusion -------------------------------------------------
    dict(id="hard-oos-rti-appeal", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="I filed an RTI in May asking for the file notings on my land record "
                   "and the PIO has not replied within 30 days. I want to file the first "
                   "appeal but I do not know the appellate authority's name. Please help me "
                   "get the information."),
    dict(id="hard-oos-consumer-court", expect_ministry=None, expect_category=None,
         expect_out_of_scope=True,
         narrative="My case against a builder is going on in the consumer commission since "
                   "2021 for possession of my flat. The hearings keep getting adjourned and "
                   "the opposite party does not appear. I want the commission to be directed "
                   "to decide the matter quickly."),
]


def main() -> int:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    existing = {c["id"] for c in cases}

    fresh = [c for c in NEW if c["id"] not in existing]
    dupes = [c["id"] for c in NEW if c["id"] in existing]

    # Every label has to name something that actually exists, or the eval measures
    # the fixture's typos rather than the router.
    taxonomy = json.loads((ROOT / "app" / "taxonomy.json").read_text(encoding="utf-8"))
    valid_m = {m["id"] for m in taxonomy["ministries"]}
    valid_c = {c["id"] for m in taxonomy["ministries"] for c in m["categories"]}
    bad = []
    for c in NEW:
        if c["expect_out_of_scope"]:
            if c["expect_ministry"] or c["expect_category"]:
                bad.append(f"{c['id']}: out-of-scope case carries a label")
            continue
        if c["expect_ministry"] not in valid_m:
            bad.append(f"{c['id']}: unknown ministry {c['expect_ministry']}")
        if c["expect_category"] not in valid_c:
            bad.append(f"{c['id']}: unknown category {c['expect_category']}")
    if bad:
        print("REFUSING - invalid labels:")
        for b in bad:
            print("  " + b)
        return 1

    oos = sum(1 for c in NEW if c["expect_out_of_scope"])
    print(f"{len(NEW)} adversarial cases, {oos} of them out-of-scope traps")
    if dupes:
        print(f"already present, skipping: {', '.join(dupes)}")
    print(f"eval goes from {len(cases)} to {len(cases) + len(fresh)} cases")

    if "--dry-run" in sys.argv:
        print("\n(dry run - nothing written)")
        return 0

    cases.extend(fresh)
    if isinstance(data, dict):
        data.setdefault("_meta", {})["provenance"] = (
            "The first 22 cases were authored alongside the router. The 'hard-' cases "
            "were added afterwards to break it: written from the patterns real "
            "complaints show on public consumer forums - backstory before the problem, "
            "amounts given but reference numbers omitted, the wrong authority named "
            "confidently, Hindi/English code-mixing, and formal or angry registers. No "
            "complaint text is reproduced from any source and no real names, numbers or "
            "addresses appear."
        )
        data["_meta"]["count"] = len(cases)
    EVAL.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten to {EVAL.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
