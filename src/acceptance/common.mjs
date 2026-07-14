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

export function validateSnapshot(value, frozen, round) {
  const valid = value && typeof value === 'object' && !Array.isArray(value)
    && value.schemaVersion === 1 && value.task === frozen.task && value.round === round
    && SHA.test(value.baseCommit) && value.frozenFingerprint === frozen.frozenFingerprint
    && Array.isArray(value.allowedPaths) && value.allowedPaths.length > 0
    && Array.isArray(value.preExistingChanges);
  if (!valid) throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot does not match the frozen task and round');
  const allowedPaths = value.allowedPaths.map(safePattern);
  if (allowedPaths.includes(null) || new Set(allowedPaths).size !== allowedPaths.length) {
    throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot contains unsafe or duplicate allowed paths');
  }
  const preExistingChanges = value.preExistingChanges.map((entry) => {
    const filePath = safePattern(entry?.path);
    const hashValid = entry?.worktreeSha256 === null || SHA256.test(entry?.worktreeSha256);
    if (!filePath || typeof entry.statusCode !== 'string' || entry.statusCode.length !== 2 || !hashValid) {
      throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot contains an invalid pre-existing change');
    }
    return { path: filePath, statusCode: entry.statusCode, worktreeSha256: entry.worktreeSha256 };
  });
  if (new Set(preExistingChanges.map((entry) => entry.path)).size !== preExistingChanges.length) {
    throw new GatedLoopError('SNAPSHOT_INVALID', 'Development snapshot repeats a pre-existing path');
  }
  if (frozen.mode === 'light' && allowedPaths.some((pattern) => pattern.includes('*') || !frozen.scope.includes(pattern))) {
    throw new GatedLoopError('SNAPSHOT_INVALID', 'Light snapshot paths must exactly match frozen scope files');
  }
  return { ...value, allowedPaths, preExistingChanges };
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
  const head = (await gitOutput(root, git, ['rev-parse', 'HEAD'], { runProcessImpl, timeoutMs })).stdout.trim();
  if (!SHA.test(head)) throw new GatedLoopError('GIT_HEAD_INVALID', 'Git HEAD is invalid');
  const statusResult = await gitOutput(root, git, ['status', '--porcelain=v1', '-z', '--untracked-files=all'], { runProcessImpl, timeoutMs });
  if (statusResult.stdoutTruncated) throw new GatedLoopError('GIT_STATUS_TRUNCATED', 'Git status is too large to attribute safely');
  return { head, entries: parseGitStatus(statusResult.stdout) };
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
