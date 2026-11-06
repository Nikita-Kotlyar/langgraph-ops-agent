import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from . import enterprise
from .config import Settings
from .llm import build_structured, flush_langfuse
from .models import (
    ApprovalDecision,
    ApprovalRequest,
    Evidence,
    InvestigationPlan,
    OpsReport,
    Reflection,
)
from .policy import normalize_report
from .prompts import SYSTEM_PROMPT, agent_prompt, assess_prompt, plan_prompt, reflect_prompt
from .state import AgentState
from .tracing import TraceRecorder


@dataclass
class GraphDeps:
    settings: Settings
    llm: BaseChatModel
    tools: list[BaseTool]
    checkpointer: BaseCheckpointSaver | None = None
    tracer: TraceRecorder | None = None
    langfuse_handler: Any | None = None
    langfuse_handler_factory: Callable[[str], Any] | None = None
    _handlers: dict[str, Any] = field(default_factory=dict)

    def config(self, thread_id: str) -> dict[str, Any]:
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 60,
        }
        handler = self.langfuse_handler
        if handler is None and self.langfuse_handler_factory is not None:
            if thread_id not in self._handlers:
                self._handlers[thread_id] = self.langfuse_handler_factory(thread_id)
            handler = self._handlers[thread_id]
        if handler is not None:
            config["callbacks"] = [handler]
        return config


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _tool_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _summarize_tool_result(name: str, payload: Any) -> str:
    if isinstance(payload, dict):
        if name == "get_wms_stock":
            return (
                f"balance={payload.get('balance_kg')} kg, reserved={payload.get('reserved_kg')} kg, "
                f"quality_hold={payload.get('quality_hold_kg')} kg, "
                f"safety_stock={payload.get('safety_stock_kg')} kg, ATP={payload.get('available_kg')} kg"
            )
        if name == "calculate_material_gap":
            return (
                f"need={payload.get('need_kg')} kg, available={payload.get('available_kg')} kg, "
                f"deficit={payload.get('deficit_kg')} kg, coverage={payload.get('coverage_pct')}%"
            )
        if name == "calculate_penalty":
            return (
                f"{payload.get('order_id')}: {payload.get('days_late')} days late, "
                f"penalty={payload.get('penalty_rub')} RUB, cap={payload.get('penalty_cap_rub')} RUB"
            )
        if name == "get_order":
            order = payload.get("order") or {}
            lines = payload.get("lines") or []
            return (
                f"{order.get('order_id')} value={order.get('total_rub')} RUB, "
                f"delivery={order.get('delivery_date')}, lines={len(lines)}"
            )
        if name == "search_emails" and isinstance(payload, list):
            return f"found {len(payload)} emails: " + "; ".join(
                f"{m.get('file')} — {m.get('subject')}" for m in payload[:3]
            )
        if name == "search_documents" and isinstance(payload, list):
            return f"found {len(payload)} documents: " + "; ".join(
                f"{d.get('source')}" for d in payload[:3]
            )
    text = _tool_content_to_text(payload)
    return text[:700]


def build_graph(deps: GraphDeps):
    """Собрать и скомпилировать StateGraph."""
    tracer = deps.tracer
    tool_node = ToolNode(deps.tools, handle_tool_errors=True)

    def traced(name: str):
        def decorator(fn):
            async def wrapper(state: AgentState, config: RunnableConfig | None = None):
                if tracer:
                    tracer.record(name, "start", thread_id=state.get("thread_id"))
                started = time.perf_counter()
                try:
                    result = await fn(state, config)
                    if tracer:
                        tracer.record(
                            name,
                            "end",
                            duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        )
                    return result
                except GraphInterrupt:
                    if tracer:
                        tracer.record(
                            name,
                            "interrupt",
                            duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        )
                    raise
                except Exception as exc:  # noqa: BLE001 - surface in trace and state
                    if tracer:
                        tracer.record(
                            name,
                            "error",
                            duration_ms=round((time.perf_counter() - started) * 1000, 1),
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    raise

            return wrapper

        return decorator

    async def intake(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        incident_id = state.get("incident_id") or deps.settings.incident_id
        incident = enterprise.get_incident(incident_id, deps.settings)
        alert = incident.get("alert", {})
        objective = (
            f"Разобрать инцидент {incident_id}: почему линия {alert.get('machine_id', '?')} "
            f"остановлена и что делать с заказом {alert.get('order_id', '?')}."
        )
        return {
            "thread_id": (state.get("thread_id") or (config or {}).get("configurable", {}).get("thread_id", "unknown")),
            "incident_id": incident_id,
            "incident": incident,
            "objective": objective,
            "status": "running",
            "iterations": 0,
            "tool_rounds": 0,
            "max_iterations": deps.settings.max_iterations,
        }

    async def plan(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structured = build_structured(deps.llm, InvestigationPlan)
        result: InvestigationPlan = await structured.ainvoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=plan_prompt(state["incident"])),
            ],
            config=deps.config(state["thread_id"]),
        )
        return {"plan": result}

    async def agent(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=agent_prompt(state))]
        if (
            state.get("tool_rounds", 0) >= deps.settings.max_tool_rounds
            or state.get("iterations", 0) >= deps.settings.max_iterations
        ):
            response = await deps.llm.ainvoke(messages, config=deps.config(state["thread_id"]))
        else:
            bound = deps.llm.bind_tools(deps.tools)
            response = await bound.ainvoke(messages, config=deps.config(state["thread_id"]))
        return {"messages": [response], "iterations": state.get("iterations", 0) + 1}

    async def tools(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        return await tool_node.ainvoke(state, config)

    async def collect(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        messages = state.get("messages", [])
        last_ai = None
        for idx in range(len(messages) - 1, -1, -1):
            if isinstance(messages[idx], AIMessage) and getattr(messages[idx], "tool_calls", None):
                last_ai = idx
                break
        if last_ai is None:
            return {"errors": ["collect: не найден AIMessage с tool_calls"]}
        evidence: list[Evidence] = []
        errors: list[str] = []
        for msg in messages[last_ai + 1 :]:
            if not isinstance(msg, ToolMessage):
                continue
            text = _tool_content_to_text(msg.content)
            try:
                payload: Any = json.loads(text)
            except json.JSONDecodeError:
                payload = text
            if isinstance(payload, dict) and payload.get("error"):
                errors.append(f"{msg.name}: {payload['error']}")
            evidence.append(
                Evidence(
                    source=str(msg.name or "tool"),
                    ref=f"{msg.name}#{str(msg.tool_call_id)[:8]}",
                    fact=_summarize_tool_result(str(msg.name or ""), payload),
                )
            )
        return {
            "evidence": evidence,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
            "errors": errors,
        }

    async def reflect(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structured = build_structured(deps.llm, Reflection)
        result: Reflection = await structured.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=reflect_prompt(state))],
            config=deps.config(state["thread_id"]),
        )
        payload: dict[str, Any] = {"reflection": result}
        if not result.sufficient and state.get("iterations", 0) < deps.settings.max_iterations:
            gaps = "; ".join(result.gaps) or "данных недостаточно"
            queries = "; ".join(result.next_queries) or "вызови недостающие инструменты"
            payload["messages"] = [
                HumanMessage(content=f"Рефлексия: {gaps}. Следующие запросы: {queries}.")
            ]
        return payload

    async def measure(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        incident = state.get("incident", {}) or {}
        alert = incident.get("alert", {}) or {}
        order_id = alert.get("order_id") or "ORD-2026-01201"
        order = enterprise.get_order(order_id, deps.settings)
        if "error" in order:
            return {"computed": {"error": order["error"]}, "errors": [f"measure: {order['error']}"]}
        skus = {line.get("sku") for line in order.get("lines", [])}
        boms = enterprise.get_bom(settings=deps.settings)
        material_ids = sorted({b.get("material_id") for b in boms if b.get("sku") in skus})
        material_id = material_ids[0] if material_ids else "MAT-CU-2.5"
        gap = enterprise.calculate_material_gap(order_id, material_id, deps.settings)
        purchase = enterprise.get_purchase_orders(material_id, deps.settings)
        pos = purchase.get("purchase_orders", [])
        po = pos[0] if pos else {}
        confirmed = str(po.get("confirmed_date") or "2026-11-12")
        quotes = purchase.get("supplier_quotes", [])
        alt = next((q for q in quotes if q.get("supplier_id") != po.get("supplier_id")), None)
        incident_date = "2026-11-06"
        alt_arrival = incident_date
        if alt:
            try:
                from datetime import date, timedelta

                alt_arrival = (
                    date.fromisoformat(incident_date)
                    + timedelta(days=int(float(alt.get("lead_days", 2))))
                ).isoformat()
            except (TypeError, ValueError):
                alt_arrival = incident_date
        risk_no = enterprise.calculate_delivery_risk(order_id, confirmed, settings=deps.settings)
        risk_realloc = enterprise.calculate_delivery_risk(order_id, incident_date, settings=deps.settings)
        risk_expedite = enterprise.calculate_delivery_risk(order_id, alt_arrival, settings=deps.settings)
        reservations = enterprise.get_wms_reservations(material_id, deps.settings)
        donor_id = reservations[0].get("order_id") if reservations else ""
        donor_order = enterprise.get_order(donor_id, deps.settings) if donor_id else {}
        donor_penalty = enterprise.calculate_penalty(donor_id, 1, deps.settings) if donor_id else {}
        alt_cost = None
        if alt:
            try:
                alt_cost = round(
                    float(alt.get("qty_kg", 0))
                    * (float(alt.get("price_rub_kg", 0)) - float(po.get("contract_price_rub_kg", 0)))
                    + float(alt.get("logistics_rub", 0)),
                    2,
                )
            except (TypeError, ValueError):
                alt_cost = None
        order_row = order.get("order", {})
        computed = {
            "order_id": order_id,
            "customer": order_row.get("customer_name"),
            "order_value_rub": float(order_row.get("total_rub", 0) or 0),
            "delivery_date": order_row.get("delivery_date"),
            "material_id": material_id,
            "need_kg": gap.get("need_kg"),
            "available_kg": gap.get("available_kg"),
            "deficit_kg": gap.get("deficit_kg"),
            "coverage_pct": gap.get("coverage_pct"),
            "purchase_order": {
                "po_id": po.get("po_id"),
                "qty_kg": po.get("qty_kg"),
                "status": po.get("status"),
                "block_reason": po.get("block_reason"),
                "paid": po.get("paid"),
                "invoice_price_rub_kg": po.get("price_rub_kg"),
                "contract_price_rub_kg": po.get("contract_price_rub_kg"),
                "confirmed_date": confirmed,
            },
            "no_action": risk_no,
            "reallocate": risk_realloc,
            "expedite": {
                **risk_expedite,
                "material_arrival_date": alt_arrival,
                "extra_cost_rub": alt_cost,
            },
            "donor": {
                "order_id": donor_id,
                "value_rub": float((donor_order.get("order") or {}).get("total_rub", 0) or 0),
                "penalty_1_day_rub": donor_penalty.get("penalty_rub"),
            },
        }
        fact = (
            f"ATP={gap.get('available_kg')} кг, дефицит={gap.get('deficit_kg')} кг; "
            f"без действий просрочка {risk_no.get('days_late')} дн., "
            f"штраф {risk_no.get('penalty_rub')} ₽"
        )
        return {"computed": computed, "evidence": [Evidence(source="Расчёт", ref="measure", fact=fact)]}

    async def assess(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structured = build_structured(deps.llm, OpsReport)
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=assess_prompt(state))]
        try:
            report: OpsReport = await structured.ainvoke(messages, config=deps.config(state["thread_id"]))
        except Exception as exc:  # noqa: BLE001 - one repair attempt
            if tracer:
                tracer.record("assess", "retry", error=str(exc))
            messages.append(
                HumanMessage(
                    content=(
                        "Предыдущий ответ не прошёл валидацию схемы OpsReport. "
                        f"Ошибка: {exc}. Верни строго валидный JSON по схеме, без пояснений."
                    )
                )
            )
            report = await structured.ainvoke(messages, config=deps.config(state["thread_id"]))
        if report is None:
            if tracer:
                tracer.record("assess", "retry_empty")
            short = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        "Вызови инструмент OpsReport сейчас, без длинных рассуждений. "
                        "Используй эти расчёты как истину: "
                        + json.dumps(state.get("computed") or {}, ensure_ascii=False, default=str)
                    )
                ),
            ]
            report = await structured.ainvoke(short, config=deps.config(state["thread_id"]))
        if report is None:
            return {"status": "failed", "errors": ["assess: structured output returned None"]}
        updates: dict[str, Any] = {}
        if not report.incident_id:
            updates["incident_id"] = state.get("incident_id")
        if not report.evidence and state.get("evidence"):
            updates["evidence"] = state["evidence"][:8]
        if updates:
            report = report.model_copy(update=updates)
        return {"report": normalize_report(report)}

    async def approval(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        report = state.get("report")
        if report is None:
            return {"status": "failed", "errors": ["approval: отчёт не сформирован"]}
        actions = [a for a in report.recommended_actions if a.requires_approval]
        if not actions:
            return {"status": "completed"}
        thread_id = (state.get("thread_id") or (config or {}).get("configurable", {}).get("thread_id", "unknown"))
        flush_langfuse()
        request = ApprovalRequest(
            thread_id=thread_id,
            incident_id=report.incident_id,
            question=(
                "Требуется согласование действий по инциденту "
                f"{report.incident_id}. Подтверди или отклони."
            ),
            actions=actions,
        )
        raw = interrupt(request.model_dump(mode="json"))
        if isinstance(raw, dict):
            decision = ApprovalDecision(**raw)
        else:
            decision = ApprovalDecision(approved=bool(raw))
        return {"approval": request, "approval_decision": decision, "status": "completed"}

    async def finalize(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        thread_id = (state.get("thread_id") or (config or {}).get("configurable", {}).get("thread_id", "unknown"))
        run_dir: Path = deps.settings.runs_dir / thread_id
        run_dir.mkdir(parents=True, exist_ok=True)
        report = state.get("report")
        decision = state.get("approval_decision")
        if report is None:
            summary = {
                "thread_id": thread_id,
                "incident_id": state.get("incident_id"),
                "status": "failed",
                "errors": state.get("errors", []),
            }
            (run_dir / "state.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            return {"status": "failed"}
        if report is not None:
            (run_dir / "report.json").write_text(
                json.dumps(_jsonable(report), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with open(run_dir / "actions.jsonl", "a", encoding="utf-8") as f:
                for action in report.recommended_actions:
                    if action.requires_approval:
                        status = "pending" if decision is None else ("approved" if decision.approved else "rejected")
                    else:
                        status = "proposed"
                    f.write(
                        json.dumps(
                            {
                                "action_id": action.action_id,
                                "title": action.title,
                                "status": status,
                                "requires_approval": action.requires_approval,
                                "approver_role": action.approver_role,
                                "cost_rub": action.cost_rub,
                                "decision": _jsonable(decision),
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    )
        summary = {
            "thread_id": thread_id,
            "incident_id": state.get("incident_id"),
            "status": "completed",
            "evidence_count": len(state.get("evidence", [])),
            "iterations": state.get("iterations"),
            "tool_rounds": state.get("tool_rounds"),
            "approval": _jsonable(state.get("approval")),
            "approval_decision": _jsonable(decision),
            "errors": state.get("errors", []),
        }
        (run_dir / "state.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        if tracer:
            tracer.record("finalize", "written", thread_id=thread_id, run_dir=str(run_dir))
        flush_langfuse()
        return {"status": "completed"}

    def route_after_agent(state: AgentState) -> str:
        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        if (
            isinstance(last, AIMessage)
            and getattr(last, "tool_calls", None)
            and state.get("tool_rounds", 0) < deps.settings.max_tool_rounds
            and state.get("iterations", 0) < deps.settings.max_iterations
        ):
            return "tools"
        return "reflect"

    def route_after_reflect(state: AgentState) -> str:
        reflection = state.get("reflection")
        if reflection is not None and reflection.sufficient:
            return "measure"
        if state.get("iterations", 0) >= deps.settings.max_iterations:
            return "measure"
        return "agent"

    def route_after_assess(state: AgentState) -> str:
        report = state.get("report")
        if report and any(a.requires_approval for a in report.recommended_actions):
            return "approval"
        return "finalize"

    builder = StateGraph(AgentState)
    builder.add_node("intake", traced("intake")(intake))
    builder.add_node("plan", traced("plan")(plan))
    builder.add_node("agent", traced("agent")(agent))
    builder.add_node("tools", traced("tools")(tools))
    builder.add_node("collect", traced("collect")(collect))
    builder.add_node("reflect", traced("reflect")(reflect))
    builder.add_node("measure", traced("measure")(measure))
    builder.add_node("assess", traced("assess")(assess))
    builder.add_node("approval", traced("approval")(approval))
    builder.add_node("finalize", traced("finalize")(finalize))

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "plan")
    builder.add_edge("plan", "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "reflect": "reflect"})
    builder.add_edge("tools", "collect")
    builder.add_edge("collect", "agent")
    builder.add_conditional_edges("reflect", route_after_reflect, {"agent": "agent", "measure": "measure"})
    builder.add_edge("measure", "assess")
    builder.add_conditional_edges("assess", route_after_assess, {"approval": "approval", "finalize": "finalize"})
    builder.add_edge("approval", "finalize")
    builder.add_edge("finalize", END)

    if deps.checkpointer is not None:
        return builder.compile(checkpointer=deps.checkpointer)
    return builder.compile()
