#!/usr/bin/env python
"""Сверка OpsReport с эталоном инцидента.

Пример:
    uv run python tools/check.py runs/<thread_id>/report.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "data" / "expected" / "15_ops_incident.json"


def _close(left: object, right: object, tolerance: float) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 2

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    actual = json.loads(path.read_text(encoding="utf-8"))
    impact = actual.get("impact") or {}
    expected_impact = expected["impact"]

    expected_actions = {item["action_id"] for item in expected["recommended_actions"]}
    actual_actions = {item.get("action_id") for item in (actual.get("recommended_actions") or [])}

    checks = [
        ("incident_id", actual.get("incident_id") == expected["incident_id"]),
        ("order_id", actual.get("order_id") == expected["order_id"]),
        ("root_cause_code", actual.get("root_cause_code") == expected["root_cause_code"]),
        ("deficit_kg", _close(impact.get("deficit_kg"), expected_impact["deficit_kg"], 5)),
        ("penalty_rub", _close(impact.get("penalty_rub"), expected_impact["penalty_rub"], 100)),
        ("days_late", impact.get("days_late_without_action") == expected_impact["days_late_without_action"]),
        ("actions", expected_actions.issubset(actual_actions)),
        ("confidence>=0.7", float(actual.get("confidence", 0)) >= 0.7),
    ]

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'OK   ' if ok else 'FAIL '}{name}")
    print("ИТОГ:", "OK" if not failed else "DIFF: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
