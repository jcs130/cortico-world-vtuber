import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { MusicGenerationClient, generationJob, generationRequest, type MusicGenerationJob } from '../../src/music-generation.ts';

const input = { requestText: '写一首河边旅行的原创歌', title: 'River walk', lyrics: '[Verse]\n河边有微风\n[Chorus]\n一起看星光', style: 'acoustic folk', durationSec: 45 };
const queued: MusicGenerationJob = { jobId: 'job-1', state: 'queued', title: 'Unsafe unreviewed title', createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' };
const ready: MusicGenerationJob = { ...queued, state: 'ready', title: input.title, trackId: 'generated-song', contentApproved: true, audioValidated: true, aiGenerated: true };
const stops: Array<() => void | Promise<void>> = [];
afterEach(async () => { for (const stop of stops.splice(0)) await stop(); });

async function fixture(options: { checkpoint?: string; onJob?: (job: MusicGenerationJob) => Promise<void> } = {}) {
  let jobs: unknown[] = [];
  const requests: { path: string; method: string; body?: Record<string, unknown> }[] = [];
  const api = vi.fn<typeof fetch>(async (url, init) => {
    const path = new URL(String(url)).pathname;
    requests.push({ path, method: init?.method ?? 'GET', ...(init?.body ? { body: JSON.parse(String(init.body)) } : {}) });
    if (path === '/jobs' && init?.method === 'POST') { jobs = [queued]; return Response.json(queued); }
    if (path.endsWith('/cancel')) { jobs = [{ ...queued, state: 'cancelled' }]; return Response.json(jobs[0]); }
    return Response.json({ jobs });
  });
  const seen: MusicGenerationJob[] = [];
  const client = new MusicGenerationClient({ url: () => 'http://generator.test', fetch: api,
    notificationFile: options.checkpoint, onJob: options.onJob ?? (async j => { seen.push(j); }) });
  stops.push(() => client.stop());
  await client.start();
  await new Promise(resolve => setTimeout(resolve, 0));
  return { client, seen, requests, setJobs: (next: unknown[]) => { jobs = next; } };
}

describe('background original-song jobs', () => {
  it('returns a queued receipt and notifies only terminal changes once', async () => {
    const f = await fixture();
    const accepted = await f.client.submit(input, 'call-1');
    expect(accepted.state).toBe('queued');
    expect(accepted.title).not.toBe(queued.title);
    expect(f.requests.find(r => r.method === 'POST')?.body).toMatchObject({ ...input, requestKey: 'call-1' });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(f.seen).toEqual([]);
    f.setJobs([{ ...queued, state: 'generating' }]);
    await f.client.refresh();
    expect(f.seen).toEqual([]);
    f.setJobs([ready]);
    await f.client.refresh(); await f.client.refresh();
    expect(f.seen).toEqual([ready]);
    expect(f.client.state().jobs).toEqual([ready]);
  });
  it('rejects ready without every approval flag and never emits unsafe titles', async () => {
    const f = await fixture();
    for (const key of ['contentApproved', 'audioValidated', 'aiGenerated'] as const) {
      const bad = { ...ready, [key]: undefined };
      f.setJobs([bad]); await f.client.refresh();
      expect(f.seen).toEqual([]);
      expect(f.client.state().error).toContain('不能播放');
    }
    f.setJobs([{ ...queued, state: 'rejected' }]); await f.client.refresh();
    expect(f.seen[0].title).not.toBe(queued.title);
    expect(f.seen[0].trackId).toBeUndefined();
  });
  it('preserves notification receipts through restart and retries failed event delivery', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'music-jobs-'));
    stops.push(() => rm(dir, { recursive: true, force: true }));
    const file = join(dir, 'notified.json');
    let available = false;
    const seen: MusicGenerationJob[] = [];
    const f = await fixture({ checkpoint: file, onJob: async j => { if (!available) throw Error('event store unavailable'); seen.push(j); } });
    f.setJobs([ready]); await f.client.refresh();
    expect(seen).toEqual([]);
    available = true;
    await f.client.refresh(); expect(seen).toEqual([ready]);
    f.client.stop();
    const again = await fixture({ checkpoint: file });
    again.setJobs([ready]); await again.client.refresh();
    expect(again.seen).toEqual([]);
  });
  it('cancels the selected generation without contacting the shared GPU interrupt API', async () => {
    const f = await fixture();
    expect((await f.client.cancel('job-1')).state).toBe('cancelled');
    expect(f.requests.filter(r => r.method === 'POST').map(r => r.path)).toEqual(['/jobs/job-1/cancel']);
    await f.client.refresh(); expect(f.seen[0].state).toBe('cancelled');
  });
  it('contains service failures and rejects invalid requests before submitting', async () => {
    const send = vi.fn<typeof fetch>(async () => { throw Error('offline'); });
    const client = new MusicGenerationClient({ url: () => 'http://generator.test', fetch: send, onJob: async () => {} });
    stops.push(() => client.stop()); await client.start();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(client.state().error).toBe('offline');
    const before = send.mock.calls.length;
    await expect(client.submit({ ...input, durationSec: 1 }, 'call-1')).rejects.toThrow('30–120');
    expect(send.mock.calls.length).toBe(before);
    client.stop();
    await client.refresh(); expect(send.mock.calls.length).toBe(before);
  });
  it('validates ids, text control characters, timestamps and required media identity', () => {
    expect(() => generationJob({ ...ready, trackId: '../song' })).toThrow('无效');
    expect(() => generationJob({ ...ready, updatedAt: 'invalid' })).toThrow('无效');
    expect(() => generationJob({ ...ready, trackId: undefined })).toThrow('不能播放');
    expect(() => generationRequest({ ...input, style: 'pop\u0000' })).toThrow('原创歌曲');
    expect(generationRequest(input).lyrics).toBe(input.lyrics);
  });
});
