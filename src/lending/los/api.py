"""
Intake API (#2) — create/read the LOS application aggregate.

  POST /applications                    → create an application (returns aggregate)
  GET  /applications/{id}               → return the persisted aggregate, 404 if absent
  GET  /applications/{id}/explanation   → reason codes + rendered adverse-action text (#17)

Pydantic validates the create payload; a malformed body yields a 422 automatically.
The repository is injected so tests can wire an in-memory engine.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from lending.explanation import build_context, render

from .repository import ApplicationRepository, make_engine
from .schema import Application, ApplicationCreate


def create_app(repository: ApplicationRepository | None = None) -> FastAPI:
    repo = repository or ApplicationRepository(make_engine())
    app = FastAPI(title="AI-Native Lending — LOS Intake")

    def get_repo() -> ApplicationRepository:
        return repo

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

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

    @app.get("/applications/{application_id}", response_model=Application)
    def read_application(
        application_id: str,
        repo: ApplicationRepository = Depends(get_repo),
    ) -> Application:
        application = repo.get(application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="application not found")
        return application

    @app.get("/applications/{application_id}/explanation")
    def read_explanation(
        application_id: str,
        language: str = "en",
        repo: ApplicationRepository = Depends(get_repo),
    ) -> dict:
        application = repo.get(application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="application not found")

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

    return app
