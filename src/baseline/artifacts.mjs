import { GatedLoopError } from '../core/errors.mjs';
import { BASELINE_GENERATOR_VERSION, BASELINE_SCHEMA_VERSION } from './parse.mjs';

export function buildBaselineArtifacts(model, { baselineFingerprint } = {}) {
  if (!model || !Array.isArray(model.acceptance) || !Array.isArray(model.tasks)) {
    throw new GatedLoopError('BASELINE_MODEL_INVALID', 'Baseline model is required');
  }
  return {
    acceptance: {
      schemaVersion: BASELINE_SCHEMA_VERSION,
      generatorVersion: BASELINE_GENERATOR_VERSION,
      ...(baselineFingerprint ? { baselineFingerprint } : {}),
      acceptance: model.acceptance.map(({ id, requirementIds, expectedResult, trace }) => ({
        id, requirementIds: [...requirementIds], expectedResult, trace: { ...trace },
      })),
    },
    tasks: {
      schemaVersion: BASELINE_SCHEMA_VERSION,
      generatorVersion: BASELINE_GENERATOR_VERSION,
      ...(baselineFingerprint ? { baselineFingerprint } : {}),
      tasks: model.tasks.map(({ id, requirementIds, acceptanceIds, text, trace }) => ({
        id, requirementIds: [...requirementIds], acceptanceIds: [...acceptanceIds], text, trace: { ...trace },
      })),
    },
  };
}
