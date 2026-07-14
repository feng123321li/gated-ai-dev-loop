import test from 'node:test';
import assert from 'node:assert/strict';
import * as fsPromises from 'node:fs/promises';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { runCli } from '../../src/cli/main.mjs';
import { startTask } from '../../src/commands/start.mjs';
import { freezeFullBaseline } from '../../src/full/freeze.mjs';
import { frozenStateFingerprint, readFullPackage } from '../../src/full/package.mjs';
import { prepareFullBaseline } from '../../src/full/prepare.mjs';
import { sha256Bytes } from '../../src/core/hash.mjs';
import { validFullBaseline } from '../helpers/full-baseline.mjs';

const fullSignals = (patch = {}) => ({
  description: 'Add a repository development gate',
  modifiesFiles: ['src/gate.mjs'],
  writesFiles: true,
  breaking: true,
  impactKnown: true,
  ...patch,
});

test('existing Full packages with the legacy handoff filename remain readable', async (t) => {
  const options = await rootFixture(t, { task: 'legacy-full-handoff' });
  await prepareFullBaseline({ ...options, baseline: 'requirements/baseline.md' });
  await freezeFullBaseline({ root: options.root, task: options.task, confirmed: true });
  const target = path.join(options.root, '.ai-dev-loop', options.task);
  await fsPromises.rename(
    path.join(target, 'development-handoff.md'),
    path.join(target, 'handoff-to-claude.md'),
  );
  const statePath = path.join(target, 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.artifactHashes['handoff-to-claude.md'] = state.artifactHashes['development-handoff.md'];
  delete state.artifactHashes['development-handoff.md'];
  state.frozenFingerprint = frozenStateFingerprint(state);
  await writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`);

  const existing = await readFullPackage({ root: options.root, task: options.task });
  assert.equal(existing.handoffName, 'handoff-to-claude.md');
  const repeated = await freezeFullBaseline({ root: options.root, task: options.task, confirmed: true });
  assert.equal(repeated.idempotent, true);
  assert.equal(repeated.artifacts.some((entry) => entry.endsWith('handoff-to-claude.md')), true);
});

async function rootFixture(t, { task = 'baseline-task', hostRuntime = 'codex' } = {}) {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-full-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, 'requirements'), { recursive: true });
  await writeFile(path.join(root, 'requirements', 'baseline.md'), validFullBaseline().replaceAll('\n', '\r\n'));
  await writeFile(path.join(root, 'requirements', 'notes.txt'), 'Source note one.\r\nSource note two.\r\n');
  await writeFile(path.join(root, 'requirements', 'api.md'), '# API context\nThe endpoint returns JSON.\n');
  await startTask({ root, task, signals: fullSignals(), hostRuntime, now: () => '2026-07-12T00:00:00.000Z' });
  return { root, task };
}

async function snapshot(root, task) {
  const target = path.join(root, '.ai-dev-loop', task);
  const result = {};
  for (const name of (await readdir(target)).sort()) {
    const stat = await fsPromises.lstat(path.join(target, name));
    if (stat.isFile()) result[name] = await readFile(path.join(target, name), 'utf8');
  }
  return result;
}

test('prepare builds a deterministic Full staging package without confirmation or model calls', async (t) => {
  const { root, task } = await rootFixture(t);
  let modelCalls = 0;
  const result = await prepareFullBaseline({
    root,
    task,
    baseline: 'requirements/baseline.md',
    sources: ['requirements/notes.txt', 'requirements/api.md'],
    now: () => '2026-07-12T01:00:00.000Z',
    invokeModel: () => { modelCalls++; },
  });

  assert.equal(result.created, true);
  assert.equal(result.stage, 'WAITING_FOR_BASELINE_CONFIRMATION');
  assert.equal(result.mode, 'full');
  assert.equal(modelCalls, 0);
  const target = path.join(root, '.ai-dev-loop', task);
  assert.deepEqual((await readdir(target)).sort(), [
    'acceptance.json', 'baseline.md', 'decision-log.md', 'mode.json',
    'source-manifest.json', 'state.json', 'tasks.json',
  ]);
  const baseline = await readFile(path.join(target, 'baseline.md'), 'utf8');
  assert.equal(baseline.includes('\r'), false);
  assert.equal(baseline.endsWith('\n'), true);
  const manifest = JSON.parse(await readFile(path.join(target, 'source-manifest.json'), 'utf8'));
  assert.deepEqual(manifest.files.map(({ path: filePath, purpose }) => ({ path: filePath, purpose })), [
    { path: 'requirements/baseline.md', purpose: 'baseline' },
    { path: 'requirements/api.md', purpose: 'source' },
    { path: 'requirements/notes.txt', purpose: 'source' },
  ]);
  assert.match(manifest.fingerprint, /^[a-f0-9]{64}$/);
  assert.equal(manifest.files[0].sha256, sha256Bytes(await readFile(path.join(root, 'requirements', 'baseline.md'))));
  const state = JSON.parse(await readFile(path.join(target, 'state.json'), 'utf8'));
  assert.equal(state.stage, 'WAITING_FOR_BASELINE_CONFIRMATION');
  assert.equal(state.hostRuntime, 'codex');
  assert.equal(state.reviewer, 'codex');
  const acceptance = JSON.parse(await readFile(path.join(target, 'acceptance.json'), 'utf8'));
  const tasks = JSON.parse(await readFile(path.join(target, 'tasks.json'), 'utf8'));
  assert.deepEqual(acceptance.acceptance[0].requirementIds, ['R-001']);
  assert.deepEqual(tasks.tasks[0].acceptanceIds, ['A-001']);
});

test('prepare accepts arbitrary binary supporting sources without decoding them as Markdown', async (t) => {
  const { root, task } = await rootFixture(t);
  const binary = Buffer.from([0xff, 0xd8, 0xff, 0x00, 0x80]);
  await writeFile(path.join(root, 'requirements', 'reference.bin'), binary);
  await prepareFullBaseline({
    root,
    task,
    baseline: 'requirements/baseline.md',
    sources: ['requirements/reference.bin'],
  });
  const manifest = JSON.parse(await readFile(path.join(root, '.ai-dev-loop', task, 'source-manifest.json'), 'utf8'));
  assert.deepEqual(manifest.files[1], {
    path: 'requirements/reference.bin',
    sha256: sha256Bytes(binary),
    purpose: 'source',
  });
});

test('prepare refuses a current Light mode package', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-light-prepare-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(path.join(root, 'baseline.md'), validFullBaseline());
  const risks = {
    loadBearing: false, breaking: false, migrations: false, dependencyChange: false,
    newDependency: false, externalContract: false, permissions: false, authentication: false,
    stateMachine: false, transaction: false, concurrency: false, idempotency: false,
    unresolvedOptions: 0, thresholdDecision: false, fileCountExceeded: false, impactKnown: true,
  };
  await startTask({
    root,
    task: 'light-task',
    hostRuntime: 'codex',
    signals: { description: 'small change', modifiesFiles: ['src/a.mjs'], writesFiles: true, impactKnown: true },
    brief: {
      goal: 'Make a small local change.',
      scope: ['src/a.mjs'],
      acceptance: { outcomes: ['The local output changes.'], testCommands: [['npm', 'test']] },
      risks,
    },
    confirmed: true,
  });
  await assert.rejects(
    () => prepareFullBaseline({ root, task: 'light-task', baseline: 'baseline.md' }),
    { code: 'FULL_MODE_REQUIRED' },
  );
});

test('prepare is idempotent and replaces only generated pre-freeze files when sources change', async (t) => {
  const { root, task } = await rootFixture(t);
  const options = {
    root, task, baseline: 'requirements/baseline.md', sources: ['requirements/notes.txt'],
    now: () => '2026-07-12T01:00:00.000Z',
  };
  const first = await prepareFullBaseline(options);
  const firstSnapshot = await snapshot(root, task);
  const second = await prepareFullBaseline({ ...options, now: () => '2026-07-12T02:00:00.000Z' });
  assert.equal(second.created, false);
  assert.equal(second.idempotent, true);
  assert.equal(second.sourceFingerprint, first.sourceFingerprint);
  assert.deepEqual(await snapshot(root, task), firstSnapshot);

  const decisionPath = path.join(root, '.ai-dev-loop', task, 'decision-log.md');
  await writeFile(decisionPath, '# Decision Log\n\n- User-selected decision.\n');
  await writeFile(path.join(root, 'requirements', 'notes.txt'), 'Changed source note.\n');
  const changed = await prepareFullBaseline({ ...options, now: () => '2026-07-12T03:00:00.000Z' });
  assert.equal(changed.updated, true);
  assert.notEqual(changed.sourceFingerprint, first.sourceFingerprint);
  assert.equal(await readFile(decisionPath, 'utf8'), '# Decision Log\n\n- User-selected decision.\n');
  assert.equal(JSON.parse(await readFile(path.join(root, '.ai-dev-loop', task, 'state.json'), 'utf8')).updatedAt, '2026-07-12T03:00:00.000Z');
});

test('prepare update is atomic when staging fails', async (t) => {
  const { root, task } = await rootFixture(t);
  const options = { root, task, baseline: 'requirements/baseline.md', sources: ['requirements/notes.txt'] };
  await prepareFullBaseline(options);
  const before = await snapshot(root, task);
  await writeFile(path.join(root, 'requirements', 'notes.txt'), 'Changed before a simulated failure.\n');
  await assert.rejects(
    () => prepareFullBaseline({ ...options, beforeCommit: () => { throw new Error('staging failed'); } }),
    /staging failed/,
  );
  assert.deepEqual(await snapshot(root, task), before);
  assert.deepEqual((await readdir(path.join(root, '.ai-dev-loop'))).filter((name) => name.includes('.tmp-')), []);
});

test('prepare rechecks every source immediately before commit', async (t) => {
  const { root, task } = await rootFixture(t);
  await assert.rejects(
    () => prepareFullBaseline({
      root,
      task,
      baseline: 'requirements/baseline.md',
      sources: ['requirements/notes.txt'],
      beforeCommit: async () => writeFile(path.join(root, 'requirements', 'notes.txt'), 'Changed during staging.\n'),
    }),
    { code: /(?:BASELINE_SOURCE_CHANGED|PATH_FILE_CHANGED)/ },
  );
  assert.deepEqual((await readdir(path.join(root, '.ai-dev-loop', task))).sort(), ['mode.json']);
});

test('prepare rejects an identity change even when source bytes are unchanged', async (t) => {
  const { root, task } = await rootFixture(t);
  const notesPath = path.join(root, 'requirements', 'notes.txt');
  const original = await readFile(notesPath);
  await assert.rejects(
    () => prepareFullBaseline({
      root,
      task,
      baseline: 'requirements/baseline.md',
      sources: ['requirements/notes.txt'],
      beforeCommit: async () => {
        await rm(notesPath);
        await writeFile(notesPath, original);
      },
    }),
    { code: 'PATH_FILE_CHANGED' },
  );
  assert.deepEqual((await readdir(path.join(root, '.ai-dev-loop', task))).sort(), ['mode.json']);
});

test('freeze requires confirmation without mutation, then revalidates and freezes a handoff', async (t) => {
  const { root, task } = await rootFixture(t);
  await prepareFullBaseline({ root, task, baseline: 'requirements/baseline.md', sources: ['requirements/notes.txt'] });
  const before = await snapshot(root, task);
  await assert.rejects(() => freezeFullBaseline({ root, task }), { code: 'CONFIRMATION_REQUIRED' });
  assert.deepEqual(await snapshot(root, task), before);

  let modelCalls = 0;
  const frozen = await freezeFullBaseline({
    root, task, confirmed: true,
    now: () => '2026-07-12T04:00:00.000Z',
    invokeModel: () => { modelCalls++; },
  });
  assert.equal(frozen.stage, 'BASELINE_FROZEN');
  assert.equal(frozen.created, true);
  assert.equal(modelCalls, 0);
  const target = path.join(root, '.ai-dev-loop', task);
  const state = JSON.parse(await readFile(path.join(target, 'state.json'), 'utf8'));
  assert.equal(state.stage, 'BASELINE_FROZEN');
  assert.equal(state.hostRuntime, state.reviewer);
  const handoff = await readFile(path.join(target, 'development-handoff.md'), 'utf8');
  assert.match(handoff, /T-001/);
  assert.match(handoff, /A-001/);
  assert.match(handoff, /\["node","--test","tests\/unit\/baseline\.test\.mjs"\]/);

  const unchanged = await snapshot(root, task);
  const repeated = await freezeFullBaseline({ root, task, confirmed: true, now: () => '2026-07-12T05:00:00.000Z' });
  assert.equal(repeated.idempotent, true);
  assert.deepEqual(await snapshot(root, task), unchanged);
});

test('Claude can be the recorded Full baseline host without a cross-model review', async (t) => {
  const { root, task } = await rootFixture(t, { task: 'claude-hosted-full', hostRuntime: 'claude' });
  await prepareFullBaseline({ root, task, baseline: 'requirements/baseline.md' });
  await freezeFullBaseline({ root, task, confirmed: true });
  const target = path.join(root, '.ai-dev-loop', task);
  const state = JSON.parse(await readFile(path.join(target, 'state.json'), 'utf8'));
  const handoff = await readFile(path.join(target, 'development-handoff.md'), 'utf8');
  assert.equal(state.hostRuntime, 'claude');
  assert.equal(state.reviewer, 'claude');
  assert.match(handoff, /Reviewed by: claude/);
  assert.match(handoff, /## Scope\n- Parse and freeze the supplied baseline\./);
  assert.match(handoff, /Do not reanalyze, reinterpret, clarify, or rewrite requirements\./);
  assert.match(handoff, /Do not judge or report `PASS`\./);
});

test('freeze detects source changes and generated artifact tampering before mutation', async (t) => {
  const sourceCase = await rootFixture(t, { task: 'changed-source' });
  await prepareFullBaseline({ ...sourceCase, baseline: 'requirements/baseline.md', sources: ['requirements/notes.txt'] });
  const sourceBefore = await snapshot(sourceCase.root, sourceCase.task);
  await writeFile(path.join(sourceCase.root, 'requirements', 'notes.txt'), 'Mutated after prepare.\n');
  await assert.rejects(() => freezeFullBaseline({ ...sourceCase, confirmed: true }), { code: 'BASELINE_SOURCE_CHANGED' });
  assert.deepEqual(await snapshot(sourceCase.root, sourceCase.task), sourceBefore);

  const artifactCase = await rootFixture(t, { task: 'changed-artifact' });
  await prepareFullBaseline({ ...artifactCase, baseline: 'requirements/baseline.md' });
  const baselinePath = path.join(artifactCase.root, '.ai-dev-loop', artifactCase.task, 'baseline.md');
  await writeFile(baselinePath, `${await readFile(baselinePath, 'utf8')}tampered\n`);
  await assert.rejects(() => freezeFullBaseline({ ...artifactCase, confirmed: true }), { code: 'BASELINE_SOURCE_CHANGED' });

  const stateCase = await rootFixture(t, { task: 'changed-state' });
  await prepareFullBaseline({ ...stateCase, baseline: 'requirements/baseline.md' });
  const statePath = path.join(stateCase.root, '.ai-dev-loop', stateCase.task, 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.inputFingerprint = '0'.repeat(64);
  await writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`);
  await assert.rejects(() => freezeFullBaseline({ ...stateCase, confirmed: true }), { code: 'BASELINE_SOURCE_CHANGED' });
});

test('freeze is atomic when final staging fails', async (t) => {
  const { root, task } = await rootFixture(t);
  await prepareFullBaseline({ root, task, baseline: 'requirements/baseline.md' });
  const before = await snapshot(root, task);
  await assert.rejects(
    () => freezeFullBaseline({ root, task, confirmed: true, beforeCommit: () => { throw new Error('freeze staging failed'); } }),
    /freeze staging failed/,
  );
  assert.deepEqual(await snapshot(root, task), before);
});

test('prepare leaves a failed restoration locked for explicit recovery', async (t) => {
  const { root, task } = await rootFixture(t);
  const options = { root, task, baseline: 'requirements/baseline.md' };
  await prepareFullBaseline(options);
  const baselinePath = path.join(root, 'requirements', 'baseline.md');
  const originalBaseline = await readFile(baselinePath);
  const packageTarget = path.join(root, '.ai-dev-loop', task);
  let packageRenameCalls = 0;
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'rename') return async (...args) => {
        const destination = String(args[1]);
        if (destination === packageTarget || destination.startsWith(`${packageTarget}.backup.tmp-`)) {
          packageRenameCalls++;
          if (packageRenameCalls >= 2) throw Object.assign(new Error('rename failed'), { code: 'EACCES' });
        }
        return target.rename(...args);
      };
      return Reflect.get(target, property, receiver);
    },
  });
  await writeFile(baselinePath, validFullBaseline({ Goal: 'Recover the canonical package.' }));
  const failure = await prepareFullBaseline({ ...options, fs }).then(
    () => undefined,
    (error) => error,
  );
  assert.equal(failure?.code, 'ATOMIC_RESTORE_FAILED');
  assert.equal(failure.details.recovery.automaticRecovery, false);
  assert.equal(failure.details.recovery.recoveryRequired, true);

  await writeFile(baselinePath, originalBaseline);
  const blocked = await prepareFullBaseline(options).then(() => undefined, (error) => error);
  assert.equal(blocked?.code, 'OPERATION_IN_PROGRESS');
  assert.equal(blocked.details.recovery.lockPath, failure.details.recovery.lockPath);
  assert.equal(blocked.details.recovery.recoveryRequired, true);
});

test('frozen packages are immutable and changed prepare input is rejected', async (t) => {
  const { root, task } = await rootFixture(t);
  const options = { root, task, baseline: 'requirements/baseline.md', sources: ['requirements/notes.txt'] };
  await prepareFullBaseline(options);
  await freezeFullBaseline({ root, task, confirmed: true });
  const before = await snapshot(root, task);
  await writeFile(path.join(root, 'requirements', 'notes.txt'), 'Changed after freeze.\n');
  await assert.rejects(() => prepareFullBaseline(options), { code: 'BASELINE_SOURCE_CHANGED' });
  assert.deepEqual(await snapshot(root, task), before);
});

test('prepare rejects unsafe, duplicate, runtime, directory, linked, and invalid UTF-8 inputs', async (t) => {
  const { root, task } = await rootFixture(t);
  const outside = await mkdtemp(path.join(tmpdir(), 'gated-loop-outside-'));
  t.after(() => rm(outside, { recursive: true, force: true }));
  await writeFile(path.join(outside, 'outside.md'), validFullBaseline());
  await writeFile(path.join(root, '.env.requirements'), validFullBaseline());
  await writeFile(path.join(root, '.envrc'), validFullBaseline());
  await writeFile(path.join(root, 'requirements', 'invalid.md'), Buffer.from([0xc3, 0x28]));

  const invalidCases = [
    { baseline: '../outside.md' },
    { baseline: path.join(root, 'requirements', 'baseline.md') },
    { baseline: '.env.requirements' },
    { baseline: '.envrc' },
    { baseline: 'requirements/baseline.md', sources: ['.envrc'] },
    { baseline: '.ai-dev-loop/baseline-task/mode.json' },
    { baseline: 'requirements' },
    { baseline: 'requirements/invalid.md' },
    { baseline: 'requirements/baseline.md', sources: ['requirements/notes.txt', '.\\requirements\\notes.txt'] },
    { baseline: 'requirements/baseline.md', sources: ['requirements/baseline.md'] },
  ];
  for (const invalid of invalidCases) {
    await assert.rejects(
      () => prepareFullBaseline({ root, task, sources: [], ...invalid }),
      { code: /(?:BASELINE_PATH_INVALID|BASELINE_SOURCE_INVALID|BASELINE_UTF8_INVALID|PATH_(?:OUTSIDE_ROOT|SYMLINK|FILE_CHANGED))/ },
      JSON.stringify(invalid),
    );
  }
  await writeFile(path.join(root, 'requirements', 'linked.md'), validFullBaseline());
  const linkedPath = path.join(root, 'requirements', 'linked.md');
  const linkedFs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'lstat') {
        return async (value, ...args) => String(value) === linkedPath
          ? { isFile: () => false, isSymbolicLink: () => true }
          : target.lstat(value, ...args);
      }
      return Reflect.get(target, property, receiver);
    },
  });
  await assert.rejects(
    () => prepareFullBaseline({ root, task, baseline: 'requirements/linked.md', fs: linkedFs }),
    { code: 'PATH_SYMLINK' },
  );
  assert.deepEqual((await readdir(path.join(root, '.ai-dev-loop', task))).sort(), ['mode.json']);
});

test('prepare uses verified handles and rejects a check/open identity race before replacement bytes are read', async (t) => {
  const { root, task } = await rootFixture(t);
  const expected = path.join(root, 'requirements', 'baseline.md');
  const replacement = path.join(root, 'requirements', 'api.md');
  let replacementRead = false;
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'open') {
        return async (value, flags) => {
          const swapped = String(value) === expected;
          const handle = await target.open(swapped ? replacement : value, flags);
          return {
            stat: (...args) => handle.stat(...args),
            readFile: (...args) => { if (swapped) replacementRead = true; return handle.readFile(...args); },
            close: () => handle.close(),
          };
        };
      }
      return Reflect.get(target, property, receiver);
    },
  });
  await assert.rejects(
    () => prepareFullBaseline({ root, task, baseline: 'requirements/baseline.md', fs }),
    { code: 'PATH_FILE_CHANGED' },
  );
  assert.equal(replacementRead, false);
  assert.deepEqual((await readdir(path.join(root, '.ai-dev-loop', task))).sort(), ['mode.json']);
});

test('prepare rejects a source replacement between identity discovery and verified reads', async (t) => {
  const { root, task } = await rootFixture(t);
  const baselinePath = path.join(root, 'requirements', 'baseline.md');
  const notesPath = path.join(root, 'requirements', 'notes.txt');
  let replaced = false;
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'lstat') return async (value, ...args) => {
        if (!replaced && String(value) === notesPath) {
          replaced = true;
          await writeFile(baselinePath, validFullBaseline({ Goal: 'Deliver a replaced requirement package.' }));
        }
        return target.lstat(value, ...args);
      };
      return Reflect.get(target, property, receiver);
    },
  });
  await assert.rejects(
    () => prepareFullBaseline({
      root, task, baseline: 'requirements/baseline.md', sources: ['requirements/notes.txt'], fs,
    }),
    { code: /(?:PATH_FILE_CHANGED|BASELINE_SOURCE_CHANGED)/ },
  );
  assert.equal(replaced, true);
  assert.deepEqual((await readdir(path.join(root, '.ai-dev-loop', task))).sort(), ['mode.json']);
});

test('CLI prepare and freeze support repeatable sources, strict options, and no model calls', async (t) => {
  const { root, task } = await rootFixture(t, { task: 'cli-baseline' });
  async function invoke(argv) {
    const out = []; const err = []; let modelCalls = 0;
    const exitCode = await runCli(argv, {
      cwd: root,
      stdout: (value) => out.push(value),
      stderr: (value) => err.push(value),
      invokeModel: () => { modelCalls++; },
    });
    return { exitCode, out: out.join(''), err: err.join(''), modelCalls };
  }
  const prepared = await invoke([
    'prepare', '--task', task, '--baseline', 'requirements/baseline.md',
    '--source', 'requirements/notes.txt', '--source', 'requirements/api.md', '--json',
  ]);
  assert.equal(prepared.exitCode, 0);
  assert.equal(JSON.parse(prepared.out).result.stage, 'WAITING_FOR_BASELINE_CONFIRMATION');
  assert.equal(prepared.modelCalls, 0);
  const unconfirmed = await invoke(['freeze', '--task', task, '--json']);
  assert.equal(JSON.parse(unconfirmed.err).error.code, 'CONFIRMATION_REQUIRED');
  const frozen = await invoke(['freeze', '--task', task, '--confirmed', '--json']);
  assert.equal(frozen.exitCode, 0);
  assert.equal(JSON.parse(frozen.out).result.stage, 'BASELINE_FROZEN');
  assert.equal(frozen.modelCalls, 0);

  for (const argv of [
    ['prepare', '--task', task],
    ['prepare', '--baseline', 'requirements/baseline.md'],
    ['prepare', '--task', task, '--baseline', 'requirements/baseline.md', '--confirmed'],
    ['freeze', '--task', task, '--baseline', 'requirements/baseline.md'],
    ['freeze', '--confirmed'],
  ]) {
    const result = await invoke([...argv, '--json']);
    assert.equal(result.exitCode, 1, argv.join(' '));
    assert.match(JSON.parse(result.err).error.code, /(?:OPTION_REQUIRED|UNKNOWN_OPTION)/);
    assert.equal(result.modelCalls, 0);
  }
});
