import { sha256Bytes } from '../core/hash.mjs';
import { renderDevelopmentHandoff } from '../handoff/render.mjs';
import { DEVELOPMENT_HANDOFF_FILE } from '../handoff/files.mjs';

const SCHEMA_VERSION = 1;
const GENERATOR_VERSION = 1;

function json(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}
function numbered(prefix, index) {
  return `${prefix}-${String(index + 1).padStart(3, '0')}`;
}

export function buildLightArtifacts({ task, reviewer, brief, markdown } = {}) {
  const baselineFingerprint = sha256Bytes(Buffer.from(markdown, 'utf8'));
  const acceptanceEntries = brief.acceptance.outcomes.map((expectedResult, index) => ({
    id: numbered('A', index),
    requirementIds: ['R-001'],
    expectedResult,
    trace: { file: 'light-brief.md', section: 'Acceptance', index: index + 1 },
  }));
  const taskEntries = [{
    id: 'T-001',
    requirementIds: ['R-001'],
    acceptanceIds: acceptanceEntries.map(({ id }) => id),
    text: brief.goal,
    scope: [...brief.scope],
    trace: { file: 'light-brief.md', section: 'Goal' },
  }];
  const acceptance = {
    schemaVersion: SCHEMA_VERSION,
    generatorVersion: GENERATOR_VERSION,
    baselineFingerprint,
    acceptance: acceptanceEntries,
  };
  const tasks = {
    schemaVersion: SCHEMA_VERSION,
    generatorVersion: GENERATOR_VERSION,
    baselineFingerprint,
    tasks: taskEntries,
  };
  const decisionLog = [
    '# Decision Log',
    '',
    `- Host review: ${reviewer}.`,
    '- User confirmation: confirmed.',
    '- No additional decisions recorded.',
    '',
  ].join('\n');
  const handoff = renderDevelopmentHandoff({
    task,
    reviewer,
    authorityFile: 'light-brief.md',
    scope: brief.scope,
    tasks: taskEntries,
    acceptance: acceptanceEntries,
    testCommands: brief.acceptance.testCommands,
  });
  return {
    acceptance,
    tasks,
    files: {
      'light-brief.md': markdown,
      'acceptance.json': json(acceptance),
      'tasks.json': json(tasks),
      'decision-log.md': decisionLog,
      [DEVELOPMENT_HANDOFF_FILE]: handoff,
    },
  };
}
