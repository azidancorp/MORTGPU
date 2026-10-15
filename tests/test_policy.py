"""Policy gate: ALLOW paths from §9 plus the BLOCK cases from §10."""
from datetime import date

import pytest

from mortgpu import policy, pool
from mortgpu.models import Proposal
from tests.test_pool import CONTRIBS, MEMBERS, PRICE

POLICY = policy.load()


def lease_ctx(member_id="amara", capital=200_000, cash=1_060_000, members=MEMBERS):
    members = [dict(m, capital_pence=capital) if m["id"] == member_id else m for m in members]
    return {"queue": pool.queue(members, CONTRIBS, date(2026, 10, 15), PRICE, leased={"ben", "carl"}),
            "member": next(m for m in members if m["id"] == member_id), "lease": None,
            "cash_pence": cash, "facts": {"holidays_12m": 0, "broken_promises": 0},
            "arrears_pence": 0, "policy": POLICY}


def open_lease(member="amara", supplier="northwind", projected=120_000, price=PRICE):
    return Proposal("open_lease", member, terms={"price_pence": price, "supplier_id": supplier,
                    "gpu_model": "RTX PRO 6000", "projected_revenue_pence": projected})


def failed(d):
    return {c.clause for c in d.clauses if not c.passed}


def test_open_lease_allow():
    d = policy.evaluate(open_lease(), lease_ctx())
    assert d.verdict == "ALLOW", d.reason
    assert [c.clause for c in d.clauses] == ["1.1", "2.1", "2.2", "2.3", "2.4", "2.5"]
    assert d.id.startswith("D-") and len(d.id) == 8


def test_deposit_19_9_blocked():
    d = policy.evaluate(open_lease(), lease_ctx(capital=199_000))
    assert d.verdict == "BLOCK" and "2.1" in failed(d)


def test_unknown_supplier_blocked():
    d = policy.evaluate(open_lease(supplier="shadyco"), lease_ctx())
    assert d.verdict == "BLOCK" and failed(d) == {"2.3"}


def test_icr_124_blocked():
    d = policy.evaluate(open_lease(projected=49_600), lease_ctx())   # 124% of £400
    assert d.verdict == "BLOCK" and failed(d) == {"2.4"}
    assert policy.evaluate(open_lease(projected=50_000), lease_ctx()).verdict == "ALLOW"


def test_price_and_cash_blocked():
    assert "2.2" in failed(policy.evaluate(open_lease(price=1_000_100), lease_ctx()))
    assert failed(policy.evaluate(open_lease(), lease_ctx(cash=999_999))) == {"2.5"}


def test_not_top_member_blocked():
    d = policy.evaluate(open_lease(member="tom"), lease_ctx(member_id="tom", capital=700_000))
    assert d.verdict == "BLOCK" and "1.1" in failed(d)


def holiday_ctx(holidays, broken):
    return {"facts": {"holidays_12m": holidays, "broken_promises": broken}, "policy": POLICY}


def test_holiday():
    p = Proposal("payment_holiday", "carl")
    assert policy.evaluate(p, holiday_ctx(0, 0)).verdict == "ALLOW"
    d = policy.evaluate(p, holiday_ctx(1, 1))
    assert d.verdict == "BLOCK" and failed(d) == {"4.2"}
    assert policy.evaluate(p, holiday_ctx(0, 1)).verdict == "BLOCK"   # broken promise alone blocks


def buyback_ctx(pool_permille, arrears=0):
    return {"lease": {"pool_permille": pool_permille, "price_pence": PRICE, "status": "active"},
            "arrears_pence": arrears, "policy": POLICY}


@pytest.mark.parametrize("amount,permille,arrears,verdict", [
    (100_000, 800, 0, "ALLOW"),     # Amara £1,000
    (50_000, 50, 0, "ALLOW"),       # Ben final £500 = remaining
    (49_900, 800, 0, "BLOCK"),      # £499
    (30_000, 30, 0, "ALLOW"),       # below £500 but exactly the remaining balance
    (100_500, 800, 0, "BLOCK"),     # not a multiple of £10
    (60_000, 50, 0, "BLOCK"),       # more than remaining
    (100_000, 800, 40_000, "BLOCK"),  # arrears outstanding
])
def test_buyback(amount, permille, arrears, verdict):
    d = policy.evaluate(Proposal("buyback", "amara", amount_pence=amount), buyback_ctx(permille, arrears))
    assert d.verdict == verdict, d.reason
    assert [c.clause for c in d.clauses] == ["5.2"]
