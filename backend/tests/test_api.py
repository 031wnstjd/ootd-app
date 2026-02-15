from __future__ import annotations

import io
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main as api_main
from app.models import JobStatus
from app.service import JobService


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    state_file = Path(tempfile.mkdtemp(prefix="jobservice-test-")) / "job_state.json"
    monkeypatch.setattr(api_main, "service", JobService(state_file=state_file))
    return TestClient(api_main.app)


def _create_job(client: TestClient, quality_mode: str = "auto_gate", look_count: int = 3) -> str:
    resp = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"look_count": str(look_count), "quality_mode": quality_mode},
    )
    assert resp.status_code == 202
    return resp.json()["job_id"]


def _wait_for_terminal(client: TestClient, job_id: str, timeout: float = 3.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        res = client.get(f"/v1/jobs/{job_id}")
        assert res.status_code == 200
        payload = res.json()
        if payload["status"] in {"COMPLETED", "FAILED", "REVIEW_REQUIRED"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("job did not reach terminal status in time")


def _wait_for_status(client: TestClient, job_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        res = client.get(f"/v1/jobs/{job_id}")
        assert res.status_code == 200
        payload = res.json()
        if payload["status"] in statuses:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job did not reach expected statuses: {statuses}")


def _assert_match_item_shape(item: dict) -> None:
    assert isinstance(item["category"], str)
    assert isinstance(item["product_id"], str)
    assert isinstance(item["product_name"], str)
    assert isinstance(item["price"], int)
    assert item["price"] > 0
    assert item["product_url"].startswith("https://")
    assert item["image_url"].startswith("https://")
    assert isinstance(item["evidence_tags"], list)
    assert item["evidence_tags"]
    score = item["score_breakdown"]
    assert score is not None
    for key in ("image", "text", "category", "price", "final"):
        assert 0.0 <= score[key] <= 1.0


def _assert_job_detail_shape(payload: dict) -> None:
    assert isinstance(payload["job_id"], str)
    assert payload["status"] in {s.value for s in JobStatus}
    assert payload["quality_mode"] in {"auto_gate", "human_review"}
    assert 1 <= payload["look_count"] <= 5
    assert payload["progress"] is None or 0 <= payload["progress"] <= 100
    assert isinstance(payload.get("attempts"), int)


def test_auto_gate_job_progresses_and_history(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    job_id = _create_job(client, quality_mode="auto_gate", look_count=3)

    detail = _wait_for_terminal(client, job_id)
    _assert_job_detail_shape(detail)
    assert detail["status"] == JobStatus.COMPLETED.value
    assert detail["look_count"] == 3
    assert detail["quality_mode"] == "auto_gate"
    assert detail["progress"] == 100
    assert detail["video_url"].endswith(f"/{job_id}.mp4")
    assert len(detail["items"]) == 3
    for item in detail["items"]:
        _assert_match_item_shape(item)

    history = client.get("/v1/history?limit=1")
    assert history.status_code == 200
    jobs = history.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id


def test_human_review_requires_approval(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    job_id = _create_job(client, quality_mode="human_review", look_count=2)

    detail = _wait_for_terminal(client, job_id)
    _assert_job_detail_shape(detail)
    assert detail["status"] == JobStatus.REVIEW_REQUIRED.value
    assert detail["progress"] == 95
    assert detail["video_url"].endswith(f"/{job_id}.mp4")
    assert len(detail["items"]) == 2

    approval = client.post(f"/v1/jobs/{job_id}/approve")
    assert approval.status_code == 200
    body = approval.json()
    assert body["job_id"] == job_id
    assert body["status"] == JobStatus.COMPLETED.value
    assert body["video_url"].endswith(f"/{job_id}.mp4")


def test_partial_match_failure_and_rerank(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.0)
    job_id = _create_job(client, quality_mode="auto_gate", look_count=4)

    # Wait until matching is available for rerank.
    end = time.time() + 2
    while time.time() < end:
        status = client.get(f"/v1/jobs/{job_id}").json()["status"]
        if status in {
            JobStatus.MATCHED.value,
            JobStatus.MATCHED_PARTIAL.value,
            JobStatus.COMPOSED.value,
            JobStatus.RENDERING.value,
        }:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("job did not reach rerankable state in time")

    rr = client.post(f"/v1/jobs/{job_id}/rerank", json={"category": "top", "price_cap": 60000})
    assert rr.status_code == 200
    rerank = rr.json()
    assert rerank["job_id"] == job_id
    assert rerank["category"] == "top"
    assert len(rerank["candidates"]) == 3
    assert rerank["selected"] is not None
    assert rerank["selected"]["category"] == "top"
    for candidate in rerank["candidates"]:
        _assert_match_item_shape(candidate)
        assert candidate["category"] == "top"
        assert candidate["price"] <= 60000

    terminal = _wait_for_terminal(client, job_id)
    _assert_job_detail_shape(terminal)
    assert terminal["status"] == JobStatus.FAILED.value
    assert terminal["failure_code"] == "EMPTY_RESULT"
    assert len(terminal["items"]) == 4
    assert terminal["items"][-1]["failure_code"] == "CRAWL_TIMEOUT"


def test_create_job_validation_errors(client: TestClient) -> None:
    missing_image = client.post(
        "/v1/jobs",
        data={"look_count": "3", "quality_mode": "auto_gate"},
    )
    assert missing_image.status_code == 422

    missing_look_count = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"quality_mode": "auto_gate"},
    )
    assert missing_look_count.status_code == 422

    out_of_range_look_count = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"look_count": "6", "quality_mode": "auto_gate"},
    )
    assert out_of_range_look_count.status_code == 422

    invalid_quality_mode = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"look_count": "3", "quality_mode": "manual"},
    )
    assert invalid_quality_mode.status_code == 422

    missing_quality_mode = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"look_count": "3"},
    )
    assert missing_quality_mode.status_code == 422

    invalid_content_type = client.post(
        "/v1/jobs",
        files={"image": ("fit.txt", io.BytesIO(b"text"), "text/plain")},
        data={"look_count": "3", "quality_mode": "auto_gate"},
    )
    assert invalid_content_type.status_code == 422

    oversized = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"x" * (10 * 1024 * 1024 + 1)), "image/jpeg")},
        data={"look_count": "3", "quality_mode": "auto_gate"},
    )
    assert oversized.status_code == 413


def test_create_job_response_schema(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    resp = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"look_count": "3", "quality_mode": "auto_gate"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert isinstance(body["job_id"], str)
    assert body["status"] == JobStatus.INGESTED.value
    assert isinstance(body["estimated_seconds"], int)
    assert body["estimated_seconds"] > 0


def test_get_job_not_found_and_invalid_uuid(client: TestClient) -> None:
    missing = client.get(f"/v1/jobs/{uuid4()}")
    assert missing.status_code == 404

    invalid_uuid = client.get("/v1/jobs/not-a-uuid")
    assert invalid_uuid.status_code == 422


def test_rerank_error_scenarios(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    no_job = client.post(f"/v1/jobs/{uuid4()}/rerank", json={"category": "top"})
    assert no_job.status_code == 404

    missing_category = client.post(f"/v1/jobs/{uuid4()}/rerank", json={"price_cap": 50000})
    assert missing_category.status_code == 422

    invalid_uuid = client.post("/v1/jobs/not-a-uuid/rerank", json={"category": "top"})
    assert invalid_uuid.status_code == 422

    job_id = _create_job(client, quality_mode="auto_gate", look_count=3)

    blocked = client.post(f"/v1/jobs/{job_id}/rerank", json={"category": "top"})
    assert blocked.status_code == 409

    _wait_for_status(
        client,
        job_id,
        statuses={
            JobStatus.MATCHED.value,
            JobStatus.MATCHED_PARTIAL.value,
            JobStatus.COMPOSED.value,
            JobStatus.RENDERING.value,
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
        },
    )

    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    available = client.post(f"/v1/jobs/{job_id}/rerank", json={"category": "top", "price_cap": 43000})
    assert available.status_code == 200
    body = available.json()
    assert body["category"] == "top"
    for candidate in body["candidates"]:
        assert candidate["price"] <= 43000


def test_rerank_applies_color_hint_and_min_price_floor(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    job_id = _create_job(client, quality_mode="auto_gate", look_count=3)

    _wait_for_status(
        client,
        job_id,
        statuses={
            JobStatus.MATCHED.value,
            JobStatus.MATCHED_PARTIAL.value,
            JobStatus.COMPOSED.value,
            JobStatus.RENDERING.value,
            JobStatus.COMPLETED.value,
        },
    )

    rerank = client.post(
        f"/v1/jobs/{job_id}/rerank",
        json={"category": "shoes", "price_cap": 9000, "color_hint": "black"},
    )
    assert rerank.status_code == 200
    payload = rerank.json()
    assert payload["selected"]["category"] == "shoes"
    assert "Black" in payload["selected"]["product_name"]
    assert "color:black" in payload["selected"]["evidence_tags"]
    for candidate in payload["candidates"]:
        # Small caps are normalized to service floor.
        assert candidate["price"] >= 10000


def test_approve_error_scenarios(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    no_job = client.post(f"/v1/jobs/{uuid4()}/approve")
    assert no_job.status_code == 404

    invalid_uuid = client.post("/v1/jobs/not-a-uuid/approve")
    assert invalid_uuid.status_code == 422

    auto_job_id = _create_job(client, quality_mode="auto_gate", look_count=2)
    _wait_for_terminal(client, auto_job_id)
    invalid_state = client.post(f"/v1/jobs/{auto_job_id}/approve")
    assert invalid_state.status_code == 409

    human_job_id = _create_job(client, quality_mode="human_review", look_count=2)
    review_state = _wait_for_terminal(client, human_job_id)
    assert review_state["status"] == JobStatus.REVIEW_REQUIRED.value

    approved = client.post(f"/v1/jobs/{human_job_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == JobStatus.COMPLETED.value


def test_history_limit_bounds(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    job_ids = [_create_job(client, quality_mode="auto_gate", look_count=2) for _ in range(3)]
    for job_id in job_ids:
        _wait_for_terminal(client, job_id)

    limit_too_low = client.get("/v1/history?limit=0")
    assert limit_too_low.status_code == 200
    assert len(limit_too_low.json()["jobs"]) == 1

    limit_too_high = client.get("/v1/history?limit=999")
    assert limit_too_high.status_code == 200
    assert len(limit_too_high.json()["jobs"]) == 3


def test_create_job_idempotency_key_returns_same_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    headers = {"Idempotency-Key": "same-request-key"}
    first = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"look_count": "2", "quality_mode": "auto_gate"},
        headers=headers,
    )
    second = client.post(
        "/v1/jobs",
        files={"image": ("fit.jpg", io.BytesIO(b"imgbytes"), "image/jpeg")},
        data={"look_count": "2", "quality_mode": "auto_gate"},
        headers=headers,
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_retry_failed_job_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.0)
    failed_job_id = _create_job(client, quality_mode="auto_gate", look_count=4)
    terminal = _wait_for_terminal(client, failed_job_id)
    assert terminal["status"] == JobStatus.FAILED.value

    retry = client.post(f"/v1/jobs/{failed_job_id}/retry")
    assert retry.status_code == 202
    body = retry.json()
    assert body["previous_job_id"] == failed_job_id
    assert body["new_job_id"] != failed_job_id

    retried_detail = client.get(f"/v1/jobs/{body['new_job_id']}")
    assert retried_detail.status_code == 200
    retried_payload = retried_detail.json()
    assert retried_payload["attempts"] == 2
    assert retried_payload["parent_job_id"] == failed_job_id


def test_health_and_metrics_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    job_id = _create_job(client, quality_mode="auto_gate", look_count=2)
    _wait_for_terminal(client, job_id)

    health = client.get("/healthz")
    assert health.status_code == 200
    health_body = health.json()
    assert health_body["status"] == "ok"
    assert health_body["total_jobs"] >= 1

    metrics = client.get("/v1/metrics")
    assert metrics.status_code == 200
    metrics_body = metrics.json()
    assert metrics_body["total_jobs_created"] >= 1
    assert metrics_body["total_jobs_completed"] >= 1
    assert metrics_body["avg_processing_seconds"] >= 0


def test_history_default_limit_and_valid_limit(client: TestClient) -> None:
    for _ in range(25):
        _create_job(client, quality_mode="auto_gate", look_count=1)

    default_history = client.get("/v1/history")
    assert default_history.status_code == 200
    assert len(default_history.json()["jobs"]) == 20

    limit_two = client.get("/v1/history?limit=2")
    assert limit_two.status_code == 200
    assert len(limit_two.json()["jobs"]) == 2


def test_history_item_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.random.random", lambda: 0.99)
    job_id = _create_job(client, quality_mode="auto_gate", look_count=2)
    _wait_for_terminal(client, job_id)

    history = client.get("/v1/history?limit=1")
    assert history.status_code == 200
    item = history.json()["jobs"][0]
    assert item["job_id"] == job_id
    assert item["status"] in {s.value for s in JobStatus}
    assert isinstance(item["created_at"], str)
    assert "T" in item["created_at"]
    assert isinstance(item["completed_at"], str)
