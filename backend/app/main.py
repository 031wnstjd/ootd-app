from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import (
    ApproveResponse,
    CatalogCrawlJobDetailResponse,
    CatalogCrawlJobResponse,
    CatalogIndexRebuildResponse,
    CatalogStatsResponse,
    CreateJobResponse,
    HealthResponse,
    HistoryResponse,
    JobDetailResponse,
    MetricsResponse,
    PublishResponse,
    QualityMode,
    RerankRequest,
    RerankResponse,
    RetryResponse,
)
from .service import JobService

app = FastAPI(title="Musinsa Shorts Generator API", version="1.0.0")
service = JobService()
app.mount("/assets", StaticFiles(directory=str(service.asset_root), check_dir=False), name="assets")
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CreateJobResponse:
    if image.content_type is None or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=422, detail="image content type must be image/*")
    content = await image.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image too large (max 10MB)")
    return service.create_job(
        look_count=look_count,
        quality_mode=quality_mode,
        theme=theme,
        tone=tone,
        image_bytes=content,
        image_content_type=image.content_type,
        idempotency_key=idempotency_key,
    )


@app.get("/v1/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job(job_id: UUID) -> JobDetailResponse:
    return service.get_job(job_id)


@app.post("/v1/jobs/{job_id}/rerank", response_model=RerankResponse)
async def rerank_job(job_id: UUID, request: RerankRequest) -> RerankResponse:
    return service.rerank(job_id, request)


@app.post("/v1/jobs/{job_id}/approve", response_model=ApproveResponse)
async def approve_job(job_id: UUID) -> ApproveResponse:
    return service.approve(job_id)


@app.post("/v1/jobs/{job_id}/retry", response_model=RetryResponse, status_code=202)
async def retry_job(job_id: UUID) -> RetryResponse:
    return service.retry(job_id)


@app.post("/v1/jobs/{job_id}/publish", response_model=PublishResponse)
async def publish_job(job_id: UUID) -> PublishResponse:
    return service.publish_youtube(job_id)


@app.post("/v1/catalog/crawl/jobs", response_model=CatalogCrawlJobResponse, status_code=202)
async def start_catalog_crawl(limit_per_category: int = 300) -> CatalogCrawlJobResponse:
    if limit_per_category < 300:
        limit_per_category = 300
    if limit_per_category > 1000:
        limit_per_category = 1000
    return service.start_catalog_crawl(limit_per_category=limit_per_category)


@app.get("/v1/catalog/crawl/jobs/{crawl_job_id}", response_model=CatalogCrawlJobDetailResponse)
async def get_catalog_crawl_job(crawl_job_id: UUID) -> CatalogCrawlJobDetailResponse:
    return service.get_catalog_crawl_job(crawl_job_id)


@app.post("/v1/catalog/index/rebuild", response_model=CatalogIndexRebuildResponse)
async def rebuild_catalog_index() -> CatalogIndexRebuildResponse:
    return service.rebuild_catalog_index()


@app.get("/v1/catalog/stats", response_model=CatalogStatsResponse)
async def catalog_stats() -> CatalogStatsResponse:
    return service.catalog_stats()


@app.get("/v1/history", response_model=HistoryResponse)
async def list_history(limit: int = 20) -> HistoryResponse:
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    return service.history(limit)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return service.health()


@app.get("/v1/metrics", response_model=MetricsResponse)
async def metrics() -> MetricsResponse:
    return service.metrics()
