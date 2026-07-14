import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

import { runCli } from '../../src/cli/main.mjs';
import { startTask } from '../../src/commands/start.mjs';
import { prepareFullBaseline } from '../../src/full/prepare.mjs';
import { freezeFullBaseline } from '../../src/full/freeze.mjs';
import { validFullBaseline } from '../helpers/full-baseline.mjs';

function git(root, ...args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8', shell: false });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

async function initializeRepository(root, name, failingTest = false) {
  await mkdir(path.join(root, 'src'), { recursive: true });
  await mkdir(path.join(root, 'tests'), { recursive: true });
  await writeFile(path.join(root, '.gitignore'), '.ai-dev-loop/\n');
  await writeFile(path.join(root, 'src', `${name}.mjs`), `export const value = "old-${name}";\n`);
  await writeFile(path.join(root, 'tests', `${name}.test.mjs`), [
    "import test from 'node:test';",
    "import assert from 'node:assert/strict';",
    `test('${name}', () => assert.equal(1, ${failingTest ? 2 : 1}));`,
    '',
  ].join('\n'));
  await writeFile(path.join(root, 'tests', `${name}-check.mjs`), failingTest
    ? 'throw new Error("gate failure");\n'
    : 'process.stdout.write("gate pass\\n");\n');
  git(root, 'init');
  git(root, 'config', 'user.email', 'gate@example.invalid');
  git(root, 'config', 'user.name', 'Gate Test');
  git(root, 'add', '.');
  git(root, 'commit', '-m', 'fixture');
}

function baseline() {
  return validFullBaseline({
    Scope: '- Change the provider contract implementation.\n- Change the consumer integration.',
    Requirements: [
      '### R-001 Update provider',
      'The provider workspace exposes the required behavior.',
      '',
      '### R-002 Update consumer',
      'The consumer workspace uses the provider behavior.',
    ].join('\n'),
    Acceptance: [
      '### A-001 [R-001]',
      'The provider test passes.',
      '',
      '### A-002 [R-002]',
      'The consumer test passes after the provider gate.',
    ].join('\n'),
    Tasks: [
      '- [ ] T-001 [R-001] [A-001] Update the provider workspace.',
      '- [ ] T-002 [R-002] [A-002] Update the dependent consumer workspace.',
    ].join('\n'),
    'Test Commands': '- ["node","tests/provider-check.mjs"]\n- ["node","tests/consumer-check.mjs"]',
  });
}

async function fixture(t, { failingProvider = false, cycle = false } = {}) {
  const parent = await mkdtemp(path.join(tmpdir(), 'gated-loop-multi-'));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const consumer = path.join(parent, 'consumer-service');
  const provider = path.join(parent, 'provider-service');
  await mkdir(consumer); await mkdir(provider);
  await initializeRepository(consumer, 'consumer');
  await initializeRepository(provider, 'provider', failingProvider);
  if (failingProvider) assert.match(await readFile(path.join(provider, 'tests', 'provider-check.mjs'), 'utf8'), /gate failure/);
  await mkdir(path.join(consumer, 'requirements'));
  await writeFile(path.join(consumer, 'requirements', 'baseline.md'), baseline());
  git(consumer, 'add', 'requirements/baseline.md');
  git(consumer, 'commit', '-m', 'add baseline');

  const task = 'multi-service-gate';
  await startTask({
    root: consumer, task, hostRuntime: 'codex',
    signals: {
      description: 'Update provider and dependent consumer', writesFiles: true, impactKnown: true,
      modifiesFiles: ['src/consumer.mjs', 'contracts/provider.mjs'], externalContract: true,
    },
  });
  await prepareFullBaseline({ root: consumer, task, baseline: 'requirements/baseline.md' });
  await freezeFullBaseline({ root: consumer, task, confirmed: true });
  const taskDir = path.join(consumer, '.ai-dev-loop', task);
  const roundDir = path.join(taskDir, 'rounds', 'round-01');
  await mkdir(roundDir, { recursive: true });
  const state = JSON.parse(await readFile(path.join(taskDir, 'state.json'), 'utf8'));
  const workspaces = [
    {
      id: 'provider-service', root: provider, branch: git(provider, 'rev-parse', '--abbrev-ref', 'HEAD'),
      baseCommit: git(provider, 'rev-parse', 'HEAD'), taskIds: ['T-001'],
      allowedPaths: ['src/provider.mjs'], preExistingChanges: [],
    },
    {
      id: 'consumer-service', root: consumer, branch: git(consumer, 'rev-parse', '--abbrev-ref', 'HEAD'),
      baseCommit: git(consumer, 'rev-parse', 'HEAD'), taskIds: ['T-002'],
      allowedPaths: ['src/consumer.mjs'], preExistingChanges: [],
    },
  ];
  await writeFile(path.join(roundDir, 'development-snapshot.json'), `${JSON.stringify({
    schemaVersion: 2, task, round: 'round-01', frozenFingerprint: state.frozenFingerprint, workspaces,
  }, null, 2)}\n`);
  await writeFile(path.join(roundDir, 'workspace-authorization.json'), `${JSON.stringify({
    schemaVersion: 1, task, round: 'round-01', coordinatorWorkspaceId: 'consumer-service',
    status: 'CONFIRMED', confirmedBy: 'user',
    workspaces: [
      {
        id: 'provider-service', root: provider, access: 'read-write', taskIds: ['T-001'],
        allowedPaths: ['src/provider.mjs'],
        testCommands: [{ cwd: provider, argv: ['node', 'tests/provider-check.mjs'] }],
      },
      {
        id: 'consumer-service', root: consumer, access: 'read-write', taskIds: ['T-002'],
        allowedPaths: ['src/consumer.mjs'],
        testCommands: [{ cwd: consumer, argv: ['node', 'tests/consumer-check.mjs'] }],
      },
    ],
  }, null, 2)}\n`);
  await writeFile(path.join(roundDir, 'workspace-coverage.json'), `${JSON.stringify({
    schemaVersion: 1, task, round: 'round-01', status: 'PASS', missing: [],
    taskCoverage: [
      { taskId: 'T-001', workspaceIds: ['provider-service'], dependsOn: cycle ? ['T-002'] : [], status: 'COVERED' },
      { taskId: 'T-002', workspaceIds: ['consumer-service'], dependsOn: ['T-001'], status: 'COVERED' },
    ],
  }, null, 2)}\n`);
  await writeFile(path.join(provider, 'src', 'provider.mjs'), 'export const value = "new-provider";\n');
  await writeFile(path.join(consumer, 'src', 'consumer.mjs'), 'export const value = "new-consumer";\n');
  return { consumer, provider, task, roundDir };
}

async function invoke(root, argv) {
  const out = []; const err = [];
  const exitCode = await runCli(argv, {
    cwd: root, stdout: (value) => out.push(value), stderr: (value) => err.push(value),
  });
  return { exitCode, out: out.join(''), err: err.join('') };
}

test('schema v2 self-check gates two repositories in dependency order and acceptance revalidates them', async (t) => {
  const { consumer, task, roundDir } = await fixture(t);
  const checked = await invoke(consumer, ['self-check', '--task', task, '--round', '1', '--json']);
  assert.equal(checked.exitCode, 0, checked.err);
  const evidence = JSON.parse(await readFile(path.join(roundDir, 'gate-evidence.json'), 'utf8'));
  assert.equal(evidence.schemaVersion, 2);
  assert.equal(evidence.status, 'PASS');
  assert.deepEqual(evidence.changedFiles, [
    { workspaceId: 'consumer-service', path: 'src/consumer.mjs' },
    { workspaceId: 'provider-service', path: 'src/provider.mjs' },
  ]);
  assert.deepEqual(evidence.tests.map((entry) => [entry.workspaceId, entry.wave, entry.status]), [
    ['provider-service', 1, 'PASS'], ['consumer-service', 2, 'PASS'],
  ]);
  assert.match(await readFile(path.join(roundDir, 'self-check-report.md'), 'utf8'), /多工作区机械自检报告/);

  const review = {
    status: 'PASS', reviewer: 'codex-review', reviewerKind: 'fresh-subagent',
    isolation: 'fresh-read-only-no-development-context', checkedAcceptanceIds: ['A-001', 'A-002'],
    counts: { p0: 0, p1: 0, p2: 0 }, findings: [], suggestedTests: [], repairInstructions: [],
  };
  await writeFile(path.join(roundDir, 'review-input.json'), `${JSON.stringify(review)}\n`);
  const accepted = await invoke(consumer, [
    'accept', '--task', task, '--round', '1', '--review-result',
    `.ai-dev-loop/${task}/rounds/round-01/review-input.json`, '--json',
  ]);
  assert.equal(accepted.exitCode, 0, accepted.err);
  assert.equal(JSON.parse(accepted.out).result.status, 'PASS');
});

test('schema v2 acceptance rejects a workspace changed after self-check', async (t) => {
  const { consumer, provider, task, roundDir } = await fixture(t);
  assert.equal((await invoke(consumer, ['self-check', '--task', task, '--json'])).exitCode, 0);
  await writeFile(path.join(provider, 'src', 'provider.mjs'), 'export const value = "changed-after-gate";\n');
  const review = {
    status: 'PASS', reviewer: 'codex-review', reviewerKind: 'fresh-subagent',
    isolation: 'fresh-read-only-no-development-context', checkedAcceptanceIds: ['A-001', 'A-002'],
    counts: { p0: 0, p1: 0, p2: 0 }, findings: [], suggestedTests: [], repairInstructions: [],
  };
  await writeFile(path.join(roundDir, 'review-input.json'), `${JSON.stringify(review)}\n`);

  const accepted = await invoke(consumer, [
    'accept', '--task', task, '--round', '1', '--review-result',
    `.ai-dev-loop/${task}/rounds/round-01/review-input.json`, '--json',
  ]);
  assert.equal(accepted.exitCode, 2);
  assert.equal(JSON.parse(accepted.out).result.status, 'NEED_HUMAN_REVIEW');
  const plan = JSON.parse(await readFile(path.join(roundDir, 'review-plan.json'), 'utf8'));
  assert.match(plan.reason, /ACCEPTANCE_EVIDENCE_CHANGED/);
});

test('schema v2 blocks dependent workspace tests when the provider gate fails', async (t) => {
  const { consumer, provider, task, roundDir } = await fixture(t, { failingProvider: true });
  const direct = spawnSync('node', ['tests/provider-check.mjs'], { cwd: provider, encoding: 'utf8', shell: false });
  assert.equal(direct.status, 1, `${direct.stdout}\n${direct.stderr}`);
  const checked = await invoke(consumer, ['self-check', '--task', task, '--json']);
  const evidence = JSON.parse(await readFile(path.join(roundDir, 'gate-evidence.json'), 'utf8'));
  assert.equal(checked.exitCode, 2, `${checked.err}\n${checked.out}\n${JSON.stringify(evidence, null, 2)}`);
  assert.equal(evidence.status, 'FAIL');
  assert.deepEqual(evidence.tests.map((entry) => [entry.workspaceId, entry.status]), [
    ['provider-service', 'FAIL'], ['consumer-service', 'BLOCKED'],
  ]);
  assert.match(evidence.blockers.join('\n'), /前置工作区门禁未通过：provider-service/);
});

test('schema v2 rejects cyclic task dependencies before running tests', async (t) => {
  const { consumer, task, roundDir } = await fixture(t, { cycle: true });
  const checked = await invoke(consumer, ['self-check', '--task', task, '--json']);
  assert.equal(checked.exitCode, 2, checked.err);
  const evidence = JSON.parse(await readFile(path.join(roundDir, 'gate-evidence.json'), 'utf8'));
  assert.equal(evidence.status, 'NEED_HUMAN_REVIEW');
  assert.equal(evidence.tests.length, 0);
  assert.match(evidence.humanReviewReasons.join('\n'), /WORKSPACE_DEPENDENCY_CYCLE/);
});
