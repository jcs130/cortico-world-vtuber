/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TTS_SPEECH_DEFAULTS } from '../../src/tts-speech.ts';
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

function mount(): Any {
  const lifecycle = new Lifecycle(); lifecycles.push(lifecycle);
  const root = doc.createElement('div'); doc.body.append(root);
  const ctx = createPanelContext({ pageId: 'world:vtuber', panelId: 'tts', root, lifecycle,
    overlayHost: doc.body, refresh: async () => {}, addLeaveGuard: () => ({ dispose() {} }),
    memo: { get: (_: string, fallback: unknown) => fallback, set() {} },
    createSocket: () => { throw new Error('no websocket required'); }, wsUrl: (path: string) => path,
    onError: vi.fn(), doc });
  ttsPanel.mount(ctx);
  return root;
}
function button(root: Any, text: string): Any {
  return [...root.querySelectorAll('button')].find((b: Any) => b.textContent === text);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn((globalThis as Any).HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn((globalThis as Any).HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
});
afterEach(() => {
  for (const lifecycle of lifecycles.splice(0)) lifecycle.dispose();
  vi.useRealTimers();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); doc.body.replaceChildren();
});

describe('selected speech backend UI', () => {
  it('saves edited settings through the framework config API and retains edits while polling', async () => {
    const st: Any = { kind: 'indextts', phase: 'running', ownership: 'owned', reachable: true,
      health: { voice: 'fixture-a' }, voices: [], speech: { ...TTS_SPEECH_DEFAULTS },
      speechVoices: ['fixture-a', 'fixture-b'] };
    const writes: Any[] = [];
    vi.stubGlobal('fetch', async (input: Any, init?: Any) => {
      const url = String(input);
      if (url === '/api/config') {
        const body = JSON.parse(init.body); writes.push(body);
        st.speech = Object.fromEntries(Object.entries(body.values).map(([key, value]) => [key.split('.').at(-1), value]));
        return json({ result: body.group });
      }
      return json(url.endsWith('/runtime') ? { kind: st.kind, service: st } : st);
    });
    const root = mount(); await flush();
    const enabled = root.querySelector('input[type=checkbox]');
    enabled.checked = true; enabled.dispatchEvent(new Event('change'));
    const voice = root.querySelector('[aria-label="音色"]');
    voice.value = 'fixture-b'; voice.dispatchEvent(new Event('input'));
    const speed = root.querySelector('[aria-label="语速"]');
    speed.value = '0.9'; speed.dispatchEvent(new Event('input'));
    await vi.advanceTimersByTimeAsync(2000); await flush();
    expect(voice.value).toBe('fixture-b');
    expect(speed.value).toBe('0.9');
    expect(writes).toHaveLength(0);
    button(root, '保存声线配置').click(); await flush();
    expect(writes).toEqual([{ group: 'world:vtuber', values: {
      'worlds.vtuber.ttsSpeech.enabled': true, 'worlds.vtuber.ttsSpeech.voice': 'fixture-b',
      'worlds.vtuber.ttsSpeech.speed': 0.9,
      'worlds.vtuber.ttsSpeech.emotionMix': TTS_SPEECH_DEFAULTS.emotionMix,
      'worlds.vtuber.ttsSpeech.emotionMinConfidence': TTS_SPEECH_DEFAULTS.emotionMinConfidence,
    } }]);
    expect(root.textContent).toContain('已保存，下一次合成生效');
    expect(button(root, '保存声线配置').disabled).toBe(true);
  });

  it('starts and restarts the selected service, disables ownership-unsafe actions and stale state', async () => {
    const st: Any = { kind: 'indextts', phase: 'stopped', ownership: 'none', reachable: false,
      health: null, voices: [] };
    const operations: string[] = [];
    let failed = false;
    vi.stubGlobal('fetch', async (input: Any) => {
      const url = String(input);
      if (failed) throw new Error('connection unavailable');
      if (url.endsWith('/start')) {
        operations.push('start'); Object.assign(st, { phase: 'running', ownership: 'owned', reachable: true });
      }
      if (url.endsWith('/stop')) {
        operations.push('stop'); Object.assign(st, { phase: 'stopped', ownership: 'none', reachable: false });
      }
      return json(url.endsWith('/runtime') ? { kind: st.kind, service: st } : st);
    });
    const root = mount(); await flush();
    expect(button(root, '停止语音').disabled).toBe(true);
    button(root, '启动语音').click(); await flush();
    expect(root.textContent).toContain('语音服务已就绪');
    expect(button(root, '启动语音').disabled).toBe(true);
    button(root, '重启语音').click(); await flush();
    expect(operations).toEqual(['start', 'stop', 'start']);
    st.ownership = 'external';
    await vi.advanceTimersByTimeAsync(2000); await flush();
    expect(button(root, '停止语音').disabled).toBe(true);
    expect(button(root, '重启语音').disabled).toBe(true);
    failed = true;
    await vi.advanceTimersByTimeAsync(2000); await flush();
    expect(root.textContent).toContain('状态不可用');
    expect(button(root, '启动语音').disabled).toBe(true);
  });

  it('shows adapter ownership and voice, hides unrelated model installation and profile controls', async () => {
    const health = { voice: 'fixture-voice', preferences_file: 'D:/deployment/voice.json' };
    vi.stubGlobal('fetch', async (input: Any) => json(String(input).endsWith('/runtime')
      ? { kind: 'indextts', service: { ownership: 'owned', url: 'http://127.0.0.1:18012', health, detail: null } }
      : { kind: 'indextts', phase: 'running', ownership: 'owned', reachable: true, health, voices: [] }));
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
