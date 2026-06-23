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

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

from lending.audit import AuditStore
from lending.auth import AuthError, AuthService, User
from lending.explanation import build_context, render

from .repository import ApplicationRepository, make_engine
from .schema import Application, ApplicationCreate


class OnboardingMessageIn(BaseModel):
    message: Optional[str] = None      # None on the first (greeting) turn


class DetailsIn(BaseModel):
    fields: dict


class ConsentIn(BaseModel):
    purpose: str


class ResolveIn(BaseModel):
    to_state: str
    reason_code: str
    note: Optional[str] = None      # required human justification (validated below)


class DocumentIn(BaseModel):
    doc_type: str
    reference: Optional[str] = None


class RegisterIn(BaseModel):
    email: str
    password: str
    name: str = ""
    role: str = "applicant"


class LoginIn(BaseModel):
    email: str
    password: str


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


async def _default_resolve_signal(application_id: str, resolution: dict) -> None:
    """Signal the parked workflow with a reviewer's resolution (#15)."""
    from temporalio.client import Client

    from lending.settings import load_settings
    from lending.workflow import LoanOriginationWorkflow

    client = await Client.connect(load_settings().temporal_address)
    handle = client.get_workflow_handle(f"app-{application_id}")
    await handle.signal(LoanOriginationWorkflow.resolve, resolution)


def create_app(
    repository: ApplicationRepository | None = None,
    *,
    audit: AuditStore | None = None,
    copilot=None,
    workflow_starter=None,
    resolve_signal=None,
    auth_service: AuthService | None = None,
    document_store=None,
) -> FastAPI:
    repo = repository or ApplicationRepository(make_engine())
    audit_store = audit or AuditStore(repo._engine)
    starter = workflow_starter or _default_workflow_starter
    resolver = resolve_signal or _default_resolve_signal
    if document_store is None:
        from lending.storage import make_document_store

        document_store = make_document_store()
    if auth_service is None:
        from lending.settings import load_settings

        auth_service = AuthService(repo._engine, load_settings().auth_secret)

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

    # ---- Auth (#38) --------------------------------------------------------

    def current_user(authorization: Optional[str] = Header(default=None)) -> User:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        user = auth_service.user_from_token(authorization.split(" ", 1)[1])
        if user is None:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        return user

    def require_authorized(application_id: str, user: User) -> Application:
        """Load the application + enforce access: underwriters see all; an
        applicant only their own."""
        application = repo.get(application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="application not found")
        if user.role != "underwriter" and application.owner_user_id != user.user_id:
            raise HTTPException(status_code=403, detail="forbidden")
        return application

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/policy")
    def read_policy(version: str = "v1", user: User = Depends(current_user)) -> dict:
        """The active lending policy in human-readable form (#16.9) — eligibility
        rules + risk bands + pricing + document thresholds, for the Ops Console."""
        from lending.policy_view import build_policy_view

        try:
            return build_policy_view(version)
        except ValueError as err:
            raise HTTPException(status_code=404, detail=str(err))

    @app.post("/auth/register")
    def register(body: RegisterIn) -> dict:
        try:
            user, token = auth_service.register(body.email, body.password, body.name, body.role)
        except AuthError as err:
            raise HTTPException(status_code=400, detail=str(err))
        return {"token": token, "user": user.public()}

    @app.post("/auth/login")
    def login(body: LoginIn) -> dict:
        try:
            user, token = auth_service.login(body.email, body.password)
        except AuthError as err:
            raise HTTPException(status_code=401, detail=str(err))
        return {"token": token, "user": user.public()}

    @app.get("/auth/me")
    def me(user: User = Depends(current_user)) -> dict:
        return user.public()

    # ---- Intake (#2) -------------------------------------------------------

    @app.post("/applications", status_code=201, response_model=Application)
    def create_application(
        payload: ApplicationCreate,
        user: User = Depends(current_user),
    ) -> Application:
        # Applications are authored by applicants only (#38). Underwriters review;
        # they must never become an application's owner — otherwise the app is
        # invisible to every applicant's owner-scoped list. Enforced here (not just
        # in the client route guard) so a crossed/stale token can't create an
        # orphaned, underwriter-owned application.
        if user.role != "applicant":
            raise HTTPException(status_code=403, detail="only applicants can create applications")
        # Demo scenario tag (optional) — validate it so a typo can't silently mean
        # "clean". Real intake ignores this field.
        scenario = (payload.features or {}).get("demo_scenario")
        if scenario is not None:
            from lending.adapters.demo_scenarios import DEMO_SCENARIOS

            if scenario not in DEMO_SCENARIOS:
                raise HTTPException(status_code=422, detail=f"unknown demo_scenario: {scenario!r}")
        application = Application(
            applicant=payload.applicant,
            features=payload.features,
            consent=payload.consent,
            owner_user_id=user.user_id,        # tracked to the creating applicant (#38)
        )
        return repo.save(application)

    @app.get("/applications")
    def list_applications(user: User = Depends(current_user)) -> dict:
        """Summaries, newest first. Underwriters see all; applicants see only their own."""
        owner = None if user.role == "underwriter" else user.user_id
        items = [
            {
                "application_id": a.application_id,
                "applicant_name": a.applicant.full_name,
                "status": a.status.value,
                "workflow_state": a.workflow_state,
                "disposition": a.decision.disposition.value if a.decision else None,
                "updated_at": a.updated_at.isoformat(),
            }
            for a in repo.list_all(owner)
        ]
        return {"applications": items}

    @app.get("/applications/{application_id}", response_model=Application)
    def read_application(application_id: str, user: User = Depends(current_user)) -> Application:
        return require_authorized(application_id, user)

    @app.get("/applications/{application_id}/explanation")
    def read_explanation(application_id: str, language: str = "en",
                         user: User = Depends(current_user)) -> dict:
        application = require_authorized(application_id, user)
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
    def onboarding_message(application_id: str, body: OnboardingMessageIn,
                          user: User = Depends(current_user)) -> dict:
        require_authorized(application_id, user)
        resp = get_copilot().turn(repo, audit_store, application_id, body.message)
        return {
            "application_id": application_id,
            "assistant_message": resp.assistant_message,
            "complete": resp.complete,
            "missing": resp.missing,
            "collected": resp.collected,
        }

    @app.post("/applications/{application_id}/details")
    def submit_details(application_id: str, body: DetailsIn,
                      user: User = Depends(current_user)) -> dict:
        """Form-fill alternative to the copilot (#42): apply structured details
        directly and return completeness."""
        from lending.agents import apply_details
        from lending.agents.onboarding import missing_fields

        require_authorized(application_id, user)
        try:
            application = apply_details(repo, application_id, body.fields)
        except Exception as err:  # pydantic coercion / validation
            raise HTTPException(status_code=422, detail=str(err))
        remaining = missing_fields(application)
        return {"application_id": application_id, "complete": not remaining, "missing": remaining}

    @app.post("/applications/{application_id}/consent")
    def capture_consent(application_id: str, body: ConsentIn,
                       user: User = Depends(current_user)) -> dict:
        from lending.consent import capture_authorization

        application = require_authorized(application_id, user)
        capture_authorization(application, body.purpose, audit_store)
        repo.save(application)
        return {"application_id": application_id, "purpose": body.purpose, "status": "active"}

    @app.post("/applications/{application_id}/documents", status_code=201)
    def upload_document(application_id: str, body: DocumentIn,
                       user: User = Depends(current_user)) -> dict:
        from lending.agents import register_document

        require_authorized(application_id, user)
        try:
            register_document(repo, application_id, body.doc_type, reference=body.reference)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        return {"application_id": application_id, "doc_type": body.doc_type, "uploaded": True}

    @app.post("/applications/{application_id}/documents/file", status_code=201)
    async def upload_document_file(
        application_id: str,
        doc_type: str = Form(...),
        file: UploadFile = File(...),
        user: User = Depends(current_user),
    ) -> dict:
        """Real file upload (#9, Phase A): store the actual bytes so the OCR/LLM
        extractor can read them, and register the document's presence with the
        storage reference."""
        from lending.agents import register_document

        require_authorized(application_id, user)
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty file")
        reference = document_store.put(
            application_id, doc_type, data, file.content_type or "application/octet-stream"
        )
        try:
            register_document(repo, application_id, doc_type, reference=reference)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        return {"application_id": application_id, "doc_type": doc_type,
                "uploaded": True, "reference": reference, "bytes": len(data)}

    @app.get("/applications/{application_id}/documents/{doc_type}/file")
    async def get_document_file(
        application_id: str,
        doc_type: str,
        user: User = Depends(current_user),
    ) -> Response:
        """Serve the uploaded document bytes for underwriter review (#19).

        Only the application owner (applicant) or any underwriter can fetch
        documents — the same `require_authorized` gate as other read paths.
        Returns the raw bytes with the stored content-type so the browser can
        render PDFs inline and display images directly."""
        require_authorized(application_id, user)
        stored = document_store.get(application_id, doc_type)
        if stored is None:
            raise HTTPException(status_code=404, detail="document not found or not yet uploaded")
        return Response(
            content=stored.data,
            media_type=stored.content_type,
            headers={"Content-Disposition": f'inline; filename="{doc_type}"'},
        )

    @app.post("/applications/{application_id}/start", status_code=202)
    async def start_application(application_id: str, user: User = Depends(current_user)) -> dict:
        application = require_authorized(application_id, user)
        # Gate entry to the workflow on completeness: an incomplete application
        # would only surface as a data-gap UW_EXCEPTION downstream (with no way to
        # supplement). Stop it here with the specific list of what's still missing.
        from lending.agents.onboarding import missing_fields

        remaining = missing_fields(application)
        if remaining:
            raise HTTPException(
                status_code=422,
                detail=f"application is incomplete — still needs: {', '.join(remaining)}",
            )
        run_ref = starter(application_id)
        if inspect.isawaitable(run_ref):
            run_ref = await run_ref
        return {"application_id": application_id, "workflow_run": run_ref, "status": "started"}

    @app.get("/applications/{application_id}/audit")
    def read_audit(application_id: str, user: User = Depends(current_user)) -> dict:
        require_authorized(application_id, user)
        events = audit_store.reconstruct(application_id)
        return {
            "application_id": application_id,
            "events": [e.model_dump(mode="json") for e in events],
        }

    @app.post("/applications/{application_id}/resolve")
    async def resolve_application(application_id: str, body: ResolveIn,
                                 user: User = Depends(current_user)) -> dict:
        """Ops Console (#15): an underwriter resolves a parked case. Validates the
        parked state + legal target + structured reason code, enforces tiered
        overrides (hard knockouts non-overridable), records a soft override as the
        decision-of-record, and signals the parked workflow to resume."""
        from lending.rules_engine import knockout_reason_codes
        from lending.workflow.statemachine import (
            AWAITING_RESOLUTION, RESOLVE_REASON_CODES, State, is_legal,
        )

        if user.role != "underwriter":
            raise HTTPException(status_code=403, detail="only underwriters can resolve cases")
        application = require_authorized(application_id, user)

        current = State(application.workflow_state) if application.workflow_state else None
        if current not in AWAITING_RESOLUTION:
            raise HTTPException(status_code=409, detail="application is not awaiting resolution")
        try:
            to_state = State(body.to_state)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown state: {body.to_state!r}")
        if not is_legal(current, to_state):
            raise HTTPException(status_code=422, detail=f"illegal resolution {current.value} → {to_state.value}")
        if body.reason_code not in RESOLVE_REASON_CODES:
            raise HTTPException(status_code=422, detail=f"unknown reason code: {body.reason_code!r}")
        # A human justification is mandatory (§16.10): the *binding* reason stays the
        # structured reason_code; this free-text note is the reviewer's rationale,
        # recorded in the audit trail for compliance.
        note = (body.note or "").strip()
        if not note:
            raise HTTPException(status_code=422, detail="a justification note is required")

        # Tiered override: a hard knockout is non-overridable (cannot be approved).
        if to_state == State.APPROVED and application.decision:
            hard = knockout_reason_codes()
            if any(rc in hard for rc in application.decision.reason_codes):
                raise HTTPException(status_code=403, detail="hard knockout is non-overridable")

        # When the resolution IS the decision (approve/decline), record it as the
        # human decision-of-record (§16.10): a REFERRED override, or a reject out of
        # a KYC/UW exception (documents not genuine / cannot underwrite). For an
        # exception there is no prior engine decision — apply_override handles that.
        if to_state in (State.APPROVED, State.DECLINED):
            from lending.decision import apply_override
            from lending.los.schema import Disposition

            disposition = Disposition.APPROVE if to_state == State.APPROVED else Disposition.DECLINE
            apply_override(repo, audit_store, application_id,
                           disposition=disposition, reviewer=user.user_id, reason_code=body.reason_code)

        resolution = {"to_state": to_state.value, "reviewer": user.user_id,
                      "reason_code": body.reason_code, "note": note}
        try:
            res = resolver(application_id, resolution)
            if inspect.isawaitable(res):
                await res
        except Exception as err:  # noqa: BLE001 — surface a clean error, not a 500
            # The parked workflow could not be signalled — most often because it is
            # no longer running (e.g. an application that predates human-in-the-loop
            # parking, whose run already completed). Report it instead of a 500.
            raise HTTPException(
                status_code=409,
                detail=f"could not resume the workflow — it may no longer be running ({err})",
            )
        return {"application_id": application_id, "resolved_to": to_state.value, "status": "resolved"}

    return app
