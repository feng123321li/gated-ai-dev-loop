import * as fsPromises from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
import { AsyncLocalStorage } from 'node:async_hooks';
import { GatedLoopError } from './errors.mjs';

const RUNTIME_TRANSACTION_CONTEXT = new AsyncLocalStorage();
const PRESERVE_RUNTIME_LOCK = Symbol('preserveRuntimeLock');

function contained(root, target, pathApi) {
  const relative = pathApi.relative(root, target);
  return relative === '' || (!relative.startsWith(`..${pathApi.sep}`) && relative !== '..' && !pathApi.isAbsolute(relative));
}

export async function assertSafePath(root, candidate, { fs = fsPromises, pathApi = path } = {}) {
  const rootAbsolute = pathApi.resolve(root);
  const candidateText = String(candidate);
  const target = pathApi.isAbsolute(candidateText) ? pathApi.resolve(candidateText) : pathApi.resolve(rootAbsolute, candidateText);
  if (pathApi.parse(rootAbsolute).root.toLowerCase() !== pathApi.parse(target).root.toLowerCase()) {
    throw new GatedLoopError('PATH_CROSS_VOLUME', `Path is on another volume: ${candidate}`);
  }
  if (!contained(rootAbsolute, target, pathApi)) throw new GatedLoopError('PATH_OUTSIDE_ROOT', `Path escapes root: ${candidate}`);
  let rootReal;
  try {
    const rootStat = await fs.lstat(rootAbsolute);
    if (rootStat.isSymbolicLink()) throw new GatedLoopError('PATH_SYMLINK', `Symbolic link is not allowed: ${rootAbsolute}`);
    rootReal = await fs.realpath(rootAbsolute);
  } catch (error) { if (error.code === 'ENOENT') rootReal = rootAbsolute; else throw error; }
  const relative = pathApi.relative(rootAbsolute, target);
  const parts = relative ? relative.split(pathApi.sep) : [];
  let current = rootAbsolute;
  for (const part of parts) {
    current = pathApi.join(current, part);
    try {
      const stat = await fs.lstat(current);
      if (stat.isSymbolicLink()) throw new GatedLoopError('PATH_SYMLINK', `Symbolic link is not allowed: ${current}`);
      const real = await fs.realpath(current);
      if (!contained(rootReal, real, pathApi)) throw new GatedLoopError('PATH_OUTSIDE_ROOT', `Real path escapes root: ${current}`);
    } catch (error) {
      if (error.code === 'ENOENT') break;
      throw error;
    }
  }
  return target;
}

function fileChanged(target) {
  throw new GatedLoopError('PATH_FILE_CHANGED', `File changed while it was being opened: ${target}`);
}

function sameIdentity(left, right) {
  const valid = (value) => (typeof value === 'number' || typeof value === 'bigint') && value !== 0 && value !== 0n;
  return valid(left?.ino) && valid(right?.ino) && left.dev === right.dev && left.ino === right.ino;
}

function sameSnapshot(left, right) {
  const leftMtime = left?.mtimeNs ?? left?.mtimeMs;
  const rightMtime = right?.mtimeNs ?? right?.mtimeMs;
  const leftCtime = left?.ctimeNs ?? left?.ctimeMs;
  const rightCtime = right?.ctimeNs ?? right?.ctimeMs;
  return sameIdentity(left, right) && left.mode === right.mode && left.size === right.size
    && leftMtime === rightMtime && leftCtime === rightCtime;
}

export function sameFileSnapshot(left, right) { return sameSnapshot(left, right); }

export async function readSafeRegularFileSnapshot(root, candidate, { fs = fsPromises, pathApi = path } = {}) {
  const target = await assertSafePath(root, candidate, { fs, pathApi });
  const before = await fs.lstat(target, { bigint: true });
  if (before.isSymbolicLink() || !before.isFile()) fileChanged(target);

  let handle;
  try {
    const flags = fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0);
    handle = await fs.open(target, flags);
    const opened = await handle.stat({ bigint: true });
    if (!opened.isFile() || !sameSnapshot(before, opened)) fileChanged(target);

    await assertSafePath(root, candidate, { fs, pathApi });
    const stillLinked = await fs.lstat(target, { bigint: true });
    if (stillLinked.isSymbolicLink() || !sameSnapshot(opened, stillLinked)) fileChanged(target);

    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    const finalLink = await fs.lstat(target, { bigint: true });
    if (!sameSnapshot(opened, after) || finalLink.isSymbolicLink() || !sameSnapshot(after, finalLink)) fileChanged(target);
    return { bytes, snapshot: after };
  } finally {
    await handle?.close();
  }
}

export async function readSafeRegularFile(root, candidate, options = {}) {
  return (await readSafeRegularFileSnapshot(root, candidate, options)).bytes;
}

function stagingName(target) { return `${target}.tmp-${process.pid}-${randomBytes(6).toString('hex')}`; }

function runtimeLockText(value) { return `${JSON.stringify(value)}\n`; }

async function observedRuntimeRecovery(descriptor, fs) {
  try {
    const parsed = JSON.parse(await fs.readFile(descriptor.recoveryPath, 'utf8'));
    return { present: true, record: parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null };
  } catch (error) {
    if (error.code === 'ENOENT') return { present: false, record: null };
    return { present: true, record: null };
  }
}

function validRecoveryTransaction(descriptor, transaction, expectedToken) {
  return transaction && typeof transaction === 'object' && !Array.isArray(transaction)
    && transaction.version === 1
    && typeof transaction.token === 'string'
    && (!expectedToken || transaction.token === expectedToken)
    && transaction.identity === descriptor.identity
    && transaction.original === descriptor.target
    && typeof transaction.phase === 'string'
    && typeof transaction.staging === 'string'
    && (transaction.backup === null || typeof transaction.backup === 'string')
    && typeof transaction.originalExisted === 'boolean';
}

async function recoveryDetails(descriptor, fs, record, observedRecord = record, knownTransaction) {
  const owner = observedRecord && typeof observedRecord === 'object' ? {
    pid: observedRecord.ownerPid,
    token: observedRecord.token,
    acquiredAt: observedRecord.acquiredAt,
  } : null;
  const expectedToken = observedRecord?.token ?? record?.token;
  const observedRecovery = knownTransaction
    ? { present: true, record: knownTransaction }
    : await observedRuntimeRecovery(descriptor, fs).catch(() => ({ present: true, record: null }));
  const transaction = validRecoveryTransaction(descriptor, observedRecovery.record, expectedToken)
    ? observedRecovery.record
    : null;
  return {
    automaticRecovery: false,
    recoveryRequired: true,
    lockPath: descriptor.lockPath,
    recoveryPath: descriptor.recoveryPath,
    runtimeDirectory: descriptor.target,
    owner,
    transaction,
    artifactPatterns: [
      `${descriptor.target}.tmp-*`,
      `${descriptor.target}.backup.tmp-*`,
    ],
  };
}

async function observedRuntimeLock(descriptor, fs) {
  try { return JSON.parse(await fs.readFile(descriptor.lockPath, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') throw error;
    return null;
  }
}

async function operationInProgress(descriptor, fs, observedRecord) {
  return new GatedLoopError('OPERATION_IN_PROGRESS', 'Another runtime-directory operation is already active', {
    details: { recovery: await recoveryDetails(descriptor, fs, undefined, observedRecord) },
  });
}

export async function runtimeTransactionLock(target, {
  fs = fsPromises,
  pathApi = path,
  platform = process.platform,
  createParent = true,
} = {}) {
  const lexicalTarget = pathApi.resolve(target);
  const parent = pathApi.dirname(lexicalTarget);
  if (createParent) await fs.mkdir(parent, { recursive: true });
  const parentStat = await fs.lstat(parent);
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink()) {
    throw new GatedLoopError('ATOMIC_TARGET_INVALID', 'Runtime directory parent is invalid');
  }
  const realParent = await fs.realpath(parent);
  const canonicalTarget = pathApi.join(realParent, pathApi.basename(lexicalTarget));
  const normalized = pathApi.resolve(canonicalTarget).normalize('NFC');
  const identity = platform === 'win32' ? normalized.toLowerCase() : normalized;
  const hash = createHash('sha256').update(identity).digest('hex').slice(0, 24);
  const lockPath = pathApi.join(realParent, `.gated-loop-runtime-${hash}.lock`);
  return {
    identity,
    target: canonicalTarget,
    lockPath,
    recoveryPath: `${lockPath}.recovery.json`,
  };
}

async function restoreClaimWithoutReplacingSuccessor(claimedPath, originalPath, fs) {
  try { await fs.link(claimedPath, originalPath); }
  catch { return; }
  await fs.rm(claimedPath).catch(() => {});
}

async function claimOwnedRuntimeFile(filePath, token, isOwned, fs) {
  let before;
  let observed = null;
  try {
    before = await fs.lstat(filePath, { bigint: true });
    if (before.isSymbolicLink() || !before.isFile()) return { status: 'lost', observed };
    observed = JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return { status: 'lost', observed: null };
    if (error instanceof SyntaxError) return { status: 'lost', observed: null };
    return { status: 'cleanup-failed', error, observed };
  }
  if (!isOwned(observed)) return { status: 'lost', observed };

  const claimedPath = `${filePath}.release-${process.pid}-${token}-${randomBytes(4).toString('hex')}`;
  try { await fs.rename(filePath, claimedPath); }
  catch (error) {
    if (error.code === 'ENOENT') return { status: 'lost', observed: null };
    return { status: 'cleanup-failed', error, observed };
  }

  let claimedStat;
  let claimedRecord = null;
  try {
    claimedStat = await fs.lstat(claimedPath, { bigint: true });
    claimedRecord = JSON.parse(await fs.readFile(claimedPath, 'utf8'));
  } catch (error) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    if (error.code === 'ENOENT' || error instanceof SyntaxError) {
      return { status: 'lost', observed: claimedRecord };
    }
    return { status: 'cleanup-failed', error, observed: claimedRecord };
  }

  if (!sameIdentity(before, claimedStat) || !isOwned(claimedRecord)) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    return { status: 'lost', observed: claimedRecord };
  }
  try { await fs.rm(claimedPath); }
  catch (error) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    return { status: 'cleanup-failed', error, observed: claimedRecord };
  }
  return { status: 'removed', observed: claimedRecord };
}

async function releaseRuntimeTransaction(descriptor, record, fs) {
  const result = await claimOwnedRuntimeFile(
    descriptor.lockPath,
    record.token,
    (observed) => observed?.token === record.token && observed?.identity === record.identity,
    fs,
  );
  if (result.status === 'lost') {
    throw new GatedLoopError('OPERATION_LOCK_OWNERSHIP_LOST', 'Runtime operation lock is no longer owned by this token', {
      details: { recovery: await recoveryDetails(descriptor, fs, record, result.observed) },
    });
  }
  if (result.status === 'cleanup-failed') {
    throw new GatedLoopError('OPERATION_LOCK_CLEANUP_FAILED', 'Unable to remove the owned runtime operation lock', {
      details: {
        cleanupCode: result.error?.code,
        recovery: await recoveryDetails(descriptor, fs, record),
      },
    });
  }
}

function activeRuntimeTransaction(descriptor) {
  return RUNTIME_TRANSACTION_CONTEXT.getStore()?.get(descriptor.identity);
}

function preserveRuntimeLock(error) {
  if (error && (typeof error === 'object' || typeof error === 'function')) error[PRESERVE_RUNTIME_LOCK] = true;
  return error;
}

async function runtimeOwnershipLost(descriptor, record, fs, observed = null) {
  return new GatedLoopError('OPERATION_LOCK_OWNERSHIP_LOST', 'Runtime operation lock is no longer owned by this token', {
    details: { recovery: await recoveryDetails(descriptor, fs, record, observed) },
  });
}

async function assertRuntimeTransactionOwner(transaction, fs) {
  let observed;
  try { observed = await observedRuntimeLock(transaction.descriptor, fs); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    throw await runtimeOwnershipLost(transaction.descriptor, transaction.record, fs, null);
  }
  if (observed?.token !== transaction.record.token || observed?.identity !== transaction.record.identity) {
    throw await runtimeOwnershipLost(transaction.descriptor, transaction.record, fs, observed);
  }
}

function recoveryTransaction(transaction, { phase, staging, backup, originalExisted }) {
  return {
    version: 1,
    token: transaction.record.token,
    identity: transaction.descriptor.identity,
    original: transaction.descriptor.target,
    phase,
    staging,
    backup,
    originalExisted,
  };
}

async function beginRuntimeRecovery(transaction, details, fs) {
  const recovery = recoveryTransaction(transaction, details);
  try {
    await fs.writeFile(
      transaction.descriptor.recoveryPath,
      runtimeLockText(recovery),
      { encoding: 'utf8', flag: 'wx' },
    );
  } catch (error) {
    if (error.code === 'EEXIST') {
      throw preserveRuntimeLock(await operationInProgress(transaction.descriptor, fs, transaction.record));
    }
    throw error;
  }
  transaction.recovery = recovery;
  return recovery;
}

async function updateRuntimeRecovery(transaction, phase, fs) {
  const recovery = { ...transaction.recovery, phase };
  try { await atomicWriteFile(transaction.descriptor.recoveryPath, runtimeLockText(recovery), { fs }); }
  catch (error) { throw preserveRuntimeLock(error); }
  transaction.recovery = recovery;
  return recovery;
}

async function removeRuntimeRecovery(transaction, fs) {
  const result = await claimOwnedRuntimeFile(
    transaction.descriptor.recoveryPath,
    transaction.record.token,
    (observed) => validRecoveryTransaction(
      transaction.descriptor,
      observed,
      transaction.record.token,
    ),
    fs,
  );
  if (result.status === 'removed') {
    transaction.recovery = null;
    return;
  }
  const code = result.status === 'lost'
    ? 'OPERATION_RECOVERY_OWNERSHIP_LOST'
    : 'OPERATION_RECOVERY_CLEANUP_FAILED';
  const message = result.status === 'lost'
    ? 'Runtime recovery journal is no longer owned by this token'
    : 'Unable to remove the owned runtime recovery journal';
  throw preserveRuntimeLock(new GatedLoopError(code, message, {
    details: {
      cleanupCode: result.error?.code,
      recovery: await recoveryDetails(
        transaction.descriptor,
        fs,
        transaction.record,
        transaction.record,
        transaction.recovery,
      ),
    },
  }));
}

export async function withRuntimeDirectoryTransaction(target, operation, {
  fs = fsPromises,
  pathApi = path,
  platform = process.platform,
  now = () => new Date(),
} = {}) {
  if (typeof operation !== 'function') throw new TypeError('Runtime transaction operation must be a function');
  const descriptor = await runtimeTransactionLock(target, { fs, pathApi, platform });
  const priorRecovery = await observedRuntimeRecovery(descriptor, fs);
  if (priorRecovery.present) {
    const observed = await observedRuntimeLock(descriptor, fs).catch(() => null);
    throw await operationInProgress(descriptor, fs, observed);
  }
  const token = randomBytes(12).toString('hex');
  const acquired = typeof now === 'function' ? now() : now;
  const acquiredAt = (acquired instanceof Date ? acquired : new Date(acquired)).toISOString();
  const record = {
    version: 1,
    target: pathApi.basename(descriptor.target),
    identity: descriptor.identity,
    ownerPid: process.pid,
    token,
    acquiredAt,
  };
  try { await fs.writeFile(descriptor.lockPath, runtimeLockText(record), { encoding: 'utf8', flag: 'wx' }); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const observed = await observedRuntimeLock(descriptor, fs).catch(() => null);
    throw await operationInProgress(descriptor, fs, observed);
  }

  const racedRecovery = await observedRuntimeRecovery(descriptor, fs);
  if (racedRecovery.present) {
    const blocked = await operationInProgress(descriptor, fs, record);
    await releaseRuntimeTransaction(descriptor, record, fs);
    throw blocked;
  }

  const inherited = RUNTIME_TRANSACTION_CONTEXT.getStore() ?? new Map();
  const context = new Map(inherited);
  context.set(descriptor.identity, { descriptor, record });
  let preserve = false;
  try {
    return await RUNTIME_TRANSACTION_CONTEXT.run(
      context,
      () => operation({ ...descriptor, token, record }),
    );
  } catch (error) {
    preserve = error?.[PRESERVE_RUNTIME_LOCK] === true;
    throw error;
  } finally {
    if (!preserve) await releaseRuntimeTransaction(descriptor, record, fs);
  }
}

export async function resolveAtomicDirectory(target, {
  fs = fsPromises,
  pathApi = path,
  platform = process.platform,
} = {}) {
  const descriptor = await runtimeTransactionLock(target, {
    fs, pathApi, platform, createParent: false,
  });
  let observed;
  let lockPresent = false;
  try {
    observed = await observedRuntimeLock(descriptor, fs);
    lockPresent = true;
  }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const active = activeRuntimeTransaction(descriptor);
  if (lockPresent) {
    if (!observed || active?.record.token !== observed.token || active.record.identity !== observed.identity) {
      throw await operationInProgress(descriptor, fs, observed);
    }
    const recovery = await observedRuntimeRecovery(descriptor, fs);
    if (recovery.present && !validRecoveryTransaction(descriptor, recovery.record, active.record.token)) {
      throw await operationInProgress(descriptor, fs, observed);
    }
  } else {
    const recovery = await observedRuntimeRecovery(descriptor, fs);
    if (active) throw await runtimeOwnershipLost(descriptor, active.record, fs, null);
    if (recovery.present) throw await operationInProgress(descriptor, fs, null);
  }
  const stat = await fs.lstat(descriptor.target);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new GatedLoopError('ATOMIC_TARGET_INVALID', 'Atomic directory target is invalid');
  }
  return descriptor.target;
}

export async function atomicWriteFile(target, content, { fs = fsPromises, beforeRename } = {}) {
  const staging = stagingName(target);
  await fs.mkdir(path.dirname(target), { recursive: true });
  try {
    await fs.writeFile(staging, content, { encoding: 'utf8', flag: 'wx' });
    if (beforeRename) await beforeRename(staging);
    await fs.rename(staging, target);
  } catch (error) {
    await fs.rm(staging, { force: true }).catch(() => {});
    throw error;
  }
}

export async function atomicWriteDirectory(target, populate, {
  fs = fsPromises,
  pathApi = path,
  platform = process.platform,
} = {}) {
  const descriptor = await runtimeTransactionLock(target, { fs, pathApi, platform });
  const active = activeRuntimeTransaction(descriptor);
  const write = async (transaction) => {
    const lockedTarget = transaction.descriptor.target;
    const staging = stagingName(lockedTarget);
    let recoveryStarted = false;
    let installed = false;
    await assertRuntimeTransactionOwner(transaction, fs);
    let existing;
    try { existing = await fs.lstat(lockedTarget); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (existing) {
      const error = new Error('Atomic directory target already exists');
      error.code = 'EEXIST';
      throw error;
    }
    await beginRuntimeRecovery(transaction, {
      phase: 'staging',
      staging,
      backup: null,
      originalExisted: false,
    }, fs);
    recoveryStarted = true;
    try {
      await fs.mkdir(staging);
      await populate(staging);
      await updateRuntimeRecovery(transaction, 'staged', fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await updateRuntimeRecovery(transaction, 'commit-pending', fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await fs.rename(staging, lockedTarget);
      installed = true;
      await updateRuntimeRecovery(transaction, 'installed', fs);
      await removeRuntimeRecovery(transaction, fs);
    } catch (error) {
      if (!installed) {
        await fs.rm(staging, { recursive: true, force: true }).catch(() => {});
        if (recoveryStarted) {
          try { await removeRuntimeRecovery(transaction, fs); }
          catch (cleanupError) { throw cleanupError; }
        }
        if (error && (typeof error === 'object' || typeof error === 'function')) {
          delete error[PRESERVE_RUNTIME_LOCK];
        }
      } else {
        preserveRuntimeLock(error);
      }
      throw error;
    }
  };

  if (active) return write(active);
  return withRuntimeDirectoryTransaction(
    descriptor.target,
    ({ record, ...ownedDescriptor }) => write({ descriptor: ownedDescriptor, record }),
    { fs, pathApi, platform },
  );
}

export async function atomicReplaceDirectory(target, populate, {
  fs = fsPromises,
  beforeSwap,
  validateUnderLock,
  pathApi = path,
  platform = process.platform,
} = {}) {
  const descriptor = await runtimeTransactionLock(target, { fs, pathApi, platform });
  const replace = async (transaction) => {
    const lockedTarget = transaction.descriptor.target;
    const staging = stagingName(lockedTarget);
    let backup = null;
    let movedExisting = false;
    let installed = false;
    let recoveryStarted = false;
    await assertRuntimeTransactionOwner(transaction, fs);
    let existing;
    try { existing = await fs.lstat(lockedTarget); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (existing && (!existing.isDirectory() || existing.isSymbolicLink())) {
      throw new GatedLoopError('ATOMIC_TARGET_INVALID', 'Atomic directory target is invalid');
    }
    if (existing) backup = stagingName(`${lockedTarget}.backup`);
    await beginRuntimeRecovery(transaction, {
      phase: 'staging',
      staging,
      backup,
      originalExisted: Boolean(existing),
    }, fs);
    recoveryStarted = true;
    try {
      await fs.mkdir(staging);
      await populate(staging);
      if (beforeSwap) await beforeSwap(staging);
      if (validateUnderLock) await validateUnderLock(staging);
      await updateRuntimeRecovery(transaction, 'staged', fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await updateRuntimeRecovery(transaction, 'commit-pending', fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      if (existing) {
        await fs.rename(lockedTarget, backup);
        movedExisting = true;
        await updateRuntimeRecovery(transaction, 'original-moved', fs);
      }
      await fs.rename(staging, lockedTarget);
      installed = true;
      await updateRuntimeRecovery(transaction, 'installed', fs);
      if (movedExisting) await fs.rm(backup, { recursive: true, force: true }).catch(() => {});
      await removeRuntimeRecovery(transaction, fs);
    } catch (error) {
      if (installed) {
        throw preserveRuntimeLock(error);
      }
      if (movedExisting) {
        try {
          await fs.rename(backup, lockedTarget);
          movedExisting = false;
        } catch (restoreError) {
          const failedRecovery = { ...transaction.recovery, phase: 'restore-failed' };
          try { await updateRuntimeRecovery(transaction, 'restore-failed', fs); }
          catch { /* The preceding durable phase and exact paths remain for manual recovery. */ }
          const failure = new GatedLoopError(
            'ATOMIC_RESTORE_FAILED',
            'Unable to restore the previous directory after a failed replacement',
            {
              details: {
                installCode: error.code,
                restoreCode: restoreError.code,
                backup,
                recovery: await recoveryDetails(
                  transaction.descriptor,
                  fs,
                  transaction.record,
                  transaction.record,
                  transaction.recovery ?? failedRecovery,
                ),
              },
            },
          );
          throw preserveRuntimeLock(failure);
        }
      }
      if (!installed) {
        await fs.rm(staging, { recursive: true, force: true }).catch(() => {});
      }
      if (recoveryStarted) {
        try { await removeRuntimeRecovery(transaction, fs); }
        catch (cleanupError) { throw cleanupError; }
      }
      if (error && (typeof error === 'object' || typeof error === 'function')) {
        delete error[PRESERVE_RUNTIME_LOCK];
      }
      throw error;
    }
  };

  const active = activeRuntimeTransaction(descriptor);
  if (active) return replace(active);
  return withRuntimeDirectoryTransaction(
    descriptor.target,
    ({ record, ...ownedDescriptor }) => replace({
      descriptor: ownedDescriptor,
      record,
    }),
    { fs, pathApi, platform },
  );
}
