"""Seed Stripe + DB + GPU rack + memory (§8 L7). Idempotent: wipes the DB, creates fresh Stripe objects.

    python -m mortgpu.setup
"""
import json
import sys

from . import billing, config, db, gpu_client, ledger, memory, observer, policy, pool, ui
from .ui import money, pct

PRICE = config.ASSET_PRICE_PENCE
CLOCKED = {"amara": "CLOCK_A", "carl": "CLOCK_B"}
SEED_LEASES = [   # (lease, member, gpu, pool_permille, opened_at)
    ("L-BEN", "ben", "GPU-002", 50, "2026-04-01"),
    ("L-CARL", "carl", "GPU-003", 960, "2026-01-10"),
]
GPU_MODEL = "NVIDIA RTX PRO 6000 Blackwell 96GB"


def main() -> None:
    if not config.STRIPE_SECRET_KEY:
        raise SystemExit("STRIPE_SECRET_KEY not set (.env)")
    ui.console.rule("[bold red]mortGPU setup")

    # 1. DB + catalog
    db.init(wipe=True)
    ids = billing.setup_catalog()
    ui.step("ACT", f"catalog: {ids}")

    # 2. clocks
    for key in ("CLOCK_A", "CLOCK_B"):
        clock = billing.create_clock(f"mortGPU {key}")
        db.kv_set(key, clock)
        ui.step("ACT", f"{key} = {clock}")

    # 3. members + contributions (charged for real in test mode)
    members = json.loads((config.FIXTURES / "members.json").read_text())
    for m in members:
        clock = db.kv_get(CLOCKED[m["id"]]) if m["id"] in CLOCKED else None
        cus, _ = billing.create_customer(m["id"], m["name"], m["email"], clock_id=clock)
        db.insert("members", id=m["id"], name=m["name"], email=m["email"], capital_pence=0,
                  credits_reset_at=m["credits_reset_at"], stripe_customer_id=cus,
                  connect_account_id=config.MEMBER_ACCOUNTS.get(m["id"]))
        for c in m["contributions"]:
            pi = billing.charge(cus, c["amount_pence"], description=f"{m['name']} pool contribution",
                                metadata={"member_id": m["id"], "contributed_at": c["contributed_at"],
                                          "reason": "savings contribution"})
            db.insert("contributions", member_id=m["id"], amount_pence=c["amount_pence"],
                      contributed_at=c["contributed_at"], stripe_payment_intent_id=pi)
            db.update("members", m["id"], capital_pence=db.member(m["id"])["capital_pence"] + c["amount_pence"])
            db.add_cash(c["amount_pence"])
            ledger.record("contribution", reason=f"{m['name']} saved {money(c['amount_pence'])} on "
                          f"{c['contributed_at']}", amount_pence=c["amount_pence"], member_id=m["id"],
                          stripe_object_id=pi)
    op, _ = billing.create_customer("operator", "mortGPU operator", "ops@example.com")
    db.kv_set("operator_customer", op)
    billing.charge(op, 50_000, description="fee float (covers Stripe fees; not member capital)",
                   metadata={"reason": "fee float"})
    ui.step("ACT", "operator fee float £500.00 charged (not member capital)")

    # 4. opening: the pool already holds stakes in Ben's and Carl's GPUs
    stakes = sum(pm * PRICE // 1000 for _, _, _, pm, _ in SEED_LEASES)
    reason = "opening: pool stakes in GPU-002 (Ben) and GPU-003 (Carl)"
    tr = billing.transfer(config.SUPPLIER_ACCOUNT_ID, stakes, description=reason, metadata={"reason": reason})
    db.add_cash(-stakes)
    ledger.record("opening_balance", reason=f"{reason}; cash {money(db.cash())}", amount_pence=-stakes,
                  stripe_object_id=tr)

    # 5. GPU rack
    gpus = gpu_client.seed([
        {"id": gpu, "model": GPU_MODEL, "title_holder": "pool", "user_member_id": mid, "status": "active"}
        for _, mid, gpu, _, _ in SEED_LEASES])
    for g in gpus:
        db.kv_set(f"gpu_key:{g['id']}", g["api_key"])
    ui.step("ACT", "GPU rack seeded: " + ", ".join(f"{g['id']} ({g['user_member_id']})" for g in gpus))

    # 6. existing leases
    for lease_id, mid, gpu, pool_permille, opened in SEED_LEASES:
        m = db.member(mid)
        rent = pool.rent_pence(PRICE, pool_permille)
        md = {"lease_id": lease_id, "member_id": mid, "policy_clause": "3.1",
              "reason": f"rent {money(rent)}/month for {pct(pool_permille)} pool share"}
        sub, item, _ = billing.create_rent_subscription(m["stripe_customer_id"], pool_permille, metadata=md)
        db.insert("leases", id=lease_id, member_id=mid, gpu_id=gpu, price_pence=PRICE, pool_permille=pool_permille,
                  status="active", stripe_customer_id=m["stripe_customer_id"], stripe_subscription_id=sub,
                  stripe_subscription_item_id=item, clock_id=db.kv_get(CLOCKED[mid]) if mid in CLOCKED else None,
                  opened_at=opened)
        ui.step("ACT", f"{lease_id}: {gpu}, pool {pct(pool_permille)}, rent {money(rent)} → {sub}")
    carl = db.lease("L-CARL")
    billing.set_card(db.member("carl")["stripe_customer_id"], "fail", subscription_id=carl["stripe_subscription_id"])
    ui.step("ACT", "Carl's card on file now declines (pm_card_chargeCustomerFail)")

    # 7. Nova Labs: Amara's inference customer, billed per GPU-minute
    nova, _ = billing.create_customer("nova", "Nova Labs", "billing@novalabs.example",
                                      clock_id=db.kv_get("CLOCK_A"))
    usage_sub = billing.create_usage_subscription(nova, destination_account=config.MEMBER_ACCOUNTS.get("amara"))
    db.kv_set("nova_customer", nova)
    db.kv_set("nova_member", "amara")
    db.kv_set("nova_subscription", usage_sub)
    ui.step("ACT", f"Nova Labs {nova}: metered subscription {usage_sub}")

    # 8. memory: policy clauses + Carl's history (forget the previous rehearsal first)
    memory.reset()
    for clause, text in policy.load()["clauses"].items():
        memory.remember(f"Policy §{clause}: {text}", dataset="policy")
    hist = json.loads((config.FIXTURES / "history" / "carl.json").read_text())
    for ev in hist["events"]:
        memory.remember(ev["text"], dataset="promises", member_id=hist["member_id"])
        pr = ev.get("promise")
        if pr:
            memory.record_promise(hist["member_id"], pr["kind"], pr["text"], pr["due_at"], "D-history",
                                  made_at=ev["date"], status=pr["status"])
        ledger.record("history", reason=ev["text"], member_id=hist["member_id"], lease_id=hist["lease_id"],
                      policy_clause=ev.get("clause"))
    ui.step("MEMORY", f"facts(carl) = {memory.facts('carl')}")

    # 9. mark seed invoices processed
    customers = [m["stripe_customer_id"] for m in db.members()] + [nova]
    seen = observer.sync_invoices(customers)
    ui.step("OBSERVE", f"{len(seen)} seed invoices marked processed")

    # 10. checks + summary
    ui.step("MEMORY", "waiting for Cognee to index the seeds…")
    memory.flush(timeout_s=900)
    ok = pool.invariant_ok(db.members(), db.leases(), db.cash())
    bal = billing.balance_available()
    q = pool.queue(db.members(), db.contributions(), config.DEMO_TODAY, PRICE,
                   leased={l["member_id"] for l in db.leases()})
    ui.table("Queue (credits as of 2026-10-15)", ["member", "credits", "savings", "eligible"],
             [[r["member_id"], f"{r['credits']:,}", money(r["savings_pence"]), "✓" if r["eligible"] else r["why"]]
              for r in q])
    ui.info(f"pool cash {money(db.cash())} · invariant {'✓' if ok else '✗'} · Stripe available {money(bal)}")
    failures = []
    if not ok:
        failures.append("invariant")
    if bal < PRICE:
        failures.append(f"balance_available {money(bal)} < {money(PRICE)}")
    if failures:
        ui.console.print(f"[bold red]SETUP CHECKS FAILED: {', '.join(failures)}")
        sys.exit(1)
    ui.console.print("[bold green]setup complete ✓")


if __name__ == "__main__":
    main()
