"""Audit ledger: record()/recent(). Amount sign: + into the pool, − out of the pool."""
from datetime import datetime, timezone

from . import db, ui


def record(kind: str, *, reason: str, amount_pence: int = 0, member_id: str | None = None,
           lease_id: str | None = None, permille_delta: int = 0, stripe_object_id: str | None = None,
           policy_clause: str | None = None, decision_id: str | None = None) -> int:
    row_id = db.insert(
        "ledger", ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), decision_id=decision_id,
        member_id=member_id, lease_id=lease_id, kind=kind, amount_pence=int(amount_pence),
        permille_delta=int(permille_delta), stripe_object_id=stripe_object_id,
        policy_clause=policy_clause, reason=reason)
    ui.audit(kind, amount_pence, policy_clause, reason)
    return row_id


def recent(n: int = 30, member_id: str | None = None) -> list[dict]:
    if member_id:
        return db.query("SELECT * FROM ledger WHERE member_id=? ORDER BY id DESC LIMIT ?", (member_id, n))
    return db.query("SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (n,))
