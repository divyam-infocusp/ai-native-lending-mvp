"""
Decision QA Agent + offer delivery (#23, Step 6).

After the deterministic decision-of-record is made (#18), this agent:
  1. QA-CHECKS the decision — well-formed, version-stamped, and (for any non-
     approve) carries reason codes + an adverse-action explanation. This enforces
     the "100% of decisions carry adverse-action reasons" invariant.
  2. For an APPROVE: prices the offer (#12), assembles a real-world **offer letter**
     (amount / rate / tenure / EMI + processing fee + GST + totals + validity +
     terms), persists it, sends a notification (#11) and routes to e-sign (#11) →
     OFFER_GENERATED.

Borderline / policy-hit cases are routed to REFERRED by the decision engine (#18)
*before* this step (income-sensitivity + policy ESCALATE → REFER), so an approved
decision reaching offer delivery has already cleared that bar; the QA check is the
backstop that proves it.

Offer-letter terms are reviewed templates + versioned policy values (§16.9/§16.11),
never free-form text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from lending.adapters import request_signature, send_notification
from lending.audit import AuditStore, EventType
from lending.policy import PRICING_POLICY
from lending.pricing import Offer, price
from lending.rules_engine import ApplicantFeatures
from lending.scorecard import RiskBand

# Standard sanction-letter clauses (reviewed template text, §16.11 — not LLM-authored).
OFFER_TERMS = [
    "Interest is charged at a fixed annual rate on a reducing-balance basis.",
    "EMIs are due on the same day each month; late payment attracts a penalty per the schedule.",
    "Prepayment / foreclosure is permitted after 6 EMIs; foreclosure charges apply per policy.",
    "This sanction is subject to successful e-signature and is valid only within the validity period.",
]

_ENGINE_FIELDS = (
    "age", "monthly_income", "monthly_obligations", "cibil_score",
    "employment_tenure_months", "loan_amount_requested", "loan_tenure_months",
    "is_salaried", "has_cibil_record",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class QAResult:
    ok: bool
    issues: list = field(default_factory=list)


@dataclass(frozen=True)
class DeliveryResult:
    status: str                          # "delivered" | "blocked"
    offer_letter: Optional[dict] = None
    issues: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Decision QA — invariants on the decision-of-record
# ---------------------------------------------------------------------------

def qa_check_decision(decision) -> QAResult:
    """Verify the decision-of-record is well-formed and compliant. A non-approve
    decision MUST carry reason codes + an adverse-action explanation."""
    issues: list[str] = []
    if decision is None:
        return QAResult(False, ["no decision recorded"])
    if decision.version_set is None:
        issues.append("missing version_set (not reproducible)")
    disposition = decision.disposition.value
    if disposition in ("decline", "refer"):
        if not decision.reason_codes:
            issues.append(f"{disposition} decision carries no reason codes")
        if not decision.explanation:
            issues.append(f"{disposition} decision carries no adverse-action explanation")
    return QAResult(not issues, issues)


# ---------------------------------------------------------------------------
# Offer letter assembly (real-world sanction-letter components)
# ---------------------------------------------------------------------------

def assemble_offer_letter(offer: Offer, *, now: datetime, pricing_version: str = "v1") -> dict:
    """Wrap priced terms into a sanction-letter-shaped offer: fees, totals, the
    net disbursal, a validity date, and the standard terms."""
    cfg = PRICING_POLICY[pricing_version]
    processing_fee = round(offer.amount * cfg["processing_fee_pct"], 2)
    gst = round(processing_fee * cfg["gst_pct"], 2)
    total_payable = round(offer.emi * offer.tenure, 2)
    total_interest = round(total_payable - offer.amount, 2)
    net_disbursal = round(offer.amount - processing_fee - gst, 2)
    valid_until = now + timedelta(days=cfg["offer_validity_days"])
    return {
        "sanctioned_amount": offer.amount,
        "interest_rate": offer.rate,
        "rate_type": "fixed",
        "tenure_months": offer.tenure,
        "emi": offer.emi,
        "processing_fee": processing_fee,
        "gst_on_fee": gst,
        "net_disbursal_amount": net_disbursal,
        "total_interest_payable": total_interest,
        "total_amount_payable": total_payable,
        "validity_days": cfg["offer_validity_days"],
        "valid_until": valid_until.isoformat(),
        "terms": list(OFFER_TERMS),
        "pricing_version": pricing_version,
    }


# ---------------------------------------------------------------------------
# Offer delivery — QA, price, assemble, notify, e-sign
# ---------------------------------------------------------------------------

def deliver_offer(
    repository,
    audit: AuditStore,
    application_id: str,
    *,
    notify_harness,
    esign_harness,
    now: Optional[datetime] = None,
    pricing_version: str = "v1",
) -> DeliveryResult:
    """Run QA on the decision, price + assemble the offer letter, persist it, send
    a notification, and route to e-sign. Used at APPROVED → OFFER_GENERATED."""
    now = now or _utcnow()
    application = repository.get(application_id)
    if application is None:
        raise ValueError(f"unknown application: {application_id!r}")

    decision = application.decision
    qa = qa_check_decision(decision)

    audit.append(
        application_id, EventType.AGENT_REASONING,
        {"agent": "decision-qa", "qa_ok": qa.ok, "issues": qa.issues,
         "disposition": decision.disposition.value if decision else None},
        actor="agent:decision-qa",
    )
    if not qa.ok:
        return DeliveryResult("blocked", issues=qa.issues)
    if decision.disposition.value != "approve":
        # Only approvals get an offer; non-approve delivery is the notification path.
        return DeliveryResult("blocked", issues=[f"not an approval: {decision.disposition.value}"])

    # Price the offer from the assembled features (#20) + the decision's band.
    feats = application.features or {}
    features = ApplicantFeatures(**{k: feats[k] for k in _ENGINE_FIELDS})
    offer = price(features, RiskBand(decision.band), pricing_version)
    letter = assemble_offer_letter(offer, now=now, pricing_version=pricing_version)

    # Persist the offer letter on the application.
    new_feats = dict(feats)
    new_feats["offer_letter"] = letter
    application.features = new_feats
    application.updated_at = now
    repository.save(application)

    # Send the offer notification (#11) and route to e-sign (#11), both idempotent.
    send_notification(notify_harness, application_id, notif_type="offer_ready", channel="email",
                      payload={"sanctioned_amount": letter["sanctioned_amount"], "emi": letter["emi"]})
    envelope = request_signature(esign_harness, application_id, document_ref="offer_letter")

    audit.append(
        application_id, EventType.AGENT_REASONING,
        {"agent": "decision-qa", "action": "offer_delivered", "offer_letter": letter,
         "esign_envelope": envelope.envelope_id},
        actor="agent:decision-qa",
    )
    return DeliveryResult("delivered", offer_letter=letter)
