import { describe, it, expect } from 'vitest';
import { TtsClient, pcm16ToWav } from '../../src/tts.ts';
import { PcmWavStream, requestMetadata, TTS_TRANSPORT_DEFAULTS, type TtsRequestTrace } from '../../src/tts-transport.ts';

const config = { ...TTS_TRANSPORT_DEFAULTS, protocol: 'openai-chunked' as const,
  model: 'test-speech', voice: 'test-voice', sessionId: 'stable-session' };
const pcm = (ms: number) => new Uint8Array(96 * ms).fill(42);
const turn = async () => { await new Promise(resolve => setTimeout(resolve, 5)); };

function fixture() {
  let control!: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  let request: { url: string; init?: RequestInit } | undefined;
  const trace: TtsRequestTrace[] = [];
  const stream = new ReadableStream<Uint8Array>({ start(c) { control = c; }, cancel() { cancelled = true; } });
  const client = new TtsClient({ url: 'http://fixture.invalid', transport: config, apiKey: 'fixture-key',
    // Remote mode must not transmit local generation or pronunciation overrides.
    profile: () => ({ instructions: 'style', seed: 42, referenceAudioB64: 'QUJD', refText: 'reference' }),
    trace: t => trace.push(t), fetchImpl: (async (url, init) => {
      request = { url: String(url), init };
      return new Response(stream, { headers: {
        'content-type': 'audio/wav', 'x-litellm-call-id': 'call-1',
        'x-litellm-session-id': 'stable-session', 'x-tts-request-id': 'request-1',
      } });
    }) as typeof fetch,
  });
  return { client, control, trace, cancelled: () => cancelled, request: () => request! };
}

describe('OpenAI chunked WAV', () => {
  it('finds terminal metadata by request ID without attributing another utterance to this session', () => {
    const trace: TtsRequestTrace = { phase: 'received', callId: 'call-1', requestId: 'native-1',
      sessionId: 'same-session', pcmBytes: 0, elapsedMs: 1 };
    const other = { request_id: 'other', session_id: 'same-session', gateway_outcome: 'completed' };
    expect(requestMetadata({ gateway_last: other }, trace)).toBeNull();
    const own = { native_request_id: 'native-1', gateway_outcome: 'cancelled', queue_ms: 10, text: 'private' };
    expect(requestMetadata({ gateway_last: other, gateway_recent: [own] }, trace))
      .toEqual({ native_request_id: 'native-1', gateway_outcome: 'cancelled', queue_ms: 10 });
  });
  it('buffers 480ms, plays before HTTP EOF and keeps transport IDs without local profile fields', async () => {
    const f = fixture(), seen: Uint8Array[] = [];
    const wav = pcm16ToWav([pcm(1000)], 48000);
    let began = 0, completed = false;
    const pending = f.client.synthStream('普通正文，不加注音。', {
      begin: () => began++, pcm: bytes => seen.push(bytes),
    }).then(piece => { completed = true; return piece; });
    f.control.enqueue(wav.slice(0, 13));
    f.control.enqueue(wav.slice(13, 44 + 96 * 479));
    await turn();
    expect(began).toBe(0); expect(seen).toHaveLength(0);
    f.control.enqueue(wav.slice(44 + 96 * 479, 44 + 96 * 480));
    await turn();
    expect(began).toBe(1); expect(completed).toBe(false);
    expect(seen.reduce((sum, b) => sum + b.length, 0)).toBe(96 * 480);
    f.control.enqueue(wav.slice(44 + 96 * 480)); f.control.close();
    const piece = await pending;
    expect(piece.durationMs).toBe(1000);
    expect(piece.wav).toEqual(wav);
    expect(piece.requestTrace).toMatchObject({ callId: 'call-1', requestId: 'request-1', phase: 'received' });
    const req = f.request();
    expect(req.url).toBe('http://fixture.invalid/v1/audio/speech');
    expect(JSON.parse(String(req.init?.body))).toEqual({ model: config.model, input: piece.text,
      voice: config.voice, speed: 1, response_format: 'wav' });
    expect(new Headers(req.init?.headers).get('x-litellm-session-id')).toBe(config.sessionId);
    expect(JSON.stringify(f.trace)).not.toContain('fixture-key');
  });

  it.each([200, 800])('cancels an unfinished HTTP read and discards queued audio after %ims', async (ms) => {
    const f = fixture(), ac = new AbortController();
    let aborted = 0, received = 0;
    const pending = f.client.synthStream('等待打断', {
      pcm: b => { received += b.length; }, abort: () => aborted++,
    }, { signal: ac.signal });
    const result = expect(pending).rejects.toBeDefined();
    const wav = pcm16ToWav([pcm(2000)], 48000);
    f.control.enqueue(wav.slice(0, 44 + 96 * ms));
    await turn(); ac.abort(); await result;
    const atAbort = received; await turn();
    expect(received).toBe(atAbort); expect(f.cancelled()).toBe(true); expect(aborted).toBe(1);
    expect(f.trace.at(-1)?.phase).toBe('aborted');
    expect(f.trace.some(t => t.phase === 'received')).toBe(false);
  });

  it('plays a valid short utterance at EOF, but rejects a truncated response before releasing it', async () => {
    const a = fixture(), short = pcm16ToWav([pcm(100)], 48000);
    let released = 0;
    const pending = a.client.synthStream('短句', { pcm: b => { released += b.length; } });
    a.control.enqueue(short); a.control.close();
    expect((await pending).durationMs).toBe(100); expect(released).toBe(9600);
    const b = fixture(); released = 0;
    const failed = b.client.synthStream('破损', { pcm: bytes => { released += bytes.length; } });
    const rejection = expect(failed).rejects.toThrow('Truncated');
    b.control.enqueue(short.slice(0, -2)); b.control.close(); await rejection;
    expect(released).toBe(0);
  });

  it('does not retry forbidden models or probe legacy endpoints', async () => {
    const urls: string[] = [];
    const c = new TtsClient({ url: 'http://fixture.invalid', transport: config,
      fetchImpl: (async url => { urls.push(String(url)); return new Response('denied', { status: 403 }); }) as typeof fetch });
    await expect(c.synthStream('test', { pcm() {} })).rejects.toThrow('403');
    expect(urls).toEqual(['http://fixture.invalid/v1/audio/speech']);
  });

  it('reads odd network chunks, skips optional WAV chunks and validates the format', () => {
    const original = pcm16ToWav([pcm(10)], 48000);
    const wav = new Uint8Array(original.length + 10);
    wav.set(original.subarray(0, 36)); wav.set([74, 85, 78, 75, 2, 0, 0, 0, 1, 2], 36);
    wav.set(original.subarray(36), 46);
    const decoder = new PcmWavStream(), decoded: number[] = [];
    for (let i = 0; i < wav.length; i += 7) decoded.push(...decoder.push(wav.subarray(i, i + 7)));
    decoder.finish(); expect(decoded).toEqual([...original.subarray(44)]);
    const wrong = pcm16ToWav([pcm(10)], 24000);
    expect(() => new PcmWavStream().push(wrong)).toThrow('48000');
  });
});
