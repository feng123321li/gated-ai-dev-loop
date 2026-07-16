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
  WORK_ITEM_GATE_LEVELS,
  WORK_ITEM_KINDS,
  WORK_ITEM_SCHEMA_VERSION,
} from './model.mjs';

export const WORK_ITEM_REGISTRY_FILE = 'work-item-registry.json';
export const WORK_ITEMS_DIRECTORY = 'work-items';
export const GOVERNANCE_DIRECTORY = '.hierarchical-delivery-governance';
export const WORK_ITEM_REGISTRY_SCHEMA_VERSION = 3;
const DELIVERY_STATUSES = Object.freeze([
  'NOT_READY',
  'WAITING_FOR_INDEPENDENT_REVIEW',
  'WAITING_FOR_USER_CONFIRMATION',
  'COMPLETED',
]);
const ACCEPTANCE_STATUSES = DELIVERY_STATUSES;
const ACCEPTANCE_REPORT_STATUSES = Object.freeze([
  ...ACCEPTANCE_STATUSES,
  'WAITING_FOR_GATE',
  'BLOCKED',
  'VERIFIED',
]);
const DEVELOPMENT_MODES = Object.freeze(['active', 'manual']);

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
    schemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
    coordinationRoot: path.resolve(root),
    revision: 0,
    currentFocus: { workItemId: null, purpose: null },
    workItems: [],
    promotionHistory: [],
    migrationHistory: [],
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

function safeWorkItemId(value) {
  return typeof value === 'string'
    && /^[a-z0-9][a-z0-9._-]*$/.test(value)
    && !value.endsWith('.')
    && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/.test(value);
}

function validDevelopmentMode(value, entry) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && value.schemaVersion === 1
    && value.taskId === entry.id
    && value.baselineFingerprint === entry.baselineFingerprint
    && DEVELOPMENT_MODES.includes(value.mode)
    && value.confirmedBy === 'user'
    && typeof value.confirmedAt === 'string'
    && !Number.isNaN(Date.parse(value.confirmedAt));
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

function validAcceptance(value) {
  return validDelivery(value);
}

function validAcceptanceReport(value, entry) {
  if (value === undefined || value === null) return true;
  const expectedDirectory = path.posix.join(
    GOVERNANCE_DIRECTORY,
    WORK_ITEMS_DIRECTORY,
    entry.id,
  );
  return value && typeof value === 'object' && !Array.isArray(value)
    && value.schemaVersion === 1
    && ACCEPTANCE_REPORT_STATUSES.includes(value.status)
    && value.jsonPath === path.posix.join(expectedDirectory, 'acceptance-report.json')
    && value.markdownPath === path.posix.join(expectedDirectory, 'acceptance-report.md')
    && typeof value.generatedAt === 'string'
    && !Number.isNaN(Date.parse(value.generatedAt));
}

function validateRegistry(registry, root) {
  const valid = registry && typeof registry === 'object' && !Array.isArray(registry)
    && registry.schemaVersion === WORK_ITEM_REGISTRY_SCHEMA_VERSION
    && registry.coordinationRoot === path.resolve(root)
    && Number.isInteger(registry.revision) && registry.revision >= 0
    && Array.isArray(registry.workItems)
    && Array.isArray(registry.promotionHistory)
    && (registry.migrationHistory === undefined || Array.isArray(registry.migrationHistory))
    && registry.currentFocus && typeof registry.currentFocus === 'object';
  if (!valid) fail('WORK_ITEM_REGISTRY_INVALID', 'Work item registry is invalid');
  const ids = registry.workItems.map(({ id }) => id);
  const safeId = safeWorkItemId;
  if (new Set(ids).size !== ids.length || ids.some((id) => !safeId(id))) {
    fail('WORK_ITEM_REGISTRY_INVALID', 'Work item registry contains duplicate or unsafe IDs');
  }
  const byId = new Map(registry.workItems.map((entry) => [entry.id, entry]));
  const fingerprint = (value) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  for (const promotion of registry.promotionHistory) {
    const recordValid = promotion && typeof promotion === 'object' && !Array.isArray(promotion);
    const kindsValid = recordValid && (
      (promotion.childKind === 'TASK' && promotion.parentKind === 'CAPABILITY')
      || (promotion.childKind === 'CAPABILITY' && promotion.parentKind === 'DELIVERY')
    );
    const promotionValid = recordValid
      && promotion.schemaVersion === 1
      && safeId(promotion.childId) && safeId(promotion.parentId)
      && kindsValid
      && fingerprint(promotion.previousBaselineFingerprint)
      && fingerprint(promotion.promotedBaselineFingerprint)
      && fingerprint(promotion.parentBaselineFingerprint)
      && typeof promotion.promotedAt === 'string'
      && !Number.isNaN(Date.parse(promotion.promotedAt));
    if (!promotionValid) fail('WORK_ITEM_REGISTRY_INVALID', 'Work item promotion history is invalid');
  }
  for (const migration of registry.migrationHistory ?? []) {
    const migrationValid = migration && typeof migration === 'object' && !Array.isArray(migration)
      && migration.schemaVersion === 1
      && migration.fromSchemaVersion === 2
      && migration.toSchemaVersion === WORK_ITEM_REGISTRY_SCHEMA_VERSION
      && safeId(migration.workItemId)
      && WORK_ITEM_GATE_LEVELS.includes(migration.taskGateLevel)
      && fingerprint(migration.previousBaselineFingerprint)
      && fingerprint(migration.migratedBaselineFingerprint)
      && fingerprint(migration.previousRegistryFingerprint)
      && typeof migration.migratedAt === 'string'
      && !Number.isNaN(Date.parse(migration.migratedAt));
    if (!migrationValid) fail('WORK_ITEM_REGISTRY_INVALID', 'Work item migration history is invalid');
  }
  for (const entry of registry.workItems) {
    const validEntry = WORK_ITEM_KINDS.includes(entry.kind)
      && entry.authorityKind === WORK_ITEM_AUTHORITIES[entry.kind]
      && WORK_ITEM_GATE_LEVELS.includes(entry.gateLevel)
      && (entry.kind === 'TASK' || entry.gateLevel === 'FULL')
      && (entry.parentId === null || safeId(entry.parentId))
      && Array.isArray(entry.childIds) && entry.childIds.every(safeId)
      && entry.packagePath === itemRelativePath(entry.id)
      && typeof entry.baselineFingerprint === 'string' && /^[a-f0-9]{64}$/.test(entry.baselineFingerprint)
      && typeof entry.contractFingerprint === 'string' && /^[a-f0-9]{64}$/.test(entry.contractFingerprint);
    if (!validEntry) fail('WORK_ITEM_REGISTRY_INVALID', `Work item registry entry is invalid: ${entry.id}`);
    const developmentModeValid = entry.kind === 'TASK'
      ? entry.developmentMode === null || validDevelopmentMode(entry.developmentMode, entry)
      : entry.developmentMode === null;
    if (!developmentModeValid) {
      fail('WORK_ITEM_REGISTRY_INVALID', `Work item development mode is invalid: ${entry.id}`);
    }
    if (entry.kind === 'TASK') {
      const waitingForMode = entry.status === 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION';
      const frozenWithoutMode = entry.developmentMode === null && entry.stage === 'BASELINE_FROZEN';
      if (waitingForMode !== frozenWithoutMode) {
        fail('WORK_ITEM_REGISTRY_INVALID', `Task development mode state is inconsistent: ${entry.id}`);
      }
    }
    const deliveryValid = entry.kind === 'DELIVERY'
      ? (entry.delivery === undefined || validDelivery(entry.delivery))
      : entry.delivery === undefined || entry.delivery === null;
    if (!deliveryValid) fail('WORK_ITEM_REGISTRY_INVALID', `Work item delivery state is invalid: ${entry.id}`);
    const acceptanceValid = entry.parentId === null
      ? entry.acceptance === undefined || validAcceptance(entry.acceptance)
      : entry.acceptance === undefined || entry.acceptance === null;
    if (!acceptanceValid || !validAcceptanceReport(entry.acceptanceReport, entry)) {
      fail('WORK_ITEM_REGISTRY_INVALID', `Work item acceptance state is invalid: ${entry.id}`);
    }
    if (entry.kind === 'DELIVERY' && entry.parentId !== null) {
      fail('WORK_ITEM_REGISTRY_INVALID', 'Delivery entries cannot have parents');
    }
    if (entry.kind !== 'DELIVERY' && entry.parentId !== null) {
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
    if (error.code === 'ENOENT') fail('WORK_ITEM_ROOT_INVALID', 'Coordination root must already exist');
    throw error;
  });
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail('WORK_ITEM_ROOT_INVALID', 'Coordination root must be a regular directory');
  const runtimeRoot = await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  await fs.mkdir(runtimeRoot, { recursive: true });
  await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  const itemsRoot = await assertSafePath(root, path.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  await fs.mkdir(itemsRoot, { recursive: true });
  await assertSafePath(root, path.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  return runtimeRoot;
}

async function assertPersistedDeliveryEvidence(root, registry, fs) {
  for (const entry of registry.workItems.filter((candidate) => {
    const acceptance = candidate.acceptance ?? candidate.delivery;
    return candidate.parentId === null && acceptance
      && acceptance.status !== 'NOT_READY'
      && acceptance.status !== 'WAITING_FOR_INDEPENDENT_REVIEW';
  })) {
    const acceptance = entry.acceptance ?? entry.delivery;
    const records = [acceptance.review];
    if (acceptance.status === 'COMPLETED') records.push(acceptance.userConfirmation);
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

async function assertPersistedDevelopmentModes(root, registry, fs) {
  for (const entry of registry.workItems.filter(({ kind, developmentMode }) => (
    kind === 'TASK' && developmentMode !== null
  ))) {
    let artifact;
    try {
      artifact = await readJsonFile(
        itemPath(root, entry.id),
        'development-mode.json',
        fs,
        'WORK_ITEM_DEVELOPMENT_MODE_INVALID',
      );
    } catch {
      fail('WORK_ITEM_DEVELOPMENT_MODE_INVALID', `${entry.id} development-mode.json is missing or unreadable`);
    }
    if (!validDevelopmentMode(artifact, entry)
        || canonicalJson(artifact) !== canonicalJson(entry.developmentMode)) {
      fail('WORK_ITEM_DEVELOPMENT_MODE_CHANGED', `${entry.id} development-mode.json changed after confirmation`);
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
  await assertPersistedDevelopmentModes(root, validated, fs);
  return validated;
}

function humanStatus(value) {
  return ({
    DELIVERY: '交付',
    CAPABILITY: '能力',
    TASK: '任务',
    PREPARED: '等待基线确认',
    WAITING_FOR_DEVELOPMENT_MODE_SELECTION: '等待选择开发方式',
    FROZEN: '已冻结',
    CLAIMED: '开发中',
    IMPLEMENTED: '等待门禁验收',
    BLOCKED: '已阻断',
    VERIFIED: '门禁已通过',
    NOT_READY: '尚未就绪',
    WAITING_FOR_INDEPENDENT_REVIEW: '等待独立验收',
    WAITING_FOR_USER_CONFIRMATION: '等待用户确认',
    COMPLETED: '已完成',
    NOT_RUN: '未运行',
    PASS: '通过',
    FAIL: '未通过',
  })[value] ?? value ?? '无';
}

function renderWorkspaceOverview(registry) {
  const lines = [
    '# 工作项总览',
    '',
    '> 本文件是面向用户和协作者的可读投影；机器权威为 `work-item-registry.json`。',
    `> 注册表版本：${registry.revision}`,
    `> 当前焦点：${registry.currentFocus.workItemId ?? '无'}`,
    '',
    '| 工作项 | 类型 | 门禁等级 | 父级 | 当前状态 | 开发方式 | 最终验收 | 直接子级 | 全部后代 | 门禁 | 认领者 | 验收报告 |',
    '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |',
  ];
  for (const item of sortedItems(registry.workItems)) {
    const report = item.acceptanceReport
      ? `[查看](${path.posix.relative(GOVERNANCE_DIRECTORY, item.acceptanceReport.markdownPath)})`
      : '尚未生成';
    const acceptance = item.acceptance ?? (item.parentId === null ? item.delivery : null);
    lines.push(`| ${item.id} | ${humanStatus(item.kind)} | ${item.gateLevel} | ${item.parentId ?? '无'} | ${humanStatus(item.status)} | ${item.developmentMode?.mode ?? '不适用'} | ${acceptance ? humanStatus(acceptance.status) : '不适用'} | ${item.progress.directChildren.verified}/${item.progress.directChildren.total} | ${item.progress.descendants.verified}/${item.progress.descendants.total} | ${humanStatus(item.gate.status)} | ${item.claim?.owner ?? '无'} | ${report} |`);
  }
  lines.push('');
  return lines.join('\n');
}

function renderItemOverview(entry) {
  return [
    `# ${entry.id} 工作项概览`,
    '',
    `- 类型：${entry.kind}`,
    `- 门禁等级：${entry.gateLevel}`,
    `- 权限性质：${entry.authorityKind}`,
    `- 父级：${entry.parentId ?? '无'}`,
    '- 基线：[baseline.md](baseline.md)',
    `- 父契约指纹：${entry.parentContractFingerprint ?? '无'}`,
    `- 子级：${entry.childIds.join(', ') || '无'}`,
    `- 验收报告：${entry.acceptanceReport ? '[acceptance-report.md](acceptance-report.md)' : '尚未生成'}`,
    '',
  ].join('\n');
}

function renderItemProgress(entry) {
  const acceptance = entry.acceptance ?? (entry.parentId === null ? entry.delivery : null);
  return [
    `# ${entry.id} 进度`,
    '',
    `- 记录版本：${entry.recordRevision}`,
    `- 阶段：${entry.stage}`,
    `- 当前状态：${humanStatus(entry.status)}`,
    `- 门禁等级：${entry.gateLevel}`,
    `- 最终验收：${acceptance ? humanStatus(acceptance.status) : '不适用'}`,
    `- 门禁：${humanStatus(entry.gate.status)}`,
    `- 开发方式：${entry.developmentMode?.mode ?? '未选择'}`,
    `- 认领：${entry.claim ? `${entry.claim.owner} / ${entry.claim.operationId}` : '无'}`,
    `- 直接子级：${entry.progress.directChildren.verified}/${entry.progress.directChildren.total} 已验证；${entry.progress.directChildren.blocked} 阻断；${entry.progress.directChildren.active} 活动`,
    `- 全部后代：${entry.progress.descendants.verified}/${entry.progress.descendants.total} 已验证；${entry.progress.descendants.blocked} 阻断；${entry.progress.descendants.active} 活动`,
    `- 验收报告：${entry.acceptanceReport ? '[acceptance-report.md](acceptance-report.md)' : '尚未生成'}`,
    `- 更新时间：${entry.updatedAt}`,
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
  const valid = state.schemaVersion === WORK_ITEM_SCHEMA_VERSION
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
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
      id: definition.id,
      kind: definition.kind,
      gateLevel: definition.gateLevel,
      authorityKind: definition.authorityKind,
      parentId: definition.parentId,
    }),
    'state.json': json(state),
  };
  if (definition.children) files['children.json'] = json({ schemaVersion: WORK_ITEM_SCHEMA_VERSION, children: definition.children });
  if (definition.execution) files['execution.json'] = json({ schemaVersion: WORK_ITEM_SCHEMA_VERSION, ...definition.execution });
  return files;
}

function rawDefinition(definition) {
  const raw = { ...definition };
  delete raw.authorityKind;
  delete raw.parentContractFingerprint;
  return raw;
}

async function writeNewPackage(target, files, fs) {
  await atomicWriteDirectory(target, async (staging) => {
    for (const [name, contents] of Object.entries(files)) {
      await atomicWriteFile(path.join(staging, name), contents, { fs });
    }
  }, { fs });
}

function entryFromDefinition(definition, state, at) {
  const rootAcceptance = definition.parentId === null
    ? { status: 'NOT_READY', review: null, userConfirmation: null }
    : null;
  return {
    id: definition.id,
    kind: definition.kind,
    gateLevel: definition.gateLevel,
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
    acceptance: rootAcceptance,
    acceptanceReport: null,
    developmentMode: null,
    claim: null,
    latestEvidence: null,
    latestResult: null,
    recordRevision: 1,
    createdAt: at,
    updatedAt: at,
  };
}

function validateTaskDependencies(definition, parent) {
  if (definition.kind !== 'TASK') return;
  if (!parent) {
    if (definition.execution.dependsOn.length > 0) {
      fail('WORK_ITEM_DEPENDENCY_INVALID', 'A root Task cannot depend on sibling Tasks; use a Capability root');
    }
    return;
  }
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
      if (definition.kind === 'DELIVERY' || definition.parentId === null) {
        candidate = validateWorkItemDefinition(definition);
      }
      else {
        const parentEntry = itemById(registry, definition.parentId);
        const parent = (await readPackageDefinition(root, parentEntry, fs)).definition;
        candidate = validateWorkItemDefinition(definition, { parent });
      }
      if (workItemBaselineFingerprint(candidate) !== current.state.baselineFingerprint) {
        fail('WORK_ITEM_SOURCE_CHANGED', `${existing.id} prepared baseline differs from the requested definition`);
      }
      return {
        created: false,
        idempotent: true,
        id: existing.id,
        kind: existing.kind,
        stage: existing.stage,
        baselineFingerprint: existing.baselineFingerprint,
        artifactDir: itemPath(root, existing.id),
      };
    }

    let parent = null;
    if (definition.kind !== 'DELIVERY' && definition.parentId !== null) {
      const parentEntry = itemById(registry, definition.parentId);
      if (parentEntry.stage !== 'BASELINE_FROZEN') fail('WORK_ITEM_PARENT_NOT_FROZEN', 'Parent baseline must be frozen first');
      parent = (await assertCurrentLineage(root, registry, parentEntry, fs)).definition;
    }
    const normalized = validateWorkItemDefinition(definition, { parent });
    validateTaskDependencies(normalized, parent);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
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
    entry.status = entry.kind === 'TASK' ? 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION' : 'FROZEN';
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.kind === 'TASK' ? 'DEVELOPMENT_MODE_SELECTION' : 'DECOMPOSITION',
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { created: true, idempotent: false, id, stage: entry.stage, baselineFingerprint: entry.baselineFingerprint };
  }, { now });
}

export async function approveWorkItem({
  root,
  definition,
  hostRuntime,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  if (confirmed !== true) {
    fail('CONFIRMATION_REQUIRED', 'Work item approval must explicitly authorize persistence and baseline freeze');
  }
  const prepared = await prepareWorkItem({
    root,
    definition,
    hostRuntime,
    explicitDogfood,
    now,
    fs,
  });
  const frozen = await freezeWorkItem({
    root,
    id: prepared.id,
    expectedBaselineFingerprint: prepared.baselineFingerprint,
    confirmed: true,
    explicitDogfood,
    now,
    fs,
  });
  return {
    ...frozen,
    approved: true,
    prepared: {
      created: prepared.created,
      idempotent: prepared.idempotent,
    },
    artifactDir: prepared.artifactDir,
  };
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
    const own = await assertCurrentLineage(root, registry, entry, fs);
    entry.status = 'FROZEN';
    entry.gate = { status: 'NOT_RUN', evidence: null };
    if (entry.parentId === null) {
      entry.acceptance = { status: 'NOT_READY', review: null, userConfirmation: null };
      if (entry.kind === 'DELIVERY') entry.delivery = entry.acceptance;
    }
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.kind === 'TASK' ? 'EXECUTION_RETRY' : 'AGGREGATE_GATE_RETRY',
    };
    registry.revision += 1;
    registry.updatedAt = at;
    if (entry.acceptanceReport) await writeAcceptanceReport(root, entry, own.definition, at, fs);
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
      if (entry.kind === 'TASK') {
        for (const name of ['development-mode.json', 'context-manifest.json', 'development-handoff.md']) {
          await fs.rm(path.join(staging, name), { force: true });
        }
      }
      for (const name of ['acceptance-report.json', 'acceptance-report.md']) {
        await fs.rm(path.join(staging, name), { force: true });
      }
    }, { fs });
    entry.childIds = normalized.children?.map(({ id }) => id) ?? [];
    entry.baselineFingerprint = state.baselineFingerprint;
    entry.contractFingerprint = state.contractFingerprint;
    entry.parentContractFingerprint = state.parentContractFingerprint;
    entry.status = entry.kind === 'TASK' ? 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION' : 'FROZEN';
    entry.developmentMode = null;
    entry.gate = { status: 'NOT_RUN', evidence: null };
    entry.acceptance = entry.parentId === null
      ? { status: 'NOT_READY', review: null, userConfirmation: null }
      : null;
    if (entry.kind === 'DELIVERY') entry.delivery = entry.acceptance;
    entry.acceptanceReport = null;
    entry.latestEvidence = null;
    entry.latestResult = null;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: entry.id,
      purpose: entry.kind === 'TASK' ? 'DEVELOPMENT_MODE_SELECTION' : 'DECOMPOSITION',
    };
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

export async function promoteWorkItem({
  root,
  id,
  parentId,
  expectedBaselineFingerprint,
  expectedParentBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail('CONFIRMATION_REQUIRED', 'Work item promotion requires explicit confirmation');
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    const parentEntry = itemById(registry, parentId);
    if (entry.id === parentEntry.id) fail('WORK_ITEM_PROMOTION_INVALID', 'A work item cannot promote under itself');
    if (entry.parentId !== null || !['TASK', 'CAPABILITY'].includes(entry.kind)) {
      fail('WORK_ITEM_PROMOTION_ROOT_REQUIRED', 'Only a root Task or root Capability can be promoted');
    }
    const expectedParentKind = entry.kind === 'TASK' ? 'CAPABILITY' : 'DELIVERY';
    if (parentEntry.kind !== expectedParentKind || parentEntry.parentId !== null) {
      fail('WORK_ITEM_PROMOTION_PARENT_INVALID', `${entry.kind} promotion requires a root ${expectedParentKind} parent`);
    }
    if (entry.stage !== 'BASELINE_FROZEN'
        || !['FROZEN', 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION'].includes(entry.status)
        || entry.gate.status !== 'NOT_RUN') {
      fail('WORK_ITEM_PROMOTION_SOURCE_NOT_FROZEN', 'Promotion source must be an unblocked, unverified frozen root');
    }
    if (parentEntry.stage !== 'BASELINE_FROZEN'
        || parentEntry.status !== 'FROZEN'
        || parentEntry.gate.status !== 'NOT_RUN') {
      fail('WORK_ITEM_PROMOTION_PARENT_NOT_FROZEN', 'Promotion parent baseline must be frozen before attachment');
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint
        || parentEntry.baselineFingerprint !== expectedParentBaselineFingerprint) {
      fail('WORK_ITEM_REVISION_CONFLICT', 'Promotion fingerprints are not current');
    }
    const active = registry.workItems.find((candidate) => (
      candidate.claim && isDescendantOf(registry, candidate, entry.id)
    ));
    if (active) fail('WORK_ITEM_PROMOTION_ACTIVE_CLAIM', 'A promoted subtree cannot contain an active claim');

    const current = await assertCurrentLineage(root, registry, entry, fs);
    const parentPackage = await assertCurrentLineage(root, registry, parentEntry, fs);
    const normalized = validateWorkItemDefinition({
      ...rawDefinition(current.definition),
      parentId: parentEntry.id,
    }, { parent: parentPackage.definition });
    validateTaskDependencies(normalized, parentPackage.definition);
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
      if (entry.kind === 'TASK') {
        for (const name of ['development-mode.json', 'context-manifest.json', 'development-handoff.md']) {
          await fs.rm(path.join(staging, name), { force: true });
        }
      }
      for (const name of ['acceptance-report.json', 'acceptance-report.md']) {
        await fs.rm(path.join(staging, name), { force: true });
      }
    }, { fs });

    const previousBaselineFingerprint = entry.baselineFingerprint;
    entry.parentId = parentEntry.id;
    entry.baselineFingerprint = state.baselineFingerprint;
    entry.contractFingerprint = state.contractFingerprint;
    entry.parentContractFingerprint = state.parentContractFingerprint;
    entry.status = entry.kind === 'TASK' ? 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION' : 'FROZEN';
    entry.developmentMode = null;
    entry.gate = { status: 'NOT_RUN', evidence: null };
    entry.acceptance = null;
    entry.acceptanceReport = null;
    entry.latestEvidence = null;
    entry.latestResult = null;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    parentEntry.recordRevision += 1;
    parentEntry.updatedAt = at;
    registry.promotionHistory.push({
      schemaVersion: 1,
      childId: entry.id,
      childKind: entry.kind,
      parentId: parentEntry.id,
      parentKind: parentEntry.kind,
      previousBaselineFingerprint,
      promotedBaselineFingerprint: entry.baselineFingerprint,
      parentBaselineFingerprint: parentEntry.baselineFingerprint,
      promotedAt: at,
    });
    registry.currentFocus = {
      workItemId: entry.id,
      purpose: entry.kind === 'TASK' ? 'DEVELOPMENT_MODE_SELECTION' : 'DECOMPOSITION',
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id: entry.id,
      kind: entry.kind,
      gateLevel: entry.gateLevel,
      parentId: entry.parentId,
      baselineRevision: state.baselineRevision,
      baselineFingerprint: entry.baselineFingerprint,
      status: entry.status,
    };
  }, { now });
}

export async function readWorkItemRegistry({ root, fs = fsPromises } = {}) {
  return readRegistryUnlocked(root, fs);
}

function legacyWorkItemContractFingerprint(definition) {
  const legacyContract = {
    schemaVersion: definition.schemaVersion,
    id: definition.id,
    kind: definition.kind,
    goal: definition.goal,
    scope: [...definition.scope].sort(),
    requirements: [...definition.requirements].sort((left, right) => left.id.localeCompare(right.id)),
    acceptance: [...definition.acceptance].sort((left, right) => left.id.localeCompare(right.id)),
    testCommands: definition.testCommands,
  };
  if (definition.children) legacyContract.children = [...definition.children].sort((left, right) => left.id.localeCompare(right.id));
  if (definition.decomposition) legacyContract.decomposition = definition.decomposition;
  if (definition.execution) legacyContract.execution = definition.execution;
  return sha256Bytes(Buffer.from(canonicalJson(legacyContract), 'utf8'));
}

function validateLegacyRootTaskRegistry(registry, root) {
  const entry = registry?.workItems?.[0];
  const registryValid = registry && typeof registry === 'object' && !Array.isArray(registry)
    && registry.schemaVersion === 2
    && registry.coordinationRoot === path.resolve(root)
    && Number.isInteger(registry.revision) && registry.revision >= 0
    && Array.isArray(registry.workItems) && registry.workItems.length === 1
    && (registry.promotionHistory === undefined
      || (Array.isArray(registry.promotionHistory) && registry.promotionHistory.length === 0))
    && registry.currentFocus && typeof registry.currentFocus === 'object';
  const entryValid = entry && safeWorkItemId(entry.id)
    && entry.kind === 'TASK'
    && entry.authorityKind === WORK_ITEM_AUTHORITIES.TASK
    && entry.parentId === null
    && Array.isArray(entry.childIds) && entry.childIds.length === 0
    && entry.packagePath === itemRelativePath(entry.id)
    && entry.stage === 'BASELINE_FROZEN'
    && ['FROZEN', 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION'].includes(entry.status)
    && typeof entry.baselineFingerprint === 'string' && /^[a-f0-9]{64}$/.test(entry.baselineFingerprint)
    && typeof entry.contractFingerprint === 'string' && /^[a-f0-9]{64}$/.test(entry.contractFingerprint)
    && entry.parentContractFingerprint === null
    && entry.gate?.status === 'NOT_RUN' && entry.gate.evidence === null
    && entry.claim === null
    && entry.latestEvidence === null
    && (entry.delivery === undefined || entry.delivery === null)
    && Number.isInteger(entry.recordRevision) && entry.recordRevision >= 1;
  if (!registryValid || !entryValid) {
    fail(
      'WORK_ITEM_SCHEMA_MIGRATION_UNSUPPORTED',
      'Schema v2 migration currently supports one inactive frozen root Task with no gate result or claim',
    );
  }
  const waitingForMode = entry.status === 'WAITING_FOR_DEVELOPMENT_MODE_SELECTION';
  if (waitingForMode !== (entry.developmentMode === null)) {
    fail('WORK_ITEM_SCHEMA_MIGRATION_UNSUPPORTED', 'Legacy Task development mode state is inconsistent');
  }
  if (entry.developmentMode !== null && !validDevelopmentMode(entry.developmentMode, entry)) {
    fail('WORK_ITEM_SCHEMA_MIGRATION_UNSUPPORTED', 'Legacy Task development mode is invalid');
  }
  return entry;
}

export async function upgradeWorkItemRegistry({
  root,
  taskGateLevel,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp(now);
  return withRuntimeDirectoryTransaction(registryPath(root), async () => {
    let registryBytes;
    try { registryBytes = await readSafeRegularFile(root, registryPath(root), { fs }); }
    catch (error) {
      if (error.code === 'ENOENT') fail('WORK_ITEM_REGISTRY_MISSING', 'Work item registry does not exist');
      throw error;
    }
    let legacyRegistry;
    try { legacyRegistry = JSON.parse(registryBytes.toString('utf8')); }
    catch { fail('WORK_ITEM_REGISTRY_INVALID', 'Work item registry is not valid JSON'); }

    if (legacyRegistry.schemaVersion === WORK_ITEM_REGISTRY_SCHEMA_VERSION) {
      const current = await readRegistryUnlocked(root, fs);
      return {
        migrated: false,
        idempotent: true,
        fromSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
        toSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
        revision: current.revision,
      };
    }
    if (confirmed !== true) {
      fail('CONFIRMATION_REQUIRED', 'Schema v2 migration requires explicit confirmation of the Task gate level');
    }
    if (!WORK_ITEM_GATE_LEVELS.includes(taskGateLevel)) {
      fail('WORK_ITEM_GATE_LEVEL_INVALID', 'Schema v2 migration requires taskGateLevel LIGHT or FULL');
    }

    const legacyEntry = validateLegacyRootTaskRegistry(legacyRegistry, root);
    const target = itemPath(root, legacyEntry.id);
    const targetStat = await fs.lstat(target).catch(() => null);
    if (!targetStat?.isDirectory() || targetStat.isSymbolicLink()) {
      fail('WORK_ITEM_PACKAGE_INVALID', `${legacyEntry.id} package path is invalid`);
    }
    const legacyDefinition = await readJsonFile(target, 'baseline.json', fs, 'WORK_ITEM_PACKAGE_INVALID');
    const legacyState = await readJsonFile(target, 'state.json', fs, 'WORK_ITEM_PACKAGE_INVALID');
    const legacyMetadata = await readJsonFile(target, 'work-item.json', fs, 'WORK_ITEM_PACKAGE_INVALID');
    const legacyBaselineFingerprint = sha256Bytes(Buffer.from(canonicalJson(legacyDefinition), 'utf8'));
    const legacyContractFingerprint = legacyWorkItemContractFingerprint(legacyDefinition);
    const packageValid = legacyDefinition.schemaVersion === 2
      && legacyDefinition.id === legacyEntry.id
      && legacyDefinition.kind === 'TASK'
      && legacyDefinition.authorityKind === WORK_ITEM_AUTHORITIES.TASK
      && legacyDefinition.parentId === null
      && legacyDefinition.parentContractFingerprint === null
      && !Object.hasOwn(legacyDefinition, 'gateLevel')
      && legacyState.schemaVersion === 2
      && legacyState.id === legacyEntry.id
      && legacyState.stage === legacyEntry.stage
      && legacyState.baselineFingerprint === legacyBaselineFingerprint
      && legacyState.contractFingerprint === legacyContractFingerprint
      && legacyState.parentContractFingerprint === null
      && legacyEntry.baselineFingerprint === legacyBaselineFingerprint
      && legacyEntry.contractFingerprint === legacyContractFingerprint
      && legacyMetadata.schemaVersion === 2
      && legacyMetadata.id === legacyEntry.id
      && legacyMetadata.kind === legacyEntry.kind
      && legacyMetadata.parentId === null;
    if (!packageValid) {
      fail('WORK_ITEM_PACKAGE_CHANGED', `${legacyEntry.id} legacy package does not match its registry`);
    }

    let developmentMode = null;
    if (legacyEntry.developmentMode !== null) {
      const artifact = await readJsonFile(target, 'development-mode.json', fs, 'WORK_ITEM_DEVELOPMENT_MODE_INVALID');
      if (canonicalJson(artifact) !== canonicalJson(legacyEntry.developmentMode)) {
        fail('WORK_ITEM_DEVELOPMENT_MODE_CHANGED', `${legacyEntry.id} development-mode.json changed after confirmation`);
      }
      developmentMode = artifact;
    }

    const migratedDefinition = validateWorkItemDefinition({
      ...rawDefinition(legacyDefinition),
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
      gateLevel: taskGateLevel,
    });
    const migratedState = {
      ...legacyState,
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
      baselineFingerprint: workItemBaselineFingerprint(migratedDefinition),
      contractFingerprint: workItemContractFingerprint(migratedDefinition),
      parentContractFingerprint: migratedDefinition.parentContractFingerprint,
      baselineRevision: (legacyState.baselineRevision ?? 1) + 1,
      revisedAt: at,
      schemaMigration: {
        fromSchemaVersion: 2,
        toSchemaVersion: WORK_ITEM_SCHEMA_VERSION,
        migratedAt: at,
      },
    };
    if (developmentMode) {
      developmentMode = {
        ...developmentMode,
        baselineFingerprint: migratedState.baselineFingerprint,
      };
    }
    const migratedEntry = {
      ...legacyEntry,
      gateLevel: taskGateLevel,
      baselineFingerprint: migratedState.baselineFingerprint,
      contractFingerprint: migratedState.contractFingerprint,
      parentContractFingerprint: migratedState.parentContractFingerprint,
      delivery: null,
      acceptance: { status: 'NOT_READY', review: null, userConfirmation: null },
      acceptanceReport: null,
      developmentMode,
      latestResult: null,
      recordRevision: legacyEntry.recordRevision + 1,
      updatedAt: at,
    };
    const migratedRegistry = {
      ...legacyRegistry,
      schemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
      revision: legacyRegistry.revision + 1,
      workItems: [migratedEntry],
      promotionHistory: legacyRegistry.promotionHistory ?? [],
      migrationHistory: [
        ...(legacyRegistry.migrationHistory ?? []),
        {
          schemaVersion: 1,
          fromSchemaVersion: 2,
          toSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
          workItemId: migratedEntry.id,
          taskGateLevel,
          previousBaselineFingerprint: legacyBaselineFingerprint,
          migratedBaselineFingerprint: migratedState.baselineFingerprint,
          previousRegistryFingerprint: sha256Bytes(registryBytes),
          migratedAt: at,
        },
      ],
      updatedAt: at,
    };
    validateRegistry(migratedRegistry, root);

    await atomicReplaceDirectory(target, async (staging) => {
      await copyPackageContents(target, staging, fs);
      for (const [name, contents] of Object.entries(definitionFiles(migratedDefinition, migratedState))) {
        await atomicWriteFile(path.join(staging, name), contents, { fs });
      }
      if (developmentMode) {
        await atomicWriteFile(path.join(staging, 'development-mode.json'), json(developmentMode), { fs });
      }
      for (const name of [
        'context-manifest.json',
        'development-handoff.md',
        'acceptance-report.json',
        'acceptance-report.md',
      ]) {
        await fs.rm(path.join(staging, name), { force: true });
      }
    }, { fs });
    await writeRegistryUnlocked(root, migratedRegistry, fs);
    return {
      migrated: true,
      idempotent: false,
      fromSchemaVersion: 2,
      toSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
      taskId: migratedEntry.id,
      taskGateLevel,
      previousBaselineFingerprint: legacyBaselineFingerprint,
      baselineFingerprint: migratedState.baselineFingerprint,
      revision: migratedRegistry.revision,
    };
  }, { fs, now });
}

export async function refreshWorkItemProjections({
  root,
  explicitDogfood = false,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  await ensureRuntimeRoot(root, fs);
  return withRuntimeDirectoryTransaction(registryPath(root), async () => {
    const registry = await readRegistryUnlocked(root, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return {
      revision: registry.revision,
      workspaceOverview: path.posix.join(GOVERNANCE_DIRECTORY, 'workspace-overview.md'),
      workItems: registry.workItems.map(({ id, acceptanceReport }) => ({
        id,
        acceptanceReport: acceptanceReport?.markdownPath ?? null,
      })),
    };
  }, { fs });
}

export async function selectDevelopmentMode({
  root,
  id,
  mode,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) {
    fail('CONFIRMATION_REQUIRED', 'Development mode selection requires explicit user confirmation');
  }
  if (!DEVELOPMENT_MODES.includes(mode)) {
    fail('WORK_ITEM_DEVELOPMENT_MODE_INVALID', 'Development mode must be active or manual');
  }
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== 'TASK' || entry.stage !== 'BASELINE_FROZEN') {
      fail('WORK_ITEM_TASK_REQUIRED', 'Development mode can only be selected for a frozen Task');
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail('WORK_ITEM_REVISION_CONFLICT', 'The development mode confirmation is not bound to the current baseline');
    }
    if (entry.claim || !['WAITING_FOR_DEVELOPMENT_MODE_SELECTION', 'FROZEN'].includes(entry.status)) {
      fail('WORK_ITEM_DEVELOPMENT_MODE_LOCKED', 'Development mode cannot change after Task dispatch begins');
    }
    if (entry.developmentMode?.mode === mode) {
      return {
        created: false,
        idempotent: true,
        id,
        status: entry.status,
        developmentMode: entry.developmentMode,
      };
    }
    if (entry.developmentMode !== null) {
      fail('WORK_ITEM_DEVELOPMENT_MODE_LOCKED', 'Development mode is fixed for the current Task baseline');
    }
    const record = {
      schemaVersion: 1,
      taskId: id,
      baselineFingerprint: entry.baselineFingerprint,
      mode,
      confirmedBy: 'user',
      confirmedAt: at,
    };
    const target = itemPath(root, id);
    await atomicWriteFile(path.join(target, 'development-mode.json'), json(record), { fs });
    entry.developmentMode = record;
    entry.status = 'FROZEN';
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: mode === 'active' ? 'ACTIVE_DISPATCH' : 'MANUAL_HANDOFF',
    };
    registry.revision += 1;
    registry.updatedAt = at;
    try {
      await writeRegistryUnlocked(root, registry, fs);
    } catch (error) {
      await fs.rm(path.join(target, 'development-mode.json'), { force: true });
      throw error;
    }
    return {
      created: true,
      idempotent: false,
      id,
      status: entry.status,
      developmentMode: record,
    };
  }, { now });
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
  let capabilitiesReady = true;
  if (entry.parentId !== null) {
    const capabilityEntry = itemById(registry, entry.parentId);
    const capability = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
    capabilitiesReady = capability.decomposition.dependsOn.every((id) => (
      registry.workItems.find((candidate) => candidate.id === id)?.status === 'VERIFIED'
    ));
  }
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

export async function listReadyTasks({ root, workItemId, fs = fsPromises } = {}) {
  const registry = await readRegistryUnlocked(root, fs);
  itemById(registry, workItemId);
  const ready = [];
  for (const entry of sortedItems(registry.workItems)) {
    if (isDescendantOf(registry, entry, workItemId) && await taskReady(root, registry, entry, fs)) ready.push(entry.id);
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
    if (entry.kind === 'TASK' && entry.developmentMode === null) {
      fail('WORK_ITEM_DEVELOPMENT_MODE_REQUIRED', `${id} requires an explicitly confirmed development mode`);
    }
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

async function readEvidenceArtifact(root, evidence, fs, {
  missingCode = 'WORK_ITEM_EVIDENCE_MISSING',
  changedCode = 'WORK_ITEM_EVIDENCE_CHANGED',
  invalidCode = 'WORK_ITEM_EVIDENCE_INVALID',
} = {}) {
  const reference = evidenceRecord(evidence);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, reference.path, { fs });
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail(missingCode, `Unable to read evidence: ${reference.path}`);
  }
  if (sha256Bytes(bytes) !== reference.sha256) {
    fail(changedCode, `Evidence hash does not match: ${reference.path}`);
  }
  let artifact;
  try { artifact = JSON.parse(bytes.toString('utf8')); }
  catch { fail(invalidCode, 'Evidence must be valid JSON'); }
  return { reference, artifact };
}

async function optionalTaskResultArtifact(root, evidence, expected, fs, strict = false) {
  const reference = evidenceRecord(evidence);
  let bytes;
  try { bytes = await readSafeRegularFile(root, reference.path, { fs }); }
  catch {
    if (strict) fail('WORK_ITEM_RESULT_EVIDENCE_MISSING', `Task result evidence is unavailable: ${reference.path}`);
    return { reference, artifact: null };
  }
  if (sha256Bytes(bytes) !== reference.sha256) {
    fail('WORK_ITEM_RESULT_EVIDENCE_CHANGED', `Task result evidence hash does not match: ${reference.path}`);
  }
  let artifact;
  try { artifact = JSON.parse(bytes.toString('utf8')); }
  catch { fail('WORK_ITEM_RESULT_EVIDENCE_INVALID', 'Task result evidence must be valid JSON'); }
  if (!validTaskResultArtifact(artifact, expected)) {
    fail('WORK_ITEM_RESULT_EVIDENCE_INVALID', 'Task result evidence does not match the active operation');
  }
  return { reference, artifact };
}

function validTaskResultArtifact(value, { id, operationId, status }) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && value.schemaVersion === 1
    && value.kind === 'TASK_RESULT'
    && value.taskId === id
    && value.operationId === operationId
    && value.status === status
    && nonEmptyString(value.summary)
    && Array.isArray(value.changedFiles) && value.changedFiles.every(nonEmptyString)
    && Array.isArray(value.tests)
    && value.tests.every((test) => test && typeof test === 'object' && Array.isArray(test.argv)
      && test.argv.every(nonEmptyString) && Number.isInteger(test.exitCode))
    && Array.isArray(value.blockers);
}

function validGateArtifact(value, entry, definition) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || value.schemaVersion !== 1 || value.kind !== 'WORK_ITEM_GATE'
      || value.workItemId !== entry.id
      || value.baselineFingerprint !== entry.baselineFingerprint
      || !['PASS', 'FAIL'].includes(value.verdict)
      || !nonEmptyString(value.summary)
      || !value.scope || typeof value.scope !== 'object' || Array.isArray(value.scope)
      || !Array.isArray(value.scope.changedFiles) || !value.scope.changedFiles.every(nonEmptyString)
      || !Array.isArray(value.scope.outOfScopeFiles) || !value.scope.outOfScopeFiles.every(nonEmptyString)
      || !Array.isArray(value.acceptance) || !Array.isArray(value.tests)
      || !value.findings || typeof value.findings !== 'object' || Array.isArray(value.findings)
      || !Array.isArray(value.findings.p0) || !Array.isArray(value.findings.p1)
      || !Array.isArray(value.findings.p2)) return false;
  const acceptanceById = new Map(value.acceptance.map((result) => [result?.id, result]));
  const testsByArgv = new Map(value.tests.map((result) => [canonicalJson(result?.argv), result]));
  const acceptanceComplete = definition.acceptance.every(({ id }) => {
    const result = acceptanceById.get(id);
    return result && ['PASS', 'FAIL'].includes(result.status) && nonEmptyString(result.evidence);
  });
  const testsComplete = definition.testCommands.every((argv) => {
    const result = testsByArgv.get(canonicalJson(argv));
    return result && Number.isInteger(result.exitCode) && nonEmptyString(result.summary)
      && (result.testsRun === undefined || (Number.isInteger(result.testsRun) && result.testsRun >= 0));
  });
  if (!acceptanceComplete || !testsComplete) return false;
  if (value.verdict === 'PASS') {
    return value.scope.outOfScopeFiles.length === 0
      && definition.acceptance.every(({ id }) => acceptanceById.get(id).status === 'PASS')
      && definition.testCommands.every((argv) => testsByArgv.get(canonicalJson(argv)).exitCode === 0)
      && value.findings.p0.length === 0
      && value.findings.p1.length === 0;
  }
  return true;
}

function reportStatus(entry) {
  const acceptance = entry.acceptance ?? (entry.parentId === null ? entry.delivery : null);
  if (acceptance && acceptance.status !== 'NOT_READY') return acceptance.status;
  if (entry.status === 'IMPLEMENTED') return 'WAITING_FOR_GATE';
  if (entry.status === 'BLOCKED') return 'BLOCKED';
  if (entry.status === 'VERIFIED') return 'VERIFIED';
  return 'NOT_READY';
}

function reportStatusText(status) {
  return ({
    NOT_READY: '尚未就绪',
    WAITING_FOR_GATE: '等待门禁验收',
    BLOCKED: '已阻断',
    VERIFIED: '门禁已通过',
    WAITING_FOR_INDEPENDENT_REVIEW: '等待独立验收',
    WAITING_FOR_USER_CONFIRMATION: '等待用户确认',
    COMPLETED: '已完成',
  })[status] ?? status;
}

function gateStatusText(status) {
  return ({ NOT_RUN: '未运行', PASS: '通过', FAIL: '未通过' })[status] ?? status;
}

function renderAcceptanceReport(report) {
  const gateArtifact = report.gate.artifact;
  const lines = [
    `# 验收报告：${report.workItem.title}`,
    '',
    `- 工作项：${report.workItem.id}`,
    `- 类型：${report.workItem.kind}`,
    `- 门禁等级：${report.workItem.gateLevel}`,
    `- 基线指纹：${report.workItem.baselineFingerprint}`,
    `- 最终状态：${reportStatusText(report.status)}`,
    `- 门禁结论：${gateStatusText(report.gate.status)}`,
    `- 生成时间：${report.generatedAt}`,
    '',
    '## 验收项',
    '',
    '| 编号 | 预期结果 | 结论 | 证据 |',
    '| --- | --- | --- | --- |',
  ];
  const results = new Map((gateArtifact?.acceptance ?? []).map((item) => [item.id, item]));
  for (const item of report.criteria) {
    const result = results.get(item.id);
    lines.push(`| ${item.id} | ${item.expectedResult} | ${result ? gateStatusText(result.status) : '待验收'} | ${result?.evidence ?? '无'} |`);
  }
  lines.push('', '## 测试结果', '');
  const tests = gateArtifact?.tests ?? report.development?.artifact?.tests ?? [];
  if (tests.length === 0) lines.push('- 尚无测试证据。');
  for (const result of tests) {
    lines.push(`- \`${JSON.stringify(result.argv)}\`：退出码 ${result.exitCode}；${result.summary ?? `Tests run: ${result.testsRun ?? '未记录'}`}`);
  }
  lines.push('', '## 变更范围', '');
  const scope = gateArtifact?.scope;
  lines.push(`- 已记录变更：${scope?.changedFiles?.join('、') || report.development?.artifact?.changedFiles?.join('、') || '无'}`);
  lines.push(`- 范围外变更：${scope?.outOfScopeFiles?.join('、') || '无'}`);
  lines.push('', '## 问题与建议', '');
  const findings = gateArtifact?.findings;
  lines.push(`- P0：${findings?.p0?.length ?? 0}`);
  lines.push(`- P1：${findings?.p1?.length ?? 0}`);
  lines.push(`- P2：${findings?.p2?.length ?? 0}`);
  lines.push('', '## 独立验收', '');
  lines.push(report.review
    ? `- ${report.review.artifact.reviewer}：${report.review.artifact.verdict}`
    : '- 尚未完成。');
  lines.push('', '## 用户确认', '');
  lines.push(report.userConfirmation
    ? `- ${report.userConfirmation.artifact.confirmedBy}：已确认`
    : '- 尚未确认。');
  lines.push('');
  return lines.join('\n');
}

async function writeAcceptanceReport(root, entry, definition, at, fs) {
  const acceptance = entry.acceptance ?? (entry.parentId === null ? entry.delivery : null);
  const status = reportStatus(entry);
  const report = {
    schemaVersion: 1,
    workItem: {
      id: entry.id,
      title: definition.title,
      kind: entry.kind,
      gateLevel: entry.gateLevel,
      baselineFingerprint: entry.baselineFingerprint,
      parentId: entry.parentId,
    },
    status,
    development: entry.latestResult,
    gate: entry.gate,
    criteria: definition.acceptance,
    review: acceptance?.review ?? null,
    userConfirmation: acceptance?.userConfirmation ?? null,
    generatedAt: at,
  };
  const directory = itemPath(root, entry.id);
  await atomicWriteFile(path.join(directory, 'acceptance-report.json'), json(report), { fs });
  await atomicWriteFile(path.join(directory, 'acceptance-report.md'), renderAcceptanceReport(report), { fs });
  entry.acceptanceReport = {
    schemaVersion: 1,
    status,
    jsonPath: path.posix.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, entry.id, 'acceptance-report.json'),
    markdownPath: path.posix.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, entry.id, 'acceptance-report.md'),
    generatedAt: at,
  };
  return report;
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
  strictEvidence = false,
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
    const own = await assertCurrentLineage(root, registry, entry, fs);
    const verifiedEvidence = await optionalTaskResultArtifact(
      root,
      evidence,
      { id, operationId, status },
      fs,
      strictEvidence,
    );
    entry.status = status;
    entry.claim = null;
    entry.latestEvidence = verifiedEvidence.reference;
    entry.latestResult = {
      evidence: verifiedEvidence.reference,
      artifact: verifiedEvidence.artifact,
      recordedAt: at,
    };
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.revision += 1;
    registry.updatedAt = at;
    await writeAcceptanceReport(root, entry, own.definition, at, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status, acceptanceReport: entry.acceptanceReport };
  }, { now });
}

function allChildrenVerified(registry, entry, definition) {
  const actual = new Map(registry.workItems.filter(({ parentId }) => parentId === entry.id).map((item) => [item.id, item]));
  return definition.children.length > 0
    && definition.children.every(({ id }) => actual.get(id)?.status === 'VERIFIED');
}

export async function recordWorkItemGate({
  root,
  id,
  status,
  evidence,
  gateArtifact = null,
  strictEvidence = false,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!['PASS', 'FAIL'].includes(status)) fail('WORK_ITEM_GATE_INVALID', 'Gate status must be PASS or FAIL');
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    const taskPackage = await assertCurrentLineage(root, registry, entry, fs);
    let verifiedGate = null;
    if (strictEvidence) {
      verifiedGate = await readEvidenceArtifact(root, evidence, fs, {
        missingCode: 'WORK_ITEM_GATE_EVIDENCE_MISSING',
        changedCode: 'WORK_ITEM_GATE_EVIDENCE_CHANGED',
        invalidCode: 'WORK_ITEM_GATE_EVIDENCE_INVALID',
      });
      if (!validGateArtifact(verifiedGate.artifact, entry, taskPackage.definition)
          || verifiedGate.artifact.verdict !== status
          || (gateArtifact && canonicalJson(gateArtifact) !== canonicalJson(verifiedGate.artifact))) {
        fail('WORK_ITEM_GATE_EVIDENCE_INVALID', 'Gate evidence does not prove the requested result');
      }
    }
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
    entry.gate = {
      status,
      evidence: verifiedGate?.reference ?? evidenceRecord(evidence),
      artifact: verifiedGate?.artifact ?? gateArtifact,
    };
    entry.status = status === 'PASS' ? 'VERIFIED' : 'BLOCKED';
    if (entry.parentId === null) {
      entry.acceptance = status === 'PASS'
        ? { status: 'WAITING_FOR_INDEPENDENT_REVIEW', review: null, userConfirmation: null }
        : { status: 'NOT_READY', review: null, userConfirmation: null };
    }
    if (entry.kind === 'DELIVERY') {
      entry.delivery = entry.acceptance;
    }
    entry.latestEvidence = entry.gate.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: status === 'PASS' && entry.parentId === null ? 'INDEPENDENT_REVIEW' : (status === 'PASS' ? 'AGGREGATION' : 'BLOCKER'),
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeAcceptanceReport(root, entry, taskPackage.definition, at, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id,
      status: entry.status,
      gate: entry.gate,
      acceptance: entry.acceptance,
      acceptanceReport: entry.acceptanceReport,
    };
  }, { now });
}

export async function acceptWorkItem({
  root,
  id,
  evidence,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  const registry = await readRegistryUnlocked(root, fs);
  const entry = itemById(registry, id);
  const own = await assertCurrentLineage(root, registry, entry, fs);
  const verified = await readEvidenceArtifact(root, evidence, fs, {
    missingCode: 'WORK_ITEM_GATE_EVIDENCE_MISSING',
    changedCode: 'WORK_ITEM_GATE_EVIDENCE_CHANGED',
    invalidCode: 'WORK_ITEM_GATE_EVIDENCE_INVALID',
  });
  if (!validGateArtifact(verified.artifact, entry, own.definition)) {
    fail('WORK_ITEM_GATE_EVIDENCE_INVALID', 'Gate evidence is incomplete or contradicts the requested verdict');
  }
  return recordWorkItemGate({
    root,
    id,
    status: verified.artifact.verdict,
    evidence: verified.reference,
    gateArtifact: verified.artifact,
    strictEvidence: true,
    explicitDogfood,
    now,
    fs,
  });
}

async function recordRootAcceptance({
  root,
  id,
  action,
  evidence,
  deliveryOnly = false,
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
    if (entry.parentId !== null || entry.status !== 'VERIFIED' || (deliveryOnly && entry.kind !== 'DELIVERY')) {
      fail(
        deliveryOnly ? 'WORK_ITEM_DELIVERY_INVALID' : 'WORK_ITEM_ACCEPTANCE_INVALID',
        'Only a verified root work item can advance final acceptance',
      );
    }
    const own = await assertCurrentLineage(root, registry, entry, fs);
    entry.acceptance ??= entry.delivery ?? {
      status: 'WAITING_FOR_INDEPENDENT_REVIEW',
      review: null,
      userConfirmation: null,
    };
    if (action === 'USER_CONFIRMED') {
      if (entry.acceptance.status !== 'WAITING_FOR_USER_CONFIRMATION') {
        fail(
          deliveryOnly ? 'WORK_ITEM_DELIVERY_STAGE_INVALID' : 'WORK_ITEM_ACCEPTANCE_STAGE_INVALID',
          'User confirmation requires a passed independent or accepted human review',
        );
      }
      const verifiedEvidence = await verifiedDeliveryEvidence(root, evidence, action, fs);
      const reviewEvidence = entry.acceptance.review.evidence;
      if (reviewEvidence.path === verifiedEvidence.reference.path
          || reviewEvidence.sha256 === verifiedEvidence.reference.sha256) {
        fail(
          deliveryOnly ? 'WORK_ITEM_DELIVERY_EVIDENCE_REUSED' : 'WORK_ITEM_ACCEPTANCE_EVIDENCE_REUSED',
          'User confirmation evidence must be distinct from review evidence',
        );
      }
      entry.acceptance = {
        ...entry.acceptance,
        status: 'COMPLETED',
        userConfirmation: {
          action,
          evidence: verifiedEvidence.reference,
          artifact: verifiedEvidence.artifact,
          recordedAt: at,
        },
      };
    } else {
      if (entry.acceptance.status !== 'WAITING_FOR_INDEPENDENT_REVIEW') {
        fail(
          deliveryOnly ? 'WORK_ITEM_DELIVERY_STAGE_INVALID' : 'WORK_ITEM_ACCEPTANCE_STAGE_INVALID',
          'Work item is not waiting for independent review',
        );
      }
      const verifiedEvidence = await verifiedDeliveryEvidence(root, evidence, action, fs);
      entry.acceptance = {
        ...entry.acceptance,
        status: 'WAITING_FOR_USER_CONFIRMATION',
        review: {
          action,
          evidence: verifiedEvidence.reference,
          artifact: verifiedEvidence.artifact,
          recordedAt: at,
        },
      };
    }
    if (entry.kind === 'DELIVERY') entry.delivery = entry.acceptance;
    entry.latestEvidence = action === 'USER_CONFIRMED'
      ? entry.acceptance.userConfirmation.evidence
      : entry.acceptance.review.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.acceptance.status === 'COMPLETED' ? 'ACCEPTANCE_COMPLETE' : 'USER_CONFIRMATION',
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeAcceptanceReport(root, entry, own.definition, at, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id,
      action,
      acceptance: entry.acceptance,
      delivery: entry.kind === 'DELIVERY' ? entry.delivery : null,
      acceptanceReport: entry.acceptanceReport,
    };
  }, { now });
}

export async function recordAcceptance(options = {}) {
  return recordRootAcceptance(options);
}

export async function recordDelivery(options = {}) {
  return recordRootAcceptance({ ...options, deliveryOnly: true });
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
  const resultTemplate = {
    schemaVersion: 1,
    kind: 'TASK_RESULT',
    taskId: context.task.id,
    operationId: context.operation?.operationId ?? '<claim-required>',
    status: 'IMPLEMENTED|BLOCKED',
    summary: '<development facts>',
    changedFiles: [],
    tests: [{ argv: ['<exact frozen argv>'], exitCode: 0, testsRun: 0 }],
    blockers: [],
  };
  return [
    '请在一个全新的开发会话中实现以下已冻结 Task。',
    '',
    `Task：${context.task.id}`,
    `Baseline fingerprint：${context.task.baselineFingerprint}`,
    `Gate level：${context.gateLevel}`,
    `Development mode：${context.developmentMode}`,
    `Operation ID：${context.operation?.operationId ?? '尚未认领；不得开始开发'}`,
    '',
    '以下冻结上下文是完整权威。不要重新分析原始需求、改变验收标准或继承其他会话的隐含假设。',
    '',
    '执行规则：',
    '- 只实现这个冻结的叶子 Task，并且只写入 Scope 中的路径。',
    '- 不修改 baseline、registry、进度投影、`.git/**` 或外部状态。',
    '- 运行列出的测试命令，只报告真实存在的证据。',
    '- 不提交、推送、发布，也不得自行报告 PASS。',
    '- 最终只返回 IMPLEMENTED 或 BLOCKED，并携带当前 Operation ID、变更文件和测试事实。',
    '- 宿主必须用 task-result 回收结果；返回开发结果后必须继续验收，IMPLEMENTED 不是完成状态。',
    '- 门禁通过后仍需独立验收、生成用户验收报告并取得用户确认。',
    '',
    '结果返回格式（由治理宿主保存为 evidence，并用相同 Operation ID 执行 task-result）：',
    '```json',
    JSON.stringify(resultTemplate, null, 2),
    '```',
    '',
    '冻结上下文：',
    '```json',
    JSON.stringify(context, null, 2),
    '```',
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
  if (entry.developmentMode === null) {
    fail('WORK_ITEM_DEVELOPMENT_MODE_REQUIRED', `${id} requires an explicitly confirmed development mode`);
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
  let capabilityDependencies = [];
  if (entry.parentId !== null) {
    const capabilityEntry = itemById(registry, entry.parentId);
    const capabilityDefinition = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
    capabilityDependencies = capabilityDefinition.decomposition.dependsOn.map((dependencyId) => {
      const dependency = itemById(registry, dependencyId);
      return {
        id: dependency.id,
        status: dependency.status,
        contractFingerprint: dependency.contractFingerprint,
        evidence: dependency.latestEvidence,
      };
    });
  }
  if (capabilityDependencies.some(({ status }) => status !== 'VERIFIED')) {
    fail('WORK_ITEM_NOT_READY', `${id} has unverified Capability dependencies`);
  }
  const context = {
    schemaVersion: 1,
    gateLevel: own.definition.gateLevel,
    developmentMode: entry.developmentMode.mode,
    operation: entry.claim ? {
      owner: entry.claim.owner,
      operationId: entry.claim.operationId,
      claimedAt: entry.claim.claimedAt,
    } : null,
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
  const handoffPrompt = renderTaskHandoff(context);
  await atomicWriteFile(path.join(own.target, 'context-manifest.json'), json(context), { fs });
  await atomicWriteFile(path.join(own.target, 'development-handoff.md'), handoffPrompt, { fs });
  return { ...context, handoffPrompt };
}

export async function dispatchTask({
  root,
  id,
  owner,
  operationId,
  explicitDogfood = false,
  now,
  fs = fsPromises,
} = {}) {
  await buildTaskContext({ root, id, explicitDogfood, fs });
  const claim = await claimTask({
    root,
    id,
    owner,
    operationId,
    explicitDogfood,
    now,
    fs,
  });
  const context = await buildTaskContext({ root, id, explicitDogfood, fs });
  return { ...claim, ...context };
}

export function registryFingerprint(registry) {
  return sha256Bytes(Buffer.from(canonicalJson(registry), 'utf8'));
}
