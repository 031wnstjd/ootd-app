# Production Plan (Post-MVP)

## Goal

MVP 데모를 넘어 실사용 가능한 1차 운영 수준(내구성, 복구, 운영 가시성)을 확보한다.

## Scope Implemented In This Iteration

1. Job 상태 파일 영속화(`JOB_STATE_FILE`) 및 재기동 복구.
2. 실패 Job 재시도 API(`POST /v1/jobs/{job_id}/retry`).
3. 운영 API 추가:
   - `GET /healthz`
   - `GET /v1/metrics`
4. 생성 입력 방어:
   - `image/*` 콘텐츠 타입 강제
   - 10MB 초과 업로드 차단
   - `Idempotency-Key` 기반 중복 생성 완화
5. 프론트 운영 UX:
   - FAILED 상태에서 `Retry Failed Job`
   - 운영 메트릭 패널 노출

## Remaining For Real Production

1. Redis queue + worker 분리(Celery/RQ).
2. PostgreSQL 기반 다중 인스턴스 안전 영속화.
3. 인증/워크스페이스 모델(이메일 로그인).
4. FFmpeg 렌더 파이프라인 및 S3 저장 연동.
