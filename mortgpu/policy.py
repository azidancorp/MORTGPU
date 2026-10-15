"""Deterministic policy gate (§7, §8 L3). Every verdict cites the clauses it checked."""
import json

from . import config, pool
from .models import ClauseResult, Decision, Proposal, new_decision_id
from .ui import money, pct


def load() -> dict:
    with open(config.POLICY_PATH) as f:
        return json.load(f)


def _open_lease(p: Proposal, ctx: dict, P: dict) -> list[ClauseResult]:
    terms = p.terms or {}
    price = int(terms.get("price_pence") or 0)
    supplier = str(terms.get("supplier_id") or "").lower()
    projected = int(terms.get("projected_revenue_pence") or 0)
    member = ctx.get("member") or {}
    capital = int(member.get("capital_pence") or 0)
    eligible = [q for q in ctx.get("queue", []) if q["eligible"]]
    top = eligible[0]["member_id"] if eligible else None
    deposit_permille, deposit = pool.convert_savings(capital, price) if price else (0, 0)
    rent = pool.rent_pence(price, 1000 - deposit_permille, P["rent_rate_bps"])
    need = rent * P["icr_min_pct"] // 100
    cash = int(ctx.get("cash_pence") or 0)
    return [
        ClauseResult("1.1", top == p.member_id,
                     f"{p.member_id} is the top eligible member" if top == p.member_id
                     else f"top eligible member is {top or 'nobody'}, not {p.member_id}"),
        ClauseResult("2.1", deposit_permille >= P["min_deposit_permille"],
                     f"deposit {money(deposit)} = {pct(deposit_permille)} "
                     f"{'≥' if deposit_permille >= P['min_deposit_permille'] else '<'} "
                     f"{pct(P['min_deposit_permille'])}"),
        ClauseResult("2.2", 0 < price <= P["max_price_pence"],
                     f"price {money(price)} {'≤' if price <= P['max_price_pence'] else '>'} "
                     f"{money(P['max_price_pence'])}"),
        ClauseResult("2.3", supplier in P["supplier_allowlist"],
                     f"supplier '{supplier}' {'is' if supplier in P['supplier_allowlist'] else 'is not'} "
                     f"on the allowlist"),
        ClauseResult("2.4", projected >= need,
                     f"projected {money(projected)} {'≥' if projected >= need else '<'} "
                     f"{P['icr_min_pct']}% × {money(rent)}"),
        ClauseResult("2.5", cash >= price,
                     f"pool cash {money(cash)} {'≥' if cash >= price else '<'} price {money(price)}"),
    ]


def _buyback(p: Proposal, ctx: dict, P: dict) -> list[ClauseResult]:
    lease = ctx.get("lease")
    amt = int(p.amount_pence or 0)
    if not lease or lease.get("status") != "active":
        return [ClauseResult("5.2", False, f"{p.member_id} has no active lease")]
    remaining = lease["pool_permille"] * lease["price_pence"] // 1000
    arrears = int(ctx.get("arrears_pence") or 0)
    problems = []
    if amt <= 0 or amt % P["buyback_step_pence"]:
        problems.append(f"{money(amt)} is not a multiple of {money(P['buyback_step_pence'])}")
    if amt < P["min_buyback_pence"] and amt != remaining:
        problems.append(f"{money(amt)} < minimum {money(P['min_buyback_pence'])}")
    if amt > remaining:
        problems.append(f"{money(amt)} > remaining {money(remaining)}")
    if arrears:
        problems.append(f"arrears {money(arrears)} outstanding")
    if problems:
        return [ClauseResult("5.2", False, "; ".join(problems))]
    return [ClauseResult("5.2", True, f"{money(amt)} at locked price, multiple of "
                         f"{money(P['buyback_step_pence'])}, ≤ remaining {money(remaining)}, no arrears")]


def _holiday(p: Proposal, ctx: dict, P: dict) -> list[ClauseResult]:
    f = ctx.get("facts") or {}
    used, broken = int(f.get("holidays_12m", 0)), int(f.get("broken_promises", 0))
    ok = used < P["max_holidays_per_12m"] and broken == 0
    return [ClauseResult("4.2", ok, f"holidays in last 12m: {used}/{P['max_holidays_per_12m']}; "
                         f"broken promises: {broken}")]


CHECKS = {"open_lease": _open_lease, "buyback": _buyback, "payment_holiday": _holiday}


def evaluate(p: Proposal, ctx: dict) -> Decision:
    P = (ctx.get("policy") or load())["params"]
    check = CHECKS.get(p.action)
    if check is None:
        return Decision(new_decision_id(), p, "BLOCK" if p.action != "no_action" else "ALLOW", [],
                        "no action proposed" if p.action == "no_action" else f"unknown action '{p.action}'")
    clauses = check(p, ctx, P)
    failed = [c for c in clauses if not c.passed]
    if failed:
        reason = "Blocked by " + "; ".join(f"§{c.clause}: {c.detail}" for c in failed)
        return Decision(new_decision_id(), p, "BLOCK", clauses, reason)
    return Decision(new_decision_id(), p, "ALLOW", clauses,
                    "Allowed: " + ", ".join(f"§{c.clause}" for c in clauses) + " pass")
