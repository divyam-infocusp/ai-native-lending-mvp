from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DispositionHint(str, Enum):
    APPROVE = "APPROVE"
    DECLINE = "DECLINE"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class ApplicantFeatures:
    age: int
    monthly_income: float        # gross, INR
    monthly_obligations: float   # total existing EMIs, INR
    cibil_score: int
    employment_tenure_months: int
    loan_amount_requested: float
    loan_tenure_months: int
    is_salaried: bool
    has_cibil_record: bool


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    value: Any
    threshold: Any


@dataclass(frozen=True)
class PolicyHit:
    rule_id: str
    reason_code: str


@dataclass(frozen=True)
class EvaluationResult:
    rule_results: list[RuleResult]
    policy_hits: list[PolicyHit]
    disposition_hint: DispositionHint
