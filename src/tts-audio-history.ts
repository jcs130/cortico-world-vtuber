/** Bounded, opt-in local TTS evidence. No microphones, extra inference, or network uploads. */
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { mkdir, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pcm16ToWav } from './tts.ts';
import type { TtsReceivedAudio } from './tts-transport.ts';

const MAX_FILES = 32;
const MAX_BYTES = 64 * 1024 * 1024;
const MAX_AGE_MS = 60 * 60_000;
const MAX_PENDING = 2;
const FILE = /^\d{13}-[a-f0-9-]{36}\.(?:wav|json)$/;

export class TtsAudioHistory {
  private queue: Promise<void> = Promise.resolve();
  private pending = 0;
  readonly directory: string;
  readonly marker: string;

  constructor(diagDir: string, private readonly now = Date.now) {
    this.directory = join(diagDir, 'tts-audio');
    this.marker = join(diagDir, 'tts-audio-capture.json');
  }

  /** Marker: {"expiresAt":"ISO timestamp"}; absent/invalid/expired means no recording. */
  capture = (audio: TtsReceivedAudio): void => {
    if (this.pending >= MAX_PENDING) return;
    const at = this.now();
    try {
      const marker = JSON.parse(readFileSync(this.marker, 'utf8').replace(/^\uFEFF/, ''));
      const expiry = Date.parse(marker.expiresAt);
      if (!Number.isFinite(expiry) || expiry <= at || expiry - at > MAX_AGE_MS) return;
    } catch { return; }
    const bytes = audio.parts.reduce((n, part) => n + part.length, 0);
    if (!bytes || bytes + 44 > MAX_BYTES) return;
    const wav = pcm16ToWav(audio.parts, audio.sampleRate);
    // Explicit whitelist: headers, credentials, endpoint URLs and arbitrary metadata never persist.
    const { trace } = audio;
    const metadata = {
      schema: 'tts-received-audio-v1', capturedAt: new Date(at).toISOString(),
      scope: 'received-pcm-not-physical-playback', text: audio.text,
      sampleRate: audio.sampleRate, pcmBytes: bytes, durationMs: bytes / 2 / audio.sampleRate * 1000,
      wavSha256: createHash('sha256').update(wav).digest('hex'),
      pcmSha256: createHash('sha256').update(wav.subarray(44)).digest('hex'),
      terminalPhase: trace.phase, callId: trace.callId, requestId: trace.requestId,
      sessionId: trace.sessionId, voice: trace.voice, referenceSha256: trace.referenceSha256,
    };
    const name = `${at}-${randomUUID()}`;
    this.pending++;
    this.queue = this.queue.then(async () => {
      await mkdir(this.directory, { recursive: true, mode: 0o700 });
      await this.prune(at, wav.length);
      await writeFile(join(this.directory, name + '.wav'), wav, { mode: 0o600 });
      try {
        await writeFile(join(this.directory, name + '.json'), JSON.stringify(metadata, null, 2), { mode: 0o600 });
      } catch (error) {
        await rm(join(this.directory, name + '.wav'), { force: true });
        throw error;
      }
    }).catch(() => { /* Disk failures never interrupt speech. */ }).finally(() => { this.pending--; });
  };

  /** Used only by offline tests / shutdown diagnostics, never awaited by synthesis. */
  async flush(): Promise<void> { await this.queue; }

  private async prune(at: number, incoming: number): Promise<void> {
    const names = (await readdir(this.directory)).filter(name => FILE.test(name) && name.endsWith('.wav')).sort();
    const rows = await Promise.all(names.map(async name => ({ name, size: (await stat(join(this.directory, name))).size })));
    let total = rows.reduce((n, row) => n + row.size, 0), count = rows.length;
    for (const row of rows) {
      if (at - Number(row.name.slice(0, 13)) > MAX_AGE_MS || count >= MAX_FILES || total + incoming > MAX_BYTES) {
        await rm(join(this.directory, row.name), { force: true });
        await rm(join(this.directory, row.name.replace(/\.wav$/, '.json')), { force: true });
        count--; total -= row.size;
      }
    }
  }
}
