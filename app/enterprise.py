"""Read-only доступ к данным цифрового двойника.

Все функции возвращают JSON-сериализуемые dict/list. Никакой записи,
никакого SQL и никакого shell.
"""
from __future__ import annotations

import csv
import email
import json
import re
from email import policy
from pathlib import Path
from typing import Any

from .config import Settings, get_settings


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _incident_dir(settings: Settings, incident_id: str | None = None) -> Path:
    if not incident_id or incident_id == settings.incident_id:
        return settings.incident_dir
    return settings.enterprise_dir / "incidents" / incident_id


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def _terms(query: str) -> list[str]:
    words = re.findall(r"[\wа-яё]+", query.lower(), flags=re.UNICODE)
    return [w for w in words if len(w) >= 3]


def _stem(term: str) -> str:
    """Грубый стеммер для русского: первые 5 символов.

    Этого достаточно для поиска «оплата» → «оплаты», «поставщик» → «поставщика».
    """
    return term[:5] if len(term) > 5 else term


def _matches(text: str, term: str) -> list[int]:
    stem = _stem(term)
    return [m.start() for m in re.finditer(rf"\b{re.escape(stem)}", text, flags=re.UNICODE)]


def _score(text: str, terms: list[str]) -> int:
    low = text.lower()
    return sum(len(_matches(low, t)) for t in terms)


def _excerpt(text: str, terms: list[str], width: int = 420) -> str:
    low = text.lower()
    positions = [m for t in terms for m in _matches(low, t)]
    pos = min(positions) if positions else 0
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def _docx_text(path: Path) -> str:
    try:
        from docx import Document
    except ImportError:  # pragma: no cover - optional at runtime
        return ""
    document = Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _email_text(path: Path) -> dict[str, str]:
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    body += part.get_content()
                except Exception:  # pragma: no cover - malformed part
                    continue
    else:
        try:
            body = msg.get_content()
        except Exception:  # pragma: no cover - malformed message
            body = ""
    return {
        "subject": str(msg.get("Subject", "")),
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "date": str(msg.get("Date", "")),
        "body": body,
    }


# ---------------------------------------------------------------------------
# READ tools
# ---------------------------------------------------------------------------
def get_incident(incident_id: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    """Вернуть алерт, README и индекс файлов инцидента."""
    settings = settings or get_settings()
    base = _incident_dir(settings, incident_id)
    alert_path = base / "alert.json"
    if not alert_path.exists():
        available = []
        root = settings.enterprise_dir / "incidents"
        if root.exists():
            available = sorted(p.name for p in root.iterdir() if (p / "alert.json").exists())
        return {
            "error": f"incident {incident_id or settings.incident_id} not found",
            "available": available,
            "hint": "Используй incident_id из списка available или вызови без параметра.",
        }
    alert = _read_json(alert_path)
    readme_path = base / "README.md"
    files = sorted(str(p.relative_to(base)).replace("\\", "/") for p in base.rglob("*") if p.is_file())
    return {
        "incident_id": alert.get("incident_id", base.name),
        "alert": alert,
        "readme": readme_path.read_text(encoding="utf-8") if readme_path.exists() else "",
        "files": files,
    }


def get_order(order_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """Шапка заказа и его строки из выгрузки 1С."""
    settings = settings or get_settings()
    base = settings.incident_dir / "1c"
    orders = _read_csv(base / "orders.csv")
    order = next((o for o in orders if o.get("order_id") == order_id), None)
    if not order:
        return {"error": f"order {order_id} not found", "available": [o.get("order_id") for o in orders]}
    lines = [line for line in _read_csv(base / "order_lines.csv") if line.get("order_id") == order_id]
    return {"order": order, "lines": lines}


def get_bom(sku: str | None = None, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Нормы расхода материала на километр кабеля."""
    settings = settings or get_settings()
    rows = _read_csv(settings.incident_dir / "bom.csv")
    if sku:
        rows = [r for r in rows if r.get("sku") == sku]
    return rows


def get_wms_stock(material_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """Остаток WMS с расчётом доступного к обещанию количества (ATP)."""
    settings = settings or get_settings()
    rows = _read_csv(settings.incident_dir / "wms" / "stock_snapshot.csv")
    row = next((r for r in rows if r.get("material_id") == material_id), None)
    if not row:
        return {"error": f"material {material_id} not found", "available": [r.get("material_id") for r in rows]}
    balance = _num(row.get("balance_kg"))
    reserved = _num(row.get("reserved_kg"))
    hold = _num(row.get("quality_hold_kg"))
    safety = _num(row.get("safety_stock_kg"))
    available = _num(row.get("available_kg"), balance - reserved - hold - safety)
    return {
        "material_id": material_id,
        "material_name": row.get("material_name"),
        "warehouse": row.get("warehouse"),
        "balance_kg": round(balance, 3),
        "reserved_kg": round(reserved, 3),
        "quality_hold_kg": round(hold, 3),
        "safety_stock_kg": round(safety, 3),
        "available_kg": round(available, 3),
        "snapshot_at": row.get("snapshot_at"),
        "formula": "ATP = balance - reserved - quality_hold - safety_stock",
    }


def get_wms_reservations(material_id: str, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Активные резервы материала по заказам."""
    settings = settings or get_settings()
    rows = _read_csv(settings.incident_dir / "wms" / "reservations.csv")
    return [r for r in rows if r.get("material_id") == material_id and r.get("status") == "active"]


def get_mes_status(
    order_id: str | None = None,
    machine_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Производственные задания и события линии из MES."""
    settings = settings or get_settings()
    base = settings.incident_dir / "mes"
    work_orders = _read_csv(base / "work_orders.csv")
    events = _read_csv(base / "line_events.csv")
    if order_id:
        work_orders = [w for w in work_orders if w.get("order_id") == order_id]
        allowed = {w.get("work_order_id") for w in work_orders}
        events = [e for e in events if e.get("work_order_id") in allowed]
    if machine_id:
        work_orders = [w for w in work_orders if w.get("machine_id") == machine_id]
        events = [e for e in events if e.get("machine_id") == machine_id]
    return {"work_orders": work_orders, "line_events": events}


def get_purchase_orders(material_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """Заявки на закупку, счета и котировки по материалу."""
    settings = settings or get_settings()
    base = settings.incident_dir / "1c"
    po = [r for r in _read_csv(base / "purchase_orders.csv") if r.get("material_id") == material_id]
    invoices = [r for r in _read_csv(base / "invoices.csv") if r.get("po_id") in {p.get("po_id") for p in po}]
    quotes = [r for r in _read_csv(base / "supplier_quotes.csv") if r.get("material_id") == material_id]
    return {"purchase_orders": po, "invoices": invoices, "supplier_quotes": quotes}


def get_customer(customer_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """Профиль клиента и условия по его заказам."""
    settings = settings or get_settings()
    base = settings.incident_dir / "crm"
    customers: list[dict[str, Any]] = []
    if (base / "customers.json").exists():
        customers = _read_json(base / "customers.json")
    query = customer_id.strip().lower()
    customer = next(
        (
            c
            for c in customers
            if query
            and (
                query == str(c.get("customer_id", "")).lower()
                or query in str(c.get("name", "")).lower()
                or str(c.get("customer_id", "")).lower().startswith(query)
            )
        ),
        None,
    )
    if customer is None and (base / "customer.json").exists():
        single = _read_json(base / "customer.json")
        if query and (query in str(single.get("name", "")).lower() or query == str(single.get("customer_id", "")).lower()):
            customer = single
    resolved_id = customer.get("customer_id") if customer else customer_id
    orders = [r for r in _read_csv(base / "orders.csv") if r.get("customer_id") == resolved_id]
    if customer is None:
        return {"error": f"customer {customer_id} not found", "orders": orders}
    return {"customer": customer, "orders": orders}


def search_emails(query: str, limit: int = 5, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Поиск по письмам инцидента (тема, отправитель, тело)."""
    settings = settings or get_settings()
    terms = _terms(query)
    results: list[dict[str, Any]] = []
    for path in sorted((settings.incident_dir / "emails").glob("*.eml")):
        msg = _email_text(path)
        haystack = f"{msg['subject']}\n{msg['from']}\n{msg['body']}"
        score = _score(msg["subject"], terms) * 3 + _score(haystack, terms)
        if score <= 0 and terms:
            continue
        results.append(
            {
                "file": path.name,
                "subject": msg["subject"],
                "from": msg["from"],
                "date": msg["date"],
                "score": score,
                "body": _excerpt(msg["body"], terms, width=700) if terms else msg["body"][:500],
            }
        )
    results.sort(key=lambda r: (-r["score"], r["file"]))
    return results[: max(1, min(limit, 20))]


def search_documents(query: str, limit: int = 5, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Поиск по договору, регламенту инцидента и глобальным регламентам предприятия."""
    settings = settings or get_settings()
    terms = _terms(query)
    candidates: list[tuple[str, str, str]] = []
    for path in sorted((settings.incident_dir / "docs").glob("*.md")):
        candidates.append((str(path.relative_to(settings.incident_dir)), path.stem, path.read_text(encoding="utf-8")))
    reg_dir = settings.enterprise_dir / "docs" / "regulations"
    if reg_dir.exists():
        for path in sorted(reg_dir.glob("*.docx")):
            text = _docx_text(path)
            if text:
                candidates.append((f"docs/regulations/{path.name}", path.stem, text))
    results: list[dict[str, Any]] = []
    for rel, title, text in candidates:
        score = _score(title, terms) * 3 + _score(text, terms)
        if score <= 0 and terms:
            continue
        results.append(
            {
                "source": rel,
                "title": title,
                "score": score,
                "excerpt": _excerpt(text, terms, width=900) if terms else text[:900],
            }
        )
    results.sort(key=lambda r: (-r["score"], r["source"]))
    return results[: max(1, min(limit, 20))]


def calculate_material_gap(
    order_id: str,
    material_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Потребность заказа в материале, ATP и дефицит."""
    settings = settings or get_settings()
    order = get_order(order_id, settings)
    if "error" in order:
        return order
    bom = {r["sku"]: _num(r["qty_per_km"]) for r in get_bom(settings=settings) if r.get("material_id") == material_id}
    lines = []
    need = 0.0
    for line in order["lines"]:
        per_km = bom.get(line.get("sku", ""), 0.0)
        qty = _num(line.get("qty"))
        line_need = qty / 1000.0 * per_km
        need += line_need
        lines.append(
            {
                "sku": line.get("sku"),
                "qty": qty,
                "unit": line.get("unit"),
                "qty_per_km": round(per_km, 3),
                "need_kg": round(line_need, 3),
            }
        )
    stock = get_wms_stock(material_id, settings)
    if "error" in stock:
        return stock
    available = stock["available_kg"]
    deficit = max(0.0, need - available)
    coverage = (available / need * 100.0) if need else 0.0
    return {
        "order_id": order_id,
        "material_id": material_id,
        "lines": lines,
        "need_kg": round(need, 3),
        "available_kg": round(available, 3),
        "deficit_kg": round(deficit, 3),
        "coverage_pct": round(coverage, 1),
        "formula": "need = Σ(qty_m / 1000 × qty_per_km); deficit = max(0, need − ATP)",
    }


def calculate_penalty(order_id: str, days_late: int, settings: Settings | None = None) -> dict[str, Any]:
    """Неустойка по договору за указанное число дней просрочки."""
    settings = settings or get_settings()
    rows = _read_csv(settings.incident_dir / "crm" / "orders.csv")
    order = next((r for r in rows if r.get("order_id") == order_id), None)
    if not order:
        return {"error": f"order {order_id} not found", "available": [r.get("order_id") for r in rows]}
    value = _num(order.get("value_rub"))
    rate = _num(order.get("penalty_rate_per_day"))
    cap = _num(order.get("penalty_cap_share"))
    days = max(0, int(days_late))
    raw = value * rate * days
    cap_value = value * cap
    penalty = min(raw, cap_value)
    return {
        "order_id": order_id,
        "order_value_rub": round(value, 2),
        "penalty_rate_per_day": rate,
        "penalty_cap_share": cap,
        "days_late": days,
        "penalty_rub": round(penalty, 2),
        "penalty_cap_rub": round(cap_value, 2),
        "formula": "penalty = min(order_value × rate × days_late, order_value × cap)",
    }


def calculate_delivery_risk(
    order_id: str,
    material_arrival_date: str,
    production_days: int = 2,
    qa_days: int = 1,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Оценить просрочку и штраф, если материал придёт в указанную дату.

    Цикл: производство 2 дня + ОТК 1 день (регламент, п. 7).
    """
    from datetime import date, datetime, timedelta

    settings = settings or get_settings()

    def parse(value: str) -> date:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"cannot parse date: {value}")

    rows = _read_csv(settings.incident_dir / "crm" / "orders.csv")
    order = next((r for r in rows if r.get("order_id") == order_id), None)
    if not order:
        return {"error": f"order {order_id} not found"}
    delivery = parse(order.get("delivery_date", ""))
    arrival = parse(material_arrival_date)
    ready = arrival + timedelta(days=max(0, production_days) + max(0, qa_days))
    days_late = max(0, (ready - delivery).days)
    penalty = calculate_penalty(order_id, days_late, settings)
    return {
        "order_id": order_id,
        "delivery_date": delivery.isoformat(),
        "material_arrival_date": arrival.isoformat(),
        "production_days": production_days,
        "qa_days": qa_days,
        "expected_ready_date": ready.isoformat(),
        "days_late": days_late,
        "penalty_rub": penalty.get("penalty_rub", 0.0),
        "penalty_cap_rub": penalty.get("penalty_cap_rub", 0.0),
        "formula": "ready = arrival + production_days + qa_days; days_late = max(0, ready − delivery)",
    }


def list_incidents(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Список инцидентов в песочнице."""
    settings = settings or get_settings()
    root = settings.enterprise_dir / "incidents"
    result = []
    if not root.exists():
        return result
    for path in sorted(root.iterdir()):
        alert_path = path / "alert.json"
        if alert_path.exists():
            alert = _read_json(alert_path)
            result.append(
                {
                    "incident_id": alert.get("incident_id", path.name),
                    "severity": alert.get("severity", ""),
                    "machine_id": alert.get("machine_id", ""),
                    "order_id": alert.get("order_id", ""),
                    "status": alert.get("status", ""),
                }
            )
    return result
