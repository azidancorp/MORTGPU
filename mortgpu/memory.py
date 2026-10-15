"""Cognee wrapper + structured mirror + keyword fallback (§8 L5).

The gate never reads Cognee: it reads facts() from the promises table. Cognee supplies the
passages the agent shows as evidence and uses in explanations.

    python -m mortgpu.memory --selftest
"""
import asyncio
import os
import re
import threading
import time
import warnings
from datetime import date, datetime, timedelta, timezone

from . import config, db, ui

os.environ.setdefault("LOG_LEVEL", "ERROR")          # keep cognee's structlog quiet on camera
os.environ.setdefault("TELEMETRY_DISABLED", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")   # GLiNER "model of type gliner2" notice
warnings.filterwarnings("ignore", category=UserWarning, module=r"gliner2\..*")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"torch\..*")

_loop: asyncio.AbstractEventLoop | None = None
_ready = threading.Event()
_start_lock = threading.Lock()
_pending: list = []                                  # concurrent.futures.Future of queued writes
_cognee = None
_cognee_error: str | None = None
DATASETS = ["policy", "promises", "decisions"]


# ---------------------------------------------------------------- background loop (cognee is async)

def _run_loop() -> None:
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _ready.set()
    _loop.run_forever()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    with _start_lock:
        if _loop is None:
            threading.Thread(target=_run_loop, name="cognee", daemon=True).start()
            _ready.wait()
    return _loop


async def _get_cognee():
    global _cognee, _cognee_error
    if _cognee is None and _cognee_error is None:
        try:
            import logging
            import cognee
            for name in ("cognee", "httpx", "LiteLLM", "transformers"):
                logging.getLogger(name).setLevel(logging.ERROR)
            cognee.config.system_root_directory(str(config.ROOT / ".cognee_system"))
            cognee.config.data_root_directory(str(config.ROOT / ".data_storage"))
            _cognee = cognee
        except Exception as e:  # import/config failure -> fallback for the rest of the run
            _cognee_error = str(e)
            ui.warn(f"cognee unavailable, using keyword recall: {e}")
    return _cognee


_write_lock: asyncio.Lock | None = None
_buffer: dict[str, list[str]] = {}                   # dataset -> texts awaiting a cognee write


async def _remember(dataset: str) -> None:
    global _write_lock
    cognee = await _get_cognee()
    if cognee is None:
        return
    _write_lock = _write_lock or asyncio.Lock()
    async with _write_lock:          # one cognify pipeline at a time; batch whatever queued meanwhile
        texts = _buffer.pop(dataset, [])
        if texts:
            await cognee.remember(texts, dataset_name=dataset)


# ---------------------------------------------------------------- public API

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def remember(text: str, *, dataset: str, member_id: str | None = None) -> None:
    db.insert("memories", ts=_now(), dataset=dataset, member_id=member_id, text=text)
    if not config.COGNEE_ENABLED:
        return
    loop = _ensure_loop()
    loop.call_soon_threadsafe(lambda: _buffer.setdefault(dataset, []).append(text))
    fut = asyncio.run_coroutine_threadsafe(_remember(dataset), loop)
    _pending.append(fut)


def record_promise(member_id: str, kind: str, text: str, due_at: str, decision_id: str,
                   *, made_at: str | None = None, status: str = "open") -> int:
    return db.insert("promises", member_id=member_id, kind=kind, made_at=made_at or _now(), due_at=due_at,
                     text=text, status=status, decision_id=decision_id)


def mark_promise(promise_id: int, status: str) -> None:
    db.update("promises", promise_id, status=status)


def facts(member_id: str, today: date = config.DEMO_TODAY) -> dict:
    since = (today - timedelta(days=365)).isoformat()
    rows = db.query("SELECT kind, made_at, status FROM promises WHERE member_id=?", (member_id,))
    return {
        "holidays_12m": sum(1 for r in rows if r["kind"] == "holiday" and (r["made_at"] or "")[:10] >= since),
        "broken_promises": sum(1 for r in rows if r["status"] == "broken"),
    }


STOP = set("a an the and or of to in on for is was were be been has have had do did does his her their "
           "he she it they i you we my me why what when how who whom which this that with at by from as "
           "any past ever".split())


def _tokens(s: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", s.lower()):
        if w in STOP:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def _keyword_recall(question: str, datasets: list[str] | None, k: int) -> list[str]:
    q = _tokens(question)
    rows = db.query("SELECT * FROM memories ORDER BY id")
    if datasets:
        rows = [r for r in rows if r["dataset"] in datasets]
    scored = []
    for r in rows:
        overlap = len(q & _tokens(r["text"]))
        if overlap:
            scored.append((overlap, r["id"], r["text"]))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [t for _, _, t in scored[:k]]


async def _cognee_recall(question: str, datasets: list[str] | None, k: int) -> list[str]:
    cognee = await _get_cognee()
    if cognee is None:
        return []
    res = await cognee.recall(question, datasets=datasets, top_k=k, only_context=True)
    out = []
    for r in res or []:
        text = getattr(r, "text", None) or (r.get("text") if isinstance(r, dict) else None) or str(r)
        if text and text not in out:
            out.append(text.strip())
    return out[:k]


def recall(question: str, datasets: list[str] | None = None, k: int = 4) -> list[str]:
    datasets = datasets or DATASETS          # never surface warmup/selftest data
    if config.COGNEE_ENABLED:
        try:
            fut = asyncio.run_coroutine_threadsafe(_cognee_recall(question, datasets, k), _ensure_loop())
            passages = fut.result(timeout=45)
            if passages:
                return passages
        except Exception as e:
            ui.warn(f"cognee recall failed ({type(e).__name__}: {e}); keyword fallback")
    return _keyword_recall(question, datasets, k)


async def _forget(datasets: list[str]) -> None:
    cognee = await _get_cognee()
    if cognee is None:
        return
    for name in datasets:
        try:
            await cognee.forget(dataset=name)
        except Exception:
            pass                      # dataset didn't exist yet


def reset(datasets: list[str] = DATASETS) -> None:
    """Forget our Cognee datasets so a fresh setup doesn't recall the previous rehearsal."""
    if config.COGNEE_ENABLED:
        try:
            asyncio.run_coroutine_threadsafe(_forget(datasets), _ensure_loop()).result(timeout=120)
        except Exception as e:
            ui.warn(f"cognee reset failed ({type(e).__name__}: {e})")


def flush(timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    for fut in list(_pending):
        try:
            fut.result(timeout=max(0.1, deadline - time.time()))
        except Exception as e:
            ui.warn(f"cognee write did not finish ({type(e).__name__}: {e})")
        _pending.remove(fut)


# ---------------------------------------------------------------- selftest

def _selftest() -> None:
    import tempfile
    config.DB_PATH = os.path.join(tempfile.mkdtemp(), "memory-selftest.db")
    db.init(wipe=True)

    def ok(name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
        if not cond:
            raise SystemExit(1)

    print(f"COGNEE_ENABLED={int(config.COGNEE_ENABLED)}")
    remember("July 2026: Carl was granted a payment holiday (§4.2). Carl promised to pay double rent in August.",
             dataset="selftest", member_id="carl")
    remember("August 2026: Carl did not pay double rent. Carl's promise is broken. Equity repossessed (§4.3).",
             dataset="selftest", member_id="carl")
    pid = record_promise("carl", "holiday", "pay double in August", "2026-08-31", "D-000000",
                         made_at="2026-07-01")
    mark_promise(pid, "broken")
    t0 = time.time()
    flush(timeout_s=600)
    ok("flush", True, f"{time.time() - t0:.1f}s")
    passages = recall("Did Carl keep his promise?", datasets=["selftest"])
    for p in passages:
        print("   ·", p[:120])
    ok('recall contains "broken"', any("broken" in p for p in passages))
    f = facts("carl", config.DEMO_TODAY)
    ok("facts(carl) = 1 holiday, 1 broken", f == {"holidays_12m": 1, "broken_promises": 1}, str(f))


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
