"""Invoice sync (pull) with dedupe (§8 L6)."""
from dataclasses import dataclass

from . import billing, db


@dataclass
class InvoiceEvent:
    kind: str           # paid | failed
    invoice_id: str
    customer_id: str
    amount_pence: int
    attempt_count: int
    next_payment_attempt: int | None


def _g(obj, key, default=None):
    try:
        v = obj[key]
    except (KeyError, TypeError):
        return default
    return default if v is None else v


def sync_invoices(customer_ids: list[str]) -> list[InvoiceEvent]:
    """status paid -> paid; status open with attempt_count > 0 -> failed. Oldest first, each once."""
    found = []
    for cus in customer_ids:
        if not cus:
            continue
        for inv in billing.list_invoices(cus):
            status, attempts = _g(inv, "status"), int(_g(inv, "attempt_count", 0))
            if status == "paid":
                if _g(_g(inv, "metadata", {}), "settlement") == "equity":
                    continue          # arrears settled in equity: no cash arrived
                kind, amount = "paid", int(_g(inv, "amount_paid", 0))
            elif status == "open" and attempts > 0:
                kind, amount = "failed", int(_g(inv, "amount_due", 0))
            else:
                continue
            if not db.mark_processed(f"inv:{inv['id']}:{kind}"):
                continue
            found.append((int(_g(inv, "created", 0)), InvoiceEvent(
                kind, inv["id"], cus, amount, attempts, _g(inv, "next_payment_attempt"))))
    return [ev for _, ev in sorted(found, key=lambda x: x[0])]
