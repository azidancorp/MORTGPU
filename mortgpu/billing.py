"""All Stripe calls (§8 L1). Stripe test mode, module-level API.

    python -m mortgpu.billing --selftest
"""
import time

import stripe

from . import config, db, ui

stripe.api_key = config.STRIPE_SECRET_KEY
stripe.max_network_retries = 2

CARDS = {
    "good": ["pm_card_bypassPending", "pm_card_bypassPendingInternational", "pm_card_visa"],
    "fail": ["pm_card_chargeCustomerFail"],
}


def _md(metadata: dict | None) -> dict:
    """Stripe metadata: string values, ≤500 chars, None dropped."""
    out = {}
    for k, v in (metadata or {}).items():
        if v is not None:
            out[str(k)[:40]] = str(v)[:500]
    return out


def _get(obj, key, default=None):
    try:
        v = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if v is None else v


# ---------------------------------------------------------------- catalog / customers

def _find_meter() -> str | None:
    for m in stripe.billing.Meter.list(limit=100).auto_paging_iter():
        if m.event_name == "gpu_minutes" and m.status == "active":
            return m.id
    return None


def setup_catalog() -> dict:
    rent_product = stripe.Product.create(name="GPU lease rent")
    rent_price = stripe.Price.create(
        product=rent_product.id, unit_amount=config.RENT_UNIT_PENCE, currency=config.CURRENCY,
        recurring={"interval": "month", "usage_type": "licensed"}, nickname="£0.50 per permille of pool ownership")
    # meter event_name is unique per account: reuse the active one across setup runs
    meter_id = _find_meter() or stripe.billing.Meter.create(
        display_name="GPU minutes", event_name="gpu_minutes", default_aggregation={"formula": "sum"},
        customer_mapping={"event_payload_key": "stripe_customer_id", "type": "by_id"},
        value_settings={"event_payload_key": "value"}).id
    usage_product = stripe.Product.create(name="Inference GPU-minutes")
    usage_price = stripe.Price.create(
        product=usage_product.id, unit_amount=config.USAGE_UNIT_PENCE, currency=config.CURRENCY,
        recurring={"interval": "month", "usage_type": "metered", "meter": meter_id}, nickname="£0.04 per GPU-minute")
    ids = {"rent_price": rent_price.id, "usage_price": usage_price.id, "meter_id": meter_id}
    for k, v in ids.items():
        db.kv_set(k, v)
    return ids


def create_clock(name: str) -> str:
    return stripe.test_helpers.TestClock.create(frozen_time=int(time.time()), name=name).id


def create_customer(member_id: str, name: str, email: str, *, clock_id: str | None = None,
                    card: str = "good") -> tuple[str, str]:
    params = {"name": name, "email": email, "metadata": _md({"member_id": member_id})}
    if clock_id:
        params["test_clock"] = clock_id
    cus = stripe.Customer.create(**params)
    return cus.id, set_card(cus.id, card)


def set_card(cus_id: str, card: str, *, subscription_id: str | None = None) -> str:
    last = None
    for token in CARDS[card]:
        try:
            pm = stripe.PaymentMethod.attach(token, customer=cus_id)
            break
        except stripe.error.StripeError as e:   # e.g. bypassPending unsupported on a UK account
            last = e
    else:
        raise last
    stripe.Customer.modify(cus_id, invoice_settings={"default_payment_method": pm.id})
    if subscription_id:
        stripe.Subscription.modify(subscription_id, default_payment_method=pm.id)
    return pm.id


def _default_pm(cus_id: str) -> str:
    cus = stripe.Customer.retrieve(cus_id)
    return _get(_get(cus, "invoice_settings", {}), "default_payment_method")


def charge(cus_id: str, amount_pence: int, *, description: str, metadata: dict) -> str:
    pi = stripe.PaymentIntent.create(
        amount=int(amount_pence), currency=config.CURRENCY, customer=cus_id, payment_method=_default_pm(cus_id),
        # payment_method_types is rejected by current API versions; dynamic methods, no redirects
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        off_session=True, confirm=True, description=description,
        metadata=_md(metadata))
    return pi.id


# ---------------------------------------------------------------- rent

def create_rent_subscription(cus_id: str, pool_permille: int, *, metadata: dict) -> tuple[str, str, str]:
    sub = stripe.Subscription.create(
        customer=cus_id, items=[{"price": db.kv_get("rent_price"), "quantity": int(pool_permille)}],
        payment_behavior="error_if_incomplete", metadata=_md(metadata))
    inv = sub.latest_invoice
    return sub.id, sub["items"]["data"][0]["id"], inv if isinstance(inv, str) else _get(inv, "id")


def set_rent_quantity(sub_id: str, item_id: str, pool_permille: int, *, metadata: dict) -> None:
    stripe.SubscriptionItem.modify(item_id, quantity=int(pool_permille), proration_behavior="none")
    stripe.Subscription.modify(sub_id, metadata=_md(metadata))


def preview_next_rent(cus_id: str, sub_id: str) -> int:
    return stripe.Invoice.create_preview(customer=cus_id, subscription=sub_id).total


# ---------------------------------------------------------------- usage

def create_usage_subscription(cus_id: str, *, destination_account: str | None = None) -> str:
    params = {"customer": cus_id, "items": [{"price": db.kv_get("usage_price")}]}
    if config.CONNECT_MODE == "connect" and destination_account:
        params["transfer_data"] = {"destination": destination_account}
    return stripe.Subscription.create(**params).id


def fire_meter_event(cus_id: str, minutes: int, identifier: str) -> None:
    stripe.billing.MeterEvent.create(
        event_name="gpu_minutes", identifier=identifier,
        payload={"stripe_customer_id": cus_id, "value": str(int(minutes))})


def wait_for_meter(cus_id: str, expected_total: int, timeout_s: int = 30) -> bool:
    meter_id = db.kv_get("meter_id")
    now = int(time.time())
    start, end = (now - 6 * 3600) // 60 * 60, (now // 60 + 2) * 60
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            summaries = stripe.billing.Meter.list_event_summaries(
                meter_id, customer=cus_id, start_time=start, end_time=end)
            total = sum(float(s.aggregated_value) for s in summaries.auto_paging_iter())
            if total >= expected_total:
                return True
            time.sleep(2)
        return False
    except stripe.error.StripeError as e:
        ui.warn(f"meter summaries unavailable ({e.user_message or e}); sleeping 15s")
        time.sleep(15)
        return True


# ---------------------------------------------------------------- clocks / invoices

def _wait_clock(clock_id: str, timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if stripe.test_helpers.TestClock.retrieve(clock_id).status == "ready":
            return
        time.sleep(2)
    raise TimeoutError(f"test clock {clock_id} not ready after {timeout_s}s")


def _advance(clock_id: str, frozen_time: int) -> None:
    _wait_clock(clock_id)
    stripe.test_helpers.TestClock.advance(clock_id, frozen_time=frozen_time)
    _wait_clock(clock_id)


def advance_clock_past_renewal(clock_id: str, sub_id: str) -> None:
    sub = stripe.Subscription.retrieve(sub_id)
    period_end = _get(sub["items"]["data"][0], "current_period_end") or _get(sub, "current_period_end")
    target = int(period_end) + 2 * 3600     # renewal + the ~1h draft -> finalize window
    _advance(clock_id, target)
    for _ in range(3):
        drafts = [i for i in list_invoices(sub.customer) if i.status == "draft"]
        if not drafts:
            return
        target += 2 * 3600
        _advance(clock_id, target)


def list_invoices(cus_id: str) -> list:
    return list(stripe.Invoice.list(customer=cus_id, limit=100).auto_paging_iter())


def settle_invoice_with_equity(invoice_id: str, *, metadata: dict) -> None:
    stripe.Invoice.modify(invoice_id, metadata=_md(metadata))
    stripe.Invoice.pay(invoice_id, paid_out_of_band=True)


def cancel_subscription(sub_id: str, *, metadata: dict) -> None:
    stripe.Subscription.modify(sub_id, metadata=_md(metadata))
    stripe.Subscription.cancel(sub_id)


# ---------------------------------------------------------------- money out

def transfer(account_id: str | None, amount_pence: int, *, description: str, metadata: dict) -> str | None:
    if config.CONNECT_MODE == "ledger" or not account_id or amount_pence <= 0:
        return None
    try:
        return stripe.Transfer.create(amount=int(amount_pence), currency=config.CURRENCY, destination=account_id,
                                      description=description, metadata=_md(metadata)).id
    except Exception as e:  # never raise: the ledger row is the source of truth
        ui.warn(f"transfer to {account_id} failed: {getattr(e, 'user_message', None) or e}")
        return None


def balance_available() -> int:
    return sum(b.amount for b in stripe.Balance.retrieve().available if b.currency == config.CURRENCY)


# ---------------------------------------------------------------- selftest

def _selftest() -> None:
    if not config.STRIPE_SECRET_KEY:
        raise SystemExit("STRIPE_SECRET_KEY not set (.env)")
    db.init()

    def ok(name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
        if not cond:
            raise SystemExit(1)

    ids = setup_catalog()
    ok("catalog", all(ids.values()), str(ids))
    clock = create_clock("selftest")
    ok("clock", clock.startswith("clock_"), clock)
    cus, pm = create_customer("selftest", "Self Test", "selftest@example.com", clock_id=clock)
    ok("customer", cus.startswith("cus_"), f"{cus} {pm}")
    sub, item, inv = create_rent_subscription(cus, 800, metadata={"lease_id": "L-TEST"})
    paid = stripe.Invoice.retrieve(inv)
    ok("rent sub qty 800 -> invoice paid 40,000", paid.status == "paid" and paid.amount_paid == 40_000,
       f"{paid.status} {paid.amount_paid}")
    set_rent_quantity(sub, item, 700, metadata={"lease_id": "L-TEST"})
    nxt = preview_next_rent(cus, sub)
    ok("qty 700 -> preview 35,000", nxt == 35_000, str(nxt))
    usage_sub = create_usage_subscription(cus)
    for i in range(5):
        fire_meter_event(cus, 10, f"selftest-{usage_sub}-{i}")
    ok("5 meter events", True)
    ok("meter summary", wait_for_meter(cus, 50, timeout_s=60))
    set_card(cus, "fail", subscription_id=sub)
    ok("fail card attached", True)
    advance_clock_past_renewal(clock, sub)
    failed = [i for i in list_invoices(cus) if i.status == "open" and (i.attempt_count or 0) > 0]
    ok("advance -> failed invoice listed", bool(failed), ", ".join(i.id for i in failed))
    t = transfer(config.SUPPLIER_ACCOUNT_ID, 100, description="selftest", metadata={"reason": "selftest"})
    ok("transfer returns an id or None", t is None or t.startswith("tr_"), str(t))
    stripe.test_helpers.TestClock.delete(clock)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
