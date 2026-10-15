"""Orchestration (§8 L4): observe → propose → (memory) → gate → act → remember → audit.

Every money/GPU action runs only after policy.evaluate() returns ALLOW, and carries the decision id
into the ledger and into Stripe metadata.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import billing, config, db, gpu_client, ledger, llm, memory, observer, policy, pool, ui
from .models import ClauseResult, Decision, Proposal, new_decision_id
from .ui import money, pct

TODAY = config.DEMO_TODAY


# ---------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _name(member_id: str | None) -> str:
    m = db.member(member_id) if member_id else None
    return m["name"] if m else (member_id or "?")


def _first(member_id: str | None) -> str:
    return _name(member_id).split()[0]


def _leased() -> set[str]:
    return {l["member_id"] for l in db.leases() if l["status"] == "active"}


def _arrears(lease_id: str | None) -> int:
    return int(db.kv_get(f"arrears:{lease_id}") or 0) if lease_id else 0


def build_ctx(member_id: str | None, lease: dict | None = None, price: int = config.ASSET_PRICE_PENCE) -> dict:
    lease = lease or (db.active_lease_for(member_id) if member_id else None)
    return {
        "queue": pool.queue(db.members(), db.contributions(), TODAY, price, leased=_leased()),
        "member": db.member(member_id) if member_id else None,
        "lease": lease,
        "cash_pence": db.cash(),
        "facts": memory.facts(member_id, TODAY) if member_id else {"holidays_12m": 0, "broken_promises": 0},
        "arrears_pence": _arrears(lease["id"]) if lease else 0,
        "policy": policy.load(),
    }


def _context_text(member_id: str | None = None) -> str:
    lines = ["Members: " + ", ".join(f"{m['id']} ({m['name']})" for m in db.members())]
    for l in db.leases():
        if l["status"] == "active":
            lines.append(f"Active lease {l['id']}: member {l['member_id']}, {l['gpu_id']}, "
                         f"pool owns {pct(l['pool_permille'])}")
    return "\n".join(lines)


def _md(decision_id: str | None, clause: str | None, reason: str, lease: dict | None = None,
        member_id: str | None = None, **extra) -> dict:
    md = {"decision_id": decision_id, "policy_clause": clause, "reason": reason[:500],
          "lease_id": lease["id"] if lease else None, "member_id": member_id or (lease or {}).get("member_id")}
    md.update(extra)
    return {k: v for k, v in md.items() if v is not None}


def _save_decision(d: Decision, trigger_kind: str, trigger_ref: str) -> None:
    db.insert("decisions", id=d.id, ts=_now(), trigger_kind=trigger_kind, trigger_ref=trigger_ref,
              action=d.proposal.action, member_id=d.proposal.member_id,
              proposal_json=json.dumps(d.proposal.to_dict()), verdict=d.verdict,
              clauses_json=json.dumps([c.__dict__ for c in d.clauses]), reason=d.reason)


def _show_proposal(p: Proposal) -> None:
    bits = [f"[bold]{p.action}[/]", p.member_id]
    if p.amount_pence is not None:
        bits.append(money(p.amount_pence))
    if p.terms:
        t = p.terms
        bits.append(f"{t.get('gpu_model')} from {t.get('supplier_id')} at {money(t.get('price_pence'))}, "
                    f"projected {money(t.get('projected_revenue_pence'))}/month")
    ui.step("PROPOSE", " · ".join(str(b) for b in bits))
    if p.evidence:
        ui.step("PROPOSE", f'[italic]“{p.evidence}”[/]')


def _gate(p: Proposal, ctx: dict, trigger_kind: str, trigger_ref: str) -> Decision:
    d = policy.evaluate(p, ctx)
    ui.gate(d.clauses)
    ui.verdict(d)
    _save_decision(d, trigger_kind, trigger_ref)
    return d


def _weights_excluding(member_id: str) -> dict[str, int]:
    return {m["id"]: m["capital_pence"] for m in db.members() if m["id"] != member_id}


# ---------------------------------------------------------------- Scene 1: contract

def handle_contract(path: str) -> Decision:
    text = Path(path).read_text()
    ui.step("OBSERVE", f"contract received: {Path(path).name}")
    p = llm.propose("contract", text, _context_text())
    _show_proposal(p)
    price = int((p.terms or {}).get("price_pence") or config.ASSET_PRICE_PENCE)
    d = _gate(p, build_ctx(p.member_id, price=price), "contract", Path(path).name)
    if d.allowed:
        _open_lease(d)
    memory.remember(f"{_now()[:10]}: decision {d.id} on {_name(p.member_id)}'s lease contract: {d.verdict}. "
                    f"{d.reason}", dataset="decisions", member_id=p.member_id)
    return d


def _open_lease(d: Decision) -> None:
    p, t = d.proposal, d.proposal.terms
    m = db.member(p.member_id)
    price, lease_id = int(t["price_pence"]), f"L-{p.member_id.upper()}"

    # 1. savings -> deposit / initial equity (§1.2), credits reset (§1.3)
    buyer_permille, used = pool.convert_savings(m["capital_pence"], price)
    pool_permille = 1000 - buyer_permille
    db.update("members", m["id"], capital_pence=m["capital_pence"] - used, credits_reset_at=TODAY.isoformat())
    ui.step("ACT", f"{_first(m['id'])}'s savings {money(used)} → deposit = {pct(buyer_permille)} equity; "
                   f"pool owns {pct(pool_permille)}; credits reset")
    ledger.record("savings_to_equity", reason=f"{money(used)} savings → {pct(buyer_permille)} equity at "
                  f"locked price {money(price)}; credits reset (§1.3)", member_id=m["id"], lease_id=lease_id,
                  permille_delta=buyer_permille, policy_clause="1.2", decision_id=d.id)

    # 2. pay the supplier (§2.3)
    reason = f"pay {t['supplier_id']} {money(price)} for {t['gpu_model']}"
    tr = billing.transfer(config.SUPPLIER_ACCOUNT_ID, price, description=reason,
                          metadata=_md(d.id, "2.3", reason, member_id=m["id"], lease_id=lease_id))
    db.add_cash(-price)
    ui.step("ACT", f"supplier paid {money(price)} → {tr or 'ledger only'}")
    ledger.record("supplier_payment", reason=reason, amount_pence=-price, member_id=m["id"], lease_id=lease_id,
                  stripe_object_id=tr, policy_clause="2.3", decision_id=d.id)

    # 3. provision the GPU
    gpu = gpu_client.provision(t["gpu_model"], m["id"])
    db.kv_set(f"gpu_key:{gpu['id']}", gpu["api_key"])
    ui.step("ACT", f"provisioned {gpu['id']} ({gpu['model']}) for {_first(m['id'])}; title holder: pool")
    ledger.record("provision", reason=f"{gpu['id']} provisioned, title held by pool", member_id=m["id"],
                  lease_id=lease_id, decision_id=d.id)

    # 4. rent subscription (§3.1)
    rent = pool.rent_pence(price, pool_permille)
    reason = f"rent {money(rent)}/month = 5% × {money(price)} × {pct(pool_permille)} pool share"
    sub, item, inv = billing.create_rent_subscription(
        m["stripe_customer_id"], pool_permille, metadata=_md(d.id, "3.1", reason, member_id=m["id"],
                                                               lease_id=lease_id))
    db.insert("leases", id=lease_id, member_id=m["id"], gpu_id=gpu["id"], price_pence=price,
              pool_permille=pool_permille, status="active", stripe_customer_id=m["stripe_customer_id"],
              stripe_subscription_id=sub, stripe_subscription_item_id=item,
              clock_id=db.kv_get("CLOCK_A"), opened_at=_now())
    ui.step("ACT", f"rent subscription {sub}: qty {pool_permille} × £0.50 = {money(rent)}/month "
                   f"(first invoice {inv})")
    ledger.record("rent_subscription", reason=reason, member_id=m["id"], lease_id=lease_id,
                  stripe_object_id=sub, policy_clause="3.1", decision_id=d.id)

    # 5. remember
    memory.remember(f"{TODAY.isoformat()}: {m['name']} took her turn (§1.1). Lease {lease_id} opened on "
                    f"{gpu['id']} ({t['gpu_model']}) at locked price {money(price)}. Deposit {money(used)} = "
                    f"{pct(buyer_permille)} equity; pool owns {pct(pool_permille)}; rent {money(rent)}/month. "
                    f"Supplier {t['supplier_id']} paid {money(price)}. Decision {d.id}.",
                    dataset="decisions", member_id=m["id"])


# ---------------------------------------------------------------- Scenes 3, 4, 5: email

def _inbox_reply(member_id: str) -> str | None:
    hits = sorted((config.FIXTURES / "inbox").glob(f"*{member_id}*"))
    return str(hits[0]) if hits else None


def handle_email(path: str) -> Decision:
    text = Path(path).read_text()
    ui.step("OBSERVE", f"email received: {Path(path).name}")
    body = text.split("\n\n", 1)[-1].strip()
    ui.step("OBSERVE", f'[italic]“{body}”[/]')
    p = llm.propose("email", text, _context_text())
    _show_proposal(p)
    lease = db.active_lease_for(p.member_id)

    if p.action == "payment_holiday":
        q = f"Has {_name(p.member_id)} kept past payment promises?"
        ui.step("MEMORY", f"recall: {q}")
        for passage in memory.recall(q, k=3):
            ui.step("MEMORY", f"[yellow]· {passage[:220]}[/]")
        f = memory.facts(p.member_id, TODAY)
        ui.step("MEMORY", f"facts (structured mirror): {f['holidays_12m']} holiday(s) in 12 months, "
                          f"{f['broken_promises']} broken promise(s)")

    d = _gate(p, build_ctx(p.member_id, lease), "email", Path(path).name)

    if p.action == "buyback" and d.allowed:
        _buyback(d, lease)
    elif p.action == "payment_holiday":
        if d.allowed:
            due = (TODAY + timedelta(days=30)).isoformat()
            ledger.record("holiday_granted", reason=f"payment holiday granted; promise due {due}",
                          member_id=p.member_id, lease_id=lease and lease["id"], policy_clause="4.2",
                          decision_id=d.id)
            memory.record_promise(p.member_id, "holiday", p.evidence or body, due, d.id)
        else:
            ledger.record("holiday_blocked", reason=d.reason, member_id=p.member_id,
                          lease_id=lease and lease["id"], policy_clause="4.2", decision_id=d.id)
    memory.remember(f"{TODAY.isoformat()}: {_name(p.member_id)} emailed: \"{body}\". Proposed {p.action}"
                    f"{' ' + money(p.amount_pence) if p.amount_pence else ''}. Verdict {d.verdict} "
                    f"(decision {d.id}): {d.reason}", dataset="decisions", member_id=p.member_id)
    return d


def _buyback(d: Decision, lease: dict) -> None:
    p = d.proposal
    m = db.member(p.member_id)
    amt, price = int(p.amount_pence), lease["price_pence"]
    delta = pool.permille_for(amt, price)
    old_rent = pool.rent_pence(price, lease["pool_permille"])

    # 1. charge the card on file (§5.2)
    reason = f"buyback {money(amt)} = {pct(delta)} at locked price {money(price)}"
    pi = billing.charge(m["stripe_customer_id"], amt, description=reason,
                        metadata=_md(d.id, "5.2", reason, lease))
    db.add_cash(amt)
    ui.step("ACT", f"charged {_first(m['id'])}'s card {money(amt)} → {pi}")
    ledger.record("buyback", reason=reason, amount_pence=amt, permille_delta=-delta, member_id=m["id"],
                  lease_id=lease["id"], stripe_object_id=pi, policy_clause="5.2", decision_id=d.id)

    # 2. ownership moves to the member
    new_pool = lease["pool_permille"] - delta
    db.update("leases", lease["id"], pool_permille=new_pool)
    ui.step("ACT", f"pool share {pct(lease['pool_permille'])} → {pct(new_pool)}; "
                   f"{_first(m['id'])} owns {pct(1000 - new_pool)}")

    if new_pool > 0:
        # 3. reprice rent (§3.1)
        new_rent = pool.rent_pence(price, new_pool)
        reason = f"rent {money(old_rent)} → {money(new_rent)} (pool share {pct(new_pool)})"
        billing.set_rent_quantity(lease["stripe_subscription_id"], lease["stripe_subscription_item_id"], new_pool,
                                  metadata=_md(d.id, "3.1", reason, lease))
        ui.step("ACT", f"rent subscription quantity {lease['pool_permille']} → {new_pool}")
        ledger.record("rent_repriced", reason=reason, member_id=m["id"], lease_id=lease["id"],
                      stripe_object_id=lease["stripe_subscription_id"], policy_clause="3.1", decision_id=d.id)
        try:
            preview = billing.preview_next_rent(lease["stripe_customer_id"], lease["stripe_subscription_id"])
            ui.step("OBSERVE", f"Stripe preview of next rent invoice: [bold]{money(preview)}[/]")
        except Exception as e:
            ui.warn(f"preview unavailable: {e}")
        memory.remember(f"{TODAY.isoformat()}: {m['name']} bought back {money(amt)} ({pct(delta)}). Pool share "
                        f"now {pct(new_pool)}; rent {money(old_rent)} → {money(new_rent)}. Decision {d.id}.",
                        dataset="decisions", member_id=m["id"])
    else:
        # 4. title transfer at 100% equity (§5.1)
        reason = f"{m['name']} owns 100% with zero arrears: title of {lease['gpu_id']} transferred"
        billing.cancel_subscription(lease["stripe_subscription_id"], metadata=_md(d.id, "5.1", reason, lease))
        ui.step("ACT", f"rent subscription {lease['stripe_subscription_id']} cancelled")
        code = gpu_client.transfer_title(lease["gpu_id"], m["id"])
        ui.step("ACT", f"GPU API transfer_title {lease['gpu_id']} → {m['id']}: HTTP {code}")
        ledger.record("title_transferred", reason=reason, member_id=m["id"], lease_id=lease["id"],
                      policy_clause="5.1", decision_id=d.id)
        db.update("leases", lease["id"], status="title_transferred", closed_at=_now())
        memory.remember(f"{TODAY.isoformat()}: {m['name']} paid the final {money(amt)} and reached 100% equity. "
                        f"Title to {lease['gpu_id']} transferred to {m['name']} (§5.1). Decision {d.id}.",
                        dataset="decisions", member_id=m["id"])


# ---------------------------------------------------------------- Scene 2: usage -> meter events

def process_usage() -> int:
    offset = int(db.kv_get("usage_offset") or 0)
    if not config.USAGE_LOG.exists():
        return 0
    with open(config.USAGE_LOG, "rb") as f:
        f.seek(offset)
        data = f.read()
    complete = data[:data.rfind(b"\n") + 1]           # never consume a half-written line
    per_customer: dict[str, int] = {}
    count = 0
    for raw in complete.decode().splitlines():
        if not raw.strip():
            continue
        line = json.loads(raw)
        if not db.mark_processed(f"usage:{line['id']}"):
            continue
        billing.fire_meter_event(line["stripe_customer_id"], line["minutes"], line["id"])
        per_customer[line["stripe_customer_id"]] = per_customer.get(line["stripe_customer_id"], 0) + line["minutes"]
        count += 1
    db.kv_set("usage_offset", offset + len(complete))
    total = sum(per_customer.values())
    if count:
        ui.step("ACT", f"{count} usage lines → {count} Stripe meter events, {total:,} GPU-minutes "
                       f"(£0.04/min = {money(total * config.USAGE_UNIT_PENCE)})")
        for cus, minutes in per_customer.items():
            m = db.member(db.kv_get("nova_member") or "") if cus == db.kv_get("nova_customer") else None
            ledger.record("usage_metered", reason=f"{minutes:,} GPU-minutes metered for {cus}",
                          member_id=m and m["id"], lease_id=m and f"L-{m['id'].upper()}")
            ui.step("OBSERVE", "waiting for Stripe to aggregate meter events…")
            ok = billing.wait_for_meter(cus, minutes)
            ui.step("OBSERVE", f"meter {'summary ✓' if ok else 'events accepted (summary aggregates async)'}: {minutes:,} minutes")
    return total


# ---------------------------------------------------------------- invoices

def sync(customer_ids: list[str]) -> list[Decision]:
    decisions = []
    for ev in observer.sync_invoices(customer_ids):
        if ev.kind == "paid":
            on_invoice_paid(ev)
        else:
            d = on_invoice_failed(ev)
            if d:
                decisions.append(d)
    return decisions


def on_invoice_paid(ev) -> None:
    if ev.amount_pence <= 0:
        return
    lease = db.lease_by_customer(ev.customer_id)
    if lease:
        _rent_paid(ev, lease)
    elif ev.customer_id == db.kv_get("nova_customer"):
        _usage_paid(ev)


def _rent_paid(ev, lease: dict) -> None:
    amt, lessee = ev.amount_pence, lease["member_id"]
    ui.step("OBSERVE", f"invoice {ev.invoice_id} paid: {_first(lessee)}'s rent {money(amt)}")
    db.add_cash(amt)
    ledger.record("rent_paid", reason=f"rent {money(amt)} received from {_name(lessee)}", amount_pence=amt,
                  member_id=lessee, lease_id=lease["id"], stripe_object_id=ev.invoice_id, policy_clause="3.1")
    split = pool.split_pro_rata(amt, _weights_excluding(lessee))
    for mid, share in split.items():
        if share <= 0:
            continue
        m = db.member(mid)
        reason = f"{_first(mid)}'s pro-rata share of {_first(lessee)}'s rent {money(amt)}"
        tr = billing.transfer(m["connect_account_id"], share, description=reason,
                              metadata=_md(None, "3.3", reason, lease, member_id=mid, invoice_id=ev.invoice_id))
        db.add_cash(-share)
        ledger.record("rent_distribution", reason=reason, amount_pence=-share, member_id=mid, lease_id=lease["id"],
                      stripe_object_id=tr, policy_clause="3.3")
    ui.step("ACT", "rent split: " + " · ".join(f"{k} {money(v)}" for k, v in split.items() if v))


def _usage_paid(ev) -> None:
    member_id = db.kv_get("nova_member") or "amara"
    lease = db.active_lease_for(member_id)
    amt = ev.amount_pence
    ui.step("OBSERVE", f"invoice {ev.invoice_id} paid: Nova Labs → {_first(member_id)} {money(amt)} for inference")
    ledger.record("member_revenue", reason=f"Nova Labs paid {money(amt)} for GPU-minutes (member revenue, "
                  f"not pool cash)", member_id=member_id, lease_id=lease and lease["id"],
                  stripe_object_id=ev.invoice_id)
    if not lease:
        return
    rent = pool.rent_pence(lease["price_pence"], lease["pool_permille"])
    icr = amt * 100 // rent if rent else 0
    ok = icr >= policy.load()["params"]["icr_min_pct"]
    ui.step("GATE", f"[{'green' if ok else 'red'}]{'✓' if ok else '⚠'} §3.2[/] ICR {money(amt)} / {money(rent)} = "
                    f"{icr}% {'≥' if ok else '<'} 125%")
    ledger.record("icr_check", reason=f"ICR {icr}% = revenue {money(amt)} / rent {money(rent)} "
                  f"{'✓' if ok else '⚠ below 125%'}", member_id=member_id, lease_id=lease["id"],
                  policy_clause="3.2")
    memory.remember(f"{TODAY.isoformat()}: {_name(member_id)} earned {money(amt)} from Nova Labs; ICR {icr}% "
                    f"against rent {money(rent)} ({'healthy' if ok else 'flagged'}, §3.2).",
                    dataset="decisions", member_id=member_id)


def on_invoice_failed(ev) -> Decision | None:
    lease = db.lease_by_customer(ev.customer_id)
    if not lease or lease["status"] != "active":
        return None
    mid, name, first = lease["member_id"], _name(lease["member_id"]), _first(lease["member_id"])
    amt = ev.amount_pence
    ui.step("OBSERVE", f"invoice {ev.invoice_id} FAILED: {first}'s rent {money(amt)} "
                       f"(attempt {ev.attempt_count})")

    # 1. §4.1 enforcement only after the final failed attempt
    final = ev.next_payment_attempt is None or config.DEMO_FIRST_FAILURE_IS_FINAL
    why = ("no retry scheduled" if ev.next_payment_attempt is None
           else "treated as final: DEMO_FIRST_FAILURE_IS_FINAL=1 (retries off for demo)")
    ledger.record("payment_failed", reason=f"rent {money(amt)} failed, attempt {ev.attempt_count}; "
                  f"{why if final else 'retry scheduled, no enforcement yet'}", member_id=mid,
                  lease_id=lease["id"], stripe_object_id=ev.invoice_id, policy_clause="4.1")
    if not final:
        return None
    db.kv_set(f"arrears:{lease['id']}", _arrears(lease["id"]) + amt)
    notice = (f"Dear {first}, your rent of {money(amt)} for {lease['gpu_id']} could not be collected. "
              f"Unless agreed otherwise, unpaid rent is settled from your equity at the locked price (§4.3).")
    ui.step("ACT", f"notice sent → {first}: [italic]{notice}[/]")
    ledger.record("notice_sent", reason=notice, member_id=mid, lease_id=lease["id"], policy_clause="4.1")

    # 2. the member's reply, if any
    reply = _inbox_reply(mid)
    hd = None
    if reply:
        hd = handle_email(reply)
        if hd.proposal.action == "payment_holiday" and hd.allowed:
            return hd

    # 3. §4.3 settle arrears in equity
    lease = db.lease(lease["id"])
    arrears, price = _arrears(lease["id"]), lease["price_pence"]
    buyer = 1000 - lease["pool_permille"]
    taken, value, written_off = pool.repossess(arrears, buyer, price)
    d = Decision(new_decision_id(), Proposal("settle_arrears", mid, arrears, None,
                                             "final payment failure: settle arrears in equity", ""),
                 "ALLOW", [ClauseResult("4.1", True, why),
                           ClauseResult("4.3", True, f"take {pct(taken)} = {money(value)} at locked price"),
                           ClauseResult("4.5", True, f"write off {money(written_off)}; savings untouched")],
                 f"Enforce arrears {money(arrears)} on {lease['id']}")
    ui.step("GATE", f"enforcement for arrears {money(arrears)} (deterministic, no LLM)")
    ui.gate(d.clauses)
    _save_decision(d, "invoice_failed", ev.invoice_id)
    new_pool = lease["pool_permille"] + taken
    db.update("leases", lease["id"], pool_permille=new_pool)
    ui.step("ACT", f"{first}'s equity {pct(buyer)} → {pct(buyer - taken)}: {pct(taken)} ({money(value)}) "
                   f"transferred to the pool")
    ledger.record("equity_repossessed", reason=f"{pct(taken)} equity worth {money(value)} taken against arrears "
                  f"{money(arrears)}, rounded down in member's favour", permille_delta=taken, member_id=mid,
                  lease_id=lease["id"], policy_clause="4.3", decision_id=d.id)

    # 4. in-kind distribution: capital credits, no cash
    split = pool.split_pro_rata(value, _weights_excluding(mid))
    for rid, share in split.items():
        if share <= 0:
            continue
        r = db.member(rid)
        db.update("members", rid, capital_pence=r["capital_pence"] + share)
        ledger.record("in_kind_distribution", reason=f"{_first(rid)} credited {money(share)} of savings "
                      f"(share of {first}'s repossessed equity, no cash)", member_id=rid, lease_id=lease["id"],
                      policy_clause="3.3", decision_id=d.id)
    ui.step("ACT", "in-kind split: " + " · ".join(f"{k} {money(v)}" for k, v in split.items() if v))

    # 5. non-recourse write-off
    if written_off > 0:
        ui.step("ACT", f"{money(written_off)} written off (non-recourse); {first}'s savings untouched")
        ledger.record("write_off", reason=f"arrears {money(written_off)} beyond {first}'s equity written off; "
                      f"savings never touched", member_id=mid, lease_id=lease["id"], policy_clause="4.5",
                      decision_id=d.id)

    # 6. close the invoice in Stripe
    reason = f"settled in equity: {pct(taken)} = {money(value)}, written off {money(written_off)}"
    billing.settle_invoice_with_equity(ev.invoice_id, metadata=_md(d.id, "4.3", reason, lease, settlement="equity"))
    db.mark_processed(f"inv:{ev.invoice_id}:paid")
    db.kv_set(f"arrears:{lease['id']}", 0)
    ui.step("ACT", f"invoice {ev.invoice_id} marked paid out of band ({reason})")

    # 7. §4.4 zero equity: end lease, revoke, relist
    revoked = False
    if buyer - taken == 0:
        reason = f"{name} has 0% equity: lease ended, access revoked, {lease['gpu_id']} re-leased"
        billing.cancel_subscription(lease["stripe_subscription_id"], metadata=_md(d.id, "4.4", reason, lease))
        code = gpu_client.revoke(lease["gpu_id"], reason)
        ui.step("ACT", f"rent subscription cancelled; GPU API revoke {lease['gpu_id']}: HTTP {code}")
        ledger.record("lease_closed", reason=reason, member_id=mid, lease_id=lease["id"],
                      stripe_object_id=lease["stripe_subscription_id"], policy_clause="4.4", decision_id=d.id)
        ledger.record("access_revoked", reason=f"{lease['gpu_id']} API key invalidated (HTTP {code})",
                      member_id=mid, lease_id=lease["id"], policy_clause="4.4", decision_id=d.id)
        ledger.record("relisted", reason=f"{lease['gpu_id']} owned 100% by the pool, available to the next member",
                      lease_id=lease["id"], policy_clause="4.4", decision_id=d.id)
        db.update("leases", lease["id"], status="repossessed", closed_at=_now())
        old_key = db.kv_get(f"gpu_key:{lease['gpu_id']}")
        status = gpu_client.workload(lease["gpu_id"], old_key, lease["stripe_customer_id"], 1)
        ui.step("OBSERVE", f"{first}'s old API key on {lease['gpu_id']} → [bold red]HTTP {status}[/]")
        revoked = True

    # 8. remember the whole decision
    blocked = hd is not None and hd.proposal.action == "payment_holiday" and not hd.allowed
    memory.remember(
        f"{TODAY.isoformat()}: {name}'s rent of {money(amt)} failed on the final attempt (§4.1). "
        + (f"His request to skip rent was BLOCKED under §4.2 because he broke his July promise to pay double "
           f"in August (decision {hd.id}). " if blocked else "")
        + f"Arrears settled in equity (§4.3): {pct(taken)} worth {money(value)} transferred to the pool and "
          f"credited in kind to the other members. {money(written_off)} written off, savings untouched (§4.5). "
        + (f"At 0% equity the lease ended, access to {lease['gpu_id']} was revoked and it was re-leased (§4.4). "
           if revoked else "")
        + f"Decision {d.id}.", dataset="decisions", member_id=mid)
    return d


# ---------------------------------------------------------------- Scene 6

def explain(question: str) -> str:
    member_id = next((m["id"] for m in db.members()
                      if m["id"] in question.lower() or m["name"].split()[0].lower() in question.lower()), None)
    ui.step("MEMORY", f"recall: {question}")
    passages = memory.recall(question, k=4)
    for p in passages:
        ui.step("MEMORY", f"[yellow]· {p[:220]}[/]")
    rows = list(reversed(ledger.recent(40, member_id=member_id)))
    clauses = policy.load()["clauses"]
    cited = {r["policy_clause"] for r in rows if r["policy_clause"]}
    return llm.explain(question, passages, rows, {k: v for k, v in clauses.items() if k in cited} or clauses)
