/** Manual, real-service checks. Run with: node --import tsx scripts/tts-acceptance.ts <config.json> <output-dir> */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { TtsClient, type TtsPiece } from '../src/tts.ts';
import { TTS_TRANSPORT_DEFAULTS, ttsHeaders, requestMetadata, type TtsRequestTrace, type TtsTransportConfig } from '../src/tts-transport.ts';
import { DeviceAudioSink } from '../src/device-audio.ts';
import type { AudioStreamSession } from '../src/orchestrator.ts';
import type { Logger } from 'cortico/core/types.ts';

interface Config {
  url: string;
  envFile?: string;
  secretName?: string;
  transport: Partial<TtsTransportConfig>;
  audioDevice?: string;
  audioSecondary?: string;
}
const cfg = JSON.parse(readFileSync(process.argv[2], 'utf8').replace(/^\uFEFF/, '')) as Config;
const out = resolve(process.argv[3]);
mkdirSync(out, { recursive: true });
const name = cfg.secretName ?? 'CORTICO_TTS_API_KEY';
const env = cfg.envFile ? readFileSync(cfg.envFile, 'utf8') : '';
const apiKey = process.env[name] || env.split(/\r?\n/).map(line => line.match(/^\s*([^=\s]+)\s*=\s*(\S+)/))
  .find(m => m?.[1] === name)?.[2] || '';
if (!apiKey) throw new Error(`Missing credential ${name}`);
const transport = { ...TTS_TRANSPORT_DEFAULTS, ...cfg.transport, protocol: 'openai-chunked' as const };
const report: Record<string, unknown> = { startedAt: new Date().toISOString(), url: cfg.url,
  model: transport.model, voice: transport.voice, prebufferMs: transport.prebufferMs, tests: [] };
const tests = report.tests as unknown[];
const save = () => writeFileSync(resolve(out, 'acceptance.json'), JSON.stringify(report, null, 2));
const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
const logs: Array<{ msg: string; data?: unknown }> = [];
const log = { trace: (msg: string, data?: unknown) => logs.push({ msg, data }),
  debug: (msg: string, data?: unknown) => logs.push({ msg, data }),
  info: (msg: string, data?: unknown) => logs.push({ msg, data }),
  warn: (msg: string, data?: unknown) => logs.push({ msg, data }),
  error: (msg: string, data?: unknown) => logs.push({ msg, data }),
  emit: (_level: string, msg: string, data?: unknown) => logs.push({ msg, data }),
  child: () => log } as Logger;

async function status(): Promise<Record<string, unknown>> {
  const res = await fetch(`${cfg.url}${transport.healthPath}`, {
    headers: ttsHeaders(apiKey, transport.sessionId), signal: AbortSignal.timeout(3000),
  });
  if (!res.ok) throw new Error(`Status HTTP ${res.status}`);
  return await res.json() as Record<string, unknown>;
}

async function run(id: string, text: string, audible: boolean, interrupt = false) {
  const start = Date.now(), traces: TtsRequestTrace[] = [], device: string[] = [];
  const ac = new AbortController();
  let session: AudioStreamSession | undefined, receivedAt: number | null = null;
  let startedAt: number | null = null, endedAt: number | null = null, interruptedAt: number | null = null;
  let piece: TtsPiece | undefined, error: string | null = null, receivedAfterAbort = 0;
  const sink = audible ? new DeviceAudioSink(log, { device: () => cfg.audioDevice ?? '',
    secondary: () => cfg.audioSecondary ?? 'off', trace: (_area, msg) => device.push(msg) }) : null;
  const client = new TtsClient({ url: cfg.url, apiKey, transport: { ...transport, sessionId: `${transport.sessionId}-${id}` },
    trace: t => { traces.push(t); if (t.phase === 'received') receivedAt = Date.now(); } });
  try {
    piece = await client.synthStream(text, {
      begin: ({ sampleRate }) => {
        if (!sink) return;
        session = sink.beginStream(sampleRate, text);
        void session.started.then(ts => {
          startedAt = ts;
          if (interrupt) setTimeout(() => {
            interruptedAt = Date.now(); ac.abort(); sink.stop(0);
          }, Math.max(0, ts - Date.now()) + 600);
        });
      },
      pcm: chunk => { if (ac.signal.aborted) receivedAfterAbort += chunk.length; session?.push(chunk); },
      abort: () => sink?.stop(0),
    }, { signal: ac.signal });
    session?.end(piece.durationMs);
    if (session) endedAt = await session.ended;
    writeFileSync(resolve(out, `${id}.wav`), piece.wav);
  } catch (err) { error = String(err); }
  finally { sink?.close(); }
  await sleep(500);
  const latest = [...traces].reverse().find(t => t.requestId || t.callId);
  const metadata = latest ? requestMetadata(await status(), latest) : null;
  const nativeOutputOpened = device.some(msg => msg.startsWith('主输出已开:'));
  const networkInterrupted = traces.some(t => t.phase === 'aborted');
  const result = { id, error, startedAt, receivedAt, endedAt, interruptedAt, nativeOutputOpened,
    startedBeforeComplete: startedAt !== null && receivedAt !== null && startedAt < receivedAt,
    networkInterrupted, receivedAfterAbort, durationMs: piece?.durationMs, elapsedMs: Date.now() - start,
    metadata, traces, device };
  tests.push(result); save();
  if (audible && !nativeOutputOpened) throw new Error(`${id}: no native audio output opened`);
  if (interrupt && (!networkInterrupted || receivedAfterAbort !== 0)) throw new Error(`${id}: interruption did not cancel an active stream`);
  if (!interrupt && error) throw new Error(error);
  if (audible && !interrupt && !result.startedBeforeComplete) throw new Error(`${id}: playback did not precede HTTP completion`);
  return result;
}

// DeviceAudioSink timers are unref'ed for host shutdown; the standalone check owns its lifetime.
const lifetime = setInterval(() => {}, 1000);
try {
  await run('play', '你好，我是可米。现在使用局域网语音服务，收到一小段声音就开始播放。我们先到村口看看，等雨停了，再一起沿着小河去找那座桥。', true);
  await run('interrupt', '这一段用来测试打断。现在我正在介绍今天的计划，我们准备整理背包，走过村庄旁边的小河，再到树林里找一些材料。沿途有很多好看的风景，我们可以慢慢探索，等回家以后再把照片分享给大家。', true, true);
  const queueSamples: Array<Record<string, unknown>> = [];
  let sampling = true;
  const sampler = (async () => {
    while (sampling) {
      const s = await status();
      queueSamples.push({ at: Date.now(), waiting: s.waiting, synthesizing: s.synthesizing,
        active: Array.isArray(s.gateway_active) ? s.gateway_active.length : null });
      await sleep(100);
    }
  })();
  let results;
  try {
    results = await Promise.allSettled([1, 2, 3].map(n => run(`queue-${n}`,
      `这里是第${n}个客户端的排队测试。今天我们一起到村庄附近散步，看看河边的树和远处的山，等大家都准备好了，再开始新的旅程。`, false)));
  } finally { sampling = false; await sampler; }
  report.queueSamples = queueSamples;
  const outcomes = results.map(r => r.status === 'fulfilled' ? r.value : { error: String(r.reason) });
  report.queuePassed = results.every(r => r.status === 'fulfilled')
    && queueSamples.some(s => Number(s.waiting) >= 2)
    && outcomes.every(o => 'metadata' in o && o.metadata !== null);
  if (!report.queuePassed) throw new Error('Three-client queue not confirmed by server metrics');
  report.completedAt = new Date().toISOString(); report.passed = true;
} catch (err) {
  report.passed = false; report.failure = String(err); process.exitCode = 1;
} finally {
  clearInterval(lifetime);
  report.logs = logs; save();
  console.log(JSON.stringify({ report: resolve(out, 'acceptance.json'), passed: report.passed, failure: report.failure }));
}
