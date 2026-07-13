import * as fsPromises from 'node:fs/promises';
import path from 'node:path';

import { GatedLoopError } from '../core/errors.mjs';
import {
  assertSafePath,
  atomicWriteDirectory,
  atomicWriteFile,
  readSafeRegularFile,
  withRuntimeDirectoryTransaction,
} from '../core/fs-safe.mjs';
import { manifestFingerprint, sha256Bytes } from '../core/hash.mjs';
import { CLASSIFIER_VERSION, classifyMode } from '../mode/classify.mjs';
import { normalizeHostRuntime, requireHostRuntime } from '../mode/host-runtime.mjs';
import { buildLightArtifacts } from './artifacts.mjs';
import { buildLightBrief, validateLightBrief } from './build-brief.mjs';

const STATE_SCHEMA_VERSION = 1;
const ARTIFACT_NAMES = Object.freeze([
  'acceptance.json', 'decision-log.md', 'handoff-to-claude.md', 'light-brief.md',
  'mode.json', 'source-manifest.json', 'state.json', 'tasks.json',
]);
const HASHED_ARTIFACT_NAMES = Object.freeze(ARTIFACT_NAMES.filter((name) => name !== 'state.json'));

function json(value) { return `${JSON.stringify(value, null, 2)}\n`; }

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function fingerprintInput(classification, markdown, hostRuntime, task) {
  return sha256Bytes(Buffer.from(canonicalJson({
    classification: {
      mode: classification.mode,
      reasons: classification.reasons,
      confidence: classification.confidence,
      evaluatedInputs: classification.evaluatedInputs,
    },
    hostRuntime,
    lightBrief: markdown,
    state: { schemaVersion: STATE_SCHEMA_VERSION, reviewer: hostRuntime },
    task,
  }), 'utf8'));
}

function artifactHashes(files) {
  return Object.fromEntries(HASHED_ARTIFACT_NAMES.map((name) => {
    const value = Buffer.isBuffer(files[name]) ? files[name] : Buffer.from(files[name], 'utf8');
    return [name, sha256Bytes(value)];
  }));
}

function frozenFingerprint(state) {
  const { frozenFingerprint: ignored, ...metadata } = state;
  return sha256Bytes(Buffer.from(canonicalJson(metadata), 'utf8'));
}

function hasExactKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && canonicalJson(Object.keys(value).sort()) === canonicalJson([...keys].sort());
}

function isCanonicalTimestamp(value) {
  if (typeof value !== 'string') return false;
  const date = new Date(value);
  return !Number.isNaN(date.valueOf()) && date.toISOString() === value;
}

function validateTask(task) {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof task !== 'string' || !/^[a-z0-9][a-z0-9._-]*$/.test(task) || task.endsWith('.') || reserved.test(task)) {
    throw new GatedLoopError('LIGHT_TASK_INVALID', 'Light task must be a safe single path segment');
  }
}

function validateClassification(classification) {
  const valid = classification && typeof classification === 'object' && !Array.isArray(classification)
    && classification.mode === 'light' && Array.isArray(classification.reasons)
    && ['high', 'medium'].includes(classification.confidence)
    && classification.evaluatedInputs && typeof classification.evaluatedInputs === 'object';
  if (!valid) throw new GatedLoopError('LIGHT_MODE_REQUIRED', 'Only a Light classification can be frozen');
  let replay;
  try { replay = classifyMode({ ...classification.evaluatedInputs, requestedMode: null }); }
  catch { throw new GatedLoopError('LIGHT_MODE_REQUIRED', 'Light classification inputs are invalid'); }
  const expectedInputs = { ...replay.evaluatedInputs, requestedMode: classification.evaluatedInputs.requestedMode ?? null };
  const consistent = replay.mode === 'light'
    && canonicalJson(replay.reasons) === canonicalJson(classification.reasons)
    && replay.confidence === classification.confidence
    && canonicalJson(expectedInputs) === canonicalJson(classification.evaluatedInputs);
  if (!consistent) throw new GatedLoopError('LIGHT_MODE_REQUIRED', 'Light classification does not match its evaluated inputs');
  return classification;
}

function timestamp(now) {
  const value = typeof now === 'function' ? now() : new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError('LIGHT_TIMESTAMP_INVALID', 'Freeze timestamp is invalid');
  return date.toISOString();
}

function parseJson(bytes) {
  return JSON.parse(bytes.toString('utf8'));
}

async function readExisting(target, fs, expectedTask) {
  let stat;
  try { stat = await fs.lstat(target); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new GatedLoopError('LIGHT_SOURCE_CHANGED', 'Existing Light artifact path is not a directory');
  }
  try {
    const names = (await fs.readdir(target)).sort();
    if (canonicalJson(names) !== canonicalJson([...ARTIFACT_NAMES].sort())) throw new Error('unexpected frozen artifact');
    const bytes = Object.fromEntries(await Promise.all(names.map(async (name) => [
      name,
      await readSafeRegularFile(target, name, { fs }),
    ])));
    const mode = parseJson(bytes['mode.json']);
    const manifest = parseJson(bytes['source-manifest.json']);
    const state = parseJson(bytes['state.json']);
    const modeKeys = ['classifierVersion', 'mode', 'reasons', 'confidence', 'evaluatedInputs', 'hostRuntime', 'createdAt'];
    const stateKeys = [
      'schemaVersion', 'task', 'mode', 'stage', 'hostRuntime', 'reviewer', 'sourceFingerprint',
      'inputFingerprint', 'artifactHashes', 'updatedAt', 'frozenFingerprint',
    ];
    if (!hasExactKeys(mode, modeKeys) || !hasExactKeys(manifest, ['version', 'files', 'fingerprint', 'inputFingerprint'])
        || !hasExactKeys(state, stateKeys) || !isCanonicalTimestamp(mode.createdAt)) throw new Error('invalid frozen schema');
    validateClassification({
      mode: mode.mode,
      reasons: mode.reasons,
      confidence: mode.confidence,
      evaluatedInputs: mode.evaluatedInputs,
    });
    const hostRuntime = normalizeHostRuntime(mode.hostRuntime);
    const sourceFiles = [
      { path: 'light-brief.md', sha256: sha256Bytes(bytes['light-brief.md']) },
      { path: 'mode.json', sha256: sha256Bytes(bytes['mode.json']) },
    ];
    const validSourceFiles = Array.isArray(manifest.files) && manifest.files.length === sourceFiles.length
      && sourceFiles.every((entry, index) => hasExactKeys(manifest.files[index], ['path', 'sha256'])
        && manifest.files[index].path === entry.path && manifest.files[index].sha256 === entry.sha256);
    const hashes = artifactHashes(bytes);
    const valid = manifest.version === 1 && validSourceFiles
      && manifest.fingerprint === manifestFingerprint(sourceFiles)
      && typeof manifest.inputFingerprint === 'string' && /^[a-f0-9]{64}$/.test(manifest.inputFingerprint)
      && mode.classifierVersion === CLASSIFIER_VERSION && mode.mode === 'light'
      && state.schemaVersion === STATE_SCHEMA_VERSION && state.task === expectedTask
      && state.mode === 'light' && state.stage === 'BASELINE_FROZEN'
      && state.hostRuntime === hostRuntime && state.reviewer === hostRuntime
      && state.sourceFingerprint === manifest.fingerprint
      && state.inputFingerprint === manifest.inputFingerprint
      && state.updatedAt === mode.createdAt && isCanonicalTimestamp(state.updatedAt)
      && canonicalJson(state.artifactHashes) === canonicalJson(hashes)
      && state.frozenFingerprint === frozenFingerprint(state)
      && manifest.inputFingerprint === fingerprintInput(
        mode,
        bytes['light-brief.md'].toString('utf8'),
        hostRuntime,
        expectedTask,
      );
    if (!valid) throw new Error('invalid frozen artifact');
    return { mode, manifest, state, bytes };
  } catch {
    throw new GatedLoopError('LIGHT_SOURCE_CHANGED', 'Existing Light artifacts are incomplete or changed');
  }
}

function outcome(target, existing, created) {
  return {
    created,
    idempotent: !created,
    mode: 'light',
    stage: 'BASELINE_FROZEN',
    artifactDir: target,
    artifacts: ARTIFACT_NAMES.map((name) => path.join(target, name)),
    sourceFingerprint: existing.manifest.fingerprint,
    inputFingerprint: existing.manifest.inputFingerprint,
    hostRuntime: existing.mode.hostRuntime,
    reviewer: existing.mode.hostRuntime,
  };
}

function generatedArtifactsMatch(existing, generated) {
  return Object.entries(generated.files).every(([name, content]) => (
    existing.bytes[name].toString('utf8') === content
  ));
}

async function freezeLightTaskLocked({
  root,
  task,
  classification,
  brief,
  hostRuntime,
  now = () => new Date(),
  beforeCommit,
  fs = fsPromises,
} = {}) {
  const markdown = buildLightBrief(brief);
  const generated = buildLightArtifacts({ task, reviewer: hostRuntime, brief, markdown });
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task), { fs });
  const inputFingerprint = fingerprintInput(classification, markdown, hostRuntime, task);
  const existing = await readExisting(target, fs, task);
  if (existing) {
    if (existing.manifest.inputFingerprint !== inputFingerprint || !generatedArtifactsMatch(existing, generated)) {
      throw new GatedLoopError('LIGHT_SOURCE_CHANGED', 'Frozen Light source differs from the requested input');
    }
    return outcome(target, existing, false);
  }

  const createdAt = timestamp(now);
  const mode = {
    classifierVersion: CLASSIFIER_VERSION,
    mode: classification.mode,
    reasons: classification.reasons,
    confidence: classification.confidence,
    evaluatedInputs: classification.evaluatedInputs,
    hostRuntime,
    createdAt,
  };
  const modeText = json(mode);
  const sourceFiles = [
    { path: 'light-brief.md', sha256: sha256Bytes(Buffer.from(markdown, 'utf8')) },
    { path: 'mode.json', sha256: sha256Bytes(Buffer.from(modeText, 'utf8')) },
  ];
  const manifest = {
    version: 1,
    files: sourceFiles,
    fingerprint: manifestFingerprint(sourceFiles),
    inputFingerprint,
  };
  const files = {
    ...generated.files,
    'mode.json': modeText,
    'source-manifest.json': json(manifest),
  };
  const state = {
    schemaVersion: STATE_SCHEMA_VERSION,
    task,
    mode: 'light',
    stage: 'BASELINE_FROZEN',
    hostRuntime,
    reviewer: hostRuntime,
    sourceFingerprint: manifest.fingerprint,
    inputFingerprint,
    artifactHashes: artifactHashes(files),
    updatedAt: createdAt,
  };
  state.frozenFingerprint = frozenFingerprint(state);
  files['state.json'] = json(state);

  try {
    await atomicWriteDirectory(target, async (staging) => {
      for (const name of ARTIFACT_NAMES) await atomicWriteFile(path.join(staging, name), files[name], { fs });
      if (beforeCommit) await beforeCommit(staging);
    }, { fs });
  } catch (error) {
    if (!['EEXIST', 'ENOTEMPTY', 'EPERM'].includes(error.code)) throw error;
    const raced = await readExisting(target, fs, task);
    if (raced?.manifest.inputFingerprint === inputFingerprint && generatedArtifactsMatch(raced, generated)) {
      return outcome(target, raced, false);
    }
    throw new GatedLoopError('LIGHT_SOURCE_CHANGED', 'A different Light source was frozen concurrently');
  }
  return outcome(target, { manifest, mode }, true);
}

export async function freezeLightTask(options = {}) {
  const {
    root,
    task,
    classification,
    brief,
    hostRuntime: suppliedHostRuntime,
    confirmed = false,
    fs = fsPromises,
  } = options;
  if (confirmed !== true) throw new GatedLoopError('CONFIRMATION_REQUIRED', 'Light brief freeze requires explicit confirmation');
  if (typeof root !== 'string' || root.length === 0) throw new GatedLoopError('LIGHT_ROOT_INVALID', 'Project root is required');
  validateTask(task);
  let stableClassification;
  let stableBrief;
  try {
    stableClassification = structuredClone(classification);
    stableBrief = structuredClone(brief);
  } catch {
    throw new GatedLoopError('LIGHT_MODE_REQUIRED', 'Light freeze inputs must be structured data');
  }
  validateClassification(stableClassification);
  const hostRuntime = requireHostRuntime(suppliedHostRuntime);
  const normalizedBrief = validateLightBrief(stableBrief);
  if (canonicalJson(normalizedBrief.scope) !== canonicalJson(stableClassification.evaluatedInputs.modifiesFiles)) {
    throw new GatedLoopError('LIGHT_SCOPE_MISMATCH', 'Light brief scope must match classified write paths');
  }
  let rootStat;
  try { rootStat = await fs.lstat(root); }
  catch (error) {
    if (error.code === 'ENOENT') throw new GatedLoopError('LIGHT_ROOT_INVALID', 'Project root must already exist');
    throw error;
  }
  if (!rootStat.isDirectory()) throw new GatedLoopError('LIGHT_ROOT_INVALID', 'Project root must be a directory');
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => freezeLightTaskLocked({
      ...options,
      classification: stableClassification,
      brief: normalizedBrief,
      hostRuntime,
      fs,
    }),
    { fs },
  );
}

export const freezeLight = freezeLightTask;
