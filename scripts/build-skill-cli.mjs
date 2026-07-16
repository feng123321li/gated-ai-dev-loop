import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const repositoryRoot = fileURLToPath(new URL('..', import.meta.url));
const defaultOutfile = path.join(
  repositoryRoot,
  'skills',
  'hierarchical-delivery-governance',
  'scripts',
  'hdg.mjs',
);

export async function buildSkillCli({ outfile = defaultOutfile } = {}) {
  await mkdir(path.dirname(outfile), { recursive: true });
  await build({
    entryPoints: [path.join(repositoryRoot, 'scripts', 'skill-cli-entry.mjs')],
    outfile,
    bundle: true,
    platform: 'node',
    format: 'esm',
    target: 'node20',
    legalComments: 'none',
    banner: {
      js: "#!/usr/bin/env node\nimport { createRequire as __createRequire } from 'node:module';\nconst require = __createRequire(import.meta.url);",
    },
  });
  return outfile;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const outfile = await buildSkillCli();
  process.stdout.write(`Built Skill controller: ${outfile}\n`);
}
