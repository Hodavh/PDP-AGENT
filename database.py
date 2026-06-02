import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/audits.db")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                url             TEXT NOT NULL,
                run_at          TEXT NOT NULL,
                target_json     TEXT NOT NULL,
                competitor_json TEXT NOT NULL,
                audit_json      TEXT NOT NULL,
                rewrite_json    TEXT NOT NULL,
                scores_json     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audits_url ON audits(url);
            CREATE INDEX IF NOT EXISTS idx_audits_run_at ON audits(run_at);
        """)


def insert_audit(
    url: str,
    target_json: dict,
    competitor_json: dict,
    audit_json: dict,
    rewrite_json: dict,
    scores_json: dict,
) -> int:
    run_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO audits
               (url, run_at, target_json, competitor_json, audit_json, rewrite_json, scores_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                url,
                run_at,
                json.dumps(target_json),
                json.dumps(competitor_json),
                json.dumps(audit_json),
                json.dumps(rewrite_json),
                json.dumps(scores_json),
            ),
        )
        return cursor.lastrowid


def get_all_audits() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, url, run_at, scores_json, target_json FROM audits ORDER BY run_at DESC"
        ).fetchall()
    result = []
    for row in rows:
        record = dict(row)
        record["scores_json"] = json.loads(record["scores_json"])
        target = json.loads(record.pop("target_json"))
        s = target.get("structured", target)
        record["product_name"] = s.get("product_name", "") or s.get("h1", "") or \
                                  target.get("headings", {}).get("h1", "") or ""
        result.append(record)
    return result


def get_audit_by_id(audit_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    if not row:
        return None
    record = dict(row)
    for col in ("target_json", "competitor_json", "audit_json", "rewrite_json", "scores_json"):
        record[col] = json.loads(record[col])
    return record


def delete_audit(audit_id: int) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM audits WHERE id = ?", (audit_id,))
        return cursor.rowcount > 0


def get_audits_by_url(url: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audits WHERE url = ? ORDER BY run_at DESC", (url,)
        ).fetchall()
    result = []
    for row in rows:
        record = dict(row)
        for col in ("target_json", "competitor_json", "audit_json", "rewrite_json", "scores_json"):
            record[col] = json.loads(record[col])
        result.append(record)
    return result
