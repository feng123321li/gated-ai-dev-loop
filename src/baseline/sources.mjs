import * as fsPromises from 'node:fs/promises';
import path from 'node:path';
import { TextDecoder } from 'node:util';

import { GatedLoopError } from '../core/errors.mjs';
import { readSafeRegularFileSnapshot, sameFileSnapshot } from '../core/fs-safe.mjs';
import { canonicalRelativePath, sha256Bytes } from '../core/hash.mjs';
import { BASELINE_GENERATOR_VERSION, BASELINE_SCHEMA_VERSION } from './parse.mjs';

const SECRET_DIRECTORY = /^(?:\.git|\.ssh|\.gnupg|credentials?(?:[._-].*)?|secrets?(?:[._-].*)?|private[._-]?keys?)$/i;
const AWS_DIRECTORY = /^\.aws(?:$|-)/i;
const ENV_FILE = /^(?:\.env(?:$|[._-])|\.envrc$)|\.env$/i;
const CONFIG_EXTENSIONS = new Set([
  'env', 'yml', 'yaml', 'json', 'toml', 'ini', 'conf', 'config', 'properties', 'xml',
  'cnf', 'cfg',
]);
const SENSITIVE_CONTENT_EXTENSIONS = new Set([...CONFIG_EXTENSIONS, 'csv', 'tsv', 'txt']);
const KEY_EXTENSIONS = new Set([
  'key', 'pem', 'p12', 'pfx', 'ppk', 'jks', 'jceks', 'bks', 'ks', 'kdbx', 'keystore', 'private-key',
]);
const ENVIRONMENT_TOKEN = /(?:^|[._-])(?:prod|production|pre|preprod|preproduction|staging)(?:[._-]|$)/i;
const SENSITIVE_DOTFILE = /^(?:\.npmrc|\.pypirc|\.netrc|\.yarnrc(?:\.yml)?)$/i;
const SENSITIVE_BARE_FILE = /^\.?(?:credentials?|secrets?|passwords?|tokens?|api[._-]?keys?|service[._-]?accounts?|client[._-]?secrets?|private[._-]?keys?|keystores?)$/i;
const SENSITIVE_STEM = /(?:^|[._-])(?:credentials?|secrets?|passwords?|tokens?|api[._-]?keys?|service[._-]?accounts?|client[._-]?secrets?|private[._-]?keys?|keystores?|access[._-]?tokens?)(?:[._-](?:prod|production|pre|preprod|preproduction|staging))?$/i;
const SSH_KEY_FILE = /^id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?(?:\.(?:pub|bak|old|orig|backup))*~?$/i;
const BACKUP_SUFFIX = /\.(?:bak|old|orig|backup)~?$/i;

function forbiddenDirectory(segment) {
  return SECRET_DIRECTORY.test(segment) || AWS_DIRECTORY.test(segment) || ENV_FILE.test(segment);
}

function withoutBackupSuffixes(value) {
  let result = value;
  while (BACKUP_SUFFIX.test(result)) result = result.replace(BACKUP_SUFFIX, '');
  return result;
}

function sensitiveCloudConfig(segments) {
  const normalized = [...segments];
  normalized[normalized.length - 1] = withoutBackupSuffixes(normalized.at(-1));
  const candidate = normalized.join('/');
  return /(?:^|\/)\.docker\/config\.json$/i.test(candidate)
    || /(?:^|\/)\.kube\/config$/i.test(candidate)
    || /(?:^|\/)\.azure\/accesstokens\.json$/i.test(candidate);
}

function forbiddenBasename(basename) {
  const name = withoutBackupSuffixes(basename);
  if (ENV_FILE.test(name) || SENSITIVE_DOTFILE.test(name) || SSH_KEY_FILE.test(basename)
      || SENSITIVE_BARE_FILE.test(name)) return true;
  const extension = path.posix.extname(name).slice(1).toLowerCase();
  if (KEY_EXTENSIONS.has(extension)) return true;
  if (!SENSITIVE_CONTENT_EXTENSIONS.has(extension)) return false;
  const stem = name.slice(0, -(extension.length + 1));
  return SENSITIVE_STEM.test(stem);
}

function configEnvironmentPath(segments) {
  const basename = withoutBackupSuffixes(segments.at(-1));
  const extension = path.posix.extname(basename).slice(1).toLowerCase();
  if (!CONFIG_EXTENSIONS.has(extension)) return false;
  const stem = basename.slice(0, -(extension.length + 1));
  return [...segments.slice(0, -1), stem].some((segment) => ENVIRONMENT_TOKEN.test(segment));
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function comparePath(left, right) { return left.path < right.path ? -1 : left.path > right.path ? 1 : 0; }

export function sourceManifestFingerprint(files) {
  const canonical = [...files].map(({ path: filePath, sha256, purpose }) => ({ path: filePath, sha256, purpose })).sort(comparePath);
  return sha256Bytes(Buffer.from(canonicalJson(canonical), 'utf8'));
}

export function sameSourceSnapshots(left, right) {
  return Array.isArray(left?.entries) && Array.isArray(right?.entries) && left.entries.length === right.entries.length
    && left.entries.every((entry, index) => entry.path === right.entries[index].path
      && sameFileSnapshot(entry.snapshot, right.entries[index].snapshot));
}

function invalid(message, details = {}) {
  throw new GatedLoopError('BASELINE_PATH_INVALID', message, { details });
}

export function normalizeBaselineInputPath(candidate) {
  if (typeof candidate !== 'string' || candidate.length === 0 || /[\u0000-\u001F\u007F]/.test(candidate)
      || /[*?\[\]{}<>"|]/.test(candidate) || path.posix.isAbsolute(candidate) || path.win32.isAbsolute(candidate)
      || /^[\\/]{2}/.test(candidate) || candidate.includes(':')) {
    invalid('Baseline inputs must be explicit repository-relative paths', { path: candidate });
  }
  let normalized;
  try { normalized = canonicalRelativePath(candidate); }
  catch { invalid('Baseline input escapes the repository', { path: candidate }); }
  const segments = normalized.split('/');
  const lower = normalized.toLowerCase();
  if (normalized === '.' || segments.includes('..') || lower === '.ai-dev-loop' || lower.startsWith('.ai-dev-loop/')
      || segments.some((segment) => /[. ]$/.test(segment))
      || segments.slice(0, -1).some(forbiddenDirectory)
      || sensitiveCloudConfig(segments) || forbiddenBasename(segments.at(-1))
      || configEnvironmentPath(segments)) {
    invalid('Baseline input path is forbidden', { path: candidate });
  }
  return normalized;
}

function decode(bytes, filePath) {
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { throw new GatedLoopError('BASELINE_UTF8_INVALID', `Baseline input is not valid UTF-8: ${filePath}`); }
}

function decodeSupportingSource(bytes) {
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { return undefined; }
}

async function readEntry(root, filePath, purpose, fs) {
  let verified;
  try { verified = await readSafeRegularFileSnapshot(root, filePath, { fs }); }
  catch (error) {
    if (error.code === 'ENOENT') throw new GatedLoopError('BASELINE_PATH_INVALID', `Baseline input does not exist: ${filePath}`);
    throw error;
  }
  const { bytes, snapshot } = verified;
  const text = purpose === 'baseline' ? decode(bytes, filePath) : decodeSupportingSource(bytes);
  return {
    path: filePath,
    purpose,
    bytes,
    text,
    snapshot,
    sha256: sha256Bytes(bytes),
    lines: text === undefined ? [] : text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split('\n').map((lineText, index) => ({
      file: filePath, line: index + 1, text: lineText,
    })),
  };
}

export async function readBaselineSources({ root, baseline, sources = [], fs = fsPromises } = {}) {
  if (typeof root !== 'string' || root.length === 0) throw new GatedLoopError('BASELINE_ROOT_INVALID', 'Project root is required');
  if (!Array.isArray(sources)) throw new GatedLoopError('BASELINE_SOURCE_INVALID', 'sources must be an array');
  const baselinePath = normalizeBaselineInputPath(baseline);
  if (!/\.md$/i.test(baselinePath)) throw new GatedLoopError('BASELINE_PATH_INVALID', 'Baseline input must be a Markdown file');
  const sourcePaths = sources.map(normalizeBaselineInputPath);
  const allPaths = [baselinePath, ...sourcePaths];
  const canonicalKeys = allPaths.map((entry) => entry.toLowerCase());
  if (new Set(canonicalKeys).size !== canonicalKeys.length) {
    throw new GatedLoopError('BASELINE_SOURCE_INVALID', 'Baseline input paths must be unique');
  }

  const baselineEntry = await readEntry(root, baselinePath, 'baseline', fs);
  const sourceEntries = [];
  for (const sourcePath of [...sourcePaths].sort()) sourceEntries.push(await readEntry(root, sourcePath, 'source', fs));
  const entries = [baselineEntry, ...sourceEntries];

  const identities = new Set();
  for (const entry of entries) {
    if (entry.snapshot.ino !== 0n && entry.snapshot.ino !== 0) {
      const identity = `${entry.snapshot.dev}:${entry.snapshot.ino}`;
      if (identities.has(identity)) throw new GatedLoopError('BASELINE_SOURCE_INVALID', 'Baseline inputs must identify distinct files');
      identities.add(identity);
    }
  }

  for (const entry of entries) {
    const verified = await readSafeRegularFileSnapshot(root, entry.path, { fs });
    if (!sameFileSnapshot(entry.snapshot, verified.snapshot) || sha256Bytes(verified.bytes) !== entry.sha256) {
      throw new GatedLoopError('PATH_FILE_CHANGED', `File changed while sources were being read: ${entry.path}`);
    }
  }

  const files = entries.map(({ path: filePath, sha256, purpose }) => ({ path: filePath, sha256, purpose }));
  const manifest = {
    schemaVersion: BASELINE_SCHEMA_VERSION,
    generatorVersion: BASELINE_GENERATOR_VERSION,
    files,
    fingerprint: sourceManifestFingerprint(files),
  };
  return { baseline: baselineEntry, sources: sourceEntries, entries, manifest };
}
