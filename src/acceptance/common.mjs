import * as fsPromises from 'node:fs/promises';
import path from 'node:path';

import { parseFullBaseline } from '../baseline/parse.mjs';
import { normalizeTestArgv } from '../baseline/test-command.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { assertSafePath, atomicWriteFile, readSafeRegularFile } from '../core/fs-safe.mjs';
import { canonicalRelativePath, sha256Bytes } from '../core/hash.mjs';
import { runProcess } from '../core/process.mjs';
import { readFullPackage } from '../full/package.mjs';
import { readLightPackage } from '../light/freeze.mjs';

const SHA = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const SHA256 = /^[a-f0-9]{64}$/;

export function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function json(value) { return `${JSON.stringify(value, null, 2)}\n`; }

export function fingerprint(value) {
  return sha256Bytes(Buffer.from(stableJson(value), 'utf8'));
}

export function normalizeRound(value = 'round-01') {
  const text = String(value);
  const match = /^(?:round-)?(\d{1,2})$/.exec(text);
  const number = match ? Number(match[1]) : 0;
  if (!Number.isInteger(number) || number < 1 || number > 99) {
    throw new GatedLoopError('ROUND_INVALID', 'Round must be between round-01 and round-99');
  }
  return `round-${String(number).padStart(2, '0')}`;
}

function parseLightBrief(markdown) {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
  const scope = [];
  const testCommands = [];
  let section = '';
  for (const line of lines) {
    const heading = /^## (.+)$/.exec(line);
    if (heading) { section = heading[1]; continue; }
    if (section === 'Scope' && line.startsWith('- ')) scope.push(canonicalRelativePath(line.slice(2)));
    if (section === 'Acceptance' && line.startsWith('- Test command: ')) {
      let value;
      try { value = JSON.parse(line.slice('- Test command: '.length)); }
      catch { throw new GatedLoopError('LIGHT_SOURCE_CHANGED', 'Frozen Light test command is invalid'); }
      const argv = normalizeTestArgv(value);
      if (!argv) throw new GatedLoopError('LIGHT_SOURCE_CHANGED', 'Frozen Light test command is unsafe');
      testCommands.push(argv);
    }
  }
  if (scope.length === 0 || testCommands.length === 0) {
    throw new GatedLoopError('LIGHT_SOURCE_CHANGED', 'Frozen Light scope or test commands are missing');
  }
  return { scope, testCommands };
}

export async function loadFrozenTask({ root, task, fs = fsPromises } = {}) {
  const modeBytes = await readSafeRegularFile(root, path.join('.ai-dev-loop', task, 'mode.json'), { fs });
  let mode;
  try { mode = JSON.parse(modeBytes.toString('utf8')); }
  catch { throw new GatedLoopError('FROZEN_TASK_INVALID', 'Frozen task mode is invalid'); }
  if (mode.mode === 'full') {
    const taskPackage = await readFullPackage({ root, task, fs });
    if (taskPackage.stage !== 'BASELINE_FROZEN') throw new GatedLoopError('BASELINE_NOT_FROZEN', 'Full baseline is not frozen');
    const authority = taskPackage.bytes['baseline.md'].toString('utf8');
    const model = parseFullBaseline(authority);
    return {
      task, mode: 'full', taskPackage, authorityName: 'baseline.md', authority,
      scope: null, testCommands: model.testCommands, acceptance: taskPackage.acceptance.acceptance,
      tasks: taskPackage.tasks.tasks, frozenFingerprint: taskPackage.state.frozenFingerprint,
    };
  }
  if (mode.mode === 'light') {
    const taskPackage = await readLightPackage({ root, task, fs });
    const authority = taskPackage.bytes['light-brief.md'].toString('utf8');
    const parsed = parseLightBrief(authority);
    return {
      task, mode: 'light', taskPackage, authorityName: 'light-brief.md', authority,
      scope: parsed.scope, testCommands: parsed.testCommands, acceptance: JSON.parse(taskPackage.bytes['acceptance.json'].toString('utf8')).acceptance,
      tasks: JSON.parse(taskPackage.bytes['tasks.json'].toString('utf8')).tasks,
      frozenFingerprint: taskPackage.state.frozenFingerprint,
    };
  }
  throw new GatedLoopError('FROZEN_TASK_INVALID', 'Task mode must be full or light');
}

function safePattern(value) {
  if (typeof value !== 'string' || value.length === 0 || value.includes('\0') || value.includes(':')) return null;
  let normalized;
  try { normalized = canonicalRelativePath(value); }
  catch { return null; }
  if (!normalized || normalized === '.' || normalized.startsWith('../')) return null;
  return normalized;
}

function exactKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && stableJson(Object.keys(value).sort()) === stableJson([...keys].sort());
}

function sameStringSet(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  const orderedLeft = [...left].sort();
  const orderedRight = [...right].sort();
  return orderedLeft.every((entry, index) => entry === orderedRight[index]);
}

function validateStringIds(value, allowed, label, { nonempty = true } = {}) {
  if (!Array.isArray(value) || (nonempty && value.length === 0)
      || value.some((entry) => typeof entry !== 'string' || !allowed.has(entry))
      || new Set(value).size !== value.length) {
    throw new GatedLoopError('SNAPSHOT_INVALID', `Development snapshot contains invalid ${label}`);
  }
  return [...value];
}

function validatePreExisting(value) {
  if (!Array.isArray(value)) throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot pre-existing changes must be an array');
  const entries = value.map((entry) => {
    const filePath = safePattern(entry?.path);
    const hashValid = entry?.worktreeSha256 === null || SHA256.test(entry?.worktreeSha256);
    if (!exactKeys(entry, ['path', 'statusCode', 'worktreeSha256'])
        || !filePath || typeof entry.statusCode !== 'string' || entry.statusCode.length !== 2 || !hashValid) {
      throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot contains an invalid pre-existing change');
    }
    return { path: filePath, statusCode: entry.statusCode, worktreeSha256: entry.worktreeSha256 };
  });
  if (new Set(entries.map((entry) => entry.path)).size !== entries.length) {
    throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot repeats a pre-existing path');
  }
  return entries;
}

function validateAllowedPaths(value, label = 'allowed paths') {
  if (!Array.isArray(value) || value.length === 0) {
    throw new GatedLoopError('SNAPSHOT_INVALID', `Development snapshot ${label} must be a non-empty array`);
  }
  const patterns = value.map(safePattern);
  if (patterns.includes(null) || new Set(patterns).size !== patterns.length) {
    throw new GatedLoopError('SNAPSHOT_INVALID', `Development snapshot contains unsafe or duplicate ${label}`);
  }
  if (patterns.some((pattern) => pattern === '.git' || pattern.startsWith('.git/')
      || pattern === '.ai-dev-loop' || pattern.startsWith('.ai-dev-loop/'))) {
    throw new GatedLoopError('SNAPSHOT_INVALID', `Development snapshot ${label} includes a protected runtime path`);
  }
  return patterns;
}

export function validateSnapshot(value, frozen, round) {
  const common = value && typeof value === 'object' && !Array.isArray(value)
    && value.task === frozen.task && value.round === round
    && value.frozenFingerprint === frozen.frozenFingerprint;
  if (!common) throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot does not match the frozen task and round');
  if (value.schemaVersion === 1) {
    if (!exactKeys(value, ['schemaVersion', 'task', 'round', 'baseCommit', 'frozenFingerprint', 'allowedPaths', 'preExistingChanges'])
        || !SHA.test(value.baseCommit)) {
      throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot schema v1 is invalid');
    }
    const allowedPaths = validateAllowedPaths(value.allowedPaths);
    const preExistingChanges = validatePreExisting(value.preExistingChanges);
    if (frozen.mode === 'light' && allowedPaths.some((pattern) => pattern.includes('*') || !frozen.scope.includes(pattern))) {
      throw new GatedLoopError('SNAPSHOT_INVALID', 'Light snapshot paths must exactly match frozen scope files');
    }
    return { ...value, allowedPaths, preExistingChanges };
  }
  if (value.schemaVersion !== 2 || frozen.mode !== 'full'
      || !exactKeys(value, ['schemaVersion', 'task', 'round', 'frozenFingerprint', 'workspaces'])
      || !Array.isArray(value.workspaces) || value.workspaces.length < 2) {
    throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot schema v2 requires a Full task and at least two workspaces');
  }
  const taskIds = new Set(frozen.tasks.map((entry) => entry.id));
  const workspaces = value.workspaces.map((entry) => {
    const valid = exactKeys(entry, ['id', 'root', 'branch', 'baseCommit', 'taskIds', 'allowedPaths', 'preExistingChanges'])
      && typeof entry.id === 'string' && /^[a-z][a-z0-9._-]{0,63}$/.test(entry.id)
      && typeof entry.root === 'string' && path.isAbsolute(entry.root) && !/[\u0000-\u001f\u007f]/.test(entry.root)
      && typeof entry.branch === 'string' && entry.branch.length > 0 && entry.branch.length <= 256
      && !/[\u0000-\u001f\u007f]/.test(entry.branch)
      && SHA.test(entry.baseCommit);
    if (!valid) throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot contains an invalid workspace');
    return {
      ...entry,
      root: path.resolve(entry.root),
      taskIds: validateStringIds(entry.taskIds, taskIds, 'workspace task IDs'),
      allowedPaths: validateAllowedPaths(entry.allowedPaths, 'workspace allowed paths'),
      preExistingChanges: validatePreExisting(entry.preExistingChanges),
    };
  });
  const rootKeys = workspaces.map((entry) => process.platform === 'win32' ? entry.root.toLowerCase() : entry.root);
  if (new Set(workspaces.map((entry) => entry.id)).size !== workspaces.length
      || new Set(rootKeys).size !== workspaces.length) {
    throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot repeats a workspace ID or root');
  }
  return { ...value, workspaces };
}

export async function readSnapshot({ root, task, round, source, frozen, fs = fsPromises } = {}) {
  const relative = source ?? path.join('.ai-dev-loop', task, 'rounds', round, 'development-snapshot.json');
  let value;
  try { value = JSON.parse((await readSafeRegularFile(root, relative, { fs })).toString('utf8')); }
  catch (error) {
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError('SNAPSHOT_READ', 'Unable to read development snapshot');
  }
  return validateSnapshot(value, frozen, round);
}

async function readRoundJson(root, task, round, name, fs) {
  try {
    return JSON.parse((await readSafeRegularFile(root, path.join('.ai-dev-loop', task, 'rounds', round, name), { fs })).toString('utf8'));
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError('WORKSPACE_GATE_READ', `Unable to read ${name}`);
  }
}

function normalizeTestCommands(value, workspaceRoot) {
  if (!Array.isArray(value) || value.length === 0) {
    throw new GatedLoopError('WORKSPACE_AUTHORIZATION_INVALID', 'Every workspace must define at least one test command');
  }
  return value.map((entry) => {
    const argv = normalizeTestArgv(entry?.argv);
    if (!exactKeys(entry, ['cwd', 'argv']) || typeof entry.cwd !== 'string' || !path.isAbsolute(entry.cwd) || !argv) {
      throw new GatedLoopError('WORKSPACE_AUTHORIZATION_INVALID', 'Workspace test command is invalid');
    }
    const cwd = path.resolve(entry.cwd);
    const relative = path.relative(workspaceRoot, cwd);
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new GatedLoopError('WORKSPACE_AUTHORIZATION_INVALID', 'Workspace test cwd escapes its authorized root');
    }
    return { cwd, argv };
  });
}

function topologicalWorkspacePlan(coverage, workspaces) {
  const byId = new Map(workspaces.map((entry) => [entry.id, entry]));
  const taskToWorkspaces = new Map();
  for (const workspace of workspaces) for (const taskId of workspace.taskIds) {
    const ids = taskToWorkspaces.get(taskId) ?? [];
    ids.push(workspace.id); taskToWorkspaces.set(taskId, ids);
  }
  const dependencies = new Map(workspaces.map((entry) => [entry.id, new Set()]));
  const taskDependencies = new Map(coverage.taskCoverage.map((entry) => [entry.taskId, entry.dependsOn]));
  const visiting = new Set(); const visited = new Set();
  const visitTask = (taskId) => {
    if (visiting.has(taskId)) throw new GatedLoopError('WORKSPACE_DEPENDENCY_CYCLE', 'Workspace task dependency graph contains a cycle');
    if (visited.has(taskId)) return;
    visiting.add(taskId);
    for (const dependency of taskDependencies.get(taskId) ?? []) visitTask(dependency);
    visiting.delete(taskId); visited.add(taskId);
  };
  for (const taskId of taskDependencies.keys()) visitTask(taskId);
  for (const entry of coverage.taskCoverage) {
    for (const dependency of entry.dependsOn) {
      for (const target of entry.workspaceIds) for (const source of taskToWorkspaces.get(dependency) ?? []) {
        if (source !== target) dependencies.get(target).add(source);
      }
    }
  }
  const waves = new Map(); const resolving = new Set();
  const wave = (id) => {
    if (waves.has(id)) return waves.get(id);
    if (resolving.has(id)) throw new GatedLoopError('WORKSPACE_DEPENDENCY_CYCLE', 'Workspace dependency graph contains a cycle');
    resolving.add(id);
    const value = 1 + Math.max(0, ...[...dependencies.get(id)].map(wave));
    resolving.delete(id); waves.set(id, value); return value;
  };
  for (const id of byId.keys()) wave(id);
  return workspaces.map((entry) => ({
    ...entry,
    dependsOnWorkspaceIds: [...dependencies.get(entry.id)].sort(),
    wave: waves.get(entry.id),
  })).sort((left, right) => left.wave - right.wave || left.id.localeCompare(right.id));
}

export async function loadWorkspacePlan({ root, task, round, snapshot, frozen, fs = fsPromises } = {}) {
  if (snapshot.schemaVersion === 1) return [{
    id: 'coordinator', root: path.resolve(root), branch: null, baseCommit: snapshot.baseCommit,
    taskIds: frozen.tasks.map((entry) => entry.id), allowedPaths: snapshot.allowedPaths,
    preExistingChanges: snapshot.preExistingChanges,
    testCommands: frozen.testCommands.map((argv) => ({ cwd: path.resolve(root), argv })),
    dependsOnWorkspaceIds: [], wave: 1, coordinator: true,
  }];
  const authorization = await readRoundJson(root, task, round, 'workspace-authorization.json', fs);
  const coverage = await readRoundJson(root, task, round, 'workspace-coverage.json', fs);
  const authorizationValid = exactKeys(authorization, ['schemaVersion', 'task', 'round', 'coordinatorWorkspaceId', 'status', 'confirmedBy', 'workspaces'])
    && authorization.schemaVersion === 1 && authorization.task === task && authorization.round === round
    && authorization.status === 'CONFIRMED' && authorization.confirmedBy === 'user'
    && typeof authorization.coordinatorWorkspaceId === 'string' && Array.isArray(authorization.workspaces);
  if (!authorizationValid) throw new GatedLoopError('WORKSPACE_AUTHORIZATION_INVALID', 'Workspace authorization is invalid or not user-confirmed');
  const frozenTaskIds = new Set(frozen.tasks.map((entry) => entry.id));
  const snapshotById = new Map(snapshot.workspaces.map((entry) => [entry.id, entry]));
  const workspaces = authorization.workspaces.map((entry) => {
    const snapshotEntry = snapshotById.get(entry?.id);
    const rootPath = typeof entry?.root === 'string' && path.isAbsolute(entry.root) ? path.resolve(entry.root) : null;
    const valid = exactKeys(entry, ['id', 'root', 'access', 'taskIds', 'allowedPaths', 'testCommands'])
      && snapshotEntry && rootPath && entry.access === 'read-write';
    if (!valid) throw new GatedLoopError('WORKSPACE_AUTHORIZATION_INVALID', 'Workspace authorization entry is invalid');
    const taskIds = validateStringIds(entry.taskIds, frozenTaskIds, 'authorized task IDs');
    const allowedPaths = validateAllowedPaths(entry.allowedPaths, 'authorized allowed paths');
    if (!sameAbsolutePath(rootPath, snapshotEntry.root) || !sameStringSet(taskIds, snapshotEntry.taskIds)
        || !sameStringSet(allowedPaths, snapshotEntry.allowedPaths)) {
      throw new GatedLoopError('WORKSPACE_AUTHORIZATION_MISMATCH', 'Workspace authorization does not match the development snapshot');
    }
    return { ...snapshotEntry, testCommands: normalizeTestCommands(entry.testCommands, rootPath) };
  });
  if (workspaces.length !== snapshot.workspaces.length || new Set(workspaces.map((entry) => entry.id)).size !== workspaces.length
      || !snapshotById.has(authorization.coordinatorWorkspaceId)) {
    throw new GatedLoopError('WORKSPACE_AUTHORIZATION_MISMATCH', 'Workspace authorization does not cover every snapshot workspace');
  }
  const coordinator = workspaces.find((entry) => entry.id === authorization.coordinatorWorkspaceId);
  if (!sameAbsolutePath(coordinator.root, root)) {
    throw new GatedLoopError('WORKSPACE_AUTHORIZATION_MISMATCH', 'Coordinator workspace root does not match the CLI project root');
  }
  const authorizedCommands = workspaces.flatMap((entry) => entry.testCommands.map(({ argv }) => JSON.stringify(argv))).sort();
  const frozenCommands = frozen.testCommands.map((argv) => JSON.stringify(argv)).sort();
  if (!sameStringSet(authorizedCommands, frozenCommands)) {
    throw new GatedLoopError('WORKSPACE_TEST_COMMAND_MISMATCH', 'Workspace test commands must exactly partition the frozen test commands');
  }
  const coverageValid = exactKeys(coverage, ['schemaVersion', 'task', 'round', 'status', 'taskCoverage', 'missing'])
    && coverage.schemaVersion === 1 && coverage.task === task && coverage.round === round
    && coverage.status === 'PASS' && Array.isArray(coverage.taskCoverage) && Array.isArray(coverage.missing)
    && coverage.missing.length === 0;
  if (!coverageValid) throw new GatedLoopError('WORKSPACE_COVERAGE_INVALID', 'Workspace coverage is not PASS');
  const workspaceIds = new Set(workspaces.map((entry) => entry.id));
  const taskCoverage = coverage.taskCoverage.map((entry) => {
    const valid = exactKeys(entry, ['taskId', 'workspaceIds', 'dependsOn', 'status'])
      && frozenTaskIds.has(entry?.taskId) && entry.status === 'COVERED';
    if (!valid) throw new GatedLoopError('WORKSPACE_COVERAGE_INVALID', 'Workspace task coverage entry is invalid');
    const coveredIds = validateStringIds(entry.workspaceIds, workspaceIds, 'covered workspace IDs');
    const dependsOn = validateStringIds(entry.dependsOn, frozenTaskIds, 'task dependencies', { nonempty: false });
    if (dependsOn.includes(entry.taskId)) throw new GatedLoopError('WORKSPACE_DEPENDENCY_CYCLE', 'A task cannot depend on itself');
    const authorizedIds = workspaces.filter((workspace) => workspace.taskIds.includes(entry.taskId)).map((workspace) => workspace.id);
    if (!sameStringSet(coveredIds, authorizedIds)) {
      throw new GatedLoopError('WORKSPACE_COVERAGE_INVALID', 'Task coverage does not match workspace authorization');
    }
    return { ...entry, workspaceIds: coveredIds, dependsOn };
  });
  if (taskCoverage.length !== frozenTaskIds.size
      || new Set(taskCoverage.map((entry) => entry.taskId)).size !== frozenTaskIds.size) {
    throw new GatedLoopError('WORKSPACE_COVERAGE_INVALID', 'Workspace coverage must include every frozen task exactly once');
  }
  for (const workspace of workspaces) {
    await assertSafePath(workspace.root, workspace.root, { fs });
    const stat = await fs.lstat(workspace.root);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new GatedLoopError('WORKSPACE_ROOT_INVALID', 'Workspace root must be a real directory');
    for (const command of workspace.testCommands) {
      await assertSafePath(workspace.root, command.cwd, { fs });
      const cwdStat = await fs.lstat(command.cwd);
      if (!cwdStat.isDirectory() || cwdStat.isSymbolicLink()) throw new GatedLoopError('WORKSPACE_TEST_CWD_INVALID', 'Workspace test cwd must be a real directory');
    }
  }
  const planned = topologicalWorkspacePlan({ ...coverage, taskCoverage }, workspaces);
  return planned.map((entry) => ({ ...entry, coordinator: entry.id === authorization.coordinatorWorkspaceId }));
}

function globRegex(pattern) {
  let source = '^';
  for (let index = 0; index < pattern.length; index++) {
    const character = pattern[index];
    if (character === '*' && pattern[index + 1] === '*') { source += '.*'; index++; }
    else if (character === '*') source += '[^/]*';
    else if (character === '?') source += '[^/]';
    else source += character.replace(/[|\\{}()[\]^$+?.]/g, '\\$&');
  }
  return new RegExp(`${source}$`);
}

export function matchesAny(filePath, patterns) {
  return patterns.some((pattern) => globRegex(pattern).test(filePath));
}

export function parseGitStatus(output) {
  const tokens = output.split('\0');
  const entries = [];
  for (let index = 0; index < tokens.length; index++) {
    const token = tokens[index];
    if (!token) continue;
    if (token.length < 4 || token[2] !== ' ') throw new GatedLoopError('GIT_STATUS_INVALID', 'Git status output is malformed');
    const statusCode = token.slice(0, 2);
    const filePath = canonicalRelativePath(token.slice(3));
    entries.push({ path: filePath, statusCode });
    if (/[RC]/.test(statusCode)) {
      const original = tokens[++index];
      if (!original) throw new GatedLoopError('GIT_STATUS_INVALID', 'Git rename status is incomplete');
      entries.push({ path: canonicalRelativePath(original), statusCode: 'D ' });
    }
  }
  return entries.sort((left, right) => left.path.localeCompare(right.path));
}

export async function gitOutput(root, git, args, { runProcessImpl = runProcess, timeoutMs = 30_000 } = {}) {
  return runProcessImpl(git, args, { cwd: root, timeoutMs, captureOutput: true });
}

export async function currentStatus({ root, git = 'git', runProcessImpl, timeoutMs } = {}) {
  const topLevel = (await gitOutput(root, git, ['rev-parse', '--show-toplevel'], { runProcessImpl, timeoutMs })).stdout.trim();
  if (!topLevel || !path.isAbsolute(topLevel) || !sameAbsolutePath(topLevel, root)) {
    throw new GatedLoopError('GIT_ROOT_MISMATCH', 'Workspace root must be the Git worktree root');
  }
  const head = (await gitOutput(root, git, ['rev-parse', 'HEAD'], { runProcessImpl, timeoutMs })).stdout.trim();
  if (!SHA.test(head)) throw new GatedLoopError('GIT_HEAD_INVALID', 'Git HEAD is invalid');
  const branch = (await gitOutput(root, git, ['rev-parse', '--abbrev-ref', 'HEAD'], { runProcessImpl, timeoutMs })).stdout.trim();
  if (!branch || /[\u0000-\u001f\u007f]/.test(branch)) {
    throw new GatedLoopError('GIT_BRANCH_INVALID', 'Git branch is invalid');
  }
  const statusResult = await gitOutput(root, git, ['status', '--porcelain=v1', '-z', '--untracked-files=all'], { runProcessImpl, timeoutMs });
  if (statusResult.stdoutTruncated) throw new GatedLoopError('GIT_STATUS_TRUNCATED', 'Git status is too large to attribute safely');
  return { topLevel: path.resolve(topLevel), head, branch, entries: parseGitStatus(statusResult.stdout) };
}

export async function worktreeHash(root, filePath, { fs = fsPromises } = {}) {
  try { return sha256Bytes(await readSafeRegularFile(root, filePath, { fs })); }
  catch (error) {
    if (error.code === 'ENOENT') return null;
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError('WORKTREE_READ_FAILED', `Unable to read changed file: ${filePath}`);
  }
}

export async function enrichStatus(root, entries, { fs = fsPromises, skipPatterns = [], skipPaths = [] } = {}) {
  const skipped = new Set(skipPaths);
  return Promise.all(entries.map(async (entry) => ({
    ...entry,
    worktreeSha256: skipped.has(entry.path) || matchesAny(entry.path, skipPatterns) ? '[NOT_READ]' : await worktreeHash(root, entry.path, { fs }),
  })));
}

export function attributeChanges(current, snapshot) {
  const previous = new Map(snapshot.preExistingChanges.map((entry) => [entry.path, entry]));
  const currentByPath = new Map(current.map((entry) => [entry.path, entry]));
  const ambiguous = [];
  const unchangedPreExisting = [];
  for (const entry of snapshot.preExistingChanges) {
    const now = currentByPath.get(entry.path);
    if (now && now.statusCode === entry.statusCode && now.worktreeSha256 === entry.worktreeSha256) unchangedPreExisting.push(entry.path);
    else ambiguous.push(entry.path);
  }
  const changed = current.filter((entry) => !previous.has(entry.path));
  return { changed, ambiguous, unchangedPreExisting };
}

export function testCounts(output) {
  const value = { passed: null, failed: null, errors: null, skipped: null };
  const tap = { passed: /# pass\s+(\d+)/i, failed: /# fail\s+(\d+)/i, skipped: /# skipped\s+(\d+)/i };
  for (const [key, pattern] of Object.entries(tap)) {
    const match = pattern.exec(output); if (match) value[key] = Number(match[1]);
  }
  const pytest = /(\d+) passed/.exec(output); if (pytest) value.passed = Number(pytest[1]);
  const pytestFailed = /(\d+) failed/.exec(output); if (pytestFailed) value.failed = Number(pytestFailed[1]);
  const pytestErrors = /(\d+) errors?/.exec(output); if (pytestErrors) value.errors = Number(pytestErrors[1]);
  const pytestSkipped = /(\d+) skipped/.exec(output); if (pytestSkipped) value.skipped = Number(pytestSkipped[1]);
  return value;
}

export async function buildDiffBundle({ root, git = 'git', changed, runProcessImpl, timeoutMs = 30_000, fs = fsPromises } = {}) {
  const paths = [...new Set(changed.map((entry) => entry.path))].sort();
  const tracked = changed.filter((entry) => entry.statusCode !== '??').map((entry) => entry.path);
  let diff = '';
  let truncated = false;
  if (tracked.length > 0) {
    const result = await gitOutput(root, git, ['diff', '--no-ext-diff', '--unified=40', 'HEAD', '--', ...tracked], { runProcessImpl, timeoutMs });
    diff = result.stdout; truncated = result.stdoutTruncated;
  }
  const untracked = [];
  let untrackedBytes = 0;
  for (const entry of changed.filter((item) => item.statusCode === '??')) {
    const bytes = await readSafeRegularFile(root, entry.path, { fs });
    untrackedBytes += bytes.length;
    if (untrackedBytes > 64 * 1024) { truncated = true; break; }
    untracked.push({ path: entry.path, content: bytes.toString('utf8') });
  }
  const text = [
    '# Changed paths', ...paths.map((filePath) => `- ${filePath}`), '',
    '# Tracked diff', diff, '', '# Untracked files',
    ...untracked.flatMap((entry) => [`## ${entry.path}`, entry.content, '']),
  ].join('\n');
  return { paths, text, truncated, sha256: sha256Bytes(Buffer.from(text, 'utf8')) };
}

function sameAbsolutePath(left, right) {
  const normalizedLeft = path.resolve(left); const normalizedRight = path.resolve(right);
  return process.platform === 'win32'
    ? normalizedLeft.toLowerCase() === normalizedRight.toLowerCase()
    : normalizedLeft === normalizedRight;
}

export async function inspectWorkspace({
  coordinatorRoot, task, workspace, git = 'git', protectedPaths = [], forbiddenPaths = [],
  isPolicyForbidden = () => false, runProcessImpl, timeoutMs = 30_000, fs = fsPromises,
} = {}) {
  const repository = await currentStatus({ root: workspace.root, git, runProcessImpl, timeoutMs });
  const runtimePrefix = `.ai-dev-loop/${task}/`;
  const relevant = repository.entries.filter((entry) => !(workspace.coordinator
    && sameAbsolutePath(workspace.root, coordinatorRoot) && entry.path.startsWith(runtimePrefix)));
  const protectedChanged = relevant.filter((entry) => matchesAny(entry.path, protectedPaths));
  const forbiddenChanged = relevant.filter((entry) => matchesAny(entry.path, forbiddenPaths) || isPolicyForbidden(entry.path));
  const enriched = await enrichStatus(workspace.root, relevant, {
    fs, skipPatterns: forbiddenPaths, skipPaths: forbiddenChanged.map((entry) => entry.path),
  });
  const attributed = attributeChanges(enriched, workspace);
  const outOfScope = attributed.changed.filter((entry) => !matchesAny(entry.path, workspace.allowedPaths));
  const forbiddenSet = new Set(forbiddenChanged.map((entry) => entry.path));
  const safeChanged = attributed.changed.filter((entry) => !forbiddenSet.has(entry.path));
  const diffBundle = await buildDiffBundle({
    root: workspace.root, git, changed: safeChanged, runProcessImpl, timeoutMs, fs,
  });
  return {
    workspace, repository, relevant, protectedChanged, forbiddenChanged, outOfScope,
    changed: attributed.changed, ambiguous: attributed.ambiguous,
    unchangedPreExisting: attributed.unchangedPreExisting, diffBundle,
  };
}

export function aggregateDiffBundles(inspections) {
  const ordered = [...inspections].sort((left, right) => left.workspace.id.localeCompare(right.workspace.id));
  const text = ordered.map((entry) => `# Workspace ${entry.workspace.id}\n${entry.diffBundle.text}`).join('\n\n');
  return {
    text,
    truncated: ordered.some((entry) => entry.diffBundle.truncated),
    sha256: sha256Bytes(Buffer.from(text, 'utf8')),
    workspaces: ordered.map((entry) => ({ workspaceId: entry.workspace.id, sha256: entry.diffBundle.sha256 })),
  };
}

export async function roundDirectory({ root, task, round, fs = fsPromises } = {}) {
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task, 'rounds', round), { fs });
  await fs.mkdir(target, { recursive: true });
  await assertSafePath(root, target, { fs });
  const stat = await fs.lstat(target);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new GatedLoopError('ROUND_DIRECTORY_INVALID', 'Round directory is invalid');
  return target;
}

export async function writeRoundFile(directory, name, content, { fs = fsPromises } = {}) {
  await atomicWriteFile(path.join(directory, name), content, { fs });
  return path.join(directory, name);
}
