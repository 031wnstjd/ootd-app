'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  approveJob,
  createJob,
  getCatalogCrawlJob,
  getCatalogStats,
  getMetrics,
  getJob,
  listHistory,
  publishJob,
  rebuildCatalogIndex,
  rerankJob,
  retryJob,
  startCatalogCrawl,
  toApiErrorMessage
} from '@/lib/api';
import {
  CatalogCrawlJobDetailResponse,
  CatalogStatsResponse,
  FailureCode,
  HistoryItem,
  JobDetailResponse,
  JobStatus,
  MatchItem,
  MetricsResponse,
  QualityMode,
  ScoreBreakdown
} from '@/lib/types';

const ACTIVE_STATUSES: JobStatus[] = [
  'INGESTED',
  'ANALYZED',
  'MATCHED_PARTIAL',
  'MATCHED',
  'COMPOSED',
  'RENDERING'
];

const STAGE_ORDER: JobStatus[] = [
  'INGESTED',
  'ANALYZED',
  'MATCHED',
  'COMPOSED',
  'RENDERING',
  'REVIEW_REQUIRED',
  'COMPLETED'
];

const RERANK_PRESET_STORAGE_KEY = 'ootd_rerank_presets_v1';

type RerankPreset = {
  priceCap?: string;
  colorHint?: string;
};

type FailureGuide = {
  title: string;
  message: string;
  actionLabel?: string;
};

function statusTone(status?: JobStatus): string {
  if (!status) return 'text-slate-500';
  if (status === 'FAILED') return 'text-bad';
  if (status === 'COMPLETED') return 'text-ok';
  if (status === 'REVIEW_REQUIRED') return 'text-warn';
  return 'text-accent';
}

function formatScore(score?: number): string {
  if (score === undefined || Number.isNaN(score)) return '-';
  return score.toFixed(3);
}

function scoreRows(score?: ScoreBreakdown): Array<{ key: string; value: number | undefined }> {
  return [
    { key: 'image', value: score?.image },
    { key: 'text', value: score?.text },
    { key: 'category', value: score?.category },
    { key: 'price', value: score?.price },
    { key: 'final', value: score?.final }
  ];
}

function failureGuide(code?: FailureCode): FailureGuide | null {
  if (!code) return null;
  if (code === 'EMPTY_RESULT') {
    return {
      title: 'No Match Result',
      message: '조건을 완화해서 rerank 하거나 color hint를 추가해 후보를 다시 생성하세요.',
      actionLabel: 'Retry Match'
    };
  }
  if (code === 'RENDER_ERROR') {
    return {
      title: 'Render Failed',
      message: '렌더 단계에서 실패했습니다. 상태를 새로고침한 뒤 재시도 가능한지 확인하세요.',
      actionLabel: 'Refresh Status'
    };
  }
  if (code === 'CRAWL_TIMEOUT') {
    return {
      title: 'Crawl Timeout',
      message: '일시적 타임아웃일 수 있습니다. price cap을 완화하고 다시 rerank 해보세요.',
      actionLabel: 'Retry Match'
    };
  }
  if (code === 'SAFETY_BLOCKED') {
    return {
      title: 'Safety Blocked',
      message: '안전 필터에 의해 차단되었습니다. 보다 보수적인 tone/theme로 새 Job을 생성하세요.',
      actionLabel: 'Apply Safe Preset'
    };
  }
  if (code === 'LICENSE_BLOCKED') {
    return {
      title: 'License Blocked',
      message: '라이선스 제약으로 중단되었습니다. 다른 스타일 조건으로 새 Job을 시도하세요.',
      actionLabel: 'Apply Safe Preset'
    };
  }
  return null;
}

export default function DashboardPage() {
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [lookCount, setLookCount] = useState<number>(3);
  const [qualityMode, setQualityMode] = useState<QualityMode>('auto_gate');
  const [tone, setTone] = useState('clean');
  const [theme, setTheme] = useState('street casual');

  const [activeJobId, setActiveJobId] = useState<string>('');
  const [job, setJob] = useState<JobDetailResponse | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [catalogStats, setCatalogStats] = useState<CatalogStatsResponse | null>(null);
  const [crawlJob, setCrawlJob] = useState<CatalogCrawlJobDetailResponse | null>(null);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRefreshingJob, setIsRefreshingJob] = useState(false);
  const [isRefreshingHistory, setIsRefreshingHistory] = useState(false);
  const [isApproving, setIsApproving] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [isCrawling, setIsCrawling] = useState(false);
  const [isRebuilding, setIsRebuilding] = useState(false);
  const [rerankBusyCategory, setRerankBusyCategory] = useState<string>('');
  const [rerankPriceCapByCategory, setRerankPriceCapByCategory] = useState<Record<string, string>>({});
  const [rerankColorHintByCategory, setRerankColorHintByCategory] = useState<Record<string, string>>({});
  const [savedRerankPresetByCategory, setSavedRerankPresetByCategory] = useState<Record<string, RerankPreset>>({});

  const [formError, setFormError] = useState('');
  const [jobError, setJobError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [metricsError, setMetricsError] = useState('');
  const [catalogError, setCatalogError] = useState('');

  const canApprove = job?.status === 'REVIEW_REQUIRED' && job.quality_mode === 'human_review';
  const isPolling = !!job?.status && ACTIVE_STATUSES.includes(job.status);

  const stageIndex = useMemo(() => {
    if (!job?.status) return -1;
    const normalized = job.status === 'MATCHED_PARTIAL' ? 'MATCHED' : job.status;
    return STAGE_ORDER.indexOf(normalized);
  }, [job?.status]);

  async function refreshHistory() {
    setHistoryError('');
    setIsRefreshingHistory(true);
    try {
      const res = await listHistory(20);
      setHistory(res.jobs ?? []);
    } catch (err) {
      setHistoryError(toApiErrorMessage(err));
    } finally {
      setIsRefreshingHistory(false);
    }
  }

  async function refreshJob(jobId: string) {
    if (!jobId) return;
    setJobError('');
    setIsRefreshingJob(true);
    try {
      const detail = await getJob(jobId);
      setJob(detail);
    } catch (err) {
      setJobError(toApiErrorMessage(err));
    } finally {
      setIsRefreshingJob(false);
    }
  }

  async function refreshMetrics() {
    setMetricsError('');
    try {
      const res = await getMetrics();
      setMetrics(res);
    } catch (err) {
      setMetricsError(toApiErrorMessage(err));
    }
  }

  async function refreshCatalogStats() {
    setCatalogError('');
    try {
      const stats = await getCatalogStats();
      setCatalogStats(stats);
    } catch (err) {
      setCatalogError(toApiErrorMessage(err));
    }
  }

  useEffect(() => {
    void refreshHistory();
    void refreshMetrics();
    void refreshCatalogStats();
  }, []);

  useEffect(() => {
    const raw = window.localStorage.getItem(RERANK_PRESET_STORAGE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as Record<string, RerankPreset>;
      setSavedRerankPresetByCategory(parsed);
    } catch {
      setSavedRerankPresetByCategory({});
    }
  }, []);

  useEffect(() => {
    if (!activeJobId || !isPolling) return;

    const timer = window.setInterval(() => {
      void refreshJob(activeJobId);
    }, 3000);

    return () => window.clearInterval(timer);
  }, [activeJobId, isPolling]);

  async function onCreateJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError('');

    if (!imageFile) {
      setFormError('image is required');
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await createJob({
        imageFile,
        lookCount,
        qualityMode,
        tone: tone.trim(),
        theme: theme.trim()
      });
      setActiveJobId(created.job_id);
      await refreshJob(created.job_id);
      await refreshHistory();
      await refreshMetrics();
    } catch (err) {
      setFormError(toApiErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function onRerank(item: MatchItem) {
    if (!job?.job_id || !item.category) return;

    const rawCap = rerankPriceCapByCategory[item.category]?.trim();
    const parsedPriceCap = rawCap ? Number(rawCap) : Number.NaN;
    const colorHint = rerankColorHintByCategory[item.category]?.trim();

    setJobError('');
    setRerankBusyCategory(item.category);
    try {
      const res = await rerankJob(job.job_id, {
        category: item.category,
        price_cap: Number.isFinite(parsedPriceCap) ? parsedPriceCap : undefined,
        color_hint: colorHint || undefined
      });
      if (res.selected && job.items) {
        const next = job.items.map((entry) =>
          entry.category === item.category ? res.selected ?? entry : entry
        );
        setJob({ ...job, items: next });
      }
      await refreshJob(job.job_id);
    } catch (err) {
      setJobError(toApiErrorMessage(err));
    } finally {
      setRerankBusyCategory('');
    }
  }

  function setRerankPriceCap(category: string, value: string) {
    setRerankPriceCapByCategory((prev) => ({ ...prev, [category]: value }));
  }

  function setRerankColorHint(category: string, value: string) {
    setRerankColorHintByCategory((prev) => ({ ...prev, [category]: value }));
  }

  function persistPreset(nextPresets: Record<string, RerankPreset>) {
    setSavedRerankPresetByCategory(nextPresets);
    window.localStorage.setItem(RERANK_PRESET_STORAGE_KEY, JSON.stringify(nextPresets));
  }

  function onSavePreset(category: string) {
    const next = {
      ...savedRerankPresetByCategory,
      [category]: {
        priceCap: rerankPriceCapByCategory[category]?.trim() || '',
        colorHint: rerankColorHintByCategory[category]?.trim() || ''
      }
    };
    persistPreset(next);
  }

  function onLoadPreset(category: string) {
    const preset = savedRerankPresetByCategory[category];
    if (!preset) return;
    setRerankPriceCap(category, preset.priceCap ?? '');
    setRerankColorHint(category, preset.colorHint ?? '');
  }

  function onClearPreset(category: string) {
    const next = { ...savedRerankPresetByCategory };
    delete next[category];
    persistPreset(next);
    setRerankPriceCap(category, '');
    setRerankColorHint(category, '');
  }

  function applySafeCreatePreset() {
    setTone('clean');
    setTheme('minimal basic');
  }

  async function onApprove() {
    if (!job?.job_id) return;

    setJobError('');
    setIsApproving(true);
    try {
      await approveJob(job.job_id);
      await refreshJob(job.job_id);
      await refreshHistory();
      await refreshMetrics();
    } catch (err) {
      setJobError(toApiErrorMessage(err));
    } finally {
      setIsApproving(false);
    }
  }

  async function onSelectHistory(item: HistoryItem) {
    if (!item.job_id) return;
    setActiveJobId(item.job_id);
    await refreshJob(item.job_id);
  }

  async function onRetryFailedJob() {
    if (!job?.job_id || job.status !== 'FAILED') return;
    setJobError('');
    setIsRetrying(true);
    try {
      const res = await retryJob(job.job_id);
      setActiveJobId(res.new_job_id);
      await refreshJob(res.new_job_id);
      await refreshHistory();
      await refreshMetrics();
    } catch (err) {
      setJobError(toApiErrorMessage(err));
    } finally {
      setIsRetrying(false);
    }
  }

  async function onPublishYoutube() {
    if (!job?.job_id) return;
    setJobError('');
    setIsPublishing(true);
    try {
      await publishJob(job.job_id);
      await refreshJob(job.job_id);
      await refreshMetrics();
    } catch (err) {
      setJobError(toApiErrorMessage(err));
    } finally {
      setIsPublishing(false);
    }
  }

  async function onStartCatalogCrawl() {
    setCatalogError('');
    setIsCrawling(true);
    try {
      const started = await startCatalogCrawl(30);
      const detail = await getCatalogCrawlJob(started.crawl_job_id);
      setCrawlJob(detail);
      await refreshCatalogStats();
    } catch (err) {
      setCatalogError(toApiErrorMessage(err));
    } finally {
      setIsCrawling(false);
    }
  }

  async function onRebuildCatalogIndex() {
    setCatalogError('');
    setIsRebuilding(true);
    try {
      await rebuildCatalogIndex();
      await refreshCatalogStats();
    } catch (err) {
      setCatalogError(toApiErrorMessage(err));
    } finally {
      setIsRebuilding(false);
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-8 py-8">
      <header className="mb-6 flex items-end justify-between gap-6">
        <div>
          <h1 className="text-3xl font-semibold">OOTD Dashboard MVP</h1>
          <p className="mt-1 text-sm text-muted">Create jobs, monitor status, review match evidence, and approve outputs.</p>
        </div>
        <p className="text-sm text-muted">API: {process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000'}</p>
      </header>

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-[1.2fr_1fr]">
        <form onSubmit={onCreateJob} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold">Create Job</h2>

          <label className="mb-4 block text-sm">
            <span className="mb-1 block font-medium">image</span>
            <input
              type="file"
              accept="image/*"
              onChange={(e) => setImageFile(e.target.files?.[0] ?? null)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 outline-none transition focus:border-accent"
            />
          </label>

          <div className="mb-4 grid grid-cols-2 gap-4">
            <label className="text-sm">
              <span className="mb-1 block font-medium">look_count</span>
              <select
                value={lookCount}
                onChange={(e) => setLookCount(Number(e.target.value))}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              >
                {[1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>

            <label className="text-sm">
              <span className="mb-1 block font-medium">quality_mode</span>
              <select
                value={qualityMode}
                onChange={(e) => setQualityMode(e.target.value as QualityMode)}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              >
                <option value="auto_gate">auto_gate</option>
                <option value="human_review">human_review</option>
              </select>
            </label>
          </div>

          <div className="mb-4 grid grid-cols-2 gap-4">
            <label className="text-sm">
              <span className="mb-1 block font-medium">tone</span>
              <input
                value={tone}
                onChange={(e) => setTone(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>

            <label className="text-sm">
              <span className="mb-1 block font-medium">theme</span>
              <input
                value={theme}
                onChange={(e) => setTheme(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </label>
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="rounded-md bg-ink px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {isSubmitting ? 'Creating...' : 'Create Job'}
          </button>

          {formError && <p className="mt-3 text-sm text-bad">{formError}</p>}
        </form>

        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Job Progress</h2>
            <button
              type="button"
              onClick={() => activeJobId && void refreshJob(activeJobId)}
              disabled={!activeJobId || isRefreshingJob}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isRefreshingJob ? 'Refreshing...' : 'Refresh'}
            </button>
          </div>

          {!job && <p className="text-sm text-muted">No job selected yet.</p>}

          {job && (
            <div className="space-y-4">
              <div className="rounded-md bg-panel p-3 text-sm">
                <p>job_id: {job.job_id}</p>
                <p className={statusTone(job.status)}>status: {job.status}</p>
                <p>progress: {job.progress ?? '-'}%</p>
                <p>quality_mode: {job.quality_mode}</p>
                <p>look_count: {job.look_count}</p>
                <p>attempts: {job.attempts ?? 1}</p>
                <p>parent_job_id: {job.parent_job_id ?? '-'}</p>
              </div>

              <div>
                <p className="mb-2 text-sm font-medium">Stage</p>
                <div className="flex gap-2 text-xs">
                  {STAGE_ORDER.map((stage, idx) => (
                    <span
                      key={stage}
                      className={`rounded px-2 py-1 ${
                        idx <= stageIndex ? 'bg-accent text-white' : 'bg-slate-200 text-slate-600'
                      }`}
                    >
                      {stage}
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <p className="mb-2 text-sm font-medium">Result Links</p>
                <div className="space-y-1 text-sm">
                  <p>
                    preview_url:{' '}
                    {job.preview_url ? (
                      <a className="text-accent underline" href={job.preview_url} target="_blank" rel="noreferrer">
                        open
                      </a>
                    ) : (
                      '-'
                    )}
                  </p>
                  <p>
                    video_url:{' '}
                    {job.video_url ? (
                      <a className="text-accent underline" href={job.video_url} target="_blank" rel="noreferrer">
                        open
                      </a>
                    ) : (
                      '-'
                    )}
                  </p>
                  <p>youtube_upload_status: {job.youtube_upload_status ?? '-'}</p>
                  <p>
                    youtube_url:{' '}
                    {job.youtube_url ? (
                      <a className="text-accent underline" href={job.youtube_url} target="_blank" rel="noreferrer">
                        open
                      </a>
                    ) : (
                      '-'
                    )}
                  </p>
                  <p>failure_code: {job.failure_code ?? '-'}</p>
                </div>
              </div>

              {job.failure_code && (
                <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">
                  <p className="font-medium">{failureGuide(job.failure_code)?.title}</p>
                  <p className="mt-1 text-slate-700">{failureGuide(job.failure_code)?.message}</p>
                  <div className="mt-2">
                    {(job.failure_code === 'EMPTY_RESULT' || job.failure_code === 'RENDER_ERROR') && (
                      <button
                        type="button"
                        onClick={() => activeJobId && void refreshJob(activeJobId)}
                        className="rounded border border-amber-500 px-3 py-1.5 text-xs font-medium text-amber-700"
                      >
                        {failureGuide(job.failure_code)?.actionLabel}
                      </button>
                    )}
                    {(job.failure_code === 'SAFETY_BLOCKED' || job.failure_code === 'LICENSE_BLOCKED') && (
                      <button
                        type="button"
                        onClick={applySafeCreatePreset}
                        className="rounded border border-amber-500 px-3 py-1.5 text-xs font-medium text-amber-700"
                      >
                        {failureGuide(job.failure_code)?.actionLabel}
                      </button>
                    )}
                  </div>
                </div>
              )}

              {canApprove && (
                <button
                  type="button"
                  onClick={() => void onApprove()}
                  disabled={isApproving}
                  className="rounded-md bg-ok px-4 py-2 text-sm font-medium text-white disabled:bg-slate-400"
                >
                  {isApproving ? 'Approving...' : 'Approve (human_review)'}
                </button>
              )}
              {job.status === 'FAILED' && (
                <button
                  type="button"
                  onClick={() => void onRetryFailedJob()}
                  disabled={isRetrying}
                  className="rounded-md bg-ink px-4 py-2 text-sm font-medium text-white disabled:bg-slate-400"
                >
                  {isRetrying ? 'Retrying...' : 'Retry Failed Job'}
                </button>
              )}
              {(job.status === 'COMPLETED' || job.status === 'REVIEW_REQUIRED') && (
                <button
                  type="button"
                  onClick={() => void onPublishYoutube()}
                  disabled={isPublishing}
                  className="rounded-md border border-accent px-4 py-2 text-sm font-medium text-accent disabled:opacity-50"
                >
                  {isPublishing ? 'Publishing...' : 'Publish to YouTube'}
                </button>
              )}
            </div>
          )}

          {jobError && <p className="mt-3 text-sm text-bad">{jobError}</p>}
        </div>
      </section>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Catalog Retrieval Ops</h2>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void onStartCatalogCrawl()}
              disabled={isCrawling}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {isCrawling ? 'Crawling...' : 'Start Crawl'}
            </button>
            <button
              type="button"
              onClick={() => void onRebuildCatalogIndex()}
              disabled={isRebuilding}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {isRebuilding ? 'Rebuilding...' : 'Rebuild Index'}
            </button>
          </div>
        </div>
        {catalogStats ? (
          <div className="grid grid-cols-2 gap-2 text-sm lg:grid-cols-4">
            <p className="rounded bg-panel px-2 py-1">products: {catalogStats.total_products}</p>
            <p className="rounded bg-panel px-2 py-1">indexed: {catalogStats.total_indexed_products}</p>
            <p className="rounded bg-panel px-2 py-1">
              categories: {Object.keys(catalogStats.categories ?? {}).length}
            </p>
            <p className="rounded bg-panel px-2 py-1">
              last crawl: {catalogStats.last_crawl_completed_at ?? '-'}
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted">No catalog stats yet.</p>
        )}
        {crawlJob && (
          <p className="mt-2 text-sm text-muted">
            crawl_job: {crawlJob.crawl_job_id} / status: {crawlJob.status} / discovered: {crawlJob.total_discovered} / indexed:{' '}
            {crawlJob.total_indexed}
          </p>
        )}
        {catalogError && <p className="mt-3 text-sm text-bad">{catalogError}</p>}
      </section>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Ops Metrics</h2>
          <button
            type="button"
            onClick={() => void refreshMetrics()}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          >
            Refresh
          </button>
        </div>
        {!metrics && <p className="text-sm text-muted">No metrics yet.</p>}
        {metrics && (
          <div className="grid grid-cols-2 gap-2 text-sm lg:grid-cols-5">
            <p className="rounded bg-panel px-2 py-1">created: {metrics.total_jobs_created}</p>
            <p className="rounded bg-panel px-2 py-1">completed: {metrics.total_jobs_completed}</p>
            <p className="rounded bg-panel px-2 py-1">failed: {metrics.total_jobs_failed}</p>
            <p className="rounded bg-panel px-2 py-1">retried: {metrics.total_jobs_retried}</p>
            <p className="rounded bg-panel px-2 py-1">avg sec: {metrics.avg_processing_seconds.toFixed(3)}</p>
            <p className="rounded bg-panel px-2 py-1">yt uploaded: {metrics.total_youtube_uploaded}</p>
          </div>
        )}
        {metricsError && <p className="mt-3 text-sm text-bad">{metricsError}</p>}
      </section>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-lg font-semibold">Score Breakdown and Evidence</h2>

        {!job?.items?.length && <p className="text-sm text-muted">No match items yet.</p>}

        {job?.items?.length ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {job.items.map((item, index) => (
              <article key={`${item.category ?? 'item'}-${item.product_id ?? index}`} className="rounded-md border border-slate-200 p-4">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted">{item.category ?? 'unknown category'}</p>
                    <p className="font-medium">{item.brand ?? '-'} / {item.product_name ?? '-'}</p>
                    <p className="text-sm text-muted">product_id: {item.product_id ?? '-'}</p>
                  </div>
                  <div className="w-48 space-y-2">
                    <input
                      type="number"
                      min={10000}
                      step={1000}
                      value={item.category ? (rerankPriceCapByCategory[item.category] ?? '') : ''}
                      onChange={(e) => item.category && setRerankPriceCap(item.category, e.target.value)}
                      placeholder="price cap"
                      className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-xs"
                    />
                    <input
                      type="text"
                      value={item.category ? (rerankColorHintByCategory[item.category] ?? '') : ''}
                      onChange={(e) => item.category && setRerankColorHint(item.category, e.target.value)}
                      placeholder="color hint"
                      className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-xs"
                    />
                    <button
                      type="button"
                      onClick={() => void onRerank(item)}
                      disabled={!item.category || rerankBusyCategory === item.category}
                      className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {rerankBusyCategory === item.category ? 'Reranking...' : 'Rerank'}
                    </button>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => item.category && onSavePreset(item.category)}
                        disabled={!item.category}
                        className="flex-1 rounded border border-slate-300 px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Save
                      </button>
                      <button
                        type="button"
                        onClick={() => item.category && onLoadPreset(item.category)}
                        disabled={!item.category || !savedRerankPresetByCategory[item.category]}
                        className="flex-1 rounded border border-slate-300 px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Load
                      </button>
                      <button
                        type="button"
                        onClick={() => item.category && onClearPreset(item.category)}
                        disabled={!item.category}
                        className="flex-1 rounded border border-slate-300 px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Clear
                      </button>
                    </div>
                    {item.category && savedRerankPresetByCategory[item.category] && (
                      <p className="text-[11px] text-muted">
                        preset: cap {savedRerankPresetByCategory[item.category]?.priceCap || '-'} / color{' '}
                        {savedRerankPresetByCategory[item.category]?.colorHint || '-'}
                      </p>
                    )}
                  </div>
                </div>

                <div className="mb-3 grid grid-cols-2 gap-2 text-sm">
                  {scoreRows(item.score_breakdown).map((row) => (
                    <p key={`${item.category ?? 'x'}-${row.key}`} className="rounded bg-panel px-2 py-1">
                      {row.key}: {formatScore(row.value)}
                    </p>
                  ))}
                </div>

                <div className="mb-2 flex flex-wrap gap-2">
                  {(item.evidence_tags ?? []).map((tag) => (
                    <span key={`${item.category ?? 'x'}-${tag}`} className="rounded-full bg-slate-200 px-2 py-0.5 text-xs text-slate-700">
                      {tag}
                    </span>
                  ))}
                  {!item.evidence_tags?.length && <span className="text-xs text-muted">No evidence tags</span>}
                </div>

                {item.product_url && (
                  <a className="text-sm text-accent underline" href={item.product_url} target="_blank" rel="noreferrer">
                    Product link
                  </a>
                )}

                {item.failure_code && (
                  <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs">
                    <p className="font-medium">{failureGuide(item.failure_code)?.title}</p>
                    <p className="mt-1 text-slate-700">{failureGuide(item.failure_code)?.message}</p>
                    {(item.failure_code === 'EMPTY_RESULT' || item.failure_code === 'CRAWL_TIMEOUT') && (
                      <button
                        type="button"
                        onClick={() => void onRerank(item)}
                        disabled={!item.category || rerankBusyCategory === item.category}
                        className="mt-2 rounded border border-amber-500 px-2 py-1 text-[11px] font-medium text-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {failureGuide(item.failure_code)?.actionLabel}
                      </button>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">History</h2>
          <button
            type="button"
            onClick={() => void refreshHistory()}
            disabled={isRefreshingHistory}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRefreshingHistory ? 'Loading...' : 'Reload'}
          </button>
        </div>

        {!history.length && <p className="text-sm text-muted">No history records.</p>}

        {history.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[800px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left">
                  <th className="py-2 pr-3 font-medium">job_id</th>
                  <th className="py-2 pr-3 font-medium">status</th>
                  <th className="py-2 pr-3 font-medium">created_at</th>
                  <th className="py-2 pr-3 font-medium">completed_at</th>
                  <th className="py-2 pr-3 font-medium">open</th>
                </tr>
              </thead>
              <tbody>
                {history.map((item, idx) => (
                  <tr key={`${item.job_id ?? 'job'}-${idx}`} className="border-b border-slate-100">
                    <td className="py-2 pr-3">{item.job_id ?? '-'}</td>
                    <td className={`py-2 pr-3 ${statusTone(item.status)}`}>{item.status ?? '-'}</td>
                    <td className="py-2 pr-3">{item.created_at ?? '-'}</td>
                    <td className="py-2 pr-3">{item.completed_at ?? '-'}</td>
                    <td className="py-2 pr-3">
                      <button
                        type="button"
                        onClick={() => void onSelectHistory(item)}
                        disabled={!item.job_id}
                        className="rounded border border-slate-300 px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Select
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {historyError && <p className="mt-3 text-sm text-bad">{historyError}</p>}
      </section>
    </main>
  );
}
