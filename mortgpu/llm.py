"""Claude proposer + explainer (§8 L4), with a deterministic fixture mode.

The LLM drafts; it never decides. policy.evaluate() decides.

    python -m mortgpu.llm --selftest
"""
import json
import re

from . import config, ui
from .models import Proposal

MEMBER_IDS = config.MEMBER_IDS

SYSTEM = f"""You read incoming documents and emails for mortGPU, a members' GPU savings pool, and draft one action.
You draft the action the sender is asking for. You do not decide. A policy engine decides.
Always respond by calling the propose_action tool exactly once, and nothing else.
Extract numbers exactly as written and convert them to pence (£1,000 -> 100000). Never calculate rent or ownership.
member_id must be one of: {", ".join(MEMBER_IDS)}. supplier_id must be lowercase, e.g. northwind.
Actions: open_lease (a signed lease-to-own contract: fill terms), buyback (member pays to buy back ownership:
amount_pence), payment_holiday (member asks to skip or defer rent), no_action (anything else).
rationale: one short line. evidence: a short verbatim quote from the source text."""

PROPOSE_TOOL = {
    "name": "propose_action",
    "description": "Draft the single action the sender is asking for, for the policy engine to allow or block.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "member_id", "amount_pence", "terms", "rationale", "evidence"],
        "properties": {
            "action": {"type": "string", "enum": ["open_lease", "buyback", "payment_holiday", "no_action"]},
            "member_id": {"type": "string", "enum": MEMBER_IDS},
            "amount_pence": {"type": ["integer", "null"]},
            "terms": {
                "anyOf": [
                    {"type": "null"},
                    {"type": "object", "additionalProperties": False,
                     "required": ["price_pence", "supplier_id", "gpu_model", "projected_revenue_pence"],
                     "properties": {"price_pence": {"type": "integer"}, "supplier_id": {"type": "string"},
                                    "gpu_model": {"type": "string"},
                                    "projected_revenue_pence": {"type": "integer"}}},
                ]
            },
            "rationale": {"type": "string"},
            "evidence": {"type": "string"},
        },
    },
}

_client = None


def _anthropic():
    global _client
    if _client is None:
        import anthropic
        if config.LLM_PROVIDER == "openrouter":
            _client = anthropic.Anthropic(base_url="https://openrouter.ai/api", api_key=config.OPENROUTER_API_KEY,
                                          timeout=60.0, max_retries=2)
        else:
            _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None, timeout=60.0, max_retries=2)
    return _client


def _create(**params):
    """messages.create with Claude-only extras (refusal fallbacks, effort, strict tools) only on Anthropic."""
    if config.LLM_PROVIDER == "anthropic":
        return _anthropic().beta.messages.create(
            **params, output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if "tools" in params:
        params["tools"] = [{k: v for k, v in t.items() if k != "strict"} for t in params["tools"]]
    return _anthropic().messages.create(**params)


def _live() -> bool:
    return config.LLM_MODE == "live"


# ---------------------------------------------------------------- fixture mode (regex parse)

def _pence(s: str) -> int:
    """'£1,000' / '£10,000.00' / '£1,200' -> pence."""
    return round(float(s.replace(",", "")) * 100)


def _money_after(pattern: str, text: str) -> int | None:
    m = re.search(pattern + r"[^£]{0,40}£\s*([\d,]+(?:\.\d{1,2})?)", text, re.I | re.S)
    return _pence(m.group(1)) if m else None


def _sender(text: str) -> str:
    m = re.search(r"From:\s*([A-Za-z]+)", text)
    if m and m.group(1).lower() in MEMBER_IDS:
        return m.group(1).lower()
    m = re.search(r"member\s*`?([a-z]+)`?", text, re.I)
    if m and m.group(1).lower() in MEMBER_IDS:
        return m.group(1).lower()
    m = re.search(r"—\s*([A-Za-z]+)\s*$", text.strip())
    if m and m.group(1).lower() in MEMBER_IDS:
        return m.group(1).lower()
    for mid in MEMBER_IDS:
        if re.search(rf"\b{mid}\b", text, re.I):
            return mid
    return "amara"


def _quote(text: str, *needles: str) -> str:
    for line in text.splitlines():
        if any(n.lower() in line.lower() for n in needles):
            return line.strip().strip("*").strip()
    return ""


def fixture_propose(trigger_kind: str, text: str) -> Proposal:
    member = _sender(text)
    low = text.lower()
    if trigger_kind == "contract" or "lease-to-own" in low:
        price = _money_after(r"locked price", text)
        projected = _money_after(r"projected revenue", text)
        sup = re.search(r"supplied by\s*\**([A-Za-z]+)", text, re.I)
        model = re.search(r"Asset:\**\s*\d+\s*×\s*(.+?)(?:,|$)", text, re.M)
        return Proposal("open_lease", member, None,
                        {"price_pence": price, "supplier_id": sup.group(1).lower() if sup else "",
                         "gpu_model": model.group(1).strip() if model else "GPU",
                         "projected_revenue_pence": projected},
                        "Signed lease-to-own contract for the top-of-queue member.",
                        _quote(text, "Projected revenue"))
    if re.search(r"\b(skip|holiday|defer|pay double)\b", low):
        return Proposal("payment_holiday", member, None, None,
                        f"{member.title()} asks to skip this month's rent.", _quote(text, "skip", "holiday"))
    if re.search(r"buy\s*back|final payment|last £", low):
        amt = _money_after(r"(?:buy\s*back|last|take)", text)
        return Proposal("buyback", member, amt, None,
                        f"{member.title()} asks to buy back ownership by card.", _quote(text, "£"))
    return Proposal("no_action", member, None, None, "No actionable request found.", "")


# ---------------------------------------------------------------- live mode

def _live_propose(trigger_kind: str, text: str, context: str) -> Proposal:
    resp = _create(
        model=config.LLM_MODEL,
        max_tokens=4000,
        system=SYSTEM,
        tools=[PROPOSE_TOOL],
        # forced tool_choice ("tool") is rejected on current Claude models; auto + strict schema + instruction
        tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        messages=[{"role": "user", "content":
                   f"Trigger: {trigger_kind}\n\nContext:\n{context}\n\nSource text:\n<<<\n{text}\n>>>"}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused")
    block = next((b for b in resp.content if b.type == "tool_use" and b.name == "propose_action"), None)
    if block is None:
        raise RuntimeError(f"no propose_action call (stop_reason={resp.stop_reason})")
    inp = block.input if isinstance(block.input, dict) else json.loads(block.input)
    terms = inp.get("terms")
    if terms:
        terms["supplier_id"] = str(terms.get("supplier_id", "")).lower()
    return Proposal(inp["action"], inp["member_id"], inp.get("amount_pence"), terms,
                    inp.get("rationale", ""), inp.get("evidence", ""))


def propose(trigger_kind: str, text: str, context: str) -> Proposal:
    if _live():
        try:
            return _live_propose(trigger_kind, text, context)
        except Exception as e:  # the demo must never die because of the API
            ui.warn(f"LLM unavailable ({type(e).__name__}: {str(e)[:120]}); fixture parse")
    return fixture_propose(trigger_kind, text)


# ---------------------------------------------------------------- explanations

EXPLAIN_KINDS = ["payment_failed", "holiday_blocked", "equity_repossessed", "write_off", "lease_closed",
                 "access_revoked", "title_transferred", "buyback", "rent_repriced"]


def _template_explain(question: str, passages: list[str], ledger_rows: list[dict], clauses: dict) -> str:
    """Deterministic fallback: the key ledger reasons, in story order, each citing its clause."""
    latest = {}
    for r in ledger_rows:                      # oldest first: keep the most recent row per kind
        latest[r["kind"]] = r
    parts = [f"{latest[k]['reason']} (§{latest[k]['policy_clause']})" for k in EXPLAIN_KINDS
             if k in latest and latest[k].get("policy_clause")]
    if not parts:
        return " ".join(passages[:2]) or "No record found."
    words = "; ".join(parts).split()
    return " ".join(words[:80]) + ("…" if len(words) > 80 else ".")


def explain(question: str, passages: list[str], ledger_rows: list[dict], clauses: dict) -> str:
    """≤ 80 words, cite clause numbers and amounts, plain English."""
    if _live():
        try:
            rows = [{k: r[k] for k in ("kind", "amount_pence", "permille_delta", "policy_clause", "reason")}
                    for r in ledger_rows]
            resp = _create(
                model=config.LLM_MODEL, max_tokens=2000,
                system="You explain decisions of a members' GPU savings pool. Answer in at most 80 words of plain "
                       "English. Cite clause numbers like §4.3 and exact £ amounts from the ledger. Use only the "
                       "evidence given. Ledger amounts are in pence (+ into the pool, − out of the pool).",
                messages=[{"role": "user", "content": json.dumps(
                    {"question": question, "memory_passages": passages, "ledger": rows, "clauses": clauses},
                    ensure_ascii=False)}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            if text and resp.stop_reason != "refusal":
                return text
        except Exception as e:
            ui.warn(f"LLM unavailable ({type(e).__name__}: {str(e)[:120]}); templated explanation")
    return _template_explain(question, passages, ledger_rows, clauses)


# ---------------------------------------------------------------- selftest

EXPECTED = {
    "contracts/amara.md": ("contract", Proposal("open_lease", "amara", None, {
        "price_pence": 1_000_000, "supplier_id": "northwind", "projected_revenue_pence": 120_000})),
    "inbox/01_amara_buyback.txt": ("email", Proposal("buyback", "amara", 100_000)),
    "inbox/02_carl_skip.txt": ("email", Proposal("payment_holiday", "carl", None)),
    "inbox/03_ben_final.txt": ("email", Proposal("buyback", "ben", 50_000)),
}


def _matches(got: Proposal, want: Proposal) -> bool:
    if (got.action, got.member_id) != (want.action, want.member_id):
        return False
    if want.amount_pence is not None and got.amount_pence != want.amount_pence:
        return False
    for k, v in (want.terms or {}).items():
        if (got.terms or {}).get(k) != v:
            return False
    return True


def _selftest() -> None:
    has_key = config.ANTHROPIC_API_KEY or config.OPENROUTER_API_KEY
    modes = ["fixture"] + (["live"] if has_key else [])
    if not has_key:
        print("SKIP live mode: neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set")
    else:
        print(f"live provider: {config.LLM_PROVIDER} · model {config.LLM_MODEL}")
    failed = False
    for mode in modes:
        config.LLM_MODE = mode
        for rel, (kind, want) in EXPECTED.items():
            text = (config.FIXTURES / rel).read_text()
            got = _live_propose(kind, text, "") if mode == "live" else fixture_propose(kind, text)
            ok = _matches(got, want)
            failed |= not ok
            print(f"{'PASS' if ok else 'FAIL'} [{mode}] {rel}: {got.action} {got.member_id} "
                  f"{got.amount_pence} {got.terms or ''}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
