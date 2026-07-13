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
import { CLASSIFIER_VERSION, classifyMode } from './classify.mjs';
import { requireHostRuntime } from './host-runtime.mjs';

function json(value) { return `${JSON.stringify(value, null, 2)}\n`; }

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
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
    throw new GatedLoopError('MODE_TASK_INVALID', 'Mode task must be a safe single path segment');
  }
}

function replayClassification(classification) {
  const valid = classification && typeof classification === 'object' && !Array.isArray(classification)
    && classification.mode === 'full' && Array.isArray(classification.reasons)
    && ['high', 'medium'].includes(classification.confidence)
    && classification.evaluatedInputs && typeof classification.evaluatedInputs === 'object';
  if (!valid) throw new GatedLoopError('FULL_MODE_REQUIRED', 'Only a Full classification can be persisted');
  let replay;
  try { replay = classifyMode(classification.evaluatedInputs); }
  catch { throw new GatedLoopError('FULL_MODE_REQUIRED', 'Full classification inputs are invalid'); }
  const consistent = replay.mode === 'full'
    && canonicalJson(replay.reasons) === canonicalJson(classification.reasons)
    && replay.confidence === classification.confidence
    && canonicalJson(replay.evaluatedInputs) === canonicalJson(classification.evaluatedInputs);
  if (!consistent) throw new GatedLoopError('FULL_MODE_REQUIRED', 'Full classification does not match its evaluated inputs');
  return replay;
}

function timestamp(now) {
  const value = typeof now === 'function' ? now() : new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError('MODE_TIMESTAMP_INVALID', 'Mode timestamp is invalid');
  return date.toISOString();
}

function semanticMode(classification, hostRuntime) {
  return {
    mode: classification.mode,
    reasons: classification.reasons,
    confidence: classification.confidence,
    evaluatedInputs: classification.evaluatedInputs,
    hostRuntime: hostRuntime ?? null,
  };
}

async function readExisting(target, fs) {
  let stat;
  try { stat = await fs.lstat(target); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  if (!stat.isDirectory()) throw new GatedLoopError('MODE_SOURCE_CHANGED', 'Existing mode artifact path is not a directory');
  try {
    const modeBytes = await readSafeRegularFile(target, 'mode.json', { fs });
    const mode = JSON.parse(modeBytes.toString('utf8'));
    const keys = ['classifierVersion', 'mode', 'reasons', 'confidence', 'evaluatedInputs', 'hostRuntime', 'createdAt'];
    if (!hasExactKeys(mode, keys) || mode.classifierVersion !== CLASSIFIER_VERSION || !isCanonicalTimestamp(mode.createdAt)) {
      throw new Error('invalid mode artifact');
    }
    const classification = replayClassification(mode);
    const hostRuntime = requireHostRuntime(mode.hostRuntime);
    return { mode, classification, hostRuntime };
  } catch {
    throw new GatedLoopError('MODE_SOURCE_CHANGED', 'Existing mode artifact is incomplete or changed');
  }
}

function result(target, existing, created) {
  const value = {
    created,
    idempotent: !created,
    mode: 'full',
    artifactDir: target,
    artifacts: [path.join(target, 'mode.json')],
  };
  if (existing.hostRuntime) value.hostRuntime = existing.hostRuntime;
  return value;
}

async function persistFullModeLocked({
  root,
  task,
  classification,
  hostRuntime: suppliedHostRuntime,
  now = () => new Date(),
  beforeCommit,
  fs = fsPromises,
} = {}) {
  if (typeof root !== 'string' || root.length === 0) throw new GatedLoopError('MODE_ROOT_INVALID', 'Project root is required');
  validateTask(task);
  let stableClassification;
  try { stableClassification = structuredClone(classification); }
  catch { throw new GatedLoopError('FULL_MODE_REQUIRED', 'Full mode input must be structured data'); }
  stableClassification = replayClassification(stableClassification);
  const hostRuntime = requireHostRuntime(suppliedHostRuntime);
  let rootStat;
  try { rootStat = await fs.lstat(root); }
  catch (error) {
    if (error.code === 'ENOENT') throw new GatedLoopError('MODE_ROOT_INVALID', 'Project root must already exist');
    throw error;
  }
  if (!rootStat.isDirectory()) throw new GatedLoopError('MODE_ROOT_INVALID', 'Project root must be a directory');
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task), { fs });
  const desiredSemantic = semanticMode(stableClassification, hostRuntime);
  const existing = await readExisting(target, fs);
  if (existing) {
    if (canonicalJson(semanticMode(existing.classification, existing.hostRuntime)) !== canonicalJson(desiredSemantic)) {
      throw new GatedLoopError('MODE_SOURCE_CHANGED', 'Persisted mode differs from the requested route');
    }
    return result(target, existing, false);
  }

  const mode = {
    classifierVersion: CLASSIFIER_VERSION,
    mode: stableClassification.mode,
    reasons: stableClassification.reasons,
    confidence: stableClassification.confidence,
    evaluatedInputs: stableClassification.evaluatedInputs,
    hostRuntime,
    createdAt: timestamp(now),
  };
  try {
    await atomicWriteDirectory(target, async (staging) => {
      await atomicWriteFile(path.join(staging, 'mode.json'), json(mode), { fs });
      if (beforeCommit) await beforeCommit(staging);
    }, { fs });
  } catch (error) {
    if (!['EEXIST', 'ENOTEMPTY', 'EPERM'].includes(error.code)) throw error;
    const raced = await readExisting(target, fs);
    if (raced && canonicalJson(semanticMode(raced.classification, raced.hostRuntime)) === canonicalJson(desiredSemantic)) {
      return result(target, raced, false);
    }
    throw new GatedLoopError('MODE_SOURCE_CHANGED', 'A different mode source was persisted concurrently');
  }
  return result(target, { mode, hostRuntime }, true);
}

export async function persistFullMode(options = {}) {
  const {
    root,
    task,
    classification,
    hostRuntime,
    fs = fsPromises,
  } = options;
  if (typeof root !== 'string' || root.length === 0) {
    throw new GatedLoopError('MODE_ROOT_INVALID', 'Project root is required');
  }
  validateTask(task);
  let stableClassification;
  try { stableClassification = structuredClone(classification); }
  catch { throw new GatedLoopError('FULL_MODE_REQUIRED', 'Full mode input must be structured data'); }
  replayClassification(stableClassification);
  requireHostRuntime(hostRuntime);
  let rootStat;
  try { rootStat = await fs.lstat(root); }
  catch (error) {
    if (error.code === 'ENOENT') throw new GatedLoopError('MODE_ROOT_INVALID', 'Project root must already exist');
    throw error;
  }
  if (!rootStat.isDirectory()) throw new GatedLoopError('MODE_ROOT_INVALID', 'Project root must be a directory');
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => persistFullModeLocked({ ...options, classification: stableClassification, fs }),
    { fs },
  );
}
