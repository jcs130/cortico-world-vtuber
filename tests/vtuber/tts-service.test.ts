import { afterEach, describe, expect, it } from 'vitest';
import { createServer, type Server } from 'node:http';
import { nullLogger } from 'cortico/core/util.ts';
import { TtsService, type TtsServiceConfig } from '../../src/tts-service.ts';

const services: TtsService[] = [];
const servers: Server[] = [];
afterEach(async () => {
  await Promise.all(services.splice(0).map(service => service.stop()));
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve()))));
});

async function port(): Promise<number> {
  const server = createServer();
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const value = (server.address() as { port: number }).port;
  await new Promise<void>(resolve => server.close(() => resolve()));
  return value;
}

async function waitFor(test: () => boolean, timeoutMs = 5000): Promise<void> {
  const end = Date.now() + timeoutMs;
  while (!test()) {
    if (Date.now() > end) throw new Error('condition timed out');
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

function fixture(extra = ''): string {
  return `const http=require('node:http');
    let healthy=true;
    process.stdin.resume(); process.stdin.on('end',()=>process.exit(0));
    http.createServer((req,res)=>{if(req.url==='/invalidate') healthy=false;
      res.end(JSON.stringify({status:'ok',version:'fixture',
      streaming:true,backend:'indextts',instance_id:healthy ? process.env.CORTICO_TTS_MANAGED_TOKEN : 'unhealthy'}));})
      .listen(Number(process.env.CORTI_TTS_PORT),'127.0.0.1'); ${extra}`;
}

function service(value: number, config: Partial<TtsServiceConfig> = {}, script = fixture()): TtsService {
  const manager = new TtsService({ url: `http://127.0.0.1:${value}`, log: nullLogger(),
    config: { kind: 'indextts', healthIntervalMs: 30, startupTimeoutMs: 1000,
      restartDelayMs: 30, maxRestarts: 2, ...config },
    voxcpm2: { runtimeDir: () => '', serverExe: () => '', modelsDir: '', port: value, log: nullLogger() },
    launchOverride: { command: process.execPath, args: ['-e', script] },
  });
  services.push(manager);
  return manager;
}

async function existing(value: number, health: object): Promise<Server> {
  const server = createServer((_, response) => response.end(JSON.stringify(health)));
  servers.push(server);
  await new Promise<void>(resolve => server.listen(value, '127.0.0.1', resolve));
  return server;
}

describe('TtsService lifecycle and ownership', () => {
  it('coalesces concurrent starts, authenticates its child and waits for stop', async () => {
    const manager = service(await port());
    const [first, second] = await Promise.all([manager.start(), manager.start()]);
    expect(first).toMatchObject({ phase: 'running', ownership: 'owned', restarts: 0 });
    expect(first.pid).toBe(second.pid);
    expect(first.health?.instance_id).toBeTruthy();
    const pid = first.pid!;
    await manager.stop();
    expect(manager.state()).toMatchObject({ phase: 'stopped', pid: null, ownership: 'none' });
    expect(() => process.kill(pid, 0)).toThrow();
  });

  it('attaches to an existing adapter without spawning or terminating it', async () => {
    const value = await port();
    await existing(value, { status: 'ok', version: 'fixture', streaming: true, backend: 'indextts', instance_id: 'another-owner' });
    const manager = service(value);
    expect(await manager.start()).toMatchObject({ phase: 'running', pid: null, ownership: 'external' });
    await manager.stop();
    expect(await manager.probe()).toBe(true);
  });

  it('rejects an unrelated healthy HTTP service without spawning a child', async () => {
    const value = await port();
    await existing(value, { status: 'ok', model: 'other-service' });
    const manager = service(value);
    expect(await manager.start()).toMatchObject({ phase: 'error', pid: null, ownership: 'none' });
    expect(manager.state().detail).toContain('占用');
    await manager.stop();
    expect((await fetch(`http://127.0.0.1:${value}/health`)).ok).toBe(true);
  });

  it('a child that serves the wrong instance credential is never declared ready', async () => {
    const manager = service(await port(), { maxRestarts: 0 }, fixture().replace('process.env.CORTICO_TTS_MANAGED_TOKEN', "'wrong-owner'"));
    await manager.start();
    await waitFor(() => manager.state().phase === 'error');
    expect(manager.state().ownership).toBe('none');
  });

  it('stopping during startup cancels the startup and automatic recovery', async () => {
    const manager = service(await port(), {}, 'process.stdin.resume(); setInterval(()=>{},1000)');
    const start = manager.start();
    await waitFor(() => manager.state().pid !== null);
    const stop = manager.stop();
    await Promise.all([start, stop]);
    await new Promise(resolve => setTimeout(resolve, 150));
    expect(manager.state()).toMatchObject({ phase: 'stopped', pid: null, restarts: 0 });
  });

  it('recovers an unexpected child exit with a new process', async () => {
    const manager = service(await port());
    const first = await manager.start();
    process.kill(first.pid!);
    await waitFor(() => manager.state().phase === 'running' && manager.state().pid !== first.pid);
    expect(manager.state()).toMatchObject({ ownership: 'owned', restarts: 1 });
  });

  it('recovers a live child after three failed health credentials', async () => {
    const value = await port();
    const manager = service(value);
    const first = await manager.start();
    await fetch(`http://127.0.0.1:${value}/invalidate`);
    await waitFor(() => manager.state().phase === 'running' && manager.state().pid !== first.pid);
    expect(manager.state()).toMatchObject({ ownership: 'owned', restarts: 1 });
    expect(() => process.kill(first.pid!, 0)).toThrow();
  });

  it('bounds repeated startup failures and manual stop cancels pending retries', async () => {
    const manager = service(await port(), {}, 'process.exit(3)');
    await manager.start();
    await waitFor(() => manager.state().detail?.includes('上限') === true);
    expect(manager.state()).toMatchObject({ phase: 'error', pid: null, restarts: 2 });
    await manager.stop();
    await new Promise(resolve => setTimeout(resolve, 150));
    expect(manager.state().phase).toBe('stopped');
  });

  it('external mode does not launch even when auto-start is enabled', async () => {
    const manager = service(await port(), { kind: 'external', autoStart: true });
    await manager.autoStart();
    expect(manager.state()).toMatchObject({ kind: 'external', phase: 'error', pid: null, restarts: 0 });
  });

  it('auto-start is opt-in and an invalid URL is reported in service state', async () => {
    const manager = service(await port());
    await manager.autoStart();
    expect(manager.state().phase).toBe('stopped');
    const invalid = new TtsService({ url: 'invalid', config: { kind: 'indextts' }, log: nullLogger(),
      voxcpm2: { runtimeDir: () => '', serverExe: () => '', modelsDir: '', port: 1, log: nullLogger() } });
    services.push(invalid);
    expect(await invalid.start()).toMatchObject({ phase: 'error', pid: null });
  });
});
