"""SQLite access. Thin on purpose - the interesting logic lives in agent/ and ladder.py."""
import json
import random
import sqlite3
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

SCHEMA = Path(__file__).parent / "schema.sql"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_case_id() -> str:
    tail = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PST-{tail}"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the first release. Applied idempotently so an existing
# persist.db keeps its cases instead of needing a wipe mid-hackathon.
MIGRATIONS = [
    ("cases", "info_asks", "INTEGER NOT NULL DEFAULT 0"),
    ("cases", "info_questions", "TEXT"),
    ("cases", "observed_json", "TEXT"),
    # when a reply actually landed — needed for time-to-first-response
    ("actions", "response_at", "TEXT"),
    # when the grievance was registered — the anchor for "days elapsed since filing".
    # updated_at is NOT that anchor: it is rewritten on every save.
    ("cases", "filed_at", "TEXT"),
]


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# ---------------------------------------------------------------- cases


def create_case(
    citizen_name: str,
    citizen_email: str,
    citizen_phone: str,
    category: str,
    narrative_raw: str,
    amount_claimed: float = 0.0,
    is_public: bool = True,
) -> str:
    case_id = new_case_id()
    ts = now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO cases (id, created_at, updated_at, citizen_name, citizen_email,
                                  citizen_phone, consent_at, category, narrative_raw,
                                  amount_claimed, is_public, status, next_action_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?, 'intake', ?)""",
            (case_id, ts, ts, citizen_name, citizen_email, citizen_phone, ts,
             category, narrative_raw, amount_claimed, int(is_public), ts),
        )
    log_event(case_id, "intake", f"Case opened by {citizen_name}. Consent recorded.")
    return case_id


def get_case(case_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def list_cases(public_only: bool = False) -> list[dict[str, Any]]:
    q = "SELECT * FROM cases"
    if public_only:
        q += " WHERE is_public = 1"
    q += " ORDER BY created_at DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def due_cases(limit: int = 25) -> list[dict[str, Any]]:
    """Cases whose next_action_at has passed - the agent's work queue."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM cases
               WHERE next_action_at IS NOT NULL
                 AND next_action_at <= ?
                 AND status NOT IN ('resolved', 'closed_unresolved',
                                    'awaiting_submission', 'needs_consent')
               ORDER BY next_action_at ASC LIMIT ?""",
            (now(), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def update_case(case_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE cases SET {cols} WHERE id = ?", (*fields.values(), case_id))


# ---------------------------------------------------------------- actions


def create_action(
    case_id: str, rung: int, channel: str, kind: str, content: str,
    recipient: str = "", subject: str = "", status: str = "pending_approval",
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO actions (case_id, created_at, rung, channel, kind,
                                    recipient, subject, content, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (case_id, now(), rung, channel, kind, recipient, subject, content, status),
        )
        return int(cur.lastrowid)


def get_action(action_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    return dict(row) if row else None


def case_actions(case_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM actions WHERE case_id = ? ORDER BY created_at ASC", (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def pending_actions() -> list[dict[str, Any]]:
    """The approval queue. Nothing in here has been sent."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.*, c.citizen_name, c.category
               FROM actions a JOIN cases c ON c.id = a.case_id
               WHERE a.status = 'pending_approval'
               ORDER BY a.created_at ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def update_action(action_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE actions SET {cols} WHERE id = ?", (*fields.values(), action_id))


# ---------------------------------------------------------------- events


def log_event(case_id: str, kind: str, detail: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (case_id, ts, kind, detail) VALUES (?,?,?,?)",
            (case_id, now(), kind, detail),
        )


def case_events(case_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE case_id = ? ORDER BY ts ASC", (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def recent_events(limit: int = 40) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.*, c.citizen_name FROM events e
               JOIN cases c ON c.id = e.case_id
               WHERE c.is_public = 1
               ORDER BY e.ts DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- helpers


def jload(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def scoreboard() -> dict[str, Any]:
    """Aggregate behaviour per department — which ones answer, which deflect.

    This is the public-good output: with enough cases it becomes a record of which
    authorities actually engage. Rates on tiny samples are noise, so every row
    carries its n and the template suppresses percentages below `min_cases`.
    """
    from statistics import median

    rows = list_cases(public_only=True)
    by_ministry: dict[str, dict[str, Any]] = {}

    with connect() as conn:
        verdicts = conn.execute(
            """SELECT case_id, response_json, response_at, created_at
               FROM actions WHERE response_json IS NOT NULL"""
        ).fetchall()
    replies: dict[str, list[dict]] = {}
    for v in verdicts:
        replies.setdefault(v["case_id"], []).append(dict(v))

    for c in rows:
        routing = jload(c["routing_json"], {}) or {}
        if routing.get("out_of_scope") or not routing.get("ministry_id"):
            continue
        key = routing["ministry_id"]
        m = by_ministry.setdefault(key, {
            "ministry_id": key,
            "ministry_name": routing.get("ministry_name", key),
            "cases": 0, "filed": 0, "resolved": 0, "unresolved": 0, "open": 0,
            "deflections": 0, "replies": 0, "rungs": [], "response_days": [],
            "recovered": 0.0,
        })
        m["cases"] += 1
        m["rungs"].append(c["rung"])
        m["recovered"] += c["amount_recovered"]
        if c["cpgrams_reg_no"]:
            m["filed"] += 1
        if c["status"] == "resolved":
            m["resolved"] += 1
        elif c["status"] == "closed_unresolved":
            m["unresolved"] += 1
        else:
            m["open"] += 1

        for r in replies.get(c["id"], []):
            assessment = jload(r["response_json"], {}) or {}
            m["replies"] += 1
            if assessment.get("verdict") in ("deflection", "rejected"):
                m["deflections"] += 1
            days = _days_between(c["created_at"], r["response_at"])
            if days is not None:
                m["response_days"].append(days)

    out = []
    for m in by_ministry.values():
        m["max_rung"] = max(m["rungs"], default=0)
        m["median_response_days"] = round(median(m["response_days"]), 1) if m["response_days"] else None
        m["deflection_rate"] = (round(100 * m["deflections"] / m["replies"]) if m["replies"] else None)
        m["resolve_rate"] = (round(100 * m["resolved"] / m["filed"]) if m["filed"] else None)
        m.pop("rungs"); m.pop("response_days")
        out.append(m)

    out.sort(key=lambda r: (-(r["deflection_rate"] or -1), -r["cases"]))
    return {
        "ministries": out,
        "total_cases": sum(m["cases"] for m in out),
        "total_replies": sum(m["replies"] for m in out),
        "total_deflections": sum(m["deflections"] for m in out),
        "overall_deflection_rate": (
            round(100 * sum(m["deflections"] for m in out) / sum(m["replies"] for m in out))
            if sum(m["replies"] for m in out) else None
        ),
    }


def _days_between(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    try:
        t0 = datetime.fromisoformat(a)
        t1 = datetime.fromisoformat(b)
    except ValueError:
        return None
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    if t1.tzinfo is None:
        t1 = t1.replace(tzinfo=timezone.utc)
    return max((t1 - t0).total_seconds() / 86400, 0)


def stats() -> dict[str, Any]:
    rows = list_cases(public_only=True)
    resolved = [c for c in rows if c["status"] == "resolved"]
    filed = [c for c in rows if c["cpgrams_reg_no"]]
    with connect() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) n FROM actions WHERE status = 'sent'"
        ).fetchone()["n"]
    return {
        "total": len(rows),
        "filed": len(filed),
        "resolved": len(resolved),
        "open": len([c for c in rows if c["status"] not in ("resolved", "closed_unresolved")]),
        "unresolved": len([c for c in rows if c["status"] == "closed_unresolved"]),
        "actions_sent": sent,
        "recovered": round(sum(c["amount_recovered"] for c in rows), 2),
        "claimed": round(sum(c["amount_claimed"] for c in rows), 2),
        "max_rung": max([c["rung"] for c in rows], default=0),
        "win_rate": round(100 * len(resolved) / len(filed), 1) if filed else 0.0,
    }


# ---------------------------------------------------------------- tools

def remember_tool(action_id: str, *, origin: str, catalog: str = "", domain: str = "",
                  credits: int = 0, schema: Any = None, build_id: str = "",
                  funded_by: str = "") -> None:
    """Record a tool the first time it is held.

    Later sightings only bump counters, so `first_seen`, `origin` and `funded_by`
    stay true to the moment it entered the toolbox rather than drifting to the most
    recent use.
    """
    with connect() as conn:
        conn.execute(
            """INSERT INTO tools (action_id, first_seen, catalog, domain, origin,
                                  credits, schema_json, build_id, funded_by)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(action_id) DO NOTHING""",
            (action_id, now(), catalog or None, domain or None, origin, credits,
             jdump(schema) if schema is not None else None, build_id or None,
             funded_by or None))


def score_tool(action_id: str, won: bool) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE tools SET uses = uses + 1, wins = wins + ? WHERE action_id = ?",
            (1 if won else 0, action_id))


def tools(origin: str = "") -> list[dict]:
    with connect() as conn:
        if origin:
            rows = conn.execute(
                "SELECT * FROM tools WHERE origin=? ORDER BY first_seen DESC",
                (origin,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tools ORDER BY origin DESC, uses DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- builds

def record_build(build_id: str, domain: str, goal: str, *, status: str,
                 code: str = "", error: str = "", credits: int = 0) -> None:
    """Log a forge attempt the moment it is made, not when it resolves.

    Written up front so a crash, a Ctrl-C, or a fifteen-minute wait that nobody sits
    through still leaves a record of what was asked for and what it cost.
    """
    with connect() as conn:
        conn.execute(
            """INSERT INTO builds (id, created_at, domain, goal, status, code,
                                   error, credits)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   status=excluded.status, code=excluded.code, error=excluded.error""",
            (build_id, now(), domain, goal, status, code or None, error or None, credits))


def finish_build(build_id: str, *, status: str, action_id: str = "", error: str = "",
                 code: str = "", refunded: bool = False, fallback: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE builds SET finished_at=?, status=?, action_id=?, error=?,
                                 code=COALESCE(NULLIF(?, ''), code),
                                 refunded=?, fallback=?
               WHERE id=?""",
            (now(), status, action_id or None, error or None, code, int(refunded),
             fallback or None, build_id))


def builds(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM builds ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

