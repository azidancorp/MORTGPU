"""Shared dataclasses (§6)."""
import secrets
from dataclasses import asdict, dataclass, field


@dataclass
class Proposal:
    action: str            # open_lease | buyback | payment_holiday | no_action
    member_id: str
    amount_pence: int | None = None
    terms: dict | None = None   # open_lease: price_pence, supplier_id, gpu_model, projected_revenue_pence
    rationale: str = ""         # LLM's one-line why
    evidence: str = ""          # quote from the source text

    def to_dict(self) -> dict:
        return asdict(self)


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
    clauses: list[ClauseResult] = field(default_factory=list)
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"


def new_decision_id() -> str:
    return "D-" + secrets.token_hex(3)
