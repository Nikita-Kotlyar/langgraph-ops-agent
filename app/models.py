from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Evidence(BaseModel):
    """Факт, подтверждённый конкретным источником."""

    source: str = Field(description="Система или файл: MES, WMS, 1С, CRM, Почта, Документы")
    ref: str = Field(description="Ссылка: файл, строка, ID события или резерва")
    fact: str = Field(description="Короткий проверяемый факт")


class InvestigationPlan(BaseModel):
    """План расследования, который строит LLM на первом шаге."""

    objective: str = Field(description="Что нужно установить")
    questions: list[str] = Field(min_length=1, description="Вопросы, на которые нужен ответ")
    tools: list[str] = Field(min_length=1, description="Какие инструменты понадобятся")
    success_criteria: list[str] = Field(min_length=1, description="Когда расследование можно считать завершённым")


class Reflection(BaseModel):
    """Проверка полноты собранных данных."""

    sufficient: bool = Field(description="Достаточно ли данных для отчёта")
    gaps: list[str] = Field(default_factory=list, description="Чего не хватает")
    next_queries: list[str] = Field(default_factory=list, description="Что проверить дальше")
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность в полноте 0..1")


class ActionRecommendation(BaseModel):
    """Предложение действия. Выполняется только после approval, если он нужен."""

    action_id: str = Field(description="snake_case идентификатор")
    title: str
    description: str
    requires_approval: bool
    approver_role: str = Field(default="", description="operations_director, procurement_head, finance_director")
    cost_rub: float = 0.0
    expected_effect: str
    risk: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class Impact(BaseModel):
    """Количественная оценка влияния инцидента."""

    order_value_rub: float
    delivery_date: str
    copper_need_kg: float
    available_kg: float
    deficit_kg: float
    days_late_without_action: int = Field(ge=0)
    penalty_rub: float = Field(ge=0)
    penalty_cap_rub: float = Field(ge=0)
    material_by_1110_kg: float | None = None
    coverage_by_1110_pct: float | None = None


class OpsReport(BaseModel):
    """Итоговый структурированный отчёт агента."""

    incident_id: str
    order_id: str
    customer: str
    status: Literal["investigated", "needs_more_data", "failed"] = "investigated"
    root_cause_code: str = Field(description="Короткий машинный код причины")
    root_cause: str = Field(description="Причина на естественном языке, с числами")
    contributing_factors: list[str] = Field(default_factory=list)
    impact: Impact
    recommended_actions: list[ActionRecommendation] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)
    unknowns: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("recommended_actions")
    @classmethod
    def unique_actions(cls, value: list[ActionRecommendation]) -> list[ActionRecommendation]:
        ids = [a.action_id for a in value]
        if len(ids) != len(set(ids)):
            raise ValueError("action_id должен быть уникальным")
        return value


class ApprovalRequest(BaseModel):
    thread_id: str
    incident_id: str
    question: str
    actions: list[ActionRecommendation]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalDecision(BaseModel):
    approved: bool
    comment: str = ""
    approver: str = "human"
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunResponse(BaseModel):
    thread_id: str
    incident_id: str
    status: Literal["running", "awaiting_approval", "completed", "failed"]
    report: OpsReport | None = None
    approval_request: ApprovalRequest | None = None
    trace_id: str | None = None
    error: str | None = None


class IncidentSummary(BaseModel):
    incident_id: str
    severity: str
    machine_id: str
    order_id: str
    status: str
