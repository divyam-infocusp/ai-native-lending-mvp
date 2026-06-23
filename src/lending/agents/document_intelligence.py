"""
Document Intelligence Agent (#19, §16.4) — extract, cross-check, ground, gate.

Turns a stack of uploaded documents into a **verified profile + per-field grounded
confidence**, or routes the application to `KYC_EXCEPTION` for human review.

Pipeline (deterministic — no LLM self-report anywhere; §2.1/§16.4):
  1. EXTRACT   each uploaded document → canonical fields, each with an OCR
     confidence. Extraction is injected (`extract` fn / OCR adapter #9), so the
     agent logic is testable without a live OCR engine.
  2. CROSS-CHECK every canonical field that ≥2 documents reported, pairwise, with
     **type-aware** comparison (doc_compare.values_match) — fully dynamic, no
     hardcoded document pairs. A field on a single source gets no check (neutral,
     not penalized). Income is split into gross vs net so we never compare a
     payslip's gross against a bank statement's net.
  3. VALIDATE  format/checksum per field (PAN structure, Aadhaar Verhoeff, …).
  4. SCORE     per-field grounded confidence via the Confidence Service (#5):
     ocr × cross-source agreement × validator pass. Plus payslip obvious-fake
     checks on the salary slip.
  5. GATE      any KEY field missing / below threshold, or any cross-source
     mismatch on a key field → KYC_EXCEPTION; otherwise write the verified
     profile and advance to KYC_VERIFIED.

The agent never makes the credit decision — it decides whether the *inputs* to
that decision can be trusted.

Cross-source population responsibility (per #19 comment): this agent builds the
`list[CrossSourceCheck]` fed to #5, always naming both real source documents so a
mismatch is reviewable in audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from itertools import combinations
from typing import Callable, Optional

from lending.audit import AuditStore, EventType
from lending.policy import CONFIDENCE_POLICY
from lending.confidence import (
    CrossSourceCheck,
    FieldConfidenceResult,
    Payslip,
    RiskFlag,
    ValidatorResult,
    check_payslip,
    field_confidence,
    validate_aadhaar,
    validate_ifsc,
    validate_pan,
)
from lending.los.schema import FieldConfidence, KycStatus

from .doc_compare import values_match

# An extractor: given (application_id, doc_type), return that document's canonical
# fields as {field_name: {"value": ..., "ocr_conf": float}}. Real OCR/KYC adapters
# (#9) implement this; tests inject a fake. Fields absent from the doc are omitted.
ExtractFn = Callable[[str, str], dict]

# KYC key fields are a credit/compliance policy judgment (§16.9), so they live in
# the versioned CONFIDENCE_POLICY — not hardcoded here. Income is split by
# semantics so cross-checks compare like-with-like (gross ← slip/Form-16; net ←
# slip/bank statement).
def key_fields_for(policy_version: str = "v1") -> frozenset:
    """The gate-critical fields for a policy version (each must be reliable +
    agree across sources, or the application routes to KYC_EXCEPTION)."""
    if policy_version not in CONFIDENCE_POLICY:
        raise ValueError(f"Unknown policy_version: {policy_version!r}")
    return frozenset(CONFIDENCE_POLICY[policy_version]["kyc_key_fields"])

# Document metadata that may appear in several extractions but must NOT be
# cross-checked as if it were a personal attribute.
_METADATA_FIELDS = frozenset({
    "document_type", "period", "statement_date", "issue_date", "page_count",
})

# Field → format/checksum validator (only fields with a known validator).
_VALIDATORS: dict[str, Callable[[str], ValidatorResult]] = {
    "pan": lambda v: validate_pan(v, field_name="pan"),
    "aadhaar": lambda v: validate_aadhaar(v, field_name="aadhaar"),
    "ifsc": lambda v: validate_ifsc(v, field_name="ifsc"),
}

# Canonical field → Applicant attribute (the rest land in features).
_APPLICANT_FIELDS = {
    "name": "full_name", "pan": "pan", "aadhaar": "aadhaar",
    "date_of_birth": "date_of_birth", "address": "current_address",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DocIntelResult:
    status: str                                   # "verified" | "exception"
    profile: dict                                 # canonical field → chosen value
    field_confidence: dict                         # canonical field → FieldConfidenceResult
    cross_checks: list                             # list[CrossSourceCheck] (all, for audit)
    exception_reasons: list = dc_field(default_factory=list)


def make_ocr_extractor(harness, *, provider: str = "ocr") -> ExtractFn:
    """Default extractor backed by the adapter harness (#1): one idempotent OCR
    call per document, purpose=`extract:<doc_type>`. The real OCR/KYC adapters
    (#9) register under `provider`; until then a MockAdapter with per-doc fixtures
    can stand in. The adapter's `data` must be the canonical {field: {value,
    ocr_conf}} shape this agent consumes."""
    from lending.adapters import AdapterRequest

    def extract(application_id: str, doc_type: str) -> dict:
        resp = harness.call(AdapterRequest(
            application_id=application_id, provider=provider, purpose=f"extract:{doc_type}",
        ))
        return resp.data

    return extract


# ---------------------------------------------------------------------------
# Pure core (no I/O) — extraction orchestration, cross-checks, scoring, gating
# ---------------------------------------------------------------------------

def _extract_all(application_id: str, doc_types: list[str], extract: ExtractFn) -> dict:
    """{doc_type: {field: {"value", "ocr_conf"}}} for every uploaded document.

    Documents are extracted concurrently: extraction is network/IO-bound (an OCR
    or LLM call per doc), so a thread pool collapses the total to ~the slowest
    single document instead of the sum. Order-independent — results are keyed by
    doc_type."""
    from concurrent.futures import ThreadPoolExecutor

    def one(doc_type: str) -> tuple[str, dict]:
        extracted = extract(application_id, doc_type) or {}
        kept = {
            f: rec for f, rec in extracted.items()
            if f not in _METADATA_FIELDS and rec and rec.get("value") not in (None, "")
        }
        return doc_type, kept

    if len(doc_types) <= 1:
        return dict(one(d) for d in doc_types)

    with ThreadPoolExecutor(max_workers=min(8, len(doc_types))) as pool:
        return dict(pool.map(one, doc_types))


def _sources_by_field(extractions: dict) -> dict:
    """Invert {doc: {field: rec}} → {field: [(doc, rec), ...]}, skipping document
    metadata (never cross-checked as a personal attribute) and empty values."""
    by_field: dict[str, list] = {}
    for doc_type, fields in extractions.items():
        for f, rec in fields.items():
            if f in _METADATA_FIELDS or not rec or rec.get("value") in (None, ""):
                continue
            by_field.setdefault(f, []).append((doc_type, rec))
    return by_field


def build_cross_checks(extractions: dict, *, policy_version: str = "v1") -> list[CrossSourceCheck]:
    """Dynamic cross-source checks: every field reported by ≥2 documents is
    compared pairwise with type-aware matching. Both real source names recorded."""
    checks: list[CrossSourceCheck] = []
    for f, sources in _sources_by_field(extractions).items():
        if len(sources) < 2:
            continue  # single source → neutral, not a penalty
        for (doc_a, rec_a), (doc_b, rec_b) in combinations(sources, 2):
            matches = values_match(
                f, rec_a["value"], rec_b["value"], policy_version=policy_version
            )
            checks.append(CrossSourceCheck(field_name=f, source_a=doc_a, source_b=doc_b, matches=matches))
    return checks


def _choose_value(sources: list) -> tuple:
    """Pick the value to store: the source that read it with highest OCR confidence."""
    doc, rec = max(sources, key=lambda s: s[1].get("ocr_conf", 0.0))
    return rec["value"], doc


def _payslip_flags(extractions: dict, *, policy_version: str) -> list[RiskFlag]:
    """Run the obvious-fake payslip checks on the salary slip.

    Plausibility (net>gross, negatives, sane range) always applies. The arithmetic
    reconciliation checks only make sense when the slip itemizes its components:
    without a deductions breakdown, `gross - 0 != net` is *expected* (tax/PF), not
    a fake — so we suppress those flags unless the breakdown is present."""
    slip = extractions.get("salary_slips", {})
    gross = (slip.get("gross_monthly_income") or {}).get("value")
    net = (slip.get("net_monthly_income") or {}).get("value")
    if gross is None or net is None:
        return []
    earnings = {k: float(v) for k, v in (slip.get("earnings", {}) or {}).items()}
    deductions = {k: float(v) for k, v in (slip.get("deductions", {}) or {}).items()}
    flags = check_payslip(
        Payslip(gross_pay=float(gross), net_pay=float(net), earnings=earnings, deductions=deductions),
        policy_version=policy_version,
    )
    if not deductions:
        flags = [f for f in flags if f != RiskFlag.NET_DERIVATION_MISMATCH]
    if not earnings:
        flags = [f for f in flags if f != RiskFlag.COMPONENT_SUM_MISMATCH]
    return flags


def score_profile(extractions: dict, *, policy_version: str = "v1") -> tuple:
    """Compute the verified profile + per-field grounded confidence.

    Returns (profile, field_results, cross_checks). Per-field confidence uses the
    *minimum* OCR confidence across the sources that reported the field (the
    worst read should flag LOW_OCR), the cross-checks for that field, and any
    format validator. Payslip obvious-fake flags attach to income fields."""
    cross_checks = build_cross_checks(extractions, policy_version=policy_version)
    by_field = _sources_by_field(extractions)
    payslip_flags = _payslip_flags(extractions, policy_version=policy_version)

    profile: dict = {}
    field_results: dict = {}
    for f, sources in by_field.items():
        value, _ = _choose_value(sources)
        profile[f] = value

        ocr_conf = min(s[1].get("ocr_conf", 0.0) for s in sources)
        field_checks = [c for c in cross_checks if c.field_name == f]
        validators = [_VALIDATORS[f](value)] if f in _VALIDATORS else []

        result = field_confidence(
            ocr_conf=ocr_conf,
            cross_source_checks=field_checks,
            validators=validators,
            policy_version=policy_version,
        )
        # Fold payslip obvious-fake flags into the income fields' risk picture.
        if f in ("gross_monthly_income", "net_monthly_income") and payslip_flags:
            result = FieldConfidenceResult(
                confidence=result.confidence,
                risk_flags=[*result.risk_flags, *payslip_flags],
                is_reliable=False,  # an obvious-fake payslip is never reliable
            )
        field_results[f] = result

    return profile, field_results, cross_checks


def evaluate(extractions: dict, *, key_fields=None, policy_version: str = "v1") -> DocIntelResult:
    """Full deterministic evaluation → verified or exception, with reasons. Key
    fields default to the versioned policy; pass `key_fields` only to override."""
    if key_fields is None:
        key_fields = key_fields_for(policy_version)
    profile, field_results, cross_checks = score_profile(extractions, policy_version=policy_version)

    reasons: list[str] = []
    for kf in key_fields:
        if kf not in field_results:
            reasons.append(f"missing_key_field:{kf}")
        elif not field_results[kf].is_reliable:
            reasons.append(f"low_confidence:{kf}")
    # An explicit cross-source mismatch on any key field is its own reason (the
    # mismatch also depresses that field's confidence, but we surface it plainly).
    for c in cross_checks:
        if c.field_name in key_fields and not c.matches:
            reasons.append(f"cross_source_mismatch:{c.field_name}:{c.source_a}!={c.source_b}")

    status = "exception" if reasons else "verified"
    # de-dup while preserving order
    reasons = list(dict.fromkeys(reasons))
    return DocIntelResult(
        status=status,
        profile=profile,
        field_confidence=field_results,
        cross_checks=cross_checks,
        exception_reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Agent entry point (I/O: reads docs, writes profile + KYC, audits one event)
# ---------------------------------------------------------------------------

def _which_docs_verified(extractions: dict, field_results: dict) -> dict:
    """A document is 'verified' when every field it contributed scored reliable."""
    verdict: dict[str, bool] = {}
    for doc_type, fields in extractions.items():
        verdict[doc_type] = all(
            field_results.get(f) is not None and field_results[f].is_reliable
            for f in fields
        )
    return verdict


def verify_documents(
    repository,
    audit: AuditStore,
    application_id: str,
    *,
    extract: ExtractFn,
    key_fields=None,
    policy_version: str = "v1",
) -> DocIntelResult:
    """Run Document Intelligence for an application: extract every uploaded
    document, ground per-field confidence, persist the verified profile + KYC
    confidence, and route verified / exception. Records one audited event."""
    application = repository.get(application_id)
    if application is None:
        raise ValueError(f"unknown application: {application_id!r}")

    docs = (application.features or {}).get("documents", {})
    uploaded = [d for d, rec in docs.items() if (rec or {}).get("uploaded")]
    if not uploaded:
        raise ValueError(f"no uploaded documents to verify for {application_id!r}")

    extractions = _extract_all(application_id, uploaded, extract)
    result = evaluate(extractions, key_fields=key_fields, policy_version=policy_version)

    # --- Persist the verified profile back onto the application ---
    feats = dict(application.features or {})
    for f, value in result.profile.items():
        if f in _APPLICANT_FIELDS:
            setattr(application.applicant, _APPLICANT_FIELDS[f], value)
        else:
            feats[f] = value
    # Keep the rules engine's `monthly_income` (gross) in sync when known.
    if "gross_monthly_income" in result.profile:
        feats["monthly_income"] = result.profile["gross_monthly_income"]

    # Mark each document's verified slot (#19 owns this; presence was set on upload).
    doc_verdict = _which_docs_verified(extractions, result.field_confidence)
    new_docs = {d: dict(rec or {}) for d, rec in docs.items()}
    for d, ok in doc_verdict.items():
        if d in new_docs:
            new_docs[d]["verified"] = ok
    feats["documents"] = new_docs
    application.features = feats

    # --- KYC record: grounded per-field confidence + aggregated flags ---
    application.kyc.field_confidence = [
        FieldConfidence(
            field_name=f,
            confidence=r.confidence,
            risk_flags=[flag.value for flag in r.risk_flags],
        )
        for f, r in result.field_confidence.items()
    ]
    application.kyc.risk_flags = sorted(
        {flag.value for r in result.field_confidence.values() for flag in r.risk_flags}
    )
    # VERIFIED on success; on exception leave PENDING (human review via KYC_EXCEPTION,
    # not a terminal FAILED).
    application.kyc.status = KycStatus.VERIFIED if result.status == "verified" else KycStatus.PENDING
    application.updated_at = _utcnow()
    repository.save(application)

    audit.append(
        application_id,
        EventType.AGENT_REASONING,
        {
            "agent": "document-intelligence",
            "status": result.status,
            "exception_reasons": result.exception_reasons,
            "profile": result.profile,
            "field_confidence": {
                f: {"confidence": r.confidence, "risk_flags": [x.value for x in r.risk_flags],
                    "is_reliable": r.is_reliable}
                for f, r in result.field_confidence.items()
            },
            "cross_checks": [
                {"field": c.field_name, "source_a": c.source_a, "source_b": c.source_b, "matches": c.matches}
                for c in result.cross_checks
            ],
            "documents_verified": doc_verdict,
            "policy_version": policy_version,
        },
        actor="agent:document-intelligence",
    )
    return result
