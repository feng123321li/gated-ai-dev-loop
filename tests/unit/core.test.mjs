import test from 'node:test';
import assert from 'node:assert/strict';
import * as fsPromises from 'node:fs/promises';
import { mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { GatedLoopError } from '../../src/core/errors.mjs';
import {
  assertSafePath,
  atomicWriteFile,
  atomicWriteDirectory,
  atomicReplaceDirectory,
  readSafeRegularFile,
  resolveAtomicDirectory,
  runtimeTransactionLock,
  withRuntimeDirectoryTransaction,
} from '../../src/core/fs-safe.mjs';
import { canonicalRelativePath, sha256Bytes, manifestFingerprint } from '../../src/core/hash.mjs';
import { runProcess } from '../../src/core/process.mjs';

test('GatedLoopError exposes stable fields', () => {
  const error = new GatedLoopError('NOPE', 'failed', { exitCode: 7, details: { a: 1 } });
  assert.equal(error.name, 'GatedLoopError');
  assert.equal(error.code, 'NOPE');
  assert.equal(error.exitCode, 7);
  assert.deepEqual(error.details, { a: 1 });
});

test('safe paths reject lexical traversal and injected cross-volume paths', async () => {
  await assert.rejects(() => assertSafePath('C:\\repo', '..\\escape'), { code: 'PATH_OUTSIDE_ROOT' });
  await assert.rejects(() => assertSafePath('C:\\repo', 'D:\\escape', { pathApi: path.win32 }), { code: 'PATH_CROSS_VOLUME' });
});

test('safe paths reject a symlink ancestor and final symlink', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const outside = await mkdtemp(path.join(tmpdir(), 'gated-loop-out-'));
  t.after(() => rm(outside, { recursive: true, force: true }));
  await symlink(outside, path.join(root, 'link'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(() => assertSafePath(root, 'link/file'), { code: 'PATH_SYMLINK' });
  await assert.rejects(() => assertSafePath(root, 'link'), { code: 'PATH_SYMLINK' });
});

test('safe path checks real containment using injected privileged semantics', async () => {
  const fake = {
    lstat: async () => ({ isSymbolicLink: () => false }),
    realpath: async (value) => value.endsWith('escape') ? '/outside' : '/root',
  };
  await assert.rejects(() => assertSafePath('/root', 'escape', { fs: fake, pathApi: path.posix }), { code: 'PATH_OUTSIDE_ROOT' });
});

test('safe paths reject a symlink root using injected filesystem semantics', async () => {
  const fake = {
    lstat: async (value) => ({ isSymbolicLink: () => value === '/root' }),
    realpath: async () => '/trusted-root',
  };
  await assert.rejects(() => assertSafePath('/root', 'file', { fs: fake, pathApi: path.posix }), { code: 'PATH_SYMLINK' });
});

test('safe file reads use a verified handle and reject a check/open identity race', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const expected = path.join(root, 'expected.txt');
  const replacement = path.join(root, 'replacement.txt');
  await writeFile(expected, 'trusted');
  await writeFile(replacement, 'untrusted');

  assert.equal((await readSafeRegularFile(root, 'expected.txt')).toString('utf8'), 'trusted');

  let replacementRead = false;
  const fake = new Proxy(await import('node:fs/promises'), {
    get(target, property, receiver) {
      if (property === 'open') {
        return async (value, flags) => {
          const handle = await target.open(value === expected ? replacement : value, flags);
          return {
            stat: (...args) => handle.stat(...args),
            readFile: (...args) => { replacementRead = true; return handle.readFile(...args); },
            close: () => handle.close(),
          };
        };
      }
      return Reflect.get(target, property, receiver);
    },
  });
  await assert.rejects(() => readSafeRegularFile(root, 'expected.txt', { fs: fake }), { code: 'PATH_FILE_CHANGED' });
  assert.equal(replacementRead, false);
});

test('atomic file writes utf8 and cleans staging after failure', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await atomicWriteFile(path.join(root, 'ok.txt'), '你好');
  assert.equal(await readFile(path.join(root, 'ok.txt'), 'utf8'), '你好');
  await assert.rejects(() => atomicWriteFile(path.join(root, 'bad.txt'), 'x', { beforeRename: async () => { throw new Error('boom'); } }));
  assert.deepEqual((await readdir(root)).sort(), ['ok.txt']);
});

test('atomic directory stages completely and cleans on failure', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await atomicWriteDirectory(path.join(root, 'done'), async (staging) => writeFile(path.join(staging, 'x'), 'yes'));
  assert.equal(await readFile(path.join(root, 'done/x'), 'utf8'), 'yes');
  await assert.rejects(() => atomicWriteDirectory(path.join(root, 'bad'), async (staging) => { await writeFile(path.join(staging, 'x'), 'no'); throw new Error('boom'); }));
  assert.deepEqual((await readdir(root)).sort(), ['done']);
});

test('atomic directory acquires the canonical runtime lock when called directly', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-direct-directory-lock-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  let reached;
  const paused = new Promise((resolve) => { reached = resolve; });
  let release;
  const hold = new Promise((resolve) => { release = resolve; });
  const writing = atomicWriteDirectory(targetPath, async (staging) => {
    await writeFile(path.join(staging, 'value.txt'), 'committed');
    reached();
    await hold;
  });
  await paused;

  await assert.rejects(
    () => withRuntimeDirectoryTransaction(targetPath, async () => {}),
    { code: 'OPERATION_IN_PROGRESS' },
  );
  release();
  await writing;
  assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'committed');
  assert.deepEqual(await readdir(root), ['target']);
});

test('runtime transaction lock identity case-folds deterministic Windows aliases', async () => {
  const fakeFs = {
    mkdir: async () => {},
    lstat: async () => ({ isDirectory: () => true, isSymbolicLink: () => false }),
    realpath: async (value) => value,
  };
  const upper = await runtimeTransactionLock('C:\\Repo\\.ai-dev-loop\\Task-A', {
    fs: fakeFs,
    pathApi: path.win32,
    platform: 'win32',
  });
  const lower = await runtimeTransactionLock('c:\\repo\\.AI-DEV-LOOP\\task-a', {
    fs: fakeFs,
    pathApi: path.win32,
    platform: 'win32',
  });
  assert.equal(upper.identity, lower.identity);
  assert.equal(path.win32.basename(upper.lockPath), path.win32.basename(lower.lockPath));
});

test('deterministic Windows case aliases contend for the same runtime lock', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-windows-alias-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  let report;
  const reached = new Promise((resolve) => { report = resolve; });
  let release;
  const hold = new Promise((resolve) => { release = resolve; });
  const owner = withRuntimeDirectoryTransaction(
    path.join(root, 'Runtime-Task'),
    async () => {
      report();
      await hold;
    },
    { platform: 'win32' },
  );
  await reached;
  await assert.rejects(
    () => withRuntimeDirectoryTransaction(
      path.join(root, 'runtime-task'),
      async () => {},
      { platform: 'win32' },
    ),
    { code: 'OPERATION_IN_PROGRESS' },
  );
  release();
  await owner;
});

test('observer cannot clear a live runtime transaction lock', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-live-owner-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  let release;
  let reached;
  const paused = new Promise((resolve) => { reached = resolve; });
  const hold = new Promise((resolve) => { release = resolve; });
  const owner = withRuntimeDirectoryTransaction(targetPath, async () => {
    reached();
    await hold;
  });
  await paused;
  const lockName = (await readdir(root)).find((name) => name.startsWith('.gated-loop-runtime-') && name.endsWith('.lock'));
  assert.ok(lockName);
  const lockPath = path.join(root, lockName);
  const before = await readFile(lockPath, 'utf8');
  await assert.rejects(() => resolveAtomicDirectory(targetPath), { code: 'OPERATION_IN_PROGRESS' });
  assert.equal(await readFile(lockPath, 'utf8'), before);
  release();
  await owner;
});

test('prior owner never removes a successor token', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-successor-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  const successor = {
    version: 1,
    target: 'target',
    identity: 'successor',
    ownerPid: process.pid,
    token: 'b'.repeat(24),
    acquiredAt: '2030-01-01T00:00:00.000Z',
  };
  const error = await withRuntimeDirectoryTransaction(targetPath, async ({ lockPath }) => {
    await rm(lockPath);
    await writeFile(lockPath, `${JSON.stringify(successor)}\n`, { flag: 'wx' });
  }).then(() => undefined, (caught) => caught);
  assert.equal(error?.code, 'OPERATION_LOCK_OWNERSHIP_LOST');
  const lockName = (await readdir(root)).find((name) => name.startsWith('.gated-loop-runtime-') && name.endsWith('.lock'));
  assert.deepEqual(JSON.parse(await readFile(path.join(root, lockName), 'utf8')), successor);
});

test('lock release preserves a successor swapped between owner verification and cleanup', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-release-race-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  const descriptor = await runtimeTransactionLock(targetPath);
  const successor = {
    version: 1,
    target: 'target',
    identity: descriptor.identity,
    ownerPid: process.pid,
    token: 'd'.repeat(24),
    acquiredAt: '2030-01-01T00:00:00.000Z',
  };
  let swapped = false;
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'rename') return async (...args) => {
        if (!swapped && String(args[0]) === descriptor.lockPath && String(args[1]).includes('.release-')) {
          swapped = true;
          await target.rm(descriptor.lockPath);
          await target.writeFile(descriptor.lockPath, `${JSON.stringify(successor)}\n`, { flag: 'wx' });
        }
        return target.rename(...args);
      };
      return Reflect.get(target, property, receiver);
    },
  });
  const error = await withRuntimeDirectoryTransaction(
    targetPath,
    async () => {},
    { fs },
  ).then(() => undefined, (caught) => caught);
  assert.equal(error?.code, 'OPERATION_LOCK_OWNERSHIP_LOST');
  assert.equal(swapped, true);
  assert.deepEqual(JSON.parse(await readFile(descriptor.lockPath, 'utf8')), successor);
});

test('stale async ownership cannot commit after the on-disk token is replaced', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-stale-context-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  const error = await withRuntimeDirectoryTransaction(targetPath, async ({ lockPath, record }) => {
    await rm(lockPath);
    await writeFile(lockPath, `${JSON.stringify({ ...record, token: 'c'.repeat(24) })}\n`, { flag: 'wx' });
    return atomicReplaceDirectory(
      targetPath,
      (staging) => writeFile(path.join(staging, 'value.txt'), 'new'),
    );
  }).then(() => undefined, (caught) => caught);
  assert.equal(error?.code, 'OPERATION_LOCK_OWNERSHIP_LOST');
  assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'old');
});

test('atomic replacement restores the old directory when either commit rename fails', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-replace-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const failingRename of [1, 2]) {
    const targetPath = path.join(root, `target-${failingRename}`);
    await mkdir(targetPath);
    await writeFile(path.join(targetPath, 'value.txt'), 'old');
    let renameCalls = 0;
    const fs = new Proxy(fsPromises, {
      get(target, property, receiver) {
        if (property === 'rename') return async (...args) => {
          const swapsRuntimeDirectory = String(args[0]) === targetPath || String(args[1]) === targetPath;
          if (swapsRuntimeDirectory) {
            renameCalls++;
            if (renameCalls === failingRename) throw Object.assign(new Error('rename failed'), { code: 'EACCES' });
          }
          return target.rename(...args);
        };
        return Reflect.get(target, property, receiver);
      },
    });
    await assert.rejects(
      () => atomicReplaceDirectory(targetPath, (staging) => writeFile(path.join(staging, 'value.txt'), 'new'), { fs }),
      { code: 'EACCES' },
    );
    assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'old');
  }
});

test('atomic replacement reports restoration failure distinctly', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-restore-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  let renameCalls = 0;
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'rename') return async (...args) => {
        const swapsRuntimeDirectory = String(args[0]) === targetPath || String(args[1]) === targetPath;
        if (swapsRuntimeDirectory) {
          renameCalls++;
          if (renameCalls >= 2) throw Object.assign(new Error('rename failed'), { code: 'EACCES' });
        }
        return target.rename(...args);
      };
      return Reflect.get(target, property, receiver);
    },
  });
  const failure = await atomicReplaceDirectory(
    targetPath,
    (staging) => writeFile(path.join(staging, 'value.txt'), 'new'),
    { fs },
  ).then(() => undefined, (error) => error);
  assert.equal(failure?.code, 'ATOMIC_RESTORE_FAILED');
  assert.equal(failure.details.recovery.automaticRecovery, false);
  assert.equal(failure.details.recovery.recoveryRequired, true);
  assert.equal(failure.details.recovery.transaction.phase, 'restore-failed');
  assert.equal(failure.details.recovery.transaction.backup, failure.details.backup);
  assert.match(failure.details.recovery.transaction.staging, /target\.tmp-/);
  await assert.rejects(() => resolveAtomicDirectory(targetPath), { code: 'OPERATION_IN_PROGRESS' });
});

test('overlapping atomic writers cannot replace or clear the active owner lock', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-writers-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  let releaseRename;
  let reportGap;
  const gap = new Promise((resolve) => { reportGap = resolve; });
  const release = new Promise((resolve) => { releaseRename = resolve; });
  const pausedFs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'rename') return async (...args) => {
        if (String(args[1]) === targetPath) { reportGap(); await release; }
        return target.rename(...args);
      };
      return Reflect.get(target, property, receiver);
    },
  });

  const first = atomicReplaceDirectory(
    targetPath,
    (staging) => writeFile(path.join(staging, 'value.txt'), 'first'),
    { fs: pausedFs },
  );
  await gap;
  const markerName = (await readdir(root)).find((name) => name.startsWith('.gated-loop-runtime-') && name.endsWith('.lock'));
  assert.ok(markerName);
  const markerBefore = await readFile(path.join(root, markerName), 'utf8');
  await assert.rejects(
    () => atomicReplaceDirectory(targetPath, (staging) => writeFile(path.join(staging, 'value.txt'), 'second')),
    { code: 'OPERATION_IN_PROGRESS' },
  );
  assert.equal(await readFile(path.join(root, markerName), 'utf8'), markerBefore);
  const blocked = await resolveAtomicDirectory(targetPath).then(() => undefined, (error) => error);
  assert.equal(blocked?.code, 'OPERATION_IN_PROGRESS');
  assert.equal(blocked.details.recovery.transaction.phase, 'original-moved');
  assert.match(blocked.details.recovery.transaction.staging, /target\.tmp-/);
  assert.match(blocked.details.recovery.transaction.backup, /target\.backup\.tmp-/);
  assert.equal(await readFile(path.join(root, markerName), 'utf8'), markerBefore);
  releaseRename();
  await first;
  assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'first');
});

test('stale lock and transaction artifacts are never stolen automatically', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-dead-owner-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  const descriptor = await runtimeTransactionLock(targetPath);
  const stale = {
    version: 1,
    target: 'target',
    identity: descriptor.identity,
    ownerPid: 2_147_483_647,
    token: 'a'.repeat(24),
    acquiredAt: '2000-01-01T00:00:00.000Z',
  };
  const lockText = `${JSON.stringify(stale)}\n`;
  const staleStaging = `${targetPath}.tmp-stale`;
  const staleBackup = `${targetPath}.backup.tmp-stale`;
  await writeFile(descriptor.lockPath, lockText, { flag: 'wx' });
  await mkdir(staleStaging);
  await mkdir(staleBackup);
  await writeFile(path.join(staleStaging, 'value.txt'), 'stale-new');
  await writeFile(path.join(staleBackup, 'value.txt'), 'stale-old');
  const staleRecovery = {
    version: 1,
    token: stale.token,
    identity: descriptor.identity,
    original: descriptor.target,
    phase: 'original-moved',
    staging: staleStaging,
    backup: staleBackup,
    originalExisted: true,
  };
  await writeFile(descriptor.recoveryPath, `${JSON.stringify(staleRecovery)}\n`, { flag: 'wx' });

  const blocked = await atomicReplaceDirectory(
    targetPath,
    (staging) => writeFile(path.join(staging, 'value.txt'), 'replacement'),
  ).then(() => undefined, (error) => error);
  assert.equal(blocked?.code, 'OPERATION_IN_PROGRESS');
  assert.equal(blocked.details.recovery.owner.pid, stale.ownerPid);
  assert.equal(blocked.details.recovery.automaticRecovery, false);
  assert.deepEqual(blocked.details.recovery.transaction, staleRecovery);
  assert.equal(await readFile(descriptor.lockPath, 'utf8'), lockText);
  assert.deepEqual(JSON.parse(await readFile(descriptor.recoveryPath, 'utf8')), staleRecovery);
  assert.equal(await readFile(path.join(staleStaging, 'value.txt'), 'utf8'), 'stale-new');
  assert.equal(await readFile(path.join(staleBackup, 'value.txt'), 'utf8'), 'stale-old');
  assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'old');
});

test('orphaned recovery journal blocks a new owner even when the lock is absent', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-orphan-journal-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  const descriptor = await runtimeTransactionLock(targetPath);
  const transaction = {
    version: 1,
    token: 'e'.repeat(24),
    identity: descriptor.identity,
    original: descriptor.target,
    phase: 'installed',
    staging: `${targetPath}.tmp-orphan`,
    backup: `${targetPath}.backup.tmp-orphan`,
    originalExisted: true,
  };
  const journalText = `${JSON.stringify(transaction)}\n`;
  await writeFile(descriptor.recoveryPath, journalText, { flag: 'wx' });
  let populated = false;

  const blocked = await atomicReplaceDirectory(targetPath, async () => { populated = true; })
    .then(() => undefined, (error) => error);
  assert.equal(blocked?.code, 'OPERATION_IN_PROGRESS');
  assert.equal(blocked.details.recovery.owner, null);
  assert.deepEqual(blocked.details.recovery.transaction, transaction);
  assert.equal(populated, false);
  assert.equal(await readFile(descriptor.recoveryPath, 'utf8'), journalText);
  assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'old');
});

test('malformed stale lock returns explicit recovery metadata without touching artifacts', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-recovery-fallback-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  const descriptor = await runtimeTransactionLock(targetPath);
  const malformed = '{not-json\n';
  const artifact = `${targetPath}.tmp-orphan`;
  await writeFile(descriptor.lockPath, malformed, { flag: 'wx' });
  await mkdir(artifact);
  await writeFile(path.join(artifact, 'value.txt'), 'orphan');

  const blocked = await resolveAtomicDirectory(targetPath).then(() => undefined, (error) => error);
  assert.equal(blocked?.code, 'OPERATION_IN_PROGRESS');
  assert.equal(blocked.details.recovery.owner, null);
  assert.equal(blocked.details.recovery.recoveryRequired, true);
  assert.equal(await readFile(descriptor.lockPath, 'utf8'), malformed);
  assert.equal(await readFile(path.join(artifact, 'value.txt'), 'utf8'), 'orphan');
});

test('observer sees operation-in-progress during the atomic commit gap', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-reader-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  let releaseRename;
  let reportGap;
  const gap = new Promise((resolve) => { reportGap = resolve; });
  const release = new Promise((resolve) => { releaseRename = resolve; });
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'rename') return async (...args) => {
        if (String(args[1]) === targetPath) { reportGap(); await release; }
        return target.rename(...args);
      };
      return Reflect.get(target, property, receiver);
    },
  });
  const replacement = atomicReplaceDirectory(
    targetPath,
    (staging) => writeFile(path.join(staging, 'value.txt'), 'new'),
    { fs },
  );
  await gap;
  await assert.rejects(() => resolveAtomicDirectory(targetPath), { code: 'OPERATION_IN_PROGRESS' });
  releaseRename();
  await replacement;
  assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'new');
});

test('atomic replacement does not report failure after committed backup cleanup fails', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-cleanup-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const targetPath = path.join(root, 'target');
  await mkdir(targetPath);
  await writeFile(path.join(targetPath, 'value.txt'), 'old');
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'rm') return async (value, options) => {
        if (String(value).includes('.backup.tmp-')) throw Object.assign(new Error('cleanup failed'), { code: 'EACCES' });
        return target.rm(value, options);
      };
      return Reflect.get(target, property, receiver);
    },
  });
  await atomicReplaceDirectory(targetPath, (staging) => writeFile(path.join(staging, 'value.txt'), 'new'), { fs });
  assert.equal(await readFile(path.join(targetPath, 'value.txt'), 'utf8'), 'new');
});

test('hash helpers canonicalize paths and produce deterministic fingerprints', () => {
  assert.equal(canonicalRelativePath('.\\a\\..\\b//c'), 'b/c');
  assert.throws(() => canonicalRelativePath('../x'), { code: 'PATH_OUTSIDE_ROOT' });
  assert.equal(sha256Bytes(Buffer.from('abc')), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  const a = manifestFingerprint([{ path: 'b', sha256: '2' }, { path: 'a', sha256: '1' }]);
  const b = manifestFingerprint([{ sha256: '1', path: 'a' }, { sha256: '2', path: 'b' }]);
  assert.equal(a, b);
});

test('runProcess uses argv with shell false and omits successful streams', async () => {
  let options;
  const spawn = (file, args, supplied) => {
    options = supplied;
    assert.equal(file, 'tool');
    assert.deepEqual(args, ['a b', '$HOME']);
    return fakeChild({ stdout: 'secret stream', stderr: '' });
  };
  const result = await runProcess('tool', ['a b', '$HOME'], { spawn });
  assert.equal(options.shell, false);
  assert.deepEqual(result, { exitCode: 0, signal: null });
});

test('runProcess handles synchronous abort and timeout races', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(() => runProcess('tool', [], { signal: controller.signal, spawn: () => { throw new Error('must not spawn'); } }), { code: 'PROCESS_ABORTED' });
  let killed = 0;
  await assert.rejects(() => runProcess('tool', [], { timeoutMs: 5, spawn: () => fakeChild({ never: true, kill: () => { killed++; } }) }), { code: 'PROCESS_TIMEOUT' });
  assert.equal(killed, 1);
});

test('runProcess handles abort triggered synchronously by injected spawn exactly once', async () => {
  const controller = new AbortController();
  let killed = 0;
  const child = fakeChild({ never: true, kill: () => { killed++; } });
  await assert.rejects(() => runProcess('tool', [], {
    signal: controller.signal,
    spawn: () => { controller.abort(); return child; },
  }), { code: 'PROCESS_ABORTED' });
  controller.abort();
  assert.equal(killed, 1);
});

test('runProcess bounds multibyte diagnostics to 64KiB bytes', async () => {
  const spawn = () => fakeChild({ stdout: '\u754c'.repeat(30_000), stderr: '\u754c'.repeat(30_000), exitCode: 2 });
  await assert.rejects(() => runProcess('tool', [], { spawn }), (error) => {
    assert.equal(error.code, 'PROCESS_FAILED');
    assert.equal(Buffer.byteLength(error.details.stdout), 65_535);
    assert.equal(Buffer.byteLength(error.details.stderr), 65_535);
    assert.equal(error.details.stdout.includes('\uFFFD'), false);
    assert.equal(error.details.stderr.includes('\uFFFD'), false);
    return true;
  });
});

function fakeChild({ stdout = '', stderr = '', exitCode = 0, never = false, kill = () => {} }) {
  const listeners = new Map();
  const stream = (data) => ({ on(event, fn) { if (event === 'data' && data) queueMicrotask(() => fn(Buffer.from(data))); return this; } });
  const child = { stdout: stream(stdout), stderr: stream(stderr), on(event, fn) { listeners.set(event, fn); return child; }, kill };
  if (!never) queueMicrotask(() => queueMicrotask(() => listeners.get('close')?.(exitCode, null)));
  return child;
}
