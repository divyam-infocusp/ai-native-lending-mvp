from .repository import ApplicationRepository, make_engine
from .schema import (
    Application,
    ApplicationCreate,
    ApplicationStatus,
    Applicant,
    Consent,
    ConsentArtifact,
    ConsentAuthorization,
    Decision,
    FieldConfidence,
    Kyc,
)

__all__ = [
    "create_app",
    "ApplicationRepository",
    "make_engine",
    "Application",
    "ApplicationCreate",
    "ApplicationStatus",
    "Applicant",
    "Consent",
    "ConsentArtifact",
    "ConsentAuthorization",
    "Decision",
    "FieldConfidence",
    "Kyc",
    "create_app",
]


def __getattr__(name: str):
    # Lazy: importing create_app pulls FastAPI, which must NOT be dragged into the
    # Temporal workflow sandbox via `import lending.los.schema`. Load it on demand.
    if name == "create_app":
        from .api import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
