import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { installSkill, parseArgs, resolveTargets } from '../../scripts/install-skill.mjs';

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-skill-install-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const sourceDir = path.join(root, 'source');
  await mkdir(path.join(sourceDir, 'references'), { recursive: true });
  await writeFile(path.join(sourceDir, 'SKILL.md'), '---\nname: hierarchical-delivery-governance\ndescription: test\n---\n');
  await writeFile(path.join(sourceDir, 'references', 'guide.md'), '# guide\n');
  return { root, sourceDir };
}

test('参数解析使用安全默认值并拒绝无效组合', () => {
  assert.deepEqual(parseArgs([]), { target: 'both', scope: 'user', dryRun: false, force: false });
  assert.deepEqual(parseArgs(['--target', 'claude', '--scope', 'project', '--project-root', 'repo', '--dry-run']), {
    target: 'claude', scope: 'project', projectRoot: 'repo', dryRun: true, force: false,
  });
  assert.throws(() => parseArgs(['--target', 'other']), /--target/);
  assert.throws(() => parseArgs(['--project-root', 'repo']), /--scope project/);
});

test('用户级目标分别使用 Codex 和 Claude 的发现目录', () => {
  const home = path.resolve('test-home');
  const codexHome = path.resolve('test-codex-home');
  const targets = resolveTargets({ target: 'both', scope: 'user' }, {
    home, env: { CODEX_HOME: codexHome }, cwd: path.resolve('test-repo'),
  });
  assert.equal(targets[0].destination, path.join(codexHome, 'skills', 'hierarchical-delivery-governance'));
  assert.equal(targets[1].destination, path.join(home, '.claude', 'skills', 'hierarchical-delivery-governance'));
});

test('项目级 Codex 使用 .agents，Claude 使用 .claude', () => {
  const repo = path.resolve('example-repo');
  const targets = resolveTargets({ target: 'both', scope: 'project', projectRoot: repo });
  assert.equal(targets[0].destination, path.join(repo, '.agents', 'skills', 'hierarchical-delivery-governance'));
  assert.equal(targets[1].destination, path.join(repo, '.claude', 'skills', 'hierarchical-delivery-governance'));
});

test('dry-run 不创建目标目录', async (t) => {
  const { root, sourceDir } = await fixture(t);
  const home = path.join(root, 'home');
  const result = await installSkill({ target: 'both', scope: 'user', dryRun: true, force: false }, { sourceDir, home, env: {} });
  assert.deepEqual(result.results.map(({ action }) => action), ['create', 'create']);
  await assert.rejects(() => readFile(path.join(home, '.claude', 'skills', 'hierarchical-delivery-governance', 'SKILL.md')), { code: 'ENOENT' });
});

test('同时安装完整 Skill，并默认拒绝覆盖', async (t) => {
  const { root, sourceDir } = await fixture(t);
  const home = path.join(root, 'home');
  const options = { target: 'both', scope: 'user', dryRun: false, force: false };
  const result = await installSkill(options, { sourceDir, home, env: {} });
  assert.deepEqual(result.results.map(({ action }) => action), ['created', 'created']);
  assert.equal(await readFile(path.join(home, '.codex', 'skills', 'hierarchical-delivery-governance', 'references', 'guide.md'), 'utf8'), '# guide\n');
  assert.equal(await readFile(path.join(home, '.claude', 'skills', 'hierarchical-delivery-governance', 'SKILL.md'), 'utf8'), '---\nname: hierarchical-delivery-governance\ndescription: test\n---\n');
  await assert.rejects(() => installSkill(options, { sourceDir, home, env: {} }), /--force/);
});

test('--force 原子替换已有安装', async (t) => {
  const { root, sourceDir } = await fixture(t);
  const home = path.join(root, 'home');
  const runtime = { sourceDir, home, env: {} };
  await installSkill({ target: 'claude', scope: 'user', dryRun: false, force: false }, runtime);
  await writeFile(path.join(sourceDir, 'SKILL.md'), 'new version\n');
  const result = await installSkill({ target: 'claude', scope: 'user', dryRun: false, force: true }, runtime);
  assert.equal(result.results[0].action, 'replaced');
  assert.equal(await readFile(path.join(home, '.claude', 'skills', 'hierarchical-delivery-governance', 'SKILL.md'), 'utf8'), 'new version\n');
});
