CREATE TABLE IF NOT EXISTS cases (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    citizen_name    TEXT NOT NULL,
    citizen_email   TEXT NOT NULL,
    citizen_phone   TEXT,
    consent_at      TEXT,

    category        TEXT NOT NULL,
    narrative_raw   TEXT NOT NULL,

    facts_json      TEXT,
    routing_json    TEXT,
    draft_text      TEXT,

    cpgrams_reg_no  TEXT,
    filed_at        TEXT,
    rung            INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'intake',
    next_action_at  TEXT,

    outcome         TEXT,
    info_asks       INTEGER NOT NULL DEFAULT 0,
    info_questions  TEXT,
    observed_json   TEXT,
    amount_claimed  REAL NOT NULL DEFAULT 0,
    amount_recovered REAL NOT NULL DEFAULT 0,
    resolved_at     TEXT,
    is_public       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         TEXT NOT NULL REFERENCES cases(id),
    created_at      TEXT NOT NULL,
    rung            INTEGER NOT NULL,
    channel         TEXT NOT NULL,           -- portal | email | phone
    kind            TEXT NOT NULL,           -- packet | officer_email | appeal | call | director
    recipient       TEXT,
    subject         TEXT,
    content         TEXT NOT NULL,
    status          TEXT NOT NULL,           -- pending_approval | approved | sent | rejected | failed
    approved_at     TEXT,
    sent_at         TEXT,
    response_raw    TEXT,
    response_json   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL REFERENCES cases(id),
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_next   ON cases(next_action_at);
CREATE INDEX IF NOT EXISTS idx_actions_case ON actions(case_id);
CREATE INDEX IF NOT EXISTS idx_events_case  ON events(case_id);

-- Every Anakin call that costs something, metered before it goes out. The free tier
-- is 300 credits for the week, so "what did this case cost to watch" is a question
-- with a real answer, and the ledger page can show it rather than assert it.
-- A failed call is kept with credits=0 and ok=0: the attempt is part of the record.
CREATE TABLE IF NOT EXISTS credits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    case_id     TEXT REFERENCES cases(id),
    endpoint    TEXT NOT NULL,           -- url-scraper | wire-run | build-request
    detail      TEXT NOT NULL,
    credits     INTEGER NOT NULL DEFAULT 0,
    ok          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_credits_case ON credits(case_id);

-- Wire actions this project has held. `origin` is the column that matters: a row
-- saying 'built' is a connector that did not exist until a case needed it, and was
-- published back into the shared catalog. Kept separate from `credits` because a
-- tool outlives the run that paid for it - the first case funds it, every case
-- afterwards uses it free.
CREATE TABLE IF NOT EXISTS tools (
    action_id     TEXT PRIMARY KEY,
    first_seen    TEXT NOT NULL,
    catalog       TEXT,
    domain        TEXT,
    origin        TEXT NOT NULL,          -- catalog | built
    credits       INTEGER NOT NULL DEFAULT 0,
    schema_json   TEXT,
    build_id      TEXT,
    funded_by     TEXT,                   -- the case that paid for the build
    uses          INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0
);
