from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse
from uuid import UUID, uuid4

from fastapi import HTTPException

from .models import (
    ApproveResponse,
    CatalogCrawlJobDetailResponse,
    CatalogCrawlJobResponse,
    CatalogIndexRebuildResponse,
    CatalogStatsResponse,
    CrawlJobStatus,
    CreateJobResponse,
    FailureCode,
    HealthResponse,
    HistoryItem,
    HistoryResponse,
    JobDetailResponse,
    JobStatus,
    MatchItem,
    MetricsResponse,
    PublishResponse,
    QualityMode,
    RerankRequest,
    RerankResponse,
    RetryResponse,
    ScoreBreakdown,
    YouTubeUploadStatus,
)

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except Exception:  # pragma: no cover
    Credentials = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]
    MediaFileUpload = None  # type: ignore[assignment]

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover
    imageio_ffmpeg = None  # type: ignore[assignment]

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


STEP_SECONDS = 0.05
DEFAULT_STATE_FILE = Path(os.getenv("JOB_STATE_FILE", "./data/job_state.json"))
DEFAULT_ASSET_ROOT = Path(os.getenv("ASSET_ROOT", "./data/assets"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
RENDER_SECONDS = float(os.getenv("RENDER_SECONDS", "4"))
ENABLE_REAL_RENDER = os.getenv("ENABLE_REAL_RENDER", "1") == "1"
YOUTUBE_UPLOAD_REQUIRED = os.getenv("YOUTUBE_UPLOAD_REQUIRED", "0") == "1"
YOUTUBE_PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "unlisted")
CATALOG_MIN_IMAGE_SIM = float(os.getenv("CATALOG_MIN_IMAGE_SIM", "0.35"))
CATALOG_MIN_ITEMS_PER_CATEGORY = int(os.getenv("CATALOG_MIN_ITEMS_PER_CATEGORY", "300"))
CATALOG_CRAWL_USE_IMAGE_EMBEDDING = os.getenv("CATALOG_CRAWL_USE_IMAGE_EMBEDDING", "1") == "1"


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
    upload_image_path: Optional[str] = None
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    youtube_upload_status: YouTubeUploadStatus = YouTubeUploadStatus.PENDING


@dataclass
class CatalogItemRecord:
    product_id: str
    category: str
    brand: str
    product_name: str
    product_url: str
    image_url: str
    price: Optional[int]
    embedding: list[float] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CrawlJobRecord:
    crawl_job_id: UUID
    status: CrawlJobStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_discovered: int = 0
    total_indexed: int = 0
    error_message: Optional[str] = None


class JobService:
    def __init__(
        self,
        state_file: Path | None = None,
        asset_root: Path | None = None,
        enable_real_render: bool = ENABLE_REAL_RENDER,
    ) -> None:
        self._jobs: dict[UUID, JobRecord] = {}
        self._catalog: dict[str, CatalogItemRecord] = {}
        self._crawl_jobs: dict[UUID, CrawlJobRecord] = {}
        self._idempotency_map: dict[str, UUID] = {}
        self._lock = threading.Lock()
        self._booted_at = time.time()
        self._state_file = (state_file or DEFAULT_STATE_FILE).resolve()
        self._asset_root = (asset_root or DEFAULT_ASSET_ROOT).resolve()
        self._uploads_dir = self._asset_root / "uploads"
        self._previews_dir = self._asset_root / "previews"
        self._videos_dir = self._asset_root / "videos"
        self._catalog_cache_dir = self._asset_root / "catalog-cache"
        self._enable_real_render = enable_real_render
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        self._previews_dir.mkdir(parents=True, exist_ok=True)
        self._videos_dir.mkdir(parents=True, exist_ok=True)
        self._catalog_cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

    @property
    def asset_root(self) -> Path:
        return self._asset_root

    def create_job(
        self,
        look_count: int,
        quality_mode: QualityMode,
        theme: str | None,
        tone: str | None,
        image_bytes: bytes,
        image_content_type: str | None,
        idempotency_key: str | None = None,
        parent_job_id: UUID | None = None,
        attempts: int = 1,
    ) -> CreateJobResponse:
        with self._lock:
            if idempotency_key and idempotency_key in self._idempotency_map:
                existing = self._jobs[self._idempotency_map[idempotency_key]]
                return CreateJobResponse(job_id=existing.job_id, status=existing.status, estimated_seconds=2)

            job_id = uuid4()
            upload_path = self._save_upload(job_id, image_bytes, image_content_type)
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
                upload_image_path=str(upload_path),
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
            upload_image_path = record.upload_image_path

        seed = JobRecord(
            job_id=job_id,
            status=JobStatus.MATCHED,
            quality_mode=QualityMode.auto_gate,
            look_count=1,
            created_at=datetime.now(timezone.utc),
            upload_image_path=upload_image_path,
        )
        candidates = self._build_candidates(seed, req.category, req.price_cap, req.color_hint)
        if not candidates:
            raise HTTPException(status_code=409, detail="no rerank candidates found")
        selected = candidates[0]

        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise HTTPException(status_code=404, detail="job not found")

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
            self._persist_locked()

            video_url = record.video_url

        self._attempt_youtube_upload(job_id)
        with self._lock:
            latest = self._jobs[job_id]
            return ApproveResponse(job_id=job_id, status=latest.status, video_url=video_url)

    def publish_youtube(self, job_id: UUID) -> PublishResponse:
        if not self._youtube_configured():
            raise HTTPException(status_code=409, detail="youtube credentials are not configured")

        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise HTTPException(status_code=404, detail="job not found")
            if not record.video_url:
                raise HTTPException(status_code=409, detail="rendered video not available")

        self._attempt_youtube_upload(job_id)
        with self._lock:
            latest = self._jobs[job_id]
            if latest.youtube_upload_status != YouTubeUploadStatus.UPLOADED or not latest.youtube_url:
                raise HTTPException(status_code=502, detail="youtube upload failed")
            return PublishResponse(
                job_id=job_id,
                youtube_video_id=latest.youtube_video_id or "",
                youtube_url=latest.youtube_url,
                youtube_upload_status=latest.youtube_upload_status,
            )

    def start_catalog_crawl(self, limit_per_category: int = 300) -> CatalogCrawlJobResponse:
        crawl_job_id = uuid4()
        with self._lock:
            job = CrawlJobRecord(crawl_job_id=crawl_job_id, status=CrawlJobStatus.QUEUED)
            self._crawl_jobs[crawl_job_id] = job
            self._persist_locked()

        t = threading.Thread(target=self._run_catalog_crawl, args=(crawl_job_id, limit_per_category), daemon=True)
        t.start()
        return CatalogCrawlJobResponse(crawl_job_id=crawl_job_id, status=CrawlJobStatus.QUEUED)

    def get_catalog_crawl_job(self, crawl_job_id: UUID) -> CatalogCrawlJobDetailResponse:
        with self._lock:
            job = self._crawl_jobs.get(crawl_job_id)
            if not job:
                raise HTTPException(status_code=404, detail="crawl job not found")
            return CatalogCrawlJobDetailResponse(
                crawl_job_id=job.crawl_job_id,
                status=job.status,
                started_at=job.started_at,
                completed_at=job.completed_at,
                total_discovered=job.total_discovered,
                total_indexed=job.total_indexed,
                error_message=job.error_message,
            )

    def rebuild_catalog_index(self) -> CatalogIndexRebuildResponse:
        with self._lock:
            items = list(self._catalog.values())

        indexed = 0
        for item in items:
            embedding = self._embedding_from_url(item.image_url)
            if embedding:
                with self._lock:
                    existing = self._catalog.get(item.product_id)
                    if existing:
                        existing.embedding = embedding
                        existing.updated_at = datetime.now(timezone.utc)
                indexed += 1

        with self._lock:
            self._persist_locked()
            return CatalogIndexRebuildResponse(total_products=len(self._catalog), total_indexed_products=indexed)

    def catalog_stats(self) -> CatalogStatsResponse:
        with self._lock:
            categories = Counter([item.category for item in self._catalog.values()])
            completed = [j.completed_at for j in self._crawl_jobs.values() if j.status == CrawlJobStatus.COMPLETED and j.completed_at]
            indexed = sum(1 for item in self._catalog.values() if item.embedding)
            return CatalogStatsResponse(
                total_products=len(self._catalog),
                total_indexed_products=indexed,
                categories=dict(categories),
                last_crawl_completed_at=max(completed) if completed else None,
            )

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
            image_path = record.upload_image_path

        if not image_path:
            raise HTTPException(status_code=409, detail="source image missing for retry")
        path = Path(image_path)
        if not path.exists():
            raise HTTPException(status_code=409, detail="source image file not found for retry")

        created = self.create_job(
            look_count=look_count,
            quality_mode=quality_mode,
            theme=theme,
            tone=tone,
            image_bytes=path.read_bytes(),
            image_content_type=self._content_type_from_path(path),
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
        total_youtube_uploaded = sum(1 for r in records if r.youtube_upload_status == YouTubeUploadStatus.UPLOADED)
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
            total_youtube_uploaded=total_youtube_uploaded,
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
            youtube_video_id=r.youtube_video_id,
            youtube_url=r.youtube_url,
            youtube_upload_status=r.youtube_upload_status,
        )

    def _run_pipeline(self, job_id: UUID) -> None:
        time.sleep(STEP_SECONDS)
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            rec.status = JobStatus.ANALYZED
            rec.progress = 20
            self._persist_locked()

        time.sleep(STEP_SECONDS)
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            look_count = rec.look_count
            upload_image_path = rec.upload_image_path
            tone = rec.tone
            theme = rec.theme
            effective_look_count = self._effective_auto_match_count(look_count, category=None)
        matched_items = self._search_catalog(
            upload_image_path=upload_image_path,
            look_count=effective_look_count,
            category=None,
            price_cap=None,
            color_hint=tone or theme,
        )
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            rec.had_partial_match = len(matched_items) < effective_look_count or effective_look_count >= 4
            if rec.had_partial_match and matched_items:
                matched_items[-1].failure_code = FailureCode.CRAWL_TIMEOUT
            rec.status = JobStatus.MATCHED_PARTIAL if rec.had_partial_match else JobStatus.MATCHED
            rec.progress = 45
            rec.items = matched_items
            rec.preview_url = f"{PUBLIC_BASE_URL}/assets/previews/{job_id}.jpg"
            self._persist_locked()

        time.sleep(STEP_SECONDS)
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            rec.status = JobStatus.COMPOSED
            rec.progress = 70
            self._persist_locked()

        time.sleep(STEP_SECONDS)
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            rec.status = JobStatus.RENDERING
            rec.progress = 85
            self._persist_locked()

        # render video outside lock
        try:
            rendered_path = self._render_video(job_id)
        except Exception:
            with self._lock:
                rec = self._jobs.get(job_id)
                if not rec:
                    return
                rec.status = JobStatus.FAILED
                rec.progress = 100
                rec.completed_at = datetime.now(timezone.utc)
                rec.failure_code = FailureCode.RENDER_ERROR
                rec.youtube_upload_status = YouTubeUploadStatus.FAILED
                self._persist_locked()
            return

        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            rec.video_url = f"{PUBLIC_BASE_URL}/assets/videos/{job_id}.mp4"

            if rec.quality_mode == QualityMode.human_review:
                rec.status = JobStatus.REVIEW_REQUIRED
                rec.progress = 95
                rec.completed_at = datetime.now(timezone.utc)
                self._persist_locked()
                return

            if rec.had_partial_match and random.random() < 0.5:
                rec.status = JobStatus.FAILED
                rec.progress = 100
                rec.completed_at = datetime.now(timezone.utc)
                rec.failure_code = FailureCode.EMPTY_RESULT
                if rec.items and all(item.failure_code is None for item in rec.items):
                    rec.items[-1].failure_code = FailureCode.CRAWL_TIMEOUT
                rec.youtube_upload_status = YouTubeUploadStatus.SKIPPED
                self._persist_locked()
                return

            if random.random() < 0.05:
                rec.status = JobStatus.FAILED
                rec.progress = 100
                rec.completed_at = datetime.now(timezone.utc)
                rec.failure_code = FailureCode.RENDER_ERROR
                rec.youtube_upload_status = YouTubeUploadStatus.FAILED
                self._persist_locked()
                return

            rec.status = JobStatus.COMPLETED
            rec.progress = 100
            rec.completed_at = datetime.now(timezone.utc)
            self._persist_locked()

        self._attempt_youtube_upload(job_id, rendered_path)

    def _attempt_youtube_upload(self, job_id: UUID, rendered_path: Path | None = None) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            if rec.youtube_upload_status == YouTubeUploadStatus.UPLOADED:
                return
            if not rec.video_url:
                return

        if rendered_path is None:
            rendered_path = self._videos_dir / f"{job_id}.mp4"
        if not rendered_path.exists():
            with self._lock:
                rec = self._jobs.get(job_id)
                if rec:
                    rec.youtube_upload_status = YouTubeUploadStatus.FAILED
                    rec.failure_code = rec.failure_code or FailureCode.RENDER_ERROR
                    self._persist_locked()
            return

        if not self._youtube_configured():
            with self._lock:
                rec = self._jobs.get(job_id)
                if rec:
                    rec.youtube_upload_status = YouTubeUploadStatus.SKIPPED
                    self._persist_locked()
            return

        try:
            video_id = self._upload_to_youtube(rendered_path, job_id)
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            with self._lock:
                rec = self._jobs.get(job_id)
                if not rec:
                    return
                rec.youtube_video_id = video_id
                rec.youtube_url = youtube_url
                rec.youtube_upload_status = YouTubeUploadStatus.UPLOADED
                self._persist_locked()
        except Exception:
            with self._lock:
                rec = self._jobs.get(job_id)
                if not rec:
                    return
                rec.youtube_upload_status = YouTubeUploadStatus.FAILED
                if YOUTUBE_UPLOAD_REQUIRED and rec.status != JobStatus.REVIEW_REQUIRED:
                    rec.status = JobStatus.FAILED
                    rec.failure_code = FailureCode.LICENSE_BLOCKED
                self._persist_locked()

    def _build_match_items(self, record: JobRecord, look_count: int) -> list[MatchItem]:
        return self._search_catalog(
            upload_image_path=record.upload_image_path,
            look_count=look_count,
            category=None,
            price_cap=None,
            color_hint=record.tone or record.theme,
        )

    def _build_candidates(
        self,
        record: JobRecord,
        category: str,
        price_cap: Optional[int],
        color_hint: Optional[str],
    ) -> list[MatchItem]:
        normalized_price_cap: Optional[int]
        if price_cap is None:
            normalized_price_cap = None
        elif price_cap < 10000:
            normalized_price_cap = None
        else:
            normalized_price_cap = price_cap
        results = self._search_catalog(
            upload_image_path=record.upload_image_path,
            look_count=3,
            category=category,
            price_cap=normalized_price_cap,
            color_hint=color_hint,
        )
        color_text = (color_hint or "").strip().title()
        if color_text:
            for item in results:
                if item.product_name and color_text not in item.product_name:
                    item.product_name = f"{category.title()} {color_text} {item.product_name}"
                if item.evidence_tags is not None:
                    item.evidence_tags.append(f"color:{color_hint.strip().lower()}")
        return results

    def _search_catalog(
        self,
        upload_image_path: Optional[str],
        look_count: int,
        category: Optional[str],
        price_cap: Optional[int],
        color_hint: Optional[str],
    ) -> list[MatchItem]:
        effective_look_count = self._effective_auto_match_count(look_count, category)
        query_vector = self._embedding_from_file(upload_image_path) if upload_image_path else []
        if not query_vector:
            query_vector = self._embedding_from_text(color_hint or "street casual")

        with self._lock:
            catalog_items = list(self._catalog.values())

        if not catalog_items:
            catalog_items = self._fallback_catalog_items()

        color_hint_text = (color_hint or "").strip().lower()
        candidates: list[tuple[float, CatalogItemRecord, ScoreBreakdown, list[str]]] = []
        for item in catalog_items:
            if category and item.category != category:
                continue
            if price_cap is not None and item.price is not None and item.price > price_cap:
                continue
            if not item.embedding:
                continue
            image_sim = self._cosine_similarity(query_vector, item.embedding)
            if image_sim < CATALOG_MIN_IMAGE_SIM:
                continue
            text_score = self._text_hint_score(item.product_name, color_hint_text)
            category_score = 1.0 if category and item.category == category else 0.8
            price_score = self._price_fit_score(item.price, price_cap)
            final = 0.70 * image_sim + 0.15 * category_score + 0.10 * text_score + 0.05 * price_score
            score = ScoreBreakdown(
                image=round(image_sim, 4),
                text=round(text_score, 4),
                category=round(category_score, 4),
                price=round(price_score, 4),
                final=round(final, 4),
            )
            tags = ["vector:hist", f"category:{item.category}", "source:crawled"]
            if price_cap is not None:
                tags.append(f"price_cap:{price_cap}")
            if color_hint_text:
                tags.append(f"color:{color_hint_text}")
            candidates.append((final, item, score, tags))

        candidates.sort(key=lambda row: row[0], reverse=True)
        required_categories = self._required_categories_for_auto_match(effective_look_count, category)
        if required_categories:
            top = self._select_balanced_candidates(candidates, effective_look_count, required_categories)
        else:
            top = candidates[:effective_look_count]
        results: list[MatchItem] = []
        for idx, (_, item, score, tags) in enumerate(top):
            results.append(
                MatchItem(
                    category=item.category,
                    product_id=item.product_id,
                    brand=item.brand,
                    product_name=item.product_name,
                    price=item.price,
                    product_url=item.product_url,
                    image_url=item.image_url,
                    evidence_tags=tags,
                    score_breakdown=score,
                )
            )

        missing_required_categories: list[str] = []
        if not category and required_categories:
            existing_categories = {item.category for item in results if item.category}
            missing_required_categories = [req for req in required_categories if req not in existing_categories]

        if (len(results) < effective_look_count or missing_required_categories) and not category:
            # Fallback candidates to avoid empty UX when crawl data is temporarily sparse.
            needed = effective_look_count - len(results)
            existing_product_ids = {item.product_id for item in results if item.product_id}
            existing_categories = {item.category for item in results if item.category}
            fallback = self._fallback_catalog_items()
            for required_category in missing_required_categories:
                if required_category in existing_categories:
                    continue
                fallback_item = next(
                    (
                        item
                        for item in fallback
                        if item.category == required_category and item.product_id not in existing_product_ids
                    ),
                    None,
                )
                if not fallback_item or needed <= 0:
                    continue
                existing_product_ids.add(fallback_item.product_id)
                existing_categories.add(fallback_item.category)
                results.append(
                    MatchItem(
                        category=fallback_item.category,
                        product_id=fallback_item.product_id,
                        brand=fallback_item.brand,
                        product_name=fallback_item.product_name,
                        price=fallback_item.price,
                        product_url=fallback_item.product_url,
                        image_url=fallback_item.image_url,
                        evidence_tags=["fallback:required-category", f"required:{required_category}"],
                        score_breakdown=ScoreBreakdown(image=0.45, text=0.5, category=0.7, price=0.5, final=0.52),
                        failure_code=FailureCode.CRAWL_TIMEOUT,
                    )
                )
                needed -= 1
                if needed <= 0:
                    break

            for item in fallback:
                if needed <= 0:
                    break
                if item.product_id in existing_product_ids:
                    continue
                existing_product_ids.add(item.product_id)
                results.append(
                    MatchItem(
                        category=item.category,
                        product_id=item.product_id,
                        brand=item.brand,
                        product_name=item.product_name,
                        price=item.price,
                        product_url=item.product_url,
                        image_url=item.image_url,
                        evidence_tags=["fallback:search-link"],
                        score_breakdown=ScoreBreakdown(image=0.45, text=0.5, category=0.7, price=0.5, final=0.52),
                        failure_code=FailureCode.CRAWL_TIMEOUT,
                    )
                )
                needed -= 1
            if len(results) > effective_look_count:
                required_set = set(required_categories)
                must_keep = [row for row in results if row.category in required_set]
                optional = [row for row in results if row.category not in required_set]
                results = (must_keep + optional)[:effective_look_count]
        return results

    @staticmethod
    def _effective_auto_match_count(look_count: int, category: Optional[str]) -> int:
        if category is not None:
            return max(1, look_count)
        # Outfit recommendation must include both top and bottom even if user selected 1.
        return max(2, look_count)

    @staticmethod
    def _required_categories_for_auto_match(look_count: int, category: Optional[str]) -> list[str]:
        if category is not None:
            return []
        if look_count < 2:
            return []
        return ["top", "bottom"]

    @staticmethod
    def _select_balanced_candidates(
        candidates: list[tuple[float, CatalogItemRecord, ScoreBreakdown, list[str]]],
        look_count: int,
        required_categories: list[str],
    ) -> list[tuple[float, CatalogItemRecord, ScoreBreakdown, list[str]]]:
        selected: list[tuple[float, CatalogItemRecord, ScoreBreakdown, list[str]]] = []
        used_product_ids: set[str] = set()

        for required_category in required_categories:
            picked = next(
                (
                    row
                    for row in candidates
                    if row[1].category == required_category and row[1].product_id not in used_product_ids
                ),
                None,
            )
            if picked is None:
                continue
            selected.append(picked)
            used_product_ids.add(picked[1].product_id)

        for row in candidates:
            if len(selected) >= look_count:
                break
            if row[1].product_id in used_product_ids:
                continue
            selected.append(row)
            used_product_ids.add(row[1].product_id)

        return selected[:look_count]

    def _run_catalog_crawl(self, crawl_job_id: UUID, limit_per_category: int) -> None:
        with self._lock:
            job = self._crawl_jobs.get(crawl_job_id)
            if not job:
                return
            job.status = CrawlJobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)
            self._persist_locked()

        try:
            discovered, indexed = self._crawl_and_index(limit_per_category)
            with self._lock:
                job = self._crawl_jobs.get(crawl_job_id)
                if not job:
                    return
                job.status = CrawlJobStatus.COMPLETED
                job.total_discovered = discovered
                job.total_indexed = indexed
                job.completed_at = datetime.now(timezone.utc)
                self._persist_locked()
        except Exception as exc:
            with self._lock:
                job = self._crawl_jobs.get(crawl_job_id)
                if not job:
                    return
                job.status = CrawlJobStatus.FAILED
                job.error_message = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                self._persist_locked()

    def _crawl_and_index(self, limit_per_category: int) -> tuple[int, int]:
        products: dict[str, CatalogItemRecord] = {}
        seeds = self._catalog_seed_queries()
        target_per_category = max(1, max(limit_per_category, CATALOG_MIN_ITEMS_PER_CATEGORY))
        if httpx is None or BeautifulSoup is None:
            fallback = self._fallback_catalog_items()
            with self._lock:
                for item in fallback:
                    self._catalog[item.product_id] = item
                self._persist_locked()
            return len(fallback), len(fallback)

        with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            for category, query in seeds.items():
                discovered = self._crawl_goods_api(client, category, query, target_per_category)
                if not discovered:
                    discovered = self._crawl_search_page(client, category, query, target_per_category)
                if len(discovered) < target_per_category:
                    fallback = self._fallback_items_for_category(category, target_per_category - len(discovered))
                    discovered.extend(fallback)
                for item in discovered:
                    products[item.product_id] = item

        indexed = 0
        for item in products.values():
            if not item.embedding:
                if CATALOG_CRAWL_USE_IMAGE_EMBEDDING:
                    item.embedding = self._embedding_from_url(item.image_url)
                if not item.embedding:
                    item.embedding = self._embedding_from_text(f"{item.category} {item.product_name}")
            if item.embedding:
                indexed += 1

        if not products:
            products = {item.product_id: item for item in self._fallback_catalog_items()}
            indexed = len(products)

        with self._lock:
            self._catalog.update(products)
            self._persist_locked()
        return len(products), indexed

    @staticmethod
    def _catalog_seed_queries() -> dict[str, str]:
        # Keep `bottom` for match pipeline compatibility while using "바지" keyword for crawl quality.
        return {
            "shoes": "신발",
            "top": "상의",
            "outer": "아우터",
            "bottom": "바지",
            "bag": "가방",
        }

    def _fallback_items_for_category(self, category: str, needed: int) -> list[CatalogItemRecord]:
        if needed <= 0:
            return []
        fallback = [item for item in self._fallback_catalog_items() if item.category == category]
        if len(fallback) >= needed:
            return fallback[:needed]
        ko = {
            "shoes": "신발",
            "top": "상의",
            "outer": "아우터",
            "bottom": "바지",
            "bag": "가방",
        }.get(category, category)
        items = list(fallback)
        start_idx = len(items) + 1
        for idx in range(start_idx, needed + 1):
            query = f"{ko} 코디"
            url = self._musinsa_search_url(query)
            items.append(
                CatalogItemRecord(
                    product_id=f"fallback-{category}-{idx}",
                    category=category,
                    brand="MUSINSA",
                    product_name=f"{ko} 추천 아이템 {idx}",
                    product_url=url,
                    image_url=url,
                    price=28000 + (idx % 10) * 3000,
                    embedding=self._embedding_from_text(f"{category} {ko} {idx}"),
                )
            )
        return items[:needed]

    def _crawl_goods_api(
        self,
        client: "httpx.Client",
        category: str,
        keyword: str,
        limit_count: int,
    ) -> list[CatalogItemRecord]:
        records: list[CatalogItemRecord] = []
        seen_ids: set[str] = set()
        page = 1
        page_size = min(60, max(10, limit_count))
        base_url = "https://api.musinsa.com/api2/dp/v1/plp/goods"

        max_pages = max(5, math.ceil(limit_count / max(page_size, 1)) + 1)
        while len(records) < limit_count and page <= max_pages:
            params = {
                "keyword": keyword,
                "caller": "SEARCH",
                "gf": "A",
                "page": page,
                "size": page_size,
                "sortCode": "POPULAR",
            }
            try:
                resp = client.get(
                    base_url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Referer": f"https://www.musinsa.com/search/results/goods?keyword={quote_plus(keyword)}",
                    },
                )
            except Exception:
                break
            if resp.status_code != 200:
                break
            try:
                payload = resp.json()
            except Exception:
                break
            if (payload.get("meta") or {}).get("result") != "SUCCESS":
                break
            data = payload.get("data") or {}
            rows = data.get("list") or []
            if not rows:
                break
            for row in rows:
                product_id = str(row.get("goodsNo") or "").strip()
                if not product_id or product_id in seen_ids:
                    continue
                product_url = str(row.get("goodsLinkUrl") or "").strip()
                image_url = str(row.get("thumbnail") or "").strip()
                product_name = str(row.get("goodsName") or "").strip()
                if not product_url or not image_url or not product_name:
                    continue
                if image_url.startswith("//"):
                    image_url = f"https:{image_url}"
                if product_url.startswith("/"):
                    product_url = urljoin("https://www.musinsa.com", product_url)
                price_raw = row.get("price")
                price: Optional[int]
                if isinstance(price_raw, (int, float)):
                    price = int(price_raw)
                else:
                    price = self._extract_price(str(price_raw or ""))
                records.append(
                    CatalogItemRecord(
                        product_id=product_id,
                        category=category,
                        brand=str(row.get("brandName") or row.get("brand") or "MUSINSA"),
                        product_name=product_name,
                        product_url=self._normalize_url(product_url),
                        image_url=image_url,
                        price=price,
                        embedding=[],
                    )
                )
                seen_ids.add(product_id)
                if len(records) >= limit_count:
                    break
            pagination = data.get("pagination") or {}
            has_next = bool(pagination.get("hasNext"))
            if not has_next:
                break
            page += 1
        return records

    def _crawl_search_page(
        self,
        client: "httpx.Client",
        category: str,
        query: str,
        limit_count: int,
    ) -> list[CatalogItemRecord]:
        url = self._musinsa_search_url(query)
        resp = client.get(url)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        records: list[CatalogItemRecord] = []
        seen: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", ""))
            if "/products/" not in href:
                continue
            product_url = urljoin("https://www.musinsa.com", href)
            product_url = self._normalize_url(product_url)
            if product_url in seen:
                continue
            seen.add(product_url)
            product_id = self._product_id_from_url(product_url)
            img_tag = anchor.find("img")
            image_url = ""
            product_name = ""
            if img_tag is not None:
                image_url = str(img_tag.get("src") or img_tag.get("data-src") or "").strip()
                product_name = str(img_tag.get("alt") or "").strip()
            if not image_url:
                continue
            if image_url.startswith("//"):
                image_url = f"https:{image_url}"
            if image_url.startswith("/"):
                image_url = urljoin("https://www.musinsa.com", image_url)
            if not product_name:
                product_name = anchor.get_text(" ", strip=True) or f"{category} item"
            record = CatalogItemRecord(
                product_id=product_id,
                category=category,
                brand="MUSINSA",
                product_name=product_name,
                product_url=product_url,
                image_url=image_url,
                price=self._extract_price(anchor.get_text(" ", strip=True)),
                embedding=[],
            )
            records.append(record)
            if len(records) >= limit_count:
                break
        return records

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url)
        clean = parsed._replace(query="", fragment="")
        return clean.geturl()

    @staticmethod
    def _extract_price(text: str) -> Optional[int]:
        match = re.search(r"([0-9][0-9,]{3,})", text)
        if not match:
            return None
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None

    @staticmethod
    def _product_id_from_url(url: str) -> str:
        token = url.rstrip("/").split("/")[-1]
        return token or f"P-{uuid4().hex[:8]}"

    @staticmethod
    def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        if n1 == 0 or n2 == 0:
            return 0.0
        return max(0.0, min(1.0, dot / (n1 * n2)))

    @staticmethod
    def _text_hint_score(name: str, color_hint: str) -> float:
        if not color_hint:
            return 0.5
        return 1.0 if color_hint in name.lower() else 0.35

    @staticmethod
    def _price_fit_score(price: Optional[int], price_cap: Optional[int]) -> float:
        if price_cap is None or price is None:
            return 0.6
        if price <= price_cap:
            return 1.0
        over = min(1.0, (price - price_cap) / max(price_cap, 1))
        return max(0.0, 1.0 - over)

    def _embedding_from_text(self, text: str) -> list[float]:
        if not text:
            return [0.0] * 48
        bins = [0.0] * 48
        for idx, ch in enumerate(text.encode("utf-8")):
            bins[idx % 48] += (ch % 31) / 31.0
        return self._normalize_vector(bins)

    def _embedding_from_file(self, path_text: Optional[str]) -> list[float]:
        if not path_text or Image is None:
            return []
        path = Path(path_text)
        if not path.exists():
            return []
        try:
            with Image.open(path) as img:
                return self._embedding_from_image(img)
        except Exception:
            return []

    def _embedding_from_url(self, image_url: str) -> list[float]:
        if Image is None or httpx is None:
            return []
        cache_path = self._catalog_cache_dir / f"{self._product_id_from_url(image_url)}.img"
        try:
            if not cache_path.exists():
                with httpx.Client(timeout=4.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                    resp = client.get(image_url)
                    if resp.status_code != 200 or not resp.content:
                        return []
                    cache_path.write_bytes(resp.content)
            with Image.open(cache_path) as img:
                return self._embedding_from_image(img)
        except Exception:
            return []

    def _embedding_from_image(self, img: "Image.Image") -> list[float]:
        rgb = img.convert("RGB").resize((96, 96))
        hist = rgb.histogram()  # 256*3
        bins_per_channel = 16
        channel_chunk = 256 // bins_per_channel
        vec: list[float] = []
        for c in range(3):
            offset = c * 256
            for i in range(bins_per_channel):
                start = offset + i * channel_chunk
                end = offset + (i + 1) * channel_chunk
                vec.append(float(sum(hist[start:end])))
        return self._normalize_vector(vec)

    @staticmethod
    def _normalize_vector(vec: list[float]) -> list[float]:
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]

    def _fallback_catalog_items(self) -> list[CatalogItemRecord]:
        categories = ["outer", "top", "bottom", "shoes", "bag"]
        category_ko = {
            "outer": "아우터",
            "top": "상의",
            "bottom": "바지",
            "shoes": "신발",
            "bag": "가방",
        }
        items: list[CatalogItemRecord] = []
        for category in categories:
            for idx in range(1, 4):
                ko = category_ko.get(category, category)
                query = f"{ko} 코디"
                url = self._musinsa_search_url(query)
                seed_vec = self._embedding_from_text(query)
                items.append(
                    CatalogItemRecord(
                        product_id=f"fallback-{category}-{idx}",
                        category=category,
                        brand="MUSINSA",
                        product_name=f"{ko} 추천 아이템 {idx}",
                        product_url=url,
                        image_url=url,
                        price=28000 + idx * 6000,
                        embedding=seed_vec,
                    )
                )
        return items

    @staticmethod
    def _musinsa_search_url(query: str) -> str:
        return f"https://www.musinsa.com/search/goods?keyword={quote_plus(query)}"

    def _save_upload(self, job_id: UUID, image_bytes: bytes, image_content_type: str | None) -> Path:
        ext = self._ext_from_content_type(image_content_type)
        upload_path = self._uploads_dir / f"{job_id}{ext}"
        preview_path = self._previews_dir / f"{job_id}.jpg"
        upload_path.write_bytes(image_bytes)
        shutil.copyfile(upload_path, preview_path)
        return upload_path

    @staticmethod
    def _ext_from_content_type(content_type: str | None) -> str:
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        return mapping.get((content_type or "").lower(), ".jpg")

    @staticmethod
    def _content_type_from_path(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".png":
            return "image/png"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/jpeg"

    def _render_video(self, job_id: UUID) -> Path:
        output = self._videos_dir / f"{job_id}.mp4"
        if not self._enable_real_render:
            output.write_bytes(b"FAKE_MP4_FOR_TEST")
            return output

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            if imageio_ffmpeg is None:
                raise RuntimeError("ffmpeg is not available")
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec or not rec.upload_image_path:
                raise RuntimeError("job image is not available")
            image_path = Path(rec.upload_image_path)
        if not image_path.exists():
            raise RuntimeError("source image file does not exist")

        cmd = [
            ffmpeg_path,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            str(RENDER_SECONDS),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return output

    def _youtube_configured(self) -> bool:
        return bool(
            os.getenv("YOUTUBE_CLIENT_ID")
            and os.getenv("YOUTUBE_CLIENT_SECRET")
            and os.getenv("YOUTUBE_REFRESH_TOKEN")
            and Credentials is not None
            and build is not None
            and MediaFileUpload is not None
        )

    def _upload_to_youtube(self, video_path: Path, job_id: UUID) -> str:
        if not self._youtube_configured():
            raise RuntimeError("youtube is not configured")

        credentials = Credentials(
            token=None,
            refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["YOUTUBE_CLIENT_ID"],
            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )

        youtube = build("youtube", "v3", credentials=credentials)

        body = {
            "snippet": {
                "title": f"OOTD Lookbook {job_id}",
                "description": "Generated by OOTD App",
                "tags": ["ootd", "fashion", "lookbook"],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": YOUTUBE_PRIVACY_STATUS,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        if not response or "id" not in response:
            raise RuntimeError("youtube upload returned empty response")
        return str(response["id"])

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        jobs_payload = payload.get("jobs", [])
        idem_payload = payload.get("idempotency_map", {})
        catalog_payload = payload.get("catalog", [])
        crawl_jobs_payload = payload.get("crawl_jobs", [])
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
        for raw in catalog_payload:
            try:
                item = self._catalog_item_from_dict(raw)
            except Exception:
                continue
            self._catalog[item.product_id] = item
        for raw in crawl_jobs_payload:
            try:
                crawl = self._crawl_job_from_dict(raw)
            except Exception:
                continue
            self._crawl_jobs[crawl.crawl_job_id] = crawl

    def _persist_locked(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "jobs": [self._record_to_dict(r) for r in self._jobs.values()],
            "idempotency_map": {k: str(v) for k, v in self._idempotency_map.items()},
            "catalog": [self._catalog_item_to_dict(item) for item in self._catalog.values()],
            "crawl_jobs": [self._crawl_job_to_dict(job) for job in self._crawl_jobs.values()],
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
            "upload_image_path": record.upload_image_path,
            "youtube_video_id": record.youtube_video_id,
            "youtube_url": record.youtube_url,
            "youtube_upload_status": record.youtube_upload_status.value,
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
            upload_image_path=raw.get("upload_image_path"),
            youtube_video_id=raw.get("youtube_video_id"),
            youtube_url=raw.get("youtube_url"),
            youtube_upload_status=YouTubeUploadStatus(raw.get("youtube_upload_status", YouTubeUploadStatus.PENDING.value)),
        )

    @staticmethod
    def _catalog_item_to_dict(item: CatalogItemRecord) -> dict:
        return {
            "product_id": item.product_id,
            "category": item.category,
            "brand": item.brand,
            "product_name": item.product_name,
            "product_url": item.product_url,
            "image_url": item.image_url,
            "price": item.price,
            "embedding": item.embedding,
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _catalog_item_from_dict(raw: dict) -> CatalogItemRecord:
        return CatalogItemRecord(
            product_id=str(raw["product_id"]),
            category=str(raw["category"]),
            brand=str(raw.get("brand") or "MUSINSA"),
            product_name=str(raw.get("product_name") or ""),
            product_url=str(raw.get("product_url") or ""),
            image_url=str(raw.get("image_url") or ""),
            price=int(raw["price"]) if raw.get("price") is not None else None,
            embedding=[float(v) for v in raw.get("embedding", [])],
            updated_at=datetime.fromisoformat(raw["updated_at"]) if raw.get("updated_at") else datetime.now(timezone.utc),
        )

    @staticmethod
    def _crawl_job_to_dict(job: CrawlJobRecord) -> dict:
        return {
            "crawl_job_id": str(job.crawl_job_id),
            "status": job.status.value,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "total_discovered": job.total_discovered,
            "total_indexed": job.total_indexed,
            "error_message": job.error_message,
        }

    @staticmethod
    def _crawl_job_from_dict(raw: dict) -> CrawlJobRecord:
        return CrawlJobRecord(
            crawl_job_id=UUID(raw["crawl_job_id"]),
            status=CrawlJobStatus(raw["status"]),
            started_at=datetime.fromisoformat(raw["started_at"]) if raw.get("started_at") else None,
            completed_at=datetime.fromisoformat(raw["completed_at"]) if raw.get("completed_at") else None,
            total_discovered=int(raw.get("total_discovered", 0)),
            total_indexed=int(raw.get("total_indexed", 0)),
            error_message=raw.get("error_message"),
        )
