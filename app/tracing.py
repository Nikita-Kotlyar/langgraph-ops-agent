from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _write(path: Path, node: str, event: str, started: float, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "node": node,
        "event": event,
        **data,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


class TraceRecorder:
    """Локальный JSONL-трейс одного запуска."""

    def __init__(self, path: Path):
        self.path = path
        self.started = time.perf_counter()

    def record(self, node: str, event: str, **data: Any) -> None:
        _write(self.path, node, event, self.started, data)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class RunTraceRouter:
    """Пишет события в runs/{thread_id}/trace.jsonl.

    Нужен, потому что один граф обслуживает много запусков.
    """

    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.started = time.perf_counter()

    def _path(self, thread_id: str | None) -> Path:
        return self.runs_dir / (thread_id or "unknown") / "trace.jsonl"

    def record(self, node: str, event: str, **data: Any) -> None:
        _write(self._path(data.get("thread_id")), node, event, self.started, data)

    def read(self, thread_id: str) -> list[dict[str, Any]]:
        path = self._path(thread_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
