import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { loadConfig } from '../../src/config/load-config.mjs';

async function fixture(t, yaml) {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-config-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (yaml !== undefined) await writeFile(path.join(root, '.gated-loop.yml'), yaml);
  return root;
}

test('config supplies secure defaults', async (t) => {
  const config = await loadConfig(await fixture(t));
  assert.deepEqual(config, { version: 1, runtimeDir: '.ai-dev-loop', maxRepairLoops: 3, tools: { claude: 'claude', codex: 'codex', git: 'git' }, protectedPaths: ['.ai-dev-loop/**', '.git/**'], forbiddenPaths: ['.env*', '**/.env*', '**/*production*', '**/*preproduction*'] });
});

test('config accepts explicit valid values', async (t) => {
  const root = await fixture(t, 'version: 1\nruntimeDir: .loop\nmaxRepairLoops: 2\ntools:\n  claude: cc\n  codex: cx\n  git: gg\nprotectedPaths: [.loop/**]\nforbiddenPaths: [.env*]\n');
  assert.equal((await loadConfig(root)).tools.claude, 'cc');
});

for (const [name, yaml, code] of [
  ['unknown keys', 'version: 1\nwat: true\n', 'CONFIG_UNKNOWN_KEY'],
  ['wrong types', 'version: 1\nmaxRepairLoops: many\n', 'CONFIG_INVALID_TYPE'],
  ['wrong version', 'version: 2\n', 'CONFIG_VERSION'],
  ['escaping runtime paths', 'version: 1\nruntimeDir: ../away\n', 'CONFIG_PATH_OUTSIDE_ROOT'],
]) test(`config rejects ${name}`, async (t) => {
  const root = await fixture(t, yaml);
  await assert.rejects(() => loadConfig(root), { code });
});

for (const key of ['protectedPaths', 'forbiddenPaths']) {
  for (const [name, pattern] of [
    ['absolute patterns', '/etc/**'],
    ['drive-absolute patterns', 'C:\\\\secrets\\\\**'],
    ['UNC patterns', '\\\\server\\\\share\\\\**'],
    ['parent segments', 'safe/../secret/**'],
    ['unsafe non-glob prefixes', 'safe:C/**'],
    ['colon suffixes after glob tokens', 'safe/**:stream'],
    ['NUL suffixes after glob tokens', 'safe/**\0'],
  ]) test(`config rejects ${name} in ${key}`, async (t) => {
    const root = await fixture(t, `version: 1\n${key}:\n  - ${JSON.stringify(pattern)}\n`);
    await assert.rejects(() => loadConfig(root), { code: 'INVALID_CONFIG' });
  });
}

test('config keeps valid glob patterns', async (t) => {
  const root = await fixture(t, 'version: 1\nprotectedPaths: [".ai-dev-loop/**", "src/**/safe?.mjs", "safe/**/[a-z].mjs"]\nforbiddenPaths: [".env*", "**/.env*"]\n');
  const config = await loadConfig(root);
  assert.deepEqual(config.protectedPaths, ['.ai-dev-loop/**', 'src/**/safe?.mjs', 'safe/**/[a-z].mjs']);
  assert.deepEqual(config.forbiddenPaths, ['.env*', '**/.env*']);
});
