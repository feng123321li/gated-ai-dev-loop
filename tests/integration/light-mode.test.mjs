import test from 'node:test';
import assert from 'node:assert/strict';
import * as fsPromises from 'node:fs/promises';
import { lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { manifestFingerprint, sha256Bytes } from '../../src/core/hash.mjs';
import { classifyMode } from '../../src/mode/classify.mjs';
import { freezeLightTask as freezeLightTaskRaw, readLightPackage } from '../../src/light/freeze.mjs';
import { canonicalJson } from '../../src/baseline/sources.mjs';
import { persistFullMode as persistFullModeRaw } from '../../src/mode/persist.mjs';
import { routeTask } from '../../src/commands/route.mjs';
import { startTask as startTaskRaw } from '../../src/commands/start.mjs';

const freezeLightTask = (options) => freezeLightTaskRaw({ hostRuntime: 'codex', ...options });
const persistFullMode = (options) => persistFullModeRaw({ hostRuntime: 'codex', ...options });
const startTask = (options) => startTaskRaw({ hostRuntime: 'codex', ...options });

test('existing Light packages with the legacy handoff filename remain readable', async (t) => {
  const root = await rootFixture(t);
  const task = 'legacy-light-handoff';
  await startTask({ root, task, signals: signals(), brief: brief(), confirmed: true });
  const target = path.join(root, '.ai-dev-loop', task);
  await fsPromises.rename(
    path.join(target, 'development-handoff.md'),
    path.join(target, 'handoff-to-claude.md'),
  );
  const statePath = path.join(target, 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.artifactHashes['handoff-to-claude.md'] = state.artifactHashes['development-handoff.md'];
  delete state.artifactHashes['development-handoff.md'];
  const { frozenFingerprint: ignored, ...metadata } = state;
  state.frozenFingerprint = sha256Bytes(Buffer.from(canonicalJson(metadata), 'utf8'));
  await writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`);

  const existing = await readLightPackage({ root, task });
  assert.equal(existing.handoffName, 'handoff-to-claude.md');
  const repeated = await startTask({ root, task, signals: signals(), brief: brief(), confirmed: true });
  assert.equal(repeated.freeze.idempotent, true);
  assert.equal(repeated.freeze.artifacts.some((entry) => entry.endsWith('handoff-to-claude.md')), true);
});

const safeRisks = () => ({
  loadBearing: false, breaking: false, migrations: false, dependencyChange: false,
  newDependency: false, externalContract: false, permissions: false, authentication: false,
  stateMachine: false, transaction: false, concurrency: false, idempotency: false,
  unresolvedOptions: 0, thresholdDecision: false, fileCountExceeded: false, impactKnown: true,
});
const brief = (goal = 'Fix the empty-value message.') => ({
  goal,
  scope: ['src/input.mjs', 'tests/unit/input.test.mjs'],
  acceptance: {
    outcomes: ['Empty submission displays "Value is required".'],
    testCommands: [['node', '--test', 'tests/unit/input.test.mjs']],
  },
  risks: safeRisks(),
});
const signals = (patch = {}) => ({
  description: 'Fix the empty-value message', modifiesFiles: ['src/input.mjs', 'tests/unit/input.test.mjs'], writesFiles: true,
  loadBearing: false, breaking: false, migrations: false, dependencyChange: false, newDependency: false,
  externalContract: false, permissions: false, authentication: false, stateMachine: false, transaction: false,
  concurrency: false, idempotency: false, unresolvedOptions: 0, thresholdDecision: false, impactKnown: true,
  ...patch,
});

async function rootFixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-light-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function exists(value) {
  try { await lstat(value); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}

function pretendArtifactIsSymlink(name) {
  return {
    ...fsPromises,
    async lstat(value) {
      const stat = await fsPromises.lstat(value);
      if (path.basename(String(value)) !== name) return stat;
      return new Proxy(stat, {
        get(target, property, receiver) {
          if (property === 'isSymbolicLink') return () => true;
          return Reflect.get(target, property, receiver);
        },
      });
    },
  };
}

function pretendArtifactIdentityChanged(name) {
  let changedArtifactRead = false;
  return {
    fs: new Proxy(fsPromises, {
      get(target, property, receiver) {
        if (property === 'open') {
          return async (value, flags) => {
            const handle = await target.open(value, flags);
            if (path.basename(value) !== name) return handle;
            return {
              async stat(...args) {
                const stat = await handle.stat(...args);
                return new Proxy(stat, {
                  get(statTarget, statProperty, statReceiver) {
                    if (statProperty === 'ino') return typeof statTarget.ino === 'bigint' ? statTarget.ino + 1n : statTarget.ino + 1;
                    const result = Reflect.get(statTarget, statProperty, statReceiver);
                    return typeof result === 'function' ? result.bind(statTarget) : result;
                  },
                });
              },
              readFile(...args) { changedArtifactRead = true; return handle.readFile(...args); },
              close() { return handle.close(); },
            };
          };
        }
        return Reflect.get(target, property, receiver);
      },
    }),
    wasChangedArtifactRead: () => changedArtifactRead,
  };
}

test('freeze requires explicit confirmation and creates no artifacts without it', async (t) => {
  const root = await rootFixture(t);
  await assert.rejects(() => freezeLightTask({ root, task: 'empty-value', classification: classifyMode(signals()), brief: brief(), confirmed: false }), { code: 'CONFIRMATION_REQUIRED' });
  assert.equal(await exists(path.join(root, '.ai-dev-loop')), false);
});

test('freeze atomically creates a host-reviewed eight-file Light development handoff', async (t) => {
  const root = await rootFixture(t);
  const lightBrief = brief();
  lightBrief.acceptance.outcomes.push('Nonempty submission does not display the empty-value message.');
  const result = await freezeLightTask({
    root,
    task: 'empty-value',
    classification: classifyMode(signals()),
    brief: lightBrief,
    confirmed: true,
    now: () => '2026-07-11T00:00:00.000Z',
  });
  const artifactDir = path.join(root, '.ai-dev-loop', 'empty-value');
  assert.deepEqual((await readdir(artifactDir)).sort(), [
    'acceptance.json', 'decision-log.md', 'development-handoff.md', 'light-brief.md',
    'mode.json', 'source-manifest.json', 'state.json', 'tasks.json',
  ]);
  const mode = JSON.parse(await readFile(path.join(artifactDir, 'mode.json'), 'utf8'));
  const manifest = JSON.parse(await readFile(path.join(artifactDir, 'source-manifest.json'), 'utf8'));
  const state = JSON.parse(await readFile(path.join(artifactDir, 'state.json'), 'utf8'));
  const acceptance = JSON.parse(await readFile(path.join(artifactDir, 'acceptance.json'), 'utf8'));
  const tasks = JSON.parse(await readFile(path.join(artifactDir, 'tasks.json'), 'utf8'));
  const decisionLog = await readFile(path.join(artifactDir, 'decision-log.md'), 'utf8');
  const handoff = await readFile(path.join(artifactDir, 'development-handoff.md'), 'utf8');
  assert.equal(mode.mode, 'light');
  assert.equal(mode.hostRuntime, 'codex');
  assert.equal(mode.createdAt, '2026-07-11T00:00:00.000Z');
  assert.equal(mode.classifierVersion, 1);
  assert.deepEqual(Object.keys(manifest), ['version', 'files', 'fingerprint', 'inputFingerprint']);
  assert.deepEqual(manifest.files.map((entry) => entry.path), ['light-brief.md', 'mode.json']);
  assert.match(manifest.fingerprint, /^[a-f0-9]{64}$/);
  assert.match(manifest.inputFingerprint, /^[a-f0-9]{64}$/);
  assert.equal(state.schemaVersion, 1);
  assert.equal(Object.hasOwn(state, 'version'), false);
  assert.equal(state.task, 'empty-value');
  assert.equal(state.mode, 'light');
  assert.equal(state.stage, 'BASELINE_FROZEN');
  assert.equal(state.hostRuntime, 'codex');
  assert.equal(state.reviewer, 'codex');
  assert.equal(state.sourceFingerprint, manifest.fingerprint);
  assert.equal(state.inputFingerprint, manifest.inputFingerprint);
  assert.equal(state.updatedAt, '2026-07-11T00:00:00.000Z');
  assert.deepEqual(Object.keys(state.artifactHashes).sort(), [
    'acceptance.json', 'decision-log.md', 'development-handoff.md', 'light-brief.md',
    'mode.json', 'source-manifest.json', 'tasks.json',
  ]);
  assert.match(state.frozenFingerprint, /^[a-f0-9]{64}$/);
  assert.deepEqual(acceptance.acceptance, [
    {
      id: 'A-001',
      requirementIds: ['R-001'],
      expectedResult: 'Empty submission displays "Value is required".',
      trace: { file: 'light-brief.md', section: 'Acceptance', index: 1 },
    },
    {
      id: 'A-002',
      requirementIds: ['R-001'],
      expectedResult: 'Nonempty submission does not display the empty-value message.',
      trace: { file: 'light-brief.md', section: 'Acceptance', index: 2 },
    },
  ]);
  assert.deepEqual(tasks.tasks, [{
    id: 'T-001',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001', 'A-002'],
    text: 'Fix the empty-value message.',
    scope: ['src/input.mjs', 'tests/unit/input.test.mjs'],
    trace: { file: 'light-brief.md', section: 'Goal' },
  }]);
  assert.match(decisionLog, /Host review: codex\./);
  assert.match(decisionLog, /User confirmation: confirmed\./);
  assert.match(handoff, /Do not reanalyze/);
  assert.match(handoff, /\["node","--test","tests\/unit\/input\.test\.mjs"\]/);
  assert.equal(result.created, true);
  assert.equal(result.idempotent, false);
  assert.equal(await exists(path.join(root, 'requirements')), false);
});

test('freeze persists the explicit validated predevelopment host runtime without invoking it', async (t) => {
  const root = await rootFixture(t);
  let calls = 0;
  const result = await startTask({
    root,
    task: 'hosted-light',
    hostRuntime: 'claude',
    signals: signals(),
    brief: brief(),
    confirmed: true,
    invokeModel: () => { calls++; },
  });
  const artifactDir = path.join(root, '.ai-dev-loop', 'hosted-light');
  const mode = JSON.parse(await readFile(path.join(artifactDir, 'mode.json'), 'utf8'));
  const state = JSON.parse(await readFile(path.join(artifactDir, 'state.json'), 'utf8'));
  assert.equal(mode.hostRuntime, 'claude');
  assert.equal(state.hostRuntime, 'claude');
  assert.equal(state.reviewer, 'claude');
  assert.equal(result.hostRuntime, 'claude');
  assert.equal(result.freeze.hostRuntime, 'claude');
  assert.equal(calls, 0);
});

test('idempotent freeze rejects tampering of every generated Light handoff artifact', async (t) => {
  for (const artifact of ['acceptance.json', 'tasks.json', 'decision-log.md', 'development-handoff.md']) {
    const root = await rootFixture(t);
    const options = {
      root,
      task: `tamper-${artifact.split('.')[0]}`,
      hostRuntime: 'claude',
      classification: classifyMode(signals()),
      brief: brief(),
      confirmed: true,
    };
    await freezeLightTask(options);
    await writeFile(path.join(root, '.ai-dev-loop', options.task, artifact), 'tampered\n');
    await assert.rejects(() => freezeLightTask(options), { code: 'LIGHT_SOURCE_CHANGED' }, artifact);
  }
});

test('invalid host runtime fails before creating artifacts', async (t) => {
  const root = await rootFixture(t);
  await assert.rejects(() => startTask({ root, task: 'hosted-light', hostRuntime: 'Other Agent!', signals: signals(), brief: brief(), confirmed: true }), { code: 'HOST_RUNTIME_INVALID' });
  assert.deepEqual(await readdir(root), []);
});

test('writing workflows require an explicit truthful host while Light preview remains read-only', async (t) => {
  const root = await rootFixture(t);
  const preview = await startTaskRaw({ root, task: 'preview', signals: signals(), brief: brief() });
  assert.equal(preview.nextAction, 'confirm');
  await assert.rejects(
    () => freezeLightTaskRaw({ root, task: 'missing-light-host', classification: classifyMode(signals()), brief: brief(), confirmed: true }),
    { code: 'HOST_RUNTIME_REQUIRED' },
  );
  await assert.rejects(
    () => startTaskRaw({ root, task: 'missing-full-host', signals: signals({ breaking: true }) }),
    { code: 'HOST_RUNTIME_REQUIRED' },
  );
  assert.equal(await exists(path.join(root, '.ai-dev-loop')), false);
});

test('identical freeze is idempotent and preserves the original artifact bytes', async (t) => {
  const root = await rootFixture(t);
  const options = { root, task: 'empty-value', classification: classifyMode(signals()), brief: brief(), confirmed: true };
  const first = await freezeLightTask({ ...options, now: () => '2026-07-11T00:00:00.000Z' });
  const statePath = path.join(root, '.ai-dev-loop', 'empty-value', 'state.json');
  const before = await readFile(statePath, 'utf8');
  const second = await freezeLightTask({ ...options, now: () => '2030-01-01T00:00:00.000Z' });
  assert.equal(second.created, false);
  assert.equal(second.idempotent, true);
  assert.equal(second.sourceFingerprint, first.sourceFingerprint);
  assert.equal(await readFile(statePath, 'utf8'), before);
});

test('changed brief or route input fails closed without changing frozen artifacts', async (t) => {
  const root = await rootFixture(t);
  const classification = classifyMode(signals());
  await freezeLightTask({ root, task: 'empty-value', classification, brief: brief(), confirmed: true });
  const artifactDir = path.join(root, '.ai-dev-loop', 'empty-value');
  const before = await Promise.all((await readdir(artifactDir)).sort().map(async (name) => [name, await readFile(path.join(artifactDir, name), 'utf8')]));
  await assert.rejects(() => freezeLightTask({ root, task: 'empty-value', classification, brief: brief('A changed goal.'), confirmed: true }), { code: 'LIGHT_SOURCE_CHANGED' });
  await assert.rejects(() => freezeLightTask({ root, task: 'empty-value', classification: classifyMode(signals({ description: 'Changed description' })), brief: brief(), confirmed: true }), { code: 'LIGHT_SOURCE_CHANGED' });
  const after = await Promise.all((await readdir(artifactDir)).sort().map(async (name) => [name, await readFile(path.join(artifactDir, name), 'utf8')]));
  assert.deepEqual(after, before);
});

test('changing the frozen host runtime is a source change', async (t) => {
  const root = await rootFixture(t);
  const classification = classifyMode(signals());
  await freezeLightTask({ root, task: 'empty-value', hostRuntime: 'codex', classification, brief: brief(), confirmed: true });
  await assert.rejects(
    () => freezeLightTask({ root, task: 'empty-value', hostRuntime: 'claude', classification, brief: brief(), confirmed: true }),
    { code: 'LIGHT_SOURCE_CHANGED' },
  );
});

test('freeze rejects forged Light classification objects', async (t) => {
  const root = await rootFixture(t);
  const forged = classifyMode(signals());
  forged.evaluatedInputs.breaking = true;
  await assert.rejects(() => freezeLightTask({ root, task: 'forged', classification: forged, brief: brief(), confirmed: true }), { code: 'LIGHT_MODE_REQUIRED' });
  assert.deepEqual(await readdir(root), []);
});

test('freeze snapshots validated classification before its first await', async (t) => {
  const root = await rootFixture(t);
  const classification = classifyMode(signals());
  const pending = freezeLightTask({ root, task: 'snapshot-light', classification, brief: brief(), confirmed: true });
  classification.evaluatedInputs.breaking = true;
  classification.reasons.push('BREAKING_CHANGE');
  await pending;
  const mode = JSON.parse(await readFile(path.join(root, '.ai-dev-loop', 'snapshot-light', 'mode.json'), 'utf8'));
  assert.equal(mode.evaluatedInputs.breaking, false);
  assert.deepEqual(mode.reasons, ['LIGHT_ELIGIBLE']);
});

test('freeze requires brief scope to equal the classified write paths', async (t) => {
  const root = await rootFixture(t);
  const mismatchedBrief = brief();
  mismatchedBrief.scope = ['src/input.mjs'];
  await assert.rejects(() => freezeLightTask({
    root,
    task: 'scope-mismatch',
    classification: classifyMode(signals()),
    brief: mismatchedBrief,
    confirmed: true,
  }), { code: 'LIGHT_SCOPE_MISMATCH' });
  assert.deepEqual(await readdir(root), []);
});

test('idempotent freeze rejects consistently rehashed semantic tampering and extra artifacts', async (t) => {
  const root = await rootFixture(t);
  const options = { root, task: 'empty-value', classification: classifyMode(signals()), brief: brief(), confirmed: true };
  await freezeLightTask(options);
  const artifactDir = path.join(root, '.ai-dev-loop', 'empty-value');
  const modePath = path.join(artifactDir, 'mode.json');
  const manifestPath = path.join(artifactDir, 'source-manifest.json');
  const statePath = path.join(artifactDir, 'state.json');
  const mode = JSON.parse(await readFile(modePath, 'utf8'));
  mode.evaluatedInputs.description = 'tampered';
  const modeText = `${JSON.stringify(mode, null, 2)}\n`;
  await writeFile(modePath, modeText);
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  manifest.files[1].sha256 = sha256Bytes(Buffer.from(modeText));
  manifest.fingerprint = manifestFingerprint(manifest.files);
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.sourceFingerprint = manifest.fingerprint;
  await writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`);
  await assert.rejects(() => freezeLightTask(options), { code: 'LIGHT_SOURCE_CHANGED' });

  const otherRoot = await rootFixture(t);
  const otherOptions = { ...options, root: otherRoot };
  await freezeLightTask(otherOptions);
  await writeFile(path.join(otherRoot, '.ai-dev-loop', 'empty-value', 'unexpected.txt'), 'x');
  await assert.rejects(() => freezeLightTask(otherOptions), { code: 'LIGHT_SOURCE_CHANGED' });
});

test('idempotent freeze binds exact task identity and state metadata', async (t) => {
  const root = await rootFixture(t);
  const options = { root, task: 'bound-task', classification: classifyMode(signals()), brief: brief(), confirmed: true };
  await freezeLightTask(options);
  const statePath = path.join(root, '.ai-dev-loop', 'bound-task', 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.task = 'different-task';
  await writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`);
  await assert.rejects(() => freezeLightTask(options), { code: 'LIGHT_SOURCE_CHANGED' });

  const otherRoot = await rootFixture(t);
  const otherOptions = { ...options, root: otherRoot };
  await freezeLightTask(otherOptions);
  const otherStatePath = path.join(otherRoot, '.ai-dev-loop', 'bound-task', 'state.json');
  const otherState = JSON.parse(await readFile(otherStatePath, 'utf8'));
  otherState.unexpected = true;
  await writeFile(otherStatePath, `${JSON.stringify(otherState, null, 2)}\n`);
  await assert.rejects(() => freezeLightTask(otherOptions), { code: 'LIGHT_SOURCE_CHANGED' });

  const hostRoot = await rootFixture(t);
  const hostOptions = { ...options, root: hostRoot };
  await freezeLightTask(hostOptions);
  const artifactDir = path.join(hostRoot, '.ai-dev-loop', 'bound-task');
  const modePath = path.join(artifactDir, 'mode.json');
  const manifestPath = path.join(artifactDir, 'source-manifest.json');
  const hostStatePath = path.join(artifactDir, 'state.json');
  const mode = JSON.parse(await readFile(modePath, 'utf8'));
  mode.hostRuntime = null;
  const modeText = `${JSON.stringify(mode, null, 2)}\n`;
  await writeFile(modePath, modeText);
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  manifest.files[1].sha256 = sha256Bytes(Buffer.from(modeText));
  manifest.fingerprint = manifestFingerprint(manifest.files);
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  const hostState = JSON.parse(await readFile(hostStatePath, 'utf8'));
  hostState.sourceFingerprint = manifest.fingerprint;
  await writeFile(hostStatePath, `${JSON.stringify(hostState, null, 2)}\n`);
  await assert.rejects(() => freezeLightTask(hostOptions), { code: 'LIGHT_SOURCE_CHANGED' });
});

test('idempotent freeze rejects child artifact symlinks before reading', async (t) => {
  const root = await rootFixture(t);
  const options = { root, task: 'linked-light', classification: classifyMode(signals()), brief: brief(), confirmed: true };
  await freezeLightTask(options);
  await assert.rejects(() => freezeLightTask({ ...options, fs: pretendArtifactIsSymlink('light-brief.md') }), { code: 'LIGHT_SOURCE_CHANGED' });
});

test('idempotent freeze rejects a child identity race before reading through its handle', async (t) => {
  const root = await rootFixture(t);
  const classification = classifyMode(signals());
  const lightBrief = brief();
  await freezeLightTask({ root, task: 'identity-race-light', classification, brief: lightBrief, confirmed: true });
  const raced = pretendArtifactIdentityChanged('mode.json');
  await assert.rejects(
    () => freezeLightTask({ root, task: 'identity-race-light', classification, brief: lightBrief, confirmed: true, fs: raced.fs }),
    { code: 'LIGHT_SOURCE_CHANGED' },
  );
  assert.equal(raced.wasChangedArtifactRead(), false);
});

test('freeze cleans staging after a pre-commit failure and never writes requirement inputs', async (t) => {
  const root = await rootFixture(t);
  await assert.rejects(() => freezeLightTask({
    root,
    task: 'empty-value',
    classification: classifyMode(signals()),
    brief: brief(),
    confirmed: true,
    beforeCommit: async () => { throw new Error('injected failure'); },
  }), /injected failure/);
  assert.deepEqual(await readdir(path.join(root, '.ai-dev-loop')), []);
  assert.equal(await exists(path.join(root, 'requirements')), false);
});

test('freeze rejects unsafe task identifiers and non-Light classifications', async (t) => {
  const root = await rootFixture(t);
  await assert.rejects(() => freezeLightTask({ root, task: '../escape', classification: classifyMode(signals()), brief: brief(), confirmed: true }), { code: 'LIGHT_TASK_INVALID' });
  await assert.rejects(() => freezeLightTask({ root, task: 'full-task', classification: classifyMode(signals({ breaking: true })), brief: brief(), confirmed: true }), { code: 'LIGHT_MODE_REQUIRED' });
  for (const task of ['MixedCase', 'con', 'task.']) {
    await assert.rejects(() => freezeLightTask({ root, task, classification: classifyMode(signals()), brief: brief(), confirmed: true }), { code: 'LIGHT_TASK_INVALID' });
  }
});

test('freeze requires an existing project root', async (t) => {
  const root = await rootFixture(t);
  const missing = path.join(root, 'missing-project');
  await assert.rejects(() => freezeLightTask({ root: missing, task: 'empty-value', classification: classifyMode(signals()), brief: brief(), confirmed: true }), { code: 'LIGHT_ROOT_INVALID' });
  assert.equal(await exists(missing), false);
});

test('freeze leaves an existing requirement tree byte-for-byte untouched', async (t) => {
  const root = await rootFixture(t);
  const sentinel = path.join(root, 'requirements', 'existing', 'request.md');
  await mkdir(path.dirname(sentinel), { recursive: true });
  await writeFile(sentinel, 'protected\n');
  await freezeLightTask({ root, task: 'empty-value', classification: classifyMode(signals()), brief: brief(), confirmed: true });
  assert.equal(await readFile(sentinel, 'utf8'), 'protected\n');
  assert.deepEqual(await readdir(path.join(root, 'requirements')), ['existing']);
});

test('route is read-only and returns the classifier result unchanged', async (t) => {
  const root = await rootFixture(t);
  const before = await readdir(root);
  const result = routeTask(signals());
  assert.equal(result.mode, 'light');
  assert.deepEqual(await readdir(root), before);
});

test('start returns no artifacts for None and does not start a model', async (t) => {
  const root = await rootFixture(t);
  let calls = 0;
  const result = await startTask({
    root,
    signals: signals({ modifiesFiles: [], writesFiles: false }),
    generateTaskId: () => { throw new Error('None must not allocate a task ID'); },
    invokeModel: () => { calls++; },
  });
  assert.equal(result.route.mode, 'none');
  assert.deepEqual(result.artifacts, []);
  assert.equal(result.nextAction, 'none');
  assert.equal(calls, 0);
  assert.deepEqual(await readdir(root), []);
});

test('start persists Full mode and returns generic prepare without creating inputs or calling a model', async (t) => {
  const root = await rootFixture(t);
  let calls = 0;
  const result = await startTask({
    root,
    generateTaskId: () => 'generated-breaking',
    hostRuntime: 'codex',
    signals: signals({ breaking: true }),
    now: () => '2026-07-11T00:00:00.000Z',
    invokeModel: () => { calls++; },
  });
  assert.equal(result.route.mode, 'full');
  assert.equal(result.nextAction, 'prepare');
  assert.equal(result.authority, 'generic-baseline');
  assert.equal(result.task, 'generated-breaking');
  assert.equal(result.persistence.created, true);
  assert.deepEqual(result.artifacts, [path.join(root, '.ai-dev-loop', 'generated-breaking', 'mode.json')]);
  const mode = JSON.parse(await readFile(result.artifacts[0], 'utf8'));
  assert.equal(mode.mode, 'full');
  assert.equal(mode.hostRuntime, 'codex');
  assert.equal(mode.createdAt, '2026-07-11T00:00:00.000Z');
  assert.equal(calls, 0);
  assert.equal(await exists(path.join(root, 'requirements')), false);
});

test('Full start persistence is idempotent and changed route input fails closed', async (t) => {
  const root = await rootFixture(t);
  const options = {
    root,
    task: 'breaking',
    signals: signals({ breaking: true }),
    hostRuntime: 'claude',
    generateTaskId: () => { throw new Error('an explicit task must bypass generation'); },
  };
  const first = await startTask({ ...options, now: () => '2026-07-11T00:00:00.000Z' });
  const modePath = path.join(root, '.ai-dev-loop', 'breaking', 'mode.json');
  const before = await readFile(modePath, 'utf8');
  const second = await startTask({ ...options, now: () => '2030-01-01T00:00:00.000Z' });
  assert.equal(first.persistence.created, true);
  assert.equal(second.persistence.created, false);
  assert.equal(second.persistence.idempotent, true);
  assert.equal(await readFile(modePath, 'utf8'), before);
  await assert.rejects(
    () => startTask({ ...options, signals: signals({ authentication: true }) }),
    { code: 'MODE_SOURCE_CHANGED' },
  );
  assert.equal(await readFile(modePath, 'utf8'), before);
});

test('Full persistence snapshots validated classification before its first await', async (t) => {
  const root = await rootFixture(t);
  const classification = classifyMode(signals({ breaking: true }));
  const pending = persistFullMode({ root, task: 'snapshot-full', classification });
  classification.evaluatedInputs.breaking = false;
  classification.reasons.splice(0, classification.reasons.length, 'AUTHENTICATION');
  await pending;
  const mode = JSON.parse(await readFile(path.join(root, '.ai-dev-loop', 'snapshot-full', 'mode.json'), 'utf8'));
  assert.equal(mode.evaluatedInputs.breaking, true);
  assert.deepEqual(mode.reasons, ['BREAKING_CHANGE']);
});

test('Full persistence rejects changed timestamp types and unknown mode metadata', async (t) => {
  const classification = classifyMode(signals({ breaking: true }));
  const root = await rootFixture(t);
  const options = { root, task: 'strict-full', classification };
  await persistFullMode(options);
  const modePath = path.join(root, '.ai-dev-loop', 'strict-full', 'mode.json');
  const mode = JSON.parse(await readFile(modePath, 'utf8'));
  mode.createdAt = 0;
  await writeFile(modePath, `${JSON.stringify(mode, null, 2)}\n`);
  await assert.rejects(() => persistFullMode(options), { code: 'MODE_SOURCE_CHANGED' });

  const otherRoot = await rootFixture(t);
  const otherOptions = { ...options, root: otherRoot };
  await persistFullMode(otherOptions);
  const otherModePath = path.join(otherRoot, '.ai-dev-loop', 'strict-full', 'mode.json');
  const otherMode = JSON.parse(await readFile(otherModePath, 'utf8'));
  otherMode.unexpected = true;
  await writeFile(otherModePath, `${JSON.stringify(otherMode, null, 2)}\n`);
  await assert.rejects(() => persistFullMode(otherOptions), { code: 'MODE_SOURCE_CHANGED' });

  const hostRoot = await rootFixture(t);
  const hostOptions = { ...options, root: hostRoot };
  await persistFullMode(hostOptions);
  const hostModePath = path.join(hostRoot, '.ai-dev-loop', 'strict-full', 'mode.json');
  const hostMode = JSON.parse(await readFile(hostModePath, 'utf8'));
  hostMode.hostRuntime = null;
  await writeFile(hostModePath, `${JSON.stringify(hostMode, null, 2)}\n`);
  await assert.rejects(() => persistFullMode(hostOptions), { code: 'MODE_SOURCE_CHANGED' });
});

test('Full persistence rejects child artifact symlinks and non-canonical task IDs', async (t) => {
  const root = await rootFixture(t);
  const classification = classifyMode(signals({ breaking: true }));
  const options = { root, task: 'linked-full', classification };
  await persistFullMode(options);
  await assert.rejects(() => persistFullMode({ ...options, fs: pretendArtifactIsSymlink('mode.json') }), { code: 'MODE_SOURCE_CHANGED' });
  for (const task of ['MixedCase', 'nul', 'task.']) {
    await assert.rejects(() => persistFullMode({ root, task, classification }), { code: 'MODE_TASK_INVALID' });
  }
});

test('Full persistence rejects a child identity race before reading through its handle', async (t) => {
  const root = await rootFixture(t);
  const classification = classifyMode(signals({ breaking: true }));
  await startTask({ root, task: 'identity-race-full', signals: classification.evaluatedInputs });
  const raced = pretendArtifactIdentityChanged('mode.json');
  await assert.rejects(
    () => startTask({ root, task: 'identity-race-full', signals: classification.evaluatedInputs, fs: raced.fs }),
    { code: 'MODE_SOURCE_CHANGED' },
  );
  assert.equal(raced.wasChangedArtifactRead(), false);
});

test('Full start uses safe atomic staging and never modifies existing requirement inputs', async (t) => {
  const root = await rootFixture(t);
  const sentinel = path.join(root, 'requirements', 'existing.md');
  await mkdir(path.dirname(sentinel), { recursive: true });
  await writeFile(sentinel, 'protected\n');
  await assert.rejects(() => startTask({
    root,
    task: 'breaking',
    signals: signals({ breaking: true }),
    beforeCommit: async () => { throw new Error('full staging failure'); },
  }), /full staging failure/);
  assert.deepEqual(await readdir(path.join(root, '.ai-dev-loop')), []);
  assert.equal(await readFile(sentinel, 'utf8'), 'protected\n');
  await assert.rejects(() => startTask({ root, task: '../escape', signals: signals({ breaking: true }) }), { code: 'MODE_TASK_INVALID' });
});

test('start previews Light and requests confirmation without writing artifacts', async (t) => {
  const root = await rootFixture(t);
  const result = await startTask({
    root,
    signals: signals(),
    brief: brief(),
    confirmed: false,
    generateTaskId: () => { throw new Error('unconfirmed Light must not allocate a task ID'); },
  });
  assert.equal(result.route.mode, 'light');
  assert.equal(result.nextAction, 'confirm');
  assert.match(result.brief, /^## Goal/m);
  assert.deepEqual(result.artifacts, []);
  assert.deepEqual(await readdir(root), []);
});

test('start freezes a confirmed injected Light brief', async (t) => {
  const root = await rootFixture(t);
  const result = await startTask({ root, signals: signals(), brief: brief(), confirmed: true, generateTaskId: () => 'generated-empty-value' });
  assert.equal(result.route.mode, 'light');
  assert.equal(result.nextAction, 'develop');
  assert.equal(result.task, 'generated-empty-value');
  assert.equal(result.freeze.stage, 'BASELINE_FROZEN');
  assert.equal(await exists(path.join(root, '.ai-dev-loop', 'generated-empty-value', 'mode.json')), true);
  assert.equal(await exists(path.join(root, 'requirements')), false);
});

test('start requires an injected brief before confirmed Light freezing', async (t) => {
  const root = await rootFixture(t);
  await assert.rejects(() => startTask({ root, task: 'empty-value', signals: signals(), confirmed: true }), { code: 'LIGHT_BRIEF_REQUIRED' });
  assert.deepEqual(await readdir(root), []);
});

test('default persisted task IDs are deterministic safe hashes, never raw description paths', async (t) => {
  const firstRoot = await rootFixture(t);
  const secondRoot = await rootFixture(t);
  const unsafeDescription = '../../CON/raw task path';
  const input = signals({ description: unsafeDescription, breaking: true });

  const first = await startTask({ root: firstRoot, signals: input });
  const second = await startTask({ root: secondRoot, signals: input });

  assert.match(first.task, /^task-[a-f0-9]{20}$/);
  assert.equal(second.task, first.task);
  assert.equal(await exists(path.join(firstRoot, '.ai-dev-loop', first.task, 'mode.json')), true);
  assert.deepEqual(await readdir(firstRoot), ['.ai-dev-loop']);
});

test('a generated task ID is validated before any persistence path is created', async (t) => {
  const root = await rootFixture(t);
  let generatorCalls = 0;
  await assert.rejects(
    () => startTask({
      root,
      signals: signals({ breaking: true }),
      generateTaskId: () => { generatorCalls++; return '../unsafe'; },
    }),
    { code: 'MODE_TASK_INVALID' },
  );
  assert.equal(generatorCalls, 1);
  assert.deepEqual(await readdir(root), []);
});
