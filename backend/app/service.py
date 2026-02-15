from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException

from .models import (
    ApproveResponse,
    CreateJobResponse,
    FailureCode,
    HistoryItem,
    HistoryResponse,
    JobDetailResponse,
    JobStatus,
    MatchItem,
    QualityMode,
    RerankRequest,
    RerankResponse,
    ScoreBreakdown,
)

STEP_SECONDS = 0.05


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


class JobService:
    def __init__(self) -> None:
        self._jobs: dict[UUID, JobRecord] = {}
        self._lock = threading.Lock()

    def create_job(self, look_count: int, quality_mode: QualityMode, theme: str | None, tone: str | None) -> CreateJobResponse:
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
        )
        with self._lock:
            self._jobs[job_id] = record

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

            candidates = self._build_candidates(req.category, req.price_cap)
            selected = candidates[0]

            replaced = False
            for idx, item in enumerate(record.items):
                if item.category == req.category:
                    record.items[idx] = selected
                    replaced = True
                    break
            if not replaced:
                record.items.append(selected)

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

            return ApproveResponse(job_id=job_id, status=record.status, video_url=record.video_url)

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

        time.sleep(STEP_SECONDS)
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return

            if rec.quality_mode == QualityMode.human_review:
                rec.status = JobStatus.REVIEW_REQUIRED
                rec.progress = 95
                rec.video_url = f"https://cdn.example.com/video/{job_id}.mp4"
                return

            if rec.had_partial_match and random.random() < 0.5:
                rec.status = JobStatus.FAILED
                rec.progress = 100
                rec.completed_at = datetime.now(timezone.utc)
                rec.failure_code = FailureCode.EMPTY_RESULT
                return

            if random.random() < 0.1:
                rec.status = JobStatus.FAILED
                rec.progress = 100
                rec.completed_at = datetime.now(timezone.utc)
                rec.failure_code = FailureCode.RENDER_ERROR
                return

            rec.status = JobStatus.COMPLETED
            rec.progress = 100
            rec.completed_at = datetime.now(timezone.utc)
            rec.video_url = f"https://cdn.example.com/video/{job_id}.mp4"

    @staticmethod
    def _matched_state(record: JobRecord) -> JobStatus:
        # Deterministic partial branch for larger requests keeps tests stable.
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
    def _build_candidates(category: str, price_cap: Optional[int]) -> list[MatchItem]:
        cap = price_cap if price_cap is not None else 80000
        candidates = []
        for idx in range(3):
            price = max(19000, cap - idx * 5000)
            score = ScoreBreakdown(
                image=0.80 - idx * 0.03,
                text=0.70,
                category=0.90,
                price=0.85 if price <= cap else 0.50,
                final=0.82 - idx * 0.02,
            )
            candidates.append(
                MatchItem(
                    category=category,
                    product_id=f"R-{idx + 1}",
                    brand="MUSINSA",
                    product_name=f"{category.title()} Candidate {idx + 1}",
                    price=price,
                    product_url=f"https://store.example.com/products/R-{idx + 1}",
                    image_url=f"https://store.example.com/images/R-{idx + 1}.jpg",
                    evidence_tags=["re_ranked", "budget_fit"],
                    score_breakdown=score,
                )
            )
        return candidates
