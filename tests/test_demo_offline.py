"""End-to-end: setup + all 6 scenes against a fake Stripe (billing.* replaced) and the real mock GPU rack.

No network, no keys: LLM_MODE=fixture, COGNEE_ENABLED=0, CONNECT_MODE=connect (fake transfers).
Asserts the §9 worked numbers through the whole agent stack.
"""
import itertools
import os
import socket
import threading
import time

import pytest
import uvicorn

from mortgpu import agent, billing, config, db, demo, gpu, pool, setup


class FakeStripe:
    def __init__(self):
        self.ids = itertools.count(1)
        self.customers, self.subs, self.invoices = {}, {}, []
        self.usage = {}

    def _id(self, prefix):
        return f"{prefix}_{next(self.ids):04d}"

    def _invoice(self, cus, amount, sub=None):
        good = self.customers[cus]["card"] == "good"
        inv = {"id": self._id("in"), "customer": cus, "subscription": sub, "created": next(self.ids),
               "status": "paid" if good or amount == 0 else "open", "amount_paid": amount if good else 0,
               "amount_due": amount, "attempt_count": 1, "next_payment_attempt": None if good else 9999,
               "metadata": {}}
        self.invoices.append(inv)
        return inv

    # billing API
    def setup_catalog(self):
        ids = {"rent_price": "price_rent", "usage_price": "price_usage", "meter_id": "mtr_1"}
        for k, v in ids.items():
            db.kv_set(k, v)
        return ids

    def create_clock(self, name):
        return self._id("clock")

    def create_customer(self, member_id, name, email, *, clock_id=None, card="good"):
        cus = self._id("cus")
        self.customers[cus] = {"card": card, "clock": clock_id}
        return cus, self._id("pm")

    def set_card(self, cus_id, card, *, subscription_id=None):
        self.customers[cus_id]["card"] = card
        return self._id("pm")

    def charge(self, cus_id, amount_pence, *, description, metadata):
        assert self.customers[cus_id]["card"] == "good", "card declined"
        assert isinstance(amount_pence, int)
        return self._id("pi")

    def create_rent_subscription(self, cus_id, pool_permille, *, metadata):
        sub = self._id("sub")
        self.subs[sub] = {"customer": cus_id, "qty": pool_permille, "kind": "rent", "active": True}
        inv = self._invoice(cus_id, pool_permille * config.RENT_UNIT_PENCE, sub)
        return sub, self._id("si"), inv["id"]

    def set_rent_quantity(self, sub_id, item_id, pool_permille, *, metadata):
        self.subs[sub_id]["qty"] = pool_permille

    def preview_next_rent(self, cus_id, sub_id):
        return self.subs[sub_id]["qty"] * config.RENT_UNIT_PENCE

    def create_usage_subscription(self, cus_id, *, destination_account=None):
        sub = self._id("sub")
        self.subs[sub] = {"customer": cus_id, "kind": "usage", "active": True}
        self._invoice(cus_id, 0, sub)
        return sub

    def fire_meter_event(self, cus_id, minutes, identifier):
        self.usage[cus_id] = self.usage.get(cus_id, 0) + minutes

    def wait_for_meter(self, cus_id, expected_total, timeout_s=30):
        return self.usage.get(cus_id, 0) >= expected_total

    def advance_clock_past_renewal(self, clock_id, sub_id):
        for sub, s in self.subs.items():
            if not s["active"] or self.customers[s["customer"]]["clock"] != clock_id:
                continue
            if s["kind"] == "rent":
                self._invoice(s["customer"], s["qty"] * config.RENT_UNIT_PENCE, sub)
            else:
                self._invoice(s["customer"], self.usage.pop(s["customer"], 0) * config.USAGE_UNIT_PENCE, sub)

    def list_invoices(self, cus_id):
        return [dict(i) for i in self.invoices if i["customer"] == cus_id]

    def settle_invoice_with_equity(self, invoice_id, *, metadata):
        inv = next(i for i in self.invoices if i["id"] == invoice_id)
        inv.update(status="paid", metadata=metadata)

    def cancel_subscription(self, sub_id, *, metadata):
        self.subs[sub_id]["active"] = False

    def transfer(self, account_id, amount_pence, *, description, metadata):
        assert amount_pence > 0
        return self._id("tr") if account_id else None

    def balance_available(self):
        return 10_000_000


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "GPU_STATE", tmp_path / "gpu_state.json")
    monkeypatch.setattr(config, "USAGE_LOG", tmp_path / "usage.log")
    monkeypatch.setattr(config, "LLM_MODE", os.environ.get("E2E_LLM_MODE", "fixture"))
    monkeypatch.setattr(config, "COGNEE_ENABLED", False)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(config, "SUPPLIER_ACCOUNT_ID", "acct_supplier")
    monkeypatch.setattr(config, "MEMBER_ACCOUNTS", {m: f"acct_{m}" for m in config.MEMBER_IDS})
    fake = FakeStripe()
    for name in [n for n in dir(fake) if not n.startswith("_")]:
        if hasattr(billing, name):
            monkeypatch.setattr(billing, name, getattr(fake, name))
    port = _free_port()
    monkeypatch.setattr(config, "GPU_API_URL", f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(gpu.app, port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield fake
    server.should_exit = True
    t.join(5)


def rows(kind, **where):
    out = db.query("SELECT * FROM ledger WHERE kind=? ORDER BY id", (kind,))
    return [r for r in out if all(r[k] == v for k, v in where.items())]


def capital(mid):
    return db.member(mid)["capital_pence"]


def test_full_demo(world):
    setup.main()
    assert db.cash() == 1_060_000
    assert pool.invariant_ok(db.members(), db.leases(), db.cash())

    demo.scene1()
    amara = db.lease("L-AMARA")
    assert (amara["gpu_id"], amara["pool_permille"], capital("amara")) == ("GPU-001", 800, 0)
    dist = {r["member_id"]: -r["amount_pence"] for r in rows("rent_distribution")}
    assert dist == {"priya": 21_390, "tom": 14_973, "ben": 2_567, "carl": 1_070}
    assert db.cash() == 60_000
    assert pool.invariant_ok(db.members(), db.leases(), db.cash())

    demo.scene2()
    assert "ICR 300%" in rows("icr_check")[-1]["reason"]
    assert len(rows("rent_paid")) == 2

    demo.scene3()
    assert db.lease("L-AMARA")["pool_permille"] == 700
    assert "£400.00 → £350.00" in rows("rent_repriced")[-1]["reason"]

    demo.scene4()
    carl = db.lease("L-CARL")
    assert (carl["pool_permille"], carl["status"]) == (1000, "repossessed")
    assert db.query("SELECT verdict FROM decisions WHERE action='payment_holiday'")[0]["verdict"] == "BLOCK"
    assert rows("holiday_blocked")
    assert "£80.00" in rows("write_off")[-1]["reason"]
    credits = {r["member_id"]: r["reason"] for r in rows("in_kind_distribution")}
    assert set(credits) == {"priya", "tom", "ben"}
    assert (capital("priya"), capital("tom"), capital("ben"), capital("carl")) == (
        1_021_978, 715_385, 122_637, 50_000)          # Carl's savings untouched (§4.5)
    assert rows("access_revoked") and rows("relisted")
    assert not any(s["active"] for s in world.subs.values() if s["customer"] == carl["stripe_customer_id"])

    demo.scene5()
    ben = db.lease("L-BEN")
    assert (ben["pool_permille"], ben["status"]) == (0, "title_transferred")
    from mortgpu import gpu_client
    assert gpu_client.get("GPU-002")["title_holder"] == "ben"
    assert gpu_client.revoke("GPU-002", "test") == 409

    demo.scene6()     # raises SystemExit if the books don't balance
    answer = agent.explain("Why was Carl disconnected?")
    print("\nANSWER:", answer)
    assert "4.3" in answer or config.LLM_MODE == "live"
    assert db.cash() == 60_000 + 100_000 + 50_000
