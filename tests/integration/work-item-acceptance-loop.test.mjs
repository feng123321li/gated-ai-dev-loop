import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';

import { sha256Bytes } from '../../src/core/hash.mjs';
import { canonicalJson } from '../../src/baseline/sources.mjs';
import {
  acceptWorkItem,
  approveWorkItem,
  dispatchTask,
  readWorkItemRegistry,
  recordAcceptance,
  recordTaskResult,
  selectDevelopmentMode,
  upgradeWorkItemRegistry,
} from '../../src/work-items/runtime.mjs';
import { issueTaskDefinition } from '../helpers/work-item-definitions.mjs';

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'hdg-acceptance-loop-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function putEvidence(root, name, artifact) {
  const relativePath = path.posix.join('evidence', name);
  const target = path.join(root, relativePath);
  await writeFile(target, `${JSON.stringify(artifact, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' })
    .catch(async (error) => {
      if (error.code !== 'ENOENT') throw error;
      await import('node:fs/promises').then(({ mkdir }) => mkdir(path.dirname(target), { recursive: true }));
      await writeFile(target, `${JSON.stringify(artifact, null, 2)}\n`, 'utf8');
    });
  const bytes = await readFile(target);
  return { path: relativePath, sha256: sha256Bytes(bytes) };
}

function legacyContractFingerprint(definition) {
  return sha256Bytes(Buffer.from(canonicalJson({
    schemaVersion: definition.schemaVersion,
    id: definition.id,
    kind: definition.kind,
    goal: definition.goal,
    scope: [...definition.scope].sort(),
    requirements: [...definition.requirements].sort((left, right) => left.id.localeCompare(right.id)),
    acceptance: [...definition.acceptance].sort((left, right) => left.id.localeCompare(right.id)),
    testCommands: definition.testCommands,
    execution: definition.execution,
  }), 'utf8'));
}

async function putLegacyRootTask(root) {
  const runtimeRoot = path.join(root, '.hierarchical-delivery-governance');
  const itemId = 't-legacy-manual-task';
  const itemRoot = path.join(runtimeRoot, 'work-items', itemId);
  await mkdir(itemRoot, { recursive: true });
  const source = issueTaskDefinition({ id: itemId, parentId: null, gateLevel: 'FULL' });
  const baseline = {
    ...source,
    schemaVersion: 2,
    authorityKind: 'EXECUTION',
    parentContractFingerprint: null,
  };
  delete baseline.gateLevel;
  const baselineFingerprint = sha256Bytes(Buffer.from(canonicalJson(baseline), 'utf8'));
  const contractFingerprint = legacyContractFingerprint(baseline);
  const developmentMode = {
    schemaVersion: 1,
    taskId: itemId,
    baselineFingerprint,
    mode: 'manual',
    confirmedBy: 'user',
    confirmedAt: '2026-07-15T23:59:00.000Z',
  };
  const state = {
    schemaVersion: 2,
    id: itemId,
    stage: 'BASELINE_FROZEN',
    baselineFingerprint,
    contractFingerprint,
    parentContractFingerprint: null,
    hostRuntime: 'codex',
    createdAt: '2026-07-15T23:58:00.000Z',
    frozenAt: '2026-07-15T23:59:00.000Z',
  };
  const entry = {
    id: itemId,
    kind: 'TASK',
    authorityKind: 'EXECUTION',
    parentId: null,
    childIds: [],
    packagePath: `work-items/${itemId}`,
    stage: 'BASELINE_FROZEN',
    status: 'FROZEN',
    baselineFingerprint,
    contractFingerprint,
    parentContractFingerprint: null,
    gate: { status: 'NOT_RUN', evidence: null },
    delivery: null,
    developmentMode,
    claim: null,
    latestEvidence: null,
    recordRevision: 3,
    createdAt: '2026-07-15T23:58:00.000Z',
    updatedAt: '2026-07-15T23:59:00.000Z',
    progress: {
      directChildren: { total: 0, verified: 0, blocked: 0, active: 0 },
      descendants: { total: 0, verified: 0, blocked: 0, active: 0 },
    },
  };
  const registry = {
    schemaVersion: 2,
    coordinationRoot: path.resolve(root),
    revision: 3,
    currentFocus: { workItemId: itemId, purpose: 'EXECUTION' },
    workItems: [entry],
    updatedAt: '2026-07-15T23:59:00.000Z',
  };
  await Promise.all([
    writeFile(path.join(runtimeRoot, 'work-item-registry.json'), `${JSON.stringify(registry, null, 2)}\n`),
    writeFile(path.join(itemRoot, 'baseline.json'), `${JSON.stringify(baseline, null, 2)}\n`),
    writeFile(path.join(itemRoot, 'baseline.md'), '# Legacy baseline\n'),
    writeFile(path.join(itemRoot, 'state.json'), `${JSON.stringify(state, null, 2)}\n`),
    writeFile(path.join(itemRoot, 'work-item.json'), `${JSON.stringify({
      schemaVersion: 2,
      id: itemId,
      kind: 'TASK',
      authorityKind: 'EXECUTION',
      parentId: null,
    }, null, 2)}\n`),
    writeFile(path.join(itemRoot, 'execution.json'), `${JSON.stringify({ schemaVersion: 2, ...baseline.execution }, null, 2)}\n`),
    writeFile(path.join(itemRoot, 'development-mode.json'), `${JSON.stringify(developmentMode, null, 2)}\n`),
    writeFile(path.join(itemRoot, 'context-manifest.json'), '{}\n'),
    writeFile(path.join(itemRoot, 'development-handoff.md'), '# Stale handoff\n'),
  ]);
  return { itemId, itemRoot, baselineFingerprint };
}

test('schema v2 manual root Task upgrades explicitly without losing frozen state or mode', async (t) => {
  const root = await fixture(t);
  const legacy = await putLegacyRootTask(root);

  await assert.rejects(
    () => upgradeWorkItemRegistry({ root, taskGateLevel: 'FULL' }),
    { code: 'CONFIRMATION_REQUIRED' },
  );

  const result = await upgradeWorkItemRegistry({
    root,
    taskGateLevel: 'FULL',
    confirmed: true,
    now: () => '2026-07-16T00:00:00.000Z',
  });
  assert.equal(result.migrated, true);
  assert.equal(result.fromSchemaVersion, 2);
  assert.equal(result.toSchemaVersion, 3);
  assert.equal(result.taskGateLevel, 'FULL');

  const registry = await readWorkItemRegistry({ root });
  const entry = registry.workItems[0];
  assert.equal(registry.schemaVersion, 3);
  assert.equal(registry.revision, 4);
  assert.equal(entry.id, legacy.itemId);
  assert.equal(entry.gateLevel, 'FULL');
  assert.equal(entry.stage, 'BASELINE_FROZEN');
  assert.equal(entry.status, 'FROZEN');
  assert.equal(entry.developmentMode.mode, 'manual');
  assert.equal(entry.developmentMode.baselineFingerprint, entry.baselineFingerprint);
  assert.notEqual(entry.baselineFingerprint, legacy.baselineFingerprint);
  assert.equal(entry.acceptance.status, 'NOT_READY');
  assert.equal(registry.migrationHistory[0].previousBaselineFingerprint, legacy.baselineFingerprint);

  const baseline = JSON.parse(await readFile(path.join(legacy.itemRoot, 'baseline.json'), 'utf8'));
  const state = JSON.parse(await readFile(path.join(legacy.itemRoot, 'state.json'), 'utf8'));
  assert.equal(baseline.schemaVersion, 3);
  assert.equal(baseline.gateLevel, 'FULL');
  assert.equal(state.schemaVersion, 3);
  assert.equal(state.baselineFingerprint, entry.baselineFingerprint);
  await assert.rejects(() => access(path.join(legacy.itemRoot, 'context-manifest.json')), { code: 'ENOENT' });
  await assert.rejects(() => access(path.join(legacy.itemRoot, 'development-handoff.md')), { code: 'ENOENT' });

  const overview = await readFile(path.join(
    root,
    '.hierarchical-delivery-governance',
    'workspace-overview.md',
  ), 'utf8');
  assert.match(overview, /工作项总览/);
  assert.match(overview, /t-legacy-manual-task/);
});

test('manual root Task closes through dispatch, gate report, independent review, and user confirmation', async (t) => {
  const root = await fixture(t);
  const task = issueTaskDefinition({
    id: 't-user-visible-acceptance',
    parentId: null,
    gateLevel: 'LIGHT',
    title: '用户可见验收闭环',
  });
  const approved = await approveWorkItem({
    root,
    definition: task,
    hostRuntime: 'codex',
    confirmed: true,
    now: () => '2026-07-16T00:00:00.000Z',
  });
  await selectDevelopmentMode({
    root,
    id: task.id,
    mode: 'manual',
    expectedBaselineFingerprint: approved.baselineFingerprint,
    confirmed: true,
    now: () => '2026-07-16T00:01:00.000Z',
  });

  const dispatched = await dispatchTask({
    root,
    id: task.id,
    owner: 'manual-developer',
    operationId: 'op-user-visible-acceptance',
    now: () => '2026-07-16T00:02:00.000Z',
  });
  assert.equal(dispatched.status, 'CLAIMED');
  assert.match(dispatched.handoffPrompt, /op-user-visible-acceptance/);
  assert.match(dispatched.handoffPrompt, /task-result/);
  assert.match(dispatched.handoffPrompt, /返回开发结果后必须继续验收/);

  const resultEvidence = await putEvidence(root, 'task-result.json', {
    schemaVersion: 1,
    kind: 'TASK_RESULT',
    taskId: task.id,
    operationId: 'op-user-visible-acceptance',
    status: 'IMPLEMENTED',
    summary: '已实现并完成开发侧检查。',
    changedFiles: [...task.scope],
    tests: [{ argv: task.testCommands[0], exitCode: 0, testsRun: 1 }],
    blockers: [],
  });
  await recordTaskResult({
    root,
    id: task.id,
    operationId: 'op-user-visible-acceptance',
    status: 'IMPLEMENTED',
    evidence: resultEvidence,
    now: () => '2026-07-16T00:03:00.000Z',
  });

  let report = await readFile(path.join(
    root,
    '.hierarchical-delivery-governance',
    'work-items',
    task.id,
    'acceptance-report.md',
  ), 'utf8');
  assert.match(report, /# 验收报告/);
  assert.match(report, /等待门禁验收/);

  const gateEvidence = await putEvidence(root, 'task-gate.json', {
    schemaVersion: 1,
    kind: 'WORK_ITEM_GATE',
    workItemId: task.id,
    baselineFingerprint: approved.baselineFingerprint,
    verdict: 'PASS',
    summary: '冻结范围与验收项全部通过。',
    scope: {
      changedFiles: [...task.scope],
      outOfScopeFiles: [],
    },
    acceptance: task.acceptance.map(({ id }) => ({ id, status: 'PASS', evidence: '定向测试通过' })),
    tests: [{ argv: task.testCommands[0], exitCode: 0, testsRun: 1, summary: '1 test passed' }],
    findings: { p0: [], p1: [], p2: [] },
  });
  await acceptWorkItem({
    root,
    id: task.id,
    evidence: gateEvidence,
    now: () => '2026-07-16T00:04:00.000Z',
  });

  let registry = await readWorkItemRegistry({ root });
  let entry = registry.workItems.find(({ id }) => id === task.id);
  assert.equal(entry.status, 'VERIFIED');
  assert.equal(entry.acceptance.status, 'WAITING_FOR_INDEPENDENT_REVIEW');
  report = await readFile(path.join(root, entry.acceptanceReport.markdownPath), 'utf8');
  assert.match(report, /门禁结论：通过/);
  assert.match(report, /等待独立验收/);
  assert.match(report, /A-001/);
  assert.match(report, /1 test passed/);

  const reviewEvidence = await putEvidence(root, 'review.json', {
    schemaVersion: 1,
    kind: 'INDEPENDENT_REVIEW',
    reviewer: 'fresh-read-only-reviewer',
    isolation: 'FRESH_READ_ONLY',
    verdict: 'PASS',
    findings: { p0: 0, p1: 0, p2: [] },
  });
  await recordAcceptance({
    root,
    id: task.id,
    action: 'INDEPENDENT_REVIEW_PASS',
    evidence: reviewEvidence,
    now: () => '2026-07-16T00:05:00.000Z',
  });

  const confirmationEvidence = await putEvidence(root, 'confirmation.json', {
    schemaVersion: 1,
    kind: 'USER_CONFIRMATION',
    confirmedBy: 'product-owner',
    decision: 'CONFIRMED',
  });
  await recordAcceptance({
    root,
    id: task.id,
    action: 'USER_CONFIRMED',
    evidence: confirmationEvidence,
    now: () => '2026-07-16T00:06:00.000Z',
  });

  registry = await readWorkItemRegistry({ root });
  entry = registry.workItems.find(({ id }) => id === task.id);
  assert.equal(entry.acceptance.status, 'COMPLETED');
  report = await readFile(path.join(root, entry.acceptanceReport.markdownPath), 'utf8');
  assert.match(report, /最终状态：已完成/);
  assert.match(report, /product-owner/);

  const workspaceOverview = await readFile(path.join(
    root,
    '.hierarchical-delivery-governance',
    'workspace-overview.md',
  ), 'utf8');
  assert.match(workspaceOverview, /# 工作项总览/);
  assert.match(workspaceOverview, /验收报告/);
  assert.match(workspaceOverview, /已完成/);
});

test('a passing gate rejects out-of-scope changes instead of producing a false report', async (t) => {
  const root = await fixture(t);
  const task = issueTaskDefinition({ id: 't-scope-rejection', parentId: null, gateLevel: 'LIGHT' });
  const approved = await approveWorkItem({ root, definition: task, hostRuntime: 'codex', confirmed: true });
  await selectDevelopmentMode({
    root,
    id: task.id,
    mode: 'active',
    expectedBaselineFingerprint: approved.baselineFingerprint,
    confirmed: true,
  });
  await dispatchTask({ root, id: task.id, owner: 'developer', operationId: 'op-scope-rejection' });
  const resultEvidence = await putEvidence(root, 'scope-result.json', {
    schemaVersion: 1,
    kind: 'TASK_RESULT',
    taskId: task.id,
    operationId: 'op-scope-rejection',
    status: 'IMPLEMENTED',
    summary: '实现完成。',
    changedFiles: [...task.scope, 'outside-scope.txt'],
    tests: [],
    blockers: [],
  });
  await recordTaskResult({
    root,
    id: task.id,
    operationId: 'op-scope-rejection',
    status: 'IMPLEMENTED',
    evidence: resultEvidence,
  });
  const gateEvidence = await putEvidence(root, 'scope-gate.json', {
    schemaVersion: 1,
    kind: 'WORK_ITEM_GATE',
    workItemId: task.id,
    baselineFingerprint: approved.baselineFingerprint,
    verdict: 'PASS',
    summary: '错误地尝试通过。',
    scope: { changedFiles: [...task.scope, 'outside-scope.txt'], outOfScopeFiles: ['outside-scope.txt'] },
    acceptance: task.acceptance.map(({ id }) => ({ id, status: 'PASS', evidence: 'claimed' })),
    tests: [{ argv: task.testCommands[0], exitCode: 0, testsRun: 1, summary: 'passed' }],
    findings: { p0: [], p1: [], p2: [] },
  });
  await assert.rejects(
    () => acceptWorkItem({ root, id: task.id, evidence: gateEvidence }),
    { code: 'WORK_ITEM_GATE_EVIDENCE_INVALID' },
  );
});
