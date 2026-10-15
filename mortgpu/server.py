"""View + /api/state (+ optional /webhook, log only) (§8 L6).

    uvicorn mortgpu.server:app --port 8000
"""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from . import config, db, gpu_client, ledger, pool, ui

app = FastAPI(title="mortGPU")
STATIC = Path(__file__).parent / "static"


@app.get("/")
def view():
    return FileResponse(STATIC / "view.html")


@app.get("/theme.css")
def theme():
    return FileResponse(config.ROOT / "docs" / "theme.css", media_type="text/css")


def _gpus() -> list:
    try:
        return [{k: v for k, v in g.items() if k != "api_key"} for g in gpu_client.status()]
    except Exception:
        return []


@app.get("/api/state")
def state():
    db.init()
    members, leases, contribs = db.members(), db.leases(), db.contributions()
    active = {l["member_id"] for l in leases if l["status"] == "active"}
    queue = pool.queue(members, contribs, config.DEMO_TODAY, config.ASSET_PRICE_PENCE, leased=active)
    credits = {q["member_id"]: q for q in queue}
    gpus = {g["id"]: g for g in _gpus()}
    decisions = db.query("SELECT * FROM decisions ORDER BY ts DESC, rowid DESC LIMIT 10")
    for d in decisions:
        d["clauses"] = json.loads(d.pop("clauses_json") or "[]")
        d["proposal"] = json.loads(d.pop("proposal_json") or "{}")
    cash = db.cash()
    return {
        "today": config.DEMO_TODAY.isoformat(),
        "members": [{**m, "credits": credits.get(m["id"], {}).get("credits", 0),
                     "eligible": credits.get(m["id"], {}).get("eligible", False),
                     "capital": ui.money(m["capital_pence"])} for m in members],
        "queue": queue,
        "leases": [{**l, "buyer_permille": 1000 - l["pool_permille"], "pool_pct": ui.pct(l["pool_permille"]),
                    "buyer_pct": ui.pct(1000 - l["pool_permille"]),
                    "rent_pence": pool.rent_pence(l["price_pence"], l["pool_permille"]) if l["status"] == "active" else 0,
                    "title_holder": gpus.get(l["gpu_id"], {}).get("title_holder"),
                    "gpu_status": gpus.get(l["gpu_id"], {}).get("status")} for l in leases],
        "gpus": list(gpus.values()),
        "cash_pence": cash,
        "invariant_ok": pool.invariant_ok(members, leases, cash),
        "ledger": ledger.recent(25),
        "decisions": decisions,
    }


@app.post("/webhook")
async def webhook(request: Request):
    """Verify and log only: the demo processes invoices via agent.sync() so nothing happens twice."""
    payload = await request.body()
    if not config.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(400, "STRIPE_WEBHOOK_SECRET not set")
    import stripe
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("stripe-signature", ""),
                                               config.STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(400, f"bad signature: {e}")
    print(f"webhook {event['type']} {event['data']['object'].get('id')}")
    return Response(status_code=204)
