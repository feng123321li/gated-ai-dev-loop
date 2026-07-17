import path from 'node:path';

import { canonicalJson } from '../baseline/sources.mjs';
import { normalizeTestArgv } from '../baseline/test-command.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { sha256Bytes } from '../core/hash.mjs';

export const WORK_ITEM_SCHEMA_VERSION = 3;
export const WORK_ITEM_KINDS = Object.freeze(['DELIVERY', 'CAPABILITY', 'TASK']);
export const WORK_ITEM_GATE_LEVELS = Object.freeze(['LIGHT', 'FULL']);
export const WORK_ITEM_CHANGE_SCENARIOS = Object.freeze([
  'API', 'DOMAIN', 'DATA', 'MIGRATION', 'CONFIG', 'UI', 'INTEGRATION', 'REFACTOR',
  'TEST', 'DOCS', 'SECURITY', 'PERFORMANCE', 'BUILD', 'OTHER',
]);
export const WORK_ITEM_INTERFACE_KINDS = Object.freeze([
  'HTTP_ENDPOINT', 'RPC', 'FUNCTION', 'METHOD', 'CLASS', 'EVENT', 'SCHEMA', 'CONFIG',
  'CLI', 'UI', 'FILE_FORMAT', 'OTHER',
]);
export const WORK_ITEM_AUTHORITIES = Object.freeze({
  DELIVERY: 'COORDINATION',
  CAPABILITY: 'COORDINATION',
  TASK: 'EXECUTION',
});

const ITEM_ID = /^[a-z0-9][a-z0-9._-]*$/;
const TRACE_ID = /^(?:R|A)-(?:00[1-9]|0[1-9]\d|[1-9]\d{2})$/;
const PLACEHOLDER = /\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?/i;
const CONTROL = /[\u0000-\u001F\u007F-\u009F]/;

function fail(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}

function exactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return canonicalJson(Object.keys(value).sort()) === canonicalJson([...expected].sort());
}

function text(value, field) {
  if (typeof value !== 'string' || value.trim().length === 0 || PLACEHOLDER.test(value) || CONTROL.test(value)) {
    fail('WORK_ITEM_VALUE_INVALID', `${field} must be nonempty text without placeholders`, { field });
  }
  return value.trim();
}

function safeId(value, field = 'id') {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof value !== 'string' || !ITEM_ID.test(value) || value.endsWith('.') || reserved.test(value)) {
    fail('WORK_ITEM_ID_INVALID', `${field} must be a safe lowercase identifier`, { field, value });
  }
  return value;
}

function gateLevel(value, kind) {
  if (!WORK_ITEM_GATE_LEVELS.includes(value) || (kind !== 'TASK' && value !== 'FULL')) {
    fail('WORK_ITEM_GATE_LEVEL_INVALID', 'gateLevel must be LIGHT or FULL, and coordination work items must be FULL');
  }
  return value;
}

function strings(values, field, { allowEmpty = false } = {}) {
  if (!Array.isArray(values) || (!allowEmpty && values.length === 0)) {
    fail('WORK_ITEM_VALUE_INVALID', `${field} must be ${allowEmpty ? 'an' : 'a nonempty'} array`, { field });
  }
  const normalized = values.map((value, index) => text(value, `${field}[${index}]`));
  if (new Set(normalized).size !== normalized.length) {
    fail('WORK_ITEM_VALUE_INVALID', `${field} contains duplicate values`, { field });
  }
  return normalized;
}

function normalizeScopePattern(value) {
  const normalized = text(value, 'scope').replaceAll('\\', '/');
  const segments = normalized.split('/');
  const wildcard = /[?*{}[\]]/;
  const supportedPattern = !wildcard.test(normalized)
    || (normalized.endsWith('/**') && !wildcard.test(normalized.slice(0, -3)));
  const invalid = path.posix.isAbsolute(normalized)
    || path.win32.isAbsolute(normalized)
    || segments.includes('..')
    || normalized.includes(':')
    || normalized.startsWith('.hierarchical-delivery-governance/')
    || normalized === '.hierarchical-delivery-governance'
    || !supportedPattern;
  if (invalid) fail('WORK_ITEM_SCOPE_INVALID', 'Scope contains an unsafe path pattern', { pattern: value });
  return normalized.replace(/^\.\//, '');
}

function normalizeScope(values) {
  const normalized = strings(values, 'scope').map(normalizeScopePattern);
  return [...new Set(normalized)].sort();
}

function traceRecords(values, prefix, field) {
  if (!Array.isArray(values) || values.length === 0) {
    fail('WORK_ITEM_TRACE_INVALID', `${field} must be a nonempty array`, { field });
  }
  const seen = new Set();
  return values.map((entry, index) => {
    const expectedKeys = prefix === 'R' ? ['id', 'text'] : ['id', 'requirementIds', 'expectedResult'];
    if (!exactKeys(entry, expectedKeys) || !TRACE_ID.test(entry.id) || !entry.id.startsWith(`${prefix}-`) || seen.has(entry.id)) {
      fail('WORK_ITEM_TRACE_INVALID', `${field}[${index}] has an invalid or duplicate ID`, { field, index });
    }
    seen.add(entry.id);
    if (prefix === 'R') return { id: entry.id, text: text(entry.text, `${field}.${entry.id}`) };
    return {
      id: entry.id,
      requirementIds: strings(entry.requirementIds, `${field}.${entry.id}.requirementIds`).sort(),
      expectedResult: text(entry.expectedResult, `${field}.${entry.id}`),
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
}

function validateTrace(requirements, acceptance) {
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const accepted = new Set();
  for (const entry of acceptance) {
    for (const id of entry.requirementIds) {
      if (!requirementIds.has(id)) fail('WORK_ITEM_TRACE_INVALID', `${entry.id} references unknown requirement ${id}`);
      accepted.add(id);
    }
  }
  if (requirements.some(({ id }) => !accepted.has(id))) {
    fail('WORK_ITEM_TRACE_INVALID', 'Every requirement must be covered by acceptance');
  }
}

function childRecords(values, kind, requirements, acceptance) {
  if (!Array.isArray(values) || values.length === 0) {
    fail('WORK_ITEM_CHILDREN_INVALID', `${kind} must declare at least one child work item`);
  }
  const expectedKind = kind === 'DELIVERY' ? 'CAPABILITY' : 'TASK';
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const acceptanceIds = new Set(acceptance.map(({ id }) => id));
  const seen = new Set();
  return values.map((entry, index) => {
    const keys = ['id', 'kind', 'title', 'requirementIds', 'acceptanceIds'];
    if (!exactKeys(entry, keys) || entry.kind !== expectedKind) {
      fail('WORK_ITEM_CHILDREN_INVALID', `${kind} children must be ${expectedKind} records`, { index });
    }
    const id = safeId(entry.id, `children[${index}].id`);
    if (seen.has(id)) fail('WORK_ITEM_CHILDREN_INVALID', `Duplicate child ID: ${id}`);
    seen.add(id);
    const linkedRequirements = strings(entry.requirementIds, `${id}.requirementIds`).sort();
    const linkedAcceptance = strings(entry.acceptanceIds, `${id}.acceptanceIds`).sort();
    if (linkedRequirements.some((linked) => !requirementIds.has(linked))
        || linkedAcceptance.some((linked) => !acceptanceIds.has(linked))) {
      fail('WORK_ITEM_TRACE_INVALID', `${id} references unknown parent trace IDs`);
    }
    return {
      id,
      kind: expectedKind,
      title: text(entry.title, `${id}.title`),
      requirementIds: linkedRequirements,
      acceptanceIds: linkedAcceptance,
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
}

function executionRecord(value, id) {
  if (!exactKeys(value, ['dependsOn', 'inputs', 'outputs'])) {
    fail('WORK_ITEM_EXECUTION_INVALID', 'Task execution must contain dependsOn, inputs, and outputs');
  }
  const dependsOn = value.dependsOn.map((dependency, index) => safeId(dependency, `dependsOn[${index}]`));
  if (dependsOn.includes(id) || new Set(dependsOn).size !== dependsOn.length) {
    fail('WORK_ITEM_DEPENDENCY_INVALID', 'Task dependencies must be unique and cannot reference the Task itself');
  }
  return {
    dependsOn: [...dependsOn].sort(),
    inputs: strings(value.inputs, 'execution.inputs', { allowEmpty: true }),
    outputs: strings(value.outputs, 'execution.outputs'),
  };
}

function decompositionRecord(value, kind, id, parent) {
  const expectedKeys = kind === 'CAPABILITY' ? ['status', 'dependsOn'] : ['status'];
  if (!exactKeys(value, expectedKeys) || !['OPEN', 'SEALED'].includes(value.status)) {
    fail('WORK_ITEM_DECOMPOSITION_INVALID', 'Coordination work items require decomposition status OPEN or SEALED');
  }
  if (kind === 'DELIVERY') return { status: value.status };
  if (!Array.isArray(value.dependsOn)) {
    fail('WORK_ITEM_DEPENDENCY_INVALID', 'Capability dependsOn must be an array');
  }
  const dependsOn = value.dependsOn.map((dependency, index) => safeId(dependency, `decomposition.dependsOn[${index}]`));
  const siblingIds = new Set(parent?.children?.filter(({ kind: childKind }) => childKind === 'CAPABILITY').map(({ id: childId }) => childId));
  if (dependsOn.includes(id) || new Set(dependsOn).size !== dependsOn.length
      || dependsOn.some((dependency) => !siblingIds.has(dependency))) {
    fail('WORK_ITEM_DEPENDENCY_INVALID', 'Capability dependencies must be unique planned siblings and cannot reference itself');
  }
  return { status: value.status, dependsOn: [...dependsOn].sort() };
}

function testCommands(values) {
  if (!Array.isArray(values) || values.length === 0) {
    fail('WORK_ITEM_TEST_COMMAND_INVALID', 'At least one test command is required');
  }
  const commands = values.map((value) => normalizeTestArgv(value));
  if (commands.some((value) => !value)) fail('WORK_ITEM_TEST_COMMAND_INVALID', 'Test commands must be safe argv arrays');
  const canonical = commands.map((value) => JSON.stringify(value));
  if (new Set(canonical).size !== canonical.length) fail('WORK_ITEM_TEST_COMMAND_INVALID', 'Duplicate test command');
  return commands;
}

function linkedTraceIds(values, allowed, field, { allowEmpty = false } = {}) {
  const linked = strings(values, field, { allowEmpty }).sort();
  if (linked.some((id) => !allowed.has(id))) {
    fail('WORK_ITEM_TRACE_INVALID', `${field} references an unknown trace ID`, { field });
  }
  return linked;
}

function developmentTestPlan(values, acceptance, testCommandCount) {
  if (!Array.isArray(values) || values.length === 0) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'developmentPlan.testPlan must be a nonempty array');
  }
  const acceptanceIds = new Set(acceptance.map(({ id }) => id));
  const covered = new Set();
  const normalized = values.map((entry, index) => {
    const field = `developmentPlan.testPlan[${index}]`;
    if (!exactKeys(entry, ['acceptanceIds', 'approach', 'commandIndexes'])) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} has missing or unknown fields`, { field });
    }
    const linkedAcceptance = linkedTraceIds(entry.acceptanceIds, acceptanceIds, `${field}.acceptanceIds`);
    linkedAcceptance.forEach((id) => covered.add(id));
    if (!Array.isArray(entry.commandIndexes) || entry.commandIndexes.length === 0
        || entry.commandIndexes.some((commandIndex) => (
          !Number.isInteger(commandIndex) || commandIndex < 0 || commandIndex >= testCommandCount
        )) || new Set(entry.commandIndexes).size !== entry.commandIndexes.length) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field}.commandIndexes must reference frozen test commands`, { field });
    }
    return {
      acceptanceIds: linkedAcceptance,
      approach: text(entry.approach, `${field}.approach`),
      commandIndexes: [...entry.commandIndexes].sort((left, right) => left - right),
    };
  });
  if (acceptance.some(({ id }) => !covered.has(id))) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Every acceptance criterion must be covered by developmentPlan.testPlan');
  }
  return normalized;
}

function taskDevelopmentPlan(value, normalized) {
  const keys = [
    'purpose', 'scenarios', 'fileChanges', 'interfaces', 'logic', 'dataAndTransactions',
    'compatibility', 'testPlan', 'reviewPoints',
  ];
  if (!exactKeys(value, keys)) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Task developmentPlan contains missing or unknown fields');
  }
  const requirementIds = new Set(normalized.requirements.map(({ id }) => id));
  const coveredRequirements = new Set();
  if (!Array.isArray(value.scenarios) || value.scenarios.length === 0) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Task developmentPlan.scenarios must be nonempty');
  }
  const scenarios = value.scenarios.map((entry, index) => {
    const field = `developmentPlan.scenarios[${index}]`;
    if (!exactKeys(entry, ['kind', 'title', 'description', 'requirementIds'])
        || !WORK_ITEM_CHANGE_SCENARIOS.includes(entry.kind)) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} is invalid`, { field });
    }
    const linkedRequirements = linkedTraceIds(entry.requirementIds, requirementIds, `${field}.requirementIds`);
    linkedRequirements.forEach((id) => coveredRequirements.add(id));
    return {
      kind: entry.kind,
      title: text(entry.title, `${field}.title`),
      description: text(entry.description, `${field}.description`),
      requirementIds: linkedRequirements,
    };
  });
  if (normalized.requirements.some(({ id }) => !coveredRequirements.has(id))) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Every requirement must be covered by a development scenario');
  }

  if (!Array.isArray(value.fileChanges) || value.fileChanges.length === 0) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Task developmentPlan.fileChanges must be nonempty');
  }
  const seenPaths = new Set();
  const fileChanges = value.fileChanges.map((entry, index) => {
    const field = `developmentPlan.fileChanges[${index}]`;
    if (!exactKeys(entry, ['path', 'action', 'purpose'])
        || !['ADD', 'MODIFY', 'REMOVE'].includes(entry.action)) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} is invalid`, { field });
    }
    const plannedPath = normalizeScopePattern(entry.path);
    if (/[*?{}[\]]/.test(plannedPath) || seenPaths.has(plannedPath)
        || !scopeContains(normalized.scope, [plannedPath])) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field}.path must be a unique exact path inside Task scope`, { field });
    }
    seenPaths.add(plannedPath);
    return {
      path: plannedPath,
      action: entry.action,
      purpose: text(entry.purpose, `${field}.purpose`),
    };
  }).sort((left, right) => left.path.localeCompare(right.path));

  if (!Array.isArray(value.interfaces)) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Task developmentPlan.interfaces must be an array');
  }
  const interfaces = value.interfaces.map((entry, index) => {
    const field = `developmentPlan.interfaces[${index}]`;
    const interfaceKeys = [
      'name', 'kind', 'action', 'location', 'currentContract', 'targetContract', 'requirementIds',
    ];
    if (!exactKeys(entry, interfaceKeys)
        || !WORK_ITEM_INTERFACE_KINDS.includes(entry.kind)
        || !['ADD', 'MODIFY', 'REMOVE'].includes(entry.action)) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} is invalid`, { field });
    }
    return {
      name: text(entry.name, `${field}.name`),
      kind: entry.kind,
      action: entry.action,
      location: text(entry.location, `${field}.location`),
      currentContract: text(entry.currentContract, `${field}.currentContract`),
      targetContract: text(entry.targetContract, `${field}.targetContract`),
      requirementIds: linkedTraceIds(entry.requirementIds, requirementIds, `${field}.requirementIds`),
    };
  });

  return {
    purpose: text(value.purpose, 'developmentPlan.purpose'),
    scenarios,
    fileChanges,
    interfaces,
    logic: strings(value.logic, 'developmentPlan.logic'),
    dataAndTransactions: strings(value.dataAndTransactions, 'developmentPlan.dataAndTransactions', { allowEmpty: true }),
    compatibility: strings(value.compatibility, 'developmentPlan.compatibility'),
    testPlan: developmentTestPlan(value.testPlan, normalized.acceptance, normalized.testCommands.length),
    reviewPoints: strings(value.reviewPoints, 'developmentPlan.reviewPoints'),
  };
}

function coordinationDevelopmentPlan(value, normalized) {
  const keys = [
    'purpose', 'childPlans', 'sharedContracts', 'integrationFlow', 'deliveryWaves',
    'testPlan', 'reviewPoints',
  ];
  if (!exactKeys(value, keys)) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Coordination developmentPlan contains missing or unknown fields');
  }
  const requirements = new Set(normalized.requirements.map(({ id }) => id));
  const acceptance = new Set(normalized.acceptance.map(({ id }) => id));
  const childById = new Map(normalized.children.map((child) => [child.id, child]));
  if (!Array.isArray(value.childPlans) || value.childPlans.length !== normalized.children.length) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'developmentPlan.childPlans must cover every direct child exactly once');
  }
  const seen = new Set();
  const childPlans = value.childPlans.map((entry, index) => {
    const field = `developmentPlan.childPlans[${index}]`;
    const child = childById.get(entry?.id);
    if (!exactKeys(entry, ['id', 'purpose', 'deliverables', 'requirementIds', 'acceptanceIds', 'dependsOn'])
        || !child || seen.has(entry.id)) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} does not match a unique planned child`, { field });
    }
    seen.add(entry.id);
    const linkedRequirements = linkedTraceIds(entry.requirementIds, requirements, `${field}.requirementIds`);
    const linkedAcceptance = linkedTraceIds(entry.acceptanceIds, acceptance, `${field}.acceptanceIds`);
    if (canonicalJson(linkedRequirements) !== canonicalJson(child.requirementIds)
        || canonicalJson(linkedAcceptance) !== canonicalJson(child.acceptanceIds)) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} trace mapping must match the child contract`, { field });
    }
    const dependsOn = entry.dependsOn.map((id, dependencyIndex) => safeId(id, `${field}.dependsOn[${dependencyIndex}]`));
    if (dependsOn.includes(entry.id) || new Set(dependsOn).size !== dependsOn.length
        || dependsOn.some((id) => !childById.has(id))) {
      fail('WORK_ITEM_DEPENDENCY_INVALID', `${field}.dependsOn must reference unique sibling children`, { field });
    }
    return {
      id: entry.id,
      purpose: text(entry.purpose, `${field}.purpose`),
      deliverables: strings(entry.deliverables, `${field}.deliverables`),
      requirementIds: linkedRequirements,
      acceptanceIds: linkedAcceptance,
      dependsOn: [...dependsOn].sort(),
    };
  }).sort((left, right) => left.id.localeCompare(right.id));

  const graph = new Map(childPlans.map(({ id, dependsOn }) => [id, dependsOn]));
  const visiting = new Set();
  const visited = new Set();
  const visit = (id) => {
    if (visiting.has(id)) fail('WORK_ITEM_DEPENDENCY_CYCLE', 'developmentPlan child dependencies contain a cycle');
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dependency of graph.get(id) ?? []) visit(dependency);
    visiting.delete(id);
    visited.add(id);
  };
  for (const id of graph.keys()) visit(id);

  if (!Array.isArray(value.sharedContracts)) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'developmentPlan.sharedContracts must be an array');
  }
  const sharedContracts = value.sharedContracts.map((entry, index) => {
    const field = `developmentPlan.sharedContracts[${index}]`;
    if (!exactKeys(entry, [
      'name', 'kind', 'description', 'providerChildIds', 'consumerChildIds', 'requirementIds',
    ]) || !WORK_ITEM_INTERFACE_KINDS.includes(entry.kind)) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} is invalid`, { field });
    }
    const childIds = new Set(childById.keys());
    const providers = strings(entry.providerChildIds, `${field}.providerChildIds`).sort();
    const consumers = strings(entry.consumerChildIds, `${field}.consumerChildIds`).sort();
    if ([...providers, ...consumers].some((id) => !childIds.has(id))) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} references an unknown child`, { field });
    }
    return {
      name: text(entry.name, `${field}.name`),
      kind: entry.kind,
      description: text(entry.description, `${field}.description`),
      providerChildIds: providers,
      consumerChildIds: consumers,
      requirementIds: linkedTraceIds(entry.requirementIds, requirements, `${field}.requirementIds`),
    };
  });

  if (!Array.isArray(value.deliveryWaves) || value.deliveryWaves.length === 0) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'developmentPlan.deliveryWaves must be nonempty');
  }
  const waveByChild = new Map();
  const waveOrders = new Set();
  const deliveryWaves = value.deliveryWaves.map((entry, index) => {
    const field = `developmentPlan.deliveryWaves[${index}]`;
    if (!exactKeys(entry, ['order', 'name', 'childIds', 'exitCriteria'])
        || !Number.isInteger(entry.order) || entry.order < 1 || waveOrders.has(entry.order)) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} is invalid`, { field });
    }
    waveOrders.add(entry.order);
    const childIds = strings(entry.childIds, `${field}.childIds`).map((id) => safeId(id, `${field}.childIds`)).sort();
    if (childIds.some((id) => !childById.has(id) || waveByChild.has(id))) {
      fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', `${field} must contain unique planned children`, { field });
    }
    childIds.forEach((id) => waveByChild.set(id, entry.order));
    return {
      order: entry.order,
      name: text(entry.name, `${field}.name`),
      childIds,
      exitCriteria: text(entry.exitCriteria, `${field}.exitCriteria`),
    };
  }).sort((left, right) => left.order - right.order);
  if (waveByChild.size !== childById.size
      || childPlans.some(({ id, dependsOn }) => dependsOn.some((dependency) => waveByChild.get(dependency) >= waveByChild.get(id)))) {
    fail('WORK_ITEM_DEVELOPMENT_PLAN_INVALID', 'Delivery waves must cover every child and order dependencies before consumers');
  }

  return {
    purpose: text(value.purpose, 'developmentPlan.purpose'),
    childPlans,
    sharedContracts,
    integrationFlow: strings(value.integrationFlow, 'developmentPlan.integrationFlow'),
    deliveryWaves,
    testPlan: developmentTestPlan(value.testPlan, normalized.acceptance, normalized.testCommands.length),
    reviewPoints: strings(value.reviewPoints, 'developmentPlan.reviewPoints'),
  };
}

function scopeCovers(parentPattern, childPattern) {
  if (parentPattern === '**') return true;
  if (!parentPattern.endsWith('/**')) return parentPattern === childPattern;
  const prefix = parentPattern.slice(0, -3);
  return childPattern === prefix || childPattern.startsWith(`${prefix}/`);
}

export function scopeContains(parentScope, childScope) {
  return childScope.every((childPattern) => parentScope.some((parentPattern) => scopeCovers(parentPattern, childPattern)));
}

export function scopePatternsOverlap(left, right) {
  return left.some((leftPattern) => right.some((rightPattern) => (
    scopeCovers(leftPattern, rightPattern) || scopeCovers(rightPattern, leftPattern)
  )));
}

function normalizeParent(definition, parent) {
  if (definition.kind === 'DELIVERY') {
    if (definition.parentId !== undefined && definition.parentId !== null) {
      fail('WORK_ITEM_PARENT_INVALID', 'Delivery cannot have a parent work item');
    }
    return { parentId: null, parentContractFingerprint: null };
  }
  if (definition.parentId === null) {
    if (parent) fail('WORK_ITEM_PARENT_INVALID', `Root ${definition.kind} cannot receive a parent contract`);
    if (definition.kind === 'TASK' && definition.execution.dependsOn.length > 0) {
      fail('WORK_ITEM_DEPENDENCY_INVALID', 'A root Task cannot depend on sibling Tasks; use a Capability root');
    }
    if (definition.kind === 'CAPABILITY' && definition.decomposition.dependsOn.length > 0) {
      fail('WORK_ITEM_DEPENDENCY_INVALID', 'A root Capability cannot depend on sibling Capabilities; use a Delivery root');
    }
    return { parentId: null, parentContractFingerprint: null };
  }
  if (!parent || definition.parentId !== parent.id) {
    fail('WORK_ITEM_PARENT_INVALID', `${definition.kind} must reference its supplied parent`);
  }
  const expectedParentKind = definition.kind === 'CAPABILITY' ? 'DELIVERY' : 'CAPABILITY';
  if (parent.kind !== expectedParentKind) {
    fail('WORK_ITEM_PARENT_INVALID', `${definition.kind} parent must be ${expectedParentKind}`);
  }
  const planned = parent.children?.find(({ id, kind }) => id === definition.id && kind === definition.kind);
  if (!planned) fail('WORK_ITEM_PARENT_PLAN_MISMATCH', `${definition.id} is not declared by its parent baseline`);
  if (!scopeContains(parent.scope, definition.scope)) {
    fail('WORK_ITEM_SCOPE_EXPANDED', `${definition.id} scope expands beyond its parent baseline`);
  }
  return {
    parentId: parent.id,
    parentContractFingerprint: workItemChildContractFingerprint(parent, definition.id),
  };
}

export function validateWorkItemDefinition(definition, { parent, allowLegacyDevelopmentPlan = false } = {}) {
  if (!definition || typeof definition !== 'object' || Array.isArray(definition)) {
    fail('WORK_ITEM_DEFINITION_INVALID', 'Work item definition must be an object');
  }
  if (!WORK_ITEM_KINDS.includes(definition.kind)) {
    fail('WORK_ITEM_KIND_INVALID', 'Work item kind must be DELIVERY, CAPABILITY, or TASK');
  }
  if (definition.schemaVersion !== WORK_ITEM_SCHEMA_VERSION) {
    fail('WORK_ITEM_SCHEMA_INVALID', `Work item schemaVersion must be ${WORK_ITEM_SCHEMA_VERSION}`);
  }
  if (definition.kind === 'TASK' && Object.hasOwn(definition, 'children')) {
    fail('WORK_ITEM_TASK_NOT_LEAF', 'Task is an executable leaf and cannot contain children');
  }
  if (definition.kind !== 'TASK' && Object.hasOwn(definition, 'execution')) {
    fail('WORK_ITEM_EXECUTION_INVALID', 'Only Task work items can contain execution metadata');
  }
  const commonKeys = [
    'schemaVersion', 'id', 'kind', 'gateLevel', 'title', 'goal', 'scope', 'nonGoals', 'requirements',
    'acceptance', 'testCommands', 'risks', 'decisions',
  ];
  const developmentPlanKeys = allowLegacyDevelopmentPlan && !Object.hasOwn(definition, 'developmentPlan')
    ? []
    : ['developmentPlan'];
  const expectedKeys = definition.kind === 'DELIVERY'
    ? [...commonKeys, ...developmentPlanKeys, 'decomposition', 'children']
    : [...commonKeys, ...developmentPlanKeys, 'parentId', ...(definition.kind === 'TASK' ? ['execution'] : ['decomposition', 'children'])];
  if (!exactKeys(definition, expectedKeys)) {
    fail('WORK_ITEM_DEFINITION_INVALID', 'Work item definition contains missing or unknown fields', {
      expectedKeys: expectedKeys.sort(),
      actualKeys: Object.keys(definition).sort(),
    });
  }

  const normalized = {
    schemaVersion: WORK_ITEM_SCHEMA_VERSION,
    id: safeId(definition.id),
    kind: definition.kind,
    gateLevel: gateLevel(definition.gateLevel, definition.kind),
    authorityKind: WORK_ITEM_AUTHORITIES[definition.kind],
    title: text(definition.title, 'title'),
    goal: text(definition.goal, 'goal'),
    scope: normalizeScope(definition.scope),
    nonGoals: strings(definition.nonGoals, 'nonGoals'),
    requirements: traceRecords(definition.requirements, 'R', 'requirements'),
    acceptance: traceRecords(definition.acceptance, 'A', 'acceptance'),
    testCommands: testCommands(definition.testCommands),
    risks: strings(definition.risks, 'risks'),
    decisions: strings(definition.decisions, 'decisions'),
  };
  validateTrace(normalized.requirements, normalized.acceptance);
  if (definition.kind === 'TASK') normalized.execution = executionRecord(definition.execution, normalized.id);
  else {
    normalized.decomposition = decompositionRecord(definition.decomposition, definition.kind, normalized.id, parent);
    normalized.children = childRecords(definition.children, definition.kind, normalized.requirements, normalized.acceptance);
  }
  if (Object.hasOwn(definition, 'developmentPlan')) {
    normalized.developmentPlan = definition.kind === 'TASK'
      ? taskDevelopmentPlan(definition.developmentPlan, normalized)
      : coordinationDevelopmentPlan(definition.developmentPlan, normalized);
  }
  Object.assign(normalized, normalizeParent({ ...definition, ...normalized }, parent));
  if (parent?.developmentPlan) {
    const planned = parent.developmentPlan.childPlans.find(({ id }) => id === normalized.id);
    const actualDependencies = normalized.kind === 'TASK'
      ? normalized.execution.dependsOn
      : normalized.decomposition.dependsOn;
    if (!planned || canonicalJson(planned.dependsOn) !== canonicalJson(actualDependencies)) {
      fail('WORK_ITEM_PARENT_PLAN_MISMATCH', `${normalized.id} dependencies do not match the frozen parent development plan`);
    }
  }
  return normalized;
}

function contract(definition) {
  const normalized = {
    schemaVersion: definition.schemaVersion,
    id: definition.id,
    kind: definition.kind,
    gateLevel: definition.gateLevel,
    goal: definition.goal,
    scope: [...definition.scope].sort(),
    requirements: [...definition.requirements].sort((left, right) => left.id.localeCompare(right.id)),
    acceptance: [...definition.acceptance].sort((left, right) => left.id.localeCompare(right.id)),
    testCommands: definition.testCommands,
  };
  if (definition.children) normalized.children = [...definition.children].sort((left, right) => left.id.localeCompare(right.id));
  if (definition.decomposition) normalized.decomposition = definition.decomposition;
  if (definition.execution) normalized.execution = definition.execution;
  if (definition.developmentPlan) normalized.developmentPlan = definition.developmentPlan;
  return normalized;
}

export function workItemContractFingerprint(definition) {
  return sha256Bytes(Buffer.from(canonicalJson(contract(definition)), 'utf8'));
}

export function workItemChildContractFingerprint(parent, childId) {
  const child = parent.children?.find(({ id }) => id === childId);
  if (!child) fail('WORK_ITEM_PARENT_PLAN_MISMATCH', `${childId} is not declared by its parent baseline`);
  const stableParentContract = contract(parent);
  delete stableParentContract.children;
  delete stableParentContract.decomposition;
  let childDevelopmentPlan;
  if (stableParentContract.developmentPlan) {
    childDevelopmentPlan = stableParentContract.developmentPlan.childPlans.find(({ id }) => id === childId);
    stableParentContract.developmentPlan = {
      ...stableParentContract.developmentPlan,
      sharedContracts: stableParentContract.developmentPlan.sharedContracts
        .filter(({ consumerChildIds }) => consumerChildIds.includes(childId)),
      childPlans: undefined,
      deliveryWaves: undefined,
    };
    delete stableParentContract.developmentPlan.childPlans;
    delete stableParentContract.developmentPlan.deliveryWaves;
  }
  return sha256Bytes(Buffer.from(canonicalJson({
    parent: stableParentContract,
    child,
    ...(childDevelopmentPlan ? { childDevelopmentPlan } : {}),
  }), 'utf8'));
}

export function workItemBaselineFingerprint(definition) {
  return sha256Bytes(Buffer.from(canonicalJson(definition), 'utf8'));
}

function list(values) {
  return values.map((value) => `- ${value}`).join('\n');
}

export function renderWorkItemBaseline(definition) {
  const lines = [
    '# Work Item Baseline',
    '',
    `Work Item: ${definition.id}`,
    `Kind: ${definition.kind}`,
    `Gate Level: ${definition.gateLevel}`,
    `Authority: ${definition.authorityKind}`,
    `Parent: ${definition.parentId ?? 'none'}`,
    `Parent Contract: ${definition.parentContractFingerprint ?? 'none'}`,
    '',
    '## Goal',
    definition.goal,
    '',
    '## Scope',
    list(definition.scope),
    '',
    '## Non-Goals',
    list(definition.nonGoals),
    '',
    '## Requirements',
  ];
  for (const requirement of definition.requirements) lines.push(`### ${requirement.id}`, requirement.text, '');
  lines.push('## Acceptance');
  for (const acceptance of definition.acceptance) {
    lines.push(`### ${acceptance.id} [${acceptance.requirementIds.join(',')}]`, acceptance.expectedResult, '');
  }
  if (definition.children) {
    lines.push(
      '## Decomposition',
      `- Status: ${definition.decomposition.status}`,
      ...(definition.kind === 'CAPABILITY'
        ? [`- Capability dependencies: ${definition.decomposition.dependsOn.join(', ') || 'none'}`]
        : []),
      '',
      '## Children',
    );
    for (const child of definition.children) {
      lines.push(`- ${child.id} [${child.kind}] [${child.requirementIds.join(',')}] [${child.acceptanceIds.join(',')}] ${child.title}`);
    }
  } else {
    lines.push(
      '## Execution',
      `- Depends on: ${definition.execution.dependsOn.join(', ') || 'none'}`,
      `- Inputs: ${definition.execution.inputs.join('; ') || 'none'}`,
      `- Outputs: ${definition.execution.outputs.join('; ')}`,
    );
  }
  lines.push('', '## Test Commands', ...definition.testCommands.map((argv) => `- ${JSON.stringify(argv)}`));
  if (definition.developmentPlan) {
    lines.push(
      '',
      '## Development Review Contract',
      definition.developmentPlan.purpose,
      '',
      '- Full human-readable plan: [development-review.md](development-review.md)',
      '- Structured plan: [development-plan.json](development-plan.json)',
    );
  }
  lines.push('', '## Risks', list(definition.risks));
  lines.push('', '## Decisions', list(definition.decisions), '');
  return lines.join('\n');
}

function reviewStatusText(state) {
  return state?.review?.status === 'APPROVED'
    ? `已由人工确认（${state.review.reviewedBy}，${state.review.reviewedAt}）`
    : '等待人工评审；尚未冻结，禁止开始开发';
}

function markdownTableCell(value) {
  return String(value).replaceAll('|', '\\|').replaceAll('\n', '<br>');
}

export function renderDevelopmentReview(definition, state) {
  const plan = definition.developmentPlan;
  if (!plan) return '';
  const lines = [
    `# 开发评审：${definition.title}`,
    '',
    `- 工作项：${definition.id}`,
    `- 层级：${definition.kind}`,
    `- 门禁等级：${definition.gateLevel}`,
    `- Baseline 指纹：${state.baselineFingerprint}`,
    `- 评审状态：${reviewStatusText(state)}`,
    `- 开发目的：${plan.purpose}`,
    '',
    '## 需求与验收边界',
    '',
    '| 需求 | 内容 |',
    '| --- | --- |',
    ...definition.requirements.map(({ id, text: requirement }) => `| ${id} | ${markdownTableCell(requirement)} |`),
    '',
    '| 验收 | 覆盖需求 | 预期结果 |',
    '| --- | --- | --- |',
    ...definition.acceptance.map(({ id, requirementIds, expectedResult }) => (
      `| ${id} | ${requirementIds.join(', ')} | ${markdownTableCell(expectedResult)} |`
    )),
    '',
  ];

  if (definition.kind === 'TASK') {
    lines.push(
      '## 变更场景',
      '',
      '| 场景 | 标题 | 开发内容 | 覆盖需求 |',
      '| --- | --- | --- | --- |',
      ...plan.scenarios.map((scenario) => (
        `| ${scenario.kind} | ${markdownTableCell(scenario.title)} | ${markdownTableCell(scenario.description)} | ${scenario.requirementIds.join(', ')} |`
      )),
      '',
      '## 文件改动',
      '',
      '| 动作 | 文件 | 目的 |',
      '| --- | --- | --- |',
      ...plan.fileChanges.map((change) => `| ${change.action} | \`${change.path}\` | ${markdownTableCell(change.purpose)} |`),
      '',
      '## 接口与功能契约',
      '',
    );
    if (plan.interfaces.length === 0) lines.push('- 本 Task 不新增、修改或删除外部/内部接口。');
    else {
      lines.push(
        '| 动作 | 类型 | 名称与位置 | 当前契约 | 目标契约 | 覆盖需求 |',
        '| --- | --- | --- | --- | --- | --- |',
        ...plan.interfaces.map((contract) => (
          `| ${contract.action} | ${contract.kind} | ${markdownTableCell(contract.name)}<br>${markdownTableCell(contract.location)} | ${markdownTableCell(contract.currentContract)} | ${markdownTableCell(contract.targetContract)} | ${contract.requirementIds.join(', ')} |`
        )),
      );
    }
    lines.push('', '## 实现逻辑', '', ...plan.logic.map((item) => `- ${item}`));
    lines.push('', '## 数据与事务', '');
    lines.push(...(plan.dataAndTransactions.length > 0
      ? plan.dataAndTransactions.map((item) => `- ${item}`)
      : ['- 不涉及数据模型、持久化或事务边界变更。']));
    lines.push('', '## 兼容性', '', ...plan.compatibility.map((item) => `- ${item}`));
  } else {
    const childLabel = definition.kind === 'DELIVERY' ? 'Capability' : 'Task';
    lines.push(
      `## ${childLabel} 开发内容`,
      '',
      `| ${childLabel} | 开发目的 | 交付内容 | 依赖 | R/A |`,
      '| --- | --- | --- | --- | --- |',
      ...plan.childPlans.map((child) => (
        `| ${child.id} | ${markdownTableCell(child.purpose)} | ${markdownTableCell(child.deliverables.join('；'))} | ${child.dependsOn.join(', ') || '无'} | ${child.requirementIds.join(', ')} / ${child.acceptanceIds.join(', ')} |`
      )),
      '',
      `## 跨 ${childLabel} 接口与共享契约`,
      '',
    );
    if (plan.sharedContracts.length === 0) lines.push(`- 无跨 ${childLabel} 共享接口；子级仅通过冻结输出和聚合门禁组合。`);
    else {
      lines.push(
        '| 类型 | 契约 | 提供方 | 消费方 | 说明 | 覆盖需求 |',
        '| --- | --- | --- | --- | --- | --- |',
        ...plan.sharedContracts.map((contract) => (
          `| ${contract.kind} | ${markdownTableCell(contract.name)} | ${contract.providerChildIds.join(', ')} | ${contract.consumerChildIds.join(', ')} | ${markdownTableCell(contract.description)} | ${contract.requirementIds.join(', ')} |`
        )),
      );
    }
    lines.push('', '## 集成流程', '', ...plan.integrationFlow.map((item) => `- ${item}`));
    lines.push(
      '',
      '## 开发与集成波次',
      '',
      '| 波次 | 名称 | 子级 | 退出条件 |',
      '| --- | --- | --- | --- |',
      ...plan.deliveryWaves.map((wave) => (
        `| ${wave.order} | ${markdownTableCell(wave.name)} | ${wave.childIds.join(', ')} | ${markdownTableCell(wave.exitCriteria)} |`
      )),
    );
  }

  lines.push(
    '',
    '## 测试与验收映射',
    '',
    '| 验收项 | 验证方法 | 冻结命令序号 |',
    '| --- | --- | --- |',
    ...plan.testPlan.map((test) => (
      `| ${test.acceptanceIds.join(', ')} | ${markdownTableCell(test.approach)} | ${test.commandIndexes.join(', ')} |`
    )),
    '',
    '## 人工评审重点',
    '',
    ...plan.reviewPoints.map((item) => `- ${item}`),
    '',
    '## 冻结说明',
    '',
    '- 请先评审本文件中的开发目的、内容、文件、接口/共享契约、依赖波次和测试映射。',
    '- 如需修改，先修改 definition 并重新 prepare；不要冻结错误版本。',
    `- 只有对指纹 \`${state.baselineFingerprint}\` 明确确认后，才可执行 freeze-item；冻结后开发上下文必须携带本计划。`,
    '',
  );
  return lines.join('\n');
}

export function resolveSelfHostingPolicy({ packageName, explicitDogfood = false } = {}) {
  const implementationPackages = new Set(['hierarchical-delivery-governance']);
  if (implementationPackages.has(packageName) && explicitDogfood !== true) {
    return {
      route: 'SELF_HOSTING_MAINTENANCE',
      createsRuntimePackage: false,
      reason: 'HIERARCHICAL_GOVERNANCE_SELF_MAINTENANCE',
    };
  }
  return {
    route: 'STANDARD_HIERARCHICAL_GOVERNANCE',
    createsRuntimePackage: true,
    reason: explicitDogfood === true ? 'EXPLICIT_DOGFOOD' : 'NOT_SELF_HOSTING',
  };
}
