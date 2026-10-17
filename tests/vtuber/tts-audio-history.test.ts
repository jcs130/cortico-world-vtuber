import { afterEach, describe, expect, it } from 'vitest';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TtsAudioHistory } from '../../src/tts-audio-history.ts';
import { TtsClient, pcm16ToWav } from '../../src/tts.ts';
import { TTS_TRANSPORT_DEFAULTS, type TtsReceivedAudio } from '../../src/tts-transport.ts';

const dirs: string[] = [];
afterEach(async () => { await Promise.all(dirs.splice(0).map(dir => rm(dir, { recursive: true, force: true }))); });
const at = Date.now();
const pcm = new Uint8Array(48000).fill(42);
const audio: TtsReceivedAudio = { text: '隧道还在往南掏。', parts: [pcm], sampleRate: 48000,
  trace: { phase: 'received', callId: 'gateway', requestId: 'native', sessionId: 'stable',
    pcmBytes: pcm.length, elapsedMs: 600, voice: 'cormi-Q5', referenceSha256: 'ref',
    server: { authorization: 'must-not-save' } } };
async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), 'tts-evidence-')); dirs.push(dir);
  let time = at;
  const recorder = new TtsAudioHistory(dir, () => time);
  const enable = async (ms = 30 * 60_000) => writeFile(recorder.marker, JSON.stringify({ expiresAt: new Date(time + ms).toISOString() }));
  return { dir, recorder, enable, tick: () => { time++; } };
}

describe('local TTS audio evidence', () => {
  it('writes nothing without a valid, bounded incident window', async () => {
    const f = await fixture();
    f.recorder.capture(audio); await f.recorder.flush();
    await expect(readdir(f.recorder.directory)).rejects.toMatchObject({ code: 'ENOENT' });
    for (const expiresAt of ['bad', new Date(at - 1).toISOString(), new Date(at + 3_600_001).toISOString()]) {
      await writeFile(f.recorder.marker, JSON.stringify({ expiresAt })); f.recorder.capture(audio); await f.recorder.flush();
    }
    await expect(readdir(f.recorder.directory)).rejects.toMatchObject({ code: 'ENOENT' });
  });

  it('preserves exact PCM and IDs, whitelists metadata, and identifies reception rather than playback', async () => {
    const f = await fixture(); await f.enable(); f.recorder.capture(audio); await f.recorder.flush();
    const files = await readdir(f.recorder.directory);
    const wav = await readFile(join(f.recorder.directory, files.find(x => x.endsWith('.wav'))!));
    expect(wav.subarray(44)).toEqual(Buffer.from(pcm));
    const raw = await readFile(join(f.recorder.directory, files.find(x => x.endsWith('.json'))!), 'utf8');
    const meta = JSON.parse(raw);
    expect(meta).toMatchObject({ text: audio.text, callId: 'gateway', requestId: 'native',
      scope: 'received-pcm-not-physical-playback', voice: 'cormi-Q5', terminalPhase: 'received' });
    expect(raw).not.toMatch(/authorization|must-not-save/);
  });

  it('keeps partial aborted audio without inventing a completed outcome', async () => {
    const f = await fixture(); await f.enable(); f.recorder.capture({ ...audio, trace: { ...audio.trace, phase: 'aborted' } });
    await f.recorder.flush();
    const name = (await readdir(f.recorder.directory)).find(x => x.endsWith('.json'))!;
    expect(JSON.parse(await readFile(join(f.recorder.directory, name), 'utf8')).terminalPhase).toBe('aborted');
  });

  it('bounds both retained utterances and pending disk writes', async () => {
    const f = await fixture(); await f.enable();
    for (let i = 0; i < 35; i++) { f.tick(); f.recorder.capture(audio); await f.recorder.flush(); }
    expect((await readdir(f.recorder.directory)).filter(x => x.endsWith('.wav'))).toHaveLength(32);
    const g = await fixture(); await g.enable();
    for (let i = 0; i < 100; i++) g.recorder.capture(audio);
    await g.recorder.flush();
    expect((await readdir(g.recorder.directory)).filter(x => x.endsWith('.wav'))).toHaveLength(2);
  });

  it('does not record above the storage budget or propagate disk failures', async () => {
    const f = await fixture(); await f.enable();
    f.recorder.capture({ ...audio, parts: [new Uint8Array(64 * 1024 * 1024)] }); await f.recorder.flush();
    await expect(readdir(f.recorder.directory)).rejects.toMatchObject({ code: 'ENOENT' });
    await writeFile(f.recorder.directory, 'not a directory');
    expect(() => f.recorder.capture(audio)).not.toThrow(); await f.recorder.flush();
  });

  it('captures the same PCM sent to streaming playback, without repeating the HTTP request', async () => {
    let requests = 0; const played: Uint8Array[] = [], captured: TtsReceivedAudio[] = [];
    const wav = pcm16ToWav([pcm], 48000);
    const client = new TtsClient({ url: 'http://fixture.invalid',
      transport: { ...TTS_TRANSPORT_DEFAULTS, protocol: 'openai-chunked', model: 'speech', voice: 'cormi-Q5', sessionId: 'stable' },
      apiKey: 'secret', capture: a => captured.push(a), fetchImpl: (async () => {
        requests++; return new Response(new Uint8Array(wav).buffer, { headers: { 'x-tts-request-id': 'native' } });
      }) as typeof fetch });
    const result = await client.synthStream('测试起声。', { pcm: chunk => played.push(chunk) });
    expect(requests).toBe(1); expect(captured).toHaveLength(1);
    expect(Buffer.concat(captured[0].parts)).toEqual(Buffer.concat(played));
    expect(Buffer.concat(played)).toEqual(Buffer.from(result.wav.subarray(44)));
    expect(captured[0].trace.phase).toBe('received');
  });

  it('ignores a failing diagnostic observer and keeps the original successful synthesis', async () => {
    const wav = pcm16ToWav([pcm], 48000);
    const client = new TtsClient({ url: 'http://fixture.invalid', capture: () => { throw new Error('disk full'); },
      transport: { ...TTS_TRANSPORT_DEFAULTS, protocol: 'openai-chunked', model: 'speech', voice: 'cormi-Q5', sessionId: 'stable' },
      fetchImpl: (async () => new Response(new Uint8Array(wav).buffer)) as typeof fetch });
    expect((await client.synth('测试起声。')).durationMs).toBe(500);
  });

  it('retains delivered PCM on cancellation without replaying or calling synthesis again', async () => {
    let control!: ReadableStreamDefaultController<Uint8Array>;
    const captured: TtsReceivedAudio[] = []; let requests = 0, aborted = false;
    const ac = new AbortController();
    const client = new TtsClient({ url: 'http://fixture.invalid', capture: a => captured.push(a),
      transport: { ...TTS_TRANSPORT_DEFAULTS, protocol: 'openai-chunked', model: 'speech', voice: 'cormi-Q5', sessionId: 'stable' },
      fetchImpl: (async () => { requests++; return new Response(new ReadableStream({ start(c) { control = c; } })); }) as typeof fetch });
    const active = client.synthStream('测试打断。', { pcm: () => ac.abort(), abort: () => { aborted = true; } }, { signal: ac.signal });
    await new Promise(resolve => setTimeout(resolve, 0));
    control.enqueue(pcm16ToWav([pcm], 48000));
    await expect(active).rejects.toThrow();
    expect(requests).toBe(1); expect(aborted).toBe(true); expect(captured).toHaveLength(1);
    expect(captured[0].trace.phase).toBe('aborted');
    expect(Buffer.concat(captured[0].parts)).toEqual(Buffer.from(pcm));
  });
});
