import { GatedLoopError } from '../core/errors.mjs';
import { sha256Bytes } from '../core/hash.mjs';
import { buildLightBrief } from '../light/build-brief.mjs';
import { freezeLightTask } from '../light/freeze.mjs';
import { normalizeHostRuntime } from '../mode/host-runtime.mjs';
import { persistFullMode } from '../mode/persist.mjs';
import { routeTask } from './route.mjs';

export function deterministicTaskId(description) {
  const canonicalDescription = typeof description === 'string' ? description.normalize('NFC') : '';
  return `task-${sha256Bytes(Buffer.from(canonicalDescription, 'utf8')).slice(0, 20)}`;
}

async function resolvePersistenceTask(task, route, generateTaskId) {
  if (task !== undefined) return task;
  if (generateTaskId !== undefined && typeof generateTaskId !== 'function') {
    throw new GatedLoopError('TASK_ID_GENERATOR_INVALID', 'Task ID generator must be a function');
  }
  const generator = generateTaskId ?? deterministicTaskId;
  return generator(route.evaluatedInputs.description);
}

export async function startTask({
  root,
  task,
  signals,
  brief,
  confirmed = false,
  hostRuntime: suppliedHostRuntime,
  generateTaskId,
  now,
  beforeCommit,
  fs,
} = {}) {
  const hostRuntime = normalizeHostRuntime(suppliedHostRuntime);
  const host = hostRuntime ? { hostRuntime } : {};
  const route = routeTask(signals);
  if (route.mode === 'none') return { route, nextAction: 'none', artifacts: [], ...host };
  if (route.mode === 'full') {
    const resolvedTask = await resolvePersistenceTask(task, route, generateTaskId);
    const persistence = await persistFullMode({ root, task: resolvedTask, classification: route, hostRuntime, now, beforeCommit, fs });
    return { route, task: resolvedTask, nextAction: 'prepare', authority: 'generic-baseline', persistence, artifacts: persistence.artifacts, ...host };
  }
  if (confirmed === true && brief === undefined) throw new GatedLoopError('LIGHT_BRIEF_REQUIRED', 'Confirmed Light start requires an injected brief');
  if (confirmed !== true) {
    return {
      route,
      nextAction: 'confirm',
      brief: brief === undefined ? null : buildLightBrief(brief),
      artifacts: [],
      ...host,
    };
  }
  buildLightBrief(brief);
  const resolvedTask = await resolvePersistenceTask(task, route, generateTaskId);
  const frozen = await freezeLightTask({ root, task: resolvedTask, classification: route, brief, confirmed, hostRuntime, now, beforeCommit, fs });
  return { route, task: resolvedTask, nextAction: 'develop', freeze: frozen, artifacts: frozen.artifacts, ...host };
}

export const start = startTask;
