import { GatedLoopError } from '../core/errors.mjs';

export function normalizeHostRuntime(hostRuntime) {
  if (hostRuntime === undefined || hostRuntime === null) return undefined;
  if (!['codex', 'claude'].includes(hostRuntime)) {
    throw new GatedLoopError('HOST_RUNTIME_INVALID', 'hostRuntime must be codex or claude');
  }
  return hostRuntime;
}

export function requireHostRuntime(hostRuntime) {
  const normalized = normalizeHostRuntime(hostRuntime);
  if (!normalized) {
    throw new GatedLoopError('HOST_RUNTIME_REQUIRED', 'A writing workflow requires --host-runtime codex or claude');
  }
  return normalized;
}
