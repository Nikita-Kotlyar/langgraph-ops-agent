from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from .models import (
    ApprovalDecision,
    ApprovalRequest,
    Evidence,
    InvestigationPlan,
    OpsReport,
    Reflection,
)


class AgentState(TypedDict, total=False):
    thread_id: str
    incident_id: str
    objective: str
    incident: dict
    plan: InvestigationPlan | None
    messages: Annotated[list[AnyMessage], add_messages]
    evidence: Annotated[list[Evidence], operator.add]
    reflection: Reflection | None
    computed: dict | None
    report: OpsReport | None
    approval: ApprovalRequest | None
    approval_decision: ApprovalDecision | None
    iterations: int
    tool_rounds: int
    max_iterations: int
    status: Literal["running", "awaiting_approval", "completed", "failed"]
    errors: Annotated[list[str], operator.add]
