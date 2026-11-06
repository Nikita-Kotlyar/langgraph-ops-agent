from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from langgraph.types import Command
from pydantic import BaseModel

from . import enterprise
from .checkpoint import sqlite_saver
from .config import Settings, get_settings
from .graph import GraphDeps, build_graph
from .llm import build_langfuse_handler, build_llm, flush_langfuse
from .mcp_client import load_mcp_tools
from .models import ApprovalDecision, ApprovalRequest, OpsReport, RunResponse
from .tracing import RunTraceRouter

logger = logging.getLogger("ops_agent.api")


class RunCreateRequest(BaseModel):
    thread_id: str | None = None
    incident_id: str | None = None


def _new_thread_id() -> str:
    return f"run-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _as_report(value: Any) -> OpsReport | None:
    if value is None:
        return None
    if isinstance(value, OpsReport):
        return value
    if isinstance(value, dict):
        return OpsReport.model_validate(value)
    return None


def _as_approval(value: Any) -> ApprovalRequest | None:
    if value is None:
        return None
    if isinstance(value, ApprovalRequest):
        return value
    if isinstance(value, dict):
        return ApprovalRequest.model_validate(value)
    return None


def _extract_interrupt(result: dict[str, Any]) -> ApprovalRequest | None:
    interrupts = result.get("__interrupt__") or []
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", None)
    if value is None and isinstance(first, dict):
        value = first.get("value")
    return _as_approval(value)


def _response(thread_id: str, incident_id: str, result: dict[str, Any]) -> RunResponse:
    approval = _extract_interrupt(result)
    report = _as_report(result.get("report"))
    status = "awaiting_approval" if approval else "completed"
    if result.get("status") == "failed" or (report is None and approval is None):
        status = "failed"
    return RunResponse(
        thread_id=thread_id,
        incident_id=incident_id,
        status=status,
        report=_as_report(result.get("report")),
        approval_request=approval,
        trace_id=thread_id,
    )


def create_app(settings: Settings | None = None, graph: Any | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.graph is not None:
            yield
            return
        tools = await load_mcp_tools(settings)
        llm = build_llm(settings)
        langfuse_factory = (
            (lambda tid: build_langfuse_handler(settings, trace_id=tid))
            if settings.langfuse_enabled
            else None
        )
        tracer = RunTraceRouter(settings.runs_dir)

        async with sqlite_saver(settings.checkpoint_db) as checkpointer:
            app.state.graph = build_graph(
                GraphDeps(
                    settings=settings,
                    llm=llm,
                    tools=tools,
                    checkpointer=checkpointer,
                    tracer=tracer,
                    langfuse_handler_factory=langfuse_factory,
                )
            )
            app.state.tools = tools
            app.state.langfuse_factory = langfuse_factory
            app.state.langfuse_handlers = {}
            app.state.tracer = tracer
            logger.info("Agent ready: tools=%d langfuse=%s", len(tools), bool(langfuse_factory))
            yield

    app = FastAPI(
        title="langgraph-ops-agent",
        description=(
            "Операционный агент разбора инцидентов: LangGraph, MCP, structured output, "
            "human-in-the-loop, LangFuse."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.graph = graph
    app.state.tools = []
    app.state.langfuse_factory = None
    app.state.langfuse_handlers = {}
    app.state.tracer = RunTraceRouter(settings.runs_dir)

    def _config(thread_id: str) -> dict[str, Any]:
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 60,
        }
        factory = app.state.langfuse_factory
        if factory is not None:
            if thread_id not in app.state.langfuse_handlers:
                app.state.langfuse_handlers[thread_id] = factory(thread_id)
            config["callbacks"] = [app.state.langfuse_handlers[thread_id]]
        return config

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "0.1.0",
            "environment": settings.environment,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.resolved_model,
            "tools": len(request.app.state.tools),
            "langfuse": bool(request.app.state.langfuse_factory or settings.langfuse_enabled),
            "mcp": "stdio",
        }

    @app.get("/incidents")
    async def incidents() -> list[dict[str, Any]]:
        return enterprise.list_incidents(settings)

    @app.post("/incidents/{incident_id}/runs", response_model=RunResponse, status_code=202)
    async def start_run(incident_id: str, body: RunCreateRequest | None = None) -> RunResponse:
        thread_id = (body.thread_id if body else None) or _new_thread_id()
        state = {
            "thread_id": thread_id,
            "incident_id": incident_id,
            "messages": [],
            "evidence": [],
            "errors": [],
        }
        try:
            result = await asyncio.wait_for(
                app.state.graph.ainvoke(state, config=_config(thread_id)),
                timeout=settings.run_timeout_seconds,
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="agent run timed out") from exc
        except Exception as exc:  # noqa: BLE001 - API boundary
            logger.exception("run failed")
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        flush_langfuse()
        response = _response(thread_id, incident_id, result)
        if response.status == "awaiting_approval":
            # 201 already sent; the body carries approval_request.
            return response
        return response

    @app.get("/runs/{thread_id}", response_model=RunResponse)
    async def get_run(thread_id: str) -> RunResponse:
        try:
            snapshot = await app.state.graph.aget_state(_config(thread_id))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=f"run {thread_id} not found") from exc
        values = snapshot.values or {}
        if not values:
            raise HTTPException(status_code=404, detail=f"run {thread_id} not found")
        approval = _as_approval(values.get("approval"))
        report = _as_report(values.get("report"))
        status = values.get("status", "completed")
        if "approval" in (snapshot.next or ()) or (approval and values.get("approval_decision") is None):
            status = "awaiting_approval"
        elif report is not None:
            status = "completed"
        return RunResponse(
            thread_id=thread_id,
            incident_id=values.get("incident_id", ""),
            status=status,
            report=report,
            approval_request=approval,
            trace_id=thread_id,
        )

    @app.post("/runs/{thread_id}/approve", response_model=RunResponse)
    async def approve(thread_id: str, decision: ApprovalDecision) -> RunResponse:
        try:
            result = await asyncio.wait_for(
                app.state.graph.ainvoke(
                    Command(resume=decision.model_dump(mode="json")),
                    config=_config(thread_id),
                ),
                timeout=settings.run_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("approval failed")
            raise HTTPException(status_code=409, detail=f"cannot resume run: {exc}") from exc
        flush_langfuse()
        snapshot = await app.state.graph.aget_state(_config(thread_id))
        values = snapshot.values or {}
        return RunResponse(
            thread_id=thread_id,
            incident_id=values.get("incident_id", ""),
            status="completed",
            report=_as_report(values.get("report") or result.get("report")),
            approval_request=None,
            trace_id=thread_id,
        )

    @app.get("/runs/{thread_id}/trace")
    async def trace(thread_id: str) -> list[dict[str, Any]]:
        return app.state.tracer.read(thread_id)

    @app.get("/runs/{thread_id}/actions")
    async def actions(thread_id: str) -> list[dict[str, Any]]:
        path: Path = settings.runs_dir / thread_id / "actions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    return app


app = create_app()
