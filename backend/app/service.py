from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException

from .models import (
    ApproveResponse,
    CreateJobResponse,
    FailureCode,
    HealthResponse,
    HistoryItem,
    HistoryResponse,
    JobDetailResponse,
    JobStatus,
    MatchItem,
    MetricsResponse,
    QualityMode,
    RerankRequest,
    RerankResponse,
    RetryResponse,
    ScoreBreakdown,
)

STEP_SECONDS = 0.05
DEFAULT_STATE_FILE = Path(os.getenv("JOB_STATE_FILE", "./data/job_state.json"))


@dataclass
class JobRecord:
    job_id: UUID
    status: JobStatus
    quality_mode: QualityMode
    look_count: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    progress: int = 0
    theme: Optional[str] = None
    tone: Optional[str] = None
    items: list[MatchItem] = field(default_factory=list)
    preview_url: Optional[str] = None
    video_url: Optional[str] = None
    failure_code: Optional[FailureCode] = None
    had_partial_match: bool = False
    parent_job_id: Optional[UUID] = None
    attempts: int = 1
    idempotency_key: Optional[str] = None


class JobService:
    def __init__(self, state_file: Path | None = None) -> None:
        self._jobs: dict[UUID, JobRecord] = {}
        self._idempotency_map: dict[str, UUID] = {}
        self._lock = threading.Lock()
        self._booted_at = time.time()
        self._state_file = (state_file or DEFAULT_STATE_FILE).resolve()
        self._load_state()

    def create_job(
        self,
        look_count: int,
        quality_mode: QualityMode,
        theme: str | None,
        tone: str | None,
        idempotency_key: str | None = None,
        parent_job_id: UUID | None = None,
        attempts: int = 1,
    ) -> CreateJobResponse:
        with self._lock:
            if idempotency_key and idempotency_key in self._idempotency_map:
                existing = self._jobs[self._idempotency_map[idempotency_key]]
                return CreateJobResponse(job_id=existing.job_id, status=existing.status, estimated_seconds=2)

            job_id = uuid4()
            record = JobRecord(
                job_id=job_id,
                status=JobStatus.INGESTED,
                quality_mode=quality_mode,
                look_count=look_count,
                created_at=datetime.now(timezone.utc),
                progress=5,
                theme=theme,
                tone=tone,
                parent_job_id=parent_job_id,
                attempts=attempts,
                idempotency_key=idempotency_key,
            )
            self._jobs[job_id] = record
            if idempotency_key:
                self._idempotency_map[idempotency_key] = job_id
            self._persist_locked()

        t = threading.Thread(target=self._run_pipeline, args=(job_id,), daemon=True)
        t.start()

        return CreateJobResponse(job_id=job_id, status=record.status, estimated_seconds=2)

    def get_job(self, job_id: UUID) -> JobDetailResponse:
        record = self._get(job_id)
        return self._to_job_detail(record)

    def rerank(self, job_id: UUID, req: RerankRequest) -> RerankResponse:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise HTTPException(status_code=404, detail="job not found")
            if record.status in {JobStatus.FAILED, JobStatus.INGESTED, JobStatus.ANALYZED}:
                raise HTTPException(status_code=409, detail="rerank not available in current status")

            candidates = self._build_candidates(req.category, req.price_cap, req.color_hint)
            selected = candidates[0]

            replaced = False
            for idx, item in enumerate(record.items):
                if item.category == req.category:
                    record.items[idx] = selected
                    replaced = True
                    break
            if not replaced:
                record.items.append(selected)
            self._persist_locked()

        return RerankResponse(job_id=job_id, category=req.category, candidates=candidates, selected=selected)

    def approve(self, job_id: UUID) -> ApproveResponse:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise HTTPException(status_code=404, detail="job not found")
            if record.status != JobStatus.REVIEW_REQUIRED:
                raise HTTPException(status_code=409, detail="approval only available for REVIEW_REQUIRED jobs")

            record.status = JobStatus.COMPLETED
            record.progress = 100
            record.completed_at = datetime.now(timezone.utc)
            record.video_url = record.video_url or f"https://cdn.example.com/video/{record.job_id}.mp4"
            self._persist_locked()

            return ApproveResponse(job_id=job_id, status=record.status, video_url=record.video_url)

    def retry(self, job_id: UUID) -> RetryResponse:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise HTTPException(status_code=404, detail="job not found")
            if record.status != JobStatus.FAILED:
                raise HTTPException(status_code=409, detail="retry only available for FAILED jobs")

            look_count = record.look_count
            quality_mode = record.quality_mode
            theme = record.theme
            tone = record.tone
            attempts = record.attempts + 1

        created = self.create_job(
            look_count=look_count,
            quality_mode=quality_mode,
            theme=theme,
            tone=tone,
            parent_job_id=job_id,
            attempts=attempts,
        )
        return RetryResponse(previous_job_id=job_id, new_job_id=created.job_id, status=created.status)

    def history(self, limit: int) -> HistoryResponse:
        with self._lock:
            records = sorted(self._jobs.values(), key=lambda r: r.created_at, reverse=True)[:limit]
            jobs = [
                HistoryItem(
                    job_id=r.job_id,
                    status=r.status,
                    created_at=r.created_at,
                    completed_at=r.completed_at,
                    thumbnail_url=r.preview_url,
                )
                for r in records
            ]
        return HistoryResponse(jobs=jobs)

    def health(self) -> HealthResponse:
        with self._lock:
            total_jobs = len(self._jobs)
        return HealthResponse(status="ok", uptime_seconds=int(time.time() - self._booted_at), total_jobs=total_jobs)

    def metrics(self) -> MetricsResponse:
        with self._lock:
            records = list(self._jobs.values())
        total_created = len(records)
        total_completed = sum(1 for r in records if r.status == JobStatus.COMPLETED)
        total_failed = sum(1 for r in records if r.status == JobStatus.FAILED)
        total_retried = sum(1 for r in records if r.parent_job_id is not None)
        durations = [
            (r.completed_at - r.created_at).total_seconds()
            for r in records
            if r.completed_at is not None and r.completed_at >= r.created_at
        ]
        avg_processing = sum(durations) / len(durations) if durations else 0.0
        return MetricsResponse(
            total_jobs_created=total_created,
            total_jobs_completed=total_completed,
            total_jobs_failed=total_failed,
            total_jobs_retried=total_retried,
            avg_processing_seconds=round(avg_processing, 3),
        )

    def _get(self, job_id: UUID) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise HTTPException(status_code=404, detail="job not found")
            return record

    def _to_job_detail(self, r: JobRecord) -> JobDetailResponse:
        return JobDetailResponse(
            job_id=r.job_id,
            status=r.status,
            quality_mode=r.quality_mode,
            look_count=r.look_count,
            progress=r.progress,
            items=r.items,
            preview_url=r.preview_url,
            video_url=r.video_url,
            failure_code=r.failure_code,
            parent_job_id=r.parent_job_id,
            attempts=r.attempts,
        )

    def _run_pipeline(self, job_id: UUID) -> None:
        transitions = [
            (JobStatus.ANALYZED, 20),
            (self._matched_state, 45),
            (JobStatus.COMPOSED, 70),
            (JobStatus.RENDERING, 85),
        ]

        for state, progress in transitions:
            time.sleep(STEP_SECONDS)
            with self._lock:
                rec = self._jobs.get(job_id)
                if not rec:
                    return
                next_state = state(rec) if callable(state) else state
                rec.status = next_state
                rec.progress = progress
                if next_state in {JobStatus.MATCHED, JobStatus.MATCHED_PARTIAL}:
                    rec.had_partial_match = next_state == JobStatus.MATCHED_PARTIAL
                    rec.items = self._build_match_items(rec.look_count, partial=(next_state == JobStatus.MATCHED_PARTIAL))
                    rec.preview_url = f"https://cdn.example.com/preview/{job_id}.jpg"
                self._persist_locked()

        time.sleep(STEP_SECONDS)
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return

            if rec.quality_mode == QualityMode.human_review:
                rec.status = JobStatus.REVIEW_REQUIRED
                rec.progress = 95
                rec.video_url = f"https://cdn.example.com/video/{job_id}.mp4"
                self._persist_locked()
                return

            if rec.had_partial_match and random.random() < 0.5:
                rec.status = JobStatus.FAILED
                rec.progress = 100
                rec.completed_at = datetime.now(timezone.utc)
                rec.failure_code = FailureCode.EMPTY_RESULT
                self._persist_locked()
                return

            if random.random() < 0.1:
                rec.status = JobStatus.FAILED
                rec.progress = 100
                rec.completed_at = datetime.now(timezone.utc)
                rec.failure_code = FailureCode.RENDER_ERROR
                self._persist_locked()
                return

            rec.status = JobStatus.COMPLETED
            rec.progress = 100
            rec.completed_at = datetime.now(timezone.utc)
            rec.video_url = f"https://cdn.example.com/video/{job_id}.mp4"
            self._persist_locked()

    @staticmethod
    def _matched_state(record: JobRecord) -> JobStatus:
        return JobStatus.MATCHED_PARTIAL if record.look_count >= 4 else JobStatus.MATCHED

    @staticmethod
    def _build_match_items(look_count: int, partial: bool) -> list[MatchItem]:
        categories = ["outer", "top", "bottom", "shoes", "accessory"]
        items: list[MatchItem] = []
        for idx in range(look_count):
            failure_code = FailureCode.CRAWL_TIMEOUT if partial and idx == look_count - 1 else None
            score = ScoreBreakdown(
                image=0.88,
                text=0.74,
                category=0.92,
                price=0.67,
                final=0.83,
            )
            items.append(
                MatchItem(
                    category=categories[idx % len(categories)],
                    product_id=f"P-{idx + 1}",
                    brand="MUSINSA",
                    product_name=f"Styled Item {idx + 1}",
                    price=49900 + idx * 7000,
                    product_url=f"https://store.example.com/products/P-{idx + 1}",
                    image_url=f"https://store.example.com/images/P-{idx + 1}.jpg",
                    evidence_tags=["color_match", "silhouette", "material"],
                    score_breakdown=score,
                    failure_code=failure_code,
                )
            )
        return items

    @staticmethod
    def _build_candidates(category: str, price_cap: Optional[int], color_hint: Optional[str]) -> list[MatchItem]:
        cap = price_cap if price_cap is not None else 80000
        safe_cap = max(cap, 10000)
        color_hint_text = color_hint.strip().lower() if color_hint else ""
        candidates = []
        for idx in range(3):
            price = max(10000, safe_cap - idx * 5000)
            score = ScoreBreakdown(
                image=0.80 - idx * 0.03,
                text=0.70,
                category=0.90,
                price=0.85 if price <= safe_cap else 0.50,
                final=0.82 - idx * 0.02,
            )
            evidence_tags = ["re_ranked", "budget_fit"]
            if color_hint_text:
                evidence_tags.append(f"color:{color_hint_text}")
            candidates.append(
                MatchItem(
                    category=category,
                    product_id=f"R-{idx + 1}",
                    brand="MUSINSA",
                    product_name=f"{category.title()} {color_hint_text.title() + ' ' if color_hint_text else ''}Candidate {idx + 1}",
                    price=price,
                    product_url=f"https://store.example.com/products/R-{idx + 1}",
                    image_url=f"https://store.example.com/images/R-{idx + 1}.jpg",
                    evidence_tags=evidence_tags,
                    score_breakdown=score,
                )
            )
        return candidates

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        jobs_payload = payload.get("jobs", [])
        idem_payload = payload.get("idempotency_map", {})
        for raw in jobs_payload:
            try:
                rec = self._record_from_dict(raw)
            except Exception:
                continue
            self._jobs[rec.job_id] = rec
        for key, raw_job_id in idem_payload.items():
            try:
                job_id = UUID(raw_job_id)
            except ValueError:
                continue
            if job_id in self._jobs:
                self._idempotency_map[key] = job_id

    def _persist_locked(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "jobs": [self._record_to_dict(r) for r in self._jobs.values()],
            "idempotency_map": {k: str(v) for k, v in self._idempotency_map.items()},
        }
        tmp_path = self._state_file.with_suffix(f".{uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        tmp_path.replace(self._state_file)

    @staticmethod
    def _record_to_dict(record: JobRecord) -> dict:
        return {
            "job_id": str(record.job_id),
            "status": record.status.value,
            "quality_mode": record.quality_mode.value,
            "look_count": record.look_count,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            "progress": record.progress,
            "theme": record.theme,
            "tone": record.tone,
            "items": [item.model_dump(mode="json") for item in record.items],
            "preview_url": record.preview_url,
            "video_url": record.video_url,
            "failure_code": record.failure_code.value if record.failure_code else None,
            "had_partial_match": record.had_partial_match,
            "parent_job_id": str(record.parent_job_id) if record.parent_job_id else None,
            "attempts": record.attempts,
            "idempotency_key": record.idempotency_key,
        }

    @staticmethod
    def _record_from_dict(raw: dict) -> JobRecord:
        return JobRecord(
            job_id=UUID(raw["job_id"]),
            status=JobStatus(raw["status"]),
            quality_mode=QualityMode(raw["quality_mode"]),
            look_count=int(raw["look_count"]),
            created_at=datetime.fromisoformat(raw["created_at"]),
            completed_at=datetime.fromisoformat(raw["completed_at"]) if raw.get("completed_at") else None,
            progress=int(raw.get("progress", 0)),
            theme=raw.get("theme"),
            tone=raw.get("tone"),
            items=[MatchItem.model_validate(item) for item in raw.get("items", [])],
            preview_url=raw.get("preview_url"),
            video_url=raw.get("video_url"),
            failure_code=FailureCode(raw["failure_code"]) if raw.get("failure_code") else None,
            had_partial_match=bool(raw.get("had_partial_match", False)),
            parent_job_id=UUID(raw["parent_job_id"]) if raw.get("parent_job_id") else None,
            attempts=int(raw.get("attempts", 1)),
            idempotency_key=raw.get("idempotency_key"),
        )
