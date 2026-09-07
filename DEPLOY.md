# Deploying Persist

The public ledger is the whole point — a judge has to be able to click it. This gets it
online, free, without the two mistakes that would quietly ruin it.

## The two mistakes

**1. A host that sleeps.** Persist's differentiator is that it runs autonomously for a
week: the tick loop wakes every few minutes, notices a case has gone unanswered, and
escalates it. A host that suspends your process on inactivity stops that loop. Your
ledger silently stops moving and you don't find out until you look.

This rules out **Render's free tier** (sleeps after 15 min) and **Koyeb free**
(cold starts). It also rules out **Fly.io**, which no longer has a free tier at all in
2026 — just a 7-day trial.

**2. An ephemeral filesystem.** Persist is SQLite. Without a mounted volume, every case
disappears on the next deploy — destroying the accumulated evidence that is the entire
moat.

## Recommended: Northflank Sandbox (free)

The one genuinely free tier left that is **always-on with no sleeping**, and supports
persistent volumes. Sandbox gives 2 services, 1 database, 2 cron jobs at $0/month. A
card may be requested as anti-abuse verification; the Sandbox tier does not bill.

### 1. Push to GitHub

```bash
cd C:\Users\hridy\Desktop\persist
git init
git add -A
git commit -m "Persist: bureaucracy-fighting agent"
```

Create an empty repo on GitHub, then push. **Check `.env` is not in the commit** —
`.gitignore` already excludes it, but confirm with `git status` before pushing.

### 2. Create the service

1. northflank.com → sign up → **Create new service** → **Build & Deploy from Git**
2. Connect GitHub, pick the repo, branch `main`
3. Build type: **Dockerfile**, path `/Dockerfile`
4. Port: **8000**, protocol HTTP, and enable the **public DNS / ingress**

### 3. Add the volume — do not skip this

**Addons → Volume**, then:

| Setting | Value |
|---|---|
| Mount path | `/data` |
| Size | 1 GB is plenty |

The Dockerfile already sets `PERSIST_DATA_DIR=/data`, so the database and the dry-run
outbox both land on the volume.

### 4. Environment variables

```
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...

TIME_SCALE=1.0
TICK_SECONDS=300

CONSOLE_PASSWORD=<generate one, see below>
PUBLIC_BASE_URL=https://<your-northflank-url>

DRY_RUN=true
PERSIST_DATA_DIR=/data
```

Generate the password:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

**`TIME_SCALE=1.0` is not optional.** The dev default compresses 21 days into three
minutes. Left on in production, the agent would escalate to a Director of Public
Grievances within minutes of filing — wrong, and rude to a real official.

`TICK_SECONDS=300` is plenty in real time; the 15s dev value just wastes cycles.

### 5. Verify

```bash
curl https://<your-url>/health
```

You want `"provider":"groq"`, `"key_configured":true`, `"dry_run":true`. Then open the
site, and check `/console` accepts your new password.

Run the doctor against the deployed config too — set the same env vars locally and:

```bash
python scripts/doctor.py
```

Its **production readiness** section fails loudly on exactly the four things that ruin a
deploy: dev `TIME_SCALE`, a weak console password, an unmounted data dir, and mock mode
on a public URL.

## After it's up

1. **Leave `DRY_RUN=true`** until you have a real case you intend to actually file.
   Everything still works; drafted correspondence goes to `/data/outbox` instead of out.
2. **Post the intake link.** This is the critical path — cases need days to accumulate
   outcomes, and no amount of code substitutes for that.
3. **Watch `/scoreboard`** fill in as replies arrive.

## Alternatives if Northflank doesn't work out

| Host | Free? | Sleeps? | Volume | Notes |
|---|---|---|---|---|
| **Northflank Sandbox** | yes | **no** | yes | recommended |
| Railway | $5 trial credit | no | yes | credit likely covers a hackathon week |
| Oracle Cloud Always Free | yes, forever | no | yes | 2 OCPU / 12 GB ARM as of Jun 2026 (halved); real VPS, capacity varies by region, most setup work |
| Render free | yes | **yes — fatal** | no | do not use |
| Fly.io | no | no | yes | free tier ended; 7-day trial only |

Any of these works from the same `Dockerfile`. On a VPS, run it behind nginx with
Caddy or certbot for TLS, and keep it to **one worker** — the scheduler and rate
limiter are in-process.
