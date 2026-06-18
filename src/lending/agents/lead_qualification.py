"""
Lead Qualification Agent (#21, §16.7) — the segment gate at Step 2.

Classifies a fresh lead into the narrowed pilot segment (salaried + credit-tested
+ in-policy) using Gemini (lite model — it's a light classification). Non-
deterministic by design: real lead data is messy/high-dimensional, so the agent
classifies rather than a brittle rule. Safety guardrails (via the #16 scaffold):
  - strict schema (LeadQualification) — reject + retry on malformed output
  - escalate-on-uncertainty — ambiguous/low-confidence → human review, never an
    arbitrary auto-decline

NOTE (§2.1): this is the eligibility *gate*, not the binding credit decision
(that stays deterministic in #18). Confidence here is model-reported (no OCR-style
grounding) and must be calibrated by the eval harness (#24) before pilot.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from lending.agent_runtime import AgentSpec, build_agent
from lending.agent_runtime.models import ReasonFn
from lending.audit import AuditStore, EventType

from .llm import gemini_reason, model_lite


class SegmentFit(str, Enum):
    IN_SEGMENT = "in_segment"
    OUT_OF_SEGMENT = "out_of_segment"
    UNCERTAIN = "uncertain"


class LeadQualification(BaseModel):
    segment_fit: SegmentFit
    employment_type: str = Field(description="normalized: salaried | self_employed | unemployed | unknown (informational only, never used to reject)")
    has_credit_history: Optional[bool] = Field(default=None, description="self-reported only if the lead mentions it; usually unknown at this stage. NOT used to route.")
    reason_code: str = Field(description="stable code, e.g. PROCEED, OUT_OF_SCOPE_NOT_A_LOAN, SPAM, INSUFFICIENT_INFO")
    confidence: float = Field(ge=0.0, le=1.0, description="model confidence in the routing")
    reasoning: str = Field(description="the model's reasoning for this routing (concise, 1-3 sentences)")


_SYSTEM_PROMPT = (
    "You triage inbound leads for an Indian personal-loan pilot. Your ONLY job is light "
    "routing — you do NOT make eligibility or credit decisions. The system makes all real "
    "eligibility, employment, and credit decisions later, deterministically and with "
    "auditable reasons.\n\n"
    "Classify segment_fit:\n"
    "- in_segment: any plausible personal-loan inquiry — let it proceed. Do NOT reject on "
    "employment type (salaried, self-employed, etc.) or on credit history; the applicant "
    "may not even know their credit status, and these are assessed downstream.\n"
    "- out_of_segment: ONLY clearly out-of-scope leads — not actually seeking a personal "
    "loan, spam/junk, or a different product entirely.\n"
    "- uncertain: too little or contradictory information to tell whether it's a genuine "
    "inquiry — escalate to a human rather than guessing.\n\n"
    "Default to in_segment for any borderline-but-plausible applicant. Normalize "
    "employment_type if stated, but never use it to reject. Always return a stable "
    "reason_code, and explain your routing in the `reasoning` field."
)

# Below this confidence (or when uncertain), the gate escalates to a human review
# instead of auto-deciding.
_THRESHOLD = 0.7
ESCALATION_STATE = "MANUAL_REVIEW"


def _confidence_fn(output: dict, tool_result: dict) -> float:
    # Uncertain always escalates, regardless of any self-reported number (§16.4 caution).
    if output.get("segment_fit") == SegmentFit.UNCERTAIN.value:
        return 0.0
    return float(output.get("confidence", 0.0))


def _soft_credit_check(context: dict) -> dict:
    # Placeholder for a real soft bureau-existence check (an adapter). For now the
    # lead's own fields carry the signal; this is a no-op tool slot.
    return {}


def build_lead_qualification_agent(reason: Optional[ReasonFn] = None, *, checkpointer=None):
    """Compile the Lead Qualification agent on the #16 scaffold. `reason` defaults
    to a Gemini (lite) call; inject a fake in tests."""
    spec = AgentSpec(
        name="lead-qualification",
        tool=_soft_credit_check,
        reason=reason or gemini_reason(_SYSTEM_PROMPT, LeadQualification, model=model_lite()),
        output_schema=LeadQualification,
        confidence_fn=_confidence_fn,
        threshold=_THRESHOLD,
        escalation_state=ESCALATION_STATE,
    )
    return build_agent(spec, checkpointer=checkpointer)


@dataclass(frozen=True)
class QualificationResult:
    status: str               # "qualified" | "declined_early" | "manual_review"
    reason_code: Optional[str]
    reasoning: Optional[str]   # the LLM's explanation of why it routed this way
    qualification: Optional[dict]


def qualify_lead(
    repository,
    audit: AuditStore,
    application_id: str,
    *,
    reason: Optional[ReasonFn] = None,
) -> QualificationResult:
    """Run the segment gate for an application, record the audited reason, and
    return the outcome. Out-of-segment is filtered here (Step 2), not downstream."""
    application = repository.get(application_id)
    if application is None:
        raise ValueError(f"unknown application: {application_id!r}")

    context = {
        "applicant": application.applicant.model_dump(),
        "features": application.features,
    }
    agent = build_lead_qualification_agent(reason=reason)
    final = agent.invoke({"context": context}, config={"configurable": {"thread_id": application_id}})

    output = final.get("validated")
    if final.get("outcome") == "escalated":
        status, reason_code = "manual_review", (output or {}).get("reason_code", "MANUAL_REVIEW")
    elif (output or {}).get("segment_fit") == SegmentFit.IN_SEGMENT.value:
        status, reason_code = "qualified", output.get("reason_code")
    else:
        status, reason_code = "declined_early", (output or {}).get("reason_code")

    reasoning = (output or {}).get("reasoning")

    audit.append(
        application_id,
        EventType.AGENT_REASONING,
        {
            "agent": "lead-qualification",
            "status": status,
            "reason_code": reason_code,
            "reasoning": reasoning,          # the LLM's "why", surfaced for audit/transparency
            "confidence": final.get("confidence"),
            "qualification": output,
        },
        actor="agent:lead-qualification",
    )
    return QualificationResult(
        status=status, reason_code=reason_code, reasoning=reasoning, qualification=output
    )
