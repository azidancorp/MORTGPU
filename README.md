# MORTGPU

**mortGPU is a ROSCA for GPUs**, a rotating savings club. Members save into a community pool and earn credits for patience (savings × days held). The member with the most credits gets the next GPU, and their savings become their deposit. An agent buys the GPU from the pool's supplier, provisions it and runs the lease:

- **Rent** is 5% of the locked price per month, times the pool's ownership share. It falls as the member buys back.
- The member resells inference to their own customers. Usage is billed per GPU-minute through Stripe meter events.
- Rent is distributed to the other members, pro rata to their savings.
- Missed rent is settled in equity. At 0% equity the member is disconnected and the pool owns the GPU.
- At 100% equity, title transfers to the member.

Each action follows the same path. An LLM **proposes** it, a deterministic policy engine (`policy.json`) **allows or blocks** it and cites the clause, and then it is **executed** in Stripe test mode and/or the GPU API. The result is **remembered** in Cognee and **written** to an audit ledger. `SPEC.md` is the full contract.

## Run

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env            # fill in STRIPE_SECRET_KEY, connected accounts, an LLM key
# warm Cognee's local models once (first use downloads GLiNER + embeddings)
python -c "import asyncio,cognee; asyncio.run(cognee.remember('warmup', dataset_name='warmup'))"

uvicorn mortgpu.gpu:app --port 8001          # mock GPU rack
uvicorn mortgpu.server:app --port 8000       # live view at http://localhost:8000
python -m mortgpu.setup                      # seed Stripe + DB + memory (run before every recording)
python -m mortgpu.demo run --pause           # the 6 scenes; Enter advances (--from N, --speed 0.25)
python -m mortgpu.demo reset                 # delete test clocks, wipe the DB
```

**LLM:** if `ANTHROPIC_API_KEY` is set, Claude is used directly (`LLM_MODEL`, default `claude-sonnet-5-5`). Otherwise `OPENROUTER_API_KEY` is used through OpenRouter's Anthropic-compatible endpoint (default `deepseek/deepseek-v4.1-flash`).

## The demo (6 scenes)

| # | Scene | Proves |
|---|---|---|
| 1 | Queue → Amara's contract → supplier paid → GPU-001 provisioned → rent £400 → rent split | contract → bill, spend under policy |
| 2 | Nova Labs runs 30,000 GPU-minutes → meter events → clock +1 month → invoices paid → ICR 300% | usage → bill |
| 3 | Email "buy back £1,000" → charge → rent £400 → £350 | diminishing rent |
| 4 | Carl's rent fails → "can I skip?" → **BLOCKED** (broken promise recalled) → last 4% equity taken → £80 written off → revoked, old key gets 403 | control + memory + enforcement |
| 5 | Ben's "final £500" → 100% → title transferred; the pool's revoke now gets 409 | payoff |
| 6 | "Why was Carl disconnected?" → answer from memory citing clauses; books balance ✓ | explains every pound |

## Layout

| File | Role |
|---|---|
| `mortgpu/config.py` `db.py` `ledger.py` `models.py` `ui.py` | constants/env, SQLite schema + helpers, audit ledger, dataclasses, rich console |
| `mortgpu/billing.py` | every Stripe call (catalog, clocks, customers, rent/usage subscriptions, meters, transfers) |
| `mortgpu/gpu.py` `gpu_client.py` | FastAPI mock GPU rack (`gpu_state.json`, `usage.log`) and its client |
| `mortgpu/pool.py` `policy.py` `policy.json` | pure ROSCA/ownership maths; the deterministic gate |
| `mortgpu/llm.py` `agent.py` | proposer/explainer (+ fixture mode); observe → propose → gate → act → remember → audit |
| `mortgpu/memory.py` | Cognee wrapper, structured `promises` mirror for the gate, keyword fallback |
| `mortgpu/observer.py` `server.py` `static/view.html` | invoice sync with dedupe; live view + `/api/state` (+ log-only `/webhook`) |
| `mortgpu/setup.py` `demo.py` `fixtures/` | seed everything; scene runner; members, contract, inbox, Carl's history |

The gate never reads Cognee. Money decisions use `memory.facts()` from the structured `promises` table. Cognee supplies the evidence the agent shows and the explanations it gives.

## Tests and self-tests

```bash
pytest tests/                                   # §9 worked numbers, BLOCK cases, full 6-scene story vs a fake Stripe
python -m mortgpu.billing --selftest            # live Stripe test mode
python -m mortgpu.gpu --selftest                # needs the rack on :8001
python -m mortgpu.llm --selftest                # fixture + live (if a key is set)
python -m mortgpu.memory --selftest             # also with COGNEE_ENABLED=0
E2E_LLM_MODE=live pytest -s tests/test_demo_offline.py   # full story with the live LLM
```

## Fallback switches

| Problem | Switch | Effect |
|---|---|---|
| Connect transfers fail | `CONNECT_MODE=ledger` | ledger rows only; scenes unchanged |
| Cognee slow or broken | `COGNEE_ENABLED=0` | keyword recall over `memories`; scenes 4 and 6 still work |
| LLM API down | `LLM_MODE=fixture` | deterministic proposals and a templated explanation (also automatic on any API error) |
| Retries can't be disabled | `DEMO_FIRST_FAILURE_IS_FINAL=1` | first failure treated as final; the ledger reason says so |

## Notes

- Money is integer pence and ownership is integer permille, everywhere.
- The books invariant is Σ savings = pool cash + Σ pool stakes. Stakes include repossessed GPUs, which stay pool-owned and are relisted.
- Live mode doesn't force `tool_choice` to one tool, because current Claude models reject that. Instead, the proposer uses `tool_choice: auto`, a strict tool schema and an explicit instruction to call `propose_action` once.
