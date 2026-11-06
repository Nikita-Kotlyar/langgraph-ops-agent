from __future__ import annotations

from .models import OpsReport

# Порог из регламента: ускоренная закупка дороже 50 000 ₽ — финансовый директор.
EXPEDITE_FINANCE_THRESHOLD_RUB = 50_000.0


def normalize_report(report: OpsReport) -> OpsReport:
    """Привести роли согласующих к регламенту, не меняя суть действий.

    LLM хорошо формулирует действия, но может перепутать роль. Политика
    детерминирована и должна быть одинаковой в каждом прогоне.
    """
    for action in report.recommended_actions:
        if action.action_id == "reallocate_reserved_copper":
            action.approver_role = "operations_director"
        elif action.action_id == "approve_price_variance_and_pay":
            action.approver_role = "procurement_head"
        elif action.action_id == "expedite_alternative_supplier":
            action.approver_role = (
                "finance_director"
                if action.cost_rub > EXPEDITE_FINANCE_THRESHOLD_RUB
                else "procurement_head"
            )
        elif action.action_id == "notify_customer":
            # Информирование не меняет обязательства и не требует approval.
            action.approver_role = ""
            action.requires_approval = False
    return report
