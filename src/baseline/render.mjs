import { GatedLoopError } from '../core/errors.mjs';
import { BASELINE_GENERATOR_VERSION, BASELINE_SCHEMA_VERSION } from './parse.mjs';

function block(value, field) {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new GatedLoopError('BASELINE_MODEL_INVALID', `${field} must be nonempty`);
  }
  return value.replace(/\r\n?/g, '\n').replace(/^\n+|\n+$/g, '');
}

function list(value, field) {
  if (!Array.isArray(value) || value.length === 0) throw new GatedLoopError('BASELINE_MODEL_INVALID', `${field} must be nonempty`);
  return value;
}

export function renderFullBaseline(model) {
  if (!model || typeof model !== 'object' || Array.isArray(model)
      || model.schemaVersion !== BASELINE_SCHEMA_VERSION || model.generatorVersion !== BASELINE_GENERATOR_VERSION) {
    throw new GatedLoopError('BASELINE_MODEL_INVALID', 'Baseline model has an unsupported schema');
  }
  const requirements = list(model.requirements, 'requirements').flatMap((entry) => [
    `### ${entry.id} ${entry.title}`,
    block(entry.text, entry.id),
    '',
  ]);
  const acceptance = list(model.acceptance, 'acceptance').flatMap((entry) => [
    `### ${entry.id} [${entry.requirementIds.join(',')}]`,
    block(entry.expectedResult, entry.id),
    '',
  ]);
  const tasks = list(model.tasks, 'tasks').map((entry) => (
    `- [ ] ${entry.id} [${entry.requirementIds.join(',')}] [${entry.acceptanceIds.join(',')}] ${entry.text}`
  ));
  const testCommands = list(model.testCommands, 'testCommands').map((argv) => `- ${JSON.stringify(argv)}`);
  return [
    '# Development Baseline',
    '',
    '## Goal',
    block(model.goal, 'goal'),
    '',
    '## Background',
    block(model.background, 'background'),
    '',
    '## Scope',
    block(model.scope, 'scope'),
    '',
    '## Non-Goals',
    block(model.nonGoals, 'nonGoals'),
    '',
    '## Requirements',
    ...requirements,
    '## Acceptance',
    ...acceptance,
    '## Tasks',
    ...tasks,
    '',
    '## Risks',
    block(model.risks, 'risks'),
    '',
    '## Test Commands',
    ...testCommands,
    '',
    '## Decisions',
    block(model.decisions, 'decisions'),
    '',
  ].join('\n');
}

export const renderBaseline = renderFullBaseline;
