import { describe, expect, it } from 'vitest';
import { VtuberWorld } from '../../src/world.ts';
import { TtsClient, TtsSkipped, pcm16ToWav, type TtsPiece, type TtsStreamSink } from '../../src/tts.ts';

interface StreamInternals {
  ttsClient: TtsClient;
  ttsOk: boolean | null;
  synthStreamAligned(text: string, sink: TtsStreamSink, signal: AbortSignal): Promise<TtsPiece>;
  tracePerf(lane: string, message: string, options?: { event?: string }): void;
}

function fixture(fetchImpl: typeof fetch): { world: StreamInternals; events: string[] } {
  const world = new VtuberWorld({ alignEnabled: () => false }) as unknown as StreamInternals;
  world.ttsClient = new TtsClient({ url: 'http://fixture.invalid', fetchImpl });
  const events: string[] = [];
  world.tracePerf = (_lane, _message, options) => { if (options?.event) events.push(options.event); };
  return { world, events };
}

describe('stream synthesis fallback', () => {
  it('an explicit duplicate skip sends no full-synthesis retry or PCM and preserves the previous TTS state', async () => {
    const paths: string[] = [];
    const received: Uint8Array[] = [];
    const fetchImpl = (async (url) => {
      paths.push(String(url));
      return new Response('{"error":"dedup"}', { status: 410 });
    }) as typeof fetch;
    const { world } = fixture(fetchImpl); world.ttsOk = true;
    await expect(world.synthStreamAligned('之前已播出的文本。', {
      pcm: chunk => received.push(chunk),
    }, new AbortController().signal)).rejects.toBeInstanceOf(TtsSkipped);
    expect(paths).toHaveLength(1);
    expect(paths[0]).toMatch(/\/stream$/);
    expect(received).toHaveLength(0);
    expect(world.ttsOk).toBe(true);
  });
  it('does not replay a sentence after PCM was delivered and the stream fails', async () => {
    const pcm = new Uint8Array([1, 0, 2, 0]);
    const header = pcm16ToWav([], 24000);
    const received: Uint8Array[] = [];
    const paths: string[] = [];
    const failure = new Error('stream disconnected');
    const fetchImpl = (async (url) => {
      paths.push(String(url));
      if (!String(url).endsWith('/stream')) return new Response(pcm16ToWav([pcm], 24000).buffer as ArrayBuffer);
      let stage = 0;
      return new Response(new ReadableStream<Uint8Array>({
        pull(controller) {
          if (stage++ === 0) controller.enqueue(header);
          else if (stage === 2) controller.enqueue(pcm);
          else controller.error(failure);
        },
      }));
    }) as typeof fetch;
    const { world, events } = fixture(fetchImpl);
    await expect(world.synthStreamAligned('这句话只播一次。', {
      pcm: chunk => received.push(chunk),
    }, new AbortController().signal)).rejects.toThrow(failure.message);
    expect(Buffer.concat(received)).toEqual(Buffer.from(pcm));
    expect(paths).toHaveLength(1);
    expect(events).toContain('stream-failed-after-audio');
  });

  it('falls back before any PCM was delivered', async () => {
    const pcm = new Uint8Array([1, 0, 2, 0]);
    const received: Uint8Array[] = [];
    const fetchImpl = (async (url) => {
      if (String(url).endsWith('/stream')) return new Response('missing route', { status: 404 });
      return new Response(pcm16ToWav([pcm], 24000).buffer as ArrayBuffer);
    }) as typeof fetch;
    const { world } = fixture(fetchImpl);
    const piece = await world.synthStreamAligned('整段回落。', {
      pcm: chunk => received.push(chunk),
    }, new AbortController().signal);
    expect(Buffer.concat(received)).toEqual(Buffer.from(pcm));
    expect(piece.wav).toEqual(pcm16ToWav([pcm], 24000));
  });

  it('forwards cancellation to the full synthesis retry', async () => {
    const ac = new AbortController();
    let retrySignal: AbortSignal | null = null;
    const received: Uint8Array[] = [];
    const fetchImpl = (async (url, init) => {
      if (String(url).endsWith('/stream')) return new Response('missing route', { status: 404 });
      retrySignal = init?.signal ?? null;
      ac.abort(new Error('synthesis cancelled'));
      if (retrySignal?.aborted) throw retrySignal.reason;
      throw new Error('cancellation not forwarded');
    }) as typeof fetch;
    const { world } = fixture(fetchImpl);
    await expect(world.synthStreamAligned('整段回落。', {
      pcm: chunk => received.push(chunk),
    }, ac.signal)).rejects.toThrow('synthesis cancelled');
    expect(retrySignal).not.toBeNull();
    expect((retrySignal as unknown as AbortSignal).aborted).toBe(true);
    expect(received).toHaveLength(0);
  });
});
