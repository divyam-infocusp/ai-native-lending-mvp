"""
Version set (#7, §9.4) — the complete, pinned set of versions that produced a
decision, so any decision is reproducible.

Every field is required: a VersionSet cannot be constructed with a missing
stamp, which is the first line of the "no decision without a complete stamp"
guarantee. `protected_namespaces=()` silences Pydantic's warning about the
`model_id` field name.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class VersionSet(BaseModel):
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    rules: str          # RULES_POLICY version
    scorecard: str      # SCORECARD_POLICY version
    pricing: str        # PRICING_POLICY version
    confidence: str     # CONFIDENCE_POLICY version
    model_id: str       # LLM model identifier
    prompt_version: str # explanation/agent prompt version
