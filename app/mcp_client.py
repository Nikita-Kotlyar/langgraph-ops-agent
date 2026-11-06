from __future__ import annotations

import os
import sys

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from .config import PROJECT_ROOT, Settings


async def load_mcp_tools(settings: Settings) -> list[BaseTool]:
    """Подключиться к MCP-серверу через stdio и загрузить инструменты.

    Схемы инструментов не дублируются в коде агента: их отдаёт MCP-сервер.
    """
    env = {
        **os.environ,
        "OPS_INCIDENT_DIR": str(settings.incident_dir),
        "OPS_ENTERPRISE_DIR": str(settings.enterprise_dir),
        "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PYTHONIOENCODING": "utf-8",
    }
    client = MultiServerMCPClient(
        {
            "enterprise": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "app.mcp_server"],
                "cwd": str(PROJECT_ROOT),
                "env": env,
                "encoding": "utf-8",
                "encoding_error_handler": "replace",
            }
        }
    )
    return await client.get_tools()
