# The video

Two to three minutes. Judges watch a lot of these and decide early, so the first
fifteen seconds carry more weight than the next ninety.

## What has to be true before you record

Do not film around these. A video that demonstrates a thing you have not actually done
is the one failure mode that cannot be recovered from if anyone checks.

- [ ] **At least one real case**, from a real person, with their consent. One is enough
      to be true. Five is enough to be a pattern.
- [ ] **`DRY_RUN=false`** and at least one letter genuinely sent. Until then the
      product writes letters into `outbox/` and the video would be showing a drawer.
- [ ] **Deployed**, with `PUBLIC_BASE_URL` set to the real host — every URL on screen
      should be one a judge can open.
- [ ] **`python scripts/check.py` green**, run the same day.
- [ ] Category names verified against the live portal (`_meta.categories_verified`).

## The through-line

One sentence, and every shot either serves it or gets cut:

> **The system is patient and people aren't. This is patient back.**

Do not open with the architecture. The gap report and the credit ledger are proof, and
proof belongs after the claim, not before it.

---

## Shot list

### 0:00–0:15 — the problem, in a real person's words
**On screen:** the intake form with a genuine narrative being pasted in. Real text,
lightly redacted if needed.
**Say:** name one real case in one sentence. *"A speed post booked in August, still not
delivered in September, two phone calls, one office visit, no answer."*

No logo, no title card, no "hi everyone". The case is the hook.

### 0:15–0:40 — what it does with that
**On screen:** the case page's before/after — the rambling narrative on the left, the
filed grievance on the right, with extracted references, the routed ministry and
category, and word counts.
**Say:** it extracts the facts and reference numbers, picks the ministry and category,
drafts it, files it.

This is the most legible thing in the product and it needs no explanation. Let it sit.

### 0:40–1:10 — the part that is actually different
**On screen:** the case timeline showing rungs climbing — filed, waited, deadline
passed, escalated to nodal officer, escalated to appellate authority — with real dates.
**Say:** *"Most complaints die because people file once and give up. Departments count
on that. This one doesn't get tired — every time a statutory deadline passes with no
real reply, it escalates on its own and cites the dates back at them."*

If you only have compressed-time cases, **say so on camera.** "Time is compressed here
so you can see six weeks in ten seconds" costs nothing and protects everything.

### 1:10–1:35 — it checks before it escalates
**On screen:** a case where the live read changed the behaviour — status already
resolved, so no escalation; or tracking still reading "in transit" and that line
quoted inside the appeal.
**Say:** it looks at the live page before climbing, so it doesn't escalate to a
Director on something quietly closed last week.

### 1:35–2:05 — the gap, and the negative result
**On screen:** a terminal running `python scripts/forge.py`. Let the output land:
grievance status → box office charts, FAA airport restrictions. India Post → Gangajal
and Holy Blessings.

**Say:** *"India's national grievance portal serves 1.4 billion people. I asked the
largest catalog of pre-built web actions on the internet — 991 sites — how to read it.
It offered me box office charts."*

**Then cut to `/toolbox`** and say the rest plainly:

*"So I asked it to build one. It charged 200 credits — the docs say 25 — reported
success, and published zero actions. That's on the page, with the receipt. I built the
fallback before I ran it, so the read dropped to the browser rung and the case carried
on."*

Do not oversell this and do not hide it. A measured negative result with the money on
screen is more credible than a success story, and it is the only version that survives
someone checking.

### 2:05–2:25 — the scoreboard
**On screen:** `/scoreboard`, with the suppression notice visible if you are under ten
replies.
**Say:** every reply is classified as a real remedy or a procedural non-answer, per
department, published. With enough cases it stops being a feature and becomes a public
record of which authorities engage.

Point out that rates are hidden below three replies. Choosing to show the *n* instead
of a flattering percentage is a thing judges notice.

### 2:25–2:40 — the boundary, then stop
**On screen:** the credential line on the case page or the footer.
**Say:** *"It never asks for anyone's portal password. It prepares everything and the
citizen makes the one credentialed submission themselves. That's deliberate, and it's
why I'd trust it with a stranger's complaint."*

End there. No summary slide, no roadmap, no thanks-for-watching.

---

## Recording notes

- **Real data or clearly-labelled seed data. Never unlabelled fake data.**
- Full-screen the browser and hide bookmarks — a visible tab bar of unrelated tabs
  reads as unfinished.
- Zoom the terminal to ~16pt. Judges watch on laptops and half of them at 1x speed.
- One take per section, stitched. Do not attempt a single continuous run.
- Say numbers out loud. "200 credits", "991 sites", "41 days" — spoken numbers are
  remembered, on-screen numbers are skimmed.
- If something breaks mid-take, keep it and narrate it. It is more convincing than a
  clean run, and this project's whole posture is that measured failures count.

## The description that goes with the link

Three lines, no more:

> Persist files Indian government grievances and escalates them automatically every
> time a statutory deadline passes. Built on Anakin for live reads; the catalog has no
> connector for pgportal.gov.in, and the receipt for trying to build one is published
> on /toolbox. Code: github.com/BlackBeanEagles/Anakin

## What not to film

- The architecture diagram. Nobody asked.
- The test suite passing. Put the number in the README instead.
- Toolsmith. It is a prototype and a second repo confuses the story.
- Anything in `outbox/` while `DRY_RUN` is still true.
