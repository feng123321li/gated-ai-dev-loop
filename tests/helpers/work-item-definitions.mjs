function exactPlannedPath(pattern) {
  return pattern.endsWith('/**') ? `${pattern.slice(0, -3)}/planned-change.mjs` : pattern;
}

function testPlan(definition) {
  return [{
    acceptanceIds: definition.acceptance.map(({ id }) => id),
    approach: 'Run the frozen command and verify every mapped acceptance result.',
    commandIndexes: [0],
  }];
}

function taskDevelopmentPlan(definition) {
  return {
    purpose: `Implement the reviewed change for ${definition.title}.`,
    scenarios: [{
      kind: 'API',
      title: `${definition.title} behavior`,
      description: definition.goal,
      requirementIds: definition.requirements.map(({ id }) => id),
    }],
    fileChanges: definition.scope.map((scopePath) => ({
      path: exactPlannedPath(scopePath),
      action: 'MODIFY',
      purpose: `Implement or verify ${definition.title} within the frozen scope.`,
    })),
    interfaces: [{
      name: `${definition.title} contract`,
      kind: 'FUNCTION',
      action: 'MODIFY',
      location: exactPlannedPath(definition.scope[0]),
      currentContract: 'The current behavior does not satisfy the frozen requirement.',
      targetContract: definition.acceptance[0].expectedResult,
      requirementIds: definition.requirements.map(({ id }) => id),
    }],
    logic: [`Implement ${definition.goal}`, 'Keep all changes inside the reviewed file list.'],
    dataAndTransactions: [],
    compatibility: ['Preserve callers outside the frozen interface change.'],
    testPlan: testPlan(definition),
    reviewPoints: ['Confirm the target contract and exact changed files before freezing.'],
  };
}

function coordinationDevelopmentPlan(definition) {
  const ids = new Set(definition.children.map(({ id }) => id));
  return {
    purpose: `Coordinate the reviewed child work for ${definition.title}.`,
    childPlans: definition.children.map((child) => ({
      id: child.id,
      purpose: `Deliver ${child.title}.`,
      deliverables: [`Verified ${child.kind} result for ${child.title}.`],
      requirementIds: child.requirementIds,
      acceptanceIds: child.acceptanceIds,
      dependsOn: child.id === 't-verify-token' && ids.has('t-issue-token') ? ['t-issue-token'] : [],
    })),
    sharedContracts: definition.children.length > 1 ? [{
      name: `${definition.title} shared contract`,
      kind: 'SCHEMA',
      description: 'The provider output is consumed by the dependent child without implicit assumptions.',
      providerChildIds: [definition.children[0].id],
      consumerChildIds: [definition.children.at(-1).id],
      requirementIds: definition.requirements.map(({ id }) => id),
    }] : [],
    integrationFlow: ['Complete provider children before consumers, then run the frozen aggregate gate.'],
    deliveryWaves: definition.children.map((child, index) => ({
      order: index + 1,
      name: `Wave ${index + 1}`,
      childIds: [child.id],
      exitCriteria: `${child.id} is verified before the next wave begins.`,
    })),
    testPlan: testPlan(definition),
    reviewPoints: ['Confirm child responsibilities, shared contracts, dependency order, and aggregate acceptance.'],
  };
}

export function developmentPlanFor(definition) {
  return definition.kind === 'TASK'
    ? taskDevelopmentPlan(definition)
    : coordinationDevelopmentPlan(definition);
}

export function deliveryDefinition(overrides = {}) {
  const definition = {
    schemaVersion: 3,
    gateLevel: 'FULL',
    id: 'd-identity-platform',
    kind: 'DELIVERY',
    title: 'Identity platform',
    goal: 'Deliver an independently verifiable identity platform.',
    scope: ['src/identity/**', 'tests/identity/**'],
    nonGoals: ['Do not deploy the platform.'],
    requirements: [
      { id: 'R-001', text: 'The delivery must provide token issuance and token verification capabilities.' },
    ],
    acceptance: [
      { id: 'A-001', requirementIds: ['R-001'], expectedResult: 'Both identity capabilities pass the delivery gate.' },
    ],
    decomposition: { status: 'SEALED' },
    children: [
      {
        id: 'c-token-lifecycle',
        kind: 'CAPABILITY',
        title: 'Token lifecycle',
        requirementIds: ['R-001'],
        acceptanceIds: ['A-001'],
      },
    ],
    testCommands: [['node', '--test', 'tests/identity/delivery.test.mjs']],
    risks: ['A parent contract change can invalidate descendant baselines.'],
    decisions: ['Delivery and Capability baselines coordinate work; only Task baselines authorize implementation.'],
    ...overrides,
  };
  definition.developmentPlan = Object.hasOwn(overrides, 'developmentPlan')
    ? overrides.developmentPlan
    : coordinationDevelopmentPlan(definition);
  return definition;
}

export function capabilityDefinition(overrides = {}) {
  const definition = {
    schemaVersion: 3,
    gateLevel: 'FULL',
    id: 'c-token-lifecycle',
    kind: 'CAPABILITY',
    parentId: 'd-identity-platform',
    title: 'Token lifecycle',
    goal: 'Deliver token issuance and verification as one integrated capability.',
    scope: ['src/identity/token/**', 'tests/identity/token/**'],
    nonGoals: ['Do not implement user-interface flows.'],
    requirements: [
      { id: 'R-001', text: 'The capability must issue and verify signed tokens.' },
    ],
    acceptance: [
      { id: 'A-001', requirementIds: ['R-001'], expectedResult: 'Issued tokens can be verified by the consumer.' },
    ],
    decomposition: { status: 'SEALED', dependsOn: [] },
    children: [
      {
        id: 't-issue-token',
        kind: 'TASK',
        title: 'Issue tokens',
        requirementIds: ['R-001'],
        acceptanceIds: ['A-001'],
      },
      {
        id: 't-verify-token',
        kind: 'TASK',
        title: 'Verify tokens',
        requirementIds: ['R-001'],
        acceptanceIds: ['A-001'],
      },
    ],
    testCommands: [['node', '--test', 'tests/identity/token/integration.test.mjs']],
    risks: ['Issuer and verifier must use the same token contract.'],
    decisions: ['The capability gate runs only after both Tasks are verified.'],
    ...overrides,
  };
  definition.developmentPlan = Object.hasOwn(overrides, 'developmentPlan')
    ? overrides.developmentPlan
    : coordinationDevelopmentPlan(definition);
  return definition;
}

export function issueTaskDefinition(overrides = {}) {
  const definition = {
    schemaVersion: 3,
    gateLevel: 'FULL',
    id: 't-issue-token',
    kind: 'TASK',
    parentId: 'c-token-lifecycle',
    title: 'Issue tokens',
    goal: 'Issue a signed token from validated claims.',
    scope: ['src/identity/token/issue.mjs', 'tests/identity/token/issue.test.mjs'],
    nonGoals: ['Do not verify tokens in this Task.'],
    requirements: [
      { id: 'R-001', text: 'The Task must produce a signed token from validated claims.' },
    ],
    acceptance: [
      { id: 'A-001', requirementIds: ['R-001'], expectedResult: 'The issuer test observes a signed token.' },
    ],
    execution: {
      dependsOn: [],
      inputs: ['Validated claims'],
      outputs: ['Signed token contract'],
    },
    testCommands: [['node', '--test', 'tests/identity/token/issue.test.mjs']],
    risks: ['The signing contract must remain inside the Capability scope.'],
    decisions: ['This Task is an executable leaf and cannot contain child work items.'],
    ...overrides,
  };
  definition.developmentPlan = Object.hasOwn(overrides, 'developmentPlan')
    ? overrides.developmentPlan
    : taskDevelopmentPlan(definition);
  return definition;
}

export function verifyTaskDefinition(overrides = {}) {
  const definition = {
    ...issueTaskDefinition(),
    id: 't-verify-token',
    title: 'Verify tokens',
    goal: 'Verify a signed token produced by the issuer.',
    scope: ['src/identity/token/verify.mjs', 'tests/identity/token/verify.test.mjs'],
    nonGoals: ['Do not issue tokens in this Task.'],
    requirements: [
      { id: 'R-001', text: 'The Task must verify tokens produced by the issuer.' },
    ],
    acceptance: [
      { id: 'A-001', requirementIds: ['R-001'], expectedResult: 'The verifier accepts the issuer output.' },
    ],
    execution: {
      dependsOn: ['t-issue-token'],
      inputs: ['Signed token contract'],
      outputs: ['Verified claims'],
    },
    testCommands: [['node', '--test', 'tests/identity/token/verify.test.mjs']],
    risks: ['The verifier must wait for the issuer contract.'],
    decisions: ['The dependency is explicit and blocks READY until the issuer is verified.'],
    ...overrides,
  };
  definition.developmentPlan = Object.hasOwn(overrides, 'developmentPlan')
    ? overrides.developmentPlan
    : taskDevelopmentPlan(definition);
  return definition;
}

export function revokeTaskDefinition(overrides = {}) {
  const definition = {
    ...issueTaskDefinition(),
    id: 't-revoke-token',
    title: 'Revoke tokens',
    goal: 'Revoke an issued token.',
    scope: ['src/identity/token/revoke.mjs', 'tests/identity/token/revoke.test.mjs'],
    nonGoals: ['Do not change token issuance in this Task.'],
    requirements: [{ id: 'R-001', text: 'The Task must revoke a known issued token.' }],
    acceptance: [{ id: 'A-001', requirementIds: ['R-001'], expectedResult: 'The revoked token is rejected.' }],
    execution: {
      dependsOn: ['t-issue-token'],
      inputs: ['An issued token identifier'],
      outputs: ['A persisted revocation result'],
    },
    testCommands: [['node', '--test', 'tests/identity/token/revoke.test.mjs']],
    risks: ['Revocation state must remain deterministic.'],
    decisions: ['Use the existing token identifier as the revocation key.'],
    ...overrides,
  };
  definition.developmentPlan = Object.hasOwn(overrides, 'developmentPlan')
    ? overrides.developmentPlan
    : taskDevelopmentPlan(definition);
  return definition;
}
