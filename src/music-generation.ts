/** Asynchronous original-song jobs; media becomes playable after gateway approval and validation. */
import { readFile, mkdir, writeFile, rename } from 'node:fs/promises';
import { dirname } from 'node:path';

export type MusicGenerationStatus = 'queued' | 'reviewing' | 'generating' | 'validating'
  | 'ready' | 'rejected' | 'review' | 'failed' | 'cancelled';
export interface MusicGenerationRequest {
  requestText: string;
  title: string;
  lyrics: string;
  style: string;
  durationSec: number;
  requesterKey?: string;
  intro?: string;
  outro?: string;
}
export interface MusicGenerationJob {
  jobId: string;
  state: MusicGenerationStatus;
  title: string;
  createdAt: string;
  updatedAt: string;
  requesterKey?: string;
  trackId?: string;
  contentApproved?: true;
  audioValidated?: true;
  aiGenerated?: true;
}
export interface MusicGenerationState { enabled: boolean; jobs: MusicGenerationJob[]; error?: string }
export interface MusicGenerationOptions {
  url: () => string;
  onJob: (job: MusicGenerationJob) => Promise<void>;
  notificationFile?: string;
  fetch?: typeof fetch;
  pollIntervalMs?: number;
}
const STATES = new Set<MusicGenerationStatus>(['queued', 'reviewing', 'generating', 'validating', 'ready', 'rejected', 'review', 'failed', 'cancelled']);
const TERMINAL = new Set<MusicGenerationStatus>(['ready', 'rejected', 'review', 'failed', 'cancelled']);
const ID = /^[\p{L}\p{N}_-]{1,100}$/u;
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

function text(value: unknown, max: number, multiline = false): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= max
    && !CONTROL.test(multiline ? value.replace(/[\r\n]/g, '') : value);
}
export function generationRequest(value: unknown): MusicGenerationRequest {
  const r = value as MusicGenerationRequest;
  if (!r || !text(r.requestText, 1500) || !text(r.title, 100) || !text(r.lyrics, 3000, true)
    || !text(r.style, 600) || !Number.isInteger(r.durationSec) || r.durationSec < 30 || r.durationSec > 120
    || (r.requesterKey !== undefined && !text(r.requesterKey, 150))
    || (r.intro !== undefined && !text(r.intro, 500)) || (r.outro !== undefined && !text(r.outro, 500))) {
    throw new Error('原创歌曲需要点歌要求、标题、原创歌词、曲风和 30–120 秒整数时长。');
  }
  return { requestText: r.requestText, title: r.title, lyrics: r.lyrics, style: r.style, durationSec: r.durationSec,
    ...(r.requesterKey ? { requesterKey: r.requesterKey } : {}), ...(r.intro ? { intro: r.intro } : {}), ...(r.outro ? { outro: r.outro } : {}) };
}
export function generationJob(value: unknown): MusicGenerationJob {
  const j = value as MusicGenerationJob;
  if (!j || typeof j.jobId !== 'string' || !ID.test(j.jobId) || !STATES.has(j.state)
    || !text(j.title, 200) || !text(j.createdAt, 40) || !Number.isFinite(Date.parse(j.createdAt))
    || !text(j.updatedAt, 40) || !Number.isFinite(Date.parse(j.updatedAt))
    || (j.requesterKey !== undefined && !text(j.requesterKey, 150))
    || (j.trackId !== undefined && (typeof j.trackId !== 'string' || !ID.test(j.trackId)))) {
    throw new Error('歌曲生成服务返回了无效任务状态。');
  }
  if (j.state === 'ready' && (!j.trackId || j.contentApproved !== true || j.audioValidated !== true || j.aiGenerated !== true)) {
    throw new Error('歌曲缺少内容审核、音频验证或 AI 标识，不能播放。');
  }
  return { jobId: j.jobId, state: j.state, title: j.contentApproved === true ? j.title : '正在创作 AI 歌曲',
    createdAt: j.createdAt, updatedAt: j.updatedAt,
    ...(j.requesterKey ? { requesterKey: j.requesterKey } : {}), ...(j.trackId ? { trackId: j.trackId } : {}),
    ...(j.contentApproved === true ? { contentApproved: true as const } : {}),
    ...(j.audioValidated === true ? { audioValidated: true as const } : {}), ...(j.aiGenerated === true ? { aiGenerated: true as const } : {}) };
}

export class MusicGenerationClient {
  private jobs = new Map<string, MusicGenerationJob>();
  private notifications = new Set<string>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private controllers = new Set<AbortController>();
  private busy = false;
  private stopped = true;
  private endpoint = '';
  private revision = 0;
  private error?: string;
  constructor(private readonly options: MusicGenerationOptions) {}

  state(): MusicGenerationState {
    return { enabled: Boolean(this.options.url().trim()), jobs: [...this.jobs.values()].map(j => ({ ...j })),
      ...(this.error ? { error: this.error } : {}) };
  }
  async start(): Promise<void> {
    this.stopped = false;
    if (this.options.notificationFile) {
      try {
        const saved = JSON.parse(await readFile(this.options.notificationFile, 'utf8')) as unknown;
        if (Array.isArray(saved) && saved.length <= 1000 && saved.every(k => typeof k === 'string' && k.length < 500)) this.notifications = new Set(saved);
      } catch { /* A missing checkpoint causes terminal receipts to be delivered again. */ }
    }
    void this.refresh();
    this.timer = setInterval(() => { void this.refresh(); }, this.options.pollIntervalMs ?? 5000);
    this.timer.unref?.();
  }
  stop(): void {
    this.stopped = true;
    this.revision++;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    for (const controller of this.controllers) controller.abort();
  }
  private base(): string {
    const value = this.options.url().trim();
    if (!value) throw new Error('未配置原创歌曲生成服务。');
    let url: URL;
    try { url = new URL(value); } catch { throw new Error('歌曲生成服务地址无效。'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('歌曲生成服务地址需要无账号、查询参数的 HTTP(S) 地址。');
    }
    const base = value.replace(/\/+$/, '');
    if (base !== this.endpoint) { this.endpoint = base; this.jobs.clear(); this.revision++; }
    return base;
  }
  private async request(base: string, path: string, payload?: unknown): Promise<unknown> {
    const controller = new AbortController();
    this.controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await (this.options.fetch ?? fetch)(base + path, {
        method: payload === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' },
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) }), signal: controller.signal, redirect: 'error',
      });
      if (!response.ok) throw new Error('歌曲生成服务未受理请求，请查询任务状态。');
      const reader = response.body?.getReader();
      if (!reader) throw new Error('歌曲生成服务没有返回状态。');
      let bytes = 0;
      const chunks: Uint8Array[] = [];
      for (;;) {
        const part = await reader.read();
        if (part.done) break;
        bytes += part.value.byteLength;
        if (bytes > 262144) { await reader.cancel(); throw new Error('歌曲生成状态过大。'); }
        chunks.push(part.value);
      }
      return JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error('歌曲生成服务返回的状态无法解析。');
      if (controller.signal.aborted) throw new Error('歌曲生成请求未确认，请查状态，不要重复提交。');
      throw error;
    } finally { clearTimeout(timer); this.controllers.delete(controller); }
  }
  async submit(input: MusicGenerationRequest, requestKey: string): Promise<MusicGenerationJob> {
    const body = generationRequest(input);
    if (!text(requestKey, 150)) throw new Error('歌曲生成请求缺少稳定标识。');
    const base = this.base();
    const revision = this.revision;
    const job = generationJob(await this.request(base, '/jobs', { ...body, requestKey }));
    if (revision !== this.revision || this.stopped) throw new Error('歌曲生成请求的 World 已重载。');
    this.jobs.set(job.jobId, job);
    void this.refresh();
    return { ...job };
  }
  async cancel(jobId: string): Promise<MusicGenerationJob> {
    if (!ID.test(jobId)) throw new Error('取消生成需要有效 jobId。');
    const base = this.base();
    const revision = this.revision;
    const job = generationJob(await this.request(base, `/jobs/${encodeURIComponent(jobId)}/cancel`, {}));
    if (revision !== this.revision || this.stopped) throw new Error('歌曲生成请求的 World 已重载。');
    this.jobs.set(job.jobId, job);
    return { ...job };
  }
  async refresh(): Promise<void> {
    if (this.busy || this.stopped) return;
    if (!this.options.url().trim()) { this.jobs.clear(); this.error = undefined; this.revision++; return; }
    this.busy = true;
    try {
      const base = this.base();
      const revision = this.revision;
      const value = await this.request(base, '/jobs') as { jobs?: unknown };
      if (!value || !Array.isArray(value.jobs) || value.jobs.length > 30) throw new Error('歌曲任务列表无效。');
      const jobs = value.jobs.map(generationJob);
      if (this.stopped || revision !== this.revision || this.options.url().trim().replace(/\/+$/, '') !== base) return;
      this.jobs = new Map(jobs.map(j => [j.jobId, j]));
      for (const job of jobs) {
        const key = `${base}:${job.jobId}`;
        if (!TERMINAL.has(job.state) || this.notifications.has(key)) continue;
        await this.options.onJob({ ...job });
        if (this.stopped || revision !== this.revision) return;
        this.notifications.add(key);
        this.notifications = new Set([...this.notifications].slice(-1000));
        if (this.options.notificationFile) {
          await mkdir(dirname(this.options.notificationFile), { recursive: true });
          await writeFile(this.options.notificationFile + '.tmp', JSON.stringify([...this.notifications]));
          await rename(this.options.notificationFile + '.tmp', this.options.notificationFile);
        }
      }
      this.error = undefined;
    } catch (error) { if (!this.stopped) this.error = error instanceof Error ? error.message : '歌曲生成服务不可用。'; }
    finally { this.busy = false; }
  }
}
