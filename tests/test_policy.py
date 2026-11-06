from __future__ import annotations

import json

from app.config import PROJECT_ROOT
from app.models import OpsReport
from app.policy import normalize_report


def test_policy_sets_roles_from_regulation():
    path = PROJECT_ROOT / "data" / "expected" / "15_ops_incident.json"
    report = OpsReport.model_validate(json.loads(path.read_text(encoding="utf-8")))

    for action in report.recommended_actions:
        action.approver_role = "wrong_role"
        action.requires_approval = True

    normalize_report(report)
    roles = {a.action_id: a.approver_role for a in report.recommended_actions}
    assert roles["reallocate_reserved_copper"] == "operations_director"
    assert roles["approve_price_variance_and_pay"] == "procurement_head"
    assert roles["expedite_alternative_supplier"] == "finance_director"

    notify = next(a for a in report.recommended_actions if a.action_id == "notify_customer")
    assert notify.requires_approval is False
    assert notify.approver_role == ""
