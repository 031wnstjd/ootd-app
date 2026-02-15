export type QualityMode = 'auto_gate' | 'human_review';

export type JobStatus =
  | 'INGESTED'
  | 'ANALYZED'
  | 'MATCHED_PARTIAL'
  | 'MATCHED'
  | 'COMPOSED'
  | 'RENDERING'
  | 'REVIEW_REQUIRED'
  | 'COMPLETED'
  | 'FAILED';

export type FailureCode =
  | 'CRAWL_TIMEOUT'
  | 'EMPTY_RESULT'
  | 'RENDER_ERROR'
  | 'SAFETY_BLOCKED'
  | 'LICENSE_BLOCKED';

export interface ScoreBreakdown {
  image: number;
  text: number;
  category: number;
  price: number;
  final: number;
}

export interface MatchItem {
  category?: string;
  product_id?: string;
  brand?: string;
  product_name?: string;
  price?: number;
  product_url?: string;
  image_url?: string;
  evidence_tags?: string[];
  score_breakdown?: ScoreBreakdown;
  failure_code?: FailureCode;
}

export interface CreateJobResponse {
  job_id: string;
  status: JobStatus;
  estimated_seconds?: number;
}

export interface JobDetailResponse {
  job_id: string;
  status: JobStatus;
  quality_mode: QualityMode;
  look_count: number;
  progress?: number;
  items?: MatchItem[];
  preview_url?: string;
  video_url?: string;
  failure_code?: FailureCode;
  parent_job_id?: string;
  attempts?: number;
}

export interface RerankRequest {
  category: string;
  price_cap?: number;
  color_hint?: string;
}

export interface RerankResponse {
  job_id?: string;
  category?: string;
  candidates?: MatchItem[];
  selected?: MatchItem;
}

export interface ApproveResponse {
  job_id?: string;
  status?: JobStatus;
  video_url?: string;
}

export interface RetryResponse {
  previous_job_id: string;
  new_job_id: string;
  status: JobStatus;
}

export interface HistoryItem {
  job_id?: string;
  status?: JobStatus;
  created_at?: string;
  completed_at?: string;
  thumbnail_url?: string;
}

export interface HistoryResponse {
  jobs?: HistoryItem[];
}

export interface MetricsResponse {
  total_jobs_created: number;
  total_jobs_completed: number;
  total_jobs_failed: number;
  total_jobs_retried: number;
  avg_processing_seconds: number;
}

export interface CreateJobInput {
  imageFile: File;
  lookCount: number;
  qualityMode: QualityMode;
  tone?: string;
  theme?: string;
}
