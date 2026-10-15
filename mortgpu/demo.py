"""Scene runner (§8 L7).

    python -m mortgpu.demo run [--pause] [--from N] [--speed 0.25]
    python -m mortgpu.demo reset
"""
import argparse

from . import agent, billing, config, db, gpu_client, memory, pool, ui
from .ui import money, pct


def _lease(lease_id: str) -> dict:
    return db.lease(lease_id)


def _cus(member_id: str) -> str:
    return db.member(member_id)["stripe_customer_id"]


def scene1() -> None:
    ui.scene(1, "Queue → contract → supplier paid → GPU provisioned → rent → rent split")
    leased = {l["member_id"] for l in db.leases() if l["status"] == "active"}
    q = pool.queue(db.members(), db.contributions(), config.DEMO_TODAY, config.ASSET_PRICE_PENCE, leased=leased)
    ui.table("Queue · credits = savings × days held", ["#", "member", "credits", "savings", "eligible"],
             [[i + 1, r["member_id"], f"{r['credits']:,}", money(r["savings_pence"]),
               "[green]✓[/]" if r["eligible"] else f"[red]✗[/] {r['why']}"] for i, r in enumerate(q)])
    agent.handle_contract(str(config.FIXTURES / "contracts" / "amara.md"))
    agent.sync([_cus("amara")])


def scene2() -> None:
    ui.scene(2, "Inference usage → meter events → +1 month → invoices paid → ICR")
    lease = _lease("L-AMARA")
    key, nova = db.kv_get(f"gpu_key:{lease['gpu_id']}"), db.kv_get("nova_customer")
    ui.step("OBSERVE", f"Nova Labs runs 30 inference jobs × 1,000 GPU-minutes on {lease['gpu_id']}")
    codes = [gpu_client.workload(lease["gpu_id"], key, nova, 1000) for _ in range(30)]
    ui.step("OBSERVE", f"GPU API: {codes.count(200)}/30 jobs accepted")
    agent.process_usage()
    ui.step("ACT", "advancing Stripe test clock A one month (renewal + finalize)…")
    billing.advance_clock_past_renewal(db.kv_get("CLOCK_A"), lease["stripe_subscription_id"])
    agent.sync([nova, _cus("amara")])


def scene3() -> None:
    ui.scene(3, "Amara buys back £1,000 → rent falls")
    agent.handle_email(str(config.FIXTURES / "inbox" / "01_amara_buyback.txt"))


def scene4() -> None:
    ui.scene(4, "Carl's rent fails → asks to skip → BLOCKED → equity taken → revoked")
    lease = _lease("L-CARL")
    rent = pool.rent_pence(lease["price_pence"], lease["pool_permille"])
    ui.info(f"Carl: {pct(1000 - lease['pool_permille'])} equity, rent {money(rent)}")
    ui.step("ACT", "advancing Stripe test clock B one month (Carl's card now declines)…")
    billing.advance_clock_past_renewal(db.kv_get("CLOCK_B"), lease["stripe_subscription_id"])
    agent.sync([_cus("carl")])


def scene5() -> None:
    ui.scene(5, "Ben's final £500 → 100% → title transferred")
    agent.handle_email(str(config.FIXTURES / "inbox" / "03_ben_final.txt"))
    code = gpu_client.revoke("GPU-002", "pool tries to revoke Ben's GPU after title transfer")
    ui.step("OBSERVE", f"pool tries to revoke GPU-002 → [bold]HTTP {code}[/] "
                       f"{'(pool no longer holds title)' if code == 409 else ''}")


def scene6() -> None:
    ui.scene(6, "“Why was Carl disconnected?”")
    ui.step("MEMORY", "flushing queued Cognee writes…")
    memory.flush()
    answer = agent.explain("Why was Carl disconnected?")
    ui.console.print(ui.Panel(answer, title="answer", border_style="green", expand=False))
    ok = pool.invariant_ok(db.members(), db.leases(), db.cash())
    capital = sum(m["capital_pence"] for m in db.members())
    ui.info(f"Σ savings {money(capital)} = cash {money(db.cash())} + pool stakes "
            f"{money(pool.pool_stake_pence(db.leases()))}")
    ui.console.print("[bold green]books balance ✓" if ok else "[bold red]books DO NOT balance ✗")
    if not ok:
        raise SystemExit(1)


SCENES = [scene1, scene2, scene3, scene4, scene5, scene6]


def run(pause: bool, start: int, speed: float) -> None:
    ui.PAUSE, ui.SPEED = pause, speed
    for n, fn in enumerate(SCENES, start=1):
        if n < start:
            continue
        fn()
        if n < len(SCENES):
            ui.pause()


def reset() -> None:
    import stripe
    for key in ("CLOCK_A", "CLOCK_B"):
        clock = db.kv_get(key) if db.query("SELECT name FROM sqlite_master WHERE name='kv'") else None
        if clock:
            try:
                stripe.test_helpers.TestClock.delete(clock)
                ui.step("ACT", f"deleted test clock {clock}")
            except stripe.StripeError as e:
                ui.warn(f"could not delete {clock}: {e}")
    db.init(wipe=True)
    ui.step("ACT", "database wiped")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m mortgpu.demo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--pause", action="store_true")
    r.add_argument("--from", dest="start", type=int, default=1)
    r.add_argument("--speed", type=float, default=0.0)
    sub.add_parser("reset")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.pause, a.start, a.speed)
    else:
        reset()


if __name__ == "__main__":
    main()
