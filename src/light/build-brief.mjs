import { GatedLoopError } from '../core/errors.mjs';
import { normalizeSignals } from '../mode/signals.mjs';
import { normalizeTestArgv } from '../baseline/test-command.mjs';

const RISK_RULES = Object.freeze([
  ['loadBearing', false, '- No load-bearing file changes.'],
  ['breaking', false, '- No breaking changes.'],
  ['migrations', false, '- No migrations (schema, data, config, storage, API-version, or dependency).'],
  ['dependencyChange', false, '- No dependency changes.'],
  ['newDependency', false, '- No new dependencies.'],
  ['externalContract', false, '- No external contract changes.'],
  ['permissions', false, '- No permission changes.'],
  ['authentication', false, '- No authentication changes.'],
  ['stateMachine', false, '- No state-machine changes.'],
  ['transaction', false, '- No transaction changes.'],
  ['concurrency', false, '- No concurrency changes.'],
  ['idempotency', false, '- No idempotency changes.'],
  ['unresolvedOptions', 0, '- No unresolved multi-option decisions.'],
  ['thresholdDecision', false, '- No threshold decisions.'],
  ['fileCountExceeded', false, '- No more than three files will be changed.'],
  ['impactKnown', true, '- Impact is known.'],
]);

export const FULL_RISK_CONFIRMATIONS = Object.freeze(RISK_RULES.map(([, , text]) => text));

function fail(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}

function containsMarkdownHeading(text) {
  return text.split('\n').some((line) => {
    let content = line;
    let previous;
    do {
      previous = content;
      content = content.replace(/^\s{0,3}>\s?/, '').replace(/^\s{0,3}(?:[-+*]|\d+[.)])\s+/, '');
    } while (content !== previous);
    return /^\s{0,3}#{1,6}\s/.test(content) || /^\s{0,3}(?:=+|-+)\s*$/.test(content);
  });
}

function validateText(value, field) {
  if (typeof value !== 'string' || value.trim().length === 0) fail('LIGHT_BRIEF_INVALID', `${field} must be nonempty`, { field });
  const text = value.replace(/\r\n?/g, '\n').trim();
  if (/[\u0000-\u0009\u000B\u000C\u000E-\u001F\u007F-\u009F]/.test(text)) {
    fail('LIGHT_BRIEF_INVALID', `${field} contains control characters`, { field });
  }
  if (/\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?/i.test(text)) {
    fail('LIGHT_BRIEF_PLACEHOLDER', `${field} contains placeholder content`, { field });
  }
  if (containsMarkdownHeading(text)) {
    fail('LIGHT_BRIEF_INVALID', `${field} cannot contain headings`, { field });
  }
  return text;
}

function normalizeList(value, field) {
  const values = Array.isArray(value) ? value : [value];
  if (values.length === 0) fail('LIGHT_BRIEF_INVALID', `${field} must be nonempty`, { field });
  return values.map((entry, index) => {
    const text = validateText(entry, `${field}[${index}]`);
    if (/[\u0000-\u001F\u007F]/.test(text)) fail('LIGHT_BRIEF_INVALID', `${field} entries must be single-line`, { field });
    return text;
  });
}

function normalizeScope(value) {
  const entries = normalizeList(value, 'scope');
  let normalized;
  try { normalized = normalizeSignals({ modifiesFiles: entries, writesFiles: true, impactKnown: true }); }
  catch { fail('LIGHT_BRIEF_INVALID', 'Scope must contain safe relative file paths', { field: 'scope' }); }
  if (normalized.loadBearing || normalized.modifiesFiles.length > 3) {
    fail('LIGHT_BRIEF_FULL_RISK', 'Scope requires Full mode', { field: 'scope' });
  }
  return normalized.modifiesFiles;
}

function normalizeAcceptance(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('LIGHT_BRIEF_INVALID', 'Acceptance must be structured');
  const outcomes = normalizeList(value.outcomes ?? value.observableOutcomes ?? [], 'acceptance.outcomes');
  if (!Array.isArray(value.testCommands) || value.testCommands.length === 0) {
    fail('LIGHT_BRIEF_INVALID', 'Acceptance requires at least one JSON argv test command');
  }
  const testCommands = value.testCommands.map((command, index) => {
    const argv = normalizeTestArgv(command);
    if (!argv) fail('LIGHT_BRIEF_INVALID', 'Acceptance test command must be a safe nonempty argv array', { index });
    return argv;
  });
  return { outcomes, testCommands };
}

function validateRisks(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('LIGHT_BRIEF_INVALID', 'Risks must be structured');
  for (const [field, safeValue] of RISK_RULES) {
    if (!Object.hasOwn(value, field)) {
      fail('LIGHT_BRIEF_RISK_CONFIRMATION_REQUIRED', `Risks must explicitly confirm ${field}`, { field });
    }
    if (value[field] !== safeValue) fail('LIGHT_BRIEF_FULL_RISK', `${field} requires Full mode`, { field });
  }
}

export function validateLightBrief(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) fail('LIGHT_BRIEF_INVALID', 'Light brief must be a mapping');
  const goal = validateText(input.goal, 'goal');
  const scope = normalizeScope(input.scope ?? []);
  const acceptance = normalizeAcceptance(input.acceptance);
  validateRisks(input.risks);
  return { goal, scope, acceptance, risks: Object.fromEntries(RISK_RULES.map(([field]) => [field, input.risks[field]])) };
}

export function buildLightBrief(input) {
  const { goal, scope, acceptance } = validateLightBrief(input);
  return [
    '## Goal',
    goal,
    '',
    '## Scope',
    ...scope.map((entry) => `- ${entry}`),
    '',
    '## Acceptance',
    ...acceptance.outcomes.map((entry) => `- ${entry}`),
    ...acceptance.testCommands.map((argv) => `- Test command: ${JSON.stringify(argv)}`),
    '',
    '## Risks',
    ...FULL_RISK_CONFIRMATIONS,
    '',
  ].join('\n');
}

export const renderLightBrief = buildLightBrief;
