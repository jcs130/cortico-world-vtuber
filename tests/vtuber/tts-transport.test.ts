import { describe, it, expect, vi } from 'vitest';
import { TtsClient, pcm16ToWav } from '../../src/tts.ts';
import { parseVoiceCatalog, PcmWavStream, requestMetadata, TTS_TRANSPORT_DEFAULTS, type TtsRequestTrace, type TtsTransportConfig } from '../../src/tts-transport.ts';
import { TTS_SPEECH_DEFAULTS } from '../../src/tts-speech.ts';

const config = { ...TTS_TRANSPORT_DEFAULTS, protocol: 'openai-chunked' as const,
  model: 'test-speech', voice: 'test-voice', sessionId: 'stable-session' };
const pcm = (ms: number) => new Uint8Array(96 * ms).fill(42);
const turn = async () => { await new Promise(resolve => setTimeout(resolve, 5)); };

function fixture(extra: Partial<TtsTransportConfig> = {}) {
  let control!: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  let request: { url: string; init?: RequestInit } | undefined;
  const trace: TtsRequestTrace[] = [];
  const stream = new ReadableStream<Uint8Array>({ start(c) { control = c; }, cancel() { cancelled = true; } });
  const client = new TtsClient({ url: 'http://fixture.invalid', transport: { ...config, ...extra }, apiKey: 'fixture-key',
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
  it('releases a gapped response as one complete PCM queue after validation', async () => {
    let now = 1_000;
    const clock = vi.spyOn(Date, 'now').mockImplementation(() => now);
    const f = fixture({ playbackMode: 'buffered' }), wav = pcm16ToWav([pcm(2000)], 48000), seen: Uint8Array[] = [];
    let began = 0;
    try {
      const pending = f.client.synthStream('continuous', { begin: () => began++, pcm: b => seen.push(b) });
      f.control.enqueue(wav.slice(0, 46)); await turn();
      now += 10_000; f.control.enqueue(wav.slice(46, 44 + 96 * 1000)); await turn();
      expect(began).toBe(0); expect(seen).toEqual([]);
      now += 3_000; f.control.enqueue(wav.slice(44 + 96 * 1000)); await turn();
      expect(began).toBe(0); expect(seen).toEqual([]);
      f.control.close(); const piece = await pending;
      expect(began).toBe(1); expect(seen).toHaveLength(1); expect(seen[0]).toEqual(wav.slice(44));
      expect(piece.wav).toEqual(wav); expect(piece.requestTrace).toMatchObject({ playbackMode: 'buffered', pcmChunks: 3, maxPcmGapMs: 10_000 });
      const phases = f.trace.map(t => t.phase); expect(phases.indexOf('playable')).toBeLessThan(phases.indexOf('received'));
    } finally { clock.mockRestore(); }
  });

  it('discards complete received bytes when cancelled before EOF in buffered mode', async () => {
    const f = fixture({ playbackMode: 'buffered' }), ac = new AbortController();
    let began = 0, played = 0, aborted = 0;
    const pending = f.client.synthStream('cancel', { begin: () => began++, pcm: () => played++, abort: () => aborted++ }, { signal: ac.signal });
    const rejection = expect(pending).rejects.toBeDefined();
    f.control.enqueue(pcm16ToWav([pcm(1000)], 48000)); await turn(); ac.abort(); await rejection;
    expect(began).toBe(0); expect(played).toBe(0); expect(aborted).toBe(1); expect(f.cancelled()).toBe(true);
  });

  it('times out a buffered read without playing or submitting a replacement request', async () => {
    const f = fixture({ playbackMode: 'buffered', timeoutMs: 30 }); let played = 0;
    const pending = f.client.synthStream('timeout', { pcm: () => played++ });
    const rejection = expect(pending).rejects.toBeDefined();
    f.control.enqueue(pcm16ToWav([pcm(1000)], 48000)); await rejection;
    expect(played).toBe(0); expect(f.cancelled()).toBe(true); expect(f.trace.at(-1)?.phase).toBe('aborted');
  });

  it('rejects truncated buffered audio even after more than the streaming prebuffer has arrived', async () => {
    const f = fixture({ playbackMode: 'buffered' }); let played = 0;
    const pending = f.client.synthStream('bad', { pcm: () => played++ });
    const rejection = expect(pending).rejects.toThrow('Truncated');
    f.control.enqueue(pcm16ToWav([pcm(1000)], 48000).slice(0, -2)); f.control.close(); await rejection;
    expect(played).toBe(0); expect(f.trace.some(t => t.phase === 'playable')).toBe(false);
  });

  it('rejects an invalid playback mode before sending a request', async () => {
    const f = fixture({ playbackMode: 'invalid' as 'buffered' });
    await expect(f.client.synthStream('bad mode', { pcm() {} })).rejects.toThrow('Invalid TTS playback mode');
    expect(f.request()).toBeUndefined();
  });

  it('reads an authenticated server catalog, caches it, and snapshots the selected voice for each request', async () => {
    const requests: { url: string; body?: Record<string, unknown> }[] = [];
    let speech = { ...TTS_SPEECH_DEFAULTS, enabled: true, voice: 'cormi-Q5' };
    let first!: ReadableStreamDefaultController<Uint8Array>;
    let calls = 0;
    const wav = pcm16ToWav([pcm(100)], 48000);
    const client = new TtsClient({ url: 'http://fixture.invalid', transport: config, apiKey: 'fixture-secret',
      profile: () => ({ speech, referenceAudioB64: 'must-not-leave-client' }),
      fetchImpl: (async (url, init) => {
        expect(new Headers(init?.headers).get('authorization')).toBe('Bearer fixture-secret');
        expect(new Headers(init?.headers).get('x-litellm-session-id')).toBe(config.sessionId);
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        requests.push({ url: String(url), body });
        if (String(url).endsWith('/voices')) return Response.json({ catalog: [
          { id: 'cormi-Q3', label: 'Q3 傲娇小魔导师', private_path: '/private/reference.wav' },
          { id: 'cormi-Q5', label: 'Q5 灵动冒险少女' },
        ] });
        const stream = ++calls === 1 ? new ReadableStream<Uint8Array>({ start(c) { first = c; } }) : new Uint8Array(wav).buffer;
        return new Response(stream, { headers: { 'x-tts-voice': String(body.voice) } });
      }) as typeof fetch });
    const voices = await client.listRemoteVoices();
    expect(voices.map(v => v.id)).toEqual(['cormi-Q3', 'cormi-Q5']);
    expect(JSON.stringify(voices)).not.toContain('private');
    await client.listRemoteVoices();
    const active = client.synthStream('first', { pcm() {} });
    speech = { ...speech, voice: 'cormi-Q3' };
    await client.synthStream('second', { pcm() {} });
    first.enqueue(wav); first.close(); await active;
    expect(requests.map(r => r.url)).toEqual(['http://fixture.invalid/v1/audio/voices',
      'http://fixture.invalid/v1/audio/speech', 'http://fixture.invalid/v1/audio/speech']);
    expect(requests.slice(1).map(r => r.body?.voice)).toEqual(['cormi-Q5', 'cormi-Q3']);
    expect(JSON.stringify(requests)).not.toContain('must-not-leave-client');
    expect(client.remoteVoice).toBe('cormi-Q3');
  });

  it('rejects a response using another voice before playing it, without falling back', async () => {
    let calls = 0, played = 0;
    const client = new TtsClient({ url: 'http://fixture.invalid', transport: config,
      fetchImpl: (async () => { calls++; return new Response(new Uint8Array(pcm16ToWav([pcm(100)], 48000)).buffer,
        { headers: { 'x-tts-voice': 'wrong-voice' } }); }) as typeof fetch });
    await expect(client.synthStream('test', { pcm() { played++; } })).rejects.toThrow('voice differs');
    expect(calls).toBe(1); expect(played).toBe(0);
    expect(parseVoiceCatalog({ voices: ['valid', '../private', 'valid'] })).toEqual([{ id: 'valid', label: 'valid' }]);
  });

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
