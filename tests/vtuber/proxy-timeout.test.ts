import { afterEach, expect, it, vi } from 'vitest';
import type { WorldHost } from 'cortico/core/types.ts';
import type { EngineRequest } from '../../src/engine-ipc.ts';
import { VtuberWorldProxy } from '../../src/proxy.ts';

afterEach(() => vi.useRealTimers());

it('引擎已接收请求但不回执时记故障并取消等待，不重发演出', async () => {
  vi.useFakeTimers();
  const sent: Array<{ t: string }> = [];
  const errors: Array<{ message: string; data: unknown }> = [];
  const proxy = new VtuberWorldProxy({});
  const inner = proxy as unknown as {
    child: { connected: boolean; pid: number; send(message: { t: string }): void };
    host: WorldHost;
    pending: Map<number, unknown>;
    rpc(req: EngineRequest, timeoutMs: number): Promise<unknown>;
  };
  inner.child = { connected: true, pid: 123, send: (message) => { sent.push(message); } };
  inner.host = {
    log: { error: (message: string, data: unknown) => { errors.push({ message, data }); } },
  } as unknown as WorldHost;
  const waiting = inner.rpc({
    kind: 'tool', name: 'vtuber_act', args: { script: '已经递交的演出' }, round: null,
    role: 'main', callId: 'unanswered-act',
  }, 20_000);
  const rejected = expect(waiting).rejects.toThrow('20s 未回执');
  await vi.advanceTimersByTimeAsync(20_000);
  await rejected;
  expect(inner.pending.size).toBe(0);
  expect(sent.map((message) => message.t)).toEqual(['req', 'cancel']);
  expect(errors).toHaveLength(1);
  expect(errors[0].data).toMatchObject({
    kind: 'tool', tool: 'vtuber_act', callId: 'unanswered-act', timeoutMs: 20_000,
    childPid: 123, connected: true,
  });
});

it('托管语音的准备时限覆盖引擎初始化，不沿用普通 RPC 的短时限', async () => {
  const proxy = new VtuberWorldProxy({ ttsService: { kind: 'indextts', autoStart: true, startupTimeoutMs: 60_000 } });
  const inner = proxy as unknown as { initTimeoutMs(): number };
  expect(inner.initTimeoutMs()).toBe(70_000);
  const external = new VtuberWorldProxy({ ttsService: { kind: 'external', autoStart: false, startupTimeoutMs: 60_000 } });
  expect((external as unknown as { initTimeoutMs(): number }).initTimeoutMs()).toBe(30_000);
});

it('初始化失败后关闭本次子进程，不能留下未挂载的演出与适配器', async () => {
  const proxy = new VtuberWorldProxy({});
  const inner = proxy as unknown as { spawn(): Promise<void>; stop(): Promise<void>; host: WorldHost | null };
  vi.spyOn(inner, 'spawn').mockRejectedValue(new Error('init failed'));
  const stop = vi.spyOn(inner, 'stop').mockResolvedValue();
  const host = { store: { latestCursor: () => 0 } } as unknown as WorldHost;
  await expect(proxy.start(host)).rejects.toThrow('init failed');
  expect(stop).toHaveBeenCalledOnce();
});
