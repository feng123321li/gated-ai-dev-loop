import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runCli } from '../../src/cli/main.mjs';
import { runHierarchicalCli } from '../../src/cli/hierarchical.mjs';
import { redact, renderJson } from '../../src/cli/output.mjs';

async function invoke(argv) { const out = []; const err = []; const exitCode = await runCli(argv, { stdout: (s) => out.push(s), stderr: (s) => err.push(s) }); return { exitCode, out: out.join(''), err: err.join('') }; }
async function invokeHierarchical(argv) { const out = []; const err = []; const exitCode = await runHierarchicalCli(argv, { stdout: (s) => out.push(s), stderr: (s) => err.push(s) }); return { exitCode, out: out.join(''), err: err.join('') }; }

test('help lists only implemented commands', async () => {
  const result = await invoke(['--help']);
  assert.equal(result.exitCode, 0);
  assert.match(result.out, /^Usage: gated-loop <command> \[options\]/);
  for (const command of ['route', 'start', 'prepare', 'freeze', 'self-check', 'accept']) assert.match(result.out, new RegExp(`\\b${command}\\b`));
  for (const command of ['install', 'doctor', 'develop', 'review']) assert.doesNotMatch(result.out, new RegExp(`\\b${command}\\b`));
  for (const command of ['prepare-item', 'promote-item', 'ready-tasks']) assert.doesNotMatch(result.out, new RegExp(`\\b${command}\\b`));
});

test('unknown and unimplemented commands have stable errors', async () => {
  assert.deepEqual(await invoke(['wat']), { exitCode: 1, out: '', err: 'ERROR UNKNOWN_COMMAND: Unknown command: wat\n' });
  assert.deepEqual(await invoke(['install']), { exitCode: 1, out: '', err: 'ERROR UNKNOWN_COMMAND: Unknown command: install\n' });
});

test('missing option values, unknown options, and duplicates are rejected', async () => {
  assert.match((await invoke(['prepare', '--baseline'])).err, /OPTION_VALUE_REQUIRED/);
  assert.match((await invoke(['prepare', '--wat'])).err, /UNKNOWN_OPTION/);
  assert.match((await invoke(['prepare', '--baseline', 'x', '--baseline', 'y'])).err, /DUPLICATE_OPTION/);
});

test('parser requires exactly one command and rejects separators and extra positionals', async () => {
  assert.match((await invoke(['freeze', 'extra'])).err, /UNKNOWN_OPTION/);
  assert.match((await invoke(['freeze', '--', '--json'])).err, /UNKNOWN_OPTION/);
  assert.match((await invoke(['freeze', '--task', 'one', 'review'])).err, /UNKNOWN_OPTION/);
});

test('option values are consumed positionally even when repeated elsewhere', async () => {
  const result = await invoke(['freeze', '--task', 'status', '--dogfood']);
  assert.match(result.err, /CONFIRMATION_REQUIRED/);
  assert.doesNotMatch(result.err, /DUPLICATE_OPTION|UNKNOWN_OPTION/);
});

test('json errors are exactly one recursively redacted object', async () => {
  const result = await invoke(['wat', '--json']);
  assert.equal(result.out, '');
  const lines = result.err.trim().split('\n');
  assert.equal(lines.length, 1);
  assert.deepEqual(JSON.parse(lines[0]), { ok: false, error: { code: 'UNKNOWN_COMMAND', message: 'Unknown command: wat', details: {} } });
});

test('recursive redaction covers sensitive keys and streams', () => {
  const value = redact({ token: 'x', nested: { API_KEY: 'y', stdout: 'z', safe: 1 }, list: [{ password: 'p', env: { X: 'x' } }] });
  assert.deepEqual(value, { token: '[REDACTED]', nested: { API_KEY: '[REDACTED]', stdout: '[REDACTED]', safe: 1 }, list: [{ password: '[REDACTED]', env: '[REDACTED]' }] });
  assert.equal(renderJson({ ok: true }), '{"ok":true}\n');
});

test('package exposes only the hdg executable and rejects old workflow commands', async () => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
  assert.equal(manifest.name, 'hierarchical-delivery-governance');
  assert.deepEqual(manifest.bin, { hdg: 'bin/hdg.mjs' });
  assert.deepEqual(await readdir(path.join(root, 'bin')), ['hdg.mjs']);
  await assert.rejects(readdir(path.join(root, 'templates')), { code: 'ENOENT' });
  for (const command of ['route', 'start', 'prepare', 'freeze', 'self-check', 'accept', 'retry-task']) {
    assert.match((await invokeHierarchical([command])).err, /UNKNOWN_COMMAND/);
  }
  assert.match((await invoke(['prepare-item'])).err, /UNKNOWN_COMMAND/);
  const hierarchicalHelp = (await invokeHierarchical(['--help'])).out;
  assert.match(hierarchicalHelp, /promote-item/);
  assert.match(hierarchicalHelp, /approve-item --definition <file\|->/);
  assert.match(hierarchicalHelp, /accept-item --item <id> --evidence <file\|->/);
  assert.match((await invokeHierarchical(['ready-tasks', '--project', 'd-example'])).err, /UNKNOWN_OPTION/);
  const smoke = spawnSync(process.execPath, [path.join(root, 'bin', 'hdg.mjs'), '--help'], { encoding: 'utf8' });
  assert.equal(smoke.status, 0);
  assert.match(smoke.stdout, /^Usage: hdg <command>/);
  assert.equal(smoke.stderr, '');
});
