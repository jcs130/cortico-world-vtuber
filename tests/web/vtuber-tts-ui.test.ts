/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
const LIFECYCLE = 'cortico/web/client/core/lifecycle.ts';
const PANEL_CONTEXT = 'cortico/web/client/console-pages/context.ts';
const TTS_PANEL = '../../src/console/tts.ts';
type Any = any;
const { Lifecycle } = await import(LIFECYCLE) as Any;
const { createPanelContext } = await import(PANEL_CONTEXT) as Any;
const { ttsPanel } = await import(TTS_PANEL) as Any;
const doc = (globalThis as Any).document;
const lifecycles: Any[] = [];
const flush = async (): Promise<void> => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const json = (body: unknown): Any => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });
afterEach(() => {
  for (const lifecycle of lifecycles.splice(0)) lifecycle.dispose();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); doc.body.replaceChildren();
});

describe('selected speech backend UI', () => {
  it('shows adapter ownership and voice, hides unrelated model installation and profile controls', async () => {
    const health = { voice: 'fixture-voice', preferences_file: 'D:/deployment/voice.json' };
    vi.stubGlobal('fetch', async (input: Any) => json(String(input).endsWith('/runtime')
      ? { kind: 'indextts', service: { ownership: 'owned', url: 'http://127.0.0.1:18012', health, detail: null } }
      : { kind: 'indextts', phase: 'running', ownership: 'owned', reachable: true, health, voices: [] }));
    vi.spyOn((globalThis as Any).HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    vi.spyOn((globalThis as Any).HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
    const lifecycle = new Lifecycle(); lifecycles.push(lifecycle);
    const root = doc.createElement('div'); doc.body.append(root);
    const ctx = createPanelContext({ pageId: 'world:vtuber', panelId: 'tts', root, lifecycle,
      overlayHost: doc.body, refresh: async () => {}, addLeaveGuard: () => ({ dispose() {} }),
      memo: { get: (_: string, fallback: unknown) => fallback, set() {} },
      createSocket: () => { throw new Error('no websocket required'); }, wsUrl: (path: string) => path,
      onError: vi.fn(), doc });
    ttsPanel.mount(ctx); await flush();
    expect(root.textContent).toContain('fixture-voice');
    expect(root.textContent).toContain('由演出扩展管理');
    expect(root.textContent).toContain('D:/deployment/voice.json');
    const buttons = [...root.querySelectorAll('button')] as Any[];
    expect(buttons.find(b => b.textContent === '安装运行时').hidden).toBe(true);
    expect(buttons.find(b => b.textContent === '保存档案').closest('[hidden]')).not.toBeNull();
    expect(buttons.find(b => b.textContent === '合成试听').disabled).toBe(false);
  });
});
