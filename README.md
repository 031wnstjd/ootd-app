# OOTD App MVP

`SPEC.md`/`openapi.yaml` 기반 MVP 구현입니다.

## Stack

- Backend: FastAPI (`backend/`)
- Frontend: Next.js + Tailwind (`frontend/`)
- API Contract: `openapi.yaml`
- Persistence: JSON state file (`JOB_STATE_FILE`, default `./data/job_state.json`)
- Assets: rendered/output files (`ASSET_ROOT`, default `./data/assets`)

## Run (Local)

### 1) Backend

```bash
cd backend
python3 -m pip install -r requirements.txt --break-system-packages
JOB_STATE_FILE=../data/job_state.json python3 -m uvicorn app.main:app --reload --port 8000
```

### 2) Frontend

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev -- --port 3005
```

브라우저: `http://localhost:3005`

## Run (Docker Compose)

```bash
cd /home/junsung/vibe_coding/lookbook-app/ootd-app
docker compose up --build
```

- Frontend: `http://localhost:3005`
- Backend docs: `http://localhost:8000/docs`

### Port preflight

```bash
cd /home/junsung/vibe_coding/lookbook-app/ootd-app
./scripts/preflight-port.sh 3005
```

### Override frontend port when 3005 is occupied

```bash
cd /home/junsung/vibe_coding/lookbook-app/ootd-app
FRONTEND_PORT=39005 docker compose up -d --build
```

## Smoke Test (Docker Compose)

`up -> health -> API smoke -> down`을 한 번에 검증:

```bash
cd /home/junsung/vibe_coding/lookbook-app/ootd-app
./scripts/compose-smoke.sh
```

옵션:

- 스택 유지: `AUTO_DOWN=0 ./scripts/compose-smoke.sh`
- 포트 변경: `FRONTEND_PORT=39005 ./scripts/compose-smoke.sh`

## Verify

### Backend tests

```bash
cd backend
python3 -m pytest -q
```

### Frontend checks

```bash
cd frontend
npm run typecheck
npm run lint
npm run build
```

## CI

- GitHub Actions: `.github/workflows/ci.yml`
- PR/`main` push 시 실행:
  - `backend` API 테스트
  - Docker Compose 스모크 2회 반복(`scripts/compose-smoke.sh`)
- Compose 단계 종료 시 진단 아티팩트 업로드:
  - `compose-logs.txt`
  - `compose-ps.txt`
  - `compose-flake-classification.txt` (1차 원인 라벨)
- 수동 실행(`workflow_dispatch`)에서 `smoke_runs` 입력으로 반복 횟수 조정 가능
- CI에서 분류 규칙 회귀 테스트 수행:
  - `./scripts/test-classify-compose-flake.sh`

## Permission Recovery (`EACCES`)

로컬과 Docker를 혼용해 `frontend/node_modules`, `frontend/.next`, `frontend/package-lock.json`이 root 소유로 바뀌면 `npm` 명령이 `EACCES`로 실패할 수 있습니다.

복구 명령:

```bash
cd /home/junsung/vibe_coding/lookbook-app/ootd-app/frontend
docker run --rm -v "$PWD":/work alpine sh -lc "chown -R $(id -u):$(id -g) /work/node_modules /work/.next /work/package-lock.json || true"
```

## API endpoints

- `POST /v1/jobs`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/rerank`
- `POST /v1/jobs/{job_id}/approve`
- `POST /v1/jobs/{job_id}/retry`
- `POST /v1/jobs/{job_id}/publish`
- `POST /v1/catalog/crawl/jobs`
- `GET /v1/catalog/crawl/jobs/{crawl_job_id}`
- `POST /v1/catalog/index/rebuild`
- `GET /v1/catalog/stats`
- `GET /v1/history`
- `GET /healthz`
- `GET /v1/metrics`

## Similarity Retrieval (Current)

- 템플릿 고정 후보 대신 카탈로그(크롤링) 기반으로 후보를 검색합니다.
- 상품 이미지 임베딩(히스토그램 기반)을 생성하여 업로드 이미지와 코사인 유사도로 랭킹합니다.
- 카탈로그가 비어 있거나 외부 수집이 실패하면 검색 링크 기반 fallback 후보를 반환합니다.
- `CATALOG_MIN_IMAGE_SIM` (default `0.35`)로 최소 이미지 유사도 임계값을 조정할 수 있습니다.

## Real Product/Video Notes

- 상품 링크는 더 이상 `store.example.com`을 사용하지 않고, 무신사 검색 URL로 생성됩니다.
- `video_url`은 백엔드가 실제로 생성한 mp4 파일(`./data/assets/videos`)을 가리킵니다.

## YouTube Upload Setup

자동 업로드 또는 `Publish to YouTube` 버튼 사용을 위해 아래 환경변수가 필요합니다.

- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`
- `YOUTUBE_PRIVACY_STATUS` (`private|unlisted|public`, default `unlisted`)
- `YOUTUBE_UPLOAD_REQUIRED` (`1`이면 업로드 실패 시 job을 실패 처리)

예시:

```bash
export YOUTUBE_CLIENT_ID=\"...\"
export YOUTUBE_CLIENT_SECRET=\"...\"
export YOUTUBE_REFRESH_TOKEN=\"...\"
export YOUTUBE_PRIVACY_STATUS=\"unlisted\"
```
