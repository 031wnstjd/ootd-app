from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .models import (
    ApproveResponse,
    CreateJobResponse,
    HistoryResponse,
    JobDetailResponse,
    QualityMode,
    RerankRequest,
    RerankResponse,
)
from .service import JobService

app = FastAPI(title="Musinsa Shorts Generator API", version="1.0.0")
service = JobService()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/v1/jobs", response_model=CreateJobResponse, status_code=202)
async def create_job(
    image: UploadFile = File(...),
    look_count: int = Form(..., ge=1, le=5),
    quality_mode: QualityMode = Form(...),
    theme: str | None = Form(default=None),
    tone: str | None = Form(default=None),
) -> CreateJobResponse:
    _ = image.filename
    return service.create_job(look_count=look_count, quality_mode=quality_mode, theme=theme, tone=tone)


@app.get("/v1/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job(job_id: UUID) -> JobDetailResponse:
    return service.get_job(job_id)


@app.post("/v1/jobs/{job_id}/rerank", response_model=RerankResponse)
async def rerank_job(job_id: UUID, request: RerankRequest) -> RerankResponse:
    return service.rerank(job_id, request)


@app.post("/v1/jobs/{job_id}/approve", response_model=ApproveResponse)
async def approve_job(job_id: UUID) -> ApproveResponse:
    return service.approve(job_id)


@app.get("/v1/history", response_model=HistoryResponse)
async def list_history(limit: int = 20) -> HistoryResponse:
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    return service.history(limit)
