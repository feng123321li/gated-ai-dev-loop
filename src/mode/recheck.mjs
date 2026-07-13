import { GatedLoopError } from '../core/errors.mjs';
import { classifyMode } from './classify.mjs';

function normalizeArguments(input, diff) {
  if (diff !== undefined) {
    const initialMode = typeof input === 'string' ? input : input?.mode;
    return { ...diff, initialMode };
  }
  return input;
}

export function recheckMode(input, diff) {
  const options = normalizeArguments(input, diff);
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new GatedLoopError('MODE_RECHECK_INVALID', 'Mode recheck input must be a mapping');
  }
  const initialMode = typeof options.initialMode === 'string' ? options.initialMode : options.initialMode?.mode;
  if (!['full', 'light', 'none'].includes(initialMode)) {
    throw new GatedLoopError('MODE_RECHECK_INVALID', 'initialMode must be full, light, or none');
  }
  const hasChangedPaths = Object.hasOwn(options, 'changedPaths');
  const hasActualChangedPaths = Object.hasOwn(options, 'actualChangedPaths');
  if (hasChangedPaths === hasActualChangedPaths) {
    throw new GatedLoopError('MODE_RECHECK_INVALID', 'Supply exactly one changed path array');
  }
  const changedPaths = hasChangedPaths ? options.changedPaths : options.actualChangedPaths;
  if (!Array.isArray(changedPaths)) throw new GatedLoopError('MODE_RECHECK_INVALID', 'changedPaths must be an array');
  const detectedSignals = options.detectedSignals ?? options.diffSignals ?? {};
  if (!detectedSignals || typeof detectedSignals !== 'object' || Array.isArray(detectedSignals)) {
    throw new GatedLoopError('MODE_RECHECK_INVALID', 'detectedSignals must be a mapping');
  }
  const classification = classifyMode({
    description: 'Actual Git diff recheck',
    writesFiles: changedPaths.length > 0,
    ...detectedSignals,
    modifiesFiles: changedPaths,
    requestedMode: null,
  });

  if (initialMode === 'full') {
    return { allowed: true, requiredMode: 'full', reasons: classification.reasons };
  }
  if (initialMode === 'light' && classification.mode === 'full') {
    return {
      allowed: false,
      requiredMode: 'full',
      code: 'MODE_ESCALATION_REQUIRED',
      reasons: classification.reasons,
    };
  }
  if (initialMode === 'none' && classification.mode !== 'none') {
    return {
      allowed: false,
      requiredMode: classification.mode,
      code: 'MODE_ESCALATION_REQUIRED',
      reasons: classification.reasons,
    };
  }
  return { allowed: true, requiredMode: initialMode, reasons: classification.reasons };
}

export const recheck = recheckMode;
