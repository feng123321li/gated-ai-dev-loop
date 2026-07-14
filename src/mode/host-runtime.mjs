import { GatedLoopError } from '../core/errors.mjs';

export const AGENT_RUNTIME_PATTERN = /^[a-z][a-z0-9._-]{0,63}$/;

export function isAgentRuntime(value) {
  return typeof value === 'string' && AGENT_RUNTIME_PATTERN.test(value);
}

export function normalizeHostRuntime(hostRuntime) {
  if (hostRuntime === undefined || hostRuntime === null) return undefined;
  if (!isAgentRuntime(hostRuntime)) {
    throw new GatedLoopError('HOST_RUNTIME_INVALID', 'hostRuntime must be a safe lowercase Agent identifier');
  }
  return hostRuntime;
}

export function requireHostRuntime(hostRuntime) {
  const normalized = normalizeHostRuntime(hostRuntime);
  if (!normalized) {
    throw new GatedLoopError('HOST_RUNTIME_REQUIRED', 'A writing workflow requires a --host-runtime Agent identifier');
  }
  return normalized;
}
