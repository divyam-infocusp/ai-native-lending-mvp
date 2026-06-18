"""
Tests for the Lead Qualification Agent (#21, §16.7).

Uses an injected fake `reason` (no live Gemini / no API key) to exercise the
three outcomes: in-segment → qualified; out-of-segment → declined-early with an
audited reason; uncertain → escalated to manual review. The live Gemini path is
verified separately with GOOGLE_API_KEY set.
"""
from lending.agents import SegmentFit, qualify_lead
from lending.agents.lead_qualification import build_lead_qualification_agent
from lending.audit import AuditStore
from lending.los import Applicant, Application, ApplicationRepository, make_engine


def fake_reason(output: dict):
    """Return a ReasonFn that always yields the given classification dict."""
    return lambda context, tool_result: output


def _seed(repo, **features) -> str:
    app = Application(applicant=Applicant(full_name="Lead Person"), features=features)
    repo.save(app)
    return app.application_id


def _stores():
    engine = make_engine()
    return ApplicationRepository(engine), AuditStore(engine)


# ---------------------------------------------------------------------------
# In-segment → qualified
# ---------------------------------------------------------------------------

def test_in_segment_qualifies():
    repo, audit = _stores()
    app_id = _seed(repo, is_salaried=True, has_cibil_record=True)
    result = qualify_lead(repo, audit, app_id, reason=fake_reason({
        "segment_fit": "in_segment", "employment_type": "salaried",
        "has_credit_history": True, "reason_code": "IN_SEGMENT",
        "confidence": 0.95, "reasoning": "salaried with credit history",
    }))
    assert result.status == "qualified"
    assert result.reason_code == "IN_SEGMENT"


# ---------------------------------------------------------------------------
# Light triage: employment type / unknown credit are NOT rejected at the gate —
# they proceed and are decided downstream (deterministically, with reasons).
# ---------------------------------------------------------------------------

def test_self_employed_still_proceeds():
    repo, audit = _stores()
    app_id = _seed(repo, is_salaried=False)
    result = qualify_lead(repo, audit, app_id, reason=fake_reason({
        "segment_fit": "in_segment", "employment_type": "self_employed",
        "has_credit_history": None, "reason_code": "PROCEED",
        "confidence": 0.9, "reasoning": "plausible personal-loan inquiry",
    }))
    # Not rejected at the gate — the deterministic engine handles employment downstream.
    assert result.status == "qualified"


def test_unknown_credit_proceeds():
    repo, audit = _stores()
    app_id = _seed(repo, is_salaried=True)  # credit status genuinely unknown at lead stage
    result = qualify_lead(repo, audit, app_id, reason=fake_reason({
        "segment_fit": "in_segment", "employment_type": "salaried",
        "has_credit_history": None, "reason_code": "PROCEED",
        "confidence": 0.92, "reasoning": "credit status assessed downstream",
    }))
    assert result.status == "qualified"


# ---------------------------------------------------------------------------
# Out-of-scope (not a genuine loan inquiry) → declined-early, reason audited
# ---------------------------------------------------------------------------

def test_out_of_scope_declined_early_with_audited_reason():
    repo, audit = _stores()
    app_id = _seed(repo)
    result = qualify_lead(repo, audit, app_id, reason=fake_reason({
        "segment_fit": "out_of_segment", "employment_type": "unknown",
        "has_credit_history": None, "reason_code": "OUT_OF_SCOPE_NOT_A_LOAN",
        "confidence": 0.95, "reasoning": "asking about a credit card, not a loan",
    }))
    assert result.status == "declined_early"
    assert result.reason_code == "OUT_OF_SCOPE_NOT_A_LOAN"
    # the LLM's reasoning is surfaced on the result and recorded in the audit event
    assert result.reasoning == "asking about a credit card, not a loan"
    trail = audit.reconstruct(app_id)
    events = [e for e in trail if e.event_type == "agent_reasoning"]
    assert len(events) == 1
    assert events[0].payload["status"] == "declined_early"
    assert events[0].payload["reason_code"] == "OUT_OF_SCOPE_NOT_A_LOAN"
    assert events[0].payload["reasoning"] == "asking about a credit card, not a loan"


# ---------------------------------------------------------------------------
# Uncertain / low confidence → escalate to human (never auto-decline)
# ---------------------------------------------------------------------------

def test_uncertain_escalates_to_manual_review():
    repo, audit = _stores()
    app_id = _seed(repo, is_salaried=True)
    result = qualify_lead(repo, audit, app_id, reason=fake_reason({
        "segment_fit": "uncertain", "employment_type": "unknown",
        "has_credit_history": False, "reason_code": "INSUFFICIENT_INFO",
        "confidence": 0.95, "reasoning": "sparse data",  # high self-report, but uncertain → escalate
    }))
    assert result.status == "manual_review"


def test_low_confidence_in_segment_escalates():
    repo, audit = _stores()
    app_id = _seed(repo, is_salaried=True, has_cibil_record=True)
    result = qualify_lead(repo, audit, app_id, reason=fake_reason({
        "segment_fit": "in_segment", "employment_type": "salaried",
        "has_credit_history": True, "reason_code": "IN_SEGMENT",
        "confidence": 0.40, "reasoning": "weak signal",
    }))
    assert result.status == "manual_review"   # below threshold → human, not auto-qualify


# ---------------------------------------------------------------------------
# Schema enforcement: malformed model output is rejected + retried
# ---------------------------------------------------------------------------

def test_malformed_then_valid_is_retried():
    calls = {"n": 0}

    def flaky(context, tool_result):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"segment_fit": "in_segment"}  # missing required fields → invalid
        return {
            "segment_fit": "in_segment", "employment_type": "salaried",
            "has_credit_history": True, "reason_code": "IN_SEGMENT",
            "confidence": 0.9, "reasoning": "ok",
        }

    repo, audit = _stores()
    app_id = _seed(repo, is_salaried=True, has_cibil_record=True)
    result = qualify_lead(repo, audit, app_id, reason=flaky)
    assert calls["n"] == 2
    assert result.status == "qualified"
