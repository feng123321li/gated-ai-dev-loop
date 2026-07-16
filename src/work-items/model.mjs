import path from 'node:path';

import { canonicalJson } from '../baseline/sources.mjs';
import { normalizeTestArgv } from '../baseline/test-command.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { sha256Bytes } from '../core/hash.mjs';

export const WORK_ITEM_SCHEMA_VERSION = 2;
export const WORK_ITEM_KINDS = Object.freeze(['DELIVERY', 'CAPABILITY', 'TASK']);
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

export function validateWorkItemDefinition(definition, { parent } = {}) {
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
    'schemaVersion', 'id', 'kind', 'title', 'goal', 'scope', 'nonGoals', 'requirements',
    'acceptance', 'testCommands', 'risks', 'decisions',
  ];
  const expectedKeys = definition.kind === 'DELIVERY'
    ? [...commonKeys, 'decomposition', 'children']
    : [...commonKeys, 'parentId', ...(definition.kind === 'TASK' ? ['execution'] : ['decomposition', 'children'])];
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
  Object.assign(normalized, normalizeParent({ ...definition, ...normalized }, parent));
  return normalized;
}

function contract(definition) {
  const normalized = {
    schemaVersion: definition.schemaVersion,
    id: definition.id,
    kind: definition.kind,
    goal: definition.goal,
    scope: [...definition.scope].sort(),
    requirements: [...definition.requirements].sort((left, right) => left.id.localeCompare(right.id)),
    acceptance: [...definition.acceptance].sort((left, right) => left.id.localeCompare(right.id)),
    testCommands: definition.testCommands,
  };
  if (definition.children) normalized.children = [...definition.children].sort((left, right) => left.id.localeCompare(right.id));
  if (definition.decomposition) normalized.decomposition = definition.decomposition;
  if (definition.execution) normalized.execution = definition.execution;
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
  return sha256Bytes(Buffer.from(canonicalJson({
    parent: stableParentContract,
    child,
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
  lines.push('', '## Risks', list(definition.risks));
  lines.push('', '## Decisions', list(definition.decisions), '');
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
