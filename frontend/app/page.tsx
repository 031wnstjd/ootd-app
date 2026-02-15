'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  approveJob,
  createJob,
  getJob,
  listHistory,
  rerankJob,
  toApiErrorMessage
} from '@/lib/api';
import {
  HistoryItem,
  JobDetailResponse,
  JobStatus,
  MatchItem,
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

export default function DashboardPage() {
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [lookCount, setLookCount] = useState<number>(3);
  const [qualityMode, setQualityMode] = useState<QualityMode>('auto_gate');
  const [tone, setTone] = useState('clean');
  const [theme, setTheme] = useState('street casual');

  const [activeJobId, setActiveJobId] = useState<string>('');
  const [job, setJob] = useState<JobDetailResponse | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRefreshingJob, setIsRefreshingJob] = useState(false);
  const [isRefreshingHistory, setIsRefreshingHistory] = useState(false);
  const [isApproving, setIsApproving] = useState(false);
  const [rerankBusyCategory, setRerankBusyCategory] = useState<string>('');

  const [formError, setFormError] = useState('');
  const [jobError, setJobError] = useState('');
  const [historyError, setHistoryError] = useState('');

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

  useEffect(() => {
    void refreshHistory();
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
    } catch (err) {
      setFormError(toApiErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function onRerank(item: MatchItem) {
    if (!job?.job_id || !item.category) return;

    setJobError('');
    setRerankBusyCategory(item.category);
    try {
      const res = await rerankJob(job.job_id, { category: item.category });
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

  async function onApprove() {
    if (!job?.job_id) return;

    setJobError('');
    setIsApproving(true);
    try {
      await approveJob(job.job_id);
      await refreshJob(job.job_id);
      await refreshHistory();
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
                  <p>failure_code: {job.failure_code ?? '-'}</p>
                </div>
              </div>

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
            </div>
          )}

          {jobError && <p className="mt-3 text-sm text-bad">{jobError}</p>}
        </div>
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
                  <button
                    type="button"
                    onClick={() => void onRerank(item)}
                    disabled={!item.category || rerankBusyCategory === item.category}
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {rerankBusyCategory === item.category ? 'Reranking...' : 'Rerank'}
                  </button>
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
