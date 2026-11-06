from __future__ import annotations

import json

import pytest

from app.mcp_client import load_mcp_tools

pytestmark = [pytest.mark.asyncio, pytest.mark.mcp]


def _payload(value):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                return json.loads(item["text"])
        return json.loads(value[0]["text"])
    return value


async def test_mcp_server_exposes_readonly_tools(settings):
    tools = await load_mcp_tools(settings)
    names = {tool.name for tool in tools}
    assert len(names) == 13
    assert {
        "get_incident",
        "get_order",
        "get_wms_stock",
        "calculate_material_gap",
        "calculate_delivery_risk",
        "search_emails",
        "search_documents",
    } <= names


async def test_mcp_tool_call_returns_atp(settings):
    tools = await load_mcp_tools(settings)
    by_name = {tool.name: tool for tool in tools}
    result = _payload(await by_name["get_wms_stock"].ainvoke({"material_id": "MAT-CU-2.5"}))
    assert result["available_kg"] == 200.0

    gap = _payload(
        await by_name["calculate_material_gap"].ainvoke(
            {"order_id": "ORD-2026-01201", "material_id": "MAT-CU-2.5"}
        )
    )
    assert gap["deficit_kg"] == pytest.approx(1501.212, abs=0.01)
