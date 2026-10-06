import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WorldHost } from 'cortico/core/types.ts';
import type { EngineNote } from '../../src/engine-ipc.ts';
import { VtuberWorldProxy } from '../../src/proxy.ts';

interface ProxyState {
  ready: boolean;
  stopping: boolean;
  host: WorldHost | null;
  child: { connected: boolean; exitCode: number | null; killed: boolean; send: (message: unknown) => void } | null;
  statusCache: string | null;
  statusObservedAt: string | null;
  onNote(note: EngineNote): void;
  teardownChild(): void;
  rpc: (...args: unknown[]) => Promise<unknown>;
}

function fixture() {
  const proxy = new VtuberWorldProxy({});
  const state = proxy as unknown as ProxyState;
  state.ready = true;
  state.host = { pushDeferred: () => {} } as unknown as WorldHost;
  const sent: unknown[] = [];
  state.child = { connected: true, exitCode: 0, killed: false, send: (message) => { sent.push(message); } };
  const observe = (line: string | null) => state.onNote({ kind: 'status', line, live: false, decl: {} });
  return { proxy, state, sent, observe };
}

afterEach(() => vi.useRealTimers());

describe('演出请求的完整缓存读数', () => {
  it('无读数或引擎未就绪时不替代状态事件', () => {
    const { proxy, state, observe } = fixture();
    expect(proxy.requestFacts()).toBeNull();
    observe('[演出状态] 安静');
    state.ready = false;
    expect(proxy.requestFacts()).toBeNull();
    state.ready = true;
    expect(proxy.requestFacts()?.snapshotTypes).toEqual(['vtuber.status']);
    observe(null);
    expect(proxy.requestFacts()).toBeNull();
  });

  it('读取沿用通知的真实观察时间且不发送RPC；下一份状态替换完整读数', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-04T01:10:00Z'));
    const { proxy, state, sent, observe } = fixture();
    const rpc = vi.fn<ProxyState['rpc']>();
    state.rpc = rpc;
    observe('[演出状态] 正在说话');
    const first = proxy.requestFacts()!;
    expect(first.text).toContain('[演出状态] 正在说话');
    expect(first.text).toMatch(/观察 2026-10-04T09:10:00/);
    vi.advanceTimersByTime(60_000);
    expect(proxy.requestFacts()).toEqual(first);
    observe('[演出状态] 排队 3 拍');
    const updated = proxy.requestFacts()!;
    expect(updated.text).toContain('[演出状态] 排队 3 拍');
    expect(updated.text).not.toContain('正在说话');
    expect(updated.text).not.toEqual(first.text);
    expect(rpc).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
    // 返回的列表不允许调用者污染下一次读取。
    (updated.snapshotTypes as string[]).push('其他状态');
    expect(proxy.requestFacts()?.snapshotTypes).toEqual(['vtuber.status']);
  });

  it('连接失效、退出与下一次初始化之间不返回上一生命周期的读数', () => {
    const { proxy, state, observe } = fixture();
    observe('[演出状态] 安静');
    expect(proxy.requestFacts()).not.toBeNull();
    state.child!.connected = false;
    expect(proxy.requestFacts()).toBeNull();
    const observation = state.statusObservedAt;
    observe('[演出状态] 旧连接迟到的通知');
    expect(state.statusObservedAt).toBe(observation);
    expect(state.statusCache).toBe('[演出状态] 安静');
    state.teardownChild();
    expect(state.statusObservedAt).toBeNull();
    expect(state.statusCache).toBeNull();
    state.ready = true;
    state.child = { connected: true, exitCode: 0, killed: false, send: () => {} };
    expect(proxy.requestFacts()).toBeNull();
    observe('[演出状态] 排队 1 拍');
    expect(proxy.requestFacts()?.text).toContain('排队 1 拍');
  });

  it('停机尚在等待子进程收尾时立即撤销缓存读数', async () => {
    const { proxy, state, observe } = fixture();
    observe('[演出状态] 正在说话');
    let release!: (value: unknown) => void;
    state.rpc = () => new Promise(resolve => { release = resolve; });
    const stopping = proxy.stop();
    expect(proxy.requestFacts()).toBeNull();
    expect(state.statusObservedAt).toBeNull();
    observe('[演出状态] 停机期间的迟到通知');
    expect(state.statusObservedAt).toBeNull();
    release(undefined);
    await stopping;
    expect(proxy.requestFacts()).toBeNull();
    expect(state.statusCache).toBeNull();
  });
});
