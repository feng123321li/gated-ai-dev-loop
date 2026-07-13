import { GatedLoopError } from '../core/errors.mjs';
import { isLoadBearingPath, normalizeSignals } from './signals.mjs';

export const CLASSIFIER_VERSION = 1;

function hardReasons(input) {
  const reasons = [];
  if (input.loadBearing) reasons.push('LOAD_BEARING_FILE');
  if (input.breaking) reasons.push('BREAKING_CHANGE');
  if (input.migrations.length > 0) reasons.push('MIGRATION');
  if (input.dependencyChange) reasons.push('DEPENDENCY_CHANGE');
  if (input.newDependency) reasons.push('NEW_DEPENDENCY');
  if (input.externalContract) reasons.push('EXTERNAL_CONTRACT');
  if (input.permissions) reasons.push('PERMISSIONS');
  if (input.authentication) reasons.push('AUTHENTICATION');
  if (input.stateMachine) reasons.push('STATE_MACHINE');
  if (input.transaction) reasons.push('TRANSACTION');
  if (input.concurrency) reasons.push('CONCURRENCY');
  if (input.idempotency) reasons.push('IDEMPOTENCY');
  if (input.unresolvedOptions > 1) reasons.push('UNRESOLVED_OPTIONS');
  if (input.thresholdDecision) reasons.push('THRESHOLD_DECISION');
  const nonLoadBearingCount = input.modifiesFiles.filter((filePath) => !isLoadBearingPath(filePath)).length;
  if (nonLoadBearingCount > 3) reasons.push('FILE_LIMIT_EXCEEDED');
  if (input.writesFiles && input.modifiesFiles.length === 0) reasons.push('WRITE_PATHS_UNKNOWN');
  if (!input.impactKnown) reasons.push('IMPACT_UNKNOWN');
  return [...new Set(reasons)].sort();
}

function result(mode, reasons, confidence, evaluatedInputs) {
  return { mode, reasons, confidence, evaluatedInputs };
}

export function classifyMode(signals) {
  const evaluatedInputs = normalizeSignals(signals);
  const reasons = hardReasons(evaluatedInputs);

  if (evaluatedInputs.requestedMode === 'light' && reasons.length > 0) {
    throw new GatedLoopError('MODE_ESCALATION_REQUIRED', 'Light mode cannot bypass Full mode requirements', {
      details: { requiredMode: 'full', reasons },
    });
  }

  if (evaluatedInputs.requestedMode === 'full') {
    const forcedReasons = [...new Set([...reasons, 'USER_FORCED_FULL'])].sort();
    return result('full', forcedReasons, 'high', evaluatedInputs);
  }
  if (reasons.length > 0) {
    const uncertain = new Set(['IMPACT_UNKNOWN', 'WRITE_PATHS_UNKNOWN']);
    const confidence = reasons.every((reason) => uncertain.has(reason)) ? 'medium' : 'high';
    return result('full', reasons, confidence, evaluatedInputs);
  }
  if (!evaluatedInputs.writesFiles) return result('none', ['NO_FILE_WRITES'], 'high', evaluatedInputs);
  return result('light', ['LIGHT_ELIGIBLE'], 'high', evaluatedInputs);
}

export const classify = classifyMode;
