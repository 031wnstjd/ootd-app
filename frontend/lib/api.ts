import {
  ApproveResponse,
  CreateJobInput,
  CreateJobResponse,
  HistoryResponse,
  JobDetailResponse,
  MetricsResponse,
  PublishResponse,
  RerankRequest,
  RerankResponse,
  RetryResponse
} from '@/lib/types';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {})
    },
    cache: 'no-store'
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new ApiError(body || `Request failed with ${res.status}`, res.status);
  }

  return (await res.json()) as T;
}

export async function createJob(input: CreateJobInput): Promise<CreateJobResponse> {
  const form = new FormData();
  form.append('image', input.imageFile);
  form.append('look_count', String(input.lookCount));
  form.append('quality_mode', input.qualityMode);
  if (input.tone) form.append('tone', input.tone);
  if (input.theme) form.append('theme', input.theme);

  return request<CreateJobResponse>('/v1/jobs', {
    method: 'POST',
    headers: {
      'Idempotency-Key': crypto.randomUUID()
    },
    body: form
  });
}

export async function getJob(jobId: string): Promise<JobDetailResponse> {
  return request<JobDetailResponse>(`/v1/jobs/${jobId}`);
}

export async function rerankJob(jobId: string, payload: RerankRequest): Promise<RerankResponse> {
  return request<RerankResponse>(`/v1/jobs/${jobId}/rerank`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function approveJob(jobId: string): Promise<ApproveResponse> {
  return request<ApproveResponse>(`/v1/jobs/${jobId}/approve`, {
    method: 'POST'
  });
}

export async function retryJob(jobId: string): Promise<RetryResponse> {
  return request<RetryResponse>(`/v1/jobs/${jobId}/retry`, {
    method: 'POST'
  });
}

export async function publishJob(jobId: string): Promise<PublishResponse> {
  return request<PublishResponse>(`/v1/jobs/${jobId}/publish`, {
    method: 'POST'
  });
}

export async function listHistory(limit = 20): Promise<HistoryResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<HistoryResponse>(`/v1/history?${params.toString()}`);
}

export async function getMetrics(): Promise<MetricsResponse> {
  return request<MetricsResponse>('/v1/metrics');
}

export function toApiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return `API ${err.status}: ${err.message}`;
  }

  if (err instanceof Error) {
    return err.message;
  }

  return 'Unknown error';
}
