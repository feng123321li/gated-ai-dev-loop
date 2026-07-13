import * as fsPromises from 'node:fs/promises';
import path from 'node:path';

import { GatedLoopError } from '../core/errors.mjs';
import { assertSafePath, atomicReplaceDirectory, atomicWriteFile, readSafeRegularFile, resolveAtomicDirectory } from '../core/fs-safe.mjs';
import { sha256Bytes } from '../core/hash.mjs';
import { CLASSIFIER_VERSION, classifyMode } from '../mode/classify.mjs';
import { canonicalJson } from '../baseline/sources.mjs';
import { renderDevelopmentHandoff } from '../handoff/render.mjs';
import { requireHostRuntime } from '../mode/host-runtime.mjs';

export const WAITING_FILES = Object.freeze([
  'acceptance.json', 'baseline.md', 'decision-log.md', 'mode.json',
  'source-manifest.json', 'state.json', 'tasks.json',
]);
export const FROZEN_FILES = Object.freeze([...WAITING_FILES, 'handoff-to-claude.md'].sort());
export const DEFAULT_DECISION_LOG = '# Decision Log\n\nNo additional decisions recorded.\n';

export function json(value) { return `${JSON.stringify(value, null, 2)}\n`; }

export function timestamp(now) {
  const value = typeof now === 'function' ? now() : new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError('BASELINE_TIMESTAMP_INVALID', 'Baseline timestamp is invalid');
  return date.toISOString();
}

export function validateTask(task) {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof task !== 'string' || !/^[a-z0-9][a-z0-9._-]*$/.test(task) || task.endsWith('.') || reserved.test(task)) {
    throw new GatedLoopError('BASELINE_TASK_INVALID', 'Task must be a safe single path segment');
  }
}

function same(left, right) { return canonicalJson(left) === canonicalJson(right); }

function canonicalTimestamp(value) {
  if (typeof value !== 'string') return false;
  const date = new Date(value);
  return !Number.isNaN(date.valueOf()) && date.toISOString() === value;
}

function frozenMetadata(state) {
  return {
    schemaVersion: state.schemaVersion,
    task: state.task,
    mode: state.mode,
    stage: state.stage,
    hostRuntime: state.hostRuntime,
    reviewer: state.reviewer,
    sourceFingerprint: state.sourceFingerprint,
    baselineFingerprint: state.baselineFingerprint,
    inputFingerprint: state.inputFingerprint,
    updatedAt: state.updatedAt,
  };
}

export function frozenStateFingerprint(state, artifactHashSet = state.artifactHashes) {
  return sha256Bytes(Buffer.from(canonicalJson({
    artifactHashes: artifactHashSet,
    state: frozenMetadata(state),
  }), 'utf8'));
}

export function renderFullHandoff(model, task, hostRuntime) {
  return renderDevelopmentHandoff({
    task,
    reviewer: hostRuntime,
    authorityFile: 'baseline.md',
    scope: model.scope,
    tasks: model.tasks,
    acceptance: model.acceptance,
    testCommands: model.testCommands,
  });
}

const FROZEN_HASH_NAMES = Object.freeze([
  'acceptance.json', 'baseline.md', 'decision-log.md', 'handoff-to-claude.md',
  'mode.json', 'source-manifest.json', 'tasks.json',
]);

export function deriveFrozenPackage(taskPackage, handoff) {
  const files = {
    'mode.json': taskPackage.modeBytes,
    'baseline.md': taskPackage.bytes['baseline.md'],
    'acceptance.json': taskPackage.bytes['acceptance.json'],
    'tasks.json': taskPackage.bytes['tasks.json'],
    'source-manifest.json': taskPackage.bytes['source-manifest.json'],
    'decision-log.md': taskPackage.bytes['decision-log.md'],
    'handoff-to-claude.md': handoff,
  };
  return { files, hashes: artifactHashes(files, FROZEN_HASH_NAMES) };
}

export function validateFrozenPackage(taskPackage, handoff) {
  const { hashes } = deriveFrozenPackage(taskPackage, handoff);
  const valid = taskPackage.bytes['handoff-to-claude.md'].toString('utf8') === handoff
    && canonicalJson(taskPackage.state.artifactHashes) === canonicalJson(hashes)
    && taskPackage.state.frozenFingerprint === frozenStateFingerprint(taskPackage.state, hashes);
  if (!valid) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Frozen handoff or state metadata changed');
}

function validateMode(mode) {
  const modeKeys = ['classifierVersion', 'mode', 'reasons', 'confidence', 'evaluatedInputs', 'hostRuntime', 'createdAt'];
  const valid = mode && typeof mode === 'object' && !Array.isArray(mode)
    && same(Object.keys(mode).sort(), modeKeys.sort())
    && mode.classifierVersion === CLASSIFIER_VERSION && mode.mode === 'full'
    && Array.isArray(mode.reasons) && ['high', 'medium'].includes(mode.confidence)
    && mode.evaluatedInputs && typeof mode.evaluatedInputs === 'object' && canonicalTimestamp(mode.createdAt);
  if (!valid) throw new GatedLoopError('FULL_MODE_REQUIRED', 'A persisted Full mode is required');
  let replay;
  try { replay = classifyMode(mode.evaluatedInputs); }
  catch { throw new GatedLoopError('FULL_MODE_REQUIRED', 'Persisted Full mode is invalid'); }
  if (replay.mode !== 'full' || !same(replay.reasons, mode.reasons) || replay.confidence !== mode.confidence
      || !same(replay.evaluatedInputs, mode.evaluatedInputs)) {
    throw new GatedLoopError('FULL_MODE_REQUIRED', 'Persisted Full mode does not match its inputs');
  }
  try { return requireHostRuntime(mode.hostRuntime); }
  catch { throw new GatedLoopError('FULL_MODE_REQUIRED', 'Persisted host runtime is invalid'); }
}

function parseJson(bytes, name) {
  try { return JSON.parse(bytes.toString('utf8')); }
  catch { throw new GatedLoopError('BASELINE_SOURCE_CHANGED', `Existing ${name} is invalid`); }
}

async function readBytes(target, name, fs) {
  try { return await readSafeRegularFile(target, name, { fs }); }
  catch (error) {
    if (error instanceof GatedLoopError) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', `Existing artifact changed: ${name}`);
    throw error;
  }
}

function validateState(state, task, hostRuntime) {
  const validStage = ['WAITING_FOR_BASELINE_CONFIRMATION', 'BASELINE_FROZEN'].includes(state?.stage);
  const stateKeys = [
    'schemaVersion', 'task', 'mode', 'stage', 'hostRuntime', 'reviewer', 'sourceFingerprint',
    'baselineFingerprint', 'inputFingerprint', 'artifactHashes', 'updatedAt',
  ];
  if (state?.stage === 'BASELINE_FROZEN') stateKeys.push('frozenFingerprint');
  const valid = state && typeof state === 'object' && !Array.isArray(state)
    && same(Object.keys(state).sort(), stateKeys.sort())
    && state.schemaVersion === 1 && state.task === task && state.mode === 'full' && validStage
    && state.hostRuntime === hostRuntime && state.reviewer === hostRuntime
    && typeof state.sourceFingerprint === 'string' && /^[a-f0-9]{64}$/.test(state.sourceFingerprint)
    && typeof state.baselineFingerprint === 'string' && /^[a-f0-9]{64}$/.test(state.baselineFingerprint)
    && typeof state.inputFingerprint === 'string' && /^[a-f0-9]{64}$/.test(state.inputFingerprint)
    && state.artifactHashes && typeof state.artifactHashes === 'object' && !Array.isArray(state.artifactHashes)
    && canonicalTimestamp(state.updatedAt);
  if (!valid) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Existing baseline state is invalid');
  const expectedInputFingerprint = sha256Bytes(Buffer.from(canonicalJson({
    task,
    hostRuntime,
    sourceFingerprint: state.sourceFingerprint,
    baselineFingerprint: state.baselineFingerprint,
    modeSha256: state.artifactHashes['mode.json'],
  }), 'utf8'));
  if (state.inputFingerprint !== expectedInputFingerprint) {
    throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Existing baseline input fingerprint is invalid');
  }
  if (state.stage === 'BASELINE_FROZEN'
      && state.frozenFingerprint !== frozenStateFingerprint(state)) {
    throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Existing frozen fingerprint is invalid');
  }
}

function validateHashes(state, bytes) {
  const expectedNames = state.stage === 'BASELINE_FROZEN'
    ? ['acceptance.json', 'baseline.md', 'decision-log.md', 'handoff-to-claude.md', 'mode.json', 'source-manifest.json', 'tasks.json']
    : ['acceptance.json', 'baseline.md', 'mode.json', 'source-manifest.json', 'tasks.json'];
  if (!same(Object.keys(state.artifactHashes).sort(), [...expectedNames].sort())) {
    throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Existing artifact hash set is invalid');
  }
  for (const name of expectedNames) {
    if (!bytes[name] || state.artifactHashes[name] !== sha256Bytes(bytes[name])) {
      throw new GatedLoopError('BASELINE_SOURCE_CHANGED', `Existing artifact changed: ${name}`);
    }
  }
}

export async function readFullPackage({ root, task, fs = fsPromises } = {}) {
  if (typeof root !== 'string' || root.length === 0) throw new GatedLoopError('BASELINE_ROOT_INVALID', 'Project root is required');
  validateTask(task);
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task), { fs });
  let readableTarget;
  let stat;
  try {
    readableTarget = await resolveAtomicDirectory(target, { fs });
    stat = await fs.lstat(readableTarget);
  }
  catch (error) {
    if (error.code === 'ENOENT') throw new GatedLoopError('FULL_MODE_REQUIRED', 'Start must persist Full mode before prepare');
    throw error;
  }
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Task artifact path is invalid');
  const names = (await fs.readdir(readableTarget)).sort();
  if (!names.includes('mode.json')) throw new GatedLoopError('FULL_MODE_REQUIRED', 'A persisted Full mode is required');
  const modeBytes = await readBytes(readableTarget, 'mode.json', fs);
  const mode = parseJson(modeBytes, 'mode.json');
  const hostRuntime = validateMode(mode);
  if (names.length === 1 && names[0] === 'mode.json') {
    return { target, stage: null, mode, modeBytes, hostRuntime, bytes: { 'mode.json': modeBytes } };
  }
  const stateBytes = await readBytes(readableTarget, 'state.json', fs);
  const state = parseJson(stateBytes, 'state.json');
  const expected = state?.stage === 'BASELINE_FROZEN' ? FROZEN_FILES : WAITING_FILES;
  if (!same(names, expected)) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Existing baseline package is incomplete or has unexpected files');
  const bytes = { 'state.json': stateBytes, 'mode.json': modeBytes };
  for (const name of names) if (name !== 'state.json' && name !== 'mode.json') bytes[name] = await readBytes(readableTarget, name, fs);
  validateState(state, task, hostRuntime);
  validateHashes(state, bytes);
  return {
    target,
    stage: state.stage,
    mode,
    modeBytes: bytes['mode.json'],
    hostRuntime,
    state,
    bytes,
    manifest: parseJson(bytes['source-manifest.json'], 'source-manifest.json'),
    acceptance: parseJson(bytes['acceptance.json'], 'acceptance.json'),
    tasks: parseJson(bytes['tasks.json'], 'tasks.json'),
  };
}

export function artifactHashes(files, names) {
  return Object.fromEntries([...names].sort().map((name) => [name, sha256Bytes(Buffer.isBuffer(files[name]) ? files[name] : Buffer.from(files[name], 'utf8'))]));
}

export async function replaceFullPackage(target, files, { fs = fsPromises, beforeCommit } = {}) {
  await atomicReplaceDirectory(target, async (staging) => {
    for (const name of Object.keys(files).sort()) {
      await atomicWriteFile(path.join(staging, name), files[name], { fs });
    }
  }, { fs, validateUnderLock: beforeCommit });
}

export function fullOutcome(taskPackage, { created = false, updated = false } = {}) {
  return {
    created,
    updated,
    idempotent: !created && !updated,
    task: taskPackage.state?.task,
    mode: 'full',
    stage: taskPackage.state?.stage,
    artifactDir: taskPackage.target,
    artifacts: (taskPackage.state?.stage === 'BASELINE_FROZEN' ? FROZEN_FILES : WAITING_FILES)
      .map((name) => path.join(taskPackage.target, name)),
    sourceFingerprint: taskPackage.state?.sourceFingerprint,
    baselineFingerprint: taskPackage.state?.baselineFingerprint,
    hostRuntime: taskPackage.hostRuntime,
    reviewer: taskPackage.hostRuntime,
  };
}
