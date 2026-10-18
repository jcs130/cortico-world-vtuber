/** Explicit Agent-side vocabulary export using the independent core public API. No TTS server modification. */
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const [consumerRoot, output, version = '1.21.1'] = process.argv.slice(2);
if (!consumerRoot || !output) throw Error('Usage: node export-minecraft-speech-vocabulary.mjs <packed-consumer-root> <output.json> [game-version]');
const require = createRequire(resolve(consumerRoot, 'package.json'));
const entry = require.resolve('agent-mc-world/extension');
const { zhName } = await import(pathToFileURL(entry).href);
const core = JSON.parse(await readFile(require.resolve('agent-mc-world/package.json'), 'utf8'));
const mcPackage = require('minecraft-data/package.json');
const registry = require('minecraft-data')(version);
const candidates = new Map();
const uncovered = [];
for (const item of [...registry.itemsArray, ...registry.blocksArray]) {
  const translated = zhName(item.name);
  if (!/[\u3400-\u9fff]/u.test(translated) || /[A-Za-z_]/u.test(translated)) {
    uncovered.push(item.name); continue;
  }
  for (const alias of [item.name, 'minecraft:' + item.name, item.displayName]) {
    const key = alias.toLowerCase();
    if (!candidates.has(key)) candidates.set(key, new Set());
    candidates.get(key).add(translated);
  }
}
const ambiguous = [...candidates].filter(([, values]) => values.size !== 1).map(([key]) => key);
const terms = Object.fromEntries([...candidates].filter(([, values]) => values.size === 1)
  .sort(([a], [b]) => a.localeCompare(b, 'en')).map(([key, values]) => [key, [...values][0]]));
const catalog = { schema: 'speech-vocabulary-v1', language: 'zh-CN',
  source: { package: core.name, version: core.version, publicExport: 'agent-mc-world/extension#zhName',
    registryPackage: 'minecraft-data', registryVersion: mcPackage.version, gameVersion: version }, terms };
await mkdir(dirname(resolve(output)), { recursive: true });
const bytes = Buffer.from(JSON.stringify(catalog, null, 2) + '\n');
await writeFile(output, bytes);
await writeFile(output + '.report.json', JSON.stringify({ entries: Object.keys(terms).length,
  ambiguousExcluded: ambiguous, uncoveredIds: [...new Set(uncovered)].sort(),
  source: catalog.source, sha256: createHash('sha256').update(bytes).digest('hex') }, null, 2));
console.log(JSON.stringify({ output: resolve(output), entries: Object.keys(terms).length,
  source: catalog.source, sha256: createHash('sha256').update(bytes).digest('hex'),
  examples: Object.fromEntries(['sculk', 'sculk_sensor', 'iron_pickaxe', 'diamond sword', 'oak_log'].map(k => [k, terms[k]])),
  ambiguousExcluded: ambiguous.length, uncoveredIds: new Set(uncovered).size }));
