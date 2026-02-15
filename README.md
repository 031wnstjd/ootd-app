# OOTD App MVP

`SPEC.md`/`openapi.yaml` 기반 MVP 구현입니다.

## Stack

- Backend: FastAPI (`backend/`)
- Frontend: Next.js + Tailwind (`frontend/`)
- API Contract: `openapi.yaml`

## Run (Local)

### 1) Backend

```bash
cd backend
python3 -m pip install -r requirements.txt --break-system-packages
python3 -m uvicorn app.main:app --reload --port 8000
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
- `GET /v1/history`
