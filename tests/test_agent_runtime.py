"""
Tests for the Agent Runtime scaffold (#16, §7).

Exercises the five-part contract with an injected (fake) reasoning step and a
tool wired to the #1 adapter harness:
  - schema-violating output → rejected + retried
  - below-threshold confidence → escalates to the configured state
  - exhausted retries → escalates
  - crash mid-loop (interrupt) → resumes from the last completed node and does
    NOT repeat the external tool call
"""
import os

import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from lending.adapters import AdapterHarness, AdapterRequest, MockAdapter
from lending.agent_runtime import AgentSpec, build_agent, make_checkpointer, run_agent


class ExtractedFields(BaseModel):
    pan: str
    monthly_income: float


def make_spec(reason, *, threshold=0.7, tool=None, max_retries=2) -> AgentSpec:
    return AgentSpec(
        name="doc-intel-test",
        tool=tool or (lambda ctx: {"ocr_text": "PAN ABCDE1234F income 50000"}),
        reason=reason,
        output_schema=ExtractedFields,
        confidence_fn=lambda output, tool_result: tool_result.get("confidence", 0.95),
        threshold=threshold,
        escalation_state="KYC_EXCEPTION",
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_clean_run_completes_with_validated_output():
    spec = make_spec(lambda ctx, tr: {"pan": "ABCDE1234F", "monthly_income": 50000})
    result = run_agent(spec, {"application_id": "a1"})
    assert result.outcome == "completed"
    assert result.output == {"pan": "ABCDE1234F", "monthly_income": 50000.0}
    assert result.retries == 0


# ---------------------------------------------------------------------------
# Schema violation → reject + retry
# ---------------------------------------------------------------------------

def test_malformed_output_is_rejected_and_retried():
    calls = {"n": 0}

    def flaky_reason(ctx, tr):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"pan": "ABCDE1234F"}  # missing monthly_income → schema violation
        return {"pan": "ABCDE1234F", "monthly_income": 50000}

    result = run_agent(make_spec(flaky_reason), {"application_id": "a1"})
    assert calls["n"] == 2                  # reasoned again after the rejection
    assert result.retries == 1              # one rejection counted
    assert result.outcome == "completed"    # the retry produced a valid output
    assert result.output["monthly_income"] == 50000.0


def test_persistently_malformed_escalates_after_retries():
    spec = make_spec(lambda ctx, tr: {"pan": "ABCDE1234F"}, max_retries=2)  # always invalid
    result = run_agent(spec, {"application_id": "a1"})
    assert result.outcome == "escalated"
    assert result.escalation_state == "KYC_EXCEPTION"
    assert result.retries == 3              # initial + 2 retries, all rejected


# ---------------------------------------------------------------------------
# Grounded confidence gate → escalate
# ---------------------------------------------------------------------------

def test_below_threshold_confidence_escalates():
    # tool reports low confidence; the grounded confidence_fn reads it (not the LLM)
    spec = make_spec(
        lambda ctx, tr: {"pan": "ABCDE1234F", "monthly_income": 50000},
        threshold=0.7,
        tool=lambda ctx: {"confidence": 0.40},
    )
    result = run_agent(spec, {"application_id": "a1"})
    assert result.outcome == "escalated"
    assert result.escalation_state == "KYC_EXCEPTION"
    assert result.confidence == 0.40


def test_at_threshold_completes():
    spec = make_spec(
        lambda ctx, tr: {"pan": "ABCDE1234F", "monthly_income": 50000},
        threshold=0.7,
        tool=lambda ctx: {"confidence": 0.70},
    )
    assert run_agent(spec, {"application_id": "a1"}).outcome == "completed"


# ---------------------------------------------------------------------------
# Retry = reload: crash mid-loop resumes and does NOT repeat the external call
# ---------------------------------------------------------------------------

def test_resume_does_not_repeat_external_tool_call():
    # The tool is a #1 idempotent adapter; we also prove the checkpoint skips it.
    harness = AdapterHarness()
    adapter = MockAdapter("ocr", fixtures={"extract": {"confidence": 0.95}})
    harness.register(adapter)

    def tool(ctx):
        resp = harness.call(AdapterRequest(ctx["application_id"], "ocr", "extract"))
        return resp.data

    spec = make_spec(
        lambda ctx, tr: {"pan": "ABCDE1234F", "monthly_income": 50000},
        tool=tool,
    )

    cp = MemorySaver()
    app = build_agent(spec, checkpointer=cp, interrupt_after=["tool"])
    cfg = {"configurable": {"thread_id": "resume-1"}}

    # Run to the interrupt right after the (external) tool node — simulates a crash.
    app.invoke({"context": {"application_id": "a1"}}, config=cfg)
    assert adapter.execution_count == 1

    # Resume from the checkpoint: continues from the last completed node.
    final = app.invoke(None, config=cfg)
    assert adapter.execution_count == 1            # tool NOT re-executed
    assert final["outcome"] == "completed"
    assert final["validated"]["pan"] == "ABCDE1234F"


# ---------------------------------------------------------------------------
# Checkpointer factory
# ---------------------------------------------------------------------------

def test_make_checkpointer_defaults_to_memory():
    assert isinstance(make_checkpointer(None), MemorySaver)
    assert isinstance(make_checkpointer("sqlite+pysqlite:///:memory:"), MemorySaver)


@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="set TEST_POSTGRES_URL to run the Postgres-backed checkpointer test",
)
def test_postgres_checkpoint_survives_a_fresh_saver():
    """The durable guarantee: a checkpoint written by one saver is resumed by a
    DIFFERENT saver (a stand-in for a worker restart) against the same DB."""
    url = os.environ["TEST_POSTGRES_URL"]
    calls = {"tool": 0}

    def tool(ctx):
        calls["tool"] += 1
        return {"confidence": 0.95}

    spec = make_spec(lambda ctx, tr: {"pan": "ABCDE1234F", "monthly_income": 50000}, tool=tool)
    cfg = {"configurable": {"thread_id": "pg-resume-1"}}

    cp1 = make_checkpointer(url)
    app1 = build_agent(spec, checkpointer=cp1, interrupt_after=["tool"])
    app1.invoke({"context": {"application_id": "a1"}}, config=cfg)
    assert calls["tool"] == 1

    # A brand-new checkpointer (new pool) — simulates a restarted worker process.
    cp2 = make_checkpointer(url)
    app2 = build_agent(spec, checkpointer=cp2, interrupt_after=["tool"])
    final = app2.invoke(None, config=cfg)              # resume from the persisted checkpoint
    assert calls["tool"] == 1                          # tool NOT re-run after "restart"
    assert final["outcome"] == "completed"
