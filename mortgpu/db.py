"""SQLite schema (§6) and helpers. Shared state lives in config.DB_PATH."""
import os
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
  id TEXT PRIMARY KEY,
  name TEXT, email TEXT,
  capital_pence INTEGER NOT NULL,
  credits_reset_at TEXT,
  stripe_customer_id TEXT,
  connect_account_id TEXT
);
CREATE TABLE IF NOT EXISTS contributions(
  id INTEGER PRIMARY KEY, member_id TEXT, amount_pence INTEGER,
  contributed_at TEXT, stripe_payment_intent_id TEXT
);
CREATE TABLE IF NOT EXISTS leases(
  id TEXT PRIMARY KEY,
  member_id TEXT, gpu_id TEXT, price_pence INTEGER,
  pool_permille INTEGER,
  status TEXT,
  stripe_customer_id TEXT, stripe_subscription_id TEXT, stripe_subscription_item_id TEXT,
  clock_id TEXT, opened_at TEXT, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS pool(id INTEGER PRIMARY KEY CHECK(id=1), cash_pence INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, decision_id TEXT,
  member_id TEXT, lease_id TEXT, kind TEXT,
  amount_pence INTEGER DEFAULT 0, permille_delta INTEGER DEFAULT 0,
  stripe_object_id TEXT, policy_clause TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS decisions(
  id TEXT PRIMARY KEY, ts TEXT, trigger_kind TEXT, trigger_ref TEXT,
  action TEXT, member_id TEXT, proposal_json TEXT,
  verdict TEXT,
  clauses_json TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS promises(
  id INTEGER PRIMARY KEY, member_id TEXT, kind TEXT,
  made_at TEXT, due_at TEXT, text TEXT,
  status TEXT,
  decision_id TEXT
);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, ts TEXT, dataset TEXT, member_id TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS processed(key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""

TABLES = ["members", "contributions", "leases", "pool", "ledger", "decisions",
          "promises", "memories", "processed", "kv"]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _rows(sql: str, args=()) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _row(sql: str, args=()) -> dict | None:
    rows = _rows(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args=()) -> int:
    """Run a write; returns lastrowid."""
    with _conn() as c:
        return c.execute(sql, args).lastrowid


def query(sql: str, args=()) -> list[dict]:
    return _rows(sql, args)


def init(wipe: bool = False) -> None:
    if wipe:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(config.DB_PATH + suffix):
                os.remove(config.DB_PATH + suffix)
    with _conn() as c:
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO pool(id, cash_pence) VALUES (1, 0)")


def kv_get(k: str) -> str | None:
    r = _row("SELECT value FROM kv WHERE key=?", (k,))
    return r["value"] if r else None


def kv_set(k: str, v) -> None:
    execute("INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, None if v is None else str(v)))


def member(id: str) -> dict | None:
    return _row("SELECT * FROM members WHERE id=?", (id,))


def members() -> list[dict]:
    return _rows("SELECT * FROM members ORDER BY rowid")


def contributions() -> list[dict]:
    return _rows("SELECT * FROM contributions ORDER BY id")


def lease(id: str) -> dict | None:
    return _row("SELECT * FROM leases WHERE id=?", (id,))


def leases() -> list[dict]:
    return _rows("SELECT * FROM leases ORDER BY opened_at, id")


def lease_by_customer(cus: str) -> dict | None:
    return _row("SELECT * FROM leases WHERE stripe_customer_id=? ORDER BY status='active' DESC LIMIT 1", (cus,))


def active_lease_for(member_id: str) -> dict | None:
    return _row("SELECT * FROM leases WHERE member_id=? AND status='active' LIMIT 1", (member_id,))


def member_by_customer(cus: str) -> dict | None:
    return _row("SELECT * FROM members WHERE stripe_customer_id=?", (cus,))


def insert(table: str, **cols) -> int:
    keys = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    return execute(f"INSERT INTO {table}({keys}) VALUES ({marks})", tuple(cols.values()))


def update(table: str, id, **cols) -> None:
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", (*cols.values(), id))


def cash() -> int:
    return _row("SELECT cash_pence FROM pool WHERE id=1")["cash_pence"]


def add_cash(delta: int) -> int:
    execute("UPDATE pool SET cash_pence = cash_pence + ? WHERE id=1", (int(delta),))
    return cash()


def mark_processed(key: str) -> bool:
    """False if already processed."""
    with _conn() as c:
        return c.execute("INSERT OR IGNORE INTO processed(key) VALUES (?)", (key,)).rowcount == 1
