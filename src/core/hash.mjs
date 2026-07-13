import { createHash } from 'node:crypto';
import path from 'node:path';
import { GatedLoopError } from './errors.mjs';

export function canonicalRelativePath(value) {
  const portable = String(value).replaceAll('\\', '/');
  const normalized = path.posix.normalize(portable).replace(/^\.\//, '');
  if (normalized === '..' || normalized.startsWith('../') || path.posix.isAbsolute(normalized)) {
    throw new GatedLoopError('PATH_OUTSIDE_ROOT', `Path escapes root: ${value}`);
  }
  return normalized;
}

export function sha256Bytes(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

export function manifestFingerprint(entries) {
  const canonical = entries.map(({ path: filePath, sha256 }) => ({ path: canonicalRelativePath(filePath), sha256 }))
    .sort((a, b) => a.path.localeCompare(b.path));
  return sha256Bytes(Buffer.from(JSON.stringify(canonical), 'utf8'));
}
