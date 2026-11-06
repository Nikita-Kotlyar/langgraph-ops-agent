.PHONY: install test lint run serve tools docker-up docker-down

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check app tests

tools:
	uv run ops-agent tools

run:
	uv run ops-agent run --incident INC-2026-11-06-01 --approve

serve:
	uv run uvicorn app.api:app --host 0.0.0.0 --port 8095

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
