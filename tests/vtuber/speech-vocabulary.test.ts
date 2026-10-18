import { describe, it, expect } from 'vitest';
import { SpeechVocabulary, loadSpeechVocabulary } from '../../src/speech-vocabulary.ts';
import { ScriptParser, type SpeechPiece } from '../../src/parser.ts';
import { EXAMPLE_PACK_DIR, loadPack } from '../../src/pack.ts';
import { TtsClient, pcm16ToWav } from '../../src/tts.ts';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const vocabulary = new SpeechVocabulary({ schema: 'speech-vocabulary-v1', terms: {
  sculk: '幽匿块', 'minecraft:sculk': '幽匿块', sculk_sensor: '幽匿感测体',
  iron_pickaxe: '铁镐', 'Iron Pickaxe': '铁镐', 'diamond sword': '钻石剑',
  cobbled_deepslate: '深板岩圆石', Stone: '石头', "Dragon's Breath": '龙息',
} });
const pack = loadPack(EXAMPLE_PACK_DIR);
const original = 'NEKO，带上 iron_pickaxe 和 diamond sword，去找 sculk 方块，路上收点 cobbled_deepslate，就出发喵。';
const chinese = 'NEKO，带上 铁镐 和 钻石剑，去找 幽匿块，路上收点 深板岩圆石，就出发喵。';

describe('Agent speech vocabulary fallback', () => {
  it('uses longest complete IDs/display names without guessing unknown namespaces or word fragments', () => {
    expect(vocabulary.render(original).text).toBe(chinese);
    expect(vocabulary.render("找SCULK_SENSOR与minecraft:sculk，拿Dragon's Breath。").text)
      .toBe('找幽匿感测体与幽匿块，拿龙息。');
    const untouched = "mod:sculk sculking sculk_sensor_extra sculk.png mystery_item Stone's";
    expect(vocabulary.render(untouched).text).toBe(untouched);
  });
  it('preserves names, mentions, commands, URLs, paths and explicit code', () => {
    const source = '玩家 Stone、昵称 sculk、@Stone、NEKO、<sculk|custom>、`sculk`、https://host/sculk、C:\\sculk、/give sculk。拿sculk。';
    expect(vocabulary.render(source).text).toBe(source.replace('拿sculk。', '拿幽匿块。'));
    const names = new SpeechVocabulary({ schema: 'speech-vocabulary-v1', terms: { stone: '石头' }, preserve: ['Stone'] });
    expect(names.render('Stone和stone').text).toBe('Stone和stone');
  });
  it('leaves the original call untouched and is idempotent', () => {
    const args = { script: original, operation: 'craft', input: { item: 'iron_pickaxe' } };
    const saved = JSON.stringify(args);
    expect(vocabulary.render(args.script).text).toBe(chinese);
    expect(JSON.stringify(args)).toBe(saved);
    expect(vocabulary.render(chinese).text).toBe(chinese);
  });
  it('renders fragmented scripts before subtitle/audio splitting and remaps inline action anchors', () => {
    const source = '(语气：温柔)拿iron_pickaxe<微笑>去找sculk。';
    const result: SpeechPiece[] = [];
    const parser = new ScriptParser({ onBeat() {}, onEnd() {}, onSpeech(_beat, piece) { result.push(piece); } },
      pack, false, vocabulary.render);
    for (const char of source) parser.feed(char);
    parser.end();
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('拿铁镐 去找幽匿块。');
    expect(result[0].anchors[0].charOffset).toBe(3);
    expect(result[0].instructions).toBe('温柔');
  });
  it('makes exactly one ordinary remote request with final Chinese input, no vocabulary or extra translation request', async () => {
    const requests: Array<Record<string, unknown>> = [];
    const client = new TtsClient({ url: 'http://fixture.invalid',
      transport: { protocol: 'openai-chunked', model: 'speech', voice: 'chosen', sessionId: 'stable' },
      fetchImpl: (async (_url, init) => {
        requests.push(JSON.parse(String(init?.body)));
        return new Response(new Uint8Array(pcm16ToWav([new Uint8Array(9600)], 48000)).buffer, { headers: { 'content-type': 'audio/wav' } });
      }) as typeof fetch });
    const rendered = vocabulary.render(original);
    const piece = await client.synth(rendered.text);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toEqual({ model: 'speech', input: chinese, voice: 'chosen', speed: 1, response_format: 'wav' });
    expect(piece.text).toBe(chinese);
  });
  it('loads only an explicit versioned local file and exposes its evidence hash', () => {
    const root = mkdtempSync(join(tmpdir(), 'speech-vocabulary-'));
    try {
      const path = join(root, 'terms.json');
      writeFileSync(path, JSON.stringify({ schema: 'speech-vocabulary-v1', terms: { sculk: '幽匿块' } }));
      const loaded = loadSpeechVocabulary(path);
      expect(loaded.vocabulary.render('找sculk').text).toBe('找幽匿块');
      expect(loaded.sha256).toMatch(/^[a-f0-9]{64}$/);
      const invalidTerms: Array<Record<string, string>> = [{}, { sculk: '[laughing]' }, { sculk: '甲', SCULK: '乙' }];
      for (const terms of invalidTerms)
        expect(() => new SpeechVocabulary({ schema: 'speech-vocabulary-v1', terms })).toThrow();
      writeFileSync(path, JSON.stringify({ schema: 'tts-spoken-terms-v1', terms: { sculk: '幽匿块' } }));
      expect(() => loadSpeechVocabulary(path)).toThrow('Unsupported');
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});
