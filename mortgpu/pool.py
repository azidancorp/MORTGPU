"""Pure ROSCA / ownership maths (§8 L3). Integer pence and permille only; no I/O."""
from datetime import date


def _d(s) -> date:
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def credits(contributions: list[dict], reset_at: str | None, today: date) -> int:
    """Σ (amount_pence // 100) × days held, counting from max(contributed_at, reset_at) to today."""
    total = 0
    for c in contributions:
        start = _d(c["contributed_at"])
        if reset_at:
            start = max(start, _d(reset_at))
        total += (c["amount_pence"] // 100) * max(0, (today - start).days)
    return total


def queue(members: list[dict], contributions: list[dict], today: date, price_pence: int,
          leased: set[str] | None = None) -> list[dict]:
    """Credits leaderboard. eligible = savings ≥ 20% of price and no active lease.

    `leased` is the set of member ids with an active lease; members may also carry a truthy
    `has_active_lease` key.
    """
    leased = set(leased or ())
    min_deposit = price_pence * 200 // 1000
    out = []
    for m in members:
        mine = [c for c in contributions if c["member_id"] == m["id"]]
        savings = m["capital_pence"]
        has_lease = m["id"] in leased or bool(m.get("has_active_lease"))
        if has_lease:
            eligible, why = False, "has an active lease"
        elif savings < min_deposit:
            eligible, why = False, f"savings below 20% of price ({savings // 100:,} < {min_deposit // 100:,} £)"
        else:
            eligible, why = True, "eligible"
        out.append({"member_id": m["id"], "credits": credits(mine, m.get("credits_reset_at"), today),
                    "savings_pence": savings, "eligible": eligible, "why": why})
    return sorted(out, key=lambda r: (-r["credits"], r["member_id"]))


def rent_pence(price_pence: int, pool_permille: int, rate_bps: int = 500) -> int:
    """price × rate_bps/10000 × pool_permille/1000."""
    return price_pence * rate_bps * pool_permille // 10_000_000


def permille_for(amount_pence: int, price_pence: int) -> int:
    return amount_pence * 1000 // price_pence


def convert_savings(capital_pence: int, price_pence: int) -> tuple[int, int]:
    """(buyer_permille, capital_used): floor to whole permille, cap at 1000."""
    permille = min(1000, permille_for(capital_pence, price_pence))
    return permille, permille * price_pence // 1000


def repossess(arrears_pence: int, buyer_permille: int, price_pence: int) -> tuple[int, int, int]:
    """(permille_taken, value_taken_pence, written_off_pence). Rounded down in the member's favour."""
    taken = min(buyer_permille, arrears_pence * 1000 // price_pence)
    value = taken * price_pence // 1000
    return taken, value, arrears_pence - value


def split_pro_rata(amount_pence: int, weights: dict[str, int]) -> dict[str, int]:
    """Largest-remainder split; sums exactly to amount; zero weights get 0."""
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0:
        return {k: 0 for k in weights}
    shares, rems = {}, []
    for k, w in weights.items():
        w = max(0, w)
        q, r = divmod(amount_pence * w, total)
        shares[k] = q
        if w:
            rems.append((r, k))
    left = amount_pence - sum(shares.values())
    for _, k in sorted(rems, key=lambda x: (-x[0], x[1]))[:left]:
        shares[k] += 1
    return shares


def pool_stake_pence(leases: list[dict]) -> int:
    """Value of the pool's ownership across GPUs it still holds a stake in.

    Repossessed GPUs stay pool-owned (1000‰, relisted), so they count too; title-transferred
    leases have pool_permille 0.
    """
    return sum(l["pool_permille"] * l["price_pence"] // 1000
               for l in leases if l.get("status") in ("active", "repossessed"))


def invariant_ok(members, leases, cash_pence) -> bool:
    """Σ capital == cash + Σ pool stakes."""
    return sum(m["capital_pence"] for m in members) == cash_pence + pool_stake_pence(leases)
