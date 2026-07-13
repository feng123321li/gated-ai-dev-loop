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
  DEFAULT_DECISION_LOG,
  artifactHashes,
  fullOutcome,
  json,
  readFullPackage,
  renderFullHandoff,
  replaceFullPackage,
  timestamp,
  validateTask,
  validateFrozenPackage,
} from './package.mjs';

function expectedArtifacts(model, renderedBaseline, manifest) {
  const baselineFingerprint = sha256Bytes(Buffer.from(renderedBaseline, 'utf8'));
  const generated = buildBaselineArtifacts(model, { baselineFingerprint });
  return {
    baselineFingerprint,
    baselineText: renderedBaseline,
    manifestText: json(manifest),
    acceptanceText: json(generated.acceptance),
    tasksText: json(generated.tasks),
  };
}

function exactPreparedArtifacts(existing, expected) {
  return existing.bytes['baseline.md'].toString('utf8') === expected.baselineText
    && existing.bytes['source-manifest.json'].toString('utf8') === expected.manifestText
    && existing.bytes['acceptance.json'].toString('utf8') === expected.acceptanceText
    && existing.bytes['tasks.json'].toString('utf8') === expected.tasksText;
}

function changed(error) {
  return error instanceof GatedLoopError
    ? new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Frozen baseline sources changed')
    : error;
}

function sameBytes(left, right) {
  return left && right && Buffer.compare(left, right) === 0;
}

async function prepareFullBaselineLocked({
  root,
  task,
  baseline,
  sources = [],
  now = () => new Date(),
  beforeCommit,
  fs = fsPromises,
} = {}) {
  const existing = await readFullPackage({ root, task, fs });
  let sourceSet;
  try { sourceSet = await readBaselineSources({ root, baseline, sources, fs }); }
  catch (error) { if (existing.stage === 'BASELINE_FROZEN') throw changed(error); throw error; }

  let model;
  try { model = parseFullBaseline(sourceSet.baseline.text, { file: sourceSet.baseline.path }); }
  catch (error) { if (existing.stage === 'BASELINE_FROZEN') throw changed(error); throw error; }
  model.supportingSourceLines = sourceSet.sources.flatMap((source) => source.lines.map((line) => ({ ...line, section: 'Source' })));
  model.allSourceLines = [...model.sourceLines, ...model.supportingSourceLines];
  const renderedBaseline = renderFullBaseline(model);
  const expected = expectedArtifacts(model, renderedBaseline, sourceSet.manifest);

  if (existing.stage) {
    const sameSource = existing.state.sourceFingerprint === sourceSet.manifest.fingerprint
      && existing.state.baselineFingerprint === expected.baselineFingerprint;
    if (existing.stage === 'BASELINE_FROZEN') {
      if (!sameSource || !exactPreparedArtifacts(existing, expected)) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Frozen baseline cannot be changed');
      validateFrozenPackage(existing, renderFullHandoff(model, task, existing.hostRuntime));
      return fullOutcome(existing);
    }
    if (sameSource) {
      if (!exactPreparedArtifacts(existing, expected)) throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Prepared baseline artifacts changed');
      return fullOutcome(existing);
    }
  }

  const updatedAt = timestamp(now);
  const decisionBytes = existing.bytes['decision-log.md'] ?? Buffer.from(DEFAULT_DECISION_LOG, 'utf8');
  const files = {
    'mode.json': existing.modeBytes,
    'baseline.md': expected.baselineText,
    'acceptance.json': expected.acceptanceText,
    'tasks.json': expected.tasksText,
    'source-manifest.json': expected.manifestText,
    'decision-log.md': decisionBytes,
  };
  const hashedNames = ['acceptance.json', 'baseline.md', 'mode.json', 'source-manifest.json', 'tasks.json'];
  const hashes = artifactHashes(files, hashedNames);
  const state = {
    schemaVersion: 1,
    task,
    mode: 'full',
    stage: 'WAITING_FOR_BASELINE_CONFIRMATION',
    hostRuntime: existing.hostRuntime,
    reviewer: existing.hostRuntime,
    sourceFingerprint: sourceSet.manifest.fingerprint,
    baselineFingerprint: expected.baselineFingerprint,
    inputFingerprint: sha256Bytes(Buffer.from(canonicalJson({
      task,
      hostRuntime: existing.hostRuntime,
      sourceFingerprint: sourceSet.manifest.fingerprint,
      baselineFingerprint: expected.baselineFingerprint,
      modeSha256: hashes['mode.json'],
    }), 'utf8')),
    artifactHashes: hashes,
    updatedAt,
  };
  files['state.json'] = json(state);

  const verifyBeforeCommit = async (staging) => {
    if (beforeCommit) await beforeCommit(staging);
    const current = await readFullPackage({ root, task, fs });
    if (current.stage !== existing.stage || !sameBytes(current.modeBytes, existing.modeBytes)
        || (existing.stage && (!sameBytes(current.bytes['state.json'], existing.bytes['state.json'])
          || !sameBytes(current.bytes['decision-log.md'], existing.bytes['decision-log.md'])))) {
      throw new GatedLoopError('BASELINE_SOURCE_CHANGED', 'Task package changed during prepare');
    }
    let finalSources;
    try { finalSources = await readBaselineSources({ root, baseline, sources, fs }); }
    catch { throw new GatedLoopError('PATH_FILE_CHANGED', 'A baseline source changed during prepare'); }
    if (finalSources.manifest.fingerprint !== sourceSet.manifest.fingerprint || !sameSourceSnapshots(sourceSet, finalSources)) {
      throw new GatedLoopError('PATH_FILE_CHANGED', 'A baseline source changed during prepare');
    }
  };
  await replaceFullPackage(existing.target, files, { fs, beforeCommit: verifyBeforeCommit });
  const prepared = await readFullPackage({ root, task, fs });
  return fullOutcome(prepared, { created: existing.stage === null, updated: existing.stage !== null });
}

export async function prepareFullBaseline(options = {}) {
  const { root, task, fs = fsPromises } = options;
  if (typeof root !== 'string' || root.length === 0) {
    throw new GatedLoopError('BASELINE_ROOT_INVALID', 'Project root is required');
  }
  validateTask(task);
  const target = await assertSafePath(root, path.join('.ai-dev-loop', task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => prepareFullBaselineLocked({ ...options, fs }),
    { fs },
  );
}

export const prepareFullTask = prepareFullBaseline;
export const prepareBaseline = prepareFullBaseline;
