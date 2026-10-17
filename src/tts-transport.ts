/** HTTP chunked WAV transport. Credentials never enter public configuration or traces. */
export interface TtsTransportConfig {
  protocol: 'legacy' | 'openai-chunked';
  model: string;
  voice: string;
  speed: number;
  sessionId: string;
  prebufferMs: number;
  timeoutMs: number;
  healthPath: string;
}

export const TTS_TRANSPORT_DEFAULTS: TtsTransportConfig = {
  protocol: 'legacy', model: '', voice: '', speed: 1, sessionId: '',
  prebufferMs: 480, timeoutMs: 180_000, healthPath: '/metrics/tts',
};
export const TTS_API_KEY_SECRET = 'CORTICO_TTS_API_KEY';

/** Public server catalog. Reference files and credentials stay on the service host. */
export interface TtsRemoteVoice {
  id: string;
  label: string;
  description?: string;
  reference_sha256?: string;
  reference_seconds?: number | null;
}

export function parseVoiceCatalog(body: unknown): TtsRemoteVoice[] {
  if (!body || typeof body !== 'object') return [];
  const b = body as { catalog?: unknown; voices?: unknown };
  const rows = Array.isArray(b.catalog) ? b.catalog : Array.isArray(b.voices) ? b.voices : [];
  const seen = new Set<string>();
  return rows.flatMap(value => {
    const row = typeof value === 'string' ? { id: value, label: value } : value;
    if (!row || typeof row !== 'object') return [];
    const r = row as Record<string, unknown>;
    if (typeof r.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(r.id) || seen.has(r.id)) return [];
    seen.add(r.id);
    return [{ id: r.id, label: typeof r.label === 'string' ? r.label : r.id,
      ...(typeof r.description === 'string' ? { description: r.description } : {}),
      ...(typeof r.reference_sha256 === 'string' && /^[0-9a-f]{64}$/.test(r.reference_sha256)
        ? { reference_sha256: r.reference_sha256 } : {}),
      ...(typeof r.reference_seconds === 'number' && Number.isFinite(r.reference_seconds)
        ? { reference_seconds: r.reference_seconds } : {}) }];
  });
}

export interface TtsRequestTrace {
  phase: 'headers' | 'playable' | 'received' | 'aborted' | 'error' | 'metadata';
  callId: string | null;
  sessionId: string;
  requestId: string | null;
  elapsedMs: number;
  pcmBytes: number;
  status?: number;
  voice?: string;
  referenceSha256?: string | null;
  server?: Record<string, unknown> | null;
}

/** Local diagnostic data only. This is received audio, not proof of physical playback. */
export interface TtsReceivedAudio {
  text: string;
  parts: readonly Uint8Array[];
  sampleRate: number;
  trace: TtsRequestTrace;
}

/** Correlate by request IDs; a session's latest request may belong to another utterance. */
export function requestMetadata(status: Record<string, unknown>, trace: TtsRequestTrace): Record<string, unknown> | null {
  const rows = [status.gateway_last, ...(Array.isArray(status.gateway_recent) ? status.gateway_recent : []),
    ...(Array.isArray(status.gateway_active) ? status.gateway_active : [])];
  const ids = [trace.callId, trace.requestId].filter(Boolean);
  const row = rows.find(value => value && typeof value === 'object'
    && ['request_id', 'native_request_id'].some(key => ids.includes((value as Record<string, string>)[key])));
  if (!row) return null;
  const record = row as Record<string, unknown>;
  return Object.fromEntries(['request_id', 'native_request_id', 'session_id', 'model', 'http_status',
    'gateway_duration_ms', 'first_audio_ms', 'gateway_sent_audio_seconds', 'execution_ms', 'queue_ms',
    'gateway_outcome', 'voice', 'reference_sha256'].filter(key => key in record).map(key => [key, record[key]]));
}

/** Accepts split RIFF headers, optional chunks, and a streaming data-size placeholder. */
export class PcmWavStream {
  sampleRate = 0;
  private header = new Uint8Array(0);
  private carry = new Uint8Array(0);
  private remaining = Infinity;
  private inData = false;

  push(value: Uint8Array): Uint8Array {
    let bytes = value;
    if (!this.inData) {
      const merged = new Uint8Array(this.header.length + bytes.length);
      merged.set(this.header); merged.set(bytes, this.header.length);
      this.header = merged;
      if (merged.length < 12) return new Uint8Array(0);
      const ascii = (at: number) => String.fromCharCode(...merged.subarray(at, at + 4));
      if (ascii(0) !== 'RIFF' || ascii(8) !== 'WAVE') throw new Error('TTS response is not RIFF WAV');
      const view = new DataView(merged.buffer, merged.byteOffset, merged.byteLength);
      let at = 12;
      while (at + 8 <= merged.length) {
        const kind = ascii(at), size = view.getUint32(at + 4, true);
        if (kind === 'data') {
          if (!this.sampleRate) throw new Error('WAV data precedes PCM format');
          this.remaining = size === 0xffffffff || size === 0x7fffffff ? Infinity : size;
          this.inData = true;
          bytes = merged.subarray(at + 8);
          this.header = new Uint8Array(0);
          break;
        }
        if (size > 65_536 || at > 65_536) throw new Error('WAV header exceeds limit');
        if (at + 8 + size > merged.length) return new Uint8Array(0);
        if (kind === 'fmt ') {
          if (size < 16 || view.getUint16(at + 8, true) !== 1 || view.getUint16(at + 10, true) !== 1
            || view.getUint16(at + 22, true) !== 16 || view.getUint16(at + 20, true) !== 2) {
            throw new Error('TTS stream requires mono PCM16 WAV');
          }
          this.sampleRate = view.getUint32(at + 12, true);
          if (this.sampleRate !== 48_000) throw new Error('TTS stream requires 48000 Hz WAV');
        }
        at += 8 + size + (size % 2);
      }
      if (!this.inData) return new Uint8Array(0);
    }
    bytes = bytes.subarray(0, Math.min(bytes.length, this.remaining));
    this.remaining -= bytes.length;
    const merged = new Uint8Array(this.carry.length + bytes.length);
    merged.set(this.carry); merged.set(bytes, this.carry.length);
    const even = merged.length - merged.length % 2;
    this.carry = merged.slice(even);
    return merged.subarray(0, even);
  }

  finish(): void {
    if (!this.inData || this.carry.length || (Number.isFinite(this.remaining) && this.remaining !== 0)) {
      throw new Error('Truncated TTS WAV response');
    }
  }
}

export function ttsHeaders(apiKey: string, sessionId: string): Record<string, string> {
  return { 'Content-Type': 'application/json',
    ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    ...(sessionId ? { 'x-litellm-session-id': sessionId } : {}) };
}

export async function receiveSpeech(options: {
  url: string; config: TtsTransportConfig; apiKey: string; text: string;
  fetchImpl: typeof fetch; signal?: AbortSignal;
  begin: (sampleRate: number) => void; pcm: (chunk: Uint8Array) => void;
  abort?: () => void; trace?: (trace: TtsRequestTrace) => void;
  capture?: (audio: TtsReceivedAudio) => void;
}): Promise<{ parts: Uint8Array[]; sampleRate: number; trace: TtsRequestTrace }> {
  const { config } = options;
  if (!config.model || !config.voice || !config.sessionId) throw new Error('TTS model, voice and stable session ID are required');
  if (!Number.isFinite(config.prebufferMs) || config.prebufferMs < 0 || config.prebufferMs > 5000) throw new Error('Invalid TTS prebuffer');
  const controller = new AbortController();
  const onAbort = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) onAbort();
  else options.signal?.addEventListener('abort', onAbort, { once: true });
  const timer = setTimeout(() => controller.abort(new Error('TTS request timed out')), config.timeoutMs);
  const started = Date.now();
  const trace: TtsRequestTrace = { phase: 'headers', callId: null, requestId: null,
    sessionId: config.sessionId, voice: config.voice, elapsedMs: 0, pcmBytes: 0 };
  const emit = (phase: TtsRequestTrace['phase']) => {
    trace.phase = phase; trace.elapsedMs = Date.now() - started;
    options.trace?.({ ...trace });
  };
  const parts: Uint8Array[] = [], buffered: Uint8Array[] = [];
  const wav = new PcmWavStream();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let playing = false;
  const flush = () => {
    controller.signal.throwIfAborted();
    if (!playing) { options.begin(wav.sampleRate); playing = true; emit('playable'); }
    for (const chunk of buffered.splice(0)) { controller.signal.throwIfAborted(); options.pcm(chunk); }
  };
  try {
    controller.signal.throwIfAborted();
    const response = await options.fetchImpl(`${options.url.replace(/\/$/, '')}/v1/audio/speech`, {
      method: 'POST', headers: ttsHeaders(options.apiKey, config.sessionId),
      body: JSON.stringify({ model: config.model, input: options.text, voice: config.voice,
        speed: config.speed, response_format: 'wav' }), signal: controller.signal,
    });
    trace.status = response.status;
    trace.callId = response.headers.get('x-litellm-call-id');
    trace.requestId = response.headers.get('x-tts-request-id');
    trace.referenceSha256 = response.headers.get('x-tts-reference-sha256');
    trace.sessionId = response.headers.get('x-litellm-session-id') || config.sessionId;
    emit('headers');
    if (!response.ok || !response.body) {
      await response.body?.cancel();
      throw new Error(`TTS speech request returned HTTP ${response.status}`);
    }
    const actualVoice = response.headers.get('x-tts-voice');
    if (actualVoice && config.voice !== 'default' && actualVoice !== config.voice) {
      await response.body.cancel();
      throw new Error('TTS response voice differs from requested voice');
    }
    reader = response.body.getReader();
    // Cancels a blocked read as well as the underlying HTTP connection.
    const cancelRead = () => { void reader?.cancel().catch(() => {}); };
    controller.signal.addEventListener('abort', cancelRead, { once: true });
    try {
      for (;;) {
        controller.signal.throwIfAborted();
        const { done, value } = await reader.read();
        controller.signal.throwIfAborted();
        if (done) break;
        const pcm = wav.push(value);
        if (!pcm.length) continue;
        parts.push(pcm); trace.pcmBytes += pcm.length;
        if (playing) options.pcm(pcm);
        else {
          buffered.push(pcm);
          if (trace.pcmBytes >= wav.sampleRate * 2 * config.prebufferMs / 1000) flush();
        }
      }
    } finally { controller.signal.removeEventListener('abort', cancelRead); }
    wav.finish();
    if (!trace.pcmBytes) throw new Error('TTS produced no audio');
    if (!playing) flush(); // A valid utterance shorter than the prebuffer still plays.
    emit('received');
    return { parts, sampleRate: wav.sampleRate, trace: { ...trace } };
  } catch (error) {
    buffered.length = 0;
    options.abort?.();
    emit(controller.signal.aborted ? 'aborted' : 'error');
    throw error;
  } finally {
    // Never delay playback or turn a diagnostic failure into a synthesis failure.
    if (parts.length && wav.sampleRate) {
      try { options.capture?.({ text: options.text, parts, sampleRate: wav.sampleRate, trace: { ...trace } }); } catch { /* diagnostic only */ }
    }
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', onAbort);
    await reader?.cancel().catch(() => {});
    reader?.releaseLock();
    controller.abort();
  }
}
