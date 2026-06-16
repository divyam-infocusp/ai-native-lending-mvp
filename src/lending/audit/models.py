"""
Audit & Explainability event model (#6, design §9.1).

An AuditEvent is one immutable line in an application's logbook. It is frozen:
once created it cannot be mutated in place. The store assigns `seq` — a global,
monotonically increasing number that fixes append order across all applications.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EventType(str, Enum):
    """The event categories called out in §9.1. `event_type` also accepts free
    strings, so this is a convenience vocabulary, not a hard constraint."""
    INPUT = "input"                    # data that entered the system
    TOOL_CALL = "tool_call"            # an external/adapter call
    MODEL_VERSION = "model_version"    # model/prompt version in effect
    RULE_FIRED = "rule_fired"          # a policy rule fired
    AGENT_REASONING = "agent_reasoning"  # an agent's reasoning step
    HUMAN_ACTION = "human_action"      # an underwriter/ops action (e.g. override)
    DECISION = "decision"              # a recorded decision-of-record
    STATE_TRANSITION = "state_transition"  # workflow state change


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int                  # global append order, assigned by the store
    event_id: str
    application_id: str
    event_type: str
    payload: dict
    created_at: datetime
    actor: Optional[str] = None  # who/what produced it: "engine" | "underwriter:u1" | "agent:doc-intel"
