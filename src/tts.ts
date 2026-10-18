/**
 * TTS 流水线:VoxCPM2(OpenAI 兼容 /v1/audio/speech)客户端 + wav 解析 +
 * 振幅包络提取(lipsync 数据源)。
 *
 * 声线与生成参数(OpenAI 兼容请求体扩展字段):
 * - reference_audio(base64 wav)+ prompt_text:有转写=续写克隆(Hi-Fi),无转写=纯克隆
 *
 * Hi-Fi 续写模式不支持文字语气控制；prompt_text 必须保留参考音频的真实逐字稿。
 * 文字语气通过独立 instructions 字段传递，先核验服务端明确支持；
 * 适配器使用同一参考音频的 reference 模式，在解码器内部附加描述。
 * 客户端不向正文或参考逐字稿注入前缀，字幕与对齐始终只使用正文。
 * - seed / cfg_value / inference_timesteps / max_steps / temperature
 */

import type { AlignedUnit } from './align.ts';
import type { TtsSpeechPreferences } from './tts-speech.ts';
import { SilenceScanner, scanSilence, type SilenceReport } from './silence-scan.ts';
import { parseVoiceCatalog, receiveSpeech, requestMetadata, ttsHeaders, TTS_TRANSPORT_DEFAULTS, type TtsTransportConfig, type TtsRequestTrace, type TtsRemoteVoice, type TtsReceivedAudio } from './tts-transport.ts';

/** 整段包络与流式增量包络共用的只读接口。 */
export interface EnvelopeLike {
  readonly hopMs: number;
  at(ms: number): number;
}

export interface TtsPiece {
  requestTrace?: TtsRequestTrace;
  text: string;
  /** 原始 wav 字节(经 device-audio 写本机声卡) */
  wav: Uint8Array;
  durationMs: number;
  envelope: EnvelopeLike;
  /** 片内逐单元起止时间;未开启标注时缺省 */
  units?: AlignedUnit[];
  /** 流式合成被中途掐断(撞时长宽容界,或撞超大静默段):已收部分照常可用,server 端已停止解码 */
  truncated?: boolean;
  /**
   * 这一片音频里最长的连续近静默段(见 silence-scan.ts)。两条合成路都带,
   * `triggered` 为真即「错误生产」的签名成立——**破坏性动作只认它**:
   * 流式在静默段起点掐流掐尾、非流式据此重合成一次。
   */
  silence?: SilenceReport;
  /**
   * 流式对齐判废时不采信 units，附带原因和 lastGoodEndMs。判废本身不触发破坏性动作；字幕与锚点回落估计，lastGoodEndMs 供达到 server 硬时长上限的末级兜底估计切点。
   */
  alignBad?: { reasons: string[]; lastGoodEndMs: number | null };
}

/** 逐 hop 的响度包络,值域 [0,1] */
export class Envelope {
  constructor(
    private readonly values: Float32Array,
    readonly hopMs: number,
  ) {}

  at(ms: number): number {
    if (this.values.length === 0) return 0;
    const idx = ms / this.hopMs;
    if (idx <= 0) return this.values[0];
    const hi = Math.ceil(idx);
    if (hi >= this.values.length) return 0;
    const lo = Math.floor(idx);
    const frac = idx - lo;
    return this.values[lo] * (1 - frac) + this.values[hi] * frac;
  }
}

/** 解码后的单声道样本(与容器无关的那部分) */
export interface PcmAudio {
  samples: Float32Array;
  sampleRate: number;
  durationMs: number;
}

export interface DecodedWav extends PcmAudio {
  /** 源文件的编码事实:要不要规范成 PCM16 再发由它决定(服务端只按 16bit 读) */
  format: number;
  bitsPerSample: number;
  channels: number;
  /** 容器是 WAVE_FORMAT_EXTENSIBLE:子格式即便写着 PCM16,服务端也只认 0x0001 那种老容器 */
  extensible: boolean;
}

/** RIFF/WAVE 解析:PCM16 / PCM24 / PCM32f,多声道并为单声道;EXTENSIBLE 容器按它的子格式走 */
export function decodeWav(bytes: Uint8Array): DecodedWav {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < 44 || view.getUint32(0, false) !== 0x52494646 || view.getUint32(8, false) !== 0x57415645) {
    throw new Error('不是 RIFF/WAVE 数据');
  }
  let offset = 12;
  let format = 0;
  let channels = 1;
  let sampleRate = 0;
  let bitsPerSample = 0;
  let extensible = false;
  let dataStart = -1;
  let dataLen = 0;
  while (offset + 8 <= bytes.length) {
    const chunkId = view.getUint32(offset, false);
    const chunkLen = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (chunkId === 0x666d7420) {
      // 'fmt '
      format = view.getUint16(body, true);
      channels = view.getUint16(body + 2, true);
      sampleRate = view.getUint32(body + 4, true);
      bitsPerSample = view.getUint16(body + 14, true);
      // WAVE_FORMAT_EXTENSIBLE:真正的编码在 cbSize 之后的 SubFormat GUID 头两个字节
      // (ffmpeg 写 24bit 或三声道以上时会给这个容器;扩展块不完整就按原样落到"不支持")
      if (format === 0xfffe && chunkLen >= 40) {
        extensible = true;
        format = view.getUint16(body + 24, true);
      }
    } else if (chunkId === 0x64617461) {
      // 'data'
      dataStart = body;
      dataLen = Math.min(chunkLen, bytes.length - body);
      break;
    }
    offset = body + chunkLen + (chunkLen % 2);
  }
  if (dataStart < 0 || sampleRate === 0) throw new Error('wav 缺 fmt/data 块');
  let frames: number;
  let read: (frame: number, ch: number) => number;
  if (format === 1 && bitsPerSample === 16) {
    frames = Math.floor(dataLen / 2 / channels);
    read = (f, c) => view.getInt16(dataStart + (f * channels + c) * 2, true) / 32768;
  } else if (format === 1 && bitsPerSample === 24) {
    frames = Math.floor(dataLen / 3 / channels);
    read = (f, c) => {
      const at = dataStart + (f * channels + c) * 3;
      // 高字节按有符号读,负数自然带出符号位
      return (view.getUint8(at) | (view.getUint8(at + 1) << 8) | (view.getInt8(at + 2) << 16)) / 8388608;
    };
  } else if (format === 3 && bitsPerSample === 32) {
    frames = Math.floor(dataLen / 4 / channels);
    read = (f, c) => view.getFloat32(dataStart + (f * channels + c) * 4, true);
  } else {
    throw new Error(`不支持的 wav 格式: format=${format} bits=${bitsPerSample}`);
  }
  const samples = new Float32Array(frames);
  for (let f = 0; f < frames; f++) {
    let acc = 0;
    for (let c = 0; c < channels; c++) acc += read(f, c);
    samples[f] = acc / channels;
  }
  return { samples, sampleRate, durationMs: (frames / sampleRate) * 1000, format, bitsPerSample, channels, extensible };
}

const ENVELOPE_HOP_MS = 20;

/**
 * Incremental 20 ms RMS envelope with the same p95 normalization and attack/release as
 * `extractEnvelope`. Values converge to batch output as samples arrive.
 */
export class StreamingEnvelope {
  readonly hopMs = ENVELOPE_HOP_MS;
  private readonly hop: number;
  private readonly rms: number[] = [];
  private carry: number[] = [];
  private smoothed: Float32Array = new Float32Array(0);
  private dirty = false;

  constructor(sampleRate: number) {
    this.hop = Math.max(1, Math.round((sampleRate * ENVELOPE_HOP_MS) / 1000));
  }

  append(samples: Float32Array): void {
    for (const s of samples) {
      this.carry.push(s);
      if (this.carry.length === this.hop) {
        let acc = 0;
        for (const v of this.carry) acc += v * v;
        this.rms.push(Math.sqrt(acc / this.hop));
        this.carry = [];
      }
    }
    this.dirty = true;
  }

  /** 收流时计入不足一个 hop 的末段。 */
  finish(): void {
    if (this.carry.length > 0) {
      let acc = 0;
      for (const v of this.carry) acc += v * v;
      this.rms.push(Math.sqrt(acc / this.carry.length));
      this.carry = [];
    }
    this.dirty = true;
  }

  /** 已覆盖到的时长(ms) */
  coveredMs(): number {
    return this.rms.length * ENVELOPE_HOP_MS;
  }

  at(ms: number): number {
    if (this.dirty) this.recompute();
    const values = this.smoothed;
    if (values.length === 0) return 0;
    const idx = ms / ENVELOPE_HOP_MS;
    if (idx <= 0) return values[0];
    const hi = Math.ceil(idx);
    if (hi >= values.length) return 0;
    const lo = Math.floor(idx);
    const frac = idx - lo;
    return values[lo] * (1 - frac) + values[hi] * frac;
  }

  private recompute(): void {
    this.dirty = false;
    const n = this.rms.length;
    const sorted = [...this.rms].sort((a, b) => a - b);
    const p95 = sorted[Math.min(n - 1, Math.floor(n * 0.95))] || 1;
    const out = new Float32Array(n);
    let level = 0;
    for (let i = 0; i < n; i++) {
      const target = Math.min(1, this.rms[i] / p95);
      const k = target > level ? 0.5 : 0.15;
      level += (target - level) * k;
      out[i] = level;
    }
    this.smoothed = out;
  }
}

/** RMS 包络:20ms hop,p95 归一,快攻慢放平滑(嘴形不抖) */
export function extractEnvelope(wav: PcmAudio): Envelope {
  const hop = Math.max(1, Math.round((wav.sampleRate * ENVELOPE_HOP_MS) / 1000));
  const n = Math.ceil(wav.samples.length / hop);
  const rms = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const start = i * hop;
    const end = Math.min(start + hop, wav.samples.length);
    let acc = 0;
    for (let j = start; j < end; j++) acc += wav.samples[j] * wav.samples[j];
    rms[i] = Math.sqrt(acc / Math.max(1, end - start));
  }
  const sorted = [...rms].sort((a, b) => a - b);
  const p95 = sorted[Math.min(n - 1, Math.floor(n * 0.95))] || 1;
  const out = new Float32Array(n);
  let level = 0;
  for (let i = 0; i < n; i++) {
    const target = Math.min(1, rms[i] / p95);
    // 攻 ~40ms、放 ~120ms(以 hop 为步长的一阶滤波)
    const k = target > level ? 0.5 : 0.15;
    level += (target - level) * k;
    out[i] = level;
  }
  return new Envelope(out, ENVELOPE_HOP_MS);
}

/** 单次合成携带的声线与生成参数;undefined 字段不进请求体(用 server 默认) */
export interface TtsSynthProfile {
  speech?: TtsSpeechPreferences;
  /** 后端明确支持时才传入；独立元数据不拼进 input 或参考音频转写。 */
  instructions?: string;
  referenceAudioB64?: string;
  /** 参考音频的转写;与 referenceAudioB64 同给才有意义 */
  refText?: string;
  seed?: number;
  cfgValue?: number;
  inferenceTimesteps?: number;
  maxSteps?: number;
  temperature?: number;
}

export interface TtsClientOptions {
  url: string;
  transport?: Partial<TtsTransportConfig>;
  apiKey?: string;
  trace?: (trace: TtsRequestTrace) => void;
  /** Optional local incident recorder; never called with credentials or request headers. */
  capture?: (audio: TtsReceivedAudio) => void;
  /** 单片合成超时;VoxCPM2 本机 CUDA 合成短句通常秒级 */
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
  /** 每次合成时取当前声线档案;undefined=裸调用 */
  profile?: () => TtsSynthProfile;
}

/** 流式合成的接收端 */
export interface TtsStreamSink {
  abort?(): void;
  /** 头解析完成,采样率已知;envelope 是增量包络,lipsync/重音扫描边播边读 */
  begin?(info: { sampleRate: number; envelope: StreamingEnvelope }): void;
  /** 一块 PCM16LE 字节(偶数长度),到达即转发舞台页 */
  pcm(chunk: Uint8Array): void;
}

/** 服务端明确跳过重复文本；本次没有合成音频。 */
export class TtsSkipped extends Error {
  readonly reason = 'duplicate';
  constructor() { super('语音服务已跳过重复文本'); this.name = 'TtsSkipped'; }
}

/** Preserve the HTTP failure so callers can distinguish capability fallback from a failed job. */
export class TtsHttpError extends Error {
  constructor(readonly status: number, detail: string, streaming = false) {
    super(`TTS${streaming ? ' stream' : ''} ${status}: ${detail.slice(0, 200)}`);
    this.name = 'TtsHttpError';
  }
}

function ttsResponseError(status: number, detail: string, streaming = false): Error {
  if (status === 410) {
    try {
      if (JSON.parse(detail)?.error === 'dedup') return new TtsSkipped();
    } catch { /* 非协议 JSON 保留为实际错误。 */ }
  }
  return new TtsHttpError(status, detail, streaming);
}

export class TtsClient {
  readonly transport: TtsTransportConfig;
  private readonly apiKey: string;
  private readonly requestTrace?: (trace: TtsRequestTrace) => void;
  private readonly capture?: (audio: TtsReceivedAudio) => void;
  private readonly url: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly profile?: () => TtsSynthProfile;
  private styleCapability: { at: number; supported: boolean } | null = null;
  private remoteCatalog: { at: number; voices: TtsRemoteVoice[] } | null = null;
  private catalogPending: Promise<TtsRemoteVoice[]> | null = null;

  constructor(opts: TtsClientOptions) {
    this.transport = { ...TTS_TRANSPORT_DEFAULTS, ...opts.transport };
    this.apiKey = opts.apiKey ?? '';
    this.requestTrace = opts.trace;
    this.capture = opts.capture;
    this.url = opts.url.replace(/\/$/, '');
    this.timeoutMs = opts.timeoutMs ?? 60_000;
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.profile = opts.profile;
  }

  get remoteVoice(): string {
    const speech = this.profile?.()?.speech;
    return speech?.enabled && speech.voice ? speech.voice : this.transport.voice;
  }

  async listRemoteVoices(): Promise<TtsRemoteVoice[]> {
    if (this.transport.protocol !== 'openai-chunked') return [];
    if (this.remoteCatalog && Date.now() - this.remoteCatalog.at < 30_000) return this.remoteCatalog.voices;
    if (this.catalogPending) return this.catalogPending;
    this.catalogPending = (async () => {
      let voices: TtsRemoteVoice[] = [];
      try {
        const r = await this.fetchImpl(`${this.url}/v1/audio/voices`, {
          headers: ttsHeaders(this.apiKey, this.transport.sessionId), signal: AbortSignal.timeout(2000),
        });
        if (r.ok) voices = parseVoiceCatalog(await r.json());
        else await r.body?.cancel();
      } catch { /* Older endpoints can synthesize without exposing a catalog. */ }
      this.remoteCatalog = { at: Date.now(), voices };
      return voices;
    })().finally(() => { this.catalogPending = null; });
    return this.catalogPending;
  }

  private async synthRemote(text: string, sink: TtsStreamSink, signal?: AbortSignal, override?: TtsSynthProfile): Promise<TtsPiece> {
    // Snapshot once: a saved selection affects the next request, never an active stream.
    const profile = override ?? this.profile?.();
    const speech = profile?.speech;
    const config = { ...this.transport, voice: speech?.enabled && speech.voice ? speech.voice : this.transport.voice };
    let envelope: StreamingEnvelope | undefined;
    const received = await receiveSpeech({ url: this.url, config, apiKey: this.apiKey,
      text, signal, instructions: profile?.instructions, fetchImpl: this.fetchImpl, abort: sink.abort,
      capture: this.capture,
      trace: trace => {
        this.requestTrace?.(trace);
        if (this.requestTrace && ['received', 'aborted', 'error'].includes(trace.phase)
          && (trace.requestId || trace.callId)) void this.recordRemoteMetadata(trace);
      },
      begin: sampleRate => {
        envelope = new StreamingEnvelope(sampleRate);
        sink.begin?.({ sampleRate, envelope });
      },
      pcm: chunk => {
        const view = new DataView(chunk.buffer, chunk.byteOffset, chunk.byteLength);
        const samples = new Float32Array(chunk.length / 2);
        for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
        envelope!.append(samples);
        sink.pcm(chunk);
      },
    });
    envelope!.finish();
    return { text, wav: pcm16ToWav(received.parts, received.sampleRate), envelope: envelope!,
      durationMs: received.trace.pcmBytes / 2 / received.sampleRate * 1000, requestTrace: received.trace };
  }

  private async recordRemoteMetadata(trace: TtsRequestTrace): Promise<void> {
    try {
      // The gateway records its terminal outcome after the response closes.
      await new Promise(resolve => setTimeout(resolve, 250));
      const response = await this.fetchImpl(`${this.url}${this.transport.healthPath}`, {
        headers: ttsHeaders(this.apiKey, this.transport.sessionId), signal: AbortSignal.timeout(2000),
      });
      if (!response.ok) { await response.body?.cancel(); return; }
      const status = await response.json() as Record<string, unknown>;
      this.requestTrace?.({ ...trace, phase: 'metadata', server: requestMetadata(status, trace) });
    } catch { /* Request transport outcome remains available when metrics cannot be read. */ }
  }

  /** 旧服务可能忽略未知请求字段；有文字语气时先核对明确的能力回执。 */
  private async requireStyleCapability(instructions: string | undefined, signal?: AbortSignal): Promise<void> {
    if (!instructions) return;
    if (this.styleCapability === null || Date.now() - this.styleCapability.at > 30_000) {
      const timeout = AbortSignal.timeout(1500);
      const res = await this.fetchImpl(`${this.url}/health`, {
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      });
      if (!res.ok) throw new TtsHttpError(res.status, '无法核验文字语气控制能力');
      const health = await res.json() as { style_instructions_supported?: boolean };
      this.styleCapability = { at: Date.now(), supported: health.style_instructions_supported === true };
    }
    if (!this.styleCapability.supported) throw new TtsHttpError(422, '当前语音服务未启用文字语气控制');
  }

  /** override 给控制台试听用:按面板上的档案合成一次,不动当前生效的那份 */
  async synth(text: string, override?: TtsSynthProfile, signal?: AbortSignal): Promise<TtsPiece> {
    if (this.transport.protocol === 'openai-chunked') return this.synthRemote(text, { pcm: () => {} }, signal, override);
    const p = override ?? this.profile?.() ?? {};
    await this.requireStyleCapability(p.instructions, signal);
    const body: Record<string, unknown> = {
      model: 'voxcpm2',
      input: text,
      voice: 'default',
      response_format: 'wav',
    };
    if (p.instructions) body.instructions = p.instructions;
    if (p.seed !== undefined) body.seed = p.seed;
    if (p.cfgValue !== undefined) body.cfg_value = p.cfgValue;
    if (p.inferenceTimesteps !== undefined) body.inference_timesteps = p.inferenceTimesteps;
    if (p.maxSteps !== undefined) body.max_steps = p.maxSteps;
    if (p.temperature !== undefined) body.temperature = p.temperature;
    if (p.speech) {
      if (p.speech.voice.trim()) body.voice = p.speech.voice.trim();
      body.speed = p.speech.speed;
      body.emotion_mix = p.speech.emotionMix;
      body.emotion_min_confidence = p.speech.emotionMinConfidence;
    }
    if (p.referenceAudioB64) {
      body.reference_audio = p.referenceAudioB64;
      if (p.refText) body.prompt_text = p.refText;
    }
    const ac = new AbortController();
    const onAbort = (): void => ac.abort(signal?.reason);
    if (signal?.aborted) onAbort();
    else signal?.addEventListener('abort', onAbort, { once: true });
    const timer = setTimeout(() => ac.abort(), this.timeoutMs);
    try {
      const res = await this.fetchImpl(`${this.url}/v1/audio/speech`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: ac.signal,
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw ttsResponseError(res.status, detail);
      }
      const wav = new Uint8Array(await res.arrayBuffer());
      const decoded = decodeWav(wav);
      return {
        text,
        wav,
        durationMs: decoded.durationMs,
        envelope: extractEnvelope(decoded),
        // 非流式没有流可掐,只把事实测出来交给上层:超大静默段是唯一的重合成判据
        silence: scanSilence(decoded.samples, decoded.sampleRate),
      };
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
    }
  }

  /**
   * 流式合成:/v1/audio/speech/stream 的 chunked wav,边收边把 PCM16 交给 sink。
   * 返回的 Promise 在收流后 resolve 成完整 TtsPiece(wav 重组、时长、包络与
   * sink.begin 给出的是同一实例)。signal 中止 = 硬打断:server 端解码环随
   * 连接断开停在当前步。
   */
  async synthStream(
    text: string,
    sink: TtsStreamSink,
    opts: { override?: TtsSynthProfile; signal?: AbortSignal; maxDurationMs?: number; segmentText?: boolean } = {},
  ): Promise<TtsPiece> {
    if (this.transport.protocol === 'openai-chunked') return this.synthRemote(text, sink, opts.signal, opts.override);
    const p = opts.override ?? this.profile?.() ?? {};
    await this.requireStyleCapability(p.instructions, opts.signal);
    const body: Record<string, unknown> = {
      model: 'voxcpm2',
      input: text,
      voice: 'default',
      response_format: 'wav',
      segment_text: opts.segmentText ?? true,
    };
    if (p.instructions) body.instructions = p.instructions;
    if (p.seed !== undefined) body.seed = p.seed;
    if (p.cfgValue !== undefined) body.cfg_value = p.cfgValue;
    if (p.inferenceTimesteps !== undefined) body.inference_timesteps = p.inferenceTimesteps;
    if (p.maxSteps !== undefined) body.max_steps = p.maxSteps;
    if (p.temperature !== undefined) body.temperature = p.temperature;
    if (p.speech) {
      if (p.speech.voice.trim()) body.voice = p.speech.voice.trim();
      body.speed = p.speech.speed;
      body.emotion_mix = p.speech.emotionMix;
      body.emotion_min_confidence = p.speech.emotionMinConfidence;
    }
    if (p.referenceAudioB64) {
      body.reference_audio = p.referenceAudioB64;
      if (p.refText) body.prompt_text = p.refText;
    }
    const timeout = AbortSignal.timeout(this.timeoutMs);
    const signal = opts.signal ? AbortSignal.any([opts.signal, timeout]) : timeout;
    const res = await this.fetchImpl(`${this.url}/v1/audio/speech/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok || !res.body) {
      const detail = await res.text().catch(() => '');
      throw ttsResponseError(res.status, detail, true);
    }
    const reader = res.body.getReader();
    let header = new Uint8Array(0);
    let sampleRate = 0;
    let envelope: StreamingEnvelope | null = null;
    let silenceScan: SilenceScanner | null = null;
    const pcmParts: Uint8Array[] = [];
    let pcmBytes = 0;
    let truncated = false;
    /** 跨 chunk 的奇数字节残留(PCM16 必须偶数对齐) */
    let oddCarry: Uint8Array | null = null;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        let buf = value as Uint8Array;
        if (envelope === null) {
          // 先凑满 44 字节 wav 头(流式头的长度字段是占位值,只取采样率)
          const merged = new Uint8Array(header.length + buf.length);
          merged.set(header);
          merged.set(buf, header.length);
          if (merged.length < 44) {
            header = merged;
            continue;
          }
          const view = new DataView(merged.buffer, merged.byteOffset);
          sampleRate = view.getUint32(24, true);
          if (!(sampleRate > 0)) throw new Error('流式 wav 头无采样率');
          envelope = new StreamingEnvelope(sampleRate);
          silenceScan = new SilenceScanner(sampleRate);
          sink.begin?.({ sampleRate, envelope });
          buf = merged.subarray(44);
          if (buf.length === 0) continue;
        }
        if (oddCarry) {
          const merged = new Uint8Array(oddCarry.length + buf.length);
          merged.set(oddCarry);
          merged.set(buf, oddCarry.length);
          buf = merged;
          oddCarry = null;
        }
        if (buf.length % 2 === 1) {
          oddCarry = buf.slice(buf.length - 1);
          buf = buf.subarray(0, buf.length - 1);
        }
        if (buf.length === 0) continue;
        const chunk = buf.slice();
        pcmParts.push(chunk);
        pcmBytes += chunk.length;
        const view = new DataView(chunk.buffer, chunk.byteOffset);
        const floats = new Float32Array(chunk.length / 2);
        for (let i = 0; i < floats.length; i++) floats[i] = view.getInt16(2 * i, true) / 32768;
        envelope.append(floats);
        silenceScan?.append(floats);
        sink.pcm(chunk);
        /*
         * 掐流的两道判据,先后有别:
         *
         * 1) 超大静默段——这才是「错误生产」的签名(解码环没停在句尾,后面接一段
         *    谁都不该念的死气)。它先判,因为它说得出**掐在哪**(静默段起点),
         *    而时长界只说得出「太长了」。
         * 2) 时长宽容界——兜底。合成得怪但一直在出声的片(笑腔、拖音)是节目效果,
         *    界因此放得很宽(见 orchestrator.ts OVERRUN_FORGIVE_*),正常片够不着。
         *
         * 两条都是取消读流:对 server 是正常收场(解码环停在当前步,GPU 当场释放),
         * 已收的部分照常交回。
         */
        const sil = silenceScan?.check();
        if (sil) {
          truncated = true;
          await reader.cancel().catch(() => {});
          break;
        }
        if (opts.maxDurationMs !== undefined && (pcmBytes / 2 / sampleRate) * 1000 >= opts.maxDurationMs) {
          truncated = true;
          await reader.cancel().catch(() => {});
          break;
        }
      }
    } finally {
      // 解析或接收端异常也关闭响应体,让服务端停止尚未生成的音频。
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
    if (envelope === null || pcmBytes === 0) throw new Error('TTS 流式合成没有产出音频');
    envelope.finish();
    silenceScan?.finish();
    // 重组带真实长度的 wav(对齐器与非流式回退都吃它)
    const wav = pcm16ToWav(pcmParts, sampleRate);
    const durationMs = (pcmBytes / 2 / sampleRate) * 1000;
    return {
      text,
      wav,
      durationMs,
      envelope,
      // 收流后按全部样本再算一次:节流检查里那次只是「够不够掐」,这份是完整事实
      ...(silenceScan ? { silence: silenceScan.report() } : {}),
      ...(truncated ? { truncated } : {}),
    };
  }
}

/** 浮点样本 → 单声道 PCM16 wav:声线库的规范形,与 ffmpeg 转码产物同规格(采样率沿用源文件,服务端自己重采样) */
export function samplesToPcm16Wav(samples: Float32Array, sampleRate: number): Uint8Array {
  const pcm = new Uint8Array(samples.length * 2);
  const view = new DataView(pcm.buffer);
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, Math.round(v * 32767), true);
  }
  return pcm16ToWav([pcm], sampleRate);
}

/** PCM16LE 单声道分块 → 完整 wav 字节(流式重组与前缀对齐都用) */
export function pcm16ToWav(parts: readonly Uint8Array[], sampleRate: number): Uint8Array {
  let pcmBytes = 0;
  for (const p of parts) pcmBytes += p.length;
  const wav = new Uint8Array(44 + pcmBytes);
  const view = new DataView(wav.buffer);
  const ascii = (at: number, s: string): void => {
    for (let i = 0; i < s.length; i++) wav[at + i] = s.charCodeAt(i);
  };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + pcmBytes, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, 'data');
  view.setUint32(40, pcmBytes, true);
  let at = 44;
  for (const part of parts) {
    wav.set(part, at);
    at += part.length;
  }
  return wav;
}
