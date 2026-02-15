from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class QualityMode(str, Enum):
    auto_gate = "auto_gate"
    human_review = "human_review"


class JobStatus(str, Enum):
    INGESTED = "INGESTED"
    ANALYZED = "ANALYZED"
    MATCHED_PARTIAL = "MATCHED_PARTIAL"
    MATCHED = "MATCHED"
    COMPOSED = "COMPOSED"
    RENDERING = "RENDERING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FailureCode(str, Enum):
    CRAWL_TIMEOUT = "CRAWL_TIMEOUT"
    EMPTY_RESULT = "EMPTY_RESULT"
    RENDER_ERROR = "RENDER_ERROR"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    LICENSE_BLOCKED = "LICENSE_BLOCKED"


class YouTubeUploadStatus(str, Enum):
    PENDING = "PENDING"
    SKIPPED = "SKIPPED"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


class ScoreBreakdown(BaseModel):
    image: float
    text: float
    category: float
    price: float
    final: float


class MatchItem(BaseModel):
    category: Optional[str] = None
    product_id: Optional[str] = None
    brand: Optional[str] = None
    product_name: Optional[str] = None
    price: Optional[int] = None
    product_url: Optional[str] = None
    image_url: Optional[str] = None
    evidence_tags: list[str] = Field(default_factory=list)
    score_breakdown: Optional[ScoreBreakdown] = None
    failure_code: Optional[FailureCode] = None


class CreateJobResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    estimated_seconds: Optional[int] = None


class JobDetailResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    quality_mode: QualityMode
    look_count: int
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    items: Optional[list[MatchItem]] = None
    preview_url: Optional[str] = None
    video_url: Optional[str] = None
    failure_code: Optional[FailureCode] = None
    parent_job_id: Optional[UUID] = None
    attempts: int = 1
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    youtube_upload_status: Optional[YouTubeUploadStatus] = None


class RerankRequest(BaseModel):
    category: str
    price_cap: Optional[int] = None
    color_hint: Optional[str] = None


class RerankResponse(BaseModel):
    job_id: UUID
    category: Optional[str] = None
    candidates: list[MatchItem] = Field(default_factory=list)
    selected: Optional[MatchItem] = None


class ApproveResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    video_url: Optional[str] = None


class RetryResponse(BaseModel):
    previous_job_id: UUID
    new_job_id: UUID
    status: JobStatus


class PublishResponse(BaseModel):
    job_id: UUID
    youtube_video_id: str
    youtube_url: str
    youtube_upload_status: YouTubeUploadStatus


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: int
    total_jobs: int


class MetricsResponse(BaseModel):
    total_jobs_created: int
    total_jobs_completed: int
    total_jobs_failed: int
    total_jobs_retried: int
    avg_processing_seconds: float
    total_youtube_uploaded: int = 0


class HistoryItem(BaseModel):
    job_id: UUID
    status: JobStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    thumbnail_url: Optional[str] = None


class HistoryResponse(BaseModel):
    jobs: list[HistoryItem]
