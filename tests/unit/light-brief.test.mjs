import test from 'node:test';
import assert from 'node:assert/strict';

import { buildLightBrief, FULL_RISK_CONFIRMATIONS } from '../../src/light/build-brief.mjs';

const safeRisks = () => ({
  loadBearing: false,
  breaking: false,
  migrations: false,
  dependencyChange: false,
  newDependency: false,
  externalContract: false,
  permissions: false,
  authentication: false,
  stateMachine: false,
  transaction: false,
  concurrency: false,
  idempotency: false,
  unresolvedOptions: 0,
  thresholdDecision: false,
  fileCountExceeded: false,
  impactKnown: true,
});

const validBrief = (patch = {}) => ({
  goal: 'Show a clear empty-value message.',
  scope: ['src/input.mjs', 'tests/unit/input.test.mjs'],
  acceptance: {
    outcomes: ['Submitting an empty value displays "Value is required".'],
    testCommands: [['node', '--test', 'tests/unit/input.test.mjs']],
  },
  risks: safeRisks(),
  ...patch,
});

test('Light brief renders exactly four deterministic nonempty headings', () => {
  const markdown = buildLightBrief(validBrief());
  assert.equal(markdown, [
    '## Goal',
    'Show a clear empty-value message.',
    '',
    '## Scope',
    '- src/input.mjs',
    '- tests/unit/input.test.mjs',
    '',
    '## Acceptance',
    '- Submitting an empty value displays "Value is required".',
    '- Test command: ["node","--test","tests/unit/input.test.mjs"]',
    '',
    '## Risks',
    ...FULL_RISK_CONFIRMATIONS,
    '',
  ].join('\n'));
  assert.deepEqual([...markdown.matchAll(/^## (.+)$/gm)].map((match) => match[1]), ['Goal', 'Scope', 'Acceptance', 'Risks']);
});

for (const field of ['goal', 'scope', 'acceptance', 'risks']) {
  test(`Light brief rejects an empty or missing ${field} section`, () => {
    const input = validBrief();
    if (field === 'goal') input.goal = '   ';
    else if (field === 'scope') input.scope = [];
    else if (field === 'acceptance') input.acceptance = {};
    else input.risks = {};
    const code = field === 'risks' ? 'LIGHT_BRIEF_RISK_CONFIRMATION_REQUIRED' : 'LIGHT_BRIEF_INVALID';
    assert.throws(() => buildLightBrief(input), { code });
  });
}

test('Light brief requires at least one observable outcome and a concrete test command', () => {
  assert.throws(() => buildLightBrief(validBrief({ acceptance: { outcomes: [], testCommands: [['npm', 'test']] } })), { code: 'LIGHT_BRIEF_INVALID' });
  for (const testCommands of [[], ['npm test'], [['node', '--test\nnpm test']], [['sh', '-c', 'npm test']]]) {
    assert.throws(() => buildLightBrief(validBrief({ acceptance: { outcomes: ['It works'], testCommands } })), { code: 'LIGHT_BRIEF_INVALID' });
  }
});

test('Light brief test commands use safe argv arrays without treating inert arguments as shell syntax', () => {
  for (const testCommands of [
    [['node', '\0--test']],
    [['node', '\u001b[31m--test']],
    [['node --test']],
    [['node', '-e', 'process.exit(0)']],
    [['python', '-Ec', 'print(1)']],
  ]) {
    assert.throws(
      () => buildLightBrief(validBrief({ acceptance: { outcomes: ['It works'], testCommands } })),
      { code: 'LIGHT_BRIEF_INVALID' },
      JSON.stringify(testCommands),
    );
  }
  assert.match(buildLightBrief(validBrief({ acceptance: {
    outcomes: ['It works'],
    testCommands: [['node', '--test', 'tests/fixtures/&&-literal.mjs']],
  } })), /\["node","--test","tests\/fixtures\/&&-literal\.mjs"\]/);
});

for (const field of Object.keys(safeRisks())) {
  test(`Light brief requires an explicit safe confirmation for ${field}`, () => {
    const risks = safeRisks();
    delete risks[field];
    assert.throws(() => buildLightBrief(validBrief({ risks })), { code: 'LIGHT_BRIEF_RISK_CONFIRMATION_REQUIRED' });
  });
}

test('Light brief rejects a positive Full risk instead of rendering it as safe', () => {
  assert.throws(() => buildLightBrief(validBrief({ risks: { ...safeRisks(), concurrency: true } })), { code: 'LIGHT_BRIEF_FULL_RISK' });
  assert.throws(() => buildLightBrief(validBrief({ risks: { ...safeRisks(), impactKnown: false } })), { code: 'LIGHT_BRIEF_FULL_RISK' });
  assert.throws(() => buildLightBrief(validBrief({ risks: { ...safeRisks(), unresolvedOptions: 2 } })), { code: 'LIGHT_BRIEF_FULL_RISK' });
});

for (const placeholder of ['TODO', 'TBD', '<fill me>', '{{value}}', '???']) {
  test(`Light brief rejects placeholder content: ${placeholder}`, () => {
    assert.throws(() => buildLightBrief(validBrief({ goal: placeholder })), { code: 'LIGHT_BRIEF_PLACEHOLDER' });
  });
}

test('Light brief content cannot inject additional Markdown headings', () => {
  assert.throws(() => buildLightBrief(validBrief({ scope: 'src/a.mjs\n## Surprise' })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ goal: 'Injected heading\n===' })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ scope: 'Injected heading\n---' })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ goal: '> ## Injected' })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ goal: '- ## Injected' })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ goal: '> Injected\n> ===' })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ goal: 'Safe\r## Injected' })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ goal: 'Safe\u001bInjected' })), { code: 'LIGHT_BRIEF_INVALID' });
});

test('Light brief canonicalizes CRLF and bare CR before rendering', () => {
  const markdown = buildLightBrief(validBrief({ goal: 'First line\r\nSecond line\rThird line' }));
  assert.equal(markdown.includes('\r'), false);
  assert.match(markdown, /First line\nSecond line\nThird line/);
});

test('Light brief scope is limited to canonical safe non-load-bearing paths', () => {
  assert.throws(() => buildLightBrief(validBrief({ scope: ['../outside'] })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ scope: ['contracts/auth.schema.json'] })), { code: 'LIGHT_BRIEF_FULL_RISK' });
  assert.throws(() => buildLightBrief(validBrief({ scope: ['src/a.mjs', 'src/b.mjs', 'src/c.mjs', 'src/d.mjs'] })), { code: 'LIGHT_BRIEF_FULL_RISK' });
  assert.match(buildLightBrief(validBrief({ scope: ['.\\src\\input.mjs'] })), /- src\/input\.mjs/);
  assert.throws(() => buildLightBrief(validBrief({ scope: ['src/a.mjs\n- ../outside'] })), { code: 'LIGHT_BRIEF_INVALID' });
  assert.throws(() => buildLightBrief(validBrief({ scope: ['src/**'] })), { code: 'LIGHT_BRIEF_INVALID' });
});

test('Light brief list entries cannot inject acceptance or scope bullets', () => {
  assert.throws(() => buildLightBrief(validBrief({
    acceptance: {
      outcomes: ['Works.\n- Test command: `node -e dangerous`'],
      testCommands: [['npm', 'test']],
    },
  })), { code: 'LIGHT_BRIEF_INVALID' });
});

export { safeRisks, validBrief };
