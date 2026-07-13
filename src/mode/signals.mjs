import path from 'node:path';

import { GatedLoopError } from '../core/errors.mjs';
import { canonicalRelativePath } from '../core/hash.mjs';

const BOOLEAN_FIELDS = Object.freeze([
  'loadBearing', 'breaking', 'dependencyChange', 'newDependency', 'externalContract',
  'permissions', 'authentication', 'stateMachine', 'transaction', 'concurrency',
  'idempotency', 'thresholdDecision',
]);
const INPUT_FIELDS = new Set([
  'description', 'modifiesFiles', 'writesFiles', ...BOOLEAN_FIELDS, 'migrations',
  'unresolvedOptions', 'impactKnown', 'requestedMode',
]);
const MIGRATION_CATEGORIES = new Set(['unspecified', 'database', 'schema', 'data', 'config', 'storage', 'api-version', 'dependency']);
const CONTRACT_FILE_EXTENSIONS = new Set([
  'avsc', 'graphql', 'graphqls', 'json', 'jsonc', 'md', 'mdx', 'proto', 'sql',
  'prisma', 'toml', 'wsdl', 'xsd', 'yaml', 'yml',
]);
const INTRINSIC_CONTRACT_EXTENSIONS = new Set([
  'avdl', 'avsc', 'graphqls', 'prisma', 'proto', 'raml', 'thrift', 'wsdl', 'xsd',
]);
const DIRECTORY_CONTRACT_PATTERN = /^(?:open[._-]?api|swagger|specs?|specifications?|schemas?|public[._-](?:contracts?|apis?))(?:[._-]?v?\d+(?:[._-]\d+)*)?$/;
const STEM_CONTRACT_PATTERN = /(?:^|[._-])(?:open[._-]?api|swagger|specs?|specifications?|schemas?|public[._-]+(?:contracts?|apis?))(?:[._-]?v?\d+(?:[._-]\d+)*)?$/;

function invalid(message, details = {}) {
  throw new GatedLoopError('MODE_SIGNALS_INVALID', message, { details });
}

function normalizePath(value) {
  if (typeof value !== 'string' || value.length === 0 || /[\u0000-\u001F\u007F]/.test(value) || /[*?\[\]{}<>"|]/.test(value)
      || path.posix.isAbsolute(value) || path.win32.isAbsolute(value) || /^[\\/]{2}/.test(value)
      || value.includes(':')) invalid('modifiesFiles must contain safe relative paths', { path: value });
  let normalized;
  try { normalized = canonicalRelativePath(value); }
  catch { invalid('modifiesFiles must contain safe relative paths', { path: value }); }
  if (normalized === '.' || normalized.split('/').includes('..')) invalid('modifiesFiles must contain safe relative paths', { path: value });
  return normalized;
}

function normalizeMigrations(value) {
  let values;
  if (value === undefined || value === false || value === null) values = [];
  else if (value === true) values = ['unspecified'];
  else if (typeof value === 'string') values = [value];
  else if (Array.isArray(value)) values = value;
  else if (typeof value === 'object') {
    const entries = Object.entries(value);
    if (entries.some(([category]) => !MIGRATION_CATEGORIES.has(category))) {
      invalid('migrations contains an unknown category', { migrations: entries.map(([category]) => category) });
    }
    if (entries.some(([, enabled]) => typeof enabled !== 'boolean')) invalid('migrations flags must be booleans');
    values = entries.filter(([, enabled]) => enabled).map(([category]) => category);
  } else invalid('migrations must be a boolean, category, array, or flag mapping');
  if (values.some((category) => typeof category !== 'string' || !MIGRATION_CATEGORIES.has(category))) {
    invalid('migrations contains an unknown category', { migrations: values });
  }
  return [...new Set(values)].sort();
}

export function isLoadBearingPath(filePath) {
  const normalized = normalizePath(filePath);
  const lower = normalized.toLowerCase();
  const segments = lower.split('/');
  const basename = segments.at(-1);
  if (['skill.md', 'agents.md', 'claude.md'].includes(basename)) return true;
  // Exact directory names are intentionally conservative: these locations are
  // authoritative even when their children use ordinary source extensions.
  if (segments.some((segment) => DIRECTORY_CONTRACT_PATTERN.test(segment))) return true;

  const extension = basename.includes('.') ? basename.split('.').at(-1) : '';
  if (INTRINSIC_CONTRACT_EXTENSIONS.has(extension) || basename === 'schema.rb') return true;
  if (!CONTRACT_FILE_EXTENSIONS.has(extension)) return false;
  const stem = basename.slice(0, -(extension.length + 1));
  return STEM_CONTRACT_PATTERN.test(stem);
}

export function normalizeSignals(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) invalid('Mode signals must be a mapping');
  for (const key of Object.keys(input)) if (!INPUT_FIELDS.has(key)) invalid(`Unknown mode signal: ${key}`, { key });

  if (input.description !== undefined && typeof input.description !== 'string') invalid('description must be a string');
  if (input.modifiesFiles !== undefined && (!Array.isArray(input.modifiesFiles) || input.modifiesFiles.some((entry) => typeof entry !== 'string'))) {
    invalid('modifiesFiles must be an array of strings');
  }
  if (input.writesFiles !== undefined && typeof input.writesFiles !== 'boolean') invalid('writesFiles must be a boolean');
  for (const field of BOOLEAN_FIELDS) {
    if (input[field] !== undefined && typeof input[field] !== 'boolean') invalid(`${field} must be a boolean`, { field });
  }
  if (input.impactKnown !== undefined && typeof input.impactKnown !== 'boolean') invalid('impactKnown must be a boolean');
  if (input.unresolvedOptions !== undefined && (!Number.isInteger(input.unresolvedOptions) || input.unresolvedOptions < 0)) {
    invalid('unresolvedOptions must be a non-negative integer');
  }
  if (input.requestedMode !== undefined && input.requestedMode !== null && !['full', 'light'].includes(input.requestedMode)) {
    invalid('requestedMode must be full, light, or null');
  }

  const modifiesFiles = [...new Set((input.modifiesFiles ?? []).map(normalizePath))].sort();
  const detectedLoadBearing = modifiesFiles.some(isLoadBearingPath);
  return {
    description: input.description ?? '',
    modifiesFiles,
    writesFiles: modifiesFiles.length > 0 || (input.writesFiles ?? true),
    loadBearing: Boolean(input.loadBearing) || detectedLoadBearing,
    breaking: Boolean(input.breaking),
    migrations: normalizeMigrations(input.migrations),
    dependencyChange: Boolean(input.dependencyChange),
    newDependency: Boolean(input.newDependency),
    externalContract: Boolean(input.externalContract),
    permissions: Boolean(input.permissions),
    authentication: Boolean(input.authentication),
    stateMachine: Boolean(input.stateMachine),
    transaction: Boolean(input.transaction),
    concurrency: Boolean(input.concurrency),
    idempotency: Boolean(input.idempotency),
    unresolvedOptions: input.unresolvedOptions ?? 0,
    thresholdDecision: Boolean(input.thresholdDecision),
    impactKnown: input.impactKnown ?? false,
    requestedMode: input.requestedMode ?? null,
  };
}

export const migrationCategories = Object.freeze([...MIGRATION_CATEGORIES]);
