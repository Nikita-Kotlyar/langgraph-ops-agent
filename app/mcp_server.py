"""MCP-сервер с read-only инструментами предприятия.

Запуск:
    python -m app.mcp_server
Транспорт: stdio. Сервер не пишет в исходные данные.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import enterprise

mcp = FastMCP(
    "kabelprom-ops",
    instructions=(
        "Инструменты операционного агента кабельного завода. "
        "Все данные read-only. ATP считается как остаток минус резерв, карантин и страховой запас."
    ),
)


@mcp.tool()
def get_incident(incident_id: str = "INC-2026-11-06-01") -> dict[str, Any]:
    """Вернуть алерт инцидента, README и список доступных файлов."""
    return enterprise.get_incident(incident_id)


@mcp.tool()
def get_order(order_id: str) -> dict[str, Any]:
    """Вернуть шапку заказа и строки заказа из выгрузки 1С."""
    return enterprise.get_order(order_id)


@mcp.tool()
def get_bom(sku: str | None = None) -> list[dict[str, Any]]:
    """Вернуть нормы расхода меди на километр кабеля. Если sku не задан — все позиции."""
    return enterprise.get_bom(sku)


@mcp.tool()
def get_wms_stock(material_id: str) -> dict[str, Any]:
    """Вернуть остаток WMS с расчётом доступного к обещанию количества (ATP)."""
    return enterprise.get_wms_stock(material_id)


@mcp.tool()
def get_wms_reservations(material_id: str) -> list[dict[str, Any]]:
    """Вернуть активные резервы материала по заказам."""
    return enterprise.get_wms_reservations(material_id)


@mcp.tool()
def get_mes_status(order_id: str | None = None, machine_id: str | None = None) -> dict[str, Any]:
    """Вернуть производственные задания и события линии из MES."""
    return enterprise.get_mes_status(order_id=order_id, machine_id=machine_id)


@mcp.tool()
def get_purchase_orders(material_id: str) -> dict[str, Any]:
    """Вернуть заявки на закупку, счета и котировки поставщиков по материалу."""
    return enterprise.get_purchase_orders(material_id)


@mcp.tool()
def get_customer(customer_id: str) -> dict[str, Any]:
    """Вернуть профиль клиента и условия по его заказам."""
    return enterprise.get_customer(customer_id)


@mcp.tool()
def search_emails(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Найти письма по теме, отправителю или тексту. Возвращает фрагменты и имена файлов."""
    return enterprise.search_emails(query, limit)


@mcp.tool()
def search_documents(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Найти выдержки в договоре, регламенте инцидента и регламентах предприятия."""
    return enterprise.search_documents(query, limit)


@mcp.tool()
def calculate_material_gap(order_id: str, material_id: str) -> dict[str, Any]:
    """Посчитать потребность заказа в материале, ATP и дефицит."""
    return enterprise.calculate_material_gap(order_id, material_id)


@mcp.tool()
def calculate_penalty(order_id: str, days_late: int) -> dict[str, Any]:
    """Посчитать неустойку по договору за указанное число дней просрочки."""
    return enterprise.calculate_penalty(order_id, days_late)


@mcp.tool()
def calculate_delivery_risk(order_id: str, material_arrival_date: str) -> dict[str, Any]:
    """Оценить просрочку и штраф, если материал придёт в дату YYYY-MM-DD.

    Учитывает производственный цикл 2 дня и приёмку ОТК 1 день.
    """
    return enterprise.calculate_delivery_risk(order_id, material_arrival_date)


if __name__ == "__main__":
    mcp.run(transport="stdio")
