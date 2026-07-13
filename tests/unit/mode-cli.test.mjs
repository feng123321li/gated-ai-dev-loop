import test from 'node:test';
import assert from 'node:assert/strict';
import * as fsPromises from 'node:fs/promises';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { COMMANDS, runCli } from '../../src/cli/main.mjs';

const safeRisks = () => ({
  loadBearing: false, breaking: false, migrations: false, dependencyChange: false,
  newDependency: false, externalContract: false, permissions: false, authentication: false,
  stateMachine: false, transaction: false, concurrency: false, idempotency: false,
  unresolvedOptions: 0, thresholdDecision: false, fileCountExceeded: false, impactKnown: true,
});
const signals = (patch = {}) => ({
  description: 'file description is not authoritative',
  modifiesFiles: ['src/input.mjs'],
  writesFiles: true,
  loadBearing: false,
  breaking: false,
  migrations: false,
  dependencyChange: false,
  newDependency: false,
  externalContract: false,
  permissions: false,
  authentication: false,
  stateMachine: false,
  transaction: false,
  concurrency: false,
  idempotency: false,
  unresolvedOptions: 0,
  thresholdDecision: false,
  impactKnown: true,
  ...patch,
});
const brief = () => ({
  goal: 'Show a clear empty-value message.',
  scope: ['src/input.mjs'],
  acceptance: { outcomes: ['Empty submission displays "Value is required".'], testCommands: [['node', '--test', 'tests/unit/input.test.mjs']] },
  risks: safeRisks(),
});

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-cli-mode-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function putJson(root, name, value) {
  await writeFile(path.join(root, name), `${JSON.stringify(value)}\n`);
  return name;
}

async function invoke(argv, context = {}) {
  const out = [];
  const err = [];
  let modelCalls = 0;
  const exitCode = await runCli(argv, {
    stdout: (value) => out.push(value),
    stderr: (value) => err.push(value),
    invokeModel: () => { modelCalls++; },
    ...context,
  });
  return { exitCode, out: out.join(''), err: err.join(''), modelCalls };
}

function swapStructuredInputOnOpen(expected, replacement) {
  let replacementRead = false;
  return {
    fs: new Proxy(fsPromises, {
      get(target, property, receiver) {
        if (property === 'open') {
          return async (value, flags) => {
            const handle = await target.open(String(value) === expected ? replacement : value, flags);
            return {
              stat: (...args) => handle.stat(...args),
              readFile: (...args) => { replacementRead = true; return handle.readFile(...args); },
              close: () => handle.close(),
            };
          };
        }
        return Reflect.get(target, property, receiver);
      },
    }),
    wasReplacementRead: () => replacementRead,
  };
}

test('CLI registry exposes route and start', () => {
  assert.equal(COMMANDS.includes('route'), true);
  assert.equal(COMMANDS.includes('start'), true);
});

test('CLI route reads structured signals, treats description as context only, and remains read-only', async (t) => {
  const root = await fixture(t);
  const signalFile = await putJson(root, 'signals.json', signals());
  const before = await readdir(root);
  const result = await invoke(['route', 'authentication migration words are only context', '--signals', signalFile, '--json'], { cwd: root });
  assert.equal(result.exitCode, 0);
  assert.equal(result.err, '');
  const payload = JSON.parse(result.out);
  assert.equal(payload.ok, true);
  assert.equal(payload.result.mode, 'light');
  assert.equal(payload.result.evaluatedInputs.description, 'authentication migration words are only context');
  assert.deepEqual(await readdir(root), before);
  assert.equal(result.modelCalls, 0);
});

test('CLI route without signals conservatively returns Full unknown impact', async (t) => {
  const root = await fixture(t);
  const result = await invoke(['route', 'just a typo', '--json'], { cwd: root });
  const payload = JSON.parse(result.out);
  assert.equal(payload.result.mode, 'full');
  assert.deepEqual(payload.result.reasons, ['IMPACT_UNKNOWN', 'WRITE_PATHS_UNKNOWN']);
  assert.deepEqual(await readdir(root), []);
});

test('CLI requested Light cannot bypass structured hard signals', async (t) => {
  const root = await fixture(t);
  const signalFile = await putJson(root, 'signals.json', signals({ breaking: true }));
  const result = await invoke(['route', 'small change', '--signals', signalFile, '--mode', 'light', '--json'], { cwd: root });
  assert.equal(result.exitCode, 1);
  assert.equal(result.out, '');
  const payload = JSON.parse(result.err);
  assert.equal(payload.error.code, 'MODE_ESCALATION_REQUIRED');
  assert.deepEqual(payload.error.details.reasons, ['BREAKING_CHANGE']);
});

test('CLI start leaves None artifact-free', async (t) => {
  const root = await fixture(t);
  const signalFile = await putJson(root, 'signals.json', signals({ modifiesFiles: [], writesFiles: false }));
  const result = await invoke(['start', 'answer a question', '--signals', signalFile, '--json'], {
    cwd: root,
    generateTaskId: () => { throw new Error('None must not allocate a task ID'); },
  });
  const payload = JSON.parse(result.out);
  assert.equal(payload.result.route.mode, 'none');
  assert.deepEqual(payload.result.artifacts, []);
  assert.deepEqual((await readdir(root)).sort(), ['signals.json']);
  assert.equal(result.modelCalls, 0);
});

test('CLI start persists Full mode and host without creating framework-specific paths', async (t) => {
  const root = await fixture(t);
  const signalFile = await putJson(root, 'signals.json', signals({ authentication: true }));
  const result = await invoke([
    'start', 'change authentication', '--signals', signalFile,
    '--host-runtime', 'claude', '--json',
  ], { cwd: root, now: () => '2026-07-11T00:00:00.000Z', generateTaskId: () => 'generated-auth-change' });
  const payload = JSON.parse(result.out);
  assert.equal(payload.result.nextAction, 'prepare');
  assert.equal(payload.result.authority, 'generic-baseline');
  assert.equal(payload.result.task, 'generated-auth-change');
  const mode = JSON.parse(await readFile(path.join(root, '.ai-dev-loop', 'generated-auth-change', 'mode.json'), 'utf8'));
  assert.equal(mode.hostRuntime, 'claude');
  assert.equal(mode.evaluatedInputs.description, 'change authentication');
  assert.equal((await readdir(root)).includes('requirements'), false);
  assert.equal(result.modelCalls, 0);
});

test('CLI start previews and then freezes an injected Light brief', async (t) => {
  const root = await fixture(t);
  const signalFile = await putJson(root, 'signals.json', signals());
  const briefFile = await putJson(root, 'brief.json', brief());
  const preview = await invoke([
    'start', 'fix empty input', '--signals', signalFile, '--brief', briefFile, '--json',
  ], { cwd: root, generateTaskId: () => { throw new Error('unconfirmed Light must not allocate a task ID'); } });
  assert.equal(JSON.parse(preview.out).result.nextAction, 'confirm');
  assert.equal((await readdir(root)).includes('.ai-dev-loop'), false);
  const frozen = await invoke([
    'start', 'fix empty input', '--signals', signalFile, '--brief', briefFile,
    '--host-runtime', 'codex', '--confirmed', '--json',
  ], { cwd: root, generateTaskId: () => 'generated-empty-input' });
  const frozenPayload = JSON.parse(frozen.out);
  assert.equal(frozenPayload.result.nextAction, 'develop');
  assert.equal(frozenPayload.result.task, 'generated-empty-input');
  assert.deepEqual((await readdir(path.join(root, '.ai-dev-loop', 'generated-empty-input'))).sort(), [
    'acceptance.json', 'decision-log.md', 'handoff-to-claude.md', 'light-brief.md',
    'mode.json', 'source-manifest.json', 'state.json', 'tasks.json',
  ]);
  assert.equal(preview.modelCalls + frozen.modelCalls, 0);
});

test('CLI start without a task or brief returns an artifact-free Light confirmation action', async (t) => {
  const root = await fixture(t);
  const signalFile = await putJson(root, 'signals.json', signals());
  const result = await invoke(['start', 'fix empty input', '--signals', signalFile, '--json'], {
    cwd: root,
    generateTaskId: () => { throw new Error('unconfirmed Light must not allocate a task ID'); },
  });

  assert.equal(result.exitCode, 0);
  const payload = JSON.parse(result.out).result;
  assert.equal(payload.route.mode, 'light');
  assert.equal(payload.nextAction, 'confirm');
  assert.equal(payload.brief, null);
  assert.deepEqual(payload.artifacts, []);
  assert.equal(Object.hasOwn(payload, 'task'), false);
  assert.deepEqual(await readdir(root), ['signals.json']);
  assert.equal(result.modelCalls, 0);
});

test('CLI mode options are strict and structured files cannot escape the project', async (t) => {
  const root = await fixture(t);
  assert.match((await invoke(['route', '--json'], { cwd: root })).err, /DESCRIPTION_REQUIRED/);
  assert.match((await invoke(['route', 'task', '--mode', 'none', '--json'], { cwd: root })).err, /OPTION_VALUE_INVALID/);
  assert.match((await invoke(['start', 'task', '--task', 'x', '--host-runtime', 'other', '--json'], { cwd: root })).err, /OPTION_VALUE_INVALID/);
  assert.match((await invoke(['route', 'task', '--brief', 'brief.json', '--json'], { cwd: root })).err, /UNKNOWN_OPTION/);
  assert.match((await invoke(['route', 'task', '--signals', '..\\outside.json', '--json'], { cwd: root })).err, /PATH_OUTSIDE_ROOT|MODE_INPUT_READ/);
});

test('CLI never reads forbidden sensitive signal or brief paths', async (t) => {
  const root = await fixture(t);
  await putJson(root, '.env', signals());
  await putJson(root, 'application-production.json', brief());
  const signalResult = await invoke(['route', 'task', '--signals', '.env', '--json'], { cwd: root });
  assert.match(signalResult.err, /INPUT_PATH_FORBIDDEN/);
  assert.doesNotMatch(signalResult.err, /file description|modifiesFiles/);
  const briefResult = await invoke([
    'start', 'task', '--task', 'x', '--signals', '-', '--brief', 'application-production.json', '--json',
  ], { cwd: root, stdin: JSON.stringify(signals()) });
  assert.match(briefResult.err, /INPUT_PATH_FORBIDDEN/);
});

test('CLI rejects consuming both signals and brief from the same stdin', async (t) => {
  const root = await fixture(t);
  const result = await invoke([
    'start', 'task', '--task', 'x', '--signals', '-', '--brief', '-', '--confirmed', '--json',
  ], { cwd: root, stdin: JSON.stringify(signals()) });
  assert.match(result.err, /INPUT_STDIN_CONFLICT/);
});

test('CLI rejects a structured-signal check/open swap before reading replacement bytes', async (t) => {
  const root = await fixture(t);
  const expected = path.join(root, 'signals.json');
  const replacement = path.join(root, 'replacement.json');
  await putJson(root, 'signals.json', signals());
  await putJson(root, 'replacement.json', signals({ authentication: true }));
  const swapped = swapStructuredInputOnOpen(expected, replacement);

  const result = await invoke(['route', 'task', '--signals', 'signals.json', '--json'], { cwd: root, fs: swapped.fs });

  assert.equal(result.exitCode, 1);
  assert.equal(JSON.parse(result.err).error.code, 'PATH_FILE_CHANGED');
  assert.equal(swapped.wasReplacementRead(), false);
  assert.equal(result.modelCalls, 0);
  assert.equal((await readdir(root)).includes('.ai-dev-loop'), false);
});

test('CLI uses the same verified-handle read for a structured Light brief', async (t) => {
  const root = await fixture(t);
  const expected = path.join(root, 'brief.json');
  const replacement = path.join(root, 'replacement-brief.json');
  await putJson(root, 'brief.json', brief());
  await putJson(root, 'replacement-brief.json', { goal: 'replacement' });
  const swapped = swapStructuredInputOnOpen(expected, replacement);

  const result = await invoke([
    'start', 'fix empty input', '--signals', '-', '--brief', 'brief.json', '--json',
  ], { cwd: root, fs: swapped.fs, stdin: JSON.stringify(signals()) });

  assert.equal(result.exitCode, 1);
  assert.equal(JSON.parse(result.err).error.code, 'PATH_FILE_CHANGED');
  assert.equal(swapped.wasReplacementRead(), false);
  assert.equal(result.modelCalls, 0);
  assert.equal((await readdir(root)).includes('.ai-dev-loop'), false);
});
