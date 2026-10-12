import { describe, expect, it } from 'vitest';
import { VtuberWorld, VTUBER_PANEL_DECLS } from '../../src/world.ts';

describe('sender metadata', () => {
  it('returns cached queue metadata without probing TTS or starting a service', () => {
    const world = new VtuberWorld();
    const metadata = world.ttsConsole().metrics();
    expect(metadata).toMatchObject({ schema: 'vtuber-sender-metrics-v1', pending_items: null, playing: null });
    expect(typeof metadata.time).toBe('number');
    expect(Object.keys(metadata).sort()).toEqual(['pending_items', 'playing', 'schema', 'time']);
    expect(VTUBER_PANEL_DECLS.find(panel => panel.id === 'tts')?.getMethods).toContain('metrics');
  });
});
