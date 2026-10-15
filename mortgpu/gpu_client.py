"""httpx client for the mock GPU rack (mortgpu.gpu)."""
import httpx

from . import config


def _post(path: str, body: dict) -> httpx.Response:
    return httpx.post(config.GPU_API_URL + path, json=body, timeout=10)


def seed(gpus: list[dict]) -> list[dict]:
    r = _post("/seed", {"gpus": gpus})
    r.raise_for_status()
    return r.json()["gpus"]


def provision(model: str, member_id: str) -> dict:
    r = _post("/provision", {"model": model, "user_member_id": member_id})
    r.raise_for_status()
    return r.json()


def workload(gpu_id: str, api_key: str, cus_id: str, minutes: int) -> int:
    return _post("/workload", {"gpu_id": gpu_id, "api_key": api_key or "",
                               "stripe_customer_id": cus_id, "minutes": minutes}).status_code


def revoke(gpu_id: str, reason: str, by: str = "pool") -> int:
    return _post("/revoke", {"gpu_id": gpu_id, "by": by, "reason": reason}).status_code


def transfer_title(gpu_id: str, new_holder: str, by: str = "pool") -> int:
    return _post("/transfer_title", {"gpu_id": gpu_id, "new_holder": new_holder, "by": by}).status_code


def status() -> list:
    r = httpx.get(config.GPU_API_URL + "/status", timeout=10)
    r.raise_for_status()
    return r.json()


def get(gpu_id: str) -> dict | None:
    return next((g for g in status() if g["id"] == gpu_id), None)
