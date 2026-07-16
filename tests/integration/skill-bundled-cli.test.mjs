import test from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import { tmpdir } from 'node:os';

import { issueTaskDefinition } from '../helpers/work-item-definitions.mjs';
import { buildSkillCli } from '../../scripts/build-skill-cli.mjs';

const execFileAsync = promisify(execFile);
const sourceSkill = path.resolve('skills', 'hierarchical-delivery-governance');

test('a copied Skill folder carries a self-contained governance controller', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'hierarchical-skill-controller-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const installedSkill = path.join(root, 'installed-skill');
  const workspace = path.join(root, 'workspace');
  await cp(sourceSkill, installedSkill, { recursive: true });
  await mkdir(workspace);

  const controller = path.join(installedSkill, 'scripts', 'hdg.mjs');
  const controllerSource = await readFile(controller, 'utf8');
  assert.ok(Buffer.byteLength(controllerSource) < 160_000);
  assert.doesNotMatch(controllerSource, /The historical start\/prepare\/freeze commands are v1 compatibility surfaces/);
  const help = await execFileAsync(process.execPath, [controller, '--help'], { cwd: workspace });
  assert.match(help.stdout, /ready-tasks --item <root-or-subtree-id>/);
  await assert.rejects(
    () => execFileAsync(process.execPath, [controller, 'route', 'legacy'], { cwd: workspace }),
    ({ stderr }) => /UNKNOWN_COMMAND/.test(stderr),
  );

  const definitionPath = path.join(workspace, 'task.json');
  await writeFile(definitionPath, `${JSON.stringify(issueTaskDefinition({ parentId: null }))}\n`);
  const prepared = await execFileAsync(process.execPath, [
    controller,
    'prepare-item',
    '--definition',
    'task.json',
    '--host-runtime',
    'claude',
    '--json',
  ], { cwd: workspace });
  assert.equal(JSON.parse(prepared.stdout).result.id, 't-issue-token');
  const registry = JSON.parse(await readFile(path.join(
    workspace,
    '.hierarchical-delivery-governance',
    'work-item-registry.json',
  ), 'utf8'));
  assert.equal(registry.workItems[0].parentId, null);
});

test('the committed Skill controller is rebuilt from the current runtime sources', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'hierarchical-skill-bundle-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const rebuilt = path.join(root, 'hdg.mjs');
  await buildSkillCli({ outfile: rebuilt });
  assert.equal(
    await readFile(rebuilt, 'utf8'),
    await readFile(path.join(sourceSkill, 'scripts', 'hdg.mjs'), 'utf8'),
  );
});
