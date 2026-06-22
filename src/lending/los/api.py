"""
Origination API (#2 intake + #36 control) — the HTTP surface the frontends drive.

Intake (#2):
  POST /applications                      → create an application (returns aggregate)
  GET  /applications/{id}                 → the persisted aggregate (incl. decision + offer)
  GET  /applications/{id}/explanation     → reason codes + adverse-action text (#17)

Control + read (#36), for the applicant journey (#29) + pipeline viewer (#30):
  POST /applications/{id}/onboarding/message → one Onboarding Copilot turn (#22)
  POST /applications/{id}/consent            → capture Layer-1 consent (#8)
  POST /applications/{id}/documents          → register an uploaded document (#19)
  POST /applications/{id}/start              → start the Temporal workflow (#13)
  GET  /applications/{id}/audit              → reconstructed audit trail (#6)

Dependencies are injected so tests run against a mock backend (no live Temporal /
Gemini): pass `audit`, `copilot`, and `workflow_starter`. Defaults wire the real
ones (audit on the repo's engine, a Gemini copilot, a Temporal starter).
"""
from __future__ import annotations

import inspect
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from lending.audit import AuditStore
from lending.explanation import build_context, render

from .repository import ApplicationRepository, make_engine
from .schema import Application, ApplicationCreate


class OnboardingMessageIn(BaseModel):
    message: Optional[str] = None      # None on the first (greeting) turn


class ConsentIn(BaseModel):
    purpose: str


class DocumentIn(BaseModel):
    doc_type: str
    reference: Optional[str] = None


async def _default_workflow_starter(application_id: str) -> str:
    """Start the origination workflow on the configured Temporal server."""
    from temporalio.client import Client

    from lending.settings import load_settings
    from lending.workflow import TASK_QUEUE, LoanOriginationWorkflow

    settings = load_settings()
    client = await Client.connect(settings.temporal_address)
    handle = await client.start_workflow(
        LoanOriginationWorkflow.run,
        application_id,
        id=f"app-{application_id}",
        task_queue=TASK_QUEUE,
    )
    return handle.id


def create_app(
    repository: ApplicationRepository | None = None,
    *,
    audit: AuditStore | None = None,
    copilot=None,
    workflow_starter=None,
) -> FastAPI:
    repo = repository or ApplicationRepository(make_engine())
    audit_store = audit or AuditStore(repo._engine)
    starter = workflow_starter or _default_workflow_starter

    def get_copilot():
        # Lazily build the default (Gemini) copilot so importing the API never
        # drags the agent/LangGraph stack in unless onboarding is actually used.
        nonlocal copilot
        if copilot is None:
            from lending.agents import OnboardingCopilot

            copilot = OnboardingCopilot()
        return copilot

    app = FastAPI(title="AI-Native Lending — Origination API")

    def get_repo() -> ApplicationRepository:
        return repo

    def _require(application_id: str) -> Application:
        application = repo.get(application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="application not found")
        return application

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # ---- Intake (#2) -------------------------------------------------------

    @app.post("/applications", status_code=201, response_model=Application)
    def create_application(
        payload: ApplicationCreate,
        repo: ApplicationRepository = Depends(get_repo),
    ) -> Application:
        application = Application(
            applicant=payload.applicant,
            features=payload.features,
            consent=payload.consent,
        )
        return repo.save(application)

    @app.get("/applications")
    def list_applications() -> dict:
        """Summaries of all applications, newest first (pipeline viewer #30)."""
        items = [
            {
                "application_id": a.application_id,
                "applicant_name": a.applicant.full_name,
                "status": a.status.value,
                "workflow_state": a.workflow_state,
                "disposition": a.decision.disposition.value if a.decision else None,
                "updated_at": a.updated_at.isoformat(),
            }
            for a in repo.list_all()
        ]
        return {"applications": items}

    @app.get("/applications/{application_id}", response_model=Application)
    def read_application(application_id: str) -> Application:
        return _require(application_id)

    @app.get("/applications/{application_id}/explanation")
    def read_explanation(application_id: str, language: str = "en") -> dict:
        application = _require(application_id)
        decision = application.decision
        reason_codes = list(decision.reason_codes) if decision else []
        rules_version = (decision.rules_version if decision else None) or "v1"
        context = build_context(application.features, rules_version)
        rendered = render(reason_codes, language, context)
        return {
            "application_id": application_id,
            "language": language,
            "reason_codes": reason_codes,
            "text": rendered.text,
        }

    # ---- Control + read (#36) ---------------------------------------------

    @app.post("/applications/{application_id}/onboarding/message")
    def onboarding_message(application_id: str, body: OnboardingMessageIn) -> dict:
        _require(application_id)
        resp = get_copilot().turn(repo, audit_store, application_id, body.message)
        return {
            "application_id": application_id,
            "assistant_message": resp.assistant_message,
            "complete": resp.complete,
            "missing": resp.missing,
            "collected": resp.collected,
        }

    @app.post("/applications/{application_id}/consent")
    def capture_consent(application_id: str, body: ConsentIn) -> dict:
        from lending.consent import capture_authorization

        application = _require(application_id)
        capture_authorization(application, body.purpose, audit_store)
        repo.save(application)
        return {"application_id": application_id, "purpose": body.purpose, "status": "active"}

    @app.post("/applications/{application_id}/documents", status_code=201)
    def upload_document(application_id: str, body: DocumentIn) -> dict:
        from lending.agents import register_document

        _require(application_id)
        try:
            register_document(repo, application_id, body.doc_type, reference=body.reference)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        return {"application_id": application_id, "doc_type": body.doc_type, "uploaded": True}

    @app.post("/applications/{application_id}/start", status_code=202)
    async def start_application(application_id: str) -> dict:
        _require(application_id)
        run_ref = starter(application_id)
        if inspect.isawaitable(run_ref):
            run_ref = await run_ref
        return {"application_id": application_id, "workflow_run": run_ref, "status": "started"}

    @app.get("/applications/{application_id}/audit")
    def read_audit(application_id: str) -> dict:
        _require(application_id)
        events = audit_store.reconstruct(application_id)
        return {
            "application_id": application_id,
            "events": [e.model_dump(mode="json") for e in events],
        }

    return app
