"""
Agent Runtime (#16, §7) — a LangGraph scaffold every agent runs inside.

The graph enforces the five-part contract uniformly:

    tool ──▶ reason ──▶ validate ──┬─ valid ─────▶ confidence ──┬─ ≥ threshold ─▶ finalize
                          ▲        │                            └─ < threshold ─▶ escalate
                          └─ retry ┤ (schema violation, retries left)
                                   └─ exhausted ─▶ escalate

  1. fixed tool set        — only `spec.tool` is callable
  2. strict output schema  — `spec.output_schema`; invalid → reject + retry
  3. grounded confidence   — `spec.confidence_fn` (from real signals, not the LLM)
  4. escalation path       — below threshold / exhausted retries → `spec.escalation_state`
  5. retry = reload        — a checkpointer persists state per node, so a resumed
                             run continues from the last completed node and never
                             repeats the (idempotent) external tool call (§16.5)
"""
from __future__ import annotations

from typing import Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .models import AgentResult, AgentSpec, AgentState


def build_agent(
    spec: AgentSpec,
    checkpointer=None,
    interrupt_after: Optional[list[str]] = None,
):
    """Compile a spec into a runnable LangGraph graph enforcing the §7 contract."""
    graph = StateGraph(AgentState)

    def tool_node(state: AgentState) -> dict:
        return {"tool_result": spec.tool(state.get("context", {}))}

    def reason_node(state: AgentState) -> dict:
        candidate = spec.reason(state.get("context", {}), state.get("tool_result", {}))
        return {"candidate": candidate}

    def validate_node(state: AgentState) -> dict:
        try:
            obj = spec.output_schema(**state.get("candidate", {}))
            return {"validated": obj.model_dump()}
        except ValidationError:
            # Reject; count the attempt so routing can retry or give up.
            return {"retries": state.get("retries", 0) + 1, "validated": None}

    def confidence_node(state: AgentState) -> dict:
        return {"confidence": spec.confidence_fn(state["validated"], state.get("tool_result", {}))}

    def escalate_node(state: AgentState) -> dict:
        return {"outcome": "escalated", "escalation_state": spec.escalation_state}

    def finalize_node(state: AgentState) -> dict:
        return {"outcome": "completed"}

    def route_after_validate(state: AgentState) -> str:
        if state.get("validated"):
            return "confidence"
        if state.get("retries", 0) <= spec.max_retries:
            return "reason"          # reject + retry
        return "escalate"            # exhausted retries → human

    def route_after_confidence(state: AgentState) -> str:
        return "finalize" if state.get("confidence", 0.0) >= spec.threshold else "escalate"

    graph.add_node("tool", tool_node)
    graph.add_node("reason", reason_node)
    graph.add_node("validate", validate_node)
    graph.add_node("confidence", confidence_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "tool")
    graph.add_edge("tool", "reason")
    graph.add_edge("reason", "validate")
    graph.add_conditional_edges(
        "validate", route_after_validate,
        {"reason": "reason", "confidence": "confidence", "escalate": "escalate"},
    )
    graph.add_conditional_edges(
        "confidence", route_after_confidence,
        {"finalize": "finalize", "escalate": "escalate"},
    )
    graph.add_edge("escalate", END)
    graph.add_edge("finalize", END)

    return graph.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_after=interrupt_after or [],
    )


def _to_result(state: AgentState) -> AgentResult:
    return AgentResult(
        outcome=state.get("outcome", "escalated"),
        output=state.get("validated"),
        confidence=state.get("confidence"),
        escalation_state=state.get("escalation_state"),
        retries=state.get("retries", 0),
    )


def run_agent(spec: AgentSpec, context: dict, *, thread_id: str = "agent") -> AgentResult:
    """Convenience: build + run an agent to completion, returning a typed result."""
    app = build_agent(spec)
    final = app.invoke({"context": context}, config={"configurable": {"thread_id": thread_id}})
    return _to_result(final)
