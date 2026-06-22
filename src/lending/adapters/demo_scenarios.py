"""
Demo scenario harness (demo only) — lets the mock bureau honor a per-application
`demo_scenario` tag so every origination path can be triggered on demand from the
UI, with no rebuild or restart. The scenario is data on the application; the
adapter reads it at call time.

NOT for pilot/production — a real bureau adapter (#10/#9) never branches on a tag.
"""
from __future__ import annotations

from types import SimpleNamespace

from .bureau import CLEAN_REPORT, THIN_FILE_REPORT

# Valid scenario tags (also accepted/validated by the API at create time).
DEMO_SCENARIOS = frozenset({
    "clean",        # happy path → Offer
    "high_dti",     # soft policy hit → Referred
    "low_cibil",    # hard knockout → Declined
    "thin_file",    # UW exception (recoverable on re-assessment)
    "doc_mismatch", # KYC exception (cross-source name mismatch)
    "lead_review",  # Lead exception (forced manual review)
})

_HIGH_DTI_REPORT = {**CLEAN_REPORT, "total_monthly_obligations": 60_000.0,
                    "report_id": "CIBIL-DEMO-HIGHDTI"}
_LOW_CIBIL_REPORT = {**CLEAN_REPORT, "score": 600, "report_id": "CIBIL-DEMO-LOWCIBIL"}


def scenario_of(application) -> str:
    """The application's demo scenario tag, defaulting to 'clean'."""
    return ((getattr(application, "features", None) or {}).get("demo_scenario")) or "clean"


class ScenarioBureauHarness:
    """A bureau harness whose report depends on the application's demo_scenario.

    Duck-typed to what `pull_bureau` needs (`.call(request).data`). Intentionally
    NOT idempotent for `thin_file`: it returns a thin file on the first pull
    (→ UW_EXCEPTION) and a healthy file on the re-pull, so resolving the exception
    ('Re-run assessment') re-assembles inputs and proceeds — demonstrating the
    re-assess path end-to-end rather than dead-ending.
    """

    def __init__(self, repository) -> None:
        self._repo = repository
        self._pulls: dict[str, int] = {}

    def call(self, request):
        application = self._repo.get(request.application_id)
        scenario = scenario_of(application)
        if scenario == "high_dti":
            data = _HIGH_DTI_REPORT
        elif scenario == "low_cibil":
            data = _LOW_CIBIL_REPORT
        elif scenario == "thin_file":
            n = self._pulls.get(request.application_id, 0) + 1
            self._pulls[request.application_id] = n
            data = THIN_FILE_REPORT if n == 1 else CLEAN_REPORT
        else:
            data = CLEAN_REPORT
        return SimpleNamespace(data=data)
