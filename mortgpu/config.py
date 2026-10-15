"""Constants (§4) and env settings."""
import json
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEMO_TODAY = date(2026, 10, 15)       # credits are computed as of this date (deterministic)
ASSET_PRICE_PENCE = 1_000_000         # £10,000 locked price
RENT_RATE_BPS = 500                   # 5% per month
RENT_UNIT_PENCE = 50                  # rent price: £0.50 per 0.1% (permille) of pool ownership
USAGE_UNIT_PENCE = 4                  # £0.04 per GPU-minute (£2.40/hr)
CURRENCY = "gbp"


def _env(key: str, default: str = "") -> str:
    v = (os.environ.get(key) or "").split(" #")[0].strip()
    return default if not v or v.endswith("...") else v  # unfilled .env.example placeholders


STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
# Claude direct when ANTHROPIC_API_KEY is set; otherwise OpenRouter's Anthropic-compatible endpoint
LLM_PROVIDER = "anthropic" if ANTHROPIC_API_KEY or not OPENROUTER_API_KEY else "openrouter"
LLM_MODEL = _env("LLM_MODEL", "claude-sonnet-5-5" if LLM_PROVIDER == "anthropic" else "deepseek/deepseek-v4.1-flash")
LLM_MODE = _env("LLM_MODE", "live")
COGNEE_ENABLED = _env("COGNEE_ENABLED", "1") == "1"
CONNECT_MODE = _env("CONNECT_MODE", "connect")
DEMO_FIRST_FAILURE_IS_FINAL = _env("DEMO_FIRST_FAILURE_IS_FINAL", "1") == "1"
SUPPLIER_ACCOUNT_ID = _env("SUPPLIER_ACCOUNT_ID") or None
MEMBER_ACCOUNTS: dict[str, str] = {
    k: v for k, v in json.loads(_env("MEMBER_ACCOUNTS") or "{}").items() if not v.endswith("...")}
GPU_API_URL = _env("GPU_API_URL", "http://localhost:8001")
DB_PATH = _env("DB_PATH", str(ROOT / "mortgpu.db"))

POLICY_PATH = ROOT / "policy.json"
FIXTURES = ROOT / "fixtures"
USAGE_LOG = ROOT / "usage.log"
GPU_STATE = ROOT / "gpu_state.json"
MEMBER_IDS = ["amara", "priya", "tom", "ben", "carl"]
