"""Mock GPU rack (§8 L2). State persists to gpu_state.json; usage appends to usage.log.

    uvicorn mortgpu.gpu:app --port 8001
    python -m mortgpu.gpu --selftest      # needs the server running
"""
import json
import secrets
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import config

app = FastAPI(title="mortGPU mock rack")
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(config.GPU_STATE.read_text())["gpus"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return []


def _save(gpus: list[dict]) -> None:
    config.GPU_STATE.write_text(json.dumps({"gpus": gpus}, indent=2))


def _find(gpus: list[dict], gpu_id: str) -> dict:
    for g in gpus:
        if g["id"] == gpu_id:
            return g
    raise HTTPException(404, f"unknown gpu {gpu_id}")


class Seed(BaseModel):
    gpus: list[dict]


class Provision(BaseModel):
    model: str
    user_member_id: str


class Workload(BaseModel):
    gpu_id: str
    api_key: str
    stripe_customer_id: str
    minutes: int


class Revoke(BaseModel):
    gpu_id: str
    by: str = "pool"
    reason: str = ""


class TransferTitle(BaseModel):
    gpu_id: str
    new_holder: str
    by: str = "pool"


@app.post("/seed")
def seed(body: Seed):
    with _lock:
        gpus = [{"id": g["id"], "model": g.get("model", ""), "title_holder": g.get("title_holder", "pool"),
                 "user_member_id": g.get("user_member_id"), "api_key": g.get("api_key") or _key(),
                 "status": g.get("status", "active")} for g in body.gpus]
        _save(gpus)
        if config.USAGE_LOG.exists():
            config.USAGE_LOG.unlink()
    return {"gpus": gpus}


def _key() -> str:
    return "gk_" + secrets.token_hex(12)


@app.post("/provision")
def provision(body: Provision):
    with _lock:
        gpus = _load()
        used = {g["id"] for g in gpus}
        n = next(i for i in range(1, len(gpus) + 2) if f"GPU-{i:03d}" not in used)   # lowest free slot
        gpu = {"id": f"GPU-{n:03d}", "model": body.model, "title_holder": "pool",
               "user_member_id": body.user_member_id, "api_key": _key(), "status": "active"}
        gpus.append(gpu)
        _save(gpus)
    return gpu


@app.post("/workload")
def workload(body: Workload):
    with _lock:
        gpu = _find(_load(), body.gpu_id)
        if gpu["status"] != "active" or not secrets.compare_digest(gpu["api_key"] or "", body.api_key):
            raise HTTPException(403, "access revoked or bad api key")
        line = {"id": "u_" + secrets.token_hex(6), "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "gpu_id": body.gpu_id, "stripe_customer_id": body.stripe_customer_id, "minutes": body.minutes}
        with open(config.USAGE_LOG, "a") as f:
            f.write(json.dumps(line) + "\n")
    return line


@app.post("/revoke")
def revoke(body: Revoke):
    with _lock:
        gpus = _load()
        gpu = _find(gpus, body.gpu_id)
        if gpu["title_holder"] != body.by:
            raise HTTPException(409, f"{body.by} no longer holds title (title holder: {gpu['title_holder']})")
        gpu.update(api_key=None, status="available", user_member_id=None)
        _save(gpus)
    return gpu


@app.post("/transfer_title")
def transfer_title(body: TransferTitle):
    with _lock:
        gpus = _load()
        gpu = _find(gpus, body.gpu_id)
        if gpu["title_holder"] != body.by:
            raise HTTPException(409, f"{body.by} does not hold title (title holder: {gpu['title_holder']})")
        gpu["title_holder"] = body.new_holder
        _save(gpus)
    return gpu


@app.get("/status")
def status():
    return _load()


def _selftest() -> None:
    from . import gpu_client as gc

    def check(name, ok):
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            raise SystemExit(1)

    gc.seed([{"id": "GPU-001", "model": "RTX PRO 6000", "title_holder": "pool", "user_member_id": "ben",
              "status": "active"}])
    g = gc.provision("RTX PRO 6000", "amara")
    check(f"provision -> {g['id']}", g["status"] == "active" and g["title_holder"] == "pool")
    check("workload 200", gc.workload(g["id"], g["api_key"], "cus_test", 200) == 200)
    lines = [json.loads(l) for l in config.USAGE_LOG.read_text().splitlines()]
    check("line in usage.log", lines[-1]["gpu_id"] == g["id"] and lines[-1]["minutes"] == 200)
    check("revoke 200", gc.revoke(g["id"], "test") == 200)
    check("workload after revoke 403", gc.workload(g["id"], g["api_key"], "cus_test", 1) == 403)
    check("transfer_title GPU-001 -> ben", gc.transfer_title("GPU-001", "ben") == 200)
    check("revoke GPU-001 409", gc.revoke("GPU-001", "test") == 409)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
