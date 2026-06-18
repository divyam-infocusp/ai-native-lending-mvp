"""
Agent Runtime contract types (#16, §7).

An AgentSpec is everything that distinguishes one agent from another — its tool,
its reasoning step, its output schema, how it derives confidence, its threshold,
and where it escalates. The runtime (runtime.py) turns any spec into a LangGraph
graph that enforces the §7 five-part contract uniformly.

The reasoning step is *injected* so the scaffold is testable without a live LLM;
real agents (#19–#23) supply a Claude-backed `reason`. Confidence is supplied by
`confidence_fn` (computed from grounded signals, e.g. #5) — never read from the
LLM's own output (§16.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, TypedDict

from pydantic import BaseModel

# A reasoning step: given the input context + the tool result, produce a candidate
# output dict (which is then validated against the schema).
ReasonFn = Callable[[dict, dict], dict]
# A tool: the (idempotent) external call — e.g. an OCR/bureau adapter from #1.
ToolFn = Callable[[dict], dict]
# Grounded confidence from the validated output + tool result.
ConfidenceFn = Callable[[dict, dict], float]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    tool: ToolFn
    reason: ReasonFn
    output_schema: type[BaseModel]
    confidence_fn: ConfidenceFn
    threshold: float
    escalation_state: str
    max_retries: int = 2


class AgentState(TypedDict, total=False):
    context: dict
    tool_result: dict
    candidate: dict
    validated: Optional[dict]
    retries: int
    confidence: float
    outcome: str               # "completed" | "escalated"
    escalation_state: Optional[str]


@dataclass(frozen=True)
class AgentResult:
    outcome: str               # "completed" | "escalated"
    output: Optional[dict]     # validated output (None if escalated before validating)
    confidence: Optional[float]
    escalation_state: Optional[str]
    retries: int
