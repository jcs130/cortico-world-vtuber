/** VTuber 的语音服务生命周期。只关闭自己启动的适配器；共享模型由配置的端点提供。 */
import { spawn, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { dirname } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import type { Logger } from 'cortico/core/types.ts';
import { TtsServerManager, type TtsServerOptions, type TtsServerPhase, type TtsServerResources } from './tts-server.ts';

export interface TtsServiceConfig {
  kind: 'voxcpm2' | 'indextts' | 'external';
  autoStart: boolean;
  pythonFile: string;
  upstreamUrl: string;
  preferencesFile: string;
  pronunciationModelDir: string;
  pronunciationTokenizerDir: string;
  decisionUrl: string;
  startupTimeoutMs: number;
  healthIntervalMs: number;
  restartDelayMs: number;
  maxRestarts: number;
}

export const TTS_SERVICE_DEFAULTS: TtsServiceConfig = {
  kind: 'voxcpm2', autoStart: false, pythonFile: 'python',
  upstreamUrl: 'http://127.0.0.1:8087', preferencesFile: '',
  pronunciationModelDir: '', pronunciationTokenizerDir: '',
  decisionUrl: 'http://127.0.0.1:8090/v1/systemone',
  startupTimeoutMs: 60_000, healthIntervalMs: 5000, restartDelayMs: 2000, maxRestarts: 3,
};

export interface TtsServiceState {
  kind: TtsServiceConfig['kind'];
  phase: TtsServerPhase;
  url: string;
  pid: number | null;
  ownership: 'owned' | 'external' | 'none';
  detail: string | null;
  restarts: number;
  health: Record<string, unknown> | null;
  resources: Partial<TtsServerResources> & {
    ready: boolean;
    files: Array<{ label: string; path: string; ready: boolean }>;
  };
}

export interface TtsServiceOptions {
  url: string;
  config?: Partial<TtsServiceConfig>;
  log: Logger;
  voxcpm2: TtsServerOptions;
  /** 本地进程夹具使用同一健康协议与 stdin 生命周期。 */
  launchOverride?: { command: string; args: string[] };
  fetchImpl?: typeof fetch;
}

type HealthResult = { kind: 'missing' } | { kind: 'occupied'; detail: string }
  | { kind: 'ready'; health: Record<string, unknown> };

export class TtsService {
  private readonly config: TtsServiceConfig;
  private readonly legacy: TtsServerManager;
  private readonly fetchImpl: typeof fetch;
  private readonly adapterFile = fileURLToPath(new URL('../adapters/indextts/indextts_adapter.py', import.meta.url));
  private proc: ChildProcess | null = null;
  private phase: TtsServerPhase = 'stopped';
  private detail: string | null = null;
  private health: Record<string, unknown> | null = null;
  private token = '';
  private generation = 0;
  private desired = false;
  private restarts = 0;
  private failures = 0;
  private starting: Promise<TtsServiceState> | null = null;
  private stopping: Promise<TtsServiceState> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private voiceCatalog: { at: number; voices: string[] } | null = null;

  private beginLaunch(generation: number): Promise<TtsServiceState> {
    const pending = this.launch(generation).finally(() => {
      if (this.starting === pending) this.starting = null;
    });
    this.starting = pending;
    return pending;
  }

  constructor(private readonly opts: TtsServiceOptions) {
    this.config = { ...TTS_SERVICE_DEFAULTS, ...opts.config };
    this.legacy = new TtsServerManager(opts.voxcpm2);
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  state(): TtsServiceState {
    if (this.config.kind === 'voxcpm2') {
      const state = this.legacy.state();
      const r = state.resources;
      return { ...state, kind: 'voxcpm2', ownership: state.pid ? 'owned' : 'none', restarts: 0, health: null,
        resources: { ...r, files: [
          { label: '运行时', ...r.server }, { label: 'BaseLM', ...r.baseLm }, { label: 'Acoustic', ...r.acoustic },
          ...(r.alignerRequired ? [{ label: 'Aligner LM', ...r.alignerLm }, { label: 'Aligner Audio', ...r.alignerAudio }] : []),
        ] } };
    }
    const files = this.config.kind === 'indextts'
      ? [{ label: '适配器', path: this.adapterFile, ready: existsSync(this.adapterFile) },
        ...([
          ['声线偏好', this.config.preferencesFile], ['读音模型', this.config.pronunciationModelDir],
          ['读音分词器', this.config.pronunciationTokenizerDir],
        ] as const).filter(([, path]) => path).map(([label, path]) => ({ label, path, ready: existsSync(path) })),
        ...(this.config.pythonFile.includes('/') || this.config.pythonFile.includes('\\')
          ? [{ label: 'Python', path: this.config.pythonFile, ready: existsSync(this.config.pythonFile) }] : [])]
      : [];
    return { kind: this.config.kind, phase: this.phase, url: this.opts.url,
      pid: this.proc?.pid ?? null, ownership: this.proc ? 'owned' : this.health ? 'external' : 'none',
      detail: this.detail, restarts: this.restarts, health: this.health,
      resources: { ready: files.every(file => file.ready), files } };
  }

  async probe(): Promise<boolean> {
    if (this.config.kind === 'voxcpm2') return this.legacy.probe();
    const result = await this.readHealth();
    const ready = result.kind === 'ready' && (!this.proc || result.health.instance_id === this.token);
    if (ready) this.health = result.health;
    return ready;
  }

  async listVoices(): Promise<string[]> {
    if (this.config.kind !== 'indextts') return [];
    if (this.voiceCatalog && Date.now() - this.voiceCatalog.at < 60_000) return this.voiceCatalog.voices;
    try {
      const response = await this.fetchImpl(`${this.config.upstreamUrl.replace(/\/$/, '')}/voices`,
        { signal: AbortSignal.timeout(2000) });
      if (!response.ok) return [];
      const body = await response.json() as { voices?: unknown };
      if (!Array.isArray(body.voices)) return [];
      const voices = body.voices.filter((voice): voice is string => typeof voice === 'string' && !!voice.trim());
      this.voiceCatalog = { at: Date.now(), voices };
      return voices;
    } catch { return []; }
  }

  async start(): Promise<TtsServiceState> {
    if (this.config.kind === 'voxcpm2') { this.legacy.start(); return this.state(); }
    if (this.stopping) return this.stopping;
    if (this.starting) return this.starting;
    if (this.proc) return this.state();
    if (this.phase === 'running') return this.state();
    this.desired = true;
    this.restarts = 0;
    this.clearTimer();
    return this.beginLaunch(++this.generation);
  }

  async autoStart(): Promise<void> {
    if (this.config.autoStart) await this.start();
  }

  stop(): Promise<TtsServiceState> {
    if (this.config.kind === 'voxcpm2') return this.legacy.stop().then(() => this.state());
    if (this.stopping) return this.stopping;
    this.desired = false;
    this.generation++;
    this.clearTimer();
    this.phase = 'stopping';
    this.stopping = (async () => {
      await this.terminate();
      await this.starting;
      if (this.health?.instance_id === this.token) this.health = null;
      this.phase = 'stopped';
      this.detail = this.health ? '外部语音服务继续运行，未由本扩展启动。' : null;
      return this.state();
    })().finally(() => { this.stopping = null; });
    return this.stopping;
  }

  private async readHealth(): Promise<HealthResult> {
    try {
      const response = await this.fetchImpl(`${this.opts.url.replace(/\/$/, '')}/health`,
        { signal: AbortSignal.timeout(Math.min(2000, this.config.startupTimeoutMs)) });
      if (!response.ok) return { kind: 'occupied', detail: `语音端点健康检查返回 ${response.status}` };
      const body = await response.json() as Record<string, unknown>;
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        return { kind: 'occupied', detail: '语音端点未返回有效的健康对象。' };
      }
      if (this.config.kind === 'indextts' && !(body.status === 'ok' && typeof body.version === 'string'
        && body.streaming === true && String(body.backend).startsWith('indextts'))) {
        return { kind: 'occupied', detail: '端口已被其他服务占用，未启动或关闭该服务。' };
      }
      if (this.config.kind === 'indextts' && typeof body.upstream_url === 'string'
        && body.upstream_url.replace(/\/$/, '') !== this.config.upstreamUrl.replace(/\/$/, '')) {
        return { kind: 'occupied', detail: '现有适配器的模型网关与配置不一致，请先确认服务归属。' };
      }
      return { kind: 'ready', health: body };
    } catch (error) {
      // HTTP 可达但响应不合法时，不能把它当成空端口。
      if (error instanceof SyntaxError) return { kind: 'occupied', detail: '语音端点未返回有效的健康信息。' };
      return { kind: 'missing' };
    }
  }

  private async launch(generation: number): Promise<TtsServiceState> {
    this.phase = 'starting';
    this.detail = null;
    this.health = null;
    const result = await this.readHealth();
    if (generation !== this.generation) return this.state();
    if (result.kind === 'ready') {
      this.health = result.health;
      this.phase = 'running';
      this.detail = '已连接外部语音服务；停止扩展不会关闭它。';
      this.monitor(generation);
      return this.state();
    }
    if (result.kind === 'occupied' || this.config.kind === 'external') {
      this.fail(result.kind === 'occupied' ? result.detail : '外部语音服务不可达。');
      return this.state();
    }
    let url: URL;
    try { url = new URL(this.opts.url); }
    catch { this.fail('语音服务地址不是有效的 URL。'); return this.state(); }
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)
      || url.pathname !== '/' || url.search || url.hash || !url.port) {
      this.fail('托管 IndexTTS 适配器需要带端口的本机 HTTP 根地址。');
      return this.state();
    }
    const missing = this.state().resources.files.filter(file => !file.ready);
    if (missing.length) {
      this.fail(`缺少文件：${missing.map(file => file.path).join('、')}`);
      return this.state();
    }
    this.token = randomUUID();
    const cfg = this.config;
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUTF8: '1',
      CORTI_TTS_PORT: url.port, CORTICO_TTS_MANAGED_TOKEN: this.token,
      CORTICO_INDEXTTS_URL: cfg.upstreamUrl, CORTICO_DECISION_URL: cfg.decisionUrl,
      CORTICO_PRONUNCIATION_DECISION_URL: cfg.decisionUrl };
    for (const [key, value] of Object.entries({ CORTI_TTS_PREFS: cfg.preferencesFile,
      CORTICO_G2PW_MODEL_DIR: cfg.pronunciationModelDir, CORTICO_G2PW_TOKENIZER_DIR: cfg.pronunciationTokenizerDir })) {
      if (value) env[key] = value;
    }
    const launch = this.opts.launchOverride ?? { command: cfg.pythonFile, args: ['-X', 'utf8', '-u', this.adapterFile] };
    let child: ChildProcess;
    try {
      child = spawn(launch.command, launch.args, { cwd: dirname(this.adapterFile), env,
        stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    } catch (error) {
      this.fail(`语音适配器启动失败：${String(error)}`);
      return this.state();
    }
    this.proc = child;
    const log = this.opts.log.child('tts-adapter');
    for (const pipe of [child.stdout, child.stderr]) {
      createInterface({ input: pipe! }).on('line', line => { if (line.trim()) log.debug(line.trim()); });
    }
    let exited = false;
    let tail = '';
    child.stderr?.on('data', (chunk: Buffer) => { tail = (tail + chunk.toString()).slice(-800); });
    const onExit = (detail: string): void => {
      if (exited) return;
      exited = true;
      if (this.proc === child) this.proc = null;
      if (generation !== this.generation || !this.desired) return;
      this.clearTimer();
      this.fail(detail);
      this.retry(generation);
    };
    child.once('error', error => onExit(`语音适配器启动失败：${error.message}`));
    child.once('exit', code => onExit(`语音适配器退出 code=${code} ${tail}`));
    const deadline = Date.now() + cfg.startupTimeoutMs;
    while (!exited && generation === this.generation && Date.now() < deadline) {
      const check = await this.readHealth();
      if (generation !== this.generation || exited) break;
      if (check.kind === 'ready' && check.health.instance_id === this.token) {
        this.health = check.health;
        this.phase = 'running';
        this.failures = 0;
        this.opts.log.info('托管语音适配器就绪', { pid: child.pid, url: this.opts.url, restarts: this.restarts });
        this.monitor(generation);
        return this.state();
      }
      await new Promise(resolve => setTimeout(resolve, Math.min(200, cfg.healthIntervalMs)));
    }
    if (generation === this.generation && !exited) {
      // 先收掉本次子进程，退出回调负责有界重试。
      this.detail = '语音适配器健康检查超时。';
      await this.terminate();
    }
    return this.state();
  }

  private monitor(generation: number): void {
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = null;
      void (async () => {
        if (!this.desired || generation !== this.generation) return;
        const healthy = await this.probe();
        if (!this.desired || generation !== this.generation) return;
        if (healthy) this.failures = 0;
        else this.failures++;
        if (this.failures >= 3) {
          this.health = null;
          this.fail('语音适配器连续三次健康检查失败。');
          if (this.proc) await this.terminate();
          else if (this.config.kind === 'indextts') this.retry(generation);
          return;
        }
        this.monitor(generation);
      })();
    }, this.config.healthIntervalMs);
    this.timer.unref?.();
  }

  private retry(generation: number): void {
    if (!this.desired || generation !== this.generation || this.timer) return;
    if (this.restarts >= this.config.maxRestarts) {
      this.detail = `${this.detail ?? ''} 已达到自动恢复上限，请检查后手动启动。`;
      return;
    }
    const delay = this.config.restartDelayMs * 2 ** this.restarts++;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.desired || generation !== this.generation) return;
      void this.beginLaunch(++this.generation);
    }, delay);
    this.timer.unref?.();
  }

  private async terminate(): Promise<void> {
    const child = this.proc;
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    await new Promise<void>(resolve => {
      const done = (): void => { clearTimeout(force); resolve(); };
      const force = setTimeout(() => child.kill('SIGKILL'), 3000);
      child.once('exit', done);
      child.once('error', done);
      // 关闭 stdin 同时通知适配器结束；强退宿主时也通过管道 EOF 清理子进程。
      child.stdin?.end();
      child.kill();
    });
    if (this.proc === child) this.proc = null;
  }

  private fail(detail: string): void {
    this.phase = 'error';
    this.detail = detail;
    this.health = null;
    this.opts.log.warn(detail);
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}
