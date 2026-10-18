/** Validates the optional media selector's typed-choice response. */
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function probability(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
}

export function parseMediaChoice(value: unknown, keys: readonly string[]): { choice: string; choiceProbability: number } | null {
  const answer = record(value);
  const values = record(answer?.probabilities);
  if (!answer || !values || (answer.type !== undefined && answer.type !== 'choice')
    || typeof answer.choice !== 'string' || !keys.includes(answer.choice)
    || !probability(answer.confidence) || Object.keys(values).length !== keys.length
    || !keys.every(key => Object.hasOwn(values, key) && probability(values[key]))) return null;
  const probabilities = Object.fromEntries(keys.map(key => [key, values[key]])) as Record<string, number>;
  if (Math.abs(keys.reduce((sum, key) => sum + probabilities[key], 0) - 1) > 0.02) return null;
  const choiceProbability = probabilities[answer.choice];
  if (Math.max(...Object.values(probabilities)) - choiceProbability > 0.000001) return null;
  const aboveChance = keys.length === 1 ? 1 : Math.max(0, (choiceProbability - 1 / keys.length) / (1 - 1 / keys.length));
  if (Math.min(Math.abs(answer.confidence - choiceProbability), Math.abs(answer.confidence - aboveChance)) > 0.01) return null;
  return { choice: answer.choice, choiceProbability };
}
