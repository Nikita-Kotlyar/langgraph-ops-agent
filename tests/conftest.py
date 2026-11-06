from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.config import PROJECT_ROOT, Settings
from app.enterprise import get_wms_stock
from app.graph import GraphDeps, build_graph
from app.models import InvestigationPlan, OpsReport, Reflection
from app.tracing import TraceRecorder


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        incident_id="INC-2026-11-06-01",
        incident_dir=PROJECT_ROOT / "data" / "incidents" / "INC-2026-11-06-01",
        enterprise_dir=PROJECT_ROOT / "data",
        runs_dir=tmp_path / "runs",
        checkpoint_db=tmp_path / "runs" / "checkpoints.sqlite",
        max_iterations=10,
        max_tool_rounds=12,
    )


@pytest.fixture
def expected_report() -> OpsReport:
    path = PROJECT_ROOT / "data" / "expected" / "15_ops_incident.json"
    return OpsReport.model_validate(json.loads(path.read_text(encoding="utf-8")))


class FakeStructured:
    def __init__(self, value: Any):
        self.value = value

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return self.value


class FakeBound:
    def __init__(self, response: AIMessage):
        self.response = response

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return self.response


class FakeLLM:
    """Детерминированная замена LLM: plan → tool call → final → report."""

    def __init__(self, report: OpsReport):
        self.report = report
        self.agent_calls = 0
        self.plan = InvestigationPlan(
            objective="Установить причину остановки линии",
            questions=["Почему ATP равен нулю?", "Что с заявкой на закупку?"],
            tools=["get_wms_stock", "calculate_material_gap"],
            success_criteria=["Причина, дефицит и штраф подтверждены"],
        )
        self.reflection = Reflection(
            sufficient=True,
            gaps=[],
            next_queries=[],
            confidence=0.9,
        )

    def bind_tools(self, tools):  # noqa: ANN001
        self.agent_calls += 1
        if self.agent_calls == 1:
            return FakeBound(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_wms_stock",
                            "args": {"material_id": "MAT-CU-2.5"},
                            "id": "call_test_1",
                            "type": "tool_call",
                        }
                    ],
                )
            )
        return FakeBound(AIMessage(content="Данных достаточно."))

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001
        if schema is InvestigationPlan:
            return FakeStructured(self.plan)
        if schema is Reflection:
            return FakeStructured(self.reflection)
        if schema is OpsReport:
            return FakeStructured(self.report)
        raise AssertionError(f"unexpected schema: {schema}")

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return AIMessage(content="Итог: причина установлена.")


@tool
def get_wms_stock_tool(material_id: str) -> dict:
    """Вернуть остаток WMS с расчётом ATP."""
    return get_wms_stock(material_id)


@pytest.fixture
def fake_llm(expected_report: OpsReport) -> FakeLLM:
    return FakeLLM(expected_report)


@pytest.fixture
def graph_with_fake(settings: Settings, fake_llm: FakeLLM):
    settings.ensure_dirs()
    tracer = TraceRecorder(settings.runs_dir / "unit-test" / "trace.jsonl")
    deps = GraphDeps(
        settings=settings,
        llm=fake_llm,
        tools=[get_wms_stock_tool],
        checkpointer=MemorySaver(),
        tracer=tracer,
    )
    return build_graph(deps)
