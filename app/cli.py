from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command

from .checkpoint import sqlite_saver
from .config import Settings
from .graph import GraphDeps, build_graph
from .llm import build_langfuse_handler, build_llm, flush_langfuse
from .mcp_client import load_mcp_tools
from .models import ApprovalDecision
from .tracing import TraceRecorder


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items() if k != "messages"}
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


async def _run(incident_id: str, thread_id: str, auto_approve: bool) -> int:
    settings = Settings()
    settings.ensure_dirs()
    tools = await load_mcp_tools(settings)
    llm = build_llm(settings)
    handler = build_langfuse_handler(settings, trace_id=thread_id)
    tracer = TraceRecorder(settings.runs_dir / thread_id / "trace.jsonl")
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
    if handler is not None:
        config["callbacks"] = [handler]

    async with sqlite_saver(settings.checkpoint_db) as checkpointer:
        graph = build_graph(
            GraphDeps(
                settings=settings,
                llm=llm,
                tools=tools,
                checkpointer=checkpointer,
                tracer=tracer,
                langfuse_handler=handler,
            )
        )
        state = {
            "thread_id": thread_id,
            "incident_id": incident_id,
            "messages": [],
            "evidence": [],
            "errors": [],
        }
        result = await graph.ainvoke(state, config=config)
        flush_langfuse()
        print(json.dumps(_dump(result), ensure_ascii=False, indent=2, default=str))
        interrupts = result.get("__interrupt__") or []
        if interrupts and auto_approve:
            decision = ApprovalDecision(
                approved=True,
                comment="CLI --approve: подтверждено оператором",
                approver="cli",
            )
            result = await graph.ainvoke(
                Command(resume=decision.model_dump(mode="json")),
                config=config,
            )
            print("\n=== AFTER APPROVAL ===")
            print(json.dumps(_dump(result), ensure_ascii=False, indent=2, default=str))
        elif interrupts:
            print("\n=== APPROVAL REQUIRED ===")
            value = getattr(interrupts[0], "value", interrupts[0])
            print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
            print(f"\nПродолжить: ops-agent run --thread-id {thread_id} --approve")
    return 0


async def _tools() -> int:
    settings = Settings()
    tools = await load_mcp_tools(settings)
    for tool in tools:
        print(f"- {tool.name}: {tool.description}")
    print(f"\nВсего инструментов: {len(tools)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ops-agent", description="LangGraph ops incident agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="запустить разбор инцидента")
    run_p.add_argument("--incident", default="INC-2026-11-06-01")
    run_p.add_argument("--thread-id", default=None)
    run_p.add_argument("--approve", action="store_true", help="автоматически подтвердить approval")
    run_p.add_argument("--json", action="store_true", help="только JSON в stdout")

    sub.add_parser("tools", help="показать MCP-инструменты")

    serve_p = sub.add_parser("serve", help="запустить FastAPI")
    serve_p.add_argument("--port", type=int, default=None)

    args = parser.parse_args()
    if args.command == "tools":
        return asyncio.run(_tools())
    if args.command == "serve":
        import uvicorn

        settings = Settings()
        port = args.port or settings.app_port
        uvicorn.run("app.api:app", host="0.0.0.0", port=port, reload=False)
        return 0

    thread_id = args.thread_id or f"run-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    return asyncio.run(_run(args.incident, thread_id, args.approve))


if __name__ == "__main__":
    raise SystemExit(main())
