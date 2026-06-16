"""
Intake API (#2) — create/read the LOS application aggregate.

  POST /applications        → create an application, returns the persisted aggregate
  GET  /applications/{id}   → return the persisted aggregate, 404 if absent

Pydantic validates the create payload; a malformed body yields a 422 automatically.
The repository is injected so tests can wire an in-memory engine.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from .repository import ApplicationRepository, make_engine
from .schema import Application, ApplicationCreate


def create_app(repository: ApplicationRepository | None = None) -> FastAPI:
    repo = repository or ApplicationRepository(make_engine())
    app = FastAPI(title="AI-Native Lending — LOS Intake")

    def get_repo() -> ApplicationRepository:
        return repo

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

    return app
