import { describe, it, expect, vi } from 'vitest';
import { CommentaryRuntimeGate } from '../../src/commentary-runtime.ts';

describe('commentary lifecycle status', () => {
  it('distinguishes ordinary idle from explicit pause; a stale or missing observation never authorizes a new model call', async () => {
    let now=1000, status: unknown={loop:{running:true,paused:false}};
    const invalidate=vi.fn();
    const get=vi.fn(async()=>status);
    const gate=new CommentaryRuntimeGate({url:()=> 'http://127.0.0.1:1000/api/status',get,invalidate,now:()=>now});
    expect(gate.active(true)).toBe(false);
    gate.tick(); await Promise.resolve(); await Promise.resolve();
    expect(gate.active(false)).toBe(true); // foreground waits, autonomous live commentary remains available
    now+=6000; expect(gate.active(true)).toBe(false);
    status={loop:{running:true,paused:true}};
    gate.tick(); await Promise.resolve(); await Promise.resolve();
    expect(gate.active(true)).toBe(false); expect(invalidate).toHaveBeenCalledOnce();
    status={loop:{running:true}}; gate.tick(); await Promise.resolve(); await Promise.resolve();
    expect(gate.active(true)).toBe(false);
    get.mockRejectedValueOnce(new Error('offline')); gate.tick(); await Promise.resolve(); await Promise.resolve();
    expect(gate.active(true)).toBe(false);
  });

  it('does not accept a response from a previous lifecycle, and keeps a single pending status read', async () => {
    let resolve!: (status:unknown)=>void;
    const get=vi.fn(()=>new Promise<unknown>(r=>{resolve=r}));
    const gate=new CommentaryRuntimeGate({url:()=> 'http://localhost:1000/api/status',get,invalidate:vi.fn()});
    gate.tick(); gate.tick(); expect(get).toHaveBeenCalledOnce();
    gate.stop(); gate.resume(); resolve({loop:{running:true,paused:false}});
    await Promise.resolve(); await Promise.resolve(); expect(gate.active(true)).toBe(false);
  });

  it('performs no extra I/O when the adapter is unconfigured, using only the conservative run phase', () => {
    const get=vi.fn(); const gate=new CommentaryRuntimeGate({url:()=>'',get,invalidate:vi.fn()});
    gate.tick(); expect(get).not.toHaveBeenCalled();
    expect(gate.active(false)).toBe(false); expect(gate.active(true)).toBe(true);
  });
});
