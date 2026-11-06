# Changelog

Все заметные изменения проекта фиксируются здесь.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/),
версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.1.0] — 2026-11-06

### Добавлено

- LangGraph-граф разбора инцидента: `intake → plan → agent ↔ tools → collect
  → reflect → measure → assess → approval → finalize`.
- 13 read-only MCP-инструментов через `FastMCP` и `MultiServerMCPClient` stdio.
- ReAct-цикл с tool calling, рефлексией и сбором evidence.
- Structured output: `InvestigationPlan`, `Reflection`, `OpsReport`.
- Human-in-the-loop: `interrupt()` и продолжение через `Command(resume=...)`.
- FastAPI: запуск, статус, approval, trace и аудит действий.
- LangFuse-трассировка: один трейс на запуск.
- AsyncSqliteSaver с whitelist Pydantic-моделей.
- Dockerfile, compose, healthcheck и CI.
- 22 теста, включая реальный MCP stdio.
- Кейс `INC-2026-11-06-01`: ATP, дефицит меди, штраф и план действий.

### Безопасность

- Инструменты только читают данные.
- Действия с резервами, деньгами и сроками клиента требуют approval.
- Секреты не попадают в репозиторий.
