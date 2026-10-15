# mortGPU — Code Plan & Build Spec

> **For every coding agent:** this file is the contract. Read it fully, build **only your lane's files**, implement the interfaces **exactly as written**, and run your lane's acceptance test before reporting done. Do **not** run git commands. The integrator commits. If you need a change outside your lane, write it in `NOTES-<lane>.md` and stop.

---

## 1. What we're building

mortGPU is **a ROSCA for GPUs** (a rotating savings club). Members save into a community pool and earn credits for patience. The member with the most credits gets the next GPU, and their savings convert into their deposit (initial equity). An agent buys the GPU from the pool's supplier, provisions it, and runs the lease:

- **Rent** = 5%/month × locked price × pool's ownership share. It falls as the member buys back.
- **Member's income:** the member rents the GPU's inference capacity to their own customers, billed per GPU-minute through Stripe meter events.
- **Rent distribution:** rent is distributed to the other members, pro rata to their savings.
- **Missed rent:** settled in equity. At 0% equity the member is disconnected and the pool owns the GPU.
- **Title:** at 100% equity the title transfers to the member.

Every action is **proposed by an LLM** and **allowed or blocked by a deterministic policy engine** citing a clause, then executed in Stripe test mode and/or the GPU API, remembered in Cognee, and written to an audit ledger.

**The demo story (6 scenes, driven by `demo.py`):**

| # | Scene | Who | Proves |
|---|---|---|---|
| 1 | Queue → contract → supplier paid → GPU provisioned → rent subscription → rent split | Amara | contract → bill, spend under policy |
| 2 | Inference usage → meter events → clock +1 month → invoices paid → ICR check | Amara + Nova Labs | usage → bill |
| 3 | Email "buy back £1,000" → charge → rent £400 → £350 | Amara | diminishing rent |
| 4 | Rent fails → "can I skip?" → **BLOCKED** (broken promise recalled) → last 4% equity taken → £80 written off → revoked | Carl | control + memory + enforcement |
| 5 | Email "final £500" → charge → 100% → title transferred | Ben | payoff |
| 6 | "Why was Carl disconnected?" → answer from memory, citing clauses | — | explains every pound |

---

## 2. Stack & running

- **Python 3.12** (installed), **uv** (installed).
- Libraries: `stripe`, `anthropic`, `cognee==1.6.4`, `fastapi`, `uvicorn`, `httpx`, `rich`, `python-dotenv`, `pytest`.
- SQLite file `mortgpu.db` (shared state); `gpu_state.json` (owned by the mock GPU service); `usage.log` (written by the GPU service, read by the agent).

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
# warm Cognee's local models NOW (first use downloads GLiNER + embeddings)
python -c "import asyncio,cognee; asyncio.run(cognee.remember('warmup', dataset_name='warmup'))"

# terminals
uvicorn mortgpu.gpu:app --port 8001          # mock GPU rack
uvicorn mortgpu.server:app --port 8000       # view + /api/state (+ optional /webhook)
python -m mortgpu.setup                      # seed Stripe + DB (run before every recording)
python -m mortgpu.demo run --pause           # the 6 scenes; Enter advances
```

---

## 3. Repo layout & lane ownership

```
mortgpu/__init__.py      L0
mortgpu/config.py        L0  constants + env settings
mortgpu/db.py            L0  schema + helpers
mortgpu/ledger.py        L0  record()/recent()
mortgpu/models.py        L0  Proposal, ClauseResult, Decision dataclasses
mortgpu/ui.py            L0  rich console helpers used by everyone
mortgpu/billing.py       L1  all Stripe calls
mortgpu/gpu.py           L2  FastAPI mock GPU rack
mortgpu/gpu_client.py    L2  httpx client for gpu.py
mortgpu/pool.py          L3  pure ROSCA/ownership maths
mortgpu/policy.py        L3  deterministic gate
policy.json              L3
tests/test_pool.py       L3
tests/test_policy.py     L3
mortgpu/llm.py           L4  Claude proposer + explainer (+ fixture mode)
mortgpu/agent.py         L4  orchestration: observe → propose → gate → act → remember → audit
mortgpu/memory.py        L5  Cognee wrapper + structured mirror + fallback
mortgpu/observer.py      L6  invoice sync (pull) with dedupe
mortgpu/server.py        L6  FastAPI: view, /api/state, /webhook
mortgpu/static/view.html L6  live tables (use docs/theme.css)
mortgpu/setup.py         L7  seed everything
mortgpu/demo.py          L7  scene runner
fixtures/**              L7
README.md                L7
requirements.txt .env.example .gitignore   L0
```

**Build order:** L0 first (integrator, ~15 min: real `config/db/ledger/models/ui` + **stub files for every other module with the exact signatures below, raising `NotImplementedError`**). Then spawn L1–L7 in parallel.

---

## 4. Config (`config.py`, `.env`)

```python
DEMO_TODAY = date(2026, 10, 15)       # credits are computed as of this date (deterministic)
ASSET_PRICE_PENCE = 1_000_000         # £10,000 locked price
RENT_RATE_BPS = 500                   # 5% per month
RENT_UNIT_PENCE = 50                  # rent price: £0.50 per 0.1% (permille) of pool ownership
USAGE_UNIT_PENCE = 4                  # £0.04 per GPU-minute (£2.40/hr)
CURRENCY = "gbp"
```

`.env` (never committed):

```
STRIPE_SECRET_KEY=sk_test_...
ANTHROPIC_API_KEY=...
LLM_MODEL=claude-sonnet-5-5
LLM_MODE=live                 # live | fixture   (fixture = deterministic parse, no API)
COGNEE_ENABLED=1              # 0 = keyword fallback over the memories table
CONNECT_MODE=connect          # connect | ledger (ledger = no Stripe transfers, ledger rows only)
DEMO_FIRST_FAILURE_IS_FINAL=1 # treat first failed attempt as final (retries off for demo)
SUPPLIER_ACCOUNT_ID=acct_...  # Stripe connected account for Northwind Compute
MEMBER_ACCOUNTS={"priya":"acct_...","tom":"acct_...","ben":"acct_...","carl":"acct_...","amara":"acct_..."}
GPU_API_URL=http://localhost:8001
DB_PATH=mortgpu.db
```

**Do not set `LLM_API_KEY`.** Cognee would then default to OpenAI. We run Cognee with no key (local GLiNER + local embeddings). Its `recall` returns passages, and Claude writes the answers.

---

## 5. Units & conventions (everyone)

- **Money is integer pence.** Never floats. Display with `ui.money(pence)` → `£400.00`.
- **Ownership is integer permille** (0–1000). `pool_permille + buyer_permille == 1000`.
- **Rent quantity** on Stripe = `pool_permille` → `pool_permille × 50p` (800 → £400.00).
- Ledger amount sign: **+ into the pool, − out of the pool**. Member-revenue rows (Nova → Amara) use `kind="member_revenue"` and do not touch pool cash.
- Every Stripe object we create or modify gets `metadata = {"decision_id", "policy_clause", "reason"[:500], "lease_id", "member_id"}` (omit keys that don't apply).
- Timestamps are ISO strings (UTC) in the DB.

---

## 6. Data model (`db.py`, L0)

```sql
CREATE TABLE members(
  id TEXT PRIMARY KEY,               -- 'amara','priya','tom','ben','carl'
  name TEXT, email TEXT,
  capital_pence INTEGER NOT NULL,    -- savings = claim on the pool
  credits_reset_at TEXT,             -- NULL = never taken a turn
  stripe_customer_id TEXT,
  connect_account_id TEXT
);
CREATE TABLE contributions(
  id INTEGER PRIMARY KEY, member_id TEXT, amount_pence INTEGER,
  contributed_at TEXT, stripe_payment_intent_id TEXT
);
CREATE TABLE leases(
  id TEXT PRIMARY KEY,               -- 'L-AMARA', 'L-BEN', 'L-CARL'
  member_id TEXT, gpu_id TEXT, price_pence INTEGER,
  pool_permille INTEGER,             -- buyer_permille = 1000 - pool_permille
  status TEXT,                       -- active | title_transferred | repossessed
  stripe_customer_id TEXT, stripe_subscription_id TEXT, stripe_subscription_item_id TEXT,
  clock_id TEXT, opened_at TEXT, closed_at TEXT
);
CREATE TABLE pool(id INTEGER PRIMARY KEY CHECK(id=1), cash_pence INTEGER NOT NULL);
CREATE TABLE ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, decision_id TEXT,
  member_id TEXT, lease_id TEXT, kind TEXT,
  amount_pence INTEGER DEFAULT 0, permille_delta INTEGER DEFAULT 0,
  stripe_object_id TEXT, policy_clause TEXT, reason TEXT
);
CREATE TABLE decisions(
  id TEXT PRIMARY KEY, ts TEXT, trigger_kind TEXT, trigger_ref TEXT,
  action TEXT, member_id TEXT, proposal_json TEXT,
  verdict TEXT,                      -- ALLOW | BLOCK
  clauses_json TEXT, reason TEXT
);
CREATE TABLE promises(               -- structured mirror of memory, used by the gate
  id INTEGER PRIMARY KEY, member_id TEXT, kind TEXT,   -- 'holiday'
  made_at TEXT, due_at TEXT, text TEXT,
  status TEXT,                       -- open | kept | broken
  decision_id TEXT
);
CREATE TABLE memories(id INTEGER PRIMARY KEY, ts TEXT, dataset TEXT, member_id TEXT, text TEXT);
CREATE TABLE processed(key TEXT PRIMARY KEY);   -- dedupe: 'inv:<id>:<status>', 'usage:<line id>'
CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT); -- price/meter/clock ids, usage.log offset
```

**Ledger kinds:** `opening_balance, contribution, savings_to_equity, supplier_payment, provision, rent_subscription, rent_paid, rent_distribution, usage_metered, member_revenue, icr_check, buyback, rent_repriced, payment_failed, notice_sent, holiday_blocked, holiday_granted, equity_repossessed, in_kind_distribution, write_off, lease_closed, access_revoked, relisted, title_transferred, history`.

`db.py` helpers: `connect()`, `init(wipe=False)`, `kv_get(k)`, `kv_set(k,v)`, `member(id)`, `members()`, `lease(id)`, `lease_by_customer(cus)`, `update(table, id, **cols)`, `cash()`, `add_cash(delta)`, `mark_processed(key) -> bool` (False if already processed).

`ledger.py`: `record(kind, *, reason, amount_pence=0, member_id=None, lease_id=None, permille_delta=0, stripe_object_id=None, policy_clause=None, decision_id=None) -> int` (also prints an `AUDIT` line via `ui`); `recent(n=30, member_id=None) -> list[dict]`.

`models.py`:

```python
@dataclass
class Proposal:
    action: str            # open_lease | buyback | payment_holiday | no_action
    member_id: str
    amount_pence: int | None = None
    terms: dict | None = None   # open_lease: price_pence, supplier_id, gpu_model, projected_revenue_pence
    rationale: str = ""         # LLM's one-line why
    evidence: str = ""          # quote from the source text

@dataclass
class ClauseResult:
    clause: str            # '2.4'
    passed: bool
    detail: str            # 'projected £1,200.00 ≥ 125% × £400.00'

@dataclass
class Decision:
    id: str                # 'D-' + 6 hex
    proposal: Proposal
    verdict: str           # ALLOW | BLOCK
    clauses: list[ClauseResult]
    reason: str
```

`ui.py`: `scene(n, title)`, `step(kind, text)` with kinds `OBSERVE PROPOSE MEMORY GATE ACT AUDIT` (GATE prints each clause ✓ green / ✗ red), `verdict(decision)`, `money(pence)`, `pct(permille)` → `80.0%`, `pause()` (Enter, only with `--pause`), and a global `SPEED` delay so lines are readable on video.

---

## 7. Policy (`policy.json`, L3)

```json
{
  "version": "2026-10-15",
  "params": {
    "max_price_pence": 1000000, "rent_rate_bps": 500, "min_deposit_permille": 200,
    "icr_min_pct": 125, "max_holidays_per_12m": 1, "min_buyback_pence": 50000,
    "buyback_step_pence": 1000, "supplier_allowlist": ["northwind"]
  },
  "clauses": {
    "1.1": "The next GPU goes to the eligible member with the most credits (savings × days held). Eligible means savings of at least 20% of the asset price. If underwriting fails, the turn passes to the next member.",
    "1.2": "On their turn, a member's savings convert into their deposit and initial equity at the locked price.",
    "1.3": "Credits reset to zero when a member takes their turn.",
    "2.1": "The deposit (converted savings) must be at least 20% of the asset price.",
    "2.2": "The asset price must not exceed £10,000.",
    "2.3": "The supplier must be on the allowlist.",
    "2.4": "Projected monthly inference revenue must be at least 125% of the initial monthly rent (ICR).",
    "2.5": "The pool must hold enough cash to pay the supplier in full.",
    "3.1": "Rent is 5% of the locked asset price per month, multiplied by the pool's ownership share.",
    "3.2": "Each month, metered inference revenue is compared with rent. An ICR below 125% is flagged.",
    "3.3": "Rent is distributed to members pro rata to their savings, excluding the lessee of that asset.",
    "4.1": "Enforcement starts only after the final failed payment attempt.",
    "4.2": "A member may take at most one payment holiday per 12 months, and none after a broken promise.",
    "4.3": "Unpaid rent is settled by transferring the member's equity to the pool at the locked price, rounded down in the member's favour.",
    "4.4": "When the member's equity reaches zero, the lease ends, access is revoked and the GPU is re-leased.",
    "4.5": "Non-recourse: arrears beyond the member's equity are written off. Savings are never touched.",
    "5.1": "Title transfers to the member at 100% equity with zero arrears.",
    "5.2": "Buybacks are voluntary, at the locked price, in multiples of £10, at least £500 (or the remaining balance), with no arrears outstanding."
  }
}
```

---

## 8. Module interfaces

### L1 — `billing.py` (Stripe; module-level `stripe.*` API, `stripe.api_key` from config)

```python
def setup_catalog() -> dict
    # Product "GPU lease rent" + Price(unit_amount=50, gbp, recurring month, licensed)
    # Meter(display_name="GPU minutes", event_name="gpu_minutes", default_aggregation={"formula":"sum"},
    #       customer_mapping={"event_payload_key":"stripe_customer_id","type":"by_id"},
    #       value_settings={"event_payload_key":"value"})
    # Product "Inference GPU-minutes" + Price(unit_amount=4, gbp, recurring month, usage_type="metered", meter=meter.id)
    # store ids in kv: rent_price, usage_price, meter_id
def create_clock(name: str) -> str                      # frozen_time = int(time.time())
def create_customer(member_id: str, name: str, email: str, *, clock_id: str | None = None,
                    card: str = "good") -> tuple[str, str]   # (cus_id, pm_id)
def set_card(cus_id: str, card: str, *, subscription_id: str | None = None) -> str
    # card: "good" -> pm_card_bypassPending (funds go straight to available balance;
    #        if that token errors on a UK account, try pm_card_bypassPendingInternational, else pm_card_visa)
    #       "fail" -> pm_card_chargeCustomerFail (attaches fine, declines on charge)
    # PaymentMethod.attach(token, customer=) -> new pm; set invoice_settings.default_payment_method
    # and the subscription's default_payment_method if given
def charge(cus_id: str, amount_pence: int, *, description: str, metadata: dict) -> str
    # PaymentIntent: payment_method=default pm, payment_method_types=["card"], off_session=True, confirm=True
def create_rent_subscription(cus_id: str, pool_permille: int, *, metadata: dict) -> tuple[str, str, str]
    # (sub_id, item_id, latest_invoice_id); quantity=pool_permille; payment_behavior="error_if_incomplete"
    # first invoice is charged immediately (= this month's rent)
def set_rent_quantity(sub_id: str, item_id: str, pool_permille: int, *, metadata: dict) -> None
    # SubscriptionItem.modify(quantity=..., proration_behavior="none"); Subscription.modify(metadata=...)
def preview_next_rent(cus_id: str, sub_id: str) -> int     # Invoice.create_preview(...).total
def create_usage_subscription(cus_id: str, *, destination_account: str | None = None) -> str
    # items=[{"price": usage_price}]; if CONNECT_MODE=connect and destination given: transfer_data={"destination": ...}
def fire_meter_event(cus_id: str, minutes: int, identifier: str) -> None
    # billing.MeterEvent.create(event_name="gpu_minutes", identifier=identifier,
    #   payload={"stripe_customer_id": cus_id, "value": str(minutes)})   # value = whole-number string
def wait_for_meter(cus_id: str, expected_total: int, timeout_s: int = 30) -> bool
    # poll billing.Meter.list_event_summaries(meter_id, customer=, start_time=, end_time=) (minute-aligned);
    # if the summaries API is awkward, sleep 15s and return True
def advance_clock_past_renewal(clock_id: str, sub_id: str) -> None
    # period end = sub["items"]["data"][0]["current_period_end"] (newer API) or sub["current_period_end"]
    # advance to period_end + 2h (renewal + the ~1h draft->finalize window), poll TestClock.retrieve until status=="ready"
    # if the renewal invoice is still draft, advance another 2h
def list_invoices(cus_id: str) -> list                      # Invoice.list(customer=cus_id) (includes clock invoices)
def settle_invoice_with_equity(invoice_id: str, *, metadata: dict) -> None
    # Invoice.modify(metadata=...); Invoice.pay(paid_out_of_band=True)
def cancel_subscription(sub_id: str, *, metadata: dict) -> None
def transfer(account_id: str | None, amount_pence: int, *, description: str, metadata: dict) -> str | None
    # CONNECT_MODE=ledger or no account -> return None. On any Stripe error: print a warning, return None (never raise)
def balance_available() -> int                              # gbp available, pence
```

**Gotchas:**
- **Test clocks:** at most **3 customers per clock**. A monthly subscription can be advanced at most 2 months per step. Rapid updates to a clocked subscription can hit rate limits; if that happens, advance the clock a few minutes between updates.
- **Customer list:** `Customer.list` hides clocked customers unless you pass `test_clock=`. Always store ids in the DB.
- **Meter events:** send them with real-time timestamps, **before** advancing the clock (the clock starts at real "now", so they fall in the current period). Events are aggregated asynchronously, so call `wait_for_meter` first.
- **Newer API versions** moved `invoice.subscription` and `invoice.charge`. **Classify invoices by customer** (lessee vs Nova), not by those fields.

### L2 — `gpu.py` (FastAPI, state persisted to `gpu_state.json`) + `gpu_client.py`

GPU record: `{id, model, title_holder, user_member_id, api_key, status}` with status `active | revoked | available`.

| Endpoint | Body | Behaviour |
|---|---|---|
| `POST /seed` | `{gpus:[record...]}` | replace state (setup only) |
| `POST /provision` | `{model, user_member_id}` | new id `GPU-00n`, `title_holder="pool"`, new `api_key`, `status="active"` |
| `POST /workload` | `{gpu_id, api_key, stripe_customer_id, minutes}` | 403 unless active and the key matches; append JSON line `{id:"u_<hex>", ts, gpu_id, stripe_customer_id, minutes}` to `usage.log` |
| `POST /revoke` | `{gpu_id, by, reason}` | **409 if `title_holder != by`** ("pool no longer holds title"); else invalidate key, `status="available"` (re-listed), `user_member_id=None` |
| `POST /transfer_title` | `{gpu_id, new_holder, by}` | 409 if `title_holder != by`; set `title_holder=new_holder` |
| `GET /status` | — | all GPUs |

`gpu_client.py`: `seed(gpus)`, `provision(model, member_id) -> dict`, `workload(gpu_id, api_key, cus_id, minutes) -> int` (HTTP status), `revoke(gpu_id, reason, by="pool") -> int`, `transfer_title(gpu_id, new_holder, by="pool") -> int`, `status() -> list`.

### L3 — `pool.py` (pure functions, no I/O) + `policy.py`

```python
def credits(contributions: list[dict], reset_at: str | None, today: date) -> int
    # Σ (amount_pence // 100) × days held, counting days from max(contributed_at, reset_at) to today
def queue(members: list[dict], contributions: list[dict], today: date, price_pence: int) -> list[dict]
    # sorted by credits desc: {member_id, credits, savings_pence, eligible, why}
    # eligible = savings ≥ 20% of price and no active lease
def rent_pence(price_pence: int, pool_permille: int, rate_bps: int = 500) -> int
    # price × rate_bps/10000 × pool_permille/1000 (exact for our numbers; use integer maths)
def permille_for(amount_pence: int, price_pence: int) -> int     # amount × 1000 // price
def convert_savings(capital_pence: int, price_pence: int) -> tuple[int, int]
    # (buyer_permille, capital_used) — floor to whole permille, cap at 1000
def repossess(arrears_pence: int, buyer_permille: int, price_pence: int) -> tuple[int, int, int]
    # (permille_taken, value_taken_pence, written_off_pence)
    # taken = min(buyer_permille, arrears*1000 // price); value = taken*price//1000; written_off = arrears - value
def split_pro_rata(amount_pence: int, weights: dict[str, int]) -> dict[str, int]
    # largest-remainder method; result sums EXACTLY to amount; zero weights get 0
def invariant_ok(members, leases, cash_pence) -> bool
    # Σ capital == cash + Σ(lease.pool_permille × price // 1000 for active leases)
```

`policy.py`:

```python
def load() -> dict
def evaluate(p: Proposal, ctx: dict) -> Decision
```

`ctx` (built by the agent): `{"queue": [...], "member": {...}, "lease": {...}|None, "cash_pence": int, "facts": {"holidays_12m": int, "broken_promises": int}, "arrears_pence": int, "policy": dict}`.

Checks per action (BLOCK if any clause fails; always return every checked clause):
- `open_lease`: 1.1 (member is the top *eligible* entry), 2.1, 2.2, 2.3, 2.4 (`projected ≥ 125% × rent_pence(price, 1000 − deposit_permille)`), 2.5.
- `buyback`: 5.2 (multiple of £10; ≥ £500, or exactly the remaining balance `pool_permille × price // 1000`; ≤ remaining; arrears == 0).
- `payment_holiday`: 4.2 (`holidays_12m < 1 and broken_promises == 0`).

### L4 — `llm.py` + `agent.py`

`llm.py` (Anthropic SDK, `LLM_MODEL`):

```python
def propose(trigger_kind: str, text: str, context: str) -> Proposal
    # Force a single tool call: tools=[PROPOSE_TOOL], tool_choice={"type":"tool","name":"propose_action"}
def explain(question: str, passages: list[str], ledger_rows: list[dict], clauses: dict) -> str
    # ≤ 80 words, cite clause numbers and amounts, plain English
```

`PROPOSE_TOOL.input_schema`: `{action: enum[open_lease, buyback, payment_holiday, no_action], member_id: str, amount_pence: int|null, terms: {price_pence, supplier_id, gpu_model, projected_revenue_pence}|null, rationale: str, evidence: str}`.

System prompt essentials:
- "You draft the action the sender is asking for. You do not decide. A policy engine decides."
- "Extract numbers exactly as written and convert them to pence. Never calculate rent or ownership."
- "member_id must be one of: amara, priya, tom, ben, carl. supplier_id must be lowercase, e.g. northwind."

**`LLM_MODE=fixture`:** regex/keyword parse of the 4 fixture files, returning the same Proposals. The demo must never die because of the API.

`agent.py`: every handler prints via `ui` and follows **observe → propose → (memory) → gate → act → remember → audit**. Decision ids go into the ledger rows and Stripe metadata.

```python
def handle_contract(path: str) -> Decision            # Scene 1
def handle_email(path: str) -> Decision               # Scenes 3, 4 (reply), 5
def process_usage() -> int                            # Scene 2: tail usage.log from kv offset -> meter events; returns minutes
def sync(customer_ids: list[str]) -> list[Decision]   # observer -> on_invoice_paid / on_invoice_failed
def on_invoice_paid(ev) -> None
def on_invoice_failed(ev) -> None                     # Scene 4 (calls handle_email on the member's inbox reply)
def explain(question: str) -> str                     # Scene 6
```

**Flows (exact order):**

**open_lease (ALLOW)**
1. `pool.convert_savings` → member capital −used, `credits_reset_at=today` → ledger `savings_to_equity` (§1.2, §1.3).
2. `billing.transfer(supplier, price)` and `db.add_cash(-price)` → ledger `supplier_payment` (−, §2.3).
3. `gpu_client.provision` → ledger `provision`.
4. `billing.create_rent_subscription(qty=pool_permille)` → insert lease → ledger `rent_subscription` (§3.1).
5. `memory.remember(...)`.

**on_invoice_paid**
- **Lessee invoice (rent):**
  1. `add_cash(+amt)` → ledger `rent_paid` (+, §3.1).
  2. `split_pro_rata(amt, capital weights excluding the lessee)` → `billing.transfer` each → `add_cash(-share)` → ledger `rent_distribution` (−, §3.3).
- **Nova invoice (usage):**
  1. Ledger `member_revenue` for Amara (no cash change).
  2. ICR = revenue / current rent → ledger `icr_check` (§3.2): ✓ or ⚠.

**buyback (ALLOW)**
1. `billing.charge` → `add_cash(+amt)` → ledger `buyback` (+, permille −Δ, §5.2).
2. `pool_permille -= Δ`.
3. If `pool_permille > 0`: `billing.set_rent_quantity` → ledger `rent_repriced` → print `preview_next_rent`.
4. If `pool_permille == 0`: §5.1, `billing.cancel_subscription` → `gpu_client.transfer_title(gpu, member)` → ledger `title_transferred` → status `title_transferred`.

**on_invoice_failed**
1. §4.1: final if `next_payment_attempt is None` or `DEMO_FIRST_FAILURE_IS_FINAL` → ledger `payment_failed` → ledger `notice_sent` (print the notice).
2. If an inbox reply exists for the member (`fixtures/inbox/*<member>*`): `handle_email`. For a payment-holiday proposal:
   - show `memory.recall("Has <name> kept past payment promises?")` passages as **MEMORY**;
   - gate §4.2 uses `memory.facts(member)`;
   - BLOCK → ledger `holiday_blocked`. ALLOW → ledger `holiday_granted` + `memory.record_promise(...)`, then stop.
3. §4.3: `pool.repossess(arrears, buyer_permille, price)` → `pool_permille += taken` → ledger `equity_repossessed` (permille +taken).
4. `split_pro_rata(value_taken, capital weights excl. lessee)` → **capital credits, no cash** → ledger `in_kind_distribution`.
5. If written_off > 0 → ledger `write_off` (§4.5).
6. `billing.settle_invoice_with_equity`.
7. If buyer equity == 0 (§4.4):
   - `billing.cancel_subscription` → `gpu_client.revoke(gpu)` → ledger `lease_closed`, `access_revoked`, `relisted`;
   - status `repossessed`;
   - demo proof: `gpu_client.workload(old key)` → print `403`.
8. `memory.remember(narrative of the whole decision)`.

### L5 — `memory.py`

```python
def remember(text: str, *, dataset: str, member_id: str | None = None) -> None
    # insert into memories table immediately; queue cognee.remember(text, dataset_name=dataset)
    # on a background thread that owns an asyncio loop (cognee is async). Never block the demo for >1s.
def record_promise(member_id: str, kind: str, text: str, due_at: str, decision_id: str) -> int
def mark_promise(promise_id: int, status: str) -> None
def facts(member_id: str, today: date) -> dict      # {"holidays_12m": n, "broken_promises": n} from promises table
def recall(question: str, datasets: list[str] | None = None, k: int = 4) -> list[str]
    # COGNEE_ENABLED: await cognee.recall(question, datasets=datasets) -> passage strings
    # fallback / on error / empty: keyword overlap over the memories table
def flush(timeout_s: int = 60) -> None               # wait for queued cognee writes (call before Scene 6)
```

Datasets: `policy` (one memory per clause), `promises`, `decisions`. **The gate never reads Cognee directly.** It reads `facts()` from the structured mirror, so a money decision never depends on fuzzy retrieval. Cognee supplies the evidence the agent shows and the explanations it gives.

### L6 — `observer.py` + `server.py` + `static/view.html`

```python
@dataclass
class InvoiceEvent:
    kind: str           # paid | failed
    invoice_id: str; customer_id: str; amount_pence: int
    attempt_count: int; next_payment_attempt: int | None

def sync_invoices(customer_ids: list[str]) -> list[InvoiceEvent]
    # billing.list_invoices per customer; status "paid" -> paid; status "open" and attempt_count > 0 -> failed
    # dedupe with db.mark_processed(f"inv:{id}:{kind}"); return oldest first
```

`setup.py` calls `sync_invoices` once at the end so seed invoices are marked processed.

`server.py`:
- `GET /` → `view.html`.
- `GET /api/state` → `{members+credits+queue, leases (pool%, buyer%, rent), gpus (from gpu_client.status), cash, invariant_ok, ledger: recent(25), decisions: last 10}`.
- `POST /webhook` (optional): verify with `STRIPE_WEBHOOK_SECRET`, **log only**. The demo uses `sync()` so nothing is processed twice.

`view.html`:
- Four panels: **Queue** (credits leaderboard), **Leases** (pool / buyer bar, rent, title holder, status), **Ledger** (kind, amount, clause, reason), **Decisions** (ALLOW green / BLOCK red).
- Polls `/api/state` every 1.5s.
- Uses `docs/theme.css` rules: charcoal, hard corners, red/green only, Saira.

### L7 — `setup.py`, `demo.py`, fixtures, README

**`setup.py` (idempotent: wipes the DB, creates fresh Stripe objects):**
1. `db.init(wipe=True)`; `billing.setup_catalog()`.
2. Clocks: `CLOCK_A` (Amara + Nova Labs), `CLOCK_B` (Carl). Unclocked customers: Priya, Tom, Ben, and an "operator" customer.
3. Members from `fixtures/members.json`. For each contribution: `billing.charge` (good card, metadata `contributed_at`) → contributions row → capital. Also charge the operator £500 "fee float" (covers Stripe fees; not member capital).
4. Opening: `billing.transfer(supplier, 1_010_000, "opening: pool stakes in GPU-002 (Ben) and GPU-003 (Carl)")` → cash = 1,060,000 → ledger `opening_balance`.
5. `gpu_client.seed`: GPU-002 (Ben, active), GPU-003 (Carl, active), both `title_holder="pool"`.
6. Leases:
   - Ben: rent subscription qty **50** (£25).
   - Carl: rent subscription qty **960** (£480) with the good card, then `set_card(carl, "fail", subscription_id=...)`.
7. Nova Labs: `create_usage_subscription` (destination = Amara's account if connect mode).
8. Memory seeds: each policy clause (`policy`). Carl's history from `fixtures/history/carl.json` → `memories` + `promises` (the July holiday status `broken`) + ledger `history` rows.
9. `observer.sync_invoices(all customers)` to mark seed invoices processed.
10. Assert `invariant_ok`, `balance_available() ≥ 1_000_000`. Print a summary.

**`demo.py run [--pause] [--from N] [--speed 0.25]`:**

| Scene | Calls |
|---|---|
| 1 | `ui.scene`; print `pool.queue`; `agent.handle_contract("fixtures/contracts/amara.md")`; `agent.sync([amara])` |
| 2 | 30× `gpu_client.workload(GPU-new, key, nova, 1000)`; `agent.process_usage()`; `billing.advance_clock_past_renewal(CLOCK_A, amara_sub)`; `agent.sync([nova, amara])` |
| 3 | `agent.handle_email("fixtures/inbox/01_amara_buyback.txt")` |
| 4 | print "Carl: 4.0% equity, rent £480.00"; `billing.advance_clock_past_renewal(CLOCK_B, carl_sub)`; `agent.sync([carl])` (reads `02_carl_skip.txt`) |
| 5 | `agent.handle_email("fixtures/inbox/03_ben_final.txt")`; then `gpu_client.revoke(GPU-002)` → print the 409 |
| 6 | `memory.flush()`; print `agent.explain("Why was Carl disconnected?")`; print `invariant_ok` ("books balance ✓") |

`demo.py reset` deletes the clocks (`TestClock.delete`) and wipes the DB.

**Fixtures:**

`fixtures/members.json` (names are fictional):

| id | name | contributions (date: £) | credits_reset_at | capital |
|---|---|---|---|---|
| amara | Amara Okafor | 2025-09-10: £2,000 | — | £2,000 |
| priya | Priya Shah | 2026-08-16: £10,000 | — | £10,000 |
| tom | Tom Reid | 2026-07-17: £7,000 | — | £7,000 |
| ben | Ben Hartley | 2026-04-01: £1,200 | 2026-04-01 | £1,200 |
| carl | Carl Novak | 2026-05-01: £500 | 2026-01-10 | £500 |

→ Credits as of 2026-10-15: **Amara 800,000** · Tom 630,000 · Priya 600,000 · Ben 236,400 · Carl 83,500. Amara is top, and eligible with £2,000 = 20%.

`fixtures/contracts/amara.md`:
- **Agreement:** lease-to-own, mortGPU Pool #1.
- **Lessee:** Amara Okafor (member amara).
- **Asset:** 1 × NVIDIA RTX PRO 6000 Blackwell 96GB, racked, supplied by **Northwind Compute Ltd**.
- **Locked price:** £10,000.00. Deposit from her pool savings.
- **Rent:** 5% per month × pool share. Buybacks are voluntary, at the locked price.
- **Intended use:** inference resale to Nova Labs. **Projected revenue £1,200/month.**

Inbox:
- `fixtures/inbox/01_amara_buyback.txt`: "Nova paid well this month — I'd like to buy back £1,000 of my GPU. Take it from my card on file. — Amara"
- `fixtures/inbox/02_carl_skip.txt`: "Rough month. Can I skip this month's rent? I'll pay double next month, promise. — Carl"
- `fixtures/inbox/03_ben_final.txt`: "Final payment! Please take the last £500 and hand me the keys. — Ben"

`fixtures/history/carl.json`, events:
- Jan: lease opened at 20%.
- Jul: payment holiday granted (§4.2); Carl promised to pay double in August.
- Aug: did not pay; **promise broken**; equity repossessed.
- Sep: missed; equity repossessed.
- Now: 4.0% equity.

---

## 9. Worked numbers (L3 tests must assert these)

| Step | Input | Output |
|---|---|---|
| Opening | capital 2,070,000; stakes 50,000 + 960,000 | cash 1,060,000; invariant ✓ |
| Amara converts | capital 200,000, price 1,000,000 | buyer 200‰, pool 800‰, capital 0 |
| Amara rent | pool 800‰ | **40,000** |
| Supplier paid | cash 1,060,000 | cash 60,000; invariant ✓ (1,870,000 = 60,000 + 50,000 + 960,000 + 800,000) |
| Rent split 40,000 | weights priya 1,000,000 · tom 700,000 · ben 120,000 · carl 50,000 (amara excluded) | **priya 21,390 · tom 14,973 · ben 2,567 · carl 1,070** (sum 40,000) |
| ICR | revenue 120,000 / rent 40,000 | 300% ✓ (≥ 125%) |
| Amara buyback | 100,000 | Δ 100‰ → pool 700‰ → rent **35,000** |
| Carl repossession | arrears 48,000, buyer 40‰ | taken 40‰, value 40,000, **written off 8,000** → pool 1000‰ |
| Carl in-kind split 40,000 | weights priya 1,000,000 · tom 700,000 · ben 120,000 (carl excluded) | **priya 21,978 · tom 15,385 · ben 2,637** |
| Ben final | 50,000, pool 50‰ | pool 0‰ → §5.1 title → Ben |

---

## 10. Lane acceptance tests

| Lane | Test |
|---|---|
| L0 | `python -c "from mortgpu import db; db.init(wipe=True)"` creates every table; `ledger.record` prints an AUDIT line |
| L1 | `python -m mortgpu.billing --selftest`: clock + customer + rent sub qty 800 → invoice paid 40,000 → qty 700 → preview 35,000 → 5 meter events → summary → fail card → advance → failed invoice listed → `transfer` returns an id or None. Prints PASS per step |
| L2 | provision → workload 200 → line in `usage.log` → revoke → workload 403 → transfer_title on another GPU → revoke 409 |
| L3 | `pytest tests/` (every row of §9 plus BLOCK cases: deposit 19.9%, unknown supplier, ICR 124%, holiday after a broken promise, buyback £499) |
| L4 | `python -m mortgpu.llm --selftest` returns the expected Proposal for each fixture in **both** `live` and `fixture` mode |
| L5 | `python -m mortgpu.memory --selftest`: remember two Carl facts → flush → `recall("Did Carl keep his promise?")` contains "broken"; `facts("carl")` = 1 holiday, 1 broken. Works with `COGNEE_ENABLED=0` too |
| L6 | `/api/state` returns JSON with an empty DB; the view renders with seeded data |
| L7 | `python -m mortgpu.setup && python -m mortgpu.demo run` completes all 6 scenes; the final invariant ✓ |

---

## 11. Integration timeline (it's ~19:10)

- **19:10–19:25:** L0 skeleton and stubs.
  - **Integrator, in parallel:** `uv pip install`; warm Cognee; Stripe dashboard prep:
    - Connect enabled; 6 connected test accounts (supplier + 5 members) → `.env`;
    - Billing → Revenue recovery: retries off, "if all retries fail → leave past due".
- **19:25:** spawn L1–L7 with the prompt below.
- **20:00, checkpoint 1:** `setup` + scenes 1–2 run end to end. If Connect isn't working → `CONNECT_MODE=ledger`.
- **20:15, checkpoint 2:** scenes 3–6. If Cognee isn't working → `COGNEE_ENABLED=0`. If the LLM is flaky → `LLM_MODE=fixture`.
- **20:20:** backup recording. **20:30:** record → submit → improve until 21:00.

**Lane prompt template (paste into each agent):**

> You are building lane **LX** of mortGPU. Read `SPEC.md` in full. You own only: `<files>`. Implement the interfaces in §8 exactly (names, args, return types). Use the units in §5. Real Stripe test mode only, with keys from `.env`. Do not touch other lanes' files and do not run git. If you need something from another lane, code against its spec'd signature, and put requests in `NOTES-LX.md`. Finish by running your acceptance test from §10 and reporting the output.

---

## 12. Fallback switches

| Problem | Switch | Effect |
|---|---|---|
| Connect transfers fail | `CONNECT_MODE=ledger` | ledger rows only; scenes unchanged |
| Cognee slow or broken | `COGNEE_ENABLED=0` | keyword recall over `memories`; scene 4 and 6 still work |
| Claude API down | `LLM_MODE=fixture` | deterministic proposals and a templated explanation |
| Retries can't be disabled | `DEMO_FIRST_FAILURE_IS_FINAL=1` | first failure treated as final; the ledger reason says so |
| Usage missing from clocked invoice | Nova moved to an unclocked customer | show `Invoice.create_preview` instead |

## 13. Out of scope

Live sign-ups/contributions on camera · pricing by credits · queue swaps/voting · auto-buyback sweeps · real GPUs/supplier orders · market-value feeds · auth/multi-user · Accounts v2.
