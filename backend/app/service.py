from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus
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


STEP_SECONDS = 0.05
DEFAULT_STATE_FILE = Path(os.getenv("JOB_STATE_FILE", "./data/job_state.json"))
DEFAULT_ASSET_ROOT = Path(os.getenv("ASSET_ROOT", "./data/assets"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
RENDER_SECONDS = float(os.getenv("RENDER_SECONDS", "4"))
ENABLE_REAL_RENDER = os.getenv("ENABLE_REAL_RENDER", "1") == "1"
YOUTUBE_UPLOAD_REQUIRED = os.getenv("YOUTUBE_UPLOAD_REQUIRED", "0") == "1"
YOUTUBE_PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "unlisted")


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


class JobService:
    def __init__(
        self,
        state_file: Path | None = None,
        asset_root: Path | None = None,
        enable_real_render: bool = ENABLE_REAL_RENDER,
    ) -> None:
        self._jobs: dict[UUID, JobRecord] = {}
        self._idempotency_map: dict[str, UUID] = {}
        self._lock = threading.Lock()
        self._booted_at = time.time()
        self._state_file = (state_file or DEFAULT_STATE_FILE).resolve()
        self._asset_root = (asset_root or DEFAULT_ASSET_ROOT).resolve()
        self._uploads_dir = self._asset_root / "uploads"
        self._previews_dir = self._asset_root / "previews"
        self._videos_dir = self._asset_root / "videos"
        self._enable_real_render = enable_real_render
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        self._previews_dir.mkdir(parents=True, exist_ok=True)
        self._videos_dir.mkdir(parents=True, exist_ok=True)
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
                    rec.preview_url = f"{PUBLIC_BASE_URL}/assets/previews/{job_id}.jpg"
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

    @staticmethod
    def _matched_state(record: JobRecord) -> JobStatus:
        return JobStatus.MATCHED_PARTIAL if record.look_count >= 4 else JobStatus.MATCHED

    @staticmethod
    def _build_match_items(look_count: int, partial: bool) -> list[MatchItem]:
        categories = ["outer", "top", "bottom", "shoes", "accessory"]
        items: list[MatchItem] = []
        for idx in range(look_count):
            category = categories[idx % len(categories)]
            failure_code = FailureCode.CRAWL_TIMEOUT if partial and idx == look_count - 1 else None
            score = ScoreBreakdown(
                image=0.88,
                text=0.74,
                category=0.92,
                price=0.67,
                final=0.83,
            )
            search_query = f"무신사 {category} 코디"
            musinsa_url = JobService._musinsa_search_url(search_query)
            items.append(
                MatchItem(
                    category=category,
                    product_id=f"MUSINSA-{category}-{idx + 1}",
                    brand="MUSINSA",
                    product_name=f"{category.title()} Styled Item {idx + 1}",
                    price=49900 + idx * 7000,
                    product_url=musinsa_url,
                    image_url=musinsa_url,
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
            query = f"무신사 {category} {color_hint_text}".strip()
            musinsa_url = JobService._musinsa_search_url(query)
            candidates.append(
                MatchItem(
                    category=category,
                    product_id=f"MUSINSA-{category}-R-{idx + 1}",
                    brand="MUSINSA",
                    product_name=f"{category.title()} {color_hint_text.title() + ' ' if color_hint_text else ''}Candidate {idx + 1}",
                    price=price,
                    product_url=musinsa_url,
                    image_url=musinsa_url,
                    evidence_tags=evidence_tags,
                    score_breakdown=score,
                )
            )
        return candidates

    @staticmethod
    def _musinsa_search_url(query: str) -> str:
        return f"https://www.musinsa.com/search/musinsa/integration?q={quote_plus(query)}"

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
