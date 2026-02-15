# Similarity Retrieval Implementation (2026-02-15)

## Implemented
- Real retrieval-oriented matching path replaced template-only matching.
- Catalog ops APIs added:
  - `POST /v1/catalog/crawl/jobs`
  - `GET /v1/catalog/crawl/jobs/{crawl_job_id}`
  - `POST /v1/catalog/index/rebuild`
  - `GET /v1/catalog/stats`
- Frontend ops panel integrated for crawl/index/stats.
- Existing job/render/youtube flows preserved.

## Technical Notes
- Current embedding is histogram-based vector fallback to keep runtime stable.
- Crawl parser is best-effort HTML extraction with deterministic fallback catalog.
- Persistent state now includes jobs + catalog + crawl jobs.

## Next Tasks
1. Replace histogram embedding with OpenCLIP service.
2. Move catalog/index persistence to PostgreSQL + Qdrant.
3. Add offline golden-set evaluator and automated quality gate in CI.
4. Expand crawl coverage and harden parser against layout drift.
