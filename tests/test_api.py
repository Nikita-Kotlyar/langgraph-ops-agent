from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import create_app


def test_api_run_pauses_and_resumes(graph_with_fake, settings):
    app = create_app(settings=settings, graph=graph_with_fake)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        incidents = client.get("/incidents")
        assert incidents.status_code == 200
        assert any(i["incident_id"] == settings.incident_id for i in incidents.json())

        response = client.post(f"/incidents/{settings.incident_id}/runs")
        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "awaiting_approval"
        assert payload["approval_request"]
        thread_id = payload["thread_id"]

        run = client.get(f"/runs/{thread_id}")
        assert run.status_code == 200
        assert run.json()["status"] == "awaiting_approval"

        approve = client.post(
            f"/runs/{thread_id}/approve",
            json={"approved": True, "comment": "ok", "approver": "test"},
        )
        assert approve.status_code == 200
        body = approve.json()
        assert body["status"] == "completed"
        assert body["report"]["root_cause_code"] == "ATP_MISMATCH_AND_BLOCKED_PO"

        actions = client.get(f"/runs/{thread_id}/actions")
        assert actions.status_code == 200
        assert actions.json()
        requiring = [item for item in actions.json() if item["requires_approval"]]
        not_requiring = [item for item in actions.json() if not item["requires_approval"]]
        assert requiring and all(item["status"] == "approved" for item in requiring)
        assert not_requiring and all(item["status"] == "proposed" for item in not_requiring)


def test_api_unknown_run_returns_404(graph_with_fake, settings):
    app = create_app(settings=settings, graph=graph_with_fake)
    with TestClient(app) as client:
        response = client.get("/runs/does-not-exist")
        assert response.status_code == 404
