import * as fsPromises from 'node:fs/promises';
import path from 'node:path';

import { buildBaselineArtifacts } from '../baseline/artifacts.mjs';
import { parseFullBaseline } from '../baseline/parse.mjs';
import { renderFullBaseline } from '../baseline/render.mjs';
import { canonicalJson, readBaselineSources, sameSourceSnapshots } from '../baseline/sources.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { assertSafePath, withRuntimeDirectoryTransaction } from '../core/fs-safe.mjs';
import { sha256Bytes } from '../core/hash.mjs';
import {
  deriveFrozenPackage,
  frozenStateFingerprint,
  fullOutcome,
  json,
  readFullPackage,
  renderFullHandoff,
  replaceFullPackage,
  timestamp,
  validateTask,
  validateFrozenPackage,
} from './package.mjs';

function sourcePaths(manifest) {
  const valid = manifest && typeof manifest === 'object' && !Array.isArray(manifest)
    && manifest.schemaVersion === 1 && manifest.generatorVersion === 1
    && Array.isArray(manifest.files) && manifest.files.length > 0
    && manifest.files[0]?.purpose === 'baseline'
    && manifest.files.slice(1).every((entry) => entry?.purpose === 'source');
  if (!valid) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Prepared source manifest is invalid');
  return { baseline: manifest.files[0].path, sources: manifest.files.slice(1).map(({ path }) => path) };
}

function comparePrepared(taskPackage, sourceSet, model) {
  const baselineText = renderFullBaseline(model);
  const baselineFingerprint = sha256Bytes(Buffer.from(baselineText, 'utf8'));
  const generated = buildBaselineArtifacts(model, { baselineFingerprint });
  const valid = taskPackage.state.sourceFingerprint === sourceSet.manifest.fingerprint
    && taskPackage.state.baselineFingerprint === baselineFingerprint
    && canonicalJson(taskPackage.manifest) === canonicalJson(sourceSet.manifest)
    && taskPackage.bytes['baseline.md'].toString('utf8') === baselineText
    && taskPackage.bytes['acceptance.json'].toString('utf8') === json(generated.acceptance)
    && taskPackage.bytes['tasks.json'].toString('utf8') === json(generated.tasks);
  if (!valid) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Prepared baseline or sources changed');
  return baselineFingerprint;
}

function sameBytes(left, right) { return left && right && Buffer.compare(left, right) === 0; }

async function freezeFullBaselineLocked({
  root,
  task,
  confirmed = false,
  now = () => new Date(),
  beforeCommit,
  fs = fsPromises,
} = {}) {
  if (confirmed !== true) throw new GatedLoopError('CONFIRMATION_REQUIRED', 'Baseline freeze requires explicit confirmation');
  const taskPackage = await readFullPackage({ root, task, fs });
  if (taskPackage.stage === null) throw new GatedLoopError('BASELINE_NOT_PREPARED', 'Prepare the Full baseline before freezing');
  const paths = sourcePaths(taskPackage.manifest);
  let sourceSet;
  try { sourceSet = await readBaselineSources({ root, ...paths, fs }); }
  catch { throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Prepared baseline sources changed'); }
  let model;
  try { model = parseFullBaseline(sourceSet.baseline.text, { file: sourceSet.baseline.path }); }
  catch { throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Prepared baseline semantics changed'); }
  comparePrepared(taskPackage, sourceSet, model);
  const handoff = renderFullHandoff(model, task, taskPackage.hostRuntime);
  const { files, hashes } = deriveFrozenPackage(taskPackage, handoff);
  if (taskPackage.stage === 'BASELINE_FROZEN') {
    validateFrozenPackage(taskPackage, handoff);
    return fullOutcome(taskPackage);
  }

  const state = {
    ...taskPackage.state,
    stage: 'BASELINE_FROZEN',
    artifactHashes: hashes,
    updatedAt: timestamp(now),
  };
  state.frozenFingerprint = frozenStateFingerprint(state, hashes);
  files['state.json'] = json(state);
  const verifyBeforeCommit = async (staging) => {
    if (beforeCommit) await beforeCommit(staging);
    const current = await readFullPackage({ root, task, fs });
    if (current.stage !== taskPackage.stage || !sameBytes(current.bytes['state.json'], taskPackage.bytes['state.json'])
        || !sameBytes(current.bytes['decision-log.md'], taskPackage.bytes['decision-log.md'])) {
      throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Task package changed during freeze');
    }
    let finalSources;
    try { finalSources = await readBaselineSources({ root, ...paths, fs }); }
    catch { throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Baseline source changed during freeze'); }
    if (finalSources.manifest.fingerprint !== sourceSet.manifest.fingerprint || !sameSourceSnapshots(sourceSet, finalSources)) {
      throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Baseline source changed during freeze');
    }
  };
  await replaceFullPackage(taskPackage.target, files, { fs, beforeCommit: verifyBeforeCommit });
  const frozen = await readFullPackage({ root, task, fs });
  return fullOutcome(frozen, { created: true });
}

export async function freezeFullBaseline(options = {}) {
  const {
    root,
    task,
    confirmed = false,
    fs = fsPromises,
  } = options;
  if (confirmed !== true) {
    throw new GatedLoopError('CONFIRMATION_REQUIRED', 'Baseline freeze requires explicit confirmation');
  }
  if (typeof root !== 'string' || root.length === 0) {
    throw new GatedLoopError('BASELINE_ROOT_INVALID', 'Project root is required');
  }
  validateTask(task);
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => freezeFullBaselineLocked({ ...options, fs }),
    { fs },
  );
}

export const freezeFullTask = freezeFullBaseline;
export const freezeBaseline = freezeFullBaseline;
