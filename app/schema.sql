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
