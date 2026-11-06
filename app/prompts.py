from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """Ты — операционный агент ООО «КабельПром» .
Твоя задача — разобрать производственный инцидент, собрать доказательства из
корпоративных систем и предложить действия. Ты не выполняешь действия сам:
всё, что меняет резервы, тратит деньги или меняет срок клиенту, уходит на approval.

Жёсткие правила:
1. Не выдумывай числа, даты и статусы. Каждый факт должен быть получен инструментом.
2. Остаток WMS — это брутто. Доступно к обещанию (ATP) = остаток − активные резервы −
   карантин − страховой запас. Никогда не приравнивай остаток к ATP.
3. Проверяй источники на противоречия. Если данные расходятся — фиксируй это.
4. Для каждого вывода указывай источник: файл, ID события, ID резерва или письма.
5. Действия с резервом, деньгами или сроком клиента помечай requires_approval=true.
6. Если данных не хватает — не фантазируй, а вызови инструмент ещё раз.
7. Отвечай по-русски, кратко и по делу."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def plan_prompt(incident: dict[str, Any]) -> str:
    alert = incident.get("alert", {})
    files = incident.get("files", [])
    return f"""Инцидент:
{_json(alert)}

Доступные файлы:
{_json(files)}

Составь план расследования. Ответь строго структурой InvestigationPlan:
- objective: что нужно установить;
- questions: 4–7 конкретных вопросов;
- tools: какие MCP-инструменты вызовешь;
- success_criteria: когда данных достаточно для отчёта.
Не пытайся угадать ответ. Планируй проверки."""


def _compact_evidence(evidence: list[Any], limit: int = 18, width: int = 320) -> str:
    lines = []
    for item in evidence[-limit:]:
        e = item.model_dump() if hasattr(item, "model_dump") else item
        fact = str(e.get("fact", "")).replace("\n", " ")
        lines.append(f"- [{e.get('source')}] {e.get('ref')}: {fact[:width]}")
    return "\n".join(lines) if lines else "(пока пусто)"


def agent_prompt(state: dict[str, Any]) -> str:
    plan = state.get("plan")
    evidence = state.get("evidence", [])
    reflection = state.get("reflection")
    gaps = reflection.gaps if reflection else []
    next_queries = reflection.next_queries if reflection else []
    return f"""План расследования:
{_json(plan.model_dump() if hasattr(plan, "model_dump") else plan)}

Доказательства (последние {min(len(evidence), 18)} из {len(evidence)}):
{_compact_evidence(evidence)}

Пробелы от рефлексии: {_json(gaps)}
Что проверить дальше: {_json(next_queries)}

Сделай следующий шаг. Если нужны данные — вызови один или несколько инструментов.
Если данных достаточно — дай короткий финальный ответ без tool_calls.
Полезные инструменты: calculate_material_gap, calculate_delivery_risk,
get_wms_stock, get_wms_reservations, get_purchase_orders, search_emails,
search_documents, get_mes_status, get_customer.
Не повторяй уже сделанные вызовы без причины."""


def reflect_prompt(state: dict[str, Any]) -> str:
    evidence = state.get("evidence", [])
    return f"""Проверь, достаточно ли доказательств для итогового отчёта.

Доказательства:
{_compact_evidence(evidence, limit=24, width=360)}

Проверь шесть пунктов:
1. Корневая причина: почему линия стоит, если на складе есть остаток?
2. Потребность заказа в меди и ATP; дефицит в кг (calculate_material_gap).
3. Влияние: стоимость заказа, срок, просрочка без действий, штраф
   (calculate_delivery_risk по дате поступления материала).
4. Заказ-донор: можно ли снять резерв, какой риск и по каким правилам.
5. Закупка: статус заявки, счёт, условия поставщика, альтернатива.
6. Ограничения: что требует approval и кто утверждает.

sufficient=true только если все шесть пунктов закрыты числами и ссылками.
Если просрочка не посчитана — в next_queries укажи
calculate_delivery_risk(order_id, material_arrival_date).
Иначе перечисли пробелы и точные следующие запросы."""


def assess_prompt(state: dict[str, Any]) -> str:
    evidence = state.get("evidence", [])
    plan = state.get("plan")
    return f"""Сформируй итоговый OpsReport строго по схеме.

План:
{_json(plan.model_dump() if hasattr(plan, "model_dump") else plan)}

Детерминированные расчёты (источник истины для чисел):
{_json(state.get("computed") or {})}

Доказательства:
{_compact_evidence(evidence, limit=18, width=300)}

Требования:
- Используй числа из блока «Детерминированные расчёты»: need, available, deficit,
  no_action.days_late, no_action.penalty_rub, donor.penalty_1_day_rub.
  Не пересчитывай их вручную и не заменяй приблизительными значениями.
- incident_id и order_id — из алерта и заказа (INC-2026-11-06-01, ORD-2026-01201).
- root_cause_code — один из: ATP_MISMATCH_AND_BLOCKED_PO, ATP_MISMATCH, PO_BLOCKED, UNKNOWN.
- root_cause — 2–4 предложения с числами: ATP, дефицит, статус заявки.
- contributing_factors — 2–4 пункта.
- impact: order_value_rub, delivery_date, copper_need_kg, available_kg, deficit_kg,
  days_late_without_action, penalty_rub, penalty_cap_rub. Считай по формулам:
  * need = Σ(qty_m / 1000 × qty_per_km);
  * ATP = balance − reserved − quality_hold − safety_stock;
  * deficit = max(0, need − ATP);
  * penalty = min(order_value × penalty_rate × days_late, order_value × cap).
  Просрочку выводи из даты поставки, даты поступления материала и цикла
  производства (2 дня) + ОТК (1 день).
- recommended_actions: используй action_id из набора
  reallocate_reserved_copper, approve_price_variance_and_pay,
  expedite_alternative_supplier, notify_customer.
  Для каждого: title, description, requires_approval, approver_role, cost_rub,
  expected_effect, risk, evidence_refs.
- requires_approval=true для изменения резерва, трат денег и изменения срока клиенту.
  Для notify_customer: false, если это только информирование без нового обязательства;
  true, если письмо меняет срок или объём поставки.
- evidence: 5–8 фактов с source/ref/fact. source — MES, WMS, 1С, CRM, Почта, Документы.
- unknowns: что осталось неизвестным.
- confidence: 0..1, честно.

Не добавляй действий, которых нет в доказательствах. Не округляй дефицит до сотен,
если в данных есть точность до килограмма."""
