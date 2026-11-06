from __future__ import annotations

import pytest
from langgraph.types import Command

pytestmark = pytest.mark.asyncio


async def test_graph_pauses_for_approval(graph_with_fake, settings):
    config = {"configurable": {"thread_id": "test-thread"}}
    state = {
        "thread_id": "test-thread",
        "incident_id": settings.incident_id,
        "messages": [],
        "evidence": [],
        "errors": [],
    }
    result = await graph_with_fake.ainvoke(state, config=config)

    assert result.get("__interrupt__"), "graph must pause for human approval"
    report = result["report"]
    assert report.root_cause_code == "ATP_MISMATCH_AND_BLOCKED_PO"
    assert report.impact.deficit_kg == pytest.approx(1501.212, abs=0.01)
    assert report.impact.penalty_rub == pytest.approx(27990.54, abs=0.01)
    assert {a.action_id for a in report.recommended_actions} >= {
        "reallocate_reserved_copper",
        "approve_price_variance_and_pay",
        "expedite_alternative_supplier",
        "notify_customer",
    }


async def test_graph_resumes_after_approval(graph_with_fake, settings):
    config = {"configurable": {"thread_id": "test-resume"}}
    state = {
        "thread_id": "test-resume",
        "incident_id": settings.incident_id,
        "messages": [],
        "evidence": [],
        "errors": [],
    }
    await graph_with_fake.ainvoke(state, config=config)
    result = await graph_with_fake.ainvoke(
        Command(resume={"approved": True, "comment": "ok", "approver": "test"}),
        config=config,
    )

    assert result["status"] == "completed"
    actions_path = settings.runs_dir / "test-resume" / "actions.jsonl"
    lines = [line for line in actions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines
    assert any('"status": "approved"' in line for line in lines)
    assert any('"status": "proposed"' in line for line in lines)


async def test_trace_contains_core_nodes(graph_with_fake, settings):
    config = {"configurable": {"thread_id": "test-trace"}}
    state = {
        "thread_id": "test-trace",
        "incident_id": settings.incident_id,
        "messages": [],
        "evidence": [],
        "errors": [],
    }
    await graph_with_fake.ainvoke(state, config=config)
    trace_path = settings.runs_dir / "unit-test" / "trace.jsonl"
    trace = trace_path.read_text(encoding="utf-8")
    for node in ("intake", "plan", "agent", "tools", "collect", "reflect", "measure", "assess", "approval"):
        assert f'"node": "{node}"' in trace
