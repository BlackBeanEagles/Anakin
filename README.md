# Persist

**An agent that fights bureaucracies for people, in public, and doesn't let go.**

Citizens send a stuck India Post parcel, an unpaid IRCTC refund, a claim nobody
answers. Persist routes it to the correct central-government authority, drafts the
filing properly, and then tracks and escalates it for as long as it takes — the
grievance officer, the appellate authority, a phone call to the regional office, the
Director of Public Grievances.

Every case, every action, and every loss is on a public ledger.

---

## The thesis

CPGRAMS grievances fail for two reasons, and both are reasoning problems:

1. **Wrong routing.** Picking the right Ministry → Organisation → Category out of a
   taxonomy with hundreds of leaves is hard. Get it wrong and the grievance is closed
   three weeks later as "not related to this department."
2. **Too vague to action.** Citizens write emotionally and out of order. Departments
   close grievances that lack dates, reference numbers, and one specific ask.

Persist fixes both, then refuses to let the matter drop.

---

## The credential boundary

CPGRAMS requires a citizen-owned account. **Persist never asks for, stores, or uses
anyone's portal password.** The work splits cleanly:

- **The human does one keystroke.** The agent produces a ready-to-submit packet —
  exact ministry path, exact category, drafted text. The citizen pastes and submits
  on their own account, then sends back the registration number.
- **The agent does the other 30 days.** Tracking, parsing replies, drafting appeals,
  emailing officers, escalating, following up.

> *The agent does everything except the one keystroke that legally has to be the citizen's.*

---

## The escalation ladder

This is what makes it an agent rather than a form-filler. Each rung has its own
audience, its own waiting period, and its own argument — an appeal does not restate
the grievance, it attacks the inadequacy of the disposal.

| Rung | Stage | Channel | Then waits |
|-----:|-------|---------|-----------|
| 0 | Preparing packet | internal | — |
| 1 | Filed on CPGRAMS | portal | 21 days |
| 2 | Emailed Grievance Officer | email | 7 days |
| 3 | Appeal to Appellate Authority | email | 30 days |
| 4 | Called regional office | phone | 3 days |
| 5 | Escalated to Director (PG) | email | 30 days |
| 6 | Ladder exhausted | — | closed publicly as unresolved |

A background tick wakes every `TICK_SECONDS`, finds cases whose `next_action_at` has
passed, and climbs. `TIME_SCALE` compresses the waits so you can watch all six rungs
run in minutes while testing.

---

## Quick start

```bash
cd C:\Users\hridy\Desktop\persist
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\doctor.py     # check the setup
.\.venv\Scripts\python.exe scripts\seed_demo.py  # three demo cases
.\.venv\Scripts\python.exe run.py
```

Open http://127.0.0.1:8000 — console password is whatever you set as
`CONSOLE_PASSWORD` (the shipped `.env` uses `persist`).

---

## Model providers

The reasoning layer is provider-agnostic. Switching is a one-line `.env` change; nothing
in `agent/` knows which model is behind it.

| Provider | `LLM_PROVIDER` | Default model | Key |
|----------|----------------|---------------|-----|
| **Groq** | `groq` | `llama-3.3-70b-versatile` | free — [console.groq.com/keys](https://console.groq.com/keys) |
| **Gemini** | `gemini` | `gemini-2.0-flash` | free — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **Anthropic** | `anthropic` | `claude-opus-5` | paid |
| **Mock** | `mock` | — | none; canned answers for offline UI work |

To go live on the free tier:

```
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
```

Groq and Gemini go through their OpenAI-compatible endpoints. Because free-tier models
drop fields and stringify numbers, `structured()` has three layers of defence: native
tool calling → JSON-mode retry with the schema inlined → coercion against the schema so
required keys always exist. `python scripts/test_coerce.py` covers that layer.

**Where model quality actually matters:** routing (rung 0) and the appeal (rung 3).
Everything else is templating. If free-tier accuracy disappoints on the eval, consider
running just those two steps on a stronger model via `LLM_MODEL`.

---

## Safety

| Switch | Default | Effect |
|--------|---------|--------|
| `LLM_PROVIDER=mock` | on in shipped `.env` | Canned keyword responses. No key, no tokens. **Never demo from mock** — a red banner says so on every page. |
| `DRY_RUN` | `true` | Nothing is transmitted. Emails are written to `outbox/` as text files. |

Plus a hard one that isn't a flag: **every outbound department action passes through the
approval console.** The agent drafts and queues; a human clicks approve. Nothing sends
itself. (Status updates to the citizen about their own case go automatically under the
consent recorded at intake, and still respect `DRY_RUN`.)

Also in place, because this runs on a public URL:

- Cases marked private are 404 to anyone who isn't the operator — not merely hidden
  from the index.
- The console cookie is an HMAC of the password with a per-process salt, not the
  password itself. Restarting invalidates sessions.
- Intake is rate-limited per IP (3 per 15 min). Every submission costs model tokens, so
  an open endpoint is a wallet-drain vector the moment the link is shared.

Still missing, and worth knowing: **no CSRF tokens** on the console forms. Low risk
behind a password for a one-week project, but don't leave the console logged in on a
shared browser.

---

## Layout

```
app/
  main.py        FastAPI routes: dashboard, intake, case page, console
  ladder.py      the escalation state machine — the heart of it
  llm.py         one structured() call + the coercion safety net
  providers.py   groq / gemini / anthropic / mock definitions
  mock.py        offline stand-in for the model
  db.py          SQLite, deliberately thin
  mailer.py      outbound email (the only rail that leaves the machine)
  inbox.py       inbound IMAP — replies ingested and assessed automatically
  whatsapp.py    WhatsApp intake with an explicit consent handshake
  notify.py      status updates to the citizen
  taxonomy.json  CPGRAMS ministry/category tree  ⚠️ SEED DATA, UNVERIFIED
  agent/
    extract.py   narrative → structured, dated, referenced facts
    route.py     facts → ministry/category, with confidence and a rationale
    draft.py     four drafters, one per rung
    parse.py     department reply → does it actually count?
    baseline.py  no-LLM keyword router — the counterfactual to measure against
evals/
  routing_cases.json   22 labelled cases incl. out-of-scope and trap cases
scripts/
  check.py       run every suite against a throwaway database
  doctor.py      check config + live model round trip
  run_eval.py    routing accuracy + the keyword-baseline counterfactual
  test_ladder.py walks a case through all 6 rungs + reject + blocked paths
  test_bugs.py   regressions for bugs that actually shipped
  test_coerce.py schema-coercion tests
  seed_demo.py   three demo cases
```

## Checks

```bash
python scripts/check.py     # everything, against a throwaway database
```

Run it before you demo, deploy, or record anything. Individually:

| Script | Guards |
|---|---|
| `test_coerce.py` | the schema-coercion net that lets free-tier models drop fields |
| `test_ladder.py` | all six rungs, the reject path, the blocked-case path |
| `test_bugs.py` | **every bug that has actually shipped and been fixed** |
| `run_eval.py --baseline-only` | routing accuracy of the no-LLM baseline (no key needed) |

`test_ladder.py` exists because rungs 3–5, reject, and the blocked path are ones you
never hit by hand during a demo — which is exactly why they break.

`test_bugs.py` exists because every bug found so far has been **silent**: nothing
crashed, the wrong thing just quietly happened. An email with no address getting logged
as a phone call, a refused case never telling the citizen, escalation letters claiming
"0 days elapsed". Those survive a demo and surface in front of judges.

---

## Design system — "Red Tape"

The visual language is official correspondence pulled apart into geometry: seal rings,
franking bars, perforation runs, registration crosses. Warm paper, ink-indigo structure,
a vermilion accent, and soft colour fields drifting behind the page. All of it lives in
`app/static/style.css` and one SVG partial, `templates/_art.html`.

**This design commits to light.** There is no dark block — every surface and ink is
painted explicitly and `color-scheme: light` keeps native form controls in the same
world, so the pages look identical regardless of the viewer's OS theme. Verified by
rendering with the browser forced to dark.

**Colour does exactly two encoding jobs, and the split is deliberate:**

| Encoding | Job | Treatment |
|---|---|---|
| **Rung** (0–5) | ordinal magnitude — how far a case has climbed | single-hue sequential ink-indigo ramp, light→dark |
| **Outcome** | reserved status | green resolved · amber waiting · crimson unresolved |

**"Active" has no hue.** It's the absence of a status, not a status, so it wears neutral
ink — which is what lets the three real signals stand out on a busy ledger.

The ramp does real work: it's the ladder graphic, the left edge of every case row, and
the left edge of every queue item, so you can scan the ledger and see which fights are
deep without reading a word.

Everything else — the washes, the hero geometry, the franking stripe under the header —
is decoration that encodes nothing, which is precisely why it's free to be colourful.

### What was measured, not eyeballed

- The status triad went through a CVD validator against the paper surface. The first
  attempt failed: vermilion and marigold sat at ΔE 11.7 for *normal* vision — below the
  15 floor, so even full-colour readers couldn't separate them. Amber moved yellower,
  red moved crimson, and the pair now clears comfortably.
- The residual CVD margin (ΔE 7.2, inside the 6–8 band) is only legal *with secondary
  encoding*, which is why **every status pill carries a coloured dot and a text label**.
  Never drop the label to save space.
- Ladder labels were contrast-tested per step. Blanket white text measured 2.1:1 and
  3.0:1 on the two lightest ramp steps. The ink now flips partway up the ramp, and
  `--rung-3` is pinned at `#5a68ae` because the lighter value measured 4.36:1 — just
  under the floor. All six steps now clear 4.5:1.

If you restyle, re-run the check rather than trusting your eye — paste this in the
browser console on any page with a ladder:

```js
[...document.querySelectorAll('.ladder .step')].map(s => {
  const c = getComputedStyle(s); return [c.backgroundColor, c.color];
})
```

---

## Deploying

**See [DEPLOY.md](DEPLOY.md) for the walkthrough.** Short version: build from the
`Dockerfile`, mount a volume at `/data`, and set `TIME_SCALE=1.0`.

Two constraints drive the host choice:

- **It must not sleep.** The whole differentiator is running autonomously for a week. A
  host that suspends the process on inactivity stops the escalation tick and the ledger
  silently stops moving. This rules out Render's free tier and Koyeb free; Fly.io no
  longer has a free tier at all in 2026.
- **It needs a persistent volume.** SQLite on an ephemeral filesystem loses every case
  on redeploy, which destroys the accumulated evidence the strategy rests on.

**Northflank Sandbox** is the free tier that satisfies both. `python scripts/doctor.py`
has a production-readiness section that fails loudly on the four settings that ruin a
deploy — dev `TIME_SCALE`, a weak console password, an unmounted data dir, and mock mode
on a public URL.

---

## Measuring it — and the counterfactual

```bash
python scripts/run_eval.py                  # agent vs keyword baseline
python scripts/run_eval.py --baseline-only  # free, no API key needed
python scripts/run_eval.py --json out.json
```

Reports three accuracies separately: **scope** (did it correctly refuse what CPGRAMS
can't action), **ministry**, and **category**. It also prints mean confidence when the
router was *wrong* — if that isn't clearly below overall mean confidence, the confidence
number is decorative and you shouldn't cite it.

**The counterfactual runs alongside every eval.** `app/agent/baseline.py` is a no-LLM
keyword router over the same taxonomy — it stands in for what a citizen skimming a
dropdown does. The claim "reasoning about routing is worth something" is worthless
unless it's measured against the obvious approach, so it is.

Measured on the current 22-case set:

```
              agent    keyword baseline    delta
  scope         ...              77.3%       ...
  ministry      ...              68.2%       ...
  category      ...              40.9%       ...
```

Note *how* the baseline fails: it gets 41% of categories right, and it misfiles **all
five out-of-scope cases into Department of Posts**, because a keyword matcher has no
concept of scope and cannot refuse. That's the number to lead with — every one of those
is a citizen who waits three weeks to be told "not related to this department."

(Agent columns fill in once you set a real `LLM_PROVIDER`; the baseline needs no key.)

`evals/routing_cases.json` includes deliberate traps: a bank-vs-railways blame loop, a
private courier that sounds like India Post, and a delay that isn't a non-delivery.
**Add every real case you take, labelled with where it actually got actioned** — that
turns your week of real work into a growing eval set.

---

## Department contacts

Officer contacts come from the official
[CPGRAMS Nodal Public Grievance Officers directory](https://pgportal.gov.in/Home/NodalPgOfficers),
checked 2026-09-07. Every ministry declares an `email_kind`, because the three cases
behave differently and the code treats them differently:

| `email_kind` | Meaning | Behaviour |
|---|---|---|
| `role` | durable departmental account (`ddgpgq@indiapost.gov.in`, `edpg@rb.railnet.gov.in`, …) | used freely; survives postholder changes |
| `individual` | a named officer's address | works today, but **rotates** — re-check before a long campaign |
| `none` | the directory lists a phone number only | the email rung is **skipped**, never faked |

**Nothing here is invented.** Ministry of External Affairs publishes no grievance-officer
email, so its email rung is skipped and the case climbs to the phone rung instead — a
plausible-looking wrong address is worse than an obvious gap, because it reaches a real
stranger instead of bouncing.

`_sendable()` in `main.py` is the last line of defence: blank, malformed, or
placeholder-looking addresses are refused at the approval gate and the action stays
queued with an explanation.

Two useful corrections that came out of checking the real directory:

- **EPFO has no CPGRAMS nodal entry at all.** It runs its own portal, EPFiGMS, which is
  usually the faster route; CPGRAMS reaches it via Ministry of Labour and Employment.
- **Pensions and airline complaints have dedicated systems too** (CPENGRAMS, AirSewa),
  noted on those entries.

## ⚠️ Still unverified: the category names

`_meta.categories_verified` is `false`. The ministries and officer contacts are real, but
the category names under each are still seed data. Routing accuracy is the product's core
claim, so before you demo it:

1. File one grievance manually on https://pgportal.gov.in.
2. Screenshot every dropdown at every level.
3. Correct the category lists and set `_meta.categories_verified: true`.
4. Confirm the appeal mechanism and the real stated timelines, then fix `RUNGS` in
   `ladder.py`.

`python scripts/doctor.py` reports all of this every run.

---

## Metrics for the submission

`GET /health` returns live JSON. The public dashboard shows: cases taken, filed,
resolved, win rate, actions taken, and rupees recovered.

Report the failures too. The losses are what make the wins believable.

---

## What's not built yet

- **Phone rail (rung 4)** generates a call script; a human places the call and logs the
  outcome. Wiring real telephony (Exotel/Plivo — Twilio has KYC friction for Indian
  outbound) is the day-5 job. You need *one* good recorded call for the video.
- **Real portal automation.** Deliberately absent — see the credential boundary above.
- **Attachments.** Citizens have receipts and screenshots; intake is text-only.
- **Low-confidence gating.** The router reports confidence and the UI flags anything
  under 70%, but nothing actually holds those cases back — they queue for approval like
  any other. Fine while a human approves everything; revisit if you ever auto-approve.
- **One process only.** APScheduler runs in-process and the rate limiter is in memory,
  so two workers would double-tick cases. Keep it at one worker.
- **No CSRF tokens** on console forms (see Safety above).

## The three public surfaces

| Route | What it's for |
|---|---|
| `/` | the live ledger — every case, every action, wins and losses |
| `/scoreboard` | **which departments actually answer** — deflection rate per ministry |
| `/case/<id>` | one case, including the before/after |
| `/api/ledger` | the whole thing as JSON, so the win rate is auditable, not just claimed |

**The before/after** on each case page is the most legible thing in the product: the
citizen's rambling narrative on the left, the filed grievance on the right, with word
counts, references extracted, and the routed category underneath. It needs no
explanation, which is why it belongs in the video.

**The scoreboard** is the part that compounds. Every assessed reply is classified as a
real remedy or a procedural non-answer, aggregated per department. With enough cases it
stops being a feature and becomes a public record of which authorities engage. Rates are
suppressed below 3 replies — showing the *n* beats a confident-looking number built on
three data points, and judges notice which one you chose.

---

## WhatsApp intake

Nobody in India fills in a web form to complain about a parcel; they message. So the
same pipeline is reachable over WhatsApp as a short conversation — describe the problem,
give explicit consent, and the agent takes it from there.

Consent is the part that matters: **a message is not authorisation** to act on someone's
behalf with a government department, so the bot asks in plain words and records the
timestamp only on an explicit yes. A case sits in `needs_consent` until then and the tick
will not touch it.

To connect (needs your account — nothing transmits until you do):

1. Twilio console → Messaging → WhatsApp sandbox, or a live sender.
2. Point the inbound webhook at `https://<your-host>/webhook/whatsapp` (POST).
3. Set `TWILIO_AUTH_TOKEN` in `.env`.

Without that token the endpoint returns 503 to everything rather than accepting
unauthenticated requests, and signatures are verified on every call.

---

## Inbound email

Set the `IMAP_*` block in `.env` and replies are ingested automatically every
`IMAP_POLL_SECONDS`: the poller matches the case id (`PST-XXXX`) carried in every
outbound subject and footer, strips the quoted original, and hands the new text to the
same assessor. A citizen replying with their registration number starts the clock
without anyone touching the console.

With Gmail, use an App Password — not your account password.

Leave it blank and everything still works; you just paste replies into the console
yourself.
