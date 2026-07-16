import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { sha256Bytes } from '../../src/core/hash.mjs';

import {
  buildTaskContext,
  claimTask,
  freezeWorkItem,
  listReadyTasks,
  prepareWorkItem,
  readWorkItemRegistry,
  recordTaskResult,
  recordDelivery,
  recordWorkItemGate,
  promoteWorkItem,
  retryWorkItem,
  reviseWorkItem,
  selectDevelopmentMode,
} from '../../src/work-items/runtime.mjs';
import {
  capabilityDefinition,
  issueTaskDefinition,
  deliveryDefinition,
  revokeTaskDefinition,
  verifyTaskDefinition,
} from '../helpers/work-item-definitions.mjs';

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'delivery-governance-work-items-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function putDeliveryEvidence(root, name, artifact) {
  const bytes = Buffer.from(`${JSON.stringify(artifact, null, 2)}\n`, 'utf8');
  await writeFile(path.join(root, name), bytes);
  return { path: name, sha256: sha256Bytes(bytes) };
}

async function prepareAndFreeze(root, definition) {
  await prepareWorkItem({
    root,
    definition,
    hostRuntime: 'codex',
    now: () => '2026-07-16T00:00:00.000Z',
  });
  return freezeWorkItem({
    root,
    id: definition.id,
    confirmed: true,
    now: () => '2026-07-16T00:01:00.000Z',
  });
}

async function selectMode(root, id, mode = 'active') {
  const registry = await readWorkItemRegistry({ root });
  const entry = registry.workItems.find((candidate) => candidate.id === id);
  return selectDevelopmentMode({
    root,
    id,
    mode,
    expectedBaselineFingerprint: entry.baselineFingerprint,
    confirmed: true,
    now: () => '2026-07-16T00:01:30.000Z',
  });
}

test('a standalone root Task has its own baseline, dispatch, context, and gate', async (t) => {
  const root = await fixture(t);
  const task = issueTaskDefinition({ id: 't-standalone', parentId: null, gateLevel: 'LIGHT' });
  await prepareAndFreeze(root, task);
  await selectMode(root, task.id);

  assert.deepEqual(await listReadyTasks({ root, workItemId: task.id }), [task.id]);
  const context = await buildTaskContext({ root, id: task.id });
  assert.equal(context.gateLevel, 'LIGHT');
  assert.deepEqual(context.parentContracts, []);
  assert.deepEqual(context.capabilityDependencies, []);

  await claimTask({ root, id: task.id, owner: 'developer-a', operationId: 'op-standalone' });
  await recordTaskResult({
    root,
    id: task.id,
    operationId: 'op-standalone',
    status: 'IMPLEMENTED',
    evidence: { path: 'results/standalone.json', sha256: 'a'.repeat(64) },
  });
  await recordWorkItemGate({
    root,
    id: task.id,
    status: 'PASS',
    evidence: { path: 'results/standalone-gate.json', sha256: 'b'.repeat(64) },
  });
  const registry = await readWorkItemRegistry({ root });
  assert.equal(registry.workItems.find(({ id }) => id === task.id).gateLevel, 'LIGHT');
  assert.equal(registry.workItems.find(({ id }) => id === task.id).status, 'VERIFIED');
});

test('a frozen root Task is promoted under a pre-frozen Capability with auditable history', async (t) => {
  const root = await fixture(t);
  const task = issueTaskDefinition({ id: 't-standalone', parentId: null, gateLevel: 'LIGHT' });
  const capability = capabilityDefinition({
    parentId: null,
    children: [{
      id: task.id,
      kind: 'TASK',
      title: task.title,
      requirementIds: ['R-001'],
      acceptanceIds: ['A-001'],
    }],
  });
  await prepareAndFreeze(root, task);
  await selectMode(root, task.id);
  await prepareWorkItem({ root, definition: capability, hostRuntime: 'codex' });

  let registry = await readWorkItemRegistry({ root });
  const sourceBefore = registry.workItems.find(({ id }) => id === task.id);
  const parentBefore = registry.workItems.find(({ id }) => id === capability.id);
  await assert.rejects(
    () => promoteWorkItem({
      root,
      id: task.id,
      parentId: capability.id,
      expectedBaselineFingerprint: sourceBefore.baselineFingerprint,
      expectedParentBaselineFingerprint: parentBefore.baselineFingerprint,
      confirmed: true,
    }),
    { code: 'WORK_ITEM_PROMOTION_PARENT_NOT_FROZEN' },
  );
  await freezeWorkItem({ root, id: capability.id, confirmed: true });
  registry = await readWorkItemRegistry({ root });
  const frozenParent = registry.workItems.find(({ id }) => id === capability.id);
  await assert.rejects(
    () => promoteWorkItem({
      root,
      id: task.id,
      parentId: capability.id,
      expectedBaselineFingerprint: sourceBefore.baselineFingerprint,
      expectedParentBaselineFingerprint: frozenParent.baselineFingerprint,
      confirmed: false,
    }),
    { code: 'CONFIRMATION_REQUIRED' },
  );

  const promoted = await promoteWorkItem({
    root,
    id: task.id,
    parentId: capability.id,
    expectedBaselineFingerprint: sourceBefore.baselineFingerprint,
    expectedParentBaselineFingerprint: frozenParent.baselineFingerprint,
    confirmed: true,
    now: () => '2026-07-16T00:05:00.000Z',
  });
  assert.equal(promoted.kind, 'TASK');
  assert.equal(promoted.parentId, capability.id);
  assert.equal(promoted.gateLevel, 'LIGHT');

  registry = await readWorkItemRegistry({ root });
  const sourceAfter = registry.workItems.find(({ id }) => id === task.id);
  const parentAfter = registry.workItems.find(({ id }) => id === capability.id);
  assert.equal(sourceAfter.parentId, capability.id);
  assert.ok(parentAfter.childIds.includes(task.id));
  assert.equal(sourceAfter.status, 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION');
  assert.equal(sourceAfter.developmentMode, null);
  assert.equal(registry.promotionHistory.length, 1);
  assert.deepEqual(registry.promotionHistory[0], {
    schemaVersion: 1,
    childId: task.id,
    childKind: 'TASK',
    parentId: capability.id,
    parentKind: 'CAPABILITY',
    previousBaselineFingerprint: sourceBefore.baselineFingerprint,
    promotedBaselineFingerprint: sourceAfter.baselineFingerprint,
    parentBaselineFingerprint: frozenParent.baselineFingerprint,
    promotedAt: '2026-07-16T00:05:00.000Z',
  });
  const baseline = JSON.parse(await readFile(path.join(
    root,
    '.hierarchical-delivery-governance',
    'work-items',
    task.id,
    'baseline.json',
  ), 'utf8'));
  assert.equal(baseline.parentId, capability.id);
  assert.equal(baseline.gateLevel, 'LIGHT');
  await assert.rejects(
    () => readFile(path.join(
      root,
      '.hierarchical-delivery-governance',
      'work-items',
      task.id,
      'development-mode.json',
    )),
    { code: 'ENOENT' },
  );
  await selectMode(root, task.id);
  assert.deepEqual(
    (await buildTaskContext({ root, id: task.id })).parentContracts.map(({ id }) => id),
    [capability.id],
  );
});

test('a frozen root Capability is promoted under a pre-frozen Delivery', async (t) => {
  const root = await fixture(t);
  const capability = capabilityDefinition({ parentId: null });
  const delivery = deliveryDefinition();
  await prepareAndFreeze(root, capability);
  await prepareAndFreeze(root, delivery);
  const before = await readWorkItemRegistry({ root });
  const source = before.workItems.find(({ id }) => id === capability.id);
  const parent = before.workItems.find(({ id }) => id === delivery.id);

  await promoteWorkItem({
    root,
    id: capability.id,
    parentId: delivery.id,
    expectedBaselineFingerprint: source.baselineFingerprint,
    expectedParentBaselineFingerprint: parent.baselineFingerprint,
    confirmed: true,
  });

  const after = await readWorkItemRegistry({ root });
  assert.equal(after.workItems.find(({ id }) => id === capability.id).parentId, delivery.id);
  assert.equal(after.workItems.find(({ id }) => id === capability.id).gateLevel, 'FULL');
  assert.deepEqual(
    after.promotionHistory.map(({ childKind, parentKind }) => ({ childKind, parentKind })),
    [{ childKind: 'CAPABILITY', parentKind: 'DELIVERY' }],
  );
});

test('a root Capability aggregates child Tasks without an invented Delivery', async (t) => {
  const root = await fixture(t);
  const capability = capabilityDefinition({
    parentId: null,
    children: [capabilityDefinition().children[0]],
  });
  await prepareAndFreeze(root, capability);
  await prepareAndFreeze(root, issueTaskDefinition());
  await selectMode(root, 't-issue-token');

  assert.deepEqual(await listReadyTasks({ root, workItemId: capability.id }), ['t-issue-token']);
  const context = await buildTaskContext({ root, id: 't-issue-token' });
  assert.deepEqual(context.parentContracts.map(({ id }) => id), [capability.id]);
  assert.deepEqual(context.capabilityDependencies, []);

  await claimTask({ root, id: 't-issue-token', owner: 'developer-a', operationId: 'op-capability-task' });
  await recordTaskResult({
    root,
    id: 't-issue-token',
    operationId: 'op-capability-task',
    status: 'IMPLEMENTED',
    evidence: { path: 'results/capability-task.json', sha256: 'c'.repeat(64) },
  });
  await recordWorkItemGate({
    root,
    id: 't-issue-token',
    status: 'PASS',
    evidence: { path: 'results/capability-task-gate.json', sha256: 'd'.repeat(64) },
  });
  await recordWorkItemGate({
    root,
    id: capability.id,
    status: 'PASS',
    evidence: { path: 'results/capability-gate.json', sha256: 'e'.repeat(64) },
  });
  const registry = await readWorkItemRegistry({ root });
  assert.equal(registry.workItems.find(({ id }) => id === capability.id).status, 'VERIFIED');
});

test('hierarchical work items freeze independent baselines and roll up only after child and aggregate gates', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  await prepareAndFreeze(root, capabilityDefinition());
  await prepareAndFreeze(root, issueTaskDefinition());
  await prepareAndFreeze(root, verifyTaskDefinition());

  let registry = await readWorkItemRegistry({ root });
  assert.equal(registry.schemaVersion, 3);
  assert.deepEqual(
    registry.workItems.map(({ id, kind, parentId }) => ({ id, kind, parentId })),
    [
      { id: 'c-token-lifecycle', kind: 'CAPABILITY', parentId: 'd-identity-platform' },
      { id: 'd-identity-platform', kind: 'DELIVERY', parentId: null },
      { id: 't-issue-token', kind: 'TASK', parentId: 'c-token-lifecycle' },
      { id: 't-verify-token', kind: 'TASK', parentId: 'c-token-lifecycle' },
    ],
  );

  for (const id of ['d-identity-platform', 'c-token-lifecycle', 't-issue-token', 't-verify-token']) {
    const baseline = await readFile(path.join(root, '.hierarchical-delivery-governance', 'work-items', id, 'baseline.md'), 'utf8');
    assert.match(baseline, new RegExp(`Work Item: ${id}`));
  }

  const waitingTask = registry.workItems.find(({ id }) => id === 't-issue-token');
  assert.equal(waitingTask.status, 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION');
  assert.equal(waitingTask.developmentMode, null);
  await assert.rejects(
    () => buildTaskContext({ root, id: 't-issue-token' }),
    { code: 'WORK_ITEM_DEVELOPMENT_MODE_REQUIRED' },
  );
  await assert.rejects(
    () => claimTask({ root, id: 't-issue-token', owner: 'developer-a', operationId: 'op-premature' }),
    { code: 'WORK_ITEM_DEVELOPMENT_MODE_REQUIRED' },
  );

  await selectMode(root, 't-issue-token');
  await selectMode(root, 't-verify-token', 'manual');
  const modeRecord = JSON.parse(await readFile(path.join(
    root,
    '.hierarchical-delivery-governance',
    'work-items',
    't-issue-token',
    'development-mode.json',
  ), 'utf8'));
  assert.deepEqual(modeRecord, {
    schemaVersion: 1,
    taskId: 't-issue-token',
    baselineFingerprint: waitingTask.baselineFingerprint,
    mode: 'active',
    confirmedBy: 'user',
    confirmedAt: '2026-07-16T00:01:30.000Z',
  });

  const context = await buildTaskContext({ root, id: 't-issue-token' });
  assert.equal(context.task.id, 't-issue-token');
  assert.equal(context.developmentMode, 'active');
  assert.deepEqual(context.parentContracts.map(({ id }) => id), ['d-identity-platform', 'c-token-lifecycle']);
  assert.deepEqual(context.execution.dependsOn, []);
  assert.equal('conversation' in context, false);

  assert.deepEqual(await listReadyTasks({ root, workItemId: 'd-identity-platform' }), ['t-issue-token']);
  await claimTask({
    root,
    id: 't-issue-token',
    owner: 'developer-a',
    operationId: 'op-issue-001',
    now: () => '2026-07-16T00:02:00.000Z',
  });
  await assert.rejects(
    () => claimTask({ root, id: 't-issue-token', owner: 'developer-b', operationId: 'op-issue-002' }),
    { code: 'WORK_ITEM_NOT_READY' },
  );
  await recordTaskResult({
    root,
    id: 't-issue-token',
    operationId: 'op-issue-001',
    status: 'IMPLEMENTED',
    evidence: { path: 'results/issue.json', sha256: 'a'.repeat(64) },
    now: () => '2026-07-16T00:03:00.000Z',
  });
  await recordWorkItemGate({
    root,
    id: 't-issue-token',
    status: 'PASS',
    evidence: { path: 'results/issue-gate.json', sha256: 'b'.repeat(64) },
    now: () => '2026-07-16T00:04:00.000Z',
  });
  const partiallyComplete = await readWorkItemRegistry({ root });
  assert.deepEqual(
    partiallyComplete.workItems.find(({ id }) => id === 'c-token-lifecycle').progress.directChildren,
    { total: 2, verified: 1, blocked: 0, active: 0 },
  );
  assert.deepEqual(await listReadyTasks({ root, workItemId: 'd-identity-platform' }), ['t-verify-token']);

  await assert.rejects(
    () => recordWorkItemGate({
      root,
      id: 'c-token-lifecycle',
      status: 'PASS',
      evidence: { path: 'results/early-capability-gate.json', sha256: 'c'.repeat(64) },
    }),
    { code: 'WORK_ITEM_CHILDREN_INCOMPLETE' },
  );

  await claimTask({ root, id: 't-verify-token', owner: 'developer-b', operationId: 'op-verify-001' });
  await recordTaskResult({
    root,
    id: 't-verify-token',
    operationId: 'op-verify-001',
    status: 'IMPLEMENTED',
    evidence: { path: 'results/verify.json', sha256: 'd'.repeat(64) },
  });
  await recordWorkItemGate({
    root,
    id: 't-verify-token',
    status: 'PASS',
    evidence: { path: 'results/verify-gate.json', sha256: 'e'.repeat(64) },
  });
  let beforeCapabilityGate = await readWorkItemRegistry({ root });
  await reviseWorkItem({
    root,
    definition: capabilityDefinition({ decomposition: { status: 'OPEN', dependsOn: [] } }),
    expectedBaselineFingerprint: beforeCapabilityGate.workItems.find(({ id }) => id === 'c-token-lifecycle').baselineFingerprint,
    confirmed: true,
  });
  await assert.rejects(
    () => recordWorkItemGate({
      root,
      id: 'c-token-lifecycle',
      status: 'PASS',
      evidence: { path: 'results/open-capability-gate.json', sha256: 'f'.repeat(64) },
    }),
    { code: 'WORK_ITEM_DECOMPOSITION_OPEN' },
  );
  beforeCapabilityGate = await readWorkItemRegistry({ root });
  await reviseWorkItem({
    root,
    definition: capabilityDefinition(),
    expectedBaselineFingerprint: beforeCapabilityGate.workItems.find(({ id }) => id === 'c-token-lifecycle').baselineFingerprint,
    confirmed: true,
  });
  await recordWorkItemGate({
    root,
    id: 'c-token-lifecycle',
    status: 'FAIL',
    evidence: { path: 'results/capability-gate-fail.json', sha256: '4'.repeat(64) },
  });
  beforeCapabilityGate = await readWorkItemRegistry({ root });
  await assert.rejects(
    () => reviseWorkItem({
      root,
      definition: capabilityDefinition(),
      expectedBaselineFingerprint: beforeCapabilityGate.workItems.find(({ id }) => id === 'c-token-lifecycle').baselineFingerprint,
      confirmed: true,
    }),
    { code: 'WORK_ITEM_RETRY_REQUIRED' },
  );
  await assert.rejects(
    () => recordWorkItemGate({
      root,
      id: 'c-token-lifecycle',
      status: 'PASS',
      evidence: { path: 'results/capability-gate-unretried.json', sha256: '3'.repeat(64) },
    }),
    { code: 'WORK_ITEM_RETRY_REQUIRED' },
  );
  await retryWorkItem({
    root,
    id: 'c-token-lifecycle',
    expectedBaselineFingerprint: beforeCapabilityGate.workItems.find(({ id }) => id === 'c-token-lifecycle').baselineFingerprint,
    confirmed: true,
  });
  await recordWorkItemGate({
    root,
    id: 'c-token-lifecycle',
    status: 'PASS',
    evidence: { path: 'results/capability-gate.json', sha256: 'f'.repeat(64) },
  });
  await recordWorkItemGate({
    root,
    id: 'd-identity-platform',
    status: 'PASS',
    evidence: { path: 'results/delivery-gate.json', sha256: '1'.repeat(64) },
  });

  const independentReviewEvidence = await putDeliveryEvidence(root, 'independent-review.json', {
    schemaVersion: 1,
    kind: 'INDEPENDENT_REVIEW',
    reviewer: 'fresh-review-agent',
    isolation: 'FRESH_READ_ONLY',
    verdict: 'PASS',
    findings: { p0: 0, p1: 0 },
  });
  const userConfirmationEvidence = await putDeliveryEvidence(root, 'user-confirmation.json', {
    schemaVersion: 1,
    kind: 'USER_CONFIRMATION',
    confirmedBy: 'delivery-owner',
    decision: 'CONFIRMED',
  });

  let delivery = await readWorkItemRegistry({ root });
  assert.equal(
    delivery.workItems.find(({ id }) => id === 'd-identity-platform').delivery.status,
    'WAITING_FOR_INDEPENDENT_REVIEW',
  );
  await assert.rejects(
    () => recordDelivery({
      root,
      id: 'd-identity-platform',
      action: 'INDEPENDENT_REVIEW_PASS',
      evidence: { path: 'missing-review.json', sha256: '0'.repeat(64) },
    }),
    { code: 'WORK_ITEM_DELIVERY_EVIDENCE_MISSING' },
  );
  await assert.rejects(
    () => recordDelivery({
      root,
      id: 'd-identity-platform',
      action: 'USER_CONFIRMED',
      evidence: userConfirmationEvidence,
    }),
    { code: 'WORK_ITEM_DELIVERY_STAGE_INVALID' },
  );
  await recordDelivery({
    root,
    id: 'd-identity-platform',
    action: 'INDEPENDENT_REVIEW_PASS',
    evidence: independentReviewEvidence,
  });
  await assert.rejects(
    () => recordDelivery({
      root,
      id: 'd-identity-platform',
      action: 'USER_CONFIRMED',
      evidence: { ...userConfirmationEvidence, sha256: '0'.repeat(64) },
    }),
    { code: 'WORK_ITEM_DELIVERY_EVIDENCE_CHANGED' },
  );
  await recordDelivery({
    root,
    id: 'd-identity-platform',
    action: 'USER_CONFIRMED',
    evidence: userConfirmationEvidence,
  });

  const completed = await readWorkItemRegistry({ root });
  assert.deepEqual(
    completed.workItems.map(({ id, status }) => ({ id, status })),
    [
      { id: 'c-token-lifecycle', status: 'VERIFIED' },
      { id: 'd-identity-platform', status: 'VERIFIED' },
      { id: 't-issue-token', status: 'VERIFIED' },
      { id: 't-verify-token', status: 'VERIFIED' },
    ],
  );
  assert.equal(
    completed.workItems.find(({ id }) => id === 'd-identity-platform').delivery.status,
    'COMPLETED',
  );
  assert.deepEqual(
    completed.workItems.find(({ id }) => id === 'd-identity-platform').progress.descendants,
    { total: 3, verified: 3, blocked: 0, active: 0 },
  );
  await rm(path.join(root, 'independent-review.json'));
  await assert.rejects(
    () => readWorkItemRegistry({ root }),
    { code: 'WORK_ITEM_DELIVERY_EVIDENCE_MISSING' },
  );
});

test('development mode selection is explicit, baseline-bound, and tamper-evident', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  await prepareAndFreeze(root, capabilityDefinition());
  await prepareAndFreeze(root, issueTaskDefinition());
  const before = await readWorkItemRegistry({ root });
  const task = before.workItems.find(({ id }) => id === 't-issue-token');

  await assert.rejects(
    () => selectDevelopmentMode({
      root,
      id: task.id,
      mode: 'active',
      expectedBaselineFingerprint: task.baselineFingerprint,
      confirmed: false,
    }),
    { code: 'CONFIRMATION_REQUIRED' },
  );
  await assert.rejects(
    () => selectDevelopmentMode({
      root,
      id: task.id,
      mode: 'active',
      expectedBaselineFingerprint: '0'.repeat(64),
      confirmed: true,
    }),
    { code: 'WORK_ITEM_REVISION_CONFLICT' },
  );

  await selectMode(root, task.id);
  await assert.rejects(
    () => selectDevelopmentMode({
      root,
      id: task.id,
      mode: 'manual',
      expectedBaselineFingerprint: task.baselineFingerprint,
      confirmed: true,
    }),
    { code: 'WORK_ITEM_DEVELOPMENT_MODE_LOCKED' },
  );
  const modePath = path.join(
    root,
    '.hierarchical-delivery-governance',
    'work-items',
    task.id,
    'development-mode.json',
  );
  const artifact = JSON.parse(await readFile(modePath, 'utf8'));
  await writeFile(modePath, `${JSON.stringify({ ...artifact, mode: 'manual' }, null, 2)}\n`);
  await assert.rejects(
    () => readWorkItemRegistry({ root }),
    { code: 'WORK_ITEM_DEVELOPMENT_MODE_CHANGED' },
  );
});

test('revising a Task invalidates its development mode and returns to mode selection', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  await prepareAndFreeze(root, capabilityDefinition());
  await prepareAndFreeze(root, issueTaskDefinition());
  await selectMode(root, 't-issue-token');
  const before = await readWorkItemRegistry({ root });
  const task = before.workItems.find(({ id }) => id === 't-issue-token');
  const revised = issueTaskDefinition({ goal: 'Issue a signed token with the revised claim contract.' });

  await reviseWorkItem({
    root,
    definition: revised,
    expectedBaselineFingerprint: task.baselineFingerprint,
    confirmed: true,
  });

  const after = await readWorkItemRegistry({ root });
  const revisedTask = after.workItems.find(({ id }) => id === 't-issue-token');
  assert.equal(revisedTask.status, 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION');
  assert.equal(revisedTask.developmentMode, null);
  await assert.rejects(
    () => readFile(path.join(
      root,
      '.hierarchical-delivery-governance',
      'work-items',
      revisedTask.id,
      'development-mode.json',
    )),
    { code: 'ENOENT' },
  );
  await assert.rejects(
    () => buildTaskContext({ root, id: revisedTask.id }),
    { code: 'WORK_ITEM_DEVELOPMENT_MODE_REQUIRED' },
  );
});

test('Task baselines reject parent drift before context creation or dispatch', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  await prepareAndFreeze(root, capabilityDefinition());
  await prepareAndFreeze(root, issueTaskDefinition());
  await selectMode(root, 't-issue-token');

  const capabilityBaselinePath = path.join(
    root, '.hierarchical-delivery-governance', 'work-items', 'c-token-lifecycle', 'baseline.json',
  );
  const baseline = JSON.parse(await readFile(capabilityBaselinePath, 'utf8'));
  baseline.scope = ['src/identity/changed/**'];
  await import('node:fs/promises').then(({ writeFile }) => writeFile(
    capabilityBaselinePath,
    `${JSON.stringify(baseline, null, 2)}\n`,
  ));

  await assert.rejects(
    () => buildTaskContext({ root, id: 't-issue-token' }),
    { code: 'WORK_ITEM_BASELINE_STALE' },
  );
  await assert.rejects(
    () => claimTask({ root, id: 't-issue-token', owner: 'developer-a', operationId: 'op-stale-001' }),
    { code: 'WORK_ITEM_BASELINE_STALE' },
  );
});

test('Capability decomposition can append a Task without invalidating unchanged sibling Task contexts', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  await prepareAndFreeze(root, capabilityDefinition());
  await prepareAndFreeze(root, issueTaskDefinition());
  await selectMode(root, 't-issue-token');
  const before = await readWorkItemRegistry({ root });
  const capability = before.workItems.find(({ id }) => id === 'c-token-lifecycle');
  const revised = capabilityDefinition();
  revised.children.push({
    id: 't-revoke-token',
    kind: 'TASK',
    title: 'Revoke tokens',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001'],
  });
  await claimTask({
    root,
    id: 't-issue-token',
    owner: 'developer-a',
    operationId: 'op-append-safe',
  });

  await reviseWorkItem({
    root,
    definition: revised,
    expectedBaselineFingerprint: capability.baselineFingerprint,
    confirmed: true,
  });

  assert.equal((await buildTaskContext({ root, id: 't-issue-token' })).task.id, 't-issue-token');
  await prepareAndFreeze(root, revokeTaskDefinition());
  const after = await readWorkItemRegistry({ root });
  assert.deepEqual(
    after.workItems.find(({ id }) => id === 'c-token-lifecycle').childIds,
    ['t-issue-token', 't-revoke-token', 't-verify-token'],
  );
  assert.equal(after.workItems.find(({ id }) => id === 't-issue-token').claim.operationId, 'op-append-safe');
});

test('a blocked Task requires an explicit fingerprint-bound retry before it becomes READY again', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  await prepareAndFreeze(root, capabilityDefinition());
  await prepareAndFreeze(root, issueTaskDefinition());
  await selectMode(root, 't-issue-token');
  await claimTask({ root, id: 't-issue-token', owner: 'developer-a', operationId: 'op-blocked' });
  await recordTaskResult({
    root,
    id: 't-issue-token',
    operationId: 'op-blocked',
    status: 'BLOCKED',
    evidence: { path: 'results/blocked.json', sha256: '9'.repeat(64) },
  });
  assert.deepEqual(await listReadyTasks({ root, workItemId: 'd-identity-platform' }), []);
  const registry = await readWorkItemRegistry({ root });
  const task = registry.workItems.find(({ id }) => id === 't-issue-token');
  await assert.rejects(
    () => retryWorkItem({
      root,
      id: task.id,
      expectedBaselineFingerprint: task.baselineFingerprint,
      confirmed: false,
    }),
    { code: 'CONFIRMATION_REQUIRED' },
  );
  await retryWorkItem({
    root,
    id: task.id,
    expectedBaselineFingerprint: task.baselineFingerprint,
    confirmed: true,
  });
  assert.deepEqual(await listReadyTasks({ root, workItemId: 'd-identity-platform' }), ['t-issue-token']);
});

test('Capability dependencies block all consumer Tasks until the provider Capability is verified', async (t) => {
  const root = await fixture(t);
  const delivery = deliveryDefinition();
  delivery.children.push({
    id: 'c-access-control',
    kind: 'CAPABILITY',
    title: 'Access control',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001'],
  });
  const providerCapability = capabilityDefinition({
    children: [capabilityDefinition().children[0]],
  });
  const consumerCapability = {
    ...capabilityDefinition(),
    id: 'c-access-control',
    title: 'Access control',
    goal: 'Enforce access decisions from verified identity contracts.',
    scope: ['src/identity/access/**', 'tests/identity/access/**'],
    decomposition: { status: 'SEALED', dependsOn: ['c-token-lifecycle'] },
    children: [{
      id: 't-enforce-access',
      kind: 'TASK',
      title: 'Enforce access',
      requirementIds: ['R-001'],
      acceptanceIds: ['A-001'],
    }],
  };
  const consumerTask = issueTaskDefinition({
    id: 't-enforce-access',
    parentId: 'c-access-control',
    title: 'Enforce access',
    goal: 'Enforce a decision from verified identity claims.',
    scope: ['src/identity/access/enforce.mjs', 'tests/identity/access/enforce.test.mjs'],
  });
  await prepareAndFreeze(root, delivery);
  await prepareAndFreeze(root, providerCapability);
  await prepareAndFreeze(root, issueTaskDefinition());
  await prepareAndFreeze(root, consumerCapability);
  await prepareAndFreeze(root, consumerTask);
  await selectMode(root, 't-issue-token');
  await selectMode(root, 't-enforce-access', 'manual');
  assert.deepEqual(await listReadyTasks({ root, workItemId: delivery.id }), ['t-issue-token']);
  await claimTask({ root, id: 't-issue-token', owner: 'provider', operationId: 'op-provider' });
  await recordTaskResult({
    root,
    id: 't-issue-token',
    operationId: 'op-provider',
    status: 'IMPLEMENTED',
    evidence: { path: 'results/provider.json', sha256: '7'.repeat(64) },
  });
  await recordWorkItemGate({
    root,
    id: 't-issue-token',
    status: 'PASS',
    evidence: { path: 'results/provider-task-gate.json', sha256: '6'.repeat(64) },
  });
  await recordWorkItemGate({
    root,
    id: 'c-token-lifecycle',
    status: 'PASS',
    evidence: { path: 'results/provider-capability-gate.json', sha256: '5'.repeat(64) },
  });
  assert.deepEqual(await listReadyTasks({ root, workItemId: delivery.id }), ['t-enforce-access']);
  const consumerContext = await buildTaskContext({ root, id: 't-enforce-access' });
  assert.deepEqual(
    consumerContext.capabilityDependencies.map(({ id, status }) => ({ id, status })),
    [{ id: 'c-token-lifecycle', status: 'VERIFIED' }],
  );
});

test('Capability dependency cycles are rejected when the closing baseline is prepared', async (t) => {
  const root = await fixture(t);
  const delivery = deliveryDefinition();
  delivery.children.push({
    id: 'c-access-control',
    kind: 'CAPABILITY',
    title: 'Access control',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001'],
  });
  await prepareAndFreeze(root, delivery);
  await prepareAndFreeze(root, capabilityDefinition({
    decomposition: { status: 'OPEN', dependsOn: ['c-access-control'] },
  }));
  const access = {
    ...capabilityDefinition(),
    id: 'c-access-control',
    title: 'Access control',
    goal: 'Deliver access control.',
    scope: ['src/identity/access/**', 'tests/identity/access/**'],
    decomposition: { status: 'OPEN', dependsOn: ['c-token-lifecycle'] },
    children: [{
      id: 't-enforce-access',
      kind: 'TASK',
      title: 'Enforce access',
      requirementIds: ['R-001'],
      acceptanceIds: ['A-001'],
    }],
  };
  await assert.rejects(
    () => prepareWorkItem({ root, definition: access, hostRuntime: 'codex' }),
    { code: 'WORK_ITEM_DEPENDENCY_CYCLE' },
  );
});

test('registry recovery rejects unsafe work item identities before any package path is resolved', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  const registryPath = path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json');
  const registry = JSON.parse(await readFile(registryPath, 'utf8'));
  registry.workItems[0].id = '../../outside';
  await writeFile(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
  await assert.rejects(
    () => readWorkItemRegistry({ root }),
    { code: 'WORK_ITEM_REGISTRY_INVALID' },
  );
});

test('registry recovery requires schema v3 gate levels and valid promotion history', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, issueTaskDefinition({ parentId: null, gateLevel: 'LIGHT' }));
  const registryPath = path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json');
  const registry = JSON.parse(await readFile(registryPath, 'utf8'));
  delete registry.workItems[0].gateLevel;
  await writeFile(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
  await assert.rejects(
    () => readWorkItemRegistry({ root }),
    { code: 'WORK_ITEM_REGISTRY_INVALID' },
  );

  registry.workItems[0].gateLevel = 'LIGHT';
  registry.promotionHistory = [null];
  await writeFile(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
  await assert.rejects(
    () => readWorkItemRegistry({ root }),
    { code: 'WORK_ITEM_REGISTRY_INVALID' },
  );
});

test('registry recovery rejects a forged completed Delivery without review and user evidence', async (t) => {
  const root = await fixture(t);
  await prepareAndFreeze(root, deliveryDefinition());
  const registryPath = path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json');
  const registry = JSON.parse(await readFile(registryPath, 'utf8'));
  const delivery = registry.workItems.find(({ kind }) => kind === 'DELIVERY');
  delivery.delivery = { status: 'COMPLETED', review: null, userConfirmation: null };
  await writeFile(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
  await assert.rejects(
    () => readWorkItemRegistry({ root }),
    { code: 'WORK_ITEM_REGISTRY_INVALID' },
  );
});
