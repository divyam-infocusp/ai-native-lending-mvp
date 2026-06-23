"""
Tests for the multi-turn Onboarding Copilot (#22).

Drives a genuine multi-turn conversation with a scripted fake `reason` (no live
Gemini): autofill (don't re-ask known fields), field accumulation across turns
with durable memory, deterministic completeness, and per-turn auditing.
"""
from langgraph.checkpoint.memory import MemorySaver

from lending.agents import OnboardingCopilot, missing_fields, register_document
from lending.agents.onboarding import REQUIRED_DOCUMENTS
from lending.audit import AuditStore
from lending.los import Applicant, Application, ApplicationRepository, make_engine


def scripted(outputs):
    """A ReasonFn that returns the next scripted OnboardingTurn dict per call."""
    it = iter(outputs)
    return lambda context, tool_result: next(it)


def _stores():
    engine = make_engine()
    return ApplicationRepository(engine), AuditStore(engine)


def _seed(repo, full_name="Priya Sharma", **features) -> str:
    app = Application(applicant=Applicant(full_name=full_name), features=features)
    repo.save(app)
    return app.application_id


def _turn(extracted=None, msg="...", reasoning="") -> dict:
    return {"extracted": extracted or {}, "assistant_message": msg, "reasoning": reasoning}


# ---------------------------------------------------------------------------
# Full multi-turn conversation → complete application
# ---------------------------------------------------------------------------

def test_multi_turn_collects_until_complete():
    repo, audit = _stores()
    app_id = _seed(repo)  # only full_name known

    copilot = OnboardingCopilot(
        reason=scripted([
            _turn(msg="Hi Priya! What's your date of birth, PAN and Aadhaar?"),
            _turn({"date_of_birth": "1994-02-11", "pan": "ABCDE1234F", "aadhaar": "234567890123"},
                  "Thanks. Your mobile and current address?"),
            _turn({"mobile": "9876543210", "current_address": "12 MG Road, Pune 411001"},
                  "Employment type, employer, how long there, and monthly income?"),
            _turn({"employment_type": "salaried", "employer_name": "Acme Corp",
                   "employment_tenure_months": 48, "monthly_income": 90000},
                  "How much do you need, over how many months, and for what?"),
            _turn({"loan_amount_requested": 300000, "loan_tenure_months": 36, "loan_purpose": "home renovation"},
                  "Please upload identity & address proof, salary slips, bank statement, and Form 16."),
            _turn(msg="Thanks — I see your documents. Submitting your application!"),
        ]),
        checkpointer=MemorySaver(),
    )

    r1 = copilot.turn(repo, audit, app_id)                       # greeting (no user msg)
    assert r1.complete is False
    r2 = copilot.turn(repo, audit, app_id, "born 11 Feb 1994, PAN ABCDE1234F, Aadhaar 2345 6789 0123")
    assert r2.complete is False
    r3 = copilot.turn(repo, audit, app_id, "mobile 9876543210, 12 MG Road Pune")
    assert r3.complete is False
    r4 = copilot.turn(repo, audit, app_id, "salaried at Acme for 4 years, earn 90k")
    assert r4.complete is False
    r5 = copilot.turn(repo, audit, app_id, "3 lakh for 36 months, home renovation")
    # all 13 data fields collected, but documents not yet uploaded
    assert r5.complete is False
    assert r5.missing == [f"document:{d}" for d in REQUIRED_DOCUMENTS]

    # Documents are uploaded via a real action (UI/upload endpoint), not from chat.
    for doc in REQUIRED_DOCUMENTS:
        register_document(repo, app_id, doc, reference=f"s3://bucket/{app_id}/{doc}.pdf")

    r6 = copilot.turn(repo, audit, app_id, "I've uploaded everything")
    assert r6.complete is True
    assert r6.missing == []

    # On completion the full filled set is returned for the UI's review screen
    assert r6.collected["full_name"] == "Priya Sharma"
    assert r6.collected["pan"] == "ABCDE1234F"
    assert r6.collected["employer_name"] == "Acme Corp"
    assert r6.collected["loan_purpose"] == "home renovation"

    # Fields accumulated + persisted; documents carry presence + a verified slot (#19)
    app = repo.get(app_id)
    assert app.applicant.aadhaar == "234567890123"
    assert app.features["documents"]["form16"]["uploaded"] is True
    assert app.features["documents"]["form16"]["verified"] is None

    events = [e for e in audit.reconstruct(app_id) if e.event_type == "agent_reasoning"]
    assert len(events) == 6
    assert events[-1].payload["complete"] is True


# ---------------------------------------------------------------------------
# Autofill: don't ask for what's already known
# ---------------------------------------------------------------------------

def test_autofill_excludes_known_fields_from_missing():
    repo, audit = _stores()
    # PAN + Aadhaar + mobile + income already on file from the lead/eKYC
    app_id = _seed(repo, full_name="Priya")
    app = repo.get(app_id)
    app.applicant.pan = "ABCDE1234F"
    app.applicant.aadhaar = "234567890123"
    app.applicant.mobile = "9876543210"
    app.features = {"monthly_income": 90000}
    repo.save(app)

    missing = missing_fields(repo.get(app_id))
    # known fields are not re-asked
    for known in ("full_name", "pan", "aadhaar", "mobile", "monthly_income"):
        assert known not in missing
    # still needs these
    assert "date_of_birth" in missing
    assert "employer_name" in missing
    assert "loan_purpose" in missing
    assert "document:salary_slips" in missing


# ---------------------------------------------------------------------------
# Completeness is deterministic, not the model's say-so
# ---------------------------------------------------------------------------

def test_completeness_is_deterministic_not_model_claim():
    repo, audit = _stores()
    app_id = _seed(repo)
    # The model claims "all set" but provides nothing → still NOT complete.
    copilot = OnboardingCopilot(
        reason=scripted([_turn({}, "All set, you're done!")]),
        checkpointer=MemorySaver(),
    )
    r = copilot.turn(repo, audit, app_id, "I'm finished")
    assert r.complete is False
    assert "pan" in r.missing


# ---------------------------------------------------------------------------
# Documents gate completeness even when all data fields are present
# ---------------------------------------------------------------------------

def test_documents_required_for_completeness():
    repo, audit = _stores()
    app_id = _seed(repo, full_name="Priya")
    # all data fields present, documents still missing
    app = repo.get(app_id)
    app.applicant.pan = "ABCDE1234F"
    app.applicant.aadhaar = "234567890123"
    app.applicant.date_of_birth = "1994-02-11"
    app.applicant.mobile = "9876543210"
    app.applicant.current_address = "12 MG Road, Pune"
    app.features = {
        "monthly_income": 90000, "employment_type": "salaried",
        "employer_name": "Acme Corp", "employment_tenure_months": 48,
        "loan_amount_requested": 300000, "loan_tenure_months": 36,
        "loan_purpose": "home renovation",
    }
    repo.save(app)

    copilot = OnboardingCopilot(
        reason=scripted([
            _turn(msg="Please upload identity & address proof, salary slips, bank statement, Form 16."),
            _turn(msg="Got your documents — done!"),
        ]),
        checkpointer=MemorySaver(),
    )
    r1 = copilot.turn(repo, audit, app_id)
    assert r1.complete is False
    assert r1.missing == [
        "document:identity_proof", "document:address_proof",
        "document:salary_slips", "document:form16",
    ]

    # Real uploads via register_document (not the chat) clear the checklist.
    for doc in REQUIRED_DOCUMENTS:
        register_document(repo, app_id, doc)
    r2 = copilot.turn(repo, audit, app_id, "uploaded all")
    assert r2.complete is True
