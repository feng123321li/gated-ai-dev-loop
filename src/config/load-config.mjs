import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { parse } from 'yaml';
import { GatedLoopError } from '../core/errors.mjs';
import { canonicalRelativePath } from '../core/hash.mjs';

const defaults = Object.freeze({
  version: 1,
  runtimeDir: '.ai-dev-loop',
  maxRepairLoops: 3,
  tools: { claude: 'claude', codex: 'codex', git: 'git' },
  protectedPaths: ['.ai-dev-loop/**', '.git/**'],
  forbiddenPaths: ['.env*', '**/.env*', '**/*production*', '**/*preproduction*'],
});
const KEYS = new Set(Object.keys(defaults)); const TOOL_KEYS = new Set(Object.keys(defaults.tools));
function fail(code, message, details = {}) { throw new GatedLoopError(code, message, { details }); }
function strings(value, key) { if (!Array.isArray(value) || value.some((x) => typeof x !== 'string')) fail('CONFIG_INVALID_TYPE', `${key} must be an array of strings`); }
function validatePatterns(value, key) {
  strings(value, key);
  for (const pattern of value) {
    const segments = pattern.split(/[\\/]/);
    const invalid = pattern.length === 0
      || path.posix.isAbsolute(pattern)
      || path.win32.isAbsolute(pattern)
      || /^[\\/]{2}/.test(pattern)
      || segments.includes('..')
      || pattern.includes(':')
      || pattern.includes('\0');
    if (invalid) fail('INVALID_CONFIG', `${key} contains an unsafe path pattern`, { key, pattern });
  }
}

export async function loadConfig(root) {
  let supplied = {};
  try { supplied = parse(await readFile(path.join(root, '.gated-loop.yml'), 'utf8')) ?? {}; }
  catch (error) { if (error.code !== 'ENOENT') fail('CONFIG_PARSE', 'Unable to parse .gated-loop.yml', { cause: error.message }); }
  if (!supplied || typeof supplied !== 'object' || Array.isArray(supplied)) fail('CONFIG_INVALID_TYPE', 'Configuration must be a mapping');
  for (const key of Object.keys(supplied)) if (!KEYS.has(key)) fail('CONFIG_UNKNOWN_KEY', `Unknown configuration key: ${key}`);
  if (supplied.version !== undefined && supplied.version !== 1) fail('CONFIG_VERSION', 'Configuration version must be 1');
  if (supplied.runtimeDir !== undefined && typeof supplied.runtimeDir !== 'string') fail('CONFIG_INVALID_TYPE', 'runtimeDir must be a string');
  if (supplied.maxRepairLoops !== undefined && (!Number.isInteger(supplied.maxRepairLoops) || supplied.maxRepairLoops < 0)) fail('CONFIG_INVALID_TYPE', 'maxRepairLoops must be a non-negative integer');
  if (supplied.tools !== undefined) {
    if (!supplied.tools || typeof supplied.tools !== 'object' || Array.isArray(supplied.tools)) fail('CONFIG_INVALID_TYPE', 'tools must be a mapping');
    for (const key of Object.keys(supplied.tools)) if (!TOOL_KEYS.has(key)) fail('CONFIG_UNKNOWN_KEY', `Unknown tool key: ${key}`);
    for (const [key, value] of Object.entries(supplied.tools)) if (typeof value !== 'string' || value.length === 0) fail('CONFIG_INVALID_TYPE', `tools.${key} must be a non-empty string`);
  }
  if (supplied.protectedPaths !== undefined) validatePatterns(supplied.protectedPaths, 'protectedPaths');
  if (supplied.forbiddenPaths !== undefined) validatePatterns(supplied.forbiddenPaths, 'forbiddenPaths');
  let runtimeDir;
  try { runtimeDir = canonicalRelativePath(supplied.runtimeDir ?? defaults.runtimeDir); }
  catch { fail('CONFIG_PATH_OUTSIDE_ROOT', 'runtimeDir must remain within the project'); }
  return { ...defaults, ...supplied, runtimeDir, tools: { ...defaults.tools, ...supplied.tools }, protectedPaths: supplied.protectedPaths ?? [...defaults.protectedPaths], forbiddenPaths: supplied.forbiddenPaths ?? [...defaults.forbiddenPaths] };
}
