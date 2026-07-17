import test from 'node:test';
import assert from 'node:assert/strict';

import {
  renderDevelopmentReview,
  renderWorkItemBaseline,
  resolveSelfHostingPolicy,
  validateWorkItemDefinition,
  workItemChildContractFingerprint,
  workItemContractFingerprint,
} from '../../src/work-items/model.mjs';
import {
  capabilityDefinition,
  developmentPlanFor,
  issueTaskDefinition,
  deliveryDefinition,
} from '../helpers/work-item-definitions.mjs';

test('Delivery, Capability, and Task definitions use distinct coordination and execution contracts', () => {
  const delivery = validateWorkItemDefinition(deliveryDefinition());
  const capability = validateWorkItemDefinition(capabilityDefinition(), { parent: delivery });
  const task = validateWorkItemDefinition(issueTaskDefinition(), { parent: capability });

  assert.equal(delivery.authorityKind, 'COORDINATION');
  assert.equal(capability.authorityKind, 'COORDINATION');
  assert.equal(capability.decomposition.status, 'SEALED');
  assert.equal(task.authorityKind, 'EXECUTION');
  assert.equal(task.parentContractFingerprint, workItemChildContractFingerprint(capability, task.id));
  assert.match(renderWorkItemBaseline(delivery), /## Children\n- c-token-lifecycle \[CAPABILITY\]/);
  assert.match(renderWorkItemBaseline(task), /## Execution\n- Depends on: none/);
  assert.doesNotMatch(renderWorkItemBaseline(task), /## Children/);
});

test('shallow governance accepts a root Task or root Capability without invented ancestors', () => {
  const rootTask = validateWorkItemDefinition(issueTaskDefinition({ parentId: null }));
  const rootCapability = validateWorkItemDefinition(capabilityDefinition({ parentId: null }));
  const nestedTask = validateWorkItemDefinition(issueTaskDefinition(), { parent: rootCapability });

  assert.equal(rootTask.parentId, null);
  assert.equal(rootTask.parentContractFingerprint, null);
  assert.equal(rootCapability.parentId, null);
  assert.equal(rootCapability.parentContractFingerprint, null);
  assert.equal(nestedTask.parentId, rootCapability.id);
});

test('shallow roots cannot declare dependencies that require a missing aggregation level', () => {
  assert.throws(
    () => validateWorkItemDefinition(issueTaskDefinition({
      parentId: null,
      execution: { ...issueTaskDefinition().execution, dependsOn: ['t-contract-provider'] },
    })),
    { code: 'WORK_ITEM_DEPENDENCY_INVALID' },
  );
  assert.throws(
    () => validateWorkItemDefinition(capabilityDefinition({
      parentId: null,
      decomposition: { status: 'SEALED', dependsOn: ['c-contract-provider'] },
    })),
    { code: 'WORK_ITEM_DEPENDENCY_INVALID' },
  );
});

test('gate level is a required machine contract and LIGHT is valid only for Task', () => {
  const light = validateWorkItemDefinition(issueTaskDefinition({ parentId: null, gateLevel: 'LIGHT' }));
  assert.equal(light.gateLevel, 'LIGHT');
  assert.match(renderWorkItemBaseline(light), /Gate Level: LIGHT/);

  assert.throws(
    () => validateWorkItemDefinition(issueTaskDefinition({ parentId: null, gateLevel: 'NONE' })),
    { code: 'WORK_ITEM_GATE_LEVEL_INVALID' },
  );
  assert.throws(
    () => validateWorkItemDefinition(capabilityDefinition({ parentId: null, gateLevel: 'LIGHT' })),
    { code: 'WORK_ITEM_GATE_LEVEL_INVALID' },
  );
  const missing = issueTaskDefinition({ parentId: null });
  delete missing.gateLevel;
  assert.throws(
    () => validateWorkItemDefinition(missing),
    { code: 'WORK_ITEM_DEFINITION_INVALID' },
  );
});

test('new work items require a structured development plan with exact in-scope Task files', () => {
  const missing = issueTaskDefinition({ parentId: null });
  delete missing.developmentPlan;
  assert.throws(
    () => validateWorkItemDefinition(missing),
    { code: 'WORK_ITEM_DEFINITION_INVALID' },
  );

  const outOfScope = issueTaskDefinition({ parentId: null });
  outOfScope.developmentPlan.fileChanges[0].path = 'src/outside/change.mjs';
  assert.throws(
    () => validateWorkItemDefinition(outOfScope),
    { code: 'WORK_ITEM_DEVELOPMENT_PLAN_INVALID' },
  );
});

test('human review rendering is hierarchy-specific and fingerprint-bound', () => {
  const state = {
    baselineFingerprint: 'a'.repeat(64),
    review: {
      status: 'WAITING_FOR_HUMAN_REVIEW',
      reviewedBy: null,
      reviewedAt: null,
    },
  };
  const taskReview = renderDevelopmentReview(validateWorkItemDefinition(
    issueTaskDefinition({ parentId: null }),
  ), state);
  const capabilityReview = renderDevelopmentReview(validateWorkItemDefinition(
    capabilityDefinition({ parentId: null }),
  ), state);
  const deliveryReview = renderDevelopmentReview(validateWorkItemDefinition(deliveryDefinition()), state);
  assert.match(taskReview, /文件改动/);
  assert.match(taskReview, /接口与功能契约/);
  assert.match(capabilityReview, /Task 开发内容/);
  assert.match(capabilityReview, /跨 Task 接口与共享契约/);
  assert.match(deliveryReview, /Capability 开发内容/);
  assert.match(deliveryReview, /跨 Capability 接口与共享契约/);
  for (const review of [taskReview, capabilityReview, deliveryReview]) {
    assert.match(review, /等待人工评审/);
    assert.match(review, new RegExp('a{64}'));
    assert.match(review, /测试与验收映射/);
  }
});

test('hierarchy validation rejects Workstream entities, unplanned children, scope expansion, and Task children', () => {
  const delivery = validateWorkItemDefinition(deliveryDefinition());
  const capability = validateWorkItemDefinition(capabilityDefinition(), { parent: delivery });

  assert.throws(
    () => validateWorkItemDefinition(deliveryDefinition({ kind: 'WORKSTREAM' })),
    { code: 'WORK_ITEM_KIND_INVALID' },
  );
  assert.throws(
    () => validateWorkItemDefinition(capabilityDefinition({ id: 'c-unplanned' }), { parent: delivery }),
    { code: 'WORK_ITEM_PARENT_PLAN_MISMATCH' },
  );
  assert.throws(
    () => validateWorkItemDefinition(capabilityDefinition({ scope: ['src/payments/**'] }), { parent: delivery }),
    { code: 'WORK_ITEM_SCOPE_EXPANDED' },
  );
  assert.throws(
    () => validateWorkItemDefinition(capabilityDefinition({ scope: ['src/identity/**/generated.*'] }), { parent: delivery }),
    { code: 'WORK_ITEM_SCOPE_INVALID' },
  );
  assert.throws(
    () => validateWorkItemDefinition(issueTaskDefinition({ children: [] }), { parent: capability }),
    { code: 'WORK_ITEM_TASK_NOT_LEAF' },
  );
});

test('contract fingerprints are deterministic and exclude presentation-only ordering', () => {
  const first = validateWorkItemDefinition(deliveryDefinition());
  const second = validateWorkItemDefinition({
    ...deliveryDefinition(),
    scope: [...deliveryDefinition().scope].reverse(),
  });
  assert.equal(workItemContractFingerprint(first), workItemContractFingerprint(second));
});

test('a child contract fingerprint ignores unrelated siblings but detects its own contract changes', () => {
  const delivery = validateWorkItemDefinition(deliveryDefinition());
  const original = validateWorkItemDefinition(capabilityDefinition(), { parent: delivery });
  const withSibling = capabilityDefinition();
  withSibling.decomposition = { status: 'OPEN', dependsOn: [] };
  withSibling.children.push({
    id: 't-revoke-token',
    kind: 'TASK',
    title: 'Revoke tokens',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001'],
  });
  withSibling.developmentPlan = developmentPlanFor(withSibling);
  withSibling.developmentPlan.sharedContracts = capabilityDefinition().developmentPlan.sharedContracts;
  const revised = validateWorkItemDefinition(withSibling, { parent: delivery });
  assert.equal(
    workItemChildContractFingerprint(original, 't-issue-token'),
    workItemChildContractFingerprint(revised, 't-issue-token'),
  );
  revised.children.find(({ id }) => id === 't-issue-token').title = 'Changed contract title';
  assert.notEqual(
    workItemChildContractFingerprint(original, 't-issue-token'),
    workItemChildContractFingerprint(revised, 't-issue-token'),
  );
});

test('Capability dependencies reference planned sibling capabilities and reject self-dependency', () => {
  const deliverySource = deliveryDefinition();
  deliverySource.children.unshift({
    id: 'c-access-control',
    kind: 'CAPABILITY',
    title: 'Access control',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001'],
  });
  deliverySource.developmentPlan = developmentPlanFor(deliverySource);
  deliverySource.developmentPlan.childPlans
    .find(({ id }) => id === 'c-token-lifecycle').dependsOn = ['c-access-control'];
  const delivery = validateWorkItemDefinition(deliverySource);
  const capability = validateWorkItemDefinition(capabilityDefinition({
    decomposition: { status: 'OPEN', dependsOn: ['c-access-control'] },
  }), { parent: delivery });
  assert.deepEqual(capability.decomposition.dependsOn, ['c-access-control']);
  assert.throws(
    () => validateWorkItemDefinition(capabilityDefinition({
      decomposition: { status: 'OPEN', dependsOn: ['c-token-lifecycle'] },
    }), { parent: delivery }),
    { code: 'WORK_ITEM_DEPENDENCY_INVALID' },
  );
});

test('self-hosting maintenance skips runtime packages unless dogfooding is explicit', () => {
  assert.deepEqual(resolveSelfHostingPolicy({
    packageName: 'hierarchical-delivery-governance',
    explicitDogfood: false,
  }), {
    route: 'SELF_HOSTING_MAINTENANCE',
    createsRuntimePackage: false,
    reason: 'HIERARCHICAL_GOVERNANCE_SELF_MAINTENANCE',
  });
  assert.equal(resolveSelfHostingPolicy({
    packageName: 'hierarchical-delivery-governance',
    explicitDogfood: true,
  }).route, 'STANDARD_HIERARCHICAL_GOVERNANCE');
  assert.equal(resolveSelfHostingPolicy({
    packageName: 'another-delivery',
    explicitDogfood: false,
  }).route, 'STANDARD_HIERARCHICAL_GOVERNANCE');
});
