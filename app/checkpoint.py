from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# Явный whitelist Pydantic-моделей, которые попадают в чекпойнт.
# Иначе LangGraph предупреждает о незнакомых типах при десериализации.
ALLOWED_MSGPACK_MODULES = [
    ("app.models", "InvestigationPlan"),
    ("app.models", "Evidence"),
    ("app.models", "Reflection"),
    ("app.models", "Impact"),
    ("app.models", "ActionRecommendation"),
    ("app.models", "OpsReport"),
    ("app.models", "ApprovalRequest"),
    ("app.models", "ApprovalDecision"),
]


@asynccontextmanager
async def sqlite_saver(path: Path):
    """AsyncSqliteSaver с явным whitelist моделей проекта."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    try:
        saver = AsyncSqliteSaver(
            conn,
            serde=JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_MODULES),
        )
        await saver.setup()
        yield saver
    finally:
        await conn.close()
