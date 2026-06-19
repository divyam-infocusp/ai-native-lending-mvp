"""
Onboarding Copilot (#22, §16.11) — genuine multi-turn conversational onboarding.

Turns a LEAD_QUALIFIED applicant into a complete, submittable application by
conversing until every required field + document is collected. It autofills from
what's already known and only asks for what's missing.

Design:
  - LangGraph graph + checkpointer → **durable conversation memory** keyed by
    application_id, so the applicant can leave and resume.
  - Each turn: Gemini (lite) extracts fields from the latest message and writes
    the next question (typed schema → structured output).
  - **Completeness is decided deterministically** (required fields present), never
    from the model's self-report — "LLM perceives, code decides" (§2.1 spirit).
  - Binding text uses reviewed templates, not live LLM translation (§16.11). The
    conversational prompts may be multilingual; the copilot here only asks
    questions (no binding text), so that surface is light.

Temporal/workflow wiring (park on applicant-reply signals → APPLICATION_SUBMITTED)
is deferred to the conversation API + #15 signals.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from lending.agent_runtime.models import ReasonFn
from lending.audit import AuditStore, EventType

from .llm import gemini_reason, model_lite

# What an Indian salaried personal-loan application needs before submission
# (grounded in current lender requirements: KYC identity, contact/address,
# employment + income, loan details, and standard documents).
REQUIRED_FIELDS = [
    # identity / KYC
    "full_name", "date_of_birth", "pan", "aadhaar",
    # contact / address
    "mobile", "current_address",
    # employment + income
    "employment_type", "employer_name", "employment_tenure_months", "monthly_income",
    # loan details
    "loan_amount_requested", "loan_tenure_months", "loan_purpose",
]
REQUIRED_DOCUMENTS = [
    "identity_proof",     # Aadhaar/PAN/Passport/DL
    "address_proof",      # utility bill / Aadhaar / passport
    "salary_slips",       # last 2-3 months
    "bank_statement",     # 2-3 months, showing salary credits
    "form16",             # or ITR, previous year
]

_APPLICANT_FIELDS = {"full_name", "pan", "aadhaar", "date_of_birth", "mobile", "email", "current_address"}


class ExtractedFields(BaseModel):
    """Data fields the model can extract from a user message (all optional — only
    what was actually said this turn). Typed so Gemini structured output is
    reliable. NOTE: document *uploads* are deliberately NOT here — the model must
    not claim a file was uploaded from chat; presence comes from register_document
    (a real upload action), and validity from Document Intelligence (#19)."""
    # identity / KYC
    full_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    pan: Optional[str] = None
    aadhaar: Optional[str] = None
    # contact / address
    mobile: Optional[str] = None
    email: Optional[str] = None
    current_address: Optional[str] = None
    # employment + income
    employment_type: Optional[str] = None
    employer_name: Optional[str] = None
    employment_tenure_months: Optional[int] = None
    monthly_income: Optional[float] = None
    # loan details
    loan_amount_requested: Optional[float] = None
    loan_tenure_months: Optional[int] = None
    loan_purpose: Optional[str] = None


class OnboardingTurn(BaseModel):
    extracted: ExtractedFields = Field(default_factory=ExtractedFields)
    assistant_message: str = Field(description="the copilot's reply / next question, in the user's language")
    reasoning: str = Field(description="what was extracted and why this question was asked")


@dataclass(frozen=True)
class OnboardingResponse:
    assistant_message: str
    complete: bool
    missing: list[str]
    reasoning: Optional[str]
    collected: dict   # all filled fields so far (applicant + features + documents),
                      # for the UI to show a review-and-correct screen before submit


_SYSTEM_PROMPT = (
    "You are an onboarding copilot for an Indian personal-loan application. Help the "
    "applicant complete their application conversationally. You are given what's already "
    "collected and what's still missing. From the applicant's latest message, extract any "
    "fields they provided (only those actually stated) into `extracted`.\n\n"
    "Then ask — warmly and in the applicant's language — for the next missing items. Group "
    "a few RELATED missing items into one message (e.g. identity details together; then "
    "employment & income together; then loan amount/tenure/purpose together; then the "
    "documents together). Ask for about 2-4 items per message: enough to move quickly, but "
    "never a long overwhelming wall of questions, and never just one trivial field at a "
    "time. Do not re-ask for anything already collected. Keep messages short and friendly. "
    "When documents are missing, ask the applicant to upload them in the app — but do NOT "
    "assume a document has been uploaded based on the conversation; uploads are tracked by "
    "the system, not by you. You never make eligibility or credit decisions."
)


class OnboardingError(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _collected_view(application) -> dict:
    a = application.applicant
    view = {
        "full_name": a.full_name, "pan": a.pan, "aadhaar": a.aadhaar,
        "date_of_birth": a.date_of_birth, "mobile": a.mobile, "email": a.email,
        "current_address": a.current_address,
    }
    view.update(application.features or {})
    return view


def missing_fields(application) -> list[str]:
    """Deterministic completeness check: required fields not yet present, plus any
    required document not yet *uploaded* (presence comes from register_document —
    a real upload action — never from the conversation)."""
    view = _collected_view(application)
    missing = [f for f in REQUIRED_FIELDS if not view.get(f)]
    docs = (application.features or {}).get("documents", {})
    missing += [
        f"document:{d}" for d in REQUIRED_DOCUMENTS
        if not (docs.get(d) or {}).get("uploaded")
    ]
    return missing


def register_document(repository, application_id: str, doc_type: str, reference: Optional[str] = None):
    """Record that a document was genuinely uploaded (the UI/upload endpoint calls
    this when a file is received). Presence is set here, not by the copilot. The
    `verified` slot is left for Document Intelligence (#19) to fill (right doc?
    readable? matches the applicant?)."""
    if doc_type not in REQUIRED_DOCUMENTS:
        raise ValueError(f"unknown document type: {doc_type!r}")
    application = repository.get(application_id)
    if application is None:
        raise ValueError(f"unknown application: {application_id!r}")
    feats = dict(application.features or {})
    docs = dict(feats.get("documents", {}))
    docs[doc_type] = {"uploaded": True, "verified": None, "reference": reference}
    feats["documents"] = docs
    application.features = feats
    application.updated_at = _utcnow()
    repository.save(application)
    return application


def _apply_updates(application, extracted: dict) -> None:
    feats = dict(application.features or {})
    for key, value in extracted.items():
        if value is None:
            continue
        if key in _APPLICANT_FIELDS:
            setattr(application.applicant, key, value)
        else:
            feats[key] = value
    application.features = feats


class _ConvState(TypedDict, total=False):
    messages: Annotated[list, operator.add]   # durable conversation history
    context: dict                              # fresh per turn (collected / missing)
    turn: dict                                 # the validated OnboardingTurn for this turn


class OnboardingCopilot:
    """Holds the conversational graph + a durable checkpointer; reuse one instance
    across turns (the checkpointer keeps per-application memory)."""

    def __init__(self, reason: Optional[ReasonFn] = None, checkpointer=None, model: Optional[str] = None) -> None:
        self._reason = reason or gemini_reason(_SYSTEM_PROMPT, OnboardingTurn, model=model or model_lite())
        self._graph = self._build_graph(checkpointer or MemorySaver())

    def _build_graph(self, checkpointer):
        graph = StateGraph(_ConvState)

        def converse(state: _ConvState) -> dict:
            ctx = {**state.get("context", {}), "history": state.get("messages", [])}
            last_error: Exception | None = None
            for _ in range(3):  # schema reject + retry
                try:
                    turn = OnboardingTurn(**self._reason(ctx, {})).model_dump()
                    break
                except ValidationError as err:
                    last_error = err
            else:
                raise OnboardingError("onboarding model output failed validation") from last_error
            return {
                "messages": [{"role": "assistant", "content": turn["assistant_message"]}],
                "turn": turn,
            }

        graph.add_node("converse", converse)
        graph.add_edge(START, "converse")
        graph.add_edge("converse", END)
        return graph.compile(checkpointer=checkpointer)

    def turn(
        self,
        repository,
        audit: AuditStore,
        application_id: str,
        user_message: Optional[str] = None,
    ) -> OnboardingResponse:
        application = repository.get(application_id)
        if application is None:
            raise ValueError(f"unknown application: {application_id!r}")

        context = {
            "collected": _collected_view(application),
            "missing": missing_fields(application),
            "required_fields": REQUIRED_FIELDS,
            "required_documents": REQUIRED_DOCUMENTS,
            "latest_user_message": user_message,
        }
        user_msgs = [{"role": "user", "content": user_message}] if user_message else []
        final = self._graph.invoke(
            {"messages": user_msgs, "context": context},
            config={"configurable": {"thread_id": application_id}},
        )
        turn = final["turn"]

        _apply_updates(application, turn.get("extracted", {}))
        application.updated_at = _utcnow()
        repository.save(application)

        remaining = missing_fields(application)
        complete = not remaining
        # Filled fields only (drop unanswered Nones), for the UI review screen.
        collected = {k: v for k, v in _collected_view(application).items() if v not in (None, "")}

        audit.append(
            application_id,
            EventType.AGENT_REASONING,
            {
                "agent": "onboarding-copilot",
                "assistant_message": turn["assistant_message"],
                "extracted": turn.get("extracted", {}),
                "missing": remaining,
                "complete": complete,
                "reasoning": turn.get("reasoning"),
            },
            actor="agent:onboarding-copilot",
        )
        return OnboardingResponse(
            assistant_message=turn["assistant_message"],
            complete=complete,
            missing=remaining,
            reasoning=turn.get("reasoning"),
            collected=collected,
        )
