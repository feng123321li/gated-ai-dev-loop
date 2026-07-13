import { classifyMode } from '../mode/classify.mjs';

export function routeTask(signals) {
  return classifyMode(signals);
}

export const route = routeTask;
