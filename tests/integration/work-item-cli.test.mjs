import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { runHierarchicalCli } from '../../src/cli/hierarchical.mjs';
import { sha256Bytes } from '../../src/core/hash.mjs';
import {
  capabilityDefinition,
  issueTaskDefinition,
  deliveryDefinition,
} from '../helpers/work-item-definitions.mjs';

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'delivery-governance-work-item-cli-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function putJson(root, name, value) {
  await writeFile(path.join(root, name), `${JSON.stringify(value)}\n`);
  return name;
}

async function putEvidenceReference(root, name, artifact) {
  const artifactName = `${name}.json`;
  const bytes = Buffer.from(`${JSON.stringify(artifact, null, 2)}\n`, 'utf8');
  await writeFile(path.join(root, artifactName), bytes);
  return putJson(root, `${name}-reference.json`, {
    path: artifactName,
    sha256: sha256Bytes(bytes),
  });
}

async function invoke(root, argv) {
  const out = [];
  const err = [];
  const exitCode = await runHierarchicalCli(argv, {
    cwd: root,
    stdout: (value) => out.push(value),
    stderr: (value) => err.push(value),
    now: () => '2026-07-16T00:00:00.000Z',
  });
  return { exitCode, out: out.join(''), err: err.join('') };
}

async function selectMode(root, id, mode = 'active', confirmed = true) {
  const registry = JSON.parse(await readFile(
    path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json'),
    'utf8',
  ));
  const entry = registry.workItems.find((candidate) => candidate.id === id);
  const argv = [
    'select-development-mode', '--item', id,
    '--development-mode', mode,
    '--expected-baseline', entry.baselineFingerprint,
  ];
  if (confirmed) argv.push('--confirmed');
  argv.push('--json');
  return invoke(root, argv);
}

function result(output) {
  return JSON.parse(output).result;
}

test('CLI manages a hierarchical Task from preparation through verified evidence', async (t) => {
  const root = await fixture(t);
  const delivery = await putJson(root, 'delivery.json', deliveryDefinition());
  const capability = await putJson(root, 'capability.json', capabilityDefinition());
  const task = await putJson(root, 'task.json', issueTaskDefinition());
  const evidence = await putJson(root, 'evidence.json', {
    path: 'evidence/t-issue-token.json',
    sha256: 'a'.repeat(64),
  });

  for (const definition of [delivery, capability, task]) {
    const prepared = await invoke(root, [
      'prepare-item', '--definition', definition, '--host-runtime', 'codex', '--json',
    ]);
    assert.equal(prepared.exitCode, 0, prepared.err);
    const preparedItem = result(prepared.out);
    const frozen = await invoke(root, [
      'freeze-item', '--item', preparedItem.id,
      '--expected-baseline', preparedItem.baselineFingerprint,
      '--confirmed', '--json',
    ]);
    assert.equal(frozen.exitCode, 0, frozen.err);
  }

  let ready = await invoke(root, ['ready-tasks', '--item', 'd-identity-platform', '--json']);
  assert.equal(ready.exitCode, 0, ready.err);
  assert.deepEqual(result(ready.out), []);

  const blockedContext = await invoke(root, ['task-context', '--item', 't-issue-token', '--json']);
  assert.equal(blockedContext.exitCode, 1);
  assert.match(blockedContext.err, /WORK_ITEM_DEVELOPMENT_MODE_REQUIRED/);

  const blockedClaim = await invoke(root, [
    'claim-task', '--item', 't-issue-token', '--owner', 'agent-a', '--operation', 'op-premature', '--json',
  ]);
  assert.equal(blockedClaim.exitCode, 1);
  assert.match(blockedClaim.err, /WORK_ITEM_DEVELOPMENT_MODE_REQUIRED/);

  const unconfirmed = await selectMode(root, 't-issue-token', 'active', false);
  assert.equal(unconfirmed.exitCode, 1);
  assert.match(unconfirmed.err, /CONFIRMATION_REQUIRED/);

  const selected = await selectMode(root, 't-issue-token');
  assert.equal(selected.exitCode, 0, selected.err);
  assert.equal(result(selected.out).status, 'FROZEN');
  assert.equal(result(selected.out).developmentMode.mode, 'active');

  ready = await invoke(root, ['ready-tasks', '--item', 'd-identity-platform', '--json']);
  assert.equal(ready.exitCode, 0, ready.err);
  assert.deepEqual(result(ready.out), ['t-issue-token']);

  const context = await invoke(root, ['task-context', '--item', 't-issue-token', '--json']);
  assert.equal(context.exitCode, 0, context.err);
  assert.equal(result(context.out).rules.inheritConversation, false);

  const claimed = await invoke(root, [
    'claim-task', '--item', 't-issue-token', '--owner', 'agent-a', '--operation', 'op-1', '--json',
  ]);
  assert.equal(claimed.exitCode, 0, claimed.err);
  assert.equal(result(claimed.out).status, 'CLAIMED');

  const implemented = await invoke(root, [
    'task-result', '--item', 't-issue-token', '--operation', 'op-1',
    '--status', 'IMPLEMENTED', '--evidence', evidence, '--json',
  ]);
  assert.equal(implemented.exitCode, 0, implemented.err);

  const gated = await invoke(root, [
    'gate-item', '--item', 't-issue-token', '--status', 'PASS', '--evidence', evidence, '--json',
  ]);
  assert.equal(gated.exitCode, 0, gated.err);
  assert.equal(result(gated.out).status, 'VERIFIED');

  const registry = JSON.parse(await readFile(
    path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json'),
    'utf8',
  ));
  assert.equal((await readdir(root)).includes('.ai-dev-loop'), false);
  assert.equal(registry.workItems.find(({ id }) => id === 't-issue-token').status, 'VERIFIED');

  const revisedCapability = capabilityDefinition();
  revisedCapability.children.push({
    id: 't-revoke-token',
    kind: 'TASK',
    title: 'Revoke tokens',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001'],
  });
  const revisionFile = await putJson(root, 'capability-revision.json', revisedCapability);
  const expected = registry.workItems.find(({ id }) => id === 'c-token-lifecycle').baselineFingerprint;
  const revision = await invoke(root, [
    'revise-item', '--definition', revisionFile, '--expected-baseline', expected,
    '--confirmed', '--json',
  ]);
  assert.equal(revision.exitCode, 0, revision.err);
  assert.equal(result(revision.out).baselineRevision, 2);
});

test('CLI approves and freezes once, then emits a reusable manual development prompt', async (t) => {
  const root = await fixture(t);
  const taskValue = issueTaskDefinition({
    id: 't-manual-handoff',
    parentId: null,
    gateLevel: 'LIGHT',
  });
  const task = await putJson(root, 'manual-task.json', taskValue);

  const unconfirmed = await invoke(root, [
    'approve-item', '--definition', task, '--host-runtime', 'codex', '--json',
  ]);
  assert.equal(unconfirmed.exitCode, 1);
  assert.match(unconfirmed.err, /CONFIRMATION_REQUIRED/);
  assert.equal((await readdir(root)).includes('.hierarchical-delivery-governance'), false);

  const approved = await invoke(root, [
    'approve-item', '--definition', task, '--host-runtime', 'codex', '--confirmed', '--json',
  ]);
  assert.equal(approved.exitCode, 0, approved.err);
  assert.equal(result(approved.out).stage, 'BASELINE_FROZEN');

  const repeatedApproval = await invoke(root, [
    'approve-item', '--definition', task, '--host-runtime', 'codex', '--confirmed', '--json',
  ]);
  assert.equal(repeatedApproval.exitCode, 0, repeatedApproval.err);
  assert.equal(result(repeatedApproval.out).idempotent, true);

  const selected = await selectMode(root, taskValue.id, 'manual');
  assert.equal(selected.exitCode, 0, selected.err);

  const contextResult = await invoke(root, ['task-context', '--item', taskValue.id, '--json']);
  assert.equal(contextResult.exitCode, 0, contextResult.err);
  const context = result(contextResult.out);
  assert.match(context.handoffPrompt, /Implement the following frozen Task/);
  assert.match(context.handoffPrompt, new RegExp(taskValue.id));
  assert.match(context.handoffPrompt, new RegExp(context.task.baselineFingerprint));
  assert.match(context.handoffPrompt, /Return exactly one of IMPLEMENTED or BLOCKED/);
  assert.match(context.handoffPrompt, /```json/);

  const handoff = await readFile(path.join(
    root,
    '.hierarchical-delivery-governance',
    'work-items',
    taskValue.id,
    'development-handoff.md',
  ), 'utf8');
  assert.equal(handoff, context.handoffPrompt);
});

test('CLI promotes a frozen root Task under a separately frozen Capability', async (t) => {
  const root = await fixture(t);
  const taskValue = issueTaskDefinition({ id: 't-standalone', parentId: null, gateLevel: 'LIGHT' });
  const capabilityValue = capabilityDefinition({
    parentId: null,
    children: [{
      id: taskValue.id,
      kind: 'TASK',
      title: taskValue.title,
      requirementIds: ['R-001'],
      acceptanceIds: ['A-001'],
    }],
  });
  for (const [name, definition] of [['task.json', taskValue], ['capability.json', capabilityValue]]) {
    await putJson(root, name, definition);
    const prepared = await invoke(root, [
      'prepare-item', '--definition', name, '--host-runtime', 'codex', '--json',
    ]);
    assert.equal(prepared.exitCode, 0, prepared.err);
    const item = result(prepared.out);
    const frozen = await invoke(root, [
      'freeze-item', '--item', item.id, '--expected-baseline', item.baselineFingerprint,
      '--confirmed', '--json',
    ]);
    assert.equal(frozen.exitCode, 0, frozen.err);
  }
  const registry = JSON.parse(await readFile(
    path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json'),
    'utf8',
  ));
  const task = registry.workItems.find(({ id }) => id === taskValue.id);
  const capability = registry.workItems.find(({ id }) => id === capabilityValue.id);
  const promoted = await invoke(root, [
    'promote-item', '--item', task.id, '--parent', capability.id,
    '--expected-baseline', task.baselineFingerprint,
    '--expected-parent-baseline', capability.baselineFingerprint,
    '--confirmed', '--json',
  ]);
  assert.equal(promoted.exitCode, 0, promoted.err);
  assert.equal(result(promoted.out).parentId, capability.id);
});

test('hierarchical writes in the implementation repository require explicit dogfood on every mutation', async (t) => {
  const root = await fixture(t);
  await putJson(root, 'package.json', { name: 'hierarchical-delivery-governance', private: true });
  const delivery = await putJson(root, 'delivery.json', deliveryDefinition());

  const blockedPrepare = await invoke(root, [
    'prepare-item', '--definition', delivery, '--host-runtime', 'codex', '--json',
  ]);
  assert.equal(blockedPrepare.exitCode, 1);
  assert.match(blockedPrepare.err, /SELF_HOSTING_DOGFOOD_REQUIRED/);
  assert.equal((await readdir(root)).includes('.hierarchical-delivery-governance'), false);

  const prepared = await invoke(root, [
    'prepare-item', '--definition', delivery, '--host-runtime', 'codex', '--dogfood', '--json',
  ]);
  assert.equal(prepared.exitCode, 0, prepared.err);
  const preparedItem = result(prepared.out);

  const blockedFreeze = await invoke(root, [
    'freeze-item', '--item', 'd-identity-platform', '--expected-baseline', preparedItem.baselineFingerprint,
    '--confirmed', '--json',
  ]);
  assert.equal(blockedFreeze.exitCode, 1);
  assert.match(blockedFreeze.err, /SELF_HOSTING_DOGFOOD_REQUIRED/);

  const frozen = await invoke(root, [
    'freeze-item', '--item', 'd-identity-platform', '--expected-baseline', preparedItem.baselineFingerprint,
    '--confirmed', '--dogfood', '--json',
  ]);
  assert.equal(frozen.exitCode, 0, frozen.err);
});

test('CLI retries a blocked coordination item and persists a reviewed user-confirmed Delivery', async (t) => {
  const root = await fixture(t);
  const capabilityValue = capabilityDefinition({
    children: [capabilityDefinition().children[0]],
  });
  const definitions = [
    await putJson(root, 'delivery.json', deliveryDefinition()),
    await putJson(root, 'capability.json', capabilityValue),
    await putJson(root, 'task.json', issueTaskDefinition()),
  ];
  const evidence = await putJson(root, 'evidence.json', {
    path: 'evidence/result.json',
    sha256: 'b'.repeat(64),
  });
  const reviewEvidence = await putEvidenceReference(root, 'independent-review', {
    schemaVersion: 1,
    kind: 'INDEPENDENT_REVIEW',
    reviewer: 'fresh-review-agent',
    isolation: 'FRESH_READ_ONLY',
    verdict: 'PASS',
    findings: { p0: 0, p1: 0 },
  });
  const confirmationEvidence = await putEvidenceReference(root, 'user-confirmation', {
    schemaVersion: 1,
    kind: 'USER_CONFIRMATION',
    confirmedBy: 'delivery-owner',
    decision: 'CONFIRMED',
  });
  for (const definition of definitions) {
    const prepared = await invoke(root, [
      'prepare-item', '--definition', definition, '--host-runtime', 'codex', '--json',
    ]);
    assert.equal(prepared.exitCode, 0, prepared.err);
    const item = result(prepared.out);
    const frozen = await invoke(root, [
      'freeze-item', '--item', item.id, '--expected-baseline', item.baselineFingerprint,
      '--confirmed', '--json',
    ]);
    assert.equal(frozen.exitCode, 0, frozen.err);
  }

  const selected = await selectMode(root, 't-issue-token');
  assert.equal(selected.exitCode, 0, selected.err);

  assert.equal((await invoke(root, [
    'claim-task', '--item', 't-issue-token', '--owner', 'agent-a', '--operation', 'op-cli', '--json',
  ])).exitCode, 0);
  assert.equal((await invoke(root, [
    'task-result', '--item', 't-issue-token', '--operation', 'op-cli',
    '--status', 'IMPLEMENTED', '--evidence', evidence, '--json',
  ])).exitCode, 0);
  assert.equal((await invoke(root, [
    'gate-item', '--item', 't-issue-token', '--status', 'PASS', '--evidence', evidence, '--json',
  ])).exitCode, 0);
  assert.equal((await invoke(root, [
    'gate-item', '--item', 'c-token-lifecycle', '--status', 'FAIL', '--evidence', evidence, '--json',
  ])).exitCode, 0);

  let registry = JSON.parse(await readFile(
    path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json'),
    'utf8',
  ));
  const capability = registry.workItems.find(({ id }) => id === 'c-token-lifecycle');
  const retried = await invoke(root, [
    'retry-item', '--item', capability.id, '--expected-baseline', capability.baselineFingerprint,
    '--confirmed', '--json',
  ]);
  assert.equal(retried.exitCode, 0, retried.err);
  assert.equal((await invoke(root, [
    'gate-item', '--item', capability.id, '--status', 'PASS', '--evidence', evidence, '--json',
  ])).exitCode, 0);
  assert.equal((await invoke(root, [
    'gate-item', '--item', 'd-identity-platform', '--status', 'PASS', '--evidence', evidence, '--json',
  ])).exitCode, 0);
  assert.equal((await invoke(root, [
    'delivery-item', '--item', 'd-identity-platform', '--action', 'INDEPENDENT_REVIEW_PASS',
    '--evidence', reviewEvidence, '--json',
  ])).exitCode, 0);
  const delivered = await invoke(root, [
    'delivery-item', '--item', 'd-identity-platform', '--action', 'USER_CONFIRMED',
    '--evidence', confirmationEvidence, '--json',
  ]);
  assert.equal(delivered.exitCode, 0, delivered.err);
  assert.equal(result(delivered.out).delivery.status, 'COMPLETED');

  registry = JSON.parse(await readFile(
    path.join(root, '.hierarchical-delivery-governance', 'work-item-registry.json'),
    'utf8',
  ));
  assert.equal(
    registry.workItems.find(({ id }) => id === 'd-identity-platform').delivery.status,
    'COMPLETED',
  );
});
