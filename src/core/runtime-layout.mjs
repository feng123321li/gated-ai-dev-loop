import path from 'node:path';

import { GatedLoopError } from './errors.mjs';

export const MUTABLE_RUNTIME_ENTRIES = Object.freeze([
  'development-overview.md',
  'progress.md',
  'final-acceptance-report.md',
  'rounds',
]);

export async function validateMutableRuntimeEntries(target, names, { fs } = {}) {
  const mutable = names.filter((name) => MUTABLE_RUNTIME_ENTRIES.includes(name));
  for (const name of mutable) {
    const stat = await fs.lstat(path.join(target, name));
    const valid = name === 'rounds' ? stat.isDirectory() : stat.isFile();
    if (!valid || stat.isSymbolicLink()) {
      throw new GatedLoopError('RUNTIME_ARTIFACT_INVALID', `Runtime artifact has an invalid type: ${name}`);
    }
  }
  return names.filter((name) => !MUTABLE_RUNTIME_ENTRIES.includes(name));
}
