"""
Scripted demo (#31) — push a clean applicant, a high-debt one (referred at the
decision stage), and an out-of-scope lead (rejected at the lead gate) through the
live pipeline and print the outcomes + audit trail.

Gated by the pilot feature flag. Run inside the stack, e.g.:
    docker compose exec worker python -m lending.demo.run_demo
"""
from __future__ import annotations

import asyncio

from temporalio.client import Client

from lending.audit import AuditStore
from lending.los import Applicant, Application, ApplicationRepository, make_engine
from lending.settings import load_settings, require_pilot
from lending.workflow import TASK_QUEUE, LoanOriginationWorkflow

# A clean applicant who should sail to an offer.
CLEAN_FEATURES = {
    "age": 32, "monthly_income": 90_000, "monthly_obligations": 3_000, "cibil_score": 780,
    "employment_tenure_months": 60, "loan_amount_requested": 300_000, "loan_tenure_months": 36,
    "is_salaried": True, "has_cibil_record": True,
}
# A messy applicant (high existing debt) who passes the lead gate but is referred
# to a human at the decision stage.
EXCEPTION_FEATURES = {**CLEAN_FEATURES, "monthly_obligations": 60_000}
# Not a genuine personal-loan inquiry → rejected at the lead gate (out-of-scope),
# never reaches the decision stage.
OUT_OF_SCOPE_FEATURES = {
    "inquiry_text": "Hi, I just want to open a savings account and check fixed-deposit "
                    "rates. I am NOT looking for any loan.",
    "product_interest": "savings_account",
}


async def _run_one(client, repo, audit, label, features) -> None:
    app = Application(applicant=Applicant(full_name=f"{label} demo"), features=features)
    repo.save(app)
    result = await client.execute_workflow(
        LoanOriginationWorkflow.run,
        app.application_id,
        id=f"demo-{app.application_id}",
        task_queue=TASK_QUEUE,
    )

    print(f"\n=== [{label}] application {app.application_id} → final state: {result} ===")
    # The full per-state trail, straight from the audit store (#6).
    for event in audit.reconstruct(app.application_id):
        if event.event_type == "state_transition":
            p = event.payload
            print(f"  {event.seq:>3}. state       {p['from']:>22}  →  {p['to']}")
        elif event.event_type == "decision":
            p = event.payload
            print(f"  {event.seq:>3}. decision    {p['disposition'].upper()}  "
                  f"band={p.get('band')} score={p.get('score')} reasons={p.get('reason_codes')}")
            if p.get("explanation"):
                print(f"       explanation: {p['explanation']}")
        elif event.event_type == "agent_reasoning":
            p = event.payload
            print(f"  {event.seq:>3}. agent       {p.get('agent')}  status={p.get('status')} "
                  f"reason={p.get('reason_code')} confidence={p.get('confidence')}")
            if p.get("reasoning"):
                print(f"       reasoning: {p['reasoning']}")
        else:
            print(f"  {event.seq:>3}. {event.event_type}")


async def main() -> None:
    settings = load_settings()
    require_pilot(settings)  # feature-flag gate
    engine = make_engine(settings.database_url)
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    client = await Client.connect(settings.temporal_address)

    await _run_one(client, repo, audit, "clean", CLEAN_FEATURES)
    await _run_one(client, repo, audit, "exception", EXCEPTION_FEATURES)
    await _run_one(client, repo, audit, "out_of_scope", OUT_OF_SCOPE_FEATURES)


if __name__ == "__main__":
    asyncio.run(main())
