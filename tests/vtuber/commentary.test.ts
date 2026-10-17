import { describe, it, expect, vi } from 'vitest';
import { CommentaryLane, COMMENTARY_DEFAULTS, parseCommentary, type CommentaryConfig, type SpeechAvailability } from '../../src/commentary.ts';

function fixture() {
  let now = 100_000;
  let config: CommentaryConfig = { ...COMMENTARY_DEFAULTS, enabled: true, sources: 'game audience terminal' };
  const available = { live: true, active: true, speech: { observedAtMs: now, idleSinceMs: now - 30_000, busy: false, ttsReady: true } as SpeechAvailability };
  let resolve!: (value: { text: string } | { error: string }) => void;
  const request = vi.fn((_brief: string) => new Promise<{ text: string } | { error: string }>(r => { resolve = r; }));
  const perform = vi.fn(async (_script: string, _id: number, _evidence: number[]) => '已开演(流式)');
  const log = vi.fn();
  const lane = new CommentaryLane({ config: () => config, availability: () => available, request, perform, log, now: () => now });
  lane.observe({ cursor: 12, ts: new Date(now - 1000).toISOString(), source: 'game', type: 'world.snapshot', origin: 'external', text: '任务受阻；实际脚下石头，未到终点。' });
  const draft = { text: JSON.stringify({ script: '这块石头挡住路了，我看看能不能从旁边绕过去。', kind: 'observation', evidence: [12] }) };
  const settle = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
  return { lane, available, request, perform, log, draft, settle,
    resolve: (value: { text: string } | { error: string } = draft) => resolve(value),
    advance: (ms: number) => { now += ms; available.speech.observedAtMs = now; },
    configure: (patch: Partial<CommentaryConfig>) => { config = { ...config, ...patch }; },
  };
}

describe('bounded commentary cognition lane', () => {
  it('drafts from the current task and recent speech, without full history, tools, credentials or body actions', async () => {
    const f = fixture();
    f.lane.foregroundSpeech('刚才那条小路已经走过了。');
    f.lane.tick();
    expect(f.request).toHaveBeenCalledOnce();
    const brief = f.request.mock.calls[0][0];
    expect(brief).toContain('任务受阻');
    expect(brief).toContain('刚才那条小路');
    expect(brief).toContain('不同source');
    expect(brief).toContain('即使是主播自己的发言也一样');
    expect(brief.length).toBeLessThan(5000);
    f.resolve(); await f.settle();
    expect(f.perform).toHaveBeenCalledWith(expect.stringContaining('石头'), 1, [12]);
    expect(f.lane.facts()).toContain('不是待播稿');
    expect(f.log).toHaveBeenCalledWith('commentary-submitted', expect.any(Object));
  });

  it.each(['disabled', 'not-live', 'paused-or-idle', 'busy', 'tts-failed', 'stale-engine', 'not-due'])(
    'does not request or synthesize when %s', async condition => {
      const f = fixture();
      if (condition === 'disabled') f.configure({ enabled: false });
      if (condition === 'not-live') f.available.live = false;
      if (condition === 'paused-or-idle') f.available.active = false;
      if (condition === 'busy') f.available.speech.busy = true;
      if (condition === 'tts-failed') f.available.speech.ttsReady = false;
      if (condition === 'stale-engine') f.available.speech.observedAtMs -= 6000;
      if (condition === 'not-due') f.available.speech.idleSinceMs = 99_000;
      f.lane.tick(); await f.settle();
      expect(f.request).not.toHaveBeenCalled(); expect(f.perform).not.toHaveBeenCalled();
    });

  it('keeps one slot until a hung request settles; newer foreground speech invalidates its draft', async () => {
    const f = fixture(); f.lane.tick();
    f.advance(40_000); f.lane.tick();
    expect(f.request).toHaveBeenCalledOnce();
    f.lane.foregroundSpeech('我刚刚已经换到另一条路了。');
    f.resolve(); await f.settle();
    expect(f.perform).not.toHaveBeenCalled(); expect(f.lane.facts()).toContain('stale');
  });

  it.each(['stop', 'pause', 'new-idle-episode', 'late-result'])(
    'discards a completed draft after %s', async condition => {
      const f = fixture(); f.lane.tick();
      if (condition === 'stop') f.lane.stop();
      if (condition === 'pause') f.available.active = false;
      if (condition === 'new-idle-episode') f.available.speech.idleSinceMs = 71_000;
      if (condition === 'late-result') f.advance(121_000);
      f.resolve(); await f.settle(); expect(f.perform).not.toHaveBeenCalled();
    });

  it('handles final engine admission races and host errors without fabricating spoken output', async () => {
    const f = fixture(); f.perform.mockResolvedValue('[vtuber_act 过期] 本次未提交');
    f.lane.tick(); f.resolve(); await f.settle();
    expect(f.lane.facts()).toContain('stale');
    expect(f.log.mock.calls.some(([event]) => event === 'commentary-submitted')).toBe(false);
    f.advance(21_000); f.lane.tick(); f.resolve({ error: '认知未启用' }); await f.settle();
    expect(f.lane.facts()).toContain('failed');
    expect(f.perform).toHaveBeenCalledOnce();
  });

  it('drops a draft when its scene or a new audience message changes during generation', async () => {
    const f = fixture(); f.lane.tick();
    f.lane.observe({ cursor: 18, ts: new Date(100_000).toISOString(), source: 'game', type: 'world.snapshot', origin: 'external', text: '已经离开石壁，进入另一片区域。' });
    f.resolve(); await f.settle();
    expect(f.perform).not.toHaveBeenCalled();
    expect(f.lane.facts()).toContain('stale');
  });

  it('keeps current snapshot per source, preserves attribution, excludes credentials and expired material', () => {
    const f = fixture();
    f.lane.observe({ cursor: 13, ts: new Date(99_900).toISOString(), source: 'game', type: 'world.snapshot', origin: 'external', text: '当前位置改变，仍未抵达。' });
    f.lane.observe({ cursor: 14, ts: new Date(99_900).toISOString(), source: 'audience', type: 'chat', origin: 'external', text: '甲说：“我刚拿了钻石。”' });
    f.lane.observe({ cursor: 15, ts: new Date(99_900).toISOString(), source: 'terminal', type: 'chat', origin: 'internal', text: '/login private-password' });
    f.lane.observe({ cursor: 16, ts: new Date(99_900).toISOString(), source: 'unconfigured', type: 'chat', origin: 'external', text: '不该读取' });
    f.lane.observe({ cursor: 17, ts: new Date(-30_000).toISOString(), source: 'game', type: 'old.receipt', origin: 'external', text: '旧库存' });
    f.lane.tick();
    const brief = f.request.mock.calls[0][0];
    expect(brief).not.toContain('任务受阻');
    expect(brief).toContain('甲说'); expect(brief).not.toContain('private-password');
    expect(brief).not.toContain('不该读取');
    expect(brief).not.toContain('旧库存');
  });

  it('does not starve a grounded draft when unrelated telemetry arrives, and does not impose an unrelated 15-second limit', async () => {
    const f = fixture(); f.lane.tick();
    for (let cursor = 20; cursor < 40; cursor++) f.lane.observe({ cursor, ts: new Date(100_000).toISOString(),
      source: 'game', type: 'game.telemetry', origin: 'external', text: `心跳 ${cursor}` });
    f.advance(18_000); f.resolve(); await f.settle();
    expect(f.perform).toHaveBeenCalledOnce();
  });

  it('yields to a new conversation and never reports an unrecognized admission as submitted', async () => {
    const f = fixture(); f.lane.tick();
    f.lane.observe({ cursor: 20, ts: new Date(100_000).toISOString(), source: 'audience', type: 'chat', origin: 'external', text: '前面有路吗？' });
    f.resolve(); await f.settle(); expect(f.perform).not.toHaveBeenCalled();
    const g = fixture(); g.perform.mockResolvedValue('未知请求，未受理');
    g.lane.tick(); g.resolve(); await g.settle();
    expect(g.lane.facts()).toContain('failed');
    expect(g.log.mock.calls.some(([event]) => event === 'commentary-submitted')).toBe(false);
    const h = fixture(); h.lane.tick(); h.configure({ sources: 'audience' });
    h.resolve(); await h.settle(); expect(h.perform).not.toHaveBeenCalled();
  });
});

describe('commentary result contract', () => {
  it('rejects missing/unknown evidence, internal tags, non-JSON and a fabricated result shape', () => {
    const allowed = new Set([12]);
    expect(parseCommentary('让我先想想', allowed)).toBeNull();
    expect(parseCommentary('{"script":"到家了","kind":"observation","evidence":[99]}', allowed)).toBeNull();
    expect(parseCommentary('{"script":"到家了","kind":"observation","evidence":[]}', allowed)).toBeNull();
    expect(parseCommentary('{"script":"<微笑>你好","kind":"thought","evidence":[]}', allowed)).toEqual({ script: '你好', evidence: [] });
    expect(parseCommentary('{"script":"【挥手】<微笑>","kind":"thought","evidence":[]}', allowed)).toBeNull();
    expect(parseCommentary('{"script":"换个角度想，这条路也挺有意思。","kind":"thought","evidence":[]}', allowed)).toEqual({ script: '换个角度想，这条路也挺有意思。', evidence: [] });
  });
});
