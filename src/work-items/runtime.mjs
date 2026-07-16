import * as fsPromises from 'node:fs/promises';
import path from 'node:path';

import { canonicalJson } from '../baseline/sources.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import {
  assertSafePath,
  atomicReplaceDirectory,
  atomicWriteDirectory,
  atomicWriteFile,
  readSafeRegularFile,
  withRuntimeDirectoryTransaction,
} from '../core/fs-safe.mjs';
import { sha256Bytes } from '../core/hash.mjs';
import { requireHostRuntime } from '../mode/host-runtime.mjs';
import {
  renderWorkItemBaseline,
  resolveSelfHostingPolicy,
  scopePatternsOverlap,
  validateWorkItemDefinition,
  workItemBaselineFingerprint,
  workItemChildContractFingerprint,
  workItemContractFingerprint,
  WORK_ITEM_AUTHORITIES,
  WORK_ITEM_KINDS,
} from './model.mjs';

export const WORK_ITEM_REGISTRY_FILE = 'work-item-registry.json';
export const WORK_ITEMS_DIRECTORY = 'work-items';
export const GOVERNANCE_DIRECTORY = '.hierarchical-delivery-governance';
const DELIVERY_STATUSES = Object.freeze([
  'NOT_READY',
  'WAITING_FOR_INDEPENDENT_REVIEW',
  'WAITING_FOR_USER_CONFIRMATION',
  'COMPLETED',
]);

function fail(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}

function json(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function timestamp(now) {
  const value = typeof now === 'function' ? now() : (now ?? new Date());
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) fail('WORK_ITEM_TIMESTAMP_INVALID', 'Work item timestamp is invalid');
  return date.toISOString();
}

async function assertSelfHostingDogfood(root, explicitDogfood, fs) {
  let packageName;
  try {
    const packageJson = JSON.parse((await readSafeRegularFile(root, 'package.json', { fs })).toString('utf8'));
    if (typeof packageJson?.name === 'string') packageName = packageJson.name;
  } catch (error) {
    if (error?.code === 'ENOENT' || error?.code === 'PATH_MISSING' || error instanceof SyntaxError) return;
    throw error;
  }
  const policy = resolveSelfHostingPolicy({ packageName, explicitDogfood });
  if (policy.createsRuntimePackage === false) {
    fail('SELF_HOSTING_DOGFOOD_REQUIRED', 'The hierarchical governance implementation repository requires explicit dogfood for runtime mutations');
  }
}

function emptyRegistry(root, at) {
  return {
    schemaVersion: 2,
    coordinationRoot: path.resolve(root),
    revision: 0,
    currentFocus: { workItemId: null, purpose: null },
    workItems: [],
    updatedAt: at,
  };
}

function registryPath(root) {
  return path.join(root, GOVERNANCE_DIRECTORY, WORK_ITEM_REGISTRY_FILE);
}

function itemRelativePath(id) {
  return path.posix.join(WORK_ITEMS_DIRECTORY, id);
}

function itemPath(root, id) {
  return path.join(root, GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, id);
}

function sortedItems(items) {
  return [...items].sort((left, right) => left.id.localeCompare(right.id));
}

function itemById(registry, id) {
  const item = registry.workItems.find((entry) => entry.id === id);
  if (!item) fail('WORK_ITEM_NOT_FOUND', `Unknown work item: ${id}`, { id });
  return item;
}

function validEvidenceReference(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const portable = typeof value.path === 'string' ? value.path.replaceAll('\\', '/') : '';
  return portable.length > 0
    && !path.posix.isAbsolute(portable)
    && !portable.split('/').includes('..')
    && typeof value.sha256 === 'string'
    && /^[a-f0-9]{64}$/.test(value.sha256);
}

function nonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function validDeliveryArtifact(action, value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.schemaVersion !== 1) return false;
  if (action === 'INDEPENDENT_REVIEW_PASS') {
    return value.kind === 'INDEPENDENT_REVIEW'
      && nonEmptyString(value.reviewer)
      && value.isolation === 'FRESH_READ_ONLY'
      && value.verdict === 'PASS'
      && value.findings && typeof value.findings === 'object' && !Array.isArray(value.findings)
      && value.findings.p0 === 0 && value.findings.p1 === 0;
  }
  if (action === 'HUMAN_REVIEW_ACCEPTED') {
    return value.kind === 'HUMAN_REVIEW'
      && nonEmptyString(value.reviewer)
      && value.verdict === 'ACCEPTED';
  }
  return action === 'USER_CONFIRMED'
    && value.kind === 'USER_CONFIRMATION'
    && nonEmptyString(value.confirmedBy)
    && value.decision === 'CONFIRMED';
}

function validDeliveryEvidence(value, actions) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && actions.includes(value.action)
    && validEvidenceReference(value.evidence)
    && validDeliveryArtifact(value.action, value.artifact)
    && typeof value.recordedAt === 'string'
    && !Number.isNaN(Date.parse(value.recordedAt));
}

function validDelivery(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || !DELIVERY_STATUSES.includes(value.status)) return false;
  if (value.status === 'NOT_READY' || value.status === 'WAITING_FOR_INDEPENDENT_REVIEW') {
    return value.review === null && value.userConfirmation === null;
  }
  const reviewValid = validDeliveryEvidence(
    value.review,
    ['INDEPENDENT_REVIEW_PASS', 'HUMAN_REVIEW_ACCEPTED'],
  );
  if (value.status === 'WAITING_FOR_USER_CONFIRMATION') {
    return reviewValid && value.userConfirmation === null;
  }
  return reviewValid && validDeliveryEvidence(value.userConfirmation, ['USER_CONFIRMED']);
}

function validateRegistry(registry, root) {
  const valid = registry && typeof registry === 'object' && !Array.isArray(registry)
    && registry.schemaVersion === 2
    && registry.coordinationRoot === path.resolve(root)
    && Number.isInteger(registry.revision) && registry.revision >= 0
    && Array.isArray(registry.workItems)
    && registry.currentFocus && typeof registry.currentFocus === 'object';
  if (!valid) fail('WORK_ITEM_REGISTRY_INVALID', 'Work item registry is invalid');
  const ids = registry.workItems.map(({ id }) => id);
  const safeId = (value) => typeof value === 'string'
    && /^[a-z0-9][a-z0-9._-]*$/.test(value)
    && !value.endsWith('.')
    && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/.test(value);
  if (new Set(ids).size !== ids.length || ids.some((id) => !safeId(id))) {
    fail('WORK_ITEM_REGISTRY_INVALID', 'Work item registry contains duplicate or unsafe IDs');
  }
  const byId = new Map(registry.workItems.map((entry) => [entry.id, entry]));
  for (const entry of registry.workItems) {
    const validEntry = WORK_ITEM_KINDS.includes(entry.kind)
      && entry.authorityKind === WORK_ITEM_AUTHORITIES[entry.kind]
      && (entry.parentId === null || safeId(entry.parentId))
      && Array.isArray(entry.childIds) && entry.childIds.every(safeId)
      && entry.packagePath === itemRelativePath(entry.id)
      && typeof entry.baselineFingerprint === 'string' && /^[a-f0-9]{64}$/.test(entry.baselineFingerprint)
      && typeof entry.contractFingerprint === 'string' && /^[a-f0-9]{64}$/.test(entry.contractFingerprint);
    if (!validEntry) fail('WORK_ITEM_REGISTRY_INVALID', `Work item registry entry is invalid: ${entry.id}`);
    const deliveryValid = entry.kind === 'DELIVERY'
      ? (entry.delivery === undefined || validDelivery(entry.delivery))
      : entry.delivery === undefined || entry.delivery === null;
    if (!deliveryValid) fail('WORK_ITEM_REGISTRY_INVALID', `Work item delivery state is invalid: ${entry.id}`);
    if (entry.kind === 'DELIVERY' && entry.parentId !== null) {
      fail('WORK_ITEM_REGISTRY_INVALID', 'Delivery entries cannot have parents');
    }
    if (entry.kind !== 'DELIVERY') {
      const parent = byId.get(entry.parentId);
      const expectedParentKind = entry.kind === 'CAPABILITY' ? 'DELIVERY' : 'CAPABILITY';
      if (!parent || parent.kind !== expectedParentKind || !parent.childIds.includes(entry.id)) {
        fail('WORK_ITEM_REGISTRY_INVALID', `Work item parent relation is invalid: ${entry.id}`);
      }
    }
  }
  const focusId = registry.currentFocus.workItemId;
  if (focusId !== null && (!safeId(focusId) || !byId.has(focusId))) {
    fail('WORK_ITEM_REGISTRY_INVALID', 'Current focus references an unknown work item');
  }
  return registry;
}

async function ensureRuntimeRoot(root, fs) {
  const rootStat = await fs.lstat(root).catch((error) => {
    if (error.code === 'ENOENT') fail('WORK_ITEM_ROOT_INVALID', 'Delivery root must already exist');
    throw error;
  });
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail('WORK_ITEM_ROOT_INVALID', 'Delivery root must be a regular directory');
  const runtimeRoot = await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  await fs.mkdir(runtimeRoot, { recursive: true });
  await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  const itemsRoot = await assertSafePath(root, path.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  await fs.mkdir(itemsRoot, { recursive: true });
  await assertSafePath(root, path.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  return runtimeRoot;
}

async function assertPersistedDeliveryEvidence(root, registry, fs) {
  for (const entry of registry.workItems.filter(({ kind, delivery }) => (
    kind === 'DELIVERY' && delivery && delivery.status !== 'NOT_READY'
      && delivery.status !== 'WAITING_FOR_INDEPENDENT_REVIEW'
  ))) {
    const records = [entry.delivery.review];
    if (entry.delivery.status === 'COMPLETED') records.push(entry.delivery.userConfirmation);
    for (const record of records) {
      let bytes;
      try { bytes = await readSafeRegularFile(root, record.evidence.path, { fs }); }
      catch { fail('WORK_ITEM_DELIVERY_EVIDENCE_MISSING', `Persisted delivery evidence is unavailable: ${record.evidence.path}`); }
      if (sha256Bytes(bytes) !== record.evidence.sha256) {
        fail('WORK_ITEM_DELIVERY_EVIDENCE_CHANGED', `Persisted delivery evidence changed: ${record.evidence.path}`);
      }
      let artifact;
      try { artifact = JSON.parse(bytes.toString('utf8')); }
      catch { fail('WORK_ITEM_DELIVERY_EVIDENCE_INVALID', `Persisted delivery evidence is invalid JSON: ${record.evidence.path}`); }
      if (!validDeliveryArtifact(record.action, artifact)
          || canonicalJson(artifact) !== canonicalJson(record.artifact)) {
        fail('WORK_ITEM_DELIVERY_EVIDENCE_CHANGED', `Persisted delivery evidence no longer matches its registry snapshot: ${record.evidence.path}`);
      }
    }
  }
}

async function readRegistryUnlocked(root, fs, { allowMissing = false, now } = {}) {
  const target = registryPath(root);
  let bytes;
  try { bytes = await readSafeRegularFile(root, target, { fs }); }
  catch (error) {
    if (error.code === 'ENOENT' && allowMissing) return emptyRegistry(root, timestamp(now));
    if (error.code === 'ENOENT') fail('WORK_ITEM_REGISTRY_MISSING', 'Work item registry does not exist');
    throw error;
  }
  let registry;
  try { registry = JSON.parse(bytes.toString('utf8')); }
  catch { fail('WORK_ITEM_REGISTRY_INVALID', 'Work item registry is not valid JSON'); }
  const validated = validateRegistry(registry, root);
  await assertPersistedDeliveryEvidence(root, validated, fs);
  return validated;
}

function renderWorkspaceOverview(registry) {
  const lines = [
    '# Work Item Overview',
    '',
    `> registry revision: ${registry.revision}`,
    `> current focus: ${registry.currentFocus.workItemId ?? 'none'}`,
    '',
    '| Work Item | Kind | Parent | Stage | Status | Delivery | Direct progress | Descendant progress | Gate | Claim |',
    '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |',
  ];
  for (const item of sortedItems(registry.workItems)) {
    lines.push(`| ${item.id} | ${item.kind} | ${item.parentId ?? 'none'} | ${item.stage} | ${item.status} | ${item.delivery?.status ?? 'n/a'} | ${item.progress.directChildren.verified}/${item.progress.directChildren.total} | ${item.progress.descendants.verified}/${item.progress.descendants.total} | ${item.gate.status} | ${item.claim?.owner ?? 'none'} |`);
  }
  lines.push('');
  return lines.join('\n');
}

function renderItemOverview(entry) {
  return [
    `# ${entry.id} Work Item Overview`,
    '',
    `- Kind: ${entry.kind}`,
    `- Authority: ${entry.authorityKind}`,
    `- Parent: ${entry.parentId ?? 'none'}`,
    `- Baseline: [baseline.md](baseline.md)`,
    `- Parent contract: ${entry.parentContractFingerprint ?? 'none'}`,
    `- Children: ${entry.childIds.join(', ') || 'none'}`,
    '',
  ].join('\n');
}

function renderItemProgress(entry) {
  return [
    `# ${entry.id} Progress`,
    '',
    `- Record revision: ${entry.recordRevision}`,
    `- Stage: ${entry.stage}`,
    `- Status: ${entry.status}`,
    `- Delivery: ${entry.delivery?.status ?? 'n/a'}`,
    `- Gate: ${entry.gate.status}`,
    `- Claim: ${entry.claim ? `${entry.claim.owner} / ${entry.claim.operationId}` : 'none'}`,
    `- Direct children: ${entry.progress.directChildren.verified}/${entry.progress.directChildren.total} verified; ${entry.progress.directChildren.blocked} blocked; ${entry.progress.directChildren.active} active`,
    `- Descendants: ${entry.progress.descendants.verified}/${entry.progress.descendants.total} verified; ${entry.progress.descendants.blocked} blocked; ${entry.progress.descendants.active} active`,
    `- Updated at: ${entry.updatedAt}`,
    '',
  ].join('\n');
}

function progressCounts(entries) {
  return {
    total: entries.length,
    verified: entries.filter(({ status }) => status === 'VERIFIED').length,
    blocked: entries.filter(({ status }) => status === 'BLOCKED').length,
    active: entries.filter(({ status }) => status === 'CLAIMED' || status === 'IMPLEMENTED').length,
  };
}

function recomputeRegistryProgress(registry) {
  const byId = new Map(registry.workItems.map((entry) => [entry.id, entry]));
  const descendants = (entry, visited = new Set()) => {
    if (visited.has(entry.id)) fail('WORK_ITEM_HIERARCHY_CYCLE', 'Work item hierarchy contains a cycle');
    const nextVisited = new Set(visited).add(entry.id);
    const result = [];
    for (const childId of entry.childIds) {
      const child = byId.get(childId) ?? { id: childId, status: 'PLANNED', childIds: [] };
      result.push(child);
      if (byId.has(childId)) result.push(...descendants(child, nextVisited));
    }
    return result;
  };
  for (const entry of registry.workItems) {
    const direct = entry.childIds.map((id) => byId.get(id) ?? { id, status: 'PLANNED' });
    entry.progress = {
      directChildren: progressCounts(direct),
      descendants: progressCounts(descendants(entry)),
    };
  }
}

async function writeRegistryUnlocked(root, registry, fs) {
  recomputeRegistryProgress(registry);
  registry.workItems = sortedItems(registry.workItems);
  await atomicWriteFile(registryPath(root), json(registry), { fs });
  await atomicWriteFile(
    path.join(root, GOVERNANCE_DIRECTORY, 'workspace-overview.md'),
    renderWorkspaceOverview(registry),
    { fs },
  );
  for (const entry of registry.workItems) {
    const target = itemPath(root, entry.id);
    let stat;
    try { stat = await fs.lstat(target); }
    catch (error) { if (error.code === 'ENOENT') continue; throw error; }
    if (!stat.isDirectory() || stat.isSymbolicLink()) fail('WORK_ITEM_PACKAGE_INVALID', `${entry.id} package path is invalid`);
    await atomicWriteFile(path.join(target, 'overview.md'), renderItemOverview(entry), { fs });
    await atomicWriteFile(path.join(target, 'progress.md'), renderItemProgress(entry), { fs });
  }
}

async function withRegistry(root, fs, operation, { now } = {}) {
  await ensureRuntimeRoot(root, fs);
  return withRuntimeDirectoryTransaction(registryPath(root), async () => {
    const registry = await readRegistryUnlocked(root, fs, { allowMissing: true, now });
    return operation(registry);
  }, { fs, now });
}

async function readJsonFile(root, target, fs, code) {
  let value;
  try { value = JSON.parse((await readSafeRegularFile(root, target, { fs })).toString('utf8')); }
  catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail(code, `Unable to read ${path.basename(target)}`);
  }
  return value;
}

async function readPackageDefinition(root, entry, fs) {
  const target = itemPath(root, entry.id);
  const definition = await readJsonFile(target, 'baseline.json', fs, 'WORK_ITEM_PACKAGE_INVALID');
  const state = await readJsonFile(target, 'state.json', fs, 'WORK_ITEM_PACKAGE_INVALID');
  const fingerprint = workItemBaselineFingerprint(definition);
  const valid = state.schemaVersion === 2
    && state.id === entry.id
    && state.baselineFingerprint === fingerprint
    && state.contractFingerprint === workItemContractFingerprint(definition)
    && entry.baselineFingerprint === state.baselineFingerprint
    && entry.contractFingerprint === state.contractFingerprint;
  if (!valid) fail('WORK_ITEM_PACKAGE_CHANGED', `${entry.id} package changed after preparation`, { id: entry.id });
  return { definition, state, target };
}

async function assertCurrentLineage(root, registry, entry, fs, seen = new Set()) {
  if (seen.has(entry.id)) fail('WORK_ITEM_HIERARCHY_CYCLE', 'Work item hierarchy contains a cycle');
  seen.add(entry.id);
  const own = await readPackageDefinition(root, entry, fs);
  if (!entry.parentId) return own;
  const parentEntry = itemById(registry, entry.parentId);
  const parentTarget = itemPath(root, parentEntry.id);
  const parentDefinition = await readJsonFile(parentTarget, 'baseline.json', fs, 'WORK_ITEM_PACKAGE_INVALID');
  const actualParentContract = workItemChildContractFingerprint(parentDefinition, entry.id);
  if (entry.parentContractFingerprint !== actualParentContract
      || own.definition.parentContractFingerprint !== actualParentContract) {
    fail('WORK_ITEM_BASELINE_STALE', `${entry.id} parent contract changed`, {
      id: entry.id,
      parentId: parentEntry.id,
      expected: entry.parentContractFingerprint,
      actual: actualParentContract,
    });
  }
  await assertCurrentLineage(root, registry, parentEntry, fs, seen);
  return own;
}

function definitionFiles(definition, state) {
  const files = {
    'baseline.json': json(definition),
    'baseline.md': renderWorkItemBaseline(definition),
    'work-item.json': json({
      schemaVersion: 2,
      id: definition.id,
      kind: definition.kind,
      authorityKind: definition.authorityKind,
      parentId: definition.parentId,
    }),
    'state.json': json(state),
  };
  if (definition.children) files['children.json'] = json({ schemaVersion: 2, children: definition.children });
  if (definition.execution) files['execution.json'] = json({ schemaVersion: 2, ...definition.execution });
  return files;
}

async function writeNewPackage(target, files, fs) {
  await atomicWriteDirectory(target, async (staging) => {
    for (const [name, contents] of Object.entries(files)) {
      await atomicWriteFile(path.join(staging, name), contents, { fs });
    }
  }, { fs });
}

function entryFromDefinition(definition, state, at) {
  return {
    id: definition.id,
    kind: definition.kind,
    authorityKind: definition.authorityKind,
    parentId: definition.parentId,
    childIds: definition.children?.map(({ id }) => id) ?? [],
    packagePath: itemRelativePath(definition.id),
    stage: state.stage,
    status: 'PREPARED',
    baselineFingerprint: state.baselineFingerprint,
    contractFingerprint: state.contractFingerprint,
    parentContractFingerprint: state.parentContractFingerprint,
    gate: { status: 'NOT_RUN', evidence: null },
    delivery: definition.kind === 'DELIVERY'
      ? { status: 'NOT_READY', review: null, userConfirmation: null }
      : null,
    claim: null,
    latestEvidence: null,
    recordRevision: 1,
    createdAt: at,
    updatedAt: at,
  };
}

function validateTaskDependencies(definition, parent) {
  if (definition.kind !== 'TASK') return;
  const siblingIds = new Set(parent.children.map(({ id }) => id));
  if (definition.execution.dependsOn.some((id) => !siblingIds.has(id))) {
    fail('WORK_ITEM_DEPENDENCY_INVALID', 'Task dependsOn must reference planned sibling Tasks');
  }
}

async function validateCapabilityDependencyGraph(root, registry, candidate, fs) {
  if (candidate.kind !== 'CAPABILITY') return;
  const graph = new Map();
  for (const entry of registry.workItems.filter(({ kind, parentId }) => (
    kind === 'CAPABILITY' && parentId === candidate.parentId
  ))) {
    const definition = entry.id === candidate.id
      ? candidate
      : (await readPackageDefinition(root, entry, fs)).definition;
    graph.set(definition.id, definition.decomposition.dependsOn);
  }
  graph.set(candidate.id, candidate.decomposition.dependsOn);
  const visiting = new Set();
  const visited = new Set();
  const visit = (id) => {
    if (visiting.has(id)) fail('WORK_ITEM_DEPENDENCY_CYCLE', 'Capability dependencies contain a cycle');
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dependency of graph.get(id) ?? []) if (graph.has(dependency)) visit(dependency);
    visiting.delete(id);
    visited.add(id);
  };
  for (const id of graph.keys()) visit(id);
}

export async function prepareWorkItem({
  root,
  definition,
  hostRuntime: suppliedHostRuntime,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp(now);
  const hostRuntime = requireHostRuntime(suppliedHostRuntime);
  return withRegistry(root, fs, async (registry) => {
    const existing = registry.workItems.find(({ id }) => id === definition?.id);
    if (existing) {
      const current = await readPackageDefinition(root, existing, fs);
      let candidate;
      if (definition.kind === 'DELIVERY') candidate = validateWorkItemDefinition(definition);
      else {
        const parentEntry = itemById(registry, definition.parentId);
        const parent = (await readPackageDefinition(root, parentEntry, fs)).definition;
        candidate = validateWorkItemDefinition(definition, { parent });
      }
      if (workItemBaselineFingerprint(candidate) !== current.state.baselineFingerprint) {
        fail('WORK_ITEM_SOURCE_CHANGED', `${existing.id} prepared baseline differs from the requested definition`);
      }
      return { created: false, idempotent: true, id: existing.id, stage: existing.stage };
    }

    let parent = null;
    if (definition.kind !== 'DELIVERY') {
      const parentEntry = itemById(registry, definition.parentId);
      if (parentEntry.stage !== 'BASELINE_FROZEN') fail('WORK_ITEM_PARENT_NOT_FROZEN', 'Parent baseline must be frozen first');
      parent = (await assertCurrentLineage(root, registry, parentEntry, fs)).definition;
    }
    const normalized = validateWorkItemDefinition(definition, { parent });
    validateTaskDependencies(normalized, parent);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      schemaVersion: 2,
      id: normalized.id,
      stage: 'WAITING_FOR_BASELINE_CONFIRMATION',
      baselineFingerprint: workItemBaselineFingerprint(normalized),
      contractFingerprint: workItemContractFingerprint(normalized),
      parentContractFingerprint: normalized.parentContractFingerprint,
      hostRuntime,
      createdAt: at,
      frozenAt: null,
    };
    const target = await assertSafePath(root, path.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, normalized.id), { fs });
    await writeNewPackage(target, definitionFiles(normalized, state), fs);
    const entry = entryFromDefinition(normalized, state, at);
    registry.workItems.push(entry);
    if (entry.parentId) {
      const parentEntry = itemById(registry, entry.parentId);
      parentEntry.childIds = [...new Set([...parentEntry.childIds, entry.id])].sort();
      parentEntry.recordRevision += 1;
      parentEntry.updatedAt = at;
    }
    registry.currentFocus = { workItemId: entry.id, purpose: 'BASELINE_CONFIRMATION' };
    registry.revision += 1;
    registry.updatedAt = at;
    try { await writeRegistryUnlocked(root, registry, fs); }
    catch (error) {
      await fs.rm(target, { recursive: true, force: true }).catch(() => {});
      throw error;
    }
    return {
      created: true,
      idempotent: false,
      id: entry.id,
      kind: entry.kind,
      stage: entry.stage,
      baselineFingerprint: entry.baselineFingerprint,
      artifactDir: target,
    };
  }, { now });
}

export async function freezeWorkItem({
  root,
  id,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail('CONFIRMATION_REQUIRED', 'Work item baseline freeze requires explicit confirmation');
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (expectedBaselineFingerprint !== undefined && entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail('WORK_ITEM_REVISION_CONFLICT', 'The confirmed baseline fingerprint is not current');
    }
    const taskPackage = await assertCurrentLineage(root, registry, entry, fs);
    if (entry.stage === 'BASELINE_FROZEN') {
      return { created: false, idempotent: true, id, stage: entry.stage, baselineFingerprint: entry.baselineFingerprint };
    }
    if (entry.stage !== 'WAITING_FOR_BASELINE_CONFIRMATION') fail('WORK_ITEM_STAGE_INVALID', `${id} is not ready to freeze`);
    const state = {
      ...taskPackage.state,
      stage: 'BASELINE_FROZEN',
      frozenAt: at,
    };
    await atomicWriteFile(path.join(taskPackage.target, 'state.json'), json(state), { fs });
    entry.stage = 'BASELINE_FROZEN';
    entry.status = 'FROZEN';
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = { workItemId: id, purpose: entry.kind === 'TASK' ? 'EXECUTION' : 'DECOMPOSITION' };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { created: true, idempotent: false, id, stage: entry.stage, baselineFingerprint: entry.baselineFingerprint };
  }, { now });
}

async function retryBlockedWorkItem({
  root,
  id,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail('CONFIRMATION_REQUIRED', 'Work item retry requires explicit confirmation');
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.status !== 'BLOCKED' || entry.claim) {
      fail('WORK_ITEM_RETRY_INVALID', 'Only an unclaimed BLOCKED work item can be retried');
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail('WORK_ITEM_REVISION_CONFLICT', 'The retry baseline fingerprint is not current');
    }
    await assertCurrentLineage(root, registry, entry, fs);
    entry.status = 'FROZEN';
    entry.gate = { status: 'NOT_RUN', evidence: null };
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.kind === 'TASK' ? 'EXECUTION_RETRY' : 'AGGREGATE_GATE_RETRY',
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, baselineFingerprint: entry.baselineFingerprint };
  }, { now });
}

export async function retryWorkItem(options = {}) {
  return retryBlockedWorkItem(options);
}

async function copyPackageContents(source, staging, fs) {
  const entries = await fs.readdir(source, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isSymbolicLink()) fail('WORK_ITEM_PACKAGE_INVALID', 'Work item packages cannot contain symbolic links');
    await fs.cp(path.join(source, entry.name), path.join(staging, entry.name), {
      recursive: entry.isDirectory(),
      force: false,
      errorOnExist: true,
    });
  }
}

export async function reviseWorkItem({
  root,
  definition,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail('CONFIRMATION_REQUIRED', 'Work item baseline revision requires explicit confirmation');
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, definition?.id);
    if (entry.stage !== 'BASELINE_FROZEN') fail('WORK_ITEM_STAGE_INVALID', 'Only frozen work items can be revised');
    if (entry.status === 'VERIFIED') fail('WORK_ITEM_REVISION_AFTER_VERIFICATION', 'Verified work items cannot be revised');
    if (entry.status === 'BLOCKED') {
      fail('WORK_ITEM_RETRY_REQUIRED', 'A BLOCKED work item must be explicitly retried before baseline revision');
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail('WORK_ITEM_REVISION_CONFLICT', 'The expected baseline fingerprint is not current');
    }
    const current = await assertCurrentLineage(root, registry, entry, fs);
    let parent;
    if (entry.parentId) {
      const parentEntry = itemById(registry, entry.parentId);
      parent = (await assertCurrentLineage(root, registry, parentEntry, fs)).definition;
    }
    const normalized = validateWorkItemDefinition(definition, { parent });
    if (normalized.id !== entry.id || normalized.kind !== entry.kind) {
      fail('WORK_ITEM_REVISION_IDENTITY_CHANGED', 'A revision cannot change work item identity or kind');
    }
    if (current.definition.children) {
      const revisedIds = new Set(normalized.children.map(({ id }) => id));
      const removed = current.definition.children.filter(({ id }) => !revisedIds.has(id));
      if (removed.length > 0) fail('WORK_ITEM_CHILD_REMOVAL_FORBIDDEN', 'Baseline revisions may append or refine children but cannot remove them');
    }
    const activeDescendants = registry.workItems.filter((candidate) => (
      candidate.claim && isDescendantOf(registry, candidate, entry.id)
    ));
    if (entry.kind === 'TASK' && activeDescendants.length > 0) {
      fail('WORK_ITEM_REVISION_ACTIVE_CLAIM', 'A claimed Task cannot be revised');
    }
    for (const candidate of activeDescendants) {
      let directChild = candidate;
      while (directChild.parentId && directChild.parentId !== entry.id) {
        directChild = itemById(registry, directChild.parentId);
      }
      const before = workItemChildContractFingerprint(current.definition, directChild.id);
      const after = workItemChildContractFingerprint(normalized, directChild.id);
      if (before !== after) {
        fail('WORK_ITEM_REVISION_ACTIVE_CLAIM', 'A revision cannot invalidate an actively claimed descendant');
      }
    }
    validateTaskDependencies(normalized, parent);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      ...current.state,
      baselineFingerprint: workItemBaselineFingerprint(normalized),
      contractFingerprint: workItemContractFingerprint(normalized),
      parentContractFingerprint: normalized.parentContractFingerprint,
      baselineRevision: (current.state.baselineRevision ?? 1) + 1,
      revisedAt: at,
    };
    const files = definitionFiles(normalized, state);
    await atomicReplaceDirectory(current.target, async (staging) => {
      await copyPackageContents(current.target, staging, fs);
      for (const [name, contents] of Object.entries(files)) {
        await atomicWriteFile(path.join(staging, name), contents, { fs });
      }
    }, { fs });
    entry.childIds = normalized.children?.map(({ id }) => id) ?? [];
    entry.baselineFingerprint = state.baselineFingerprint;
    entry.contractFingerprint = state.contractFingerprint;
    entry.parentContractFingerprint = state.parentContractFingerprint;
    entry.status = 'FROZEN';
    entry.gate = { status: 'NOT_RUN', evidence: null };
    entry.latestEvidence = null;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = { workItemId: entry.id, purpose: entry.kind === 'TASK' ? 'EXECUTION' : 'DECOMPOSITION' };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id: entry.id,
      kind: entry.kind,
      baselineRevision: state.baselineRevision,
      baselineFingerprint: state.baselineFingerprint,
      status: entry.status,
    };
  }, { now });
}

export async function readWorkItemRegistry({ root, fs = fsPromises } = {}) {
  return readRegistryUnlocked(root, fs);
}

function isDescendantOf(registry, entry, ancestorId) {
  let current = entry;
  const visited = new Set();
  while (current) {
    if (current.id === ancestorId) return true;
    if (!current.parentId || visited.has(current.id)) return false;
    visited.add(current.id);
    current = registry.workItems.find(({ id }) => id === current.parentId);
  }
  return false;
}

async function taskDefinition(root, entry, fs) {
  return (await readPackageDefinition(root, entry, fs)).definition;
}

async function taskReady(root, registry, entry, fs) {
  if (entry.kind !== 'TASK' || entry.stage !== 'BASELINE_FROZEN' || entry.status !== 'FROZEN' || entry.claim) return false;
  await assertCurrentLineage(root, registry, entry, fs);
  const definition = await taskDefinition(root, entry, fs);
  const capabilityEntry = itemById(registry, entry.parentId);
  const capability = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
  const capabilitiesReady = capability.decomposition.dependsOn.every((id) => (
    registry.workItems.find((candidate) => candidate.id === id)?.status === 'VERIFIED'
  ));
  if (!capabilitiesReady) return false;
  const dependenciesReady = definition.execution.dependsOn.every((id) => (
    registry.workItems.find((candidate) => candidate.id === id)?.status === 'VERIFIED'
  ));
  if (!dependenciesReady) return false;
  for (const claimed of registry.workItems.filter((candidate) => candidate.claim)) {
    const claimedDefinition = await taskDefinition(root, claimed, fs);
    if (scopePatternsOverlap(definition.scope, claimedDefinition.scope)) return false;
  }
  return true;
}

export async function listReadyTasks({ root, deliveryId, fs = fsPromises } = {}) {
  const registry = await readRegistryUnlocked(root, fs);
  const delivery = itemById(registry, deliveryId);
  if (delivery.kind !== 'DELIVERY') fail('WORK_ITEM_DELIVERY_REQUIRED', 'ready task listing requires a Delivery ID');
  const ready = [];
  for (const entry of sortedItems(registry.workItems)) {
    if (isDescendantOf(registry, entry, deliveryId) && await taskReady(root, registry, entry, fs)) ready.push(entry.id);
  }
  return ready;
}

function safeOperationId(value, field) {
  if (typeof value !== 'string' || !/^[a-z0-9][a-z0-9._-]*$/.test(value)) {
    fail('WORK_ITEM_OPERATION_INVALID', `${field} must be a safe lowercase identifier`);
  }
  return value;
}

export async function claimTask({ root, id, owner, operationId, explicitDogfood = false, now, fs = fsPromises } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (!await taskReady(root, registry, entry, fs)) fail('WORK_ITEM_NOT_READY', `${id} is not ready for dispatch`);
    entry.claim = {
      owner: safeOperationId(owner, 'owner'),
      operationId: safeOperationId(operationId, 'operationId'),
      claimedAt: at,
    };
    entry.status = 'CLAIMED';
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = { workItemId: id, purpose: 'EXECUTION' };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, claim: entry.claim };
  }, { now });
}

function evidenceRecord(value) {
  const valid = value && typeof value === 'object' && !Array.isArray(value)
    && typeof value.path === 'string' && value.path.length > 0
    && !path.posix.isAbsolute(value.path.replaceAll('\\', '/'))
    && !value.path.replaceAll('\\', '/').split('/').includes('..')
    && typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.sha256);
  if (!valid) fail('WORK_ITEM_EVIDENCE_INVALID', 'Evidence must contain a safe relative path and sha256');
  return { path: value.path.replaceAll('\\', '/'), sha256: value.sha256 };
}

async function verifiedDeliveryEvidence(root, evidence, action, fs) {
  const reference = evidenceRecord(evidence);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, reference.path, { fs });
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail('WORK_ITEM_DELIVERY_EVIDENCE_MISSING', `Unable to read delivery evidence: ${reference.path}`);
  }
  if (sha256Bytes(bytes) !== reference.sha256) {
    fail('WORK_ITEM_DELIVERY_EVIDENCE_CHANGED', `Delivery evidence hash does not match: ${reference.path}`);
  }
  let artifact;
  try { artifact = JSON.parse(bytes.toString('utf8')); }
  catch { fail('WORK_ITEM_DELIVERY_EVIDENCE_INVALID', 'Delivery evidence must be valid JSON'); }
  if (!validDeliveryArtifact(action, artifact)) {
    fail('WORK_ITEM_DELIVERY_EVIDENCE_INVALID', `Delivery evidence does not prove ${action}`);
  }
  return { reference, artifact };
}

export async function recordTaskResult({
  root,
  id,
  operationId,
  status,
  evidence,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!['IMPLEMENTED', 'BLOCKED'].includes(status)) fail('WORK_ITEM_RESULT_INVALID', 'Task result must be IMPLEMENTED or BLOCKED');
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== 'TASK' || entry.status !== 'CLAIMED' || entry.claim?.operationId !== operationId) {
      fail('WORK_ITEM_OPERATION_INVALID', `${id} does not have the supplied active operation`);
    }
    entry.status = status;
    entry.claim = null;
    entry.latestEvidence = evidenceRecord(evidence);
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status };
  }, { now });
}

function allChildrenVerified(registry, entry, definition) {
  const actual = new Map(registry.workItems.filter(({ parentId }) => parentId === entry.id).map((item) => [item.id, item]));
  return definition.children.length > 0
    && definition.children.every(({ id }) => actual.get(id)?.status === 'VERIFIED');
}

export async function recordWorkItemGate({ root, id, status, evidence, explicitDogfood = false, now, fs = fsPromises } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!['PASS', 'FAIL'].includes(status)) fail('WORK_ITEM_GATE_INVALID', 'Gate status must be PASS or FAIL');
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    const taskPackage = await assertCurrentLineage(root, registry, entry, fs);
    if (entry.status === 'BLOCKED') {
      fail('WORK_ITEM_RETRY_REQUIRED', `${id} must be explicitly retried before its gate can run again`);
    }
    if (entry.status === 'VERIFIED') {
      fail('WORK_ITEM_GATE_ALREADY_PASSED', `${id} gate has already passed`);
    }
    if (status === 'PASS') {
      if (entry.kind === 'TASK' && entry.status !== 'IMPLEMENTED') {
        fail('WORK_ITEM_IMPLEMENTATION_INCOMPLETE', `${id} must be implemented before its gate can pass`);
      }
      if (entry.kind !== 'TASK') {
        if (taskPackage.definition.decomposition.status !== 'SEALED') {
          fail('WORK_ITEM_DECOMPOSITION_OPEN', `${id} decomposition must be SEALED before its aggregate gate can pass`);
        }
        if (!allChildrenVerified(registry, entry, taskPackage.definition)) {
          fail('WORK_ITEM_CHILDREN_INCOMPLETE', `${id} children must all be verified before its aggregate gate can pass`);
        }
      }
    }
    entry.gate = { status, evidence: evidenceRecord(evidence) };
    entry.status = status === 'PASS' ? 'VERIFIED' : 'BLOCKED';
    if (entry.kind === 'DELIVERY') {
      entry.delivery = status === 'PASS'
        ? { status: 'WAITING_FOR_INDEPENDENT_REVIEW', review: null, userConfirmation: null }
        : { status: 'NOT_READY', review: null, userConfirmation: null };
    }
    entry.latestEvidence = entry.gate.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = { workItemId: id, purpose: status === 'PASS' ? 'AGGREGATION' : 'BLOCKER' };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, gate: entry.gate };
  }, { now });
}

export async function recordDelivery({
  root,
  id,
  action,
  evidence,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!['INDEPENDENT_REVIEW_PASS', 'HUMAN_REVIEW_ACCEPTED', 'USER_CONFIRMED'].includes(action)) {
    fail('WORK_ITEM_DELIVERY_ACTION_INVALID', 'Delivery action is invalid');
  }
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== 'DELIVERY' || entry.status !== 'VERIFIED') {
      fail('WORK_ITEM_DELIVERY_INVALID', 'Only a verified Delivery can advance delivery');
    }
    await assertCurrentLineage(root, registry, entry, fs);
    entry.delivery ??= {
      status: 'WAITING_FOR_INDEPENDENT_REVIEW',
      review: null,
      userConfirmation: null,
    };
    if (action === 'USER_CONFIRMED') {
      if (entry.delivery.status !== 'WAITING_FOR_USER_CONFIRMATION') {
        fail('WORK_ITEM_DELIVERY_STAGE_INVALID', 'User confirmation requires a passed independent or accepted human review');
      }
      const verifiedEvidence = await verifiedDeliveryEvidence(root, evidence, action, fs);
      const reviewEvidence = entry.delivery.review.evidence;
      if (reviewEvidence.path === verifiedEvidence.reference.path
          || reviewEvidence.sha256 === verifiedEvidence.reference.sha256) {
        fail('WORK_ITEM_DELIVERY_EVIDENCE_REUSED', 'User confirmation evidence must be distinct from review evidence');
      }
      entry.delivery = {
        ...entry.delivery,
        status: 'COMPLETED',
        userConfirmation: {
          action,
          evidence: verifiedEvidence.reference,
          artifact: verifiedEvidence.artifact,
          recordedAt: at,
        },
      };
    } else {
      if (entry.delivery.status !== 'WAITING_FOR_INDEPENDENT_REVIEW') {
        fail('WORK_ITEM_DELIVERY_STAGE_INVALID', 'Delivery is not waiting for independent review');
      }
      const verifiedEvidence = await verifiedDeliveryEvidence(root, evidence, action, fs);
      entry.delivery = {
        ...entry.delivery,
        status: 'WAITING_FOR_USER_CONFIRMATION',
        review: {
          action,
          evidence: verifiedEvidence.reference,
          artifact: verifiedEvidence.artifact,
          recordedAt: at,
        },
      };
    }
    entry.latestEvidence = action === 'USER_CONFIRMED'
      ? entry.delivery.userConfirmation.evidence
      : entry.delivery.review.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.delivery.status === 'COMPLETED' ? 'DELIVERY_COMPLETE' : 'USER_CONFIRMATION',
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, action, delivery: entry.delivery };
  }, { now });
}

function parentContractSnapshot(parent, childId) {
  const child = parent.children.find(({ id }) => id === childId);
  return {
    id: parent.id,
    kind: parent.kind,
    contractFingerprint: workItemChildContractFingerprint(parent, childId),
    goal: parent.goal,
    scope: parent.scope,
    childContract: child,
  };
}

function renderTaskHandoff(context) {
  return [
    '# Task Development Handoff',
    '',
    `Task: ${context.task.id}`,
    'Frozen authority: `baseline.json`',
    'Independent context: `context-manifest.json`',
    '',
    '## Rules',
    '- Implement only this frozen leaf Task.',
    '- Do not reinterpret parent contracts or acceptance criteria.',
    '- Write only within the listed Scope.',
    '- Return BLOCKED when a dependency, contract, or workspace is unavailable.',
    '- Do not commit, push, publish, or report PASS.',
    '',
    '## Scope',
    ...context.task.scope.map((entry) => `- ${entry}`),
    '',
    '## Test Commands',
    ...context.testCommands.map((argv) => `- ${JSON.stringify(argv)}`),
    '',
  ].join('\n');
}

export async function buildTaskContext({ root, id, explicitDogfood = false, fs = fsPromises } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const registry = await readRegistryUnlocked(root, fs);
  const entry = itemById(registry, id);
  if (entry.kind !== 'TASK' || entry.stage !== 'BASELINE_FROZEN') {
    fail('WORK_ITEM_TASK_REQUIRED', 'Independent context can only be built for a frozen Task');
  }
  const own = await assertCurrentLineage(root, registry, entry, fs);
  const parents = [];
  let childId = entry.id;
  let parentId = entry.parentId;
  while (parentId) {
    const parentEntry = itemById(registry, parentId);
    const parent = (await readPackageDefinition(root, parentEntry, fs)).definition;
    parents.unshift(parentContractSnapshot(parent, childId));
    childId = parent.id;
    parentId = parent.parentId;
  }
  const dependencies = [];
  for (const dependencyId of own.definition.execution.dependsOn) {
    const dependency = itemById(registry, dependencyId);
    const definition = (await readPackageDefinition(root, dependency, fs)).definition;
    dependencies.push({
      id: dependency.id,
      status: dependency.status,
      outputs: definition.execution.outputs,
      evidence: dependency.latestEvidence,
    });
  }
  if (dependencies.some(({ status }) => status !== 'VERIFIED')) {
    fail('WORK_ITEM_NOT_READY', `${id} has unverified Task dependencies`);
  }
  const capabilityEntry = itemById(registry, entry.parentId);
  const capabilityDefinition = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
  const capabilityDependencies = capabilityDefinition.decomposition.dependsOn.map((dependencyId) => {
    const dependency = itemById(registry, dependencyId);
    return {
      id: dependency.id,
      status: dependency.status,
      contractFingerprint: dependency.contractFingerprint,
      evidence: dependency.latestEvidence,
    };
  });
  if (capabilityDependencies.some(({ status }) => status !== 'VERIFIED')) {
    fail('WORK_ITEM_NOT_READY', `${id} has unverified Capability dependencies`);
  }
  const context = {
    schemaVersion: 1,
    task: {
      id: own.definition.id,
      title: own.definition.title,
      goal: own.definition.goal,
      scope: own.definition.scope,
      baselineFingerprint: entry.baselineFingerprint,
    },
    parentContracts: parents,
    capabilityDependencies,
    dependencies,
    requirements: own.definition.requirements,
    acceptance: own.definition.acceptance,
    execution: own.definition.execution,
    testCommands: own.definition.testCommands,
    rules: {
      inheritConversation: false,
      allowRequirementChanges: false,
      allowExternalStateChanges: false,
    },
  };
  await atomicWriteFile(path.join(own.target, 'context-manifest.json'), json(context), { fs });
  await atomicWriteFile(path.join(own.target, 'development-handoff.md'), renderTaskHandoff(context), { fs });
  return context;
}

export function registryFingerprint(registry) {
  return sha256Bytes(Buffer.from(canonicalJson(registry), 'utf8'));
}
