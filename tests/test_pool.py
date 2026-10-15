"""§9 worked numbers."""
from datetime import date

from mortgpu import pool

TODAY = date(2026, 10, 15)
PRICE = 1_000_000

MEMBERS = [
    {"id": "amara", "capital_pence": 200_000, "credits_reset_at": None},
    {"id": "priya", "capital_pence": 1_000_000, "credits_reset_at": None},
    {"id": "tom", "capital_pence": 700_000, "credits_reset_at": None},
    {"id": "ben", "capital_pence": 120_000, "credits_reset_at": "2026-04-01"},
    {"id": "carl", "capital_pence": 50_000, "credits_reset_at": "2026-01-10"},
]
CONTRIBS = [
    {"member_id": "amara", "amount_pence": 200_000, "contributed_at": "2025-09-10"},
    {"member_id": "priya", "amount_pence": 1_000_000, "contributed_at": "2026-08-16"},
    {"member_id": "tom", "amount_pence": 700_000, "contributed_at": "2026-07-17"},
    {"member_id": "ben", "amount_pence": 120_000, "contributed_at": "2026-04-01"},
    {"member_id": "carl", "amount_pence": 50_000, "contributed_at": "2026-05-01"},
]


def lease(pool_permille, status="active"):
    return {"pool_permille": pool_permille, "price_pence": PRICE, "status": status}


def test_credits_and_queue():
    q = pool.queue(MEMBERS, CONTRIBS, TODAY, PRICE, leased={"ben", "carl"})
    assert [(r["member_id"], r["credits"]) for r in q] == [
        ("amara", 800_000), ("tom", 630_000), ("priya", 600_000), ("ben", 236_400), ("carl", 83_500)]
    assert q[0]["eligible"] and q[0]["savings_pence"] == 200_000
    assert not q[3]["eligible"] and not q[4]["eligible"]


def test_opening_invariant():
    assert sum(m["capital_pence"] for m in MEMBERS) == 2_070_000
    assert pool.invariant_ok(MEMBERS, [lease(50), lease(960)], 1_060_000)


def test_amara_converts_and_rent():
    assert pool.convert_savings(200_000, PRICE) == (200, 200_000)
    assert pool.rent_pence(PRICE, 800) == 40_000


def test_supplier_paid_invariant():
    members = [dict(m, capital_pence=0) if m["id"] == "amara" else m for m in MEMBERS]
    assert sum(m["capital_pence"] for m in members) == 1_870_000
    assert pool.invariant_ok(members, [lease(50), lease(960), lease(800)], 60_000)


def test_rent_split():
    split = pool.split_pro_rata(40_000, {"priya": 1_000_000, "tom": 700_000, "ben": 120_000, "carl": 50_000})
    assert split == {"priya": 21_390, "tom": 14_973, "ben": 2_567, "carl": 1_070}
    assert sum(split.values()) == 40_000


def test_split_zero_weights():
    assert pool.split_pro_rata(100, {"a": 0, "b": 3, "c": 0}) == {"a": 0, "b": 100, "c": 0}


def test_icr():
    assert 120_000 * 100 // 40_000 == 300 >= 125


def test_amara_buyback():
    d = pool.permille_for(100_000, PRICE)
    assert d == 100
    assert pool.rent_pence(PRICE, 800 - d) == 35_000


def test_carl_repossession_and_in_kind():
    assert pool.repossess(48_000, 40, PRICE) == (40, 40_000, 8_000)
    split = pool.split_pro_rata(40_000, {"priya": 1_000_000, "tom": 700_000, "ben": 120_000})
    assert split == {"priya": 21_978, "tom": 15_385, "ben": 2_637}


def test_ben_final():
    assert pool.permille_for(50_000, PRICE) == 50
    assert 50 - pool.permille_for(50_000, PRICE) == 0


def test_full_story_invariant():
    # after all scenes: amara pool 700‰, carl repossessed 1000‰, ben title transferred 0‰
    capital = 1_870_000 + 40_000           # + in-kind credits
    cash = 60_000 + 100_000 + 50_000      # + buyback + ben final (rent in == rent out)
    members = [{"capital_pence": capital}]
    leases = [lease(700), lease(1000, "repossessed"), lease(0, "title_transferred")]
    assert pool.invariant_ok(members, leases, cash)
