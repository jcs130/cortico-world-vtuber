/** Optional media fallback while a main model request is pending. Assets are prepared before selection. */
import type { RunPhase } from 'cortico/core/types.ts';
import { parseMediaChoice } from './media-choice.ts';

export interface WaitingMediaConfig {
  enabled: boolean;
  endpoint: string;
  timeoutMs: number;
  minConfidence: number;
  afterMs: number;
  intervalMs: number;
  cooldownMs: number;
  maxPerRequest: number;
  musicIds: string;
  lines: string;
}

export const WAITING_MEDIA_DEFAULTS: WaitingMediaConfig = {
  enabled: false, endpoint: '', timeoutMs: 150, minConfidence: .8,
  afterMs: 12_000, intervalMs: 45_000, cooldownMs: 90_000, maxPerRequest: 2,
  musicIds: '', lines: '',
};

export type WaitingPlaybackStatus = 'queued' | 'playing' | 'ended' | 'stopped' | 'failed';
export interface WaitingMediaAsset {
  id: string;
  kind: 'music' | 'speech';
  title: string;
  durationMs: number;
  play(report: (status: WaitingPlaybackStatus) => void): () => void;
}

interface WaitingMediaDependencies {
  config(): WaitingMediaConfig;
  assetKey(): string;
  prepare(config: WaitingMediaConfig, signal: AbortSignal): Promise<WaitingMediaAsset[]>;
  availability(): { live: boolean; idle: boolean; quietSinceMs: number | null };
  report(data: Record<string, unknown>): void;
  fetch?: typeof fetch;
  now?: () => number;
}

export class WaitingMedia {
  private readonly now: () => number;
  private readonly fetch: typeof fetch;
  private phase: RunPhase | null = null;
  private requestKey = '';
  private key = '';
  private assets: WaitingMediaAsset[] = [];
  private preparing: AbortController | null = null;
  private selecting: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private poll: ReturnType<typeof setInterval> | undefined;
  private cancelPlayback: (() => void) | null = null;
  private playbackGeneration = 0;
  private played = 0;
  private attempts = 0;
  private yielded = false;
  private used = new Set<string>();
  private recent: { id: string; atMs: number }[] = [];
  private lastPlayedAt = -Infinity;
  private nextAttemptAt = -Infinity;

  constructor(private readonly d: WaitingMediaDependencies) {
    this.now = d.now ?? Date.now;
    this.fetch = d.fetch ?? fetch;
  }

  start(): void {
    if (this.poll) return;
    this.poll = setInterval(() => this.refresh(), 1000);
    this.poll.unref?.();
    this.refresh();
  }

  stop(): void {
    clearInterval(this.poll); this.poll = undefined;
    this.phase = null; this.requestKey = '';
    this.preparing?.abort(); this.preparing = null;
    this.key = ''; this.assets = [];
    this.cancel();
  }

  onPhase(phase: RunPhase): void {
    const key = `${phase.enteredAt}:${phase.round ?? ''}`;
    if (phase.state !== 'model' || key !== this.requestKey) {
      this.cancel();
      this.requestKey = phase.state === 'model' ? key : '';
      this.played = 0; this.attempts = 0; this.yielded = false; this.used.clear(); this.nextAttemptAt = -Infinity;
    }
    this.phase = phase;
    if (phase.state !== 'model' || phase.running.length) this.cancel();
    else this.schedule();
  }

  /** New primary speech takes the audio lane even before the model round has ended. */
  yield(): void { this.yielded = true; this.cancel(); }

  private cancel(): void {
    clearTimeout(this.timer); this.timer = undefined;
    this.selecting?.abort(); this.selecting = null;
    const stop = this.cancelPlayback;
    this.playbackGeneration++;
    this.cancelPlayback = null;
    stop?.();
  }

  private refresh(): void {
    const cfg = this.d.config();
    if (!cfg.enabled || !cfg.endpoint.trim()) {
      this.cancel(); this.preparing?.abort(); this.preparing = null;
      this.assets = []; this.key = '';
      return;
    }
    const key = JSON.stringify([this.d.assetKey(), cfg.lines, cfg.musicIds]);
    if (key !== this.key) {
      this.cancel(); this.preparing?.abort(); this.assets = []; this.key = key;
      const controller = new AbortController();
      this.preparing = controller;
      void this.d.prepare(cfg, controller.signal).then(assets => {
        if (controller.signal.aborted || key !== this.key) return;
        this.assets = assets.filter(asset => Number.isFinite(asset.durationMs) && asset.durationMs > 0
          && asset.durationMs <= (asset.kind === 'speech' ? 8000 : 120_000)).slice(0, 9);
        this.d.report({ status: 'prepared', count: this.assets.length });
      }).catch(error => {
        if (!controller.signal.aborted) this.d.report({ status: 'prepare-failed', error: String(error) });
      }).finally(() => { if (this.preparing === controller) this.preparing = null; });
    }
    this.schedule();
  }

  private eligible(checkAttempts = true): boolean {
    const cfg = this.d.config();
    const a = this.d.availability();
    const startedAt = Date.parse(this.phase?.enteredAt ?? '');
    return cfg.enabled && !!cfg.endpoint.trim() && !this.yielded && this.phase?.state === 'model' && !this.phase.running.length
      && Number.isFinite(startedAt) && this.now() - startedAt >= cfg.afterMs
      && a.live && a.idle && a.quietSinceMs !== null && this.now() - a.quietSinceMs >= cfg.afterMs
      && this.played < cfg.maxPerRequest && (!checkAttempts || this.attempts < cfg.maxPerRequest * 2)
      && this.now() >= this.nextAttemptAt && this.now() - this.lastPlayedAt >= cfg.cooldownMs;
  }

  private schedule(): void {
    if (this.timer || this.selecting || this.cancelPlayback || this.phase?.state !== 'model') return;
    const cfg = this.d.config();
    if (!cfg.enabled || this.yielded || this.played >= cfg.maxPerRequest || this.attempts >= cfg.maxPerRequest * 2) return;
    const started = Date.parse(this.phase.enteredAt);
    const delay = Math.max(1000, started + cfg.afterMs - this.now(), this.nextAttemptAt - this.now(),
      this.lastPlayedAt + cfg.cooldownMs - this.now());
    this.timer = setTimeout(() => { this.timer = undefined; void this.select(); }, delay);
    this.timer.unref?.();
  }

  private async select(): Promise<void> {
    if (!this.eligible()) { this.schedule(); return; }
    this.recent = this.recent.filter(row => this.now() - row.atMs < Math.max(300_000, this.d.config().cooldownMs));
    const candidates = this.assets.filter(asset => !this.used.has(asset.id) && !this.recent.some(row => row.id === asset.id));
    if (!candidates.length) { this.nextAttemptAt = this.now() + this.d.config().intervalMs; this.schedule(); return; }
    const cfg = this.d.config();
    const configKey = JSON.stringify(cfg);
    const key = this.requestKey;
    const assetKey = this.key;
    const controller = new AbortController();
    this.selecting = controller; this.attempts++;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false;
    let onAbort: (() => void) | undefined;
    const began = this.now();
    const criteria = Object.fromEntries(candidates.map(asset => [asset.id,
      `${asset.kind === 'speech' ? '播放预制短音频' : '播放已准备歌曲'}：${asset.title}；${Math.round(asset.durationMs / 1000)}秒`]));
    try {
      const request = this.fetch(cfg.endpoint, { method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          state: { modelPendingMs: this.now() - Date.parse(this.phase!.enteredAt),
            audio: '演出流有消费者，音频队列空闲；模型请求仍未返回', recentMedia: this.recent.map(row => row.id),
            boundary: '只能选择已准备的媒体缓解等待；不表示游戏进展、任务完成或即将得到答案。主请求、游戏身体任务和观众点歌都保持原状。' },
          questions: { media: { type: 'choice', instructions: '判断是否适合播放一段已有媒体陪伴观众。不确定或没有合适内容选 quiet；不要输出解释。',
            criteria: { quiet: '继续安静等待，不播放任何内容', ...criteria } } },
        }) }).then(response => response.ok ? response.json() : null);
      const raw = await Promise.race([request, new Promise<null>(resolve => {
        timeout = setTimeout(() => { timedOut = true; controller.abort(); resolve(null); }, Math.max(1, Math.min(250, cfg.timeoutMs)));
      }), new Promise<null>(resolve => { onAbort = () => resolve(null); controller.signal.addEventListener('abort', onAbort, { once: true }); })]);
      if (timedOut) { this.d.report({ status: 'timeout', requestKey: key, elapsedMs: this.now() - began }); return; }
      if (controller.signal.aborted || key !== this.requestKey || assetKey !== this.key
        || configKey !== JSON.stringify(this.d.config()) || !this.eligible(false)) return;
      const choice = parseMediaChoice(raw?.answers?.media, ['quiet', ...candidates.map(asset => asset.id)]);
      const asset = choice && choice.choiceProbability >= cfg.minConfidence
        ? candidates.find(asset => asset.id === choice.choice) : undefined;
      this.d.report({ status: asset ? 'selected' : 'quiet', elapsedMs: this.now() - began,
        requestKey: key, modelPendingMs: this.now() - Date.parse(this.phase!.enteredAt),
        choice: asset?.id ?? null, probability: choice?.choiceProbability ?? null });
      if (!asset) return;
      this.played++; this.used.add(asset.id); this.recent.push({ id: asset.id, atMs: this.now() });
      this.lastPlayedAt = this.now();
      let terminal = false;
      const playbackGeneration = ++this.playbackGeneration;
      const stop = asset.play(status => {
        this.d.report({ status, id: asset.id, kind: asset.kind, title: asset.title });
        if (status === 'ended' || status === 'failed' || status === 'stopped') {
          terminal = true;
          if (playbackGeneration === this.playbackGeneration) this.cancelPlayback = null;
        }
      });
      if (!terminal && playbackGeneration === this.playbackGeneration) this.cancelPlayback = stop;
      else if (!terminal) stop();
    } catch (error) {
      if (!controller.signal.aborted) this.d.report({ status: 'unavailable', error: String(error) });
    } finally {
      clearTimeout(timeout);
      if (onAbort) controller.signal.removeEventListener('abort', onAbort);
      if (this.selecting === controller) this.selecting = null;
      if (key === this.requestKey) this.nextAttemptAt = this.now() + cfg.intervalMs;
      this.schedule();
    }
  }
}
