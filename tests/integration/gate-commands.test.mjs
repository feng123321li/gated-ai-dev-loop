import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

import { runCli } from '../../src/cli/main.mjs';
import { startTask } from '../../src/commands/start.mjs';

const risks = {
  loadBearing: false, breaking: false, migrations: false, dependencyChange: false,
  newDependency: false, externalContract: false, permissions: false, authentication: false,
  stateMachine: false, transaction: false, concurrency: false, idempotency: false,
  unresolvedOptions: 0, thresholdDecision: false, fileCountExceeded: false, impactKnown: true,
};

function git(root, ...args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8', shell: false });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-acceptance-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, 'src'));
  await mkdir(path.join(root, 'tests'));
  await writeFile(path.join(root, '.gitignore'), '.ai-dev-loop/\n');
  await writeFile(path.join(root, 'src', 'input.mjs'), 'export const message = "old";\n');
  await writeFile(path.join(root, 'tests', 'pass.test.mjs'), "import test from 'node:test';\nimport assert from 'node:assert/strict';\ntest('pass', () => assert.equal(1, 1));\n");
  git(root, 'init'); git(root, 'config', 'user.email', 'gate@example.invalid'); git(root, 'config', 'user.name', 'Gate Test');
  git(root, 'add', '.'); git(root, 'commit', '-m', 'fixture');
  const task = 'light-gate';
  await startTask({
    root, task, hostRuntime: 'codex', confirmed: true,
    signals: { description: 'change input message', modifiesFiles: ['src/input.mjs'], writesFiles: true, impactKnown: true },
    brief: {
      goal: 'Change the exported input message.', scope: ['src/input.mjs'],
      acceptance: { outcomes: ['The module exports the new message.'], testCommands: [['node', '--test', 'tests/pass.test.mjs']] },
      risks,
    },
    now: () => '2026-07-14T00:00:00.000Z',
  });
  const taskDir = path.join(root, '.ai-dev-loop', task);
  const state = JSON.parse(await readFile(path.join(taskDir, 'state.json'), 'utf8'));
  const roundDir = path.join(taskDir, 'rounds', 'round-01');
  await mkdir(roundDir, { recursive: true });
  await writeFile(path.join(roundDir, 'development-snapshot.json'), `${JSON.stringify({
    schemaVersion: 1, task, round: 'round-01', baseCommit: git(root, 'rev-parse', 'HEAD'),
    frozenFingerprint: state.frozenFingerprint, allowedPaths: ['src/input.mjs'], preExistingChanges: [],
  }, null, 2)}\n`);
  await writeFile(path.join(root, 'src', 'input.mjs'), 'export const message = "new";\n');
  return { root, task, roundDir };
}

async function invoke(root, argv, extra = {}) {
  const out = []; const err = [];
  const exitCode = await runCli(argv, { cwd: root, stdout: (value) => out.push(value), stderr: (value) => err.push(value), ...extra });
  return { exitCode, out: out.join(''), err: err.join('') };
}

test('self-check writes deterministic evidence and accept records a fresh subagent P2 review', async (t) => {
  const { root, task, roundDir } = await fixture(t);
  const checked = await invoke(root, ['self-check', '--task', task, '--round', '1', '--json']);
  assert.equal(checked.exitCode, 0, checked.err);
  assert.equal(JSON.parse(checked.out).result.status, 'PASS');
  const evidence = JSON.parse(await readFile(path.join(roundDir, 'gate-evidence.json'), 'utf8'));
  assert.deepEqual(evidence.changedFiles, ['src/input.mjs']);
  assert.equal(evidence.tests[0].status, 'PASS');
  assert.match(await readFile(path.join(roundDir, 'self-check-report.md'), 'utf8'), /机械自检报告/);

  const review = {
    status: 'PASS', reviewer: 'opencode', reviewerKind: 'fresh-subagent',
    isolation: 'fresh-read-only-no-development-context', checkedAcceptanceIds: ['A-001'],
    counts: { p0: 0, p1: 0, p2: 1 },
    findings: [{
      id: 'F-001', severity: 'P2', title: '补充针对导出值的直接断言', relatedIds: ['A-001'],
      file: 'tests/pass.test.mjs', line: 3, evidence: '当前测试只验证测试框架可运行。',
      impact: '不阻断当前冻结验收，但回归保护较弱。', remediation: '增加对新导出值的断言。',
    }],
    suggestedTests: ['直接导入模块并断言导出值。'], repairInstructions: [],
  };
  await writeFile(path.join(roundDir, 'review-input.json'), `${JSON.stringify(review)}\n`);
  const accepted = await invoke(root, ['accept', '--task', task, '--round', '1', '--review-result', `.ai-dev-loop/${task}/rounds/round-01/review-input.json`, '--json']);
  assert.equal(accepted.exitCode, 0, accepted.err);
  const result = JSON.parse(accepted.out).result;
  assert.equal(result.status, 'PASS');
  assert.equal(result.counts.p2, 1);
  assert.equal(result.finalReportPath, path.join(root, '.ai-dev-loop', task, 'final-acceptance-report.md'));
  const report = await readFile(path.join(roundDir, 'acceptance-report.md'), 'utf8');
  assert.match(report, /fresh-subagent/);
  assert.match(report, /P2 非阻断建议/);
  const finalReport = await readFile(path.join(root, '.ai-dev-loop', task, 'final-acceptance-report.md'), 'utf8');
  assert.match(finalReport, /最终验收报告/);
  assert.match(finalReport, /WAITING_FOR_MANUAL_ACCEPTANCE/);
  assert.match(finalReport, /F-001/);
  assert.match(finalReport, /rounds\/round-01\/acceptance-report\.md/);

  const acceptedAgain = await invoke(root, ['accept', '--task', task, '--round', '1', '--review-result', `.ai-dev-loop/${task}/rounds/round-01/review-input.json`, '--json']);
  assert.equal(acceptedAgain.exitCode, 0, acceptedAgain.err);
});

test('accept enforces P1 as FAIL and returns a non-zero gate status', async (t) => {
  const { root, task, roundDir } = await fixture(t);
  assert.equal((await invoke(root, ['self-check', '--task', task, '--json'])).exitCode, 0);
  const review = {
    status: 'FAIL', reviewer: 'claude', reviewerKind: 'independent-agent',
    isolation: 'fresh-read-only-no-development-context', checkedAcceptanceIds: ['A-001'],
    counts: { p0: 0, p1: 1, p2: 0 },
    findings: [{
      id: 'F-001', severity: 'P1', title: '验收结果缺少行为测试', relatedIds: ['A-001'],
      file: 'tests/pass.test.mjs', line: 3, evidence: '测试未读取被修改模块。',
      impact: 'A-001 没有直接证据。', remediation: '增加模块导入和导出值断言。',
    }],
    suggestedTests: ['断言导出值。'], repairInstructions: ['修复 F-001 后重新运行全部冻结测试。'],
  };
  await writeFile(path.join(roundDir, 'review-input.json'), `${JSON.stringify(review)}\n`);
  const accepted = await invoke(root, ['accept', '--task', task, '--review-result', `.ai-dev-loop/${task}/rounds/round-01/review-input.json`, '--json']);
  assert.equal(accepted.exitCode, 2);
  assert.equal(JSON.parse(accepted.out).result.status, 'FAIL');
  assert.match(await readFile(path.join(roundDir, 'acceptance-report.md'), 'utf8'), /P1 阻断问题/);
  const finalReport = await readFile(path.join(root, '.ai-dev-loop', task, 'final-acceptance-report.md'), 'utf8');
  assert.match(finalReport, /BLOCKED_BY_P0_P1/);
  assert.match(finalReport, /F-001/);
});

test('accept still writes the human-facing root report when evidence needs human review', async (t) => {
  const { root, task } = await fixture(t);
  const accepted = await invoke(root, ['accept', '--task', task, '--json']);
  assert.equal(accepted.exitCode, 2);
  assert.equal(JSON.parse(accepted.out).result.status, 'NEED_HUMAN_REVIEW');
  const finalReport = await readFile(path.join(root, '.ai-dev-loop', task, 'final-acceptance-report.md'), 'utf8');
  assert.match(finalReport, /NEED_HUMAN_REVIEW/);
  assert.match(finalReport, /UNVERIFIED/);
});
