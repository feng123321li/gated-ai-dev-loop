#!/usr/bin/env node

// src/cli/hierarchical.mjs
import * as fsPromises3 from "node:fs/promises";

// src/core/errors.mjs
var GatedLoopError = class extends Error {
  constructor(code, message, { exitCode = 1, details = {} } = {}) {
    super(message);
    this.name = "GatedLoopError";
    this.code = code;
    this.exitCode = exitCode;
    this.details = details;
  }
};

// src/core/fs-safe.mjs
import * as fsPromises from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import path from "node:path";
import { createHash, randomBytes } from "node:crypto";
import { AsyncLocalStorage } from "node:async_hooks";
var RUNTIME_TRANSACTION_CONTEXT = new AsyncLocalStorage();
var PRESERVE_RUNTIME_LOCK = /* @__PURE__ */ Symbol("preserveRuntimeLock");
function contained(root, target, pathApi) {
  const relative = pathApi.relative(root, target);
  return relative === "" || !relative.startsWith(`..${pathApi.sep}`) && relative !== ".." && !pathApi.isAbsolute(relative);
}
async function assertSafePath(root, candidate, { fs = fsPromises, pathApi = path } = {}) {
  const rootAbsolute = pathApi.resolve(root);
  const candidateText = String(candidate);
  const target = pathApi.isAbsolute(candidateText) ? pathApi.resolve(candidateText) : pathApi.resolve(rootAbsolute, candidateText);
  if (pathApi.parse(rootAbsolute).root.toLowerCase() !== pathApi.parse(target).root.toLowerCase()) {
    throw new GatedLoopError("PATH_CROSS_VOLUME", `Path is on another volume: ${candidate}`);
  }
  if (!contained(rootAbsolute, target, pathApi)) throw new GatedLoopError("PATH_OUTSIDE_ROOT", `Path escapes root: ${candidate}`);
  let rootReal;
  try {
    const rootStat = await fs.lstat(rootAbsolute);
    if (rootStat.isSymbolicLink()) throw new GatedLoopError("PATH_SYMLINK", `Symbolic link is not allowed: ${rootAbsolute}`);
    rootReal = await fs.realpath(rootAbsolute);
  } catch (error) {
    if (error.code === "ENOENT") rootReal = rootAbsolute;
    else throw error;
  }
  const relative = pathApi.relative(rootAbsolute, target);
  const parts = relative ? relative.split(pathApi.sep) : [];
  let current = rootAbsolute;
  for (const part of parts) {
    current = pathApi.join(current, part);
    try {
      const stat = await fs.lstat(current);
      if (stat.isSymbolicLink()) throw new GatedLoopError("PATH_SYMLINK", `Symbolic link is not allowed: ${current}`);
      const real = await fs.realpath(current);
      if (!contained(rootReal, real, pathApi)) throw new GatedLoopError("PATH_OUTSIDE_ROOT", `Real path escapes root: ${current}`);
    } catch (error) {
      if (error.code === "ENOENT") break;
      throw error;
    }
  }
  return target;
}
function fileChanged(target) {
  throw new GatedLoopError("PATH_FILE_CHANGED", `File changed while it was being opened: ${target}`);
}
function sameIdentity(left, right) {
  const valid = (value) => (typeof value === "number" || typeof value === "bigint") && value !== 0 && value !== 0n;
  return valid(left?.ino) && valid(right?.ino) && left.dev === right.dev && left.ino === right.ino;
}
function sameSnapshot(left, right) {
  const leftMtime = left?.mtimeNs ?? left?.mtimeMs;
  const rightMtime = right?.mtimeNs ?? right?.mtimeMs;
  const leftCtime = left?.ctimeNs ?? left?.ctimeMs;
  const rightCtime = right?.ctimeNs ?? right?.ctimeMs;
  return sameIdentity(left, right) && left.mode === right.mode && left.size === right.size && leftMtime === rightMtime && leftCtime === rightCtime;
}
async function readSafeRegularFileSnapshot(root, candidate, { fs = fsPromises, pathApi = path } = {}) {
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
async function readSafeRegularFile(root, candidate, options = {}) {
  return (await readSafeRegularFileSnapshot(root, candidate, options)).bytes;
}
function stagingName(target) {
  return `${target}.tmp-${process.pid}-${randomBytes(6).toString("hex")}`;
}
function runtimeLockText(value) {
  return `${JSON.stringify(value)}
`;
}
async function observedRuntimeRecovery(descriptor, fs) {
  try {
    const parsed = JSON.parse(await fs.readFile(descriptor.recoveryPath, "utf8"));
    return { present: true, record: parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null };
  } catch (error) {
    if (error.code === "ENOENT") return { present: false, record: null };
    return { present: true, record: null };
  }
}
function validRecoveryTransaction(descriptor, transaction, expectedToken) {
  return transaction && typeof transaction === "object" && !Array.isArray(transaction) && transaction.version === 1 && typeof transaction.token === "string" && (!expectedToken || transaction.token === expectedToken) && transaction.identity === descriptor.identity && transaction.original === descriptor.target && typeof transaction.phase === "string" && typeof transaction.staging === "string" && (transaction.backup === null || typeof transaction.backup === "string") && typeof transaction.originalExisted === "boolean";
}
async function recoveryDetails(descriptor, fs, record, observedRecord = record, knownTransaction) {
  const owner = observedRecord && typeof observedRecord === "object" ? {
    pid: observedRecord.ownerPid,
    token: observedRecord.token,
    acquiredAt: observedRecord.acquiredAt
  } : null;
  const expectedToken = observedRecord?.token ?? record?.token;
  const observedRecovery = knownTransaction ? { present: true, record: knownTransaction } : await observedRuntimeRecovery(descriptor, fs).catch(() => ({ present: true, record: null }));
  const transaction = validRecoveryTransaction(descriptor, observedRecovery.record, expectedToken) ? observedRecovery.record : null;
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
      `${descriptor.target}.backup.tmp-*`
    ]
  };
}
async function observedRuntimeLock(descriptor, fs) {
  try {
    return JSON.parse(await fs.readFile(descriptor.lockPath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") throw error;
    return null;
  }
}
async function operationInProgress(descriptor, fs, observedRecord) {
  return new GatedLoopError("OPERATION_IN_PROGRESS", "Another runtime-directory operation is already active", {
    details: { recovery: await recoveryDetails(descriptor, fs, void 0, observedRecord) }
  });
}
async function runtimeTransactionLock(target, {
  fs = fsPromises,
  pathApi = path,
  platform = process.platform,
  createParent = true
} = {}) {
  const lexicalTarget = pathApi.resolve(target);
  const parent = pathApi.dirname(lexicalTarget);
  if (createParent) await fs.mkdir(parent, { recursive: true });
  const parentStat = await fs.lstat(parent);
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink()) {
    throw new GatedLoopError("ATOMIC_TARGET_INVALID", "Runtime directory parent is invalid");
  }
  const realParent = await fs.realpath(parent);
  const canonicalTarget = pathApi.join(realParent, pathApi.basename(lexicalTarget));
  const normalized = pathApi.resolve(canonicalTarget).normalize("NFC");
  const identity = platform === "win32" ? normalized.toLowerCase() : normalized;
  const hash = createHash("sha256").update(identity).digest("hex").slice(0, 24);
  const lockPath = pathApi.join(realParent, `.gated-loop-runtime-${hash}.lock`);
  return {
    identity,
    target: canonicalTarget,
    lockPath,
    recoveryPath: `${lockPath}.recovery.json`
  };
}
async function restoreClaimWithoutReplacingSuccessor(claimedPath, originalPath, fs) {
  try {
    await fs.link(claimedPath, originalPath);
  } catch {
    return;
  }
  await fs.rm(claimedPath).catch(() => {
  });
}
async function claimOwnedRuntimeFile(filePath, token, isOwned, fs) {
  let before;
  let observed = null;
  try {
    before = await fs.lstat(filePath, { bigint: true });
    if (before.isSymbolicLink() || !before.isFile()) return { status: "lost", observed };
    observed = JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return { status: "lost", observed: null };
    if (error instanceof SyntaxError) return { status: "lost", observed: null };
    return { status: "cleanup-failed", error, observed };
  }
  if (!isOwned(observed)) return { status: "lost", observed };
  const claimedPath = `${filePath}.release-${process.pid}-${token}-${randomBytes(4).toString("hex")}`;
  try {
    await fs.rename(filePath, claimedPath);
  } catch (error) {
    if (error.code === "ENOENT") return { status: "lost", observed: null };
    return { status: "cleanup-failed", error, observed };
  }
  let claimedStat;
  let claimedRecord = null;
  try {
    claimedStat = await fs.lstat(claimedPath, { bigint: true });
    claimedRecord = JSON.parse(await fs.readFile(claimedPath, "utf8"));
  } catch (error) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    if (error.code === "ENOENT" || error instanceof SyntaxError) {
      return { status: "lost", observed: claimedRecord };
    }
    return { status: "cleanup-failed", error, observed: claimedRecord };
  }
  if (!sameIdentity(before, claimedStat) || !isOwned(claimedRecord)) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    return { status: "lost", observed: claimedRecord };
  }
  try {
    await fs.rm(claimedPath);
  } catch (error) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    return { status: "cleanup-failed", error, observed: claimedRecord };
  }
  return { status: "removed", observed: claimedRecord };
}
async function releaseRuntimeTransaction(descriptor, record, fs) {
  const result = await claimOwnedRuntimeFile(
    descriptor.lockPath,
    record.token,
    (observed) => observed?.token === record.token && observed?.identity === record.identity,
    fs
  );
  if (result.status === "lost") {
    throw new GatedLoopError("OPERATION_LOCK_OWNERSHIP_LOST", "Runtime operation lock is no longer owned by this token", {
      details: { recovery: await recoveryDetails(descriptor, fs, record, result.observed) }
    });
  }
  if (result.status === "cleanup-failed") {
    throw new GatedLoopError("OPERATION_LOCK_CLEANUP_FAILED", "Unable to remove the owned runtime operation lock", {
      details: {
        cleanupCode: result.error?.code,
        recovery: await recoveryDetails(descriptor, fs, record)
      }
    });
  }
}
function activeRuntimeTransaction(descriptor) {
  return RUNTIME_TRANSACTION_CONTEXT.getStore()?.get(descriptor.identity);
}
function preserveRuntimeLock(error) {
  if (error && (typeof error === "object" || typeof error === "function")) error[PRESERVE_RUNTIME_LOCK] = true;
  return error;
}
async function runtimeOwnershipLost(descriptor, record, fs, observed = null) {
  return new GatedLoopError("OPERATION_LOCK_OWNERSHIP_LOST", "Runtime operation lock is no longer owned by this token", {
    details: { recovery: await recoveryDetails(descriptor, fs, record, observed) }
  });
}
async function assertRuntimeTransactionOwner(transaction, fs) {
  let observed;
  try {
    observed = await observedRuntimeLock(transaction.descriptor, fs);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
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
    originalExisted
  };
}
async function beginRuntimeRecovery(transaction, details, fs) {
  const recovery = recoveryTransaction(transaction, details);
  try {
    await fs.writeFile(
      transaction.descriptor.recoveryPath,
      runtimeLockText(recovery),
      { encoding: "utf8", flag: "wx" }
    );
  } catch (error) {
    if (error.code === "EEXIST") {
      throw preserveRuntimeLock(await operationInProgress(transaction.descriptor, fs, transaction.record));
    }
    throw error;
  }
  transaction.recovery = recovery;
  return recovery;
}
async function updateRuntimeRecovery(transaction, phase, fs) {
  const recovery = { ...transaction.recovery, phase };
  try {
    await atomicWriteFile(transaction.descriptor.recoveryPath, runtimeLockText(recovery), { fs });
  } catch (error) {
    throw preserveRuntimeLock(error);
  }
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
      transaction.record.token
    ),
    fs
  );
  if (result.status === "removed") {
    transaction.recovery = null;
    return;
  }
  const code = result.status === "lost" ? "OPERATION_RECOVERY_OWNERSHIP_LOST" : "OPERATION_RECOVERY_CLEANUP_FAILED";
  const message = result.status === "lost" ? "Runtime recovery journal is no longer owned by this token" : "Unable to remove the owned runtime recovery journal";
  throw preserveRuntimeLock(new GatedLoopError(code, message, {
    details: {
      cleanupCode: result.error?.code,
      recovery: await recoveryDetails(
        transaction.descriptor,
        fs,
        transaction.record,
        transaction.record,
        transaction.recovery
      )
    }
  }));
}
async function withRuntimeDirectoryTransaction(target, operation, {
  fs = fsPromises,
  pathApi = path,
  platform = process.platform,
  now = () => /* @__PURE__ */ new Date()
} = {}) {
  if (typeof operation !== "function") throw new TypeError("Runtime transaction operation must be a function");
  const descriptor = await runtimeTransactionLock(target, { fs, pathApi, platform });
  const priorRecovery = await observedRuntimeRecovery(descriptor, fs);
  if (priorRecovery.present) {
    const observed = await observedRuntimeLock(descriptor, fs).catch(() => null);
    throw await operationInProgress(descriptor, fs, observed);
  }
  const token = randomBytes(12).toString("hex");
  const acquired = typeof now === "function" ? now() : now;
  const acquiredAt = (acquired instanceof Date ? acquired : new Date(acquired)).toISOString();
  const record = {
    version: 1,
    target: pathApi.basename(descriptor.target),
    identity: descriptor.identity,
    ownerPid: process.pid,
    token,
    acquiredAt
  };
  try {
    await fs.writeFile(descriptor.lockPath, runtimeLockText(record), { encoding: "utf8", flag: "wx" });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const observed = await observedRuntimeLock(descriptor, fs).catch(() => null);
    throw await operationInProgress(descriptor, fs, observed);
  }
  const racedRecovery = await observedRuntimeRecovery(descriptor, fs);
  if (racedRecovery.present) {
    const blocked = await operationInProgress(descriptor, fs, record);
    await releaseRuntimeTransaction(descriptor, record, fs);
    throw blocked;
  }
  const inherited = RUNTIME_TRANSACTION_CONTEXT.getStore() ?? /* @__PURE__ */ new Map();
  const context = new Map(inherited);
  context.set(descriptor.identity, { descriptor, record });
  let preserve = false;
  try {
    return await RUNTIME_TRANSACTION_CONTEXT.run(
      context,
      () => operation({ ...descriptor, token, record })
    );
  } catch (error) {
    preserve = error?.[PRESERVE_RUNTIME_LOCK] === true;
    throw error;
  } finally {
    if (!preserve) await releaseRuntimeTransaction(descriptor, record, fs);
  }
}
async function atomicWriteFile(target, content, { fs = fsPromises, beforeRename } = {}) {
  const staging = stagingName(target);
  await fs.mkdir(path.dirname(target), { recursive: true });
  try {
    await fs.writeFile(staging, content, { encoding: "utf8", flag: "wx" });
    if (beforeRename) await beforeRename(staging);
    await fs.rename(staging, target);
  } catch (error) {
    await fs.rm(staging, { force: true }).catch(() => {
    });
    throw error;
  }
}
async function atomicWriteDirectory(target, populate, {
  fs = fsPromises,
  pathApi = path,
  platform = process.platform
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
    try {
      existing = await fs.lstat(lockedTarget);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (existing) {
      const error = new Error("Atomic directory target already exists");
      error.code = "EEXIST";
      throw error;
    }
    await beginRuntimeRecovery(transaction, {
      phase: "staging",
      staging,
      backup: null,
      originalExisted: false
    }, fs);
    recoveryStarted = true;
    try {
      await fs.mkdir(staging);
      await populate(staging);
      await updateRuntimeRecovery(transaction, "staged", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await updateRuntimeRecovery(transaction, "commit-pending", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await fs.rename(staging, lockedTarget);
      installed = true;
      await updateRuntimeRecovery(transaction, "installed", fs);
      await removeRuntimeRecovery(transaction, fs);
    } catch (error) {
      if (!installed) {
        await fs.rm(staging, { recursive: true, force: true }).catch(() => {
        });
        if (recoveryStarted) {
          try {
            await removeRuntimeRecovery(transaction, fs);
          } catch (cleanupError) {
            throw cleanupError;
          }
        }
        if (error && (typeof error === "object" || typeof error === "function")) {
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
    { fs, pathApi, platform }
  );
}
async function atomicReplaceDirectory(target, populate, {
  fs = fsPromises,
  beforeSwap,
  validateUnderLock,
  pathApi = path,
  platform = process.platform
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
    try {
      existing = await fs.lstat(lockedTarget);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (existing && (!existing.isDirectory() || existing.isSymbolicLink())) {
      throw new GatedLoopError("ATOMIC_TARGET_INVALID", "Atomic directory target is invalid");
    }
    if (existing) backup = stagingName(`${lockedTarget}.backup`);
    await beginRuntimeRecovery(transaction, {
      phase: "staging",
      staging,
      backup,
      originalExisted: Boolean(existing)
    }, fs);
    recoveryStarted = true;
    try {
      await fs.mkdir(staging);
      await populate(staging);
      if (beforeSwap) await beforeSwap(staging);
      if (validateUnderLock) await validateUnderLock(staging);
      await updateRuntimeRecovery(transaction, "staged", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await updateRuntimeRecovery(transaction, "commit-pending", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      if (existing) {
        await fs.rename(lockedTarget, backup);
        movedExisting = true;
        await updateRuntimeRecovery(transaction, "original-moved", fs);
      }
      await fs.rename(staging, lockedTarget);
      installed = true;
      await updateRuntimeRecovery(transaction, "installed", fs);
      if (movedExisting) await fs.rm(backup, { recursive: true, force: true }).catch(() => {
      });
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
          const failedRecovery = { ...transaction.recovery, phase: "restore-failed" };
          try {
            await updateRuntimeRecovery(transaction, "restore-failed", fs);
          } catch {
          }
          const failure = new GatedLoopError(
            "ATOMIC_RESTORE_FAILED",
            "Unable to restore the previous directory after a failed replacement",
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
                  transaction.recovery ?? failedRecovery
                )
              }
            }
          );
          throw preserveRuntimeLock(failure);
        }
      }
      if (!installed) {
        await fs.rm(staging, { recursive: true, force: true }).catch(() => {
        });
      }
      if (recoveryStarted) {
        try {
          await removeRuntimeRecovery(transaction, fs);
        } catch (cleanupError) {
          throw cleanupError;
        }
      }
      if (error && (typeof error === "object" || typeof error === "function")) {
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
      record
    }),
    { fs, pathApi, platform }
  );
}

// src/mode/host-runtime.mjs
var AGENT_RUNTIME_PATTERN = /^[a-z][a-z0-9._-]{0,63}$/;
function isAgentRuntime(value) {
  return typeof value === "string" && AGENT_RUNTIME_PATTERN.test(value);
}
function normalizeHostRuntime(hostRuntime) {
  if (hostRuntime === void 0 || hostRuntime === null) return void 0;
  if (!isAgentRuntime(hostRuntime)) {
    throw new GatedLoopError("HOST_RUNTIME_INVALID", "hostRuntime must be a safe lowercase Agent identifier");
  }
  return hostRuntime;
}
function requireHostRuntime(hostRuntime) {
  const normalized = normalizeHostRuntime(hostRuntime);
  if (!normalized) {
    throw new GatedLoopError("HOST_RUNTIME_REQUIRED", "A writing workflow requires a --host-runtime Agent identifier");
  }
  return normalized;
}

// src/work-items/runtime.mjs
import * as fsPromises2 from "node:fs/promises";
import path3 from "node:path";

// src/core/hash.mjs
import { createHash as createHash2 } from "node:crypto";
function sha256Bytes(bytes) {
  return createHash2("sha256").update(bytes).digest("hex");
}

// src/baseline/test-command.mjs
var CONTROL = /[\u0000-\u001F\u007F-\u009F]/;
var SHELL_EXECUTABLES = /* @__PURE__ */ new Set([
  "ash",
  "bash",
  "csh",
  "dash",
  "elvish",
  "fish",
  "hush",
  "ksh",
  "ksh93",
  "mksh",
  "nu",
  "nushell",
  "osh",
  "pdksh",
  "sh",
  "tcsh",
  "xonsh",
  "ysh",
  "zsh",
  "cmd",
  "command.com",
  "powershell",
  "pwsh"
]);
var STRING_INTERPRETER_FLAGS = /* @__PURE__ */ new Map([
  ["bun", /* @__PURE__ */ new Set(["-e", "--eval", "-p", "--print"])],
  ["deno", /* @__PURE__ */ new Set(["-e", "--eval"])],
  ["lua", /* @__PURE__ */ new Set(["-e"])],
  ["node", /* @__PURE__ */ new Set(["-e", "--eval", "-p", "--print"])],
  ["perl", /* @__PURE__ */ new Set(["-e"])],
  ["php", /* @__PURE__ */ new Set([
    "-r",
    "--run",
    "-B",
    "--process-begin",
    "-R",
    "--process-code",
    "-E",
    "--process-end"
  ])],
  ["py", /* @__PURE__ */ new Set(["-c", "--command"])],
  ["python", /* @__PURE__ */ new Set(["-c", "--command"])],
  ["pypy", /* @__PURE__ */ new Set(["-c", "--command"])],
  ["ruby", /* @__PURE__ */ new Set(["-e", "--eval"])]
]);
var STRING_INTERPRETER_SUBCOMMANDS = /* @__PURE__ */ new Map([
  ["deno", /* @__PURE__ */ new Set(["eval"])]
]);
var INTERPRETER_OPTIONS_WITH_VALUES = /* @__PURE__ */ new Map([
  ["deno", /* @__PURE__ */ new Set(["--config", "--import-map", "--node-modules-dir"])],
  ["lua", /* @__PURE__ */ new Set(["-l"])],
  ["node", /* @__PURE__ */ new Set([
    "-r",
    "--require",
    "--import",
    "--loader",
    "--experimental-loader",
    "--conditions",
    "--input-type",
    "--redirect-warnings",
    "--env-file",
    "--env-file-if-exists",
    "--icu-data-dir",
    "--openssl-config",
    "--snapshot-blob",
    "--inspect-port",
    "--diagnostic-dir",
    "--report-dir",
    "--report-directory",
    "--report-filename",
    "--test-concurrency",
    "--test-name-pattern",
    "--test-reporter",
    "--test-reporter-destination",
    "--test-shard",
    "--test-timeout",
    "--title",
    "--experimental-default-type",
    "--dns-result-order",
    "--unhandled-rejections",
    "--disable-proto",
    "--trace-event-categories"
  ])],
  ["perl", /* @__PURE__ */ new Set(["-I"])],
  ["php", /* @__PURE__ */ new Set(["-c", "-d", "-z"])],
  ["python", /* @__PURE__ */ new Set(["-W", "-X", "-Q", "--check-hash-based-pycs"])],
  ["pypy", /* @__PURE__ */ new Set(["-W", "-X", "-Q"])],
  ["ruby", /* @__PURE__ */ new Set(["-I", "-r", "-C", "-E", "--encoding", "--external-encoding", "--internal-encoding"])]
]);
var INTERPRETER_ENTRYPOINT_OPTIONS = /* @__PURE__ */ new Map([
  ["node", /* @__PURE__ */ new Set(["--run"])],
  ["php", /* @__PURE__ */ new Set(["-f"])],
  ["python", /* @__PURE__ */ new Set(["-m"])],
  ["pypy", /* @__PURE__ */ new Set(["-m"])],
  ["ruby", /* @__PURE__ */ new Set(["-S"])]
]);
var INTERPRETER_BOOLEAN_OPTIONS = /* @__PURE__ */ new Map([
  ["node", /* @__PURE__ */ new Set(["-c", "--check", "--no-warnings", "--test"])]
]);
var EXEC_OPTIONS_WITH_VALUES = /* @__PURE__ */ new Set([
  "-p",
  "--package",
  "--cache",
  "--userconfig",
  "--registry",
  "--prefix",
  "-w",
  "--workspace",
  "--loglevel"
]);
var EXEC_BOOLEAN_OPTIONS = /* @__PURE__ */ new Set([
  "-y",
  "--yes",
  "--no",
  "-q",
  "--quiet",
  "-s",
  "--silent",
  "--workspaces",
  "--include-workspace-root",
  "--ignore-existing"
]);
var NPM_OPTIONS_WITH_VALUES = /* @__PURE__ */ new Set([
  "-C",
  "--prefix",
  "--cache",
  "--userconfig",
  "--registry",
  "-w",
  "--workspace",
  "--loglevel"
]);
var NPM_BOOLEAN_OPTIONS = /* @__PURE__ */ new Set([
  "-q",
  "--quiet",
  "-s",
  "--silent",
  "--verbose",
  "--workspaces",
  "--include-workspace-root",
  "--no-progress",
  "--color",
  "--no-color"
]);
function executableName(value) {
  return value.toLowerCase().replaceAll("\\", "/").split("/").at(-1).replace(/\.(?:exe|cmd|bat)$/, "");
}
function isShellExecutable(value) {
  return SHELL_EXECUTABLES.has(executableName(value));
}
function interpreterKind(value) {
  const name = executableName(value);
  if (/^pyw?$/.test(name)) return "python";
  if (/^(?:pythonw?)\d*(?:\.\d+)*$/.test(name)) return "python";
  if (/^pypy\d*(?:\.\d+)*$/.test(name)) return "pypy";
  if (/^node(?:js)?\d*(?:\.\d+)*$/.test(name)) return "node";
  if (/^rubyw\d*(?:\.\d+)*$/.test(name)) return "ruby";
  if (/^wperl\d*(?:\.\d+)*$/.test(name)) return "perl";
  if (/^(?:bun|deno)\d*(?:\.\d+)*$/.test(name)) return name.startsWith("bun") ? "bun" : "deno";
  for (const kind of ["ruby", "perl", "php", "lua"]) {
    if (new RegExp(`^${kind}\\d*(?:\\.\\d+)*$`).test(name)) return kind;
  }
  return STRING_INTERPRETER_FLAGS.has(name) ? name : void 0;
}
function isStringFlag(argument, kind, flags) {
  if (kind === "python" || kind === "pypy") {
    if (!/^-[^-]/.test(argument)) return [...flags].some((flag) => argument === flag || flag.startsWith("--") && argument.startsWith(`${flag}=`));
    for (const option of argument.slice(1)) {
      if (option === "c") return true;
      if (["W", "X", "Q"].includes(option)) return false;
    }
    return false;
  }
  if (kind === "perl" && /^-[^-]/.test(argument)) {
    for (const option of argument.slice(1)) {
      if (option === "e" || option === "E") return true;
      if (["I", "M", "m", "F", "C", "D", "U"].includes(option)) return false;
    }
  }
  if (kind === "ruby" && /^-[a-z]*e/.test(argument)) return true;
  return [...flags].some((flag) => argument === flag || flag.startsWith("--") && argument.startsWith(`${flag}=`) || flag.length === 2 && argument.startsWith(flag) && argument.length > flag.length);
}
function invokesInterpreterString(argv, executableIndex, kind, flags) {
  const subcommands = STRING_INTERPRETER_SUBCOMMANDS.get(kind) ?? /* @__PURE__ */ new Set();
  const valueOptions = INTERPRETER_OPTIONS_WITH_VALUES.get(kind) ?? /* @__PURE__ */ new Set();
  const entrypointOptions = INTERPRETER_ENTRYPOINT_OPTIONS.get(kind) ?? /* @__PURE__ */ new Set();
  const booleanOptions = INTERPRETER_BOOLEAN_OPTIONS.get(kind) ?? /* @__PURE__ */ new Set();
  let unknownOptionMayTakeValue = false;
  for (let index = executableIndex + 1; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return false;
    if (isStringFlag(argument, kind, flags)) return true;
    if (subcommands.has(argument.toLowerCase())) return true;
    if ([...entrypointOptions].some((option) => argument === option || argument.startsWith(`${option}=`) || option.length === 2 && argument.startsWith(option) && argument.length > 2)) return false;
    const valueOption = [...valueOptions].find((option) => argument === option || argument.startsWith(`${option}=`) || option.length === 2 && argument.startsWith(option) && argument.length > 2);
    if (valueOption) {
      if (argument === valueOption) index++;
      unknownOptionMayTakeValue = false;
      continue;
    }
    if (booleanOptions.has(argument)) {
      unknownOptionMayTakeValue = false;
      continue;
    }
    if (argument.startsWith("-")) {
      unknownOptionMayTakeValue = kind === "node" && !argument.includes("=");
      continue;
    }
    if (unknownOptionMayTakeValue) {
      const laterCreatesString = argv.slice(index + 1).some((later) => isStringFlag(later, kind, flags) || subcommands.has(later.toLowerCase()));
      if (laterCreatesString) {
        unknownOptionMayTakeValue = false;
        continue;
      }
    }
    return false;
  }
  return false;
}
function optionMatch(argument, options) {
  return [...options].find((value) => argument === value || value.startsWith("--") && argument.startsWith(`${value}=`) || value.length === 2 && argument.startsWith(value) && argument.length > 2);
}
function envCommand(argv, start) {
  const optionsWithValues = /* @__PURE__ */ new Set(["-u", "--unset", "-C", "--chdir", "-a", "--argv0"]);
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "-S" || argument.startsWith("-S") || argument === "--split-string" || argument.startsWith("--split-string=")) return { rejected: true };
    if (argument === "--") return { index: index + 1 };
    const option = optionMatch(argument, optionsWithValues);
    if (option) {
      if (argument === option) index++;
      continue;
    }
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(argument) || argument.startsWith("-")) continue;
    return { index };
  }
  return {};
}
function multiCallApplet(argv, start) {
  const argument = argv[start];
  if (argument === "--") return start + 1;
  if (argument?.startsWith("-")) return void 0;
  return argument === void 0 ? void 0 : start;
}
function wrapperCommand(argv, start, optionsWithValues = /* @__PURE__ */ new Set()) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return index + 1;
    const option = optionMatch(argument, optionsWithValues);
    if (option) {
      if (argument === option) index++;
      continue;
    }
    if (argument.startsWith("-")) continue;
    return index;
  }
  return void 0;
}
function execCommand(argv, start) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return { index: index + 1 };
    if (argument === "-c" || argument.startsWith("-c") && !argument.startsWith("--") || argument === "--call" || argument.startsWith("--call=")) return { rejected: true };
    const valueOption = optionMatch(argument, EXEC_OPTIONS_WITH_VALUES);
    if (valueOption) {
      if (argument === valueOption) {
        if (index + 1 >= argv.length) return { rejected: true };
        index++;
      }
      continue;
    }
    if (EXEC_BOOLEAN_OPTIONS.has(argument) || [...EXEC_BOOLEAN_OPTIONS].some((option) => option.startsWith("--") && argument.startsWith(`${option}=`))) continue;
    if (argument.startsWith("-")) return { rejected: true };
    return { index };
  }
  return {};
}
function npmCommand(argv, start) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return {};
    const valueOption = optionMatch(argument, NPM_OPTIONS_WITH_VALUES);
    if (valueOption) {
      if (argument === valueOption) {
        if (index + 1 >= argv.length) return { rejected: true };
        index++;
      }
      continue;
    }
    if (NPM_BOOLEAN_OPTIONS.has(argument) || [...NPM_BOOLEAN_OPTIONS].some((option) => option.startsWith("--") && argument.startsWith(`${option}=`))) continue;
    if (argument.startsWith("-")) return { rejected: true };
    return ["exec", "exe", "x"].includes(argument.toLowerCase()) ? execCommand(argv, index + 1) : {};
  }
  return {};
}
function inspectExecutableChain(argv, executableIndex = 0, depth = 0) {
  if (executableIndex >= argv.length || depth > 8) return false;
  const name = executableName(argv[executableIndex]);
  if (isShellExecutable(name)) return true;
  const kind = interpreterKind(name);
  const flags = kind && STRING_INTERPRETER_FLAGS.get(kind);
  if (flags) return invokesInterpreterString(argv, executableIndex, kind, flags);
  if (name === "env") {
    const command = envCommand(argv, executableIndex + 1);
    return command.rejected === true || command.index !== void 0 && inspectExecutableChain(argv, command.index, depth + 1);
  }
  if (name === "busybox" || name === "toybox") {
    const command = multiCallApplet(argv, executableIndex + 1);
    return command !== void 0 && inspectExecutableChain(argv, command, depth + 1);
  }
  if (name === "wsl") {
    const command = wrapperCommand(
      argv,
      executableIndex + 1,
      /* @__PURE__ */ new Set(["-d", "--distribution", "-u", "--user", "--cd", "--shell-type"])
    );
    return command === void 0 || inspectExecutableChain(argv, command, depth + 1);
  }
  if (name === "npx") {
    const command = execCommand(argv, executableIndex + 1);
    return command.rejected === true || command.index !== void 0 && inspectExecutableChain(argv, command.index, depth + 1);
  }
  if (name === "npm") {
    const command = npmCommand(argv, executableIndex + 1);
    return command.rejected === true || command.index !== void 0 && inspectExecutableChain(argv, command.index, depth + 1);
  }
  return false;
}
function normalizeTestArgv(value) {
  if (!Array.isArray(value) || value.length === 0 || typeof value[0] !== "string" || value[0].trim().length === 0 || /\s/.test(value[0]) || value.some((entry) => typeof entry !== "string" || entry.length === 0 || CONTROL.test(entry)) || inspectExecutableChain(value)) return null;
  return [...value];
}

// src/baseline/parse.mjs
var FULL_BASELINE_SECTIONS = Object.freeze([
  "Goal",
  "Background",
  "Scope",
  "Non-Goals",
  "Requirements",
  "Acceptance",
  "Tasks",
  "Risks",
  "Test Commands",
  "Decisions"
]);
var ID_NUMBER = "(?:00[1-9]|0[1-9]\\d|[1-9]\\d{2})";
var REQUIREMENT = new RegExp(`^### (R-${ID_NUMBER}) (\\S(?:.*\\S)?)$`);
var ACCEPTANCE = new RegExp(`^### (A-${ID_NUMBER}) \\[([^\\]]+)\\]$`);
var TASK = new RegExp(`^- \\[ \\] (T-${ID_NUMBER}) \\[([^\\]]+)\\] \\[([^\\]]+)\\] (\\S(?:.*\\S)?)$`);

// src/baseline/sources.mjs
var CONFIG_EXTENSIONS = /* @__PURE__ */ new Set([
  "env",
  "yml",
  "yaml",
  "json",
  "toml",
  "ini",
  "conf",
  "config",
  "properties",
  "xml",
  "cnf",
  "cfg"
]);
var SENSITIVE_CONTENT_EXTENSIONS = /* @__PURE__ */ new Set([...CONFIG_EXTENSIONS, "csv", "tsv", "txt"]);
function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// src/work-items/model.mjs
import path2 from "node:path";
var WORK_ITEM_SCHEMA_VERSION = 3;
var WORK_ITEM_KINDS = Object.freeze(["DELIVERY", "CAPABILITY", "TASK"]);
var WORK_ITEM_GATE_LEVELS = Object.freeze(["LIGHT", "FULL"]);
var WORK_ITEM_CHANGE_SCENARIOS = Object.freeze([
  "API",
  "DOMAIN",
  "DATA",
  "MIGRATION",
  "CONFIG",
  "UI",
  "INTEGRATION",
  "REFACTOR",
  "TEST",
  "DOCS",
  "SECURITY",
  "PERFORMANCE",
  "BUILD",
  "OTHER"
]);
var WORK_ITEM_INTERFACE_KINDS = Object.freeze([
  "HTTP_ENDPOINT",
  "RPC",
  "FUNCTION",
  "METHOD",
  "CLASS",
  "EVENT",
  "SCHEMA",
  "CONFIG",
  "CLI",
  "UI",
  "FILE_FORMAT",
  "OTHER"
]);
var WORK_ITEM_AUTHORITIES = Object.freeze({
  DELIVERY: "COORDINATION",
  CAPABILITY: "COORDINATION",
  TASK: "EXECUTION"
});
var ITEM_ID = /^[a-z0-9][a-z0-9._-]*$/;
var TRACE_ID = /^(?:R|A)-(?:00[1-9]|0[1-9]\d|[1-9]\d{2})$/;
var PLACEHOLDER = /\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?/i;
var CONTROL2 = /[\u0000-\u001F\u007F-\u009F]/;
function fail(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}
function exactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return canonicalJson(Object.keys(value).sort()) === canonicalJson([...expected].sort());
}
function text(value, field) {
  if (typeof value !== "string" || value.trim().length === 0 || PLACEHOLDER.test(value) || CONTROL2.test(value)) {
    fail("WORK_ITEM_VALUE_INVALID", `${field} must be nonempty text without placeholders`, { field });
  }
  return value.trim();
}
function safeId(value, field = "id") {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof value !== "string" || !ITEM_ID.test(value) || value.endsWith(".") || reserved.test(value)) {
    fail("WORK_ITEM_ID_INVALID", `${field} must be a safe lowercase identifier`, { field, value });
  }
  return value;
}
function gateLevel(value, kind) {
  if (!WORK_ITEM_GATE_LEVELS.includes(value) || kind !== "TASK" && value !== "FULL") {
    fail("WORK_ITEM_GATE_LEVEL_INVALID", "gateLevel must be LIGHT or FULL, and coordination work items must be FULL");
  }
  return value;
}
function strings(values, field, { allowEmpty = false } = {}) {
  if (!Array.isArray(values) || !allowEmpty && values.length === 0) {
    fail("WORK_ITEM_VALUE_INVALID", `${field} must be ${allowEmpty ? "an" : "a nonempty"} array`, { field });
  }
  const normalized = values.map((value, index) => text(value, `${field}[${index}]`));
  if (new Set(normalized).size !== normalized.length) {
    fail("WORK_ITEM_VALUE_INVALID", `${field} contains duplicate values`, { field });
  }
  return normalized;
}
function normalizeScopePattern(value) {
  const normalized = text(value, "scope").replaceAll("\\", "/");
  const segments = normalized.split("/");
  const wildcard = /[?*{}[\]]/;
  const supportedPattern = !wildcard.test(normalized) || normalized.endsWith("/**") && !wildcard.test(normalized.slice(0, -3));
  const invalid = path2.posix.isAbsolute(normalized) || path2.win32.isAbsolute(normalized) || segments.includes("..") || normalized.includes(":") || normalized.startsWith(".hierarchical-delivery-governance/") || normalized === ".hierarchical-delivery-governance" || !supportedPattern;
  if (invalid) fail("WORK_ITEM_SCOPE_INVALID", "Scope contains an unsafe path pattern", { pattern: value });
  return normalized.replace(/^\.\//, "");
}
function normalizeScope(values) {
  const normalized = strings(values, "scope").map(normalizeScopePattern);
  return [...new Set(normalized)].sort();
}
function traceRecords(values, prefix, field) {
  if (!Array.isArray(values) || values.length === 0) {
    fail("WORK_ITEM_TRACE_INVALID", `${field} must be a nonempty array`, { field });
  }
  const seen = /* @__PURE__ */ new Set();
  return values.map((entry, index) => {
    const expectedKeys = prefix === "R" ? ["id", "text"] : ["id", "requirementIds", "expectedResult"];
    if (!exactKeys(entry, expectedKeys) || !TRACE_ID.test(entry.id) || !entry.id.startsWith(`${prefix}-`) || seen.has(entry.id)) {
      fail("WORK_ITEM_TRACE_INVALID", `${field}[${index}] has an invalid or duplicate ID`, { field, index });
    }
    seen.add(entry.id);
    if (prefix === "R") return { id: entry.id, text: text(entry.text, `${field}.${entry.id}`) };
    return {
      id: entry.id,
      requirementIds: strings(entry.requirementIds, `${field}.${entry.id}.requirementIds`).sort(),
      expectedResult: text(entry.expectedResult, `${field}.${entry.id}`)
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
}
function validateTrace(requirements, acceptance) {
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const accepted = /* @__PURE__ */ new Set();
  for (const entry of acceptance) {
    for (const id of entry.requirementIds) {
      if (!requirementIds.has(id)) fail("WORK_ITEM_TRACE_INVALID", `${entry.id} references unknown requirement ${id}`);
      accepted.add(id);
    }
  }
  if (requirements.some(({ id }) => !accepted.has(id))) {
    fail("WORK_ITEM_TRACE_INVALID", "Every requirement must be covered by acceptance");
  }
}
function childRecords(values, kind, requirements, acceptance) {
  if (!Array.isArray(values) || values.length === 0) {
    fail("WORK_ITEM_CHILDREN_INVALID", `${kind} must declare at least one child work item`);
  }
  const expectedKind = kind === "DELIVERY" ? "CAPABILITY" : "TASK";
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const acceptanceIds = new Set(acceptance.map(({ id }) => id));
  const seen = /* @__PURE__ */ new Set();
  return values.map((entry, index) => {
    const keys = ["id", "kind", "title", "requirementIds", "acceptanceIds"];
    if (!exactKeys(entry, keys) || entry.kind !== expectedKind) {
      fail("WORK_ITEM_CHILDREN_INVALID", `${kind} children must be ${expectedKind} records`, { index });
    }
    const id = safeId(entry.id, `children[${index}].id`);
    if (seen.has(id)) fail("WORK_ITEM_CHILDREN_INVALID", `Duplicate child ID: ${id}`);
    seen.add(id);
    const linkedRequirements = strings(entry.requirementIds, `${id}.requirementIds`).sort();
    const linkedAcceptance = strings(entry.acceptanceIds, `${id}.acceptanceIds`).sort();
    if (linkedRequirements.some((linked) => !requirementIds.has(linked)) || linkedAcceptance.some((linked) => !acceptanceIds.has(linked))) {
      fail("WORK_ITEM_TRACE_INVALID", `${id} references unknown parent trace IDs`);
    }
    return {
      id,
      kind: expectedKind,
      title: text(entry.title, `${id}.title`),
      requirementIds: linkedRequirements,
      acceptanceIds: linkedAcceptance
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
}
function executionRecord(value, id) {
  if (!exactKeys(value, ["dependsOn", "inputs", "outputs"])) {
    fail("WORK_ITEM_EXECUTION_INVALID", "Task execution must contain dependsOn, inputs, and outputs");
  }
  const dependsOn = value.dependsOn.map((dependency, index) => safeId(dependency, `dependsOn[${index}]`));
  if (dependsOn.includes(id) || new Set(dependsOn).size !== dependsOn.length) {
    fail("WORK_ITEM_DEPENDENCY_INVALID", "Task dependencies must be unique and cannot reference the Task itself");
  }
  return {
    dependsOn: [...dependsOn].sort(),
    inputs: strings(value.inputs, "execution.inputs", { allowEmpty: true }),
    outputs: strings(value.outputs, "execution.outputs")
  };
}
function decompositionRecord(value, kind, id, parent) {
  const expectedKeys = kind === "CAPABILITY" ? ["status", "dependsOn"] : ["status"];
  if (!exactKeys(value, expectedKeys) || !["OPEN", "SEALED"].includes(value.status)) {
    fail("WORK_ITEM_DECOMPOSITION_INVALID", "Coordination work items require decomposition status OPEN or SEALED");
  }
  if (kind === "DELIVERY") return { status: value.status };
  if (!Array.isArray(value.dependsOn)) {
    fail("WORK_ITEM_DEPENDENCY_INVALID", "Capability dependsOn must be an array");
  }
  const dependsOn = value.dependsOn.map((dependency, index) => safeId(dependency, `decomposition.dependsOn[${index}]`));
  const siblingIds = new Set(parent?.children?.filter(({ kind: childKind }) => childKind === "CAPABILITY").map(({ id: childId }) => childId));
  if (dependsOn.includes(id) || new Set(dependsOn).size !== dependsOn.length || dependsOn.some((dependency) => !siblingIds.has(dependency))) {
    fail("WORK_ITEM_DEPENDENCY_INVALID", "Capability dependencies must be unique planned siblings and cannot reference itself");
  }
  return { status: value.status, dependsOn: [...dependsOn].sort() };
}
function testCommands(values) {
  if (!Array.isArray(values) || values.length === 0) {
    fail("WORK_ITEM_TEST_COMMAND_INVALID", "At least one test command is required");
  }
  const commands = values.map((value) => normalizeTestArgv(value));
  if (commands.some((value) => !value)) fail("WORK_ITEM_TEST_COMMAND_INVALID", "Test commands must be safe argv arrays");
  const canonical = commands.map((value) => JSON.stringify(value));
  if (new Set(canonical).size !== canonical.length) fail("WORK_ITEM_TEST_COMMAND_INVALID", "Duplicate test command");
  return commands;
}
function linkedTraceIds(values, allowed, field, { allowEmpty = false } = {}) {
  const linked = strings(values, field, { allowEmpty }).sort();
  if (linked.some((id) => !allowed.has(id))) {
    fail("WORK_ITEM_TRACE_INVALID", `${field} references an unknown trace ID`, { field });
  }
  return linked;
}
function developmentTestPlan(values, acceptance, testCommandCount) {
  if (!Array.isArray(values) || values.length === 0) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.testPlan must be a nonempty array");
  }
  const acceptanceIds = new Set(acceptance.map(({ id }) => id));
  const covered = /* @__PURE__ */ new Set();
  const normalized = values.map((entry, index) => {
    const field = `developmentPlan.testPlan[${index}]`;
    if (!exactKeys(entry, ["acceptanceIds", "approach", "commandIndexes"])) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} has missing or unknown fields`, { field });
    }
    const linkedAcceptance = linkedTraceIds(entry.acceptanceIds, acceptanceIds, `${field}.acceptanceIds`);
    linkedAcceptance.forEach((id) => covered.add(id));
    if (!Array.isArray(entry.commandIndexes) || entry.commandIndexes.length === 0 || entry.commandIndexes.some((commandIndex) => !Number.isInteger(commandIndex) || commandIndex < 0 || commandIndex >= testCommandCount) || new Set(entry.commandIndexes).size !== entry.commandIndexes.length) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field}.commandIndexes must reference frozen test commands`, { field });
    }
    return {
      acceptanceIds: linkedAcceptance,
      approach: text(entry.approach, `${field}.approach`),
      commandIndexes: [...entry.commandIndexes].sort((left, right) => left - right)
    };
  });
  if (acceptance.some(({ id }) => !covered.has(id))) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Every acceptance criterion must be covered by developmentPlan.testPlan");
  }
  return normalized;
}
function taskDevelopmentPlan(value, normalized) {
  const keys = [
    "purpose",
    "scenarios",
    "fileChanges",
    "interfaces",
    "logic",
    "dataAndTransactions",
    "compatibility",
    "testPlan",
    "reviewPoints"
  ];
  if (!exactKeys(value, keys)) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan contains missing or unknown fields");
  }
  const requirementIds = new Set(normalized.requirements.map(({ id }) => id));
  const coveredRequirements = /* @__PURE__ */ new Set();
  if (!Array.isArray(value.scenarios) || value.scenarios.length === 0) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.scenarios must be nonempty");
  }
  const scenarios = value.scenarios.map((entry, index) => {
    const field = `developmentPlan.scenarios[${index}]`;
    if (!exactKeys(entry, ["kind", "title", "description", "requirementIds"]) || !WORK_ITEM_CHANGE_SCENARIOS.includes(entry.kind)) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} is invalid`, { field });
    }
    const linkedRequirements = linkedTraceIds(entry.requirementIds, requirementIds, `${field}.requirementIds`);
    linkedRequirements.forEach((id) => coveredRequirements.add(id));
    return {
      kind: entry.kind,
      title: text(entry.title, `${field}.title`),
      description: text(entry.description, `${field}.description`),
      requirementIds: linkedRequirements
    };
  });
  if (normalized.requirements.some(({ id }) => !coveredRequirements.has(id))) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Every requirement must be covered by a development scenario");
  }
  if (!Array.isArray(value.fileChanges) || value.fileChanges.length === 0) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.fileChanges must be nonempty");
  }
  const seenPaths = /* @__PURE__ */ new Set();
  const fileChanges = value.fileChanges.map((entry, index) => {
    const field = `developmentPlan.fileChanges[${index}]`;
    if (!exactKeys(entry, ["path", "action", "purpose"]) || !["ADD", "MODIFY", "REMOVE"].includes(entry.action)) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} is invalid`, { field });
    }
    const plannedPath = normalizeScopePattern(entry.path);
    if (/[*?{}[\]]/.test(plannedPath) || seenPaths.has(plannedPath) || !scopeContains(normalized.scope, [plannedPath])) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field}.path must be a unique exact path inside Task scope`, { field });
    }
    seenPaths.add(plannedPath);
    return {
      path: plannedPath,
      action: entry.action,
      purpose: text(entry.purpose, `${field}.purpose`)
    };
  }).sort((left, right) => left.path.localeCompare(right.path));
  if (!Array.isArray(value.interfaces)) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.interfaces must be an array");
  }
  const interfaces = value.interfaces.map((entry, index) => {
    const field = `developmentPlan.interfaces[${index}]`;
    const interfaceKeys = [
      "name",
      "kind",
      "action",
      "location",
      "currentContract",
      "targetContract",
      "requirementIds"
    ];
    if (!exactKeys(entry, interfaceKeys) || !WORK_ITEM_INTERFACE_KINDS.includes(entry.kind) || !["ADD", "MODIFY", "REMOVE"].includes(entry.action)) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} is invalid`, { field });
    }
    return {
      name: text(entry.name, `${field}.name`),
      kind: entry.kind,
      action: entry.action,
      location: text(entry.location, `${field}.location`),
      currentContract: text(entry.currentContract, `${field}.currentContract`),
      targetContract: text(entry.targetContract, `${field}.targetContract`),
      requirementIds: linkedTraceIds(entry.requirementIds, requirementIds, `${field}.requirementIds`)
    };
  });
  return {
    purpose: text(value.purpose, "developmentPlan.purpose"),
    scenarios,
    fileChanges,
    interfaces,
    logic: strings(value.logic, "developmentPlan.logic"),
    dataAndTransactions: strings(value.dataAndTransactions, "developmentPlan.dataAndTransactions", { allowEmpty: true }),
    compatibility: strings(value.compatibility, "developmentPlan.compatibility"),
    testPlan: developmentTestPlan(value.testPlan, normalized.acceptance, normalized.testCommands.length),
    reviewPoints: strings(value.reviewPoints, "developmentPlan.reviewPoints")
  };
}
function coordinationDevelopmentPlan(value, normalized) {
  const keys = [
    "purpose",
    "childPlans",
    "sharedContracts",
    "integrationFlow",
    "deliveryWaves",
    "testPlan",
    "reviewPoints"
  ];
  if (!exactKeys(value, keys)) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Coordination developmentPlan contains missing or unknown fields");
  }
  const requirements = new Set(normalized.requirements.map(({ id }) => id));
  const acceptance = new Set(normalized.acceptance.map(({ id }) => id));
  const childById = new Map(normalized.children.map((child) => [child.id, child]));
  if (!Array.isArray(value.childPlans) || value.childPlans.length !== normalized.children.length) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.childPlans must cover every direct child exactly once");
  }
  const seen = /* @__PURE__ */ new Set();
  const childPlans = value.childPlans.map((entry, index) => {
    const field = `developmentPlan.childPlans[${index}]`;
    const child = childById.get(entry?.id);
    if (!exactKeys(entry, ["id", "purpose", "deliverables", "requirementIds", "acceptanceIds", "dependsOn"]) || !child || seen.has(entry.id)) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} does not match a unique planned child`, { field });
    }
    seen.add(entry.id);
    const linkedRequirements = linkedTraceIds(entry.requirementIds, requirements, `${field}.requirementIds`);
    const linkedAcceptance = linkedTraceIds(entry.acceptanceIds, acceptance, `${field}.acceptanceIds`);
    if (canonicalJson(linkedRequirements) !== canonicalJson(child.requirementIds) || canonicalJson(linkedAcceptance) !== canonicalJson(child.acceptanceIds)) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} trace mapping must match the child contract`, { field });
    }
    const dependsOn = entry.dependsOn.map((id, dependencyIndex) => safeId(id, `${field}.dependsOn[${dependencyIndex}]`));
    if (dependsOn.includes(entry.id) || new Set(dependsOn).size !== dependsOn.length || dependsOn.some((id) => !childById.has(id))) {
      fail("WORK_ITEM_DEPENDENCY_INVALID", `${field}.dependsOn must reference unique sibling children`, { field });
    }
    return {
      id: entry.id,
      purpose: text(entry.purpose, `${field}.purpose`),
      deliverables: strings(entry.deliverables, `${field}.deliverables`),
      requirementIds: linkedRequirements,
      acceptanceIds: linkedAcceptance,
      dependsOn: [...dependsOn].sort()
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
  const graph = new Map(childPlans.map(({ id, dependsOn }) => [id, dependsOn]));
  const visiting = /* @__PURE__ */ new Set();
  const visited = /* @__PURE__ */ new Set();
  const visit = (id) => {
    if (visiting.has(id)) fail("WORK_ITEM_DEPENDENCY_CYCLE", "developmentPlan child dependencies contain a cycle");
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dependency of graph.get(id) ?? []) visit(dependency);
    visiting.delete(id);
    visited.add(id);
  };
  for (const id of graph.keys()) visit(id);
  if (!Array.isArray(value.sharedContracts)) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.sharedContracts must be an array");
  }
  const sharedContracts = value.sharedContracts.map((entry, index) => {
    const field = `developmentPlan.sharedContracts[${index}]`;
    if (!exactKeys(entry, [
      "name",
      "kind",
      "description",
      "providerChildIds",
      "consumerChildIds",
      "requirementIds"
    ]) || !WORK_ITEM_INTERFACE_KINDS.includes(entry.kind)) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} is invalid`, { field });
    }
    const childIds = new Set(childById.keys());
    const providers = strings(entry.providerChildIds, `${field}.providerChildIds`).sort();
    const consumers = strings(entry.consumerChildIds, `${field}.consumerChildIds`).sort();
    if ([...providers, ...consumers].some((id) => !childIds.has(id))) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} references an unknown child`, { field });
    }
    return {
      name: text(entry.name, `${field}.name`),
      kind: entry.kind,
      description: text(entry.description, `${field}.description`),
      providerChildIds: providers,
      consumerChildIds: consumers,
      requirementIds: linkedTraceIds(entry.requirementIds, requirements, `${field}.requirementIds`)
    };
  });
  if (!Array.isArray(value.deliveryWaves) || value.deliveryWaves.length === 0) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.deliveryWaves must be nonempty");
  }
  const waveByChild = /* @__PURE__ */ new Map();
  const waveOrders = /* @__PURE__ */ new Set();
  const deliveryWaves = value.deliveryWaves.map((entry, index) => {
    const field = `developmentPlan.deliveryWaves[${index}]`;
    if (!exactKeys(entry, ["order", "name", "childIds", "exitCriteria"]) || !Number.isInteger(entry.order) || entry.order < 1 || waveOrders.has(entry.order)) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} is invalid`, { field });
    }
    waveOrders.add(entry.order);
    const childIds = strings(entry.childIds, `${field}.childIds`).map((id) => safeId(id, `${field}.childIds`)).sort();
    if (childIds.some((id) => !childById.has(id) || waveByChild.has(id))) {
      fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", `${field} must contain unique planned children`, { field });
    }
    childIds.forEach((id) => waveByChild.set(id, entry.order));
    return {
      order: entry.order,
      name: text(entry.name, `${field}.name`),
      childIds,
      exitCriteria: text(entry.exitCriteria, `${field}.exitCriteria`)
    };
  }).sort((left, right) => left.order - right.order);
  if (waveByChild.size !== childById.size || childPlans.some(({ id, dependsOn }) => dependsOn.some((dependency) => waveByChild.get(dependency) >= waveByChild.get(id)))) {
    fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Delivery waves must cover every child and order dependencies before consumers");
  }
  return {
    purpose: text(value.purpose, "developmentPlan.purpose"),
    childPlans,
    sharedContracts,
    integrationFlow: strings(value.integrationFlow, "developmentPlan.integrationFlow"),
    deliveryWaves,
    testPlan: developmentTestPlan(value.testPlan, normalized.acceptance, normalized.testCommands.length),
    reviewPoints: strings(value.reviewPoints, "developmentPlan.reviewPoints")
  };
}
function scopeCovers(parentPattern, childPattern) {
  if (parentPattern === "**") return true;
  if (!parentPattern.endsWith("/**")) return parentPattern === childPattern;
  const prefix = parentPattern.slice(0, -3);
  return childPattern === prefix || childPattern.startsWith(`${prefix}/`);
}
function scopeContains(parentScope, childScope) {
  return childScope.every((childPattern) => parentScope.some((parentPattern) => scopeCovers(parentPattern, childPattern)));
}
function scopePatternsOverlap(left, right) {
  return left.some((leftPattern) => right.some((rightPattern) => scopeCovers(leftPattern, rightPattern) || scopeCovers(rightPattern, leftPattern)));
}
function normalizeParent(definition, parent) {
  if (definition.kind === "DELIVERY") {
    if (definition.parentId !== void 0 && definition.parentId !== null) {
      fail("WORK_ITEM_PARENT_INVALID", "Delivery cannot have a parent work item");
    }
    return { parentId: null, parentContractFingerprint: null };
  }
  if (definition.parentId === null) {
    if (parent) fail("WORK_ITEM_PARENT_INVALID", `Root ${definition.kind} cannot receive a parent contract`);
    if (definition.kind === "TASK" && definition.execution.dependsOn.length > 0) {
      fail("WORK_ITEM_DEPENDENCY_INVALID", "A root Task cannot depend on sibling Tasks; use a Capability root");
    }
    if (definition.kind === "CAPABILITY" && definition.decomposition.dependsOn.length > 0) {
      fail("WORK_ITEM_DEPENDENCY_INVALID", "A root Capability cannot depend on sibling Capabilities; use a Delivery root");
    }
    return { parentId: null, parentContractFingerprint: null };
  }
  if (!parent || definition.parentId !== parent.id) {
    fail("WORK_ITEM_PARENT_INVALID", `${definition.kind} must reference its supplied parent`);
  }
  const expectedParentKind = definition.kind === "CAPABILITY" ? "DELIVERY" : "CAPABILITY";
  if (parent.kind !== expectedParentKind) {
    fail("WORK_ITEM_PARENT_INVALID", `${definition.kind} parent must be ${expectedParentKind}`);
  }
  const planned = parent.children?.find(({ id, kind }) => id === definition.id && kind === definition.kind);
  if (!planned) fail("WORK_ITEM_PARENT_PLAN_MISMATCH", `${definition.id} is not declared by its parent baseline`);
  if (!scopeContains(parent.scope, definition.scope)) {
    fail("WORK_ITEM_SCOPE_EXPANDED", `${definition.id} scope expands beyond its parent baseline`);
  }
  return {
    parentId: parent.id,
    parentContractFingerprint: workItemChildContractFingerprint(parent, definition.id)
  };
}
function validateWorkItemDefinition(definition, { parent, allowLegacyDevelopmentPlan = false } = {}) {
  if (!definition || typeof definition !== "object" || Array.isArray(definition)) {
    fail("WORK_ITEM_DEFINITION_INVALID", "Work item definition must be an object");
  }
  if (!WORK_ITEM_KINDS.includes(definition.kind)) {
    fail("WORK_ITEM_KIND_INVALID", "Work item kind must be DELIVERY, CAPABILITY, or TASK");
  }
  if (definition.schemaVersion !== WORK_ITEM_SCHEMA_VERSION) {
    fail("WORK_ITEM_SCHEMA_INVALID", `Work item schemaVersion must be ${WORK_ITEM_SCHEMA_VERSION}`);
  }
  if (definition.kind === "TASK" && Object.hasOwn(definition, "children")) {
    fail("WORK_ITEM_TASK_NOT_LEAF", "Task is an executable leaf and cannot contain children");
  }
  if (definition.kind !== "TASK" && Object.hasOwn(definition, "execution")) {
    fail("WORK_ITEM_EXECUTION_INVALID", "Only Task work items can contain execution metadata");
  }
  const commonKeys = [
    "schemaVersion",
    "id",
    "kind",
    "gateLevel",
    "title",
    "goal",
    "scope",
    "nonGoals",
    "requirements",
    "acceptance",
    "testCommands",
    "risks",
    "decisions"
  ];
  const developmentPlanKeys = allowLegacyDevelopmentPlan && !Object.hasOwn(definition, "developmentPlan") ? [] : ["developmentPlan"];
  const expectedKeys = definition.kind === "DELIVERY" ? [...commonKeys, ...developmentPlanKeys, "decomposition", "children"] : [...commonKeys, ...developmentPlanKeys, "parentId", ...definition.kind === "TASK" ? ["execution"] : ["decomposition", "children"]];
  if (!exactKeys(definition, expectedKeys)) {
    fail("WORK_ITEM_DEFINITION_INVALID", "Work item definition contains missing or unknown fields", {
      expectedKeys: expectedKeys.sort(),
      actualKeys: Object.keys(definition).sort()
    });
  }
  const normalized = {
    schemaVersion: WORK_ITEM_SCHEMA_VERSION,
    id: safeId(definition.id),
    kind: definition.kind,
    gateLevel: gateLevel(definition.gateLevel, definition.kind),
    authorityKind: WORK_ITEM_AUTHORITIES[definition.kind],
    title: text(definition.title, "title"),
    goal: text(definition.goal, "goal"),
    scope: normalizeScope(definition.scope),
    nonGoals: strings(definition.nonGoals, "nonGoals"),
    requirements: traceRecords(definition.requirements, "R", "requirements"),
    acceptance: traceRecords(definition.acceptance, "A", "acceptance"),
    testCommands: testCommands(definition.testCommands),
    risks: strings(definition.risks, "risks"),
    decisions: strings(definition.decisions, "decisions")
  };
  validateTrace(normalized.requirements, normalized.acceptance);
  if (definition.kind === "TASK") normalized.execution = executionRecord(definition.execution, normalized.id);
  else {
    normalized.decomposition = decompositionRecord(definition.decomposition, definition.kind, normalized.id, parent);
    normalized.children = childRecords(definition.children, definition.kind, normalized.requirements, normalized.acceptance);
  }
  if (Object.hasOwn(definition, "developmentPlan")) {
    normalized.developmentPlan = definition.kind === "TASK" ? taskDevelopmentPlan(definition.developmentPlan, normalized) : coordinationDevelopmentPlan(definition.developmentPlan, normalized);
  }
  Object.assign(normalized, normalizeParent({ ...definition, ...normalized }, parent));
  if (parent?.developmentPlan) {
    const planned = parent.developmentPlan.childPlans.find(({ id }) => id === normalized.id);
    const actualDependencies = normalized.kind === "TASK" ? normalized.execution.dependsOn : normalized.decomposition.dependsOn;
    if (!planned || canonicalJson(planned.dependsOn) !== canonicalJson(actualDependencies)) {
      fail("WORK_ITEM_PARENT_PLAN_MISMATCH", `${normalized.id} dependencies do not match the frozen parent development plan`);
    }
  }
  return normalized;
}
function contract(definition) {
  const normalized = {
    schemaVersion: definition.schemaVersion,
    id: definition.id,
    kind: definition.kind,
    gateLevel: definition.gateLevel,
    goal: definition.goal,
    scope: [...definition.scope].sort(),
    requirements: [...definition.requirements].sort((left, right) => left.id.localeCompare(right.id)),
    acceptance: [...definition.acceptance].sort((left, right) => left.id.localeCompare(right.id)),
    testCommands: definition.testCommands
  };
  if (definition.children) normalized.children = [...definition.children].sort((left, right) => left.id.localeCompare(right.id));
  if (definition.decomposition) normalized.decomposition = definition.decomposition;
  if (definition.execution) normalized.execution = definition.execution;
  if (definition.developmentPlan) normalized.developmentPlan = definition.developmentPlan;
  return normalized;
}
function workItemContractFingerprint(definition) {
  return sha256Bytes(Buffer.from(canonicalJson(contract(definition)), "utf8"));
}
function workItemChildContractFingerprint(parent, childId) {
  const child = parent.children?.find(({ id }) => id === childId);
  if (!child) fail("WORK_ITEM_PARENT_PLAN_MISMATCH", `${childId} is not declared by its parent baseline`);
  const stableParentContract = contract(parent);
  delete stableParentContract.children;
  delete stableParentContract.decomposition;
  let childDevelopmentPlan;
  if (stableParentContract.developmentPlan) {
    childDevelopmentPlan = stableParentContract.developmentPlan.childPlans.find(({ id }) => id === childId);
    stableParentContract.developmentPlan = {
      ...stableParentContract.developmentPlan,
      sharedContracts: stableParentContract.developmentPlan.sharedContracts.filter(({ consumerChildIds }) => consumerChildIds.includes(childId)),
      childPlans: void 0,
      deliveryWaves: void 0
    };
    delete stableParentContract.developmentPlan.childPlans;
    delete stableParentContract.developmentPlan.deliveryWaves;
  }
  return sha256Bytes(Buffer.from(canonicalJson({
    parent: stableParentContract,
    child,
    ...childDevelopmentPlan ? { childDevelopmentPlan } : {}
  }), "utf8"));
}
function workItemBaselineFingerprint(definition) {
  return sha256Bytes(Buffer.from(canonicalJson(definition), "utf8"));
}
function list(values) {
  return values.map((value) => `- ${value}`).join("\n");
}
function renderWorkItemBaseline(definition) {
  const lines = [
    "# Work Item Baseline",
    "",
    `Work Item: ${definition.id}`,
    `Kind: ${definition.kind}`,
    `Gate Level: ${definition.gateLevel}`,
    `Authority: ${definition.authorityKind}`,
    `Parent: ${definition.parentId ?? "none"}`,
    `Parent Contract: ${definition.parentContractFingerprint ?? "none"}`,
    "",
    "## Goal",
    definition.goal,
    "",
    "## Scope",
    list(definition.scope),
    "",
    "## Non-Goals",
    list(definition.nonGoals),
    "",
    "## Requirements"
  ];
  for (const requirement of definition.requirements) lines.push(`### ${requirement.id}`, requirement.text, "");
  lines.push("## Acceptance");
  for (const acceptance of definition.acceptance) {
    lines.push(`### ${acceptance.id} [${acceptance.requirementIds.join(",")}]`, acceptance.expectedResult, "");
  }
  if (definition.children) {
    lines.push(
      "## Decomposition",
      `- Status: ${definition.decomposition.status}`,
      ...definition.kind === "CAPABILITY" ? [`- Capability dependencies: ${definition.decomposition.dependsOn.join(", ") || "none"}`] : [],
      "",
      "## Children"
    );
    for (const child of definition.children) {
      lines.push(`- ${child.id} [${child.kind}] [${child.requirementIds.join(",")}] [${child.acceptanceIds.join(",")}] ${child.title}`);
    }
  } else {
    lines.push(
      "## Execution",
      `- Depends on: ${definition.execution.dependsOn.join(", ") || "none"}`,
      `- Inputs: ${definition.execution.inputs.join("; ") || "none"}`,
      `- Outputs: ${definition.execution.outputs.join("; ")}`
    );
  }
  lines.push("", "## Test Commands", ...definition.testCommands.map((argv) => `- ${JSON.stringify(argv)}`));
  if (definition.developmentPlan) {
    lines.push(
      "",
      "## Development Review Contract",
      definition.developmentPlan.purpose,
      "",
      "- Full human-readable plan: [development-review.md](development-review.md)",
      "- Structured plan: [development-plan.json](development-plan.json)"
    );
  }
  lines.push("", "## Risks", list(definition.risks));
  lines.push("", "## Decisions", list(definition.decisions), "");
  return lines.join("\n");
}
function reviewStatusText(state) {
  return state?.review?.status === "APPROVED" ? `\u5DF2\u7531\u4EBA\u5DE5\u786E\u8BA4\uFF08${state.review.reviewedBy}\uFF0C${state.review.reviewedAt}\uFF09` : "\u7B49\u5F85\u4EBA\u5DE5\u8BC4\u5BA1\uFF1B\u5C1A\u672A\u51BB\u7ED3\uFF0C\u7981\u6B62\u5F00\u59CB\u5F00\u53D1";
}
function markdownTableCell(value) {
  return String(value).replaceAll("|", "\\|").replaceAll("\n", "<br>");
}
function renderDevelopmentReview(definition, state) {
  const plan = definition.developmentPlan;
  if (!plan) return "";
  const lines = [
    `# \u5F00\u53D1\u8BC4\u5BA1\uFF1A${definition.title}`,
    "",
    `- \u5DE5\u4F5C\u9879\uFF1A${definition.id}`,
    `- \u5C42\u7EA7\uFF1A${definition.kind}`,
    `- \u95E8\u7981\u7B49\u7EA7\uFF1A${definition.gateLevel}`,
    `- Baseline \u6307\u7EB9\uFF1A${state.baselineFingerprint}`,
    `- \u8BC4\u5BA1\u72B6\u6001\uFF1A${reviewStatusText(state)}`,
    `- \u5F00\u53D1\u76EE\u7684\uFF1A${plan.purpose}`,
    "",
    "## \u9700\u6C42\u4E0E\u9A8C\u6536\u8FB9\u754C",
    "",
    "| \u9700\u6C42 | \u5185\u5BB9 |",
    "| --- | --- |",
    ...definition.requirements.map(({ id, text: requirement }) => `| ${id} | ${markdownTableCell(requirement)} |`),
    "",
    "| \u9A8C\u6536 | \u8986\u76D6\u9700\u6C42 | \u9884\u671F\u7ED3\u679C |",
    "| --- | --- | --- |",
    ...definition.acceptance.map(({ id, requirementIds, expectedResult }) => `| ${id} | ${requirementIds.join(", ")} | ${markdownTableCell(expectedResult)} |`),
    ""
  ];
  if (definition.kind === "TASK") {
    lines.push(
      "## \u53D8\u66F4\u573A\u666F",
      "",
      "| \u573A\u666F | \u6807\u9898 | \u5F00\u53D1\u5185\u5BB9 | \u8986\u76D6\u9700\u6C42 |",
      "| --- | --- | --- | --- |",
      ...plan.scenarios.map((scenario) => `| ${scenario.kind} | ${markdownTableCell(scenario.title)} | ${markdownTableCell(scenario.description)} | ${scenario.requirementIds.join(", ")} |`),
      "",
      "## \u6587\u4EF6\u6539\u52A8",
      "",
      "| \u52A8\u4F5C | \u6587\u4EF6 | \u76EE\u7684 |",
      "| --- | --- | --- |",
      ...plan.fileChanges.map((change) => `| ${change.action} | \`${change.path}\` | ${markdownTableCell(change.purpose)} |`),
      "",
      "## \u63A5\u53E3\u4E0E\u529F\u80FD\u5951\u7EA6",
      ""
    );
    if (plan.interfaces.length === 0) lines.push("- \u672C Task \u4E0D\u65B0\u589E\u3001\u4FEE\u6539\u6216\u5220\u9664\u5916\u90E8/\u5185\u90E8\u63A5\u53E3\u3002");
    else {
      lines.push(
        "| \u52A8\u4F5C | \u7C7B\u578B | \u540D\u79F0\u4E0E\u4F4D\u7F6E | \u5F53\u524D\u5951\u7EA6 | \u76EE\u6807\u5951\u7EA6 | \u8986\u76D6\u9700\u6C42 |",
        "| --- | --- | --- | --- | --- | --- |",
        ...plan.interfaces.map((contract2) => `| ${contract2.action} | ${contract2.kind} | ${markdownTableCell(contract2.name)}<br>${markdownTableCell(contract2.location)} | ${markdownTableCell(contract2.currentContract)} | ${markdownTableCell(contract2.targetContract)} | ${contract2.requirementIds.join(", ")} |`)
      );
    }
    lines.push("", "## \u5B9E\u73B0\u903B\u8F91", "", ...plan.logic.map((item) => `- ${item}`));
    lines.push("", "## \u6570\u636E\u4E0E\u4E8B\u52A1", "");
    lines.push(...plan.dataAndTransactions.length > 0 ? plan.dataAndTransactions.map((item) => `- ${item}`) : ["- \u4E0D\u6D89\u53CA\u6570\u636E\u6A21\u578B\u3001\u6301\u4E45\u5316\u6216\u4E8B\u52A1\u8FB9\u754C\u53D8\u66F4\u3002"]);
    lines.push("", "## \u517C\u5BB9\u6027", "", ...plan.compatibility.map((item) => `- ${item}`));
  } else {
    const childLabel = definition.kind === "DELIVERY" ? "Capability" : "Task";
    lines.push(
      `## ${childLabel} \u5F00\u53D1\u5185\u5BB9`,
      "",
      `| ${childLabel} | \u5F00\u53D1\u76EE\u7684 | \u4EA4\u4ED8\u5185\u5BB9 | \u4F9D\u8D56 | R/A |`,
      "| --- | --- | --- | --- | --- |",
      ...plan.childPlans.map((child) => `| ${child.id} | ${markdownTableCell(child.purpose)} | ${markdownTableCell(child.deliverables.join("\uFF1B"))} | ${child.dependsOn.join(", ") || "\u65E0"} | ${child.requirementIds.join(", ")} / ${child.acceptanceIds.join(", ")} |`),
      "",
      `## \u8DE8 ${childLabel} \u63A5\u53E3\u4E0E\u5171\u4EAB\u5951\u7EA6`,
      ""
    );
    if (plan.sharedContracts.length === 0) lines.push(`- \u65E0\u8DE8 ${childLabel} \u5171\u4EAB\u63A5\u53E3\uFF1B\u5B50\u7EA7\u4EC5\u901A\u8FC7\u51BB\u7ED3\u8F93\u51FA\u548C\u805A\u5408\u95E8\u7981\u7EC4\u5408\u3002`);
    else {
      lines.push(
        "| \u7C7B\u578B | \u5951\u7EA6 | \u63D0\u4F9B\u65B9 | \u6D88\u8D39\u65B9 | \u8BF4\u660E | \u8986\u76D6\u9700\u6C42 |",
        "| --- | --- | --- | --- | --- | --- |",
        ...plan.sharedContracts.map((contract2) => `| ${contract2.kind} | ${markdownTableCell(contract2.name)} | ${contract2.providerChildIds.join(", ")} | ${contract2.consumerChildIds.join(", ")} | ${markdownTableCell(contract2.description)} | ${contract2.requirementIds.join(", ")} |`)
      );
    }
    lines.push("", "## \u96C6\u6210\u6D41\u7A0B", "", ...plan.integrationFlow.map((item) => `- ${item}`));
    lines.push(
      "",
      "## \u5F00\u53D1\u4E0E\u96C6\u6210\u6CE2\u6B21",
      "",
      "| \u6CE2\u6B21 | \u540D\u79F0 | \u5B50\u7EA7 | \u9000\u51FA\u6761\u4EF6 |",
      "| --- | --- | --- | --- |",
      ...plan.deliveryWaves.map((wave) => `| ${wave.order} | ${markdownTableCell(wave.name)} | ${wave.childIds.join(", ")} | ${markdownTableCell(wave.exitCriteria)} |`)
    );
  }
  lines.push(
    "",
    "## \u6D4B\u8BD5\u4E0E\u9A8C\u6536\u6620\u5C04",
    "",
    "| \u9A8C\u6536\u9879 | \u9A8C\u8BC1\u65B9\u6CD5 | \u51BB\u7ED3\u547D\u4EE4\u5E8F\u53F7 |",
    "| --- | --- | --- |",
    ...plan.testPlan.map((test) => `| ${test.acceptanceIds.join(", ")} | ${markdownTableCell(test.approach)} | ${test.commandIndexes.join(", ")} |`),
    "",
    "## \u4EBA\u5DE5\u8BC4\u5BA1\u91CD\u70B9",
    "",
    ...plan.reviewPoints.map((item) => `- ${item}`),
    "",
    "## \u51BB\u7ED3\u8BF4\u660E",
    "",
    "- \u8BF7\u5148\u8BC4\u5BA1\u672C\u6587\u4EF6\u4E2D\u7684\u5F00\u53D1\u76EE\u7684\u3001\u5185\u5BB9\u3001\u6587\u4EF6\u3001\u63A5\u53E3/\u5171\u4EAB\u5951\u7EA6\u3001\u4F9D\u8D56\u6CE2\u6B21\u548C\u6D4B\u8BD5\u6620\u5C04\u3002",
    "- \u5982\u9700\u4FEE\u6539\uFF0C\u5148\u4FEE\u6539 definition \u5E76\u91CD\u65B0 prepare\uFF1B\u4E0D\u8981\u51BB\u7ED3\u9519\u8BEF\u7248\u672C\u3002",
    `- \u53EA\u6709\u5BF9\u6307\u7EB9 \`${state.baselineFingerprint}\` \u660E\u786E\u786E\u8BA4\u540E\uFF0C\u624D\u53EF\u6267\u884C freeze-item\uFF1B\u51BB\u7ED3\u540E\u5F00\u53D1\u4E0A\u4E0B\u6587\u5FC5\u987B\u643A\u5E26\u672C\u8BA1\u5212\u3002`,
    ""
  );
  return lines.join("\n");
}
function resolveSelfHostingPolicy({ packageName, explicitDogfood = false } = {}) {
  const implementationPackages = /* @__PURE__ */ new Set(["hierarchical-delivery-governance"]);
  if (implementationPackages.has(packageName) && explicitDogfood !== true) {
    return {
      route: "SELF_HOSTING_MAINTENANCE",
      createsRuntimePackage: false,
      reason: "HIERARCHICAL_GOVERNANCE_SELF_MAINTENANCE"
    };
  }
  return {
    route: "STANDARD_HIERARCHICAL_GOVERNANCE",
    createsRuntimePackage: true,
    reason: explicitDogfood === true ? "EXPLICIT_DOGFOOD" : "NOT_SELF_HOSTING"
  };
}

// src/work-items/runtime.mjs
var WORK_ITEM_REGISTRY_FILE = "work-item-registry.json";
var WORK_ITEMS_DIRECTORY = "work-items";
var GOVERNANCE_DIRECTORY = ".hierarchical-delivery-governance";
var WORK_ITEM_REGISTRY_SCHEMA_VERSION = 3;
var DELIVERY_STATUSES = Object.freeze([
  "NOT_READY",
  "WAITING_FOR_INDEPENDENT_REVIEW",
  "WAITING_FOR_USER_CONFIRMATION",
  "COMPLETED"
]);
var ACCEPTANCE_STATUSES = DELIVERY_STATUSES;
var ACCEPTANCE_REPORT_STATUSES = Object.freeze([
  ...ACCEPTANCE_STATUSES,
  "WAITING_FOR_GATE",
  "BLOCKED",
  "VERIFIED"
]);
var DEVELOPMENT_MODES = Object.freeze(["active", "manual"]);
function fail2(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}
function json(value) {
  return `${JSON.stringify(value, null, 2)}
`;
}
function timestamp(now) {
  const value = typeof now === "function" ? now() : now ?? /* @__PURE__ */ new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) fail2("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid");
  return date.toISOString();
}
async function assertSelfHostingDogfood(root, explicitDogfood, fs) {
  let packageName;
  try {
    const packageJson = JSON.parse((await readSafeRegularFile(root, "package.json", { fs })).toString("utf8"));
    if (typeof packageJson?.name === "string") packageName = packageJson.name;
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "PATH_MISSING" || error instanceof SyntaxError) return;
    throw error;
  }
  const policy = resolveSelfHostingPolicy({ packageName, explicitDogfood });
  if (policy.createsRuntimePackage === false) {
    fail2("SELF_HOSTING_DOGFOOD_REQUIRED", "The hierarchical governance implementation repository requires explicit dogfood for runtime mutations");
  }
}
function emptyRegistry(root, at) {
  return {
    schemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
    coordinationRoot: path3.resolve(root),
    revision: 0,
    currentFocus: { workItemId: null, purpose: null },
    workItems: [],
    promotionHistory: [],
    migrationHistory: [],
    updatedAt: at
  };
}
function registryPath(root) {
  return path3.join(root, GOVERNANCE_DIRECTORY, WORK_ITEM_REGISTRY_FILE);
}
function itemRelativePath(id) {
  return path3.posix.join(WORK_ITEMS_DIRECTORY, id);
}
function itemPath(root, id) {
  return path3.join(root, GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, id);
}
function sortedItems(items) {
  return [...items].sort((left, right) => left.id.localeCompare(right.id));
}
function itemById(registry, id) {
  const item = registry.workItems.find((entry) => entry.id === id);
  if (!item) fail2("WORK_ITEM_NOT_FOUND", `Unknown work item: ${id}`, { id });
  return item;
}
function validEvidenceReference(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const portable = typeof value.path === "string" ? value.path.replaceAll("\\", "/") : "";
  return portable.length > 0 && !path3.posix.isAbsolute(portable) && !portable.split("/").includes("..") && typeof value.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.sha256);
}
function nonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}
function safeWorkItemId(value) {
  return typeof value === "string" && /^[a-z0-9][a-z0-9._-]*$/.test(value) && !value.endsWith(".") && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/.test(value);
}
function validDevelopmentMode(value, entry) {
  return value && typeof value === "object" && !Array.isArray(value) && value.schemaVersion === 1 && value.taskId === entry.id && value.baselineFingerprint === entry.baselineFingerprint && DEVELOPMENT_MODES.includes(value.mode) && value.confirmedBy === "user" && typeof value.confirmedAt === "string" && !Number.isNaN(Date.parse(value.confirmedAt));
}
function validDeliveryArtifact(action, value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.schemaVersion !== 1) return false;
  if (action === "INDEPENDENT_REVIEW_PASS") {
    return value.kind === "INDEPENDENT_REVIEW" && nonEmptyString(value.reviewer) && value.isolation === "FRESH_READ_ONLY" && value.verdict === "PASS" && value.findings && typeof value.findings === "object" && !Array.isArray(value.findings) && value.findings.p0 === 0 && value.findings.p1 === 0;
  }
  if (action === "HUMAN_REVIEW_ACCEPTED") {
    return value.kind === "HUMAN_REVIEW" && nonEmptyString(value.reviewer) && value.verdict === "ACCEPTED";
  }
  return action === "USER_CONFIRMED" && value.kind === "USER_CONFIRMATION" && nonEmptyString(value.confirmedBy) && value.decision === "CONFIRMED";
}
function validDeliveryEvidence(value, actions) {
  return value && typeof value === "object" && !Array.isArray(value) && actions.includes(value.action) && validEvidenceReference(value.evidence) && validDeliveryArtifact(value.action, value.artifact) && typeof value.recordedAt === "string" && !Number.isNaN(Date.parse(value.recordedAt));
}
function validDelivery(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !DELIVERY_STATUSES.includes(value.status)) return false;
  if (value.status === "NOT_READY" || value.status === "WAITING_FOR_INDEPENDENT_REVIEW") {
    return value.review === null && value.userConfirmation === null;
  }
  const reviewValid = validDeliveryEvidence(
    value.review,
    ["INDEPENDENT_REVIEW_PASS", "HUMAN_REVIEW_ACCEPTED"]
  );
  if (value.status === "WAITING_FOR_USER_CONFIRMATION") {
    return reviewValid && value.userConfirmation === null;
  }
  return reviewValid && validDeliveryEvidence(value.userConfirmation, ["USER_CONFIRMED"]);
}
function validAcceptance(value) {
  return validDelivery(value);
}
function validAcceptanceReport(value, entry) {
  if (value === void 0 || value === null) return true;
  const expectedDirectory = path3.posix.join(
    GOVERNANCE_DIRECTORY,
    WORK_ITEMS_DIRECTORY,
    entry.id
  );
  return value && typeof value === "object" && !Array.isArray(value) && value.schemaVersion === 1 && ACCEPTANCE_REPORT_STATUSES.includes(value.status) && value.jsonPath === path3.posix.join(expectedDirectory, "acceptance-report.json") && value.markdownPath === path3.posix.join(expectedDirectory, "acceptance-report.md") && typeof value.generatedAt === "string" && !Number.isNaN(Date.parse(value.generatedAt));
}
function validateRegistry(registry, root) {
  const valid = registry && typeof registry === "object" && !Array.isArray(registry) && registry.schemaVersion === WORK_ITEM_REGISTRY_SCHEMA_VERSION && registry.coordinationRoot === path3.resolve(root) && Number.isInteger(registry.revision) && registry.revision >= 0 && Array.isArray(registry.workItems) && Array.isArray(registry.promotionHistory) && (registry.migrationHistory === void 0 || Array.isArray(registry.migrationHistory)) && registry.currentFocus && typeof registry.currentFocus === "object";
  if (!valid) fail2("WORK_ITEM_REGISTRY_INVALID", "Work item registry is invalid");
  const ids = registry.workItems.map(({ id }) => id);
  const safeId2 = safeWorkItemId;
  if (new Set(ids).size !== ids.length || ids.some((id) => !safeId2(id))) {
    fail2("WORK_ITEM_REGISTRY_INVALID", "Work item registry contains duplicate or unsafe IDs");
  }
  const byId = new Map(registry.workItems.map((entry) => [entry.id, entry]));
  const fingerprint = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  for (const promotion of registry.promotionHistory) {
    const recordValid = promotion && typeof promotion === "object" && !Array.isArray(promotion);
    const kindsValid = recordValid && (promotion.childKind === "TASK" && promotion.parentKind === "CAPABILITY" || promotion.childKind === "CAPABILITY" && promotion.parentKind === "DELIVERY");
    const promotionValid = recordValid && promotion.schemaVersion === 1 && safeId2(promotion.childId) && safeId2(promotion.parentId) && kindsValid && fingerprint(promotion.previousBaselineFingerprint) && fingerprint(promotion.promotedBaselineFingerprint) && fingerprint(promotion.parentBaselineFingerprint) && typeof promotion.promotedAt === "string" && !Number.isNaN(Date.parse(promotion.promotedAt));
    if (!promotionValid) fail2("WORK_ITEM_REGISTRY_INVALID", "Work item promotion history is invalid");
  }
  for (const migration of registry.migrationHistory ?? []) {
    const migrationValid = migration && typeof migration === "object" && !Array.isArray(migration) && migration.schemaVersion === 1 && migration.fromSchemaVersion === 2 && migration.toSchemaVersion === WORK_ITEM_REGISTRY_SCHEMA_VERSION && safeId2(migration.workItemId) && WORK_ITEM_GATE_LEVELS.includes(migration.taskGateLevel) && fingerprint(migration.previousBaselineFingerprint) && fingerprint(migration.migratedBaselineFingerprint) && fingerprint(migration.previousRegistryFingerprint) && typeof migration.migratedAt === "string" && !Number.isNaN(Date.parse(migration.migratedAt));
    if (!migrationValid) fail2("WORK_ITEM_REGISTRY_INVALID", "Work item migration history is invalid");
  }
  for (const entry of registry.workItems) {
    const validEntry = WORK_ITEM_KINDS.includes(entry.kind) && entry.authorityKind === WORK_ITEM_AUTHORITIES[entry.kind] && WORK_ITEM_GATE_LEVELS.includes(entry.gateLevel) && (entry.kind === "TASK" || entry.gateLevel === "FULL") && (entry.parentId === null || safeId2(entry.parentId)) && Array.isArray(entry.childIds) && entry.childIds.every(safeId2) && entry.packagePath === itemRelativePath(entry.id) && (entry.developmentReview === void 0 || typeof entry.developmentReview === "boolean") && typeof entry.baselineFingerprint === "string" && /^[a-f0-9]{64}$/.test(entry.baselineFingerprint) && typeof entry.contractFingerprint === "string" && /^[a-f0-9]{64}$/.test(entry.contractFingerprint);
    if (!validEntry) fail2("WORK_ITEM_REGISTRY_INVALID", `Work item registry entry is invalid: ${entry.id}`);
    const developmentModeValid = entry.kind === "TASK" ? entry.developmentMode === null || validDevelopmentMode(entry.developmentMode, entry) : entry.developmentMode === null;
    if (!developmentModeValid) {
      fail2("WORK_ITEM_REGISTRY_INVALID", `Work item development mode is invalid: ${entry.id}`);
    }
    if (entry.kind === "TASK") {
      const waitingForMode = entry.status === "WAITING_FOR_DEVELOPMENT_MODE_SELECTION";
      const frozenWithoutMode = entry.developmentMode === null && entry.stage === "BASELINE_FROZEN";
      if (waitingForMode !== frozenWithoutMode) {
        fail2("WORK_ITEM_REGISTRY_INVALID", `Task development mode state is inconsistent: ${entry.id}`);
      }
    }
    const deliveryValid = entry.kind === "DELIVERY" ? entry.delivery === void 0 || validDelivery(entry.delivery) : entry.delivery === void 0 || entry.delivery === null;
    if (!deliveryValid) fail2("WORK_ITEM_REGISTRY_INVALID", `Work item delivery state is invalid: ${entry.id}`);
    const acceptanceValid = entry.parentId === null ? entry.acceptance === void 0 || validAcceptance(entry.acceptance) : entry.acceptance === void 0 || entry.acceptance === null;
    if (!acceptanceValid || !validAcceptanceReport(entry.acceptanceReport, entry)) {
      fail2("WORK_ITEM_REGISTRY_INVALID", `Work item acceptance state is invalid: ${entry.id}`);
    }
    if (entry.kind === "DELIVERY" && entry.parentId !== null) {
      fail2("WORK_ITEM_REGISTRY_INVALID", "Delivery entries cannot have parents");
    }
    if (entry.kind !== "DELIVERY" && entry.parentId !== null) {
      const parent = byId.get(entry.parentId);
      const expectedParentKind = entry.kind === "CAPABILITY" ? "DELIVERY" : "CAPABILITY";
      if (!parent || parent.kind !== expectedParentKind || !parent.childIds.includes(entry.id)) {
        fail2("WORK_ITEM_REGISTRY_INVALID", `Work item parent relation is invalid: ${entry.id}`);
      }
    }
  }
  const focusId = registry.currentFocus.workItemId;
  if (focusId !== null && (!safeId2(focusId) || !byId.has(focusId))) {
    fail2("WORK_ITEM_REGISTRY_INVALID", "Current focus references an unknown work item");
  }
  return registry;
}
async function ensureRuntimeRoot(root, fs) {
  const rootStat = await fs.lstat(root).catch((error) => {
    if (error.code === "ENOENT") fail2("WORK_ITEM_ROOT_INVALID", "Coordination root must already exist");
    throw error;
  });
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail2("WORK_ITEM_ROOT_INVALID", "Coordination root must be a regular directory");
  const runtimeRoot = await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  await fs.mkdir(runtimeRoot, { recursive: true });
  await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  const itemsRoot = await assertSafePath(root, path3.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  await fs.mkdir(itemsRoot, { recursive: true });
  await assertSafePath(root, path3.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  return runtimeRoot;
}
async function assertPersistedDeliveryEvidence(root, registry, fs) {
  for (const entry of registry.workItems.filter((candidate) => {
    const acceptance = candidate.acceptance ?? candidate.delivery;
    return candidate.parentId === null && acceptance && acceptance.status !== "NOT_READY" && acceptance.status !== "WAITING_FOR_INDEPENDENT_REVIEW";
  })) {
    const acceptance = entry.acceptance ?? entry.delivery;
    const records = [acceptance.review];
    if (acceptance.status === "COMPLETED") records.push(acceptance.userConfirmation);
    for (const record of records) {
      let bytes;
      try {
        bytes = await readSafeRegularFile(root, record.evidence.path, { fs });
      } catch {
        fail2("WORK_ITEM_DELIVERY_EVIDENCE_MISSING", `Persisted delivery evidence is unavailable: ${record.evidence.path}`);
      }
      if (sha256Bytes(bytes) !== record.evidence.sha256) {
        fail2("WORK_ITEM_DELIVERY_EVIDENCE_CHANGED", `Persisted delivery evidence changed: ${record.evidence.path}`);
      }
      let artifact;
      try {
        artifact = JSON.parse(bytes.toString("utf8"));
      } catch {
        fail2("WORK_ITEM_DELIVERY_EVIDENCE_INVALID", `Persisted delivery evidence is invalid JSON: ${record.evidence.path}`);
      }
      if (!validDeliveryArtifact(record.action, artifact) || canonicalJson(artifact) !== canonicalJson(record.artifact)) {
        fail2("WORK_ITEM_DELIVERY_EVIDENCE_CHANGED", `Persisted delivery evidence no longer matches its registry snapshot: ${record.evidence.path}`);
      }
    }
  }
}
async function assertPersistedDevelopmentModes(root, registry, fs) {
  for (const entry of registry.workItems.filter(({ kind, developmentMode }) => kind === "TASK" && developmentMode !== null)) {
    let artifact;
    try {
      artifact = await readJsonFile(
        itemPath(root, entry.id),
        "development-mode.json",
        fs,
        "WORK_ITEM_DEVELOPMENT_MODE_INVALID"
      );
    } catch {
      fail2("WORK_ITEM_DEVELOPMENT_MODE_INVALID", `${entry.id} development-mode.json is missing or unreadable`);
    }
    if (!validDevelopmentMode(artifact, entry) || canonicalJson(artifact) !== canonicalJson(entry.developmentMode)) {
      fail2("WORK_ITEM_DEVELOPMENT_MODE_CHANGED", `${entry.id} development-mode.json changed after confirmation`);
    }
  }
}
async function readRegistryUnlocked(root, fs, { allowMissing = false, now } = {}) {
  const target = registryPath(root);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, target, { fs });
  } catch (error) {
    if (error.code === "ENOENT" && allowMissing) return emptyRegistry(root, timestamp(now));
    if (error.code === "ENOENT") fail2("WORK_ITEM_REGISTRY_MISSING", "Work item registry does not exist");
    throw error;
  }
  let registry;
  try {
    registry = JSON.parse(bytes.toString("utf8"));
  } catch {
    fail2("WORK_ITEM_REGISTRY_INVALID", "Work item registry is not valid JSON");
  }
  const validated = validateRegistry(registry, root);
  await assertPersistedDeliveryEvidence(root, validated, fs);
  await assertPersistedDevelopmentModes(root, validated, fs);
  return validated;
}
function humanStatus(value) {
  return {
    DELIVERY: "\u4EA4\u4ED8",
    CAPABILITY: "\u80FD\u529B",
    TASK: "\u4EFB\u52A1",
    PREPARED: "\u7B49\u5F85\u5F00\u53D1\u65B9\u6848\u8BC4\u5BA1",
    WAITING_FOR_DEVELOPMENT_MODE_SELECTION: "\u7B49\u5F85\u9009\u62E9\u5F00\u53D1\u65B9\u5F0F",
    FROZEN: "\u5DF2\u51BB\u7ED3",
    CLAIMED: "\u5F00\u53D1\u4E2D",
    IMPLEMENTED: "\u7B49\u5F85\u95E8\u7981\u9A8C\u6536",
    BLOCKED: "\u5DF2\u963B\u65AD",
    VERIFIED: "\u95E8\u7981\u5DF2\u901A\u8FC7",
    NOT_READY: "\u5C1A\u672A\u5C31\u7EEA",
    WAITING_FOR_INDEPENDENT_REVIEW: "\u7B49\u5F85\u72EC\u7ACB\u9A8C\u6536",
    WAITING_FOR_USER_CONFIRMATION: "\u7B49\u5F85\u7528\u6237\u786E\u8BA4",
    COMPLETED: "\u5DF2\u5B8C\u6210",
    NOT_RUN: "\u672A\u8FD0\u884C",
    PASS: "\u901A\u8FC7",
    FAIL: "\u672A\u901A\u8FC7"
  }[value] ?? value ?? "\u65E0";
}
function itemHumanArtifacts(id, acceptanceReport = null) {
  const base = path3.posix.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, id);
  return {
    overview: path3.posix.join(base, "overview.md"),
    developmentReview: path3.posix.join(base, "development-review.md"),
    baseline: path3.posix.join(base, "baseline.md"),
    progress: path3.posix.join(base, "progress.md"),
    acceptanceReport: acceptanceReport?.markdownPath ?? null
  };
}
function nextAction(entry) {
  if (entry.stage === "WAITING_FOR_BASELINE_CONFIRMATION") {
    return "\u4EBA\u5DE5\u8BC4\u5BA1 development-review.md\uFF1B\u9700\u8981\u4FEE\u6539\u5219\u91CD\u65B0\u8D77\u8349\uFF0C\u786E\u8BA4\u65E0\u8BEF\u540E\u6309\u5F53\u524D\u6307\u7EB9\u6267\u884C freeze-item\u3002";
  }
  if (entry.status === "WAITING_FOR_DEVELOPMENT_MODE_SELECTION") return "\u4EBA\u5DE5\u9009\u62E9 active \u6216 manual \u5F00\u53D1\u65B9\u5F0F\u3002";
  if (entry.status === "FROZEN" && entry.kind === "TASK") return "\u7B49\u5F85\u4F9D\u8D56\u6EE1\u8DB3\u540E\u6267\u884C dispatch-task\u3002";
  if (entry.status === "FROZEN") return "\u7EE7\u7EED\u51C6\u5907\u5DF2\u8BA1\u5212\u5B50\u7EA7\uFF0C\u6216\u5728\u5206\u89E3\u5C01\u53E3\u4E14\u5B50\u7EA7\u901A\u8FC7\u540E\u8FD0\u884C\u805A\u5408\u95E8\u7981\u3002";
  if (entry.status === "CLAIMED") return "\u7B49\u5F85\u5F00\u53D1\u7ED3\u679C\u6309 operationId \u5199\u56DE\u3002";
  if (entry.status === "IMPLEMENTED") return "\u5F62\u6210\u4E25\u683C evidence \u5E76\u6267\u884C accept-item \u95E8\u7981\u9A8C\u6536\u3002";
  if (entry.status === "BLOCKED") return "\u5904\u7406\u963B\u65AD\u540E\u6309\u5F53\u524D\u6307\u7EB9\u663E\u5F0F retry-item\u3002";
  if (entry.status === "VERIFIED" && entry.parentId === null) {
    const acceptance = entry.acceptance ?? entry.delivery;
    if (acceptance?.status === "WAITING_FOR_INDEPENDENT_REVIEW") return "\u6267\u884C\u72EC\u7ACB\u9A8C\u6536\u6216\u8BB0\u5F55\u4EBA\u5DE5\u9A8C\u6536\u63A5\u53D7\u3002";
    if (acceptance?.status === "WAITING_FOR_USER_CONFIRMATION") return "\u7B49\u5F85\u7528\u6237\u6700\u7EC8\u786E\u8BA4\u3002";
  }
  return entry.status === "VERIFIED" ? "\u7B49\u5F85\u7236\u7EA7\u805A\u5408\u95E8\u7981\u3002" : "\u67E5\u770B\u5F53\u524D\u72B6\u6001\u4E0E\u95E8\u7981\u8BC1\u636E\u3002";
}
function renderWorkspaceOverview(registry) {
  const lines = [
    "# \u5DE5\u4F5C\u9879\u603B\u89C8",
    "",
    "> \u672C\u6587\u4EF6\u662F\u9762\u5411\u7528\u6237\u548C\u534F\u4F5C\u8005\u7684\u53EF\u8BFB\u6295\u5F71\uFF1B\u673A\u5668\u6743\u5A01\u4E3A `work-item-registry.json`\u3002",
    `> \u6CE8\u518C\u8868\u7248\u672C\uFF1A${registry.revision}`,
    `> \u5F53\u524D\u7126\u70B9\uFF1A${registry.currentFocus.workItemId ?? "\u65E0"}`,
    "",
    "| \u5DE5\u4F5C\u9879 | \u7C7B\u578B | \u95E8\u7981\u7B49\u7EA7 | \u7236\u7EA7 | \u5F53\u524D\u72B6\u6001 | \u5F00\u53D1\u8BC4\u5BA1 | \u5F00\u53D1\u65B9\u5F0F | \u6700\u7EC8\u9A8C\u6536 | \u76F4\u63A5\u5B50\u7EA7 | \u5168\u90E8\u540E\u4EE3 | \u95E8\u7981 | \u8BA4\u9886\u8005 | \u9A8C\u6536\u62A5\u544A |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
  ];
  for (const item of sortedItems(registry.workItems)) {
    const report = item.acceptanceReport ? `[\u67E5\u770B](${path3.posix.relative(GOVERNANCE_DIRECTORY, item.acceptanceReport.markdownPath)})` : "\u5C1A\u672A\u751F\u6210";
    const acceptance = item.acceptance ?? (item.parentId === null ? item.delivery : null);
    const itemLink = `[${item.id}](${path3.posix.join(WORK_ITEMS_DIRECTORY, item.id, "overview.md")})`;
    const review = item.developmentReview ? `[\u67E5\u770B](${path3.posix.join(WORK_ITEMS_DIRECTORY, item.id, "development-review.md")})` : "\u65E7\u7248\u57FA\u7EBF\u672A\u8BB0\u5F55";
    lines.push(`| ${itemLink} | ${humanStatus(item.kind)} | ${item.gateLevel} | ${item.parentId ?? "\u65E0"} | ${humanStatus(item.status)} | ${review} | ${item.developmentMode?.mode ?? "\u4E0D\u9002\u7528"} | ${acceptance ? humanStatus(acceptance.status) : "\u4E0D\u9002\u7528"} | ${item.progress.directChildren.verified}/${item.progress.directChildren.total} | ${item.progress.descendants.verified}/${item.progress.descendants.total} | ${humanStatus(item.gate.status)} | ${item.claim?.owner ?? "\u65E0"} | ${report} |`);
  }
  lines.push("");
  return lines.join("\n");
}
function renderItemOverview(entry) {
  return [
    `# ${entry.id} \u5DE5\u4F5C\u9879\u6982\u89C8`,
    "",
    `- \u7C7B\u578B\uFF1A${entry.kind}`,
    `- \u95E8\u7981\u7B49\u7EA7\uFF1A${entry.gateLevel}`,
    `- \u6743\u9650\u6027\u8D28\uFF1A${entry.authorityKind}`,
    `- \u7236\u7EA7\uFF1A${entry.parentId ?? "\u65E0"}`,
    "- \u57FA\u7EBF\uFF1A[baseline.md](baseline.md)",
    `- \u5F00\u53D1\u8BC4\u5BA1\uFF1A${entry.developmentReview ? "[development-review.md](development-review.md)" : "\u65E7\u7248\u57FA\u7EBF\u672A\u8BB0\u5F55\uFF1B\u4FEE\u8BA2\u65F6\u5FC5\u987B\u8865\u5145"}`,
    `- \u7ED3\u6784\u5316\u5F00\u53D1\u8BA1\u5212\uFF1A${entry.developmentReview ? "[development-plan.json](development-plan.json)" : "\u65E7\u7248\u57FA\u7EBF\u672A\u8BB0\u5F55"}`,
    "- \u8FDB\u5EA6\uFF1A[progress.md](progress.md)",
    `- \u7236\u5951\u7EA6\u6307\u7EB9\uFF1A${entry.parentContractFingerprint ?? "\u65E0"}`,
    `- \u5B50\u7EA7\uFF1A${entry.childIds.join(", ") || "\u65E0"}`,
    `- \u9A8C\u6536\u62A5\u544A\uFF1A${entry.acceptanceReport ? "[acceptance-report.md](acceptance-report.md)" : "\u5C1A\u672A\u751F\u6210"}`,
    `- \u4E0B\u4E00\u6B65\uFF1A${nextAction(entry)}`,
    ""
  ].join("\n");
}
function renderItemProgress(entry) {
  const acceptance = entry.acceptance ?? (entry.parentId === null ? entry.delivery : null);
  return [
    `# ${entry.id} \u8FDB\u5EA6`,
    "",
    `- \u8BB0\u5F55\u7248\u672C\uFF1A${entry.recordRevision}`,
    `- \u9636\u6BB5\uFF1A${entry.stage}`,
    `- \u5F53\u524D\u72B6\u6001\uFF1A${humanStatus(entry.status)}`,
    `- \u95E8\u7981\u7B49\u7EA7\uFF1A${entry.gateLevel}`,
    `- \u6700\u7EC8\u9A8C\u6536\uFF1A${acceptance ? humanStatus(acceptance.status) : "\u4E0D\u9002\u7528"}`,
    `- \u95E8\u7981\uFF1A${humanStatus(entry.gate.status)}`,
    `- \u5F00\u53D1\u65B9\u5F0F\uFF1A${entry.developmentMode?.mode ?? "\u672A\u9009\u62E9"}`,
    `- \u8BA4\u9886\uFF1A${entry.claim ? `${entry.claim.owner} / ${entry.claim.operationId}` : "\u65E0"}`,
    `- \u76F4\u63A5\u5B50\u7EA7\uFF1A${entry.progress.directChildren.verified}/${entry.progress.directChildren.total} \u5DF2\u9A8C\u8BC1\uFF1B${entry.progress.directChildren.blocked} \u963B\u65AD\uFF1B${entry.progress.directChildren.active} \u6D3B\u52A8`,
    `- \u5168\u90E8\u540E\u4EE3\uFF1A${entry.progress.descendants.verified}/${entry.progress.descendants.total} \u5DF2\u9A8C\u8BC1\uFF1B${entry.progress.descendants.blocked} \u963B\u65AD\uFF1B${entry.progress.descendants.active} \u6D3B\u52A8`,
    `- \u9A8C\u6536\u62A5\u544A\uFF1A${entry.acceptanceReport ? "[acceptance-report.md](acceptance-report.md)" : "\u5C1A\u672A\u751F\u6210"}`,
    `- \u4E0B\u4E00\u6B65\uFF1A${nextAction(entry)}`,
    `- \u66F4\u65B0\u65F6\u95F4\uFF1A${entry.updatedAt}`,
    ""
  ].join("\n");
}
function progressCounts(entries) {
  return {
    total: entries.length,
    verified: entries.filter(({ status }) => status === "VERIFIED").length,
    blocked: entries.filter(({ status }) => status === "BLOCKED").length,
    active: entries.filter(({ status }) => status === "CLAIMED" || status === "IMPLEMENTED").length
  };
}
function recomputeRegistryProgress(registry) {
  const byId = new Map(registry.workItems.map((entry) => [entry.id, entry]));
  const descendants = (entry, visited = /* @__PURE__ */ new Set()) => {
    if (visited.has(entry.id)) fail2("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle");
    const nextVisited = new Set(visited).add(entry.id);
    const result = [];
    for (const childId of entry.childIds) {
      const child = byId.get(childId) ?? { id: childId, status: "PLANNED", childIds: [] };
      result.push(child);
      if (byId.has(childId)) result.push(...descendants(child, nextVisited));
    }
    return result;
  };
  for (const entry of registry.workItems) {
    const direct = entry.childIds.map((id) => byId.get(id) ?? { id, status: "PLANNED" });
    entry.progress = {
      directChildren: progressCounts(direct),
      descendants: progressCounts(descendants(entry))
    };
  }
}
async function writeRegistryUnlocked(root, registry, fs) {
  recomputeRegistryProgress(registry);
  registry.workItems = sortedItems(registry.workItems);
  await atomicWriteFile(registryPath(root), json(registry), { fs });
  await atomicWriteFile(
    path3.join(root, GOVERNANCE_DIRECTORY, "workspace-overview.md"),
    renderWorkspaceOverview(registry),
    { fs }
  );
  for (const entry of registry.workItems) {
    const target = itemPath(root, entry.id);
    let stat;
    try {
      stat = await fs.lstat(target);
    } catch (error) {
      if (error.code === "ENOENT") continue;
      throw error;
    }
    if (!stat.isDirectory() || stat.isSymbolicLink()) fail2("WORK_ITEM_PACKAGE_INVALID", `${entry.id} package path is invalid`);
    await atomicWriteFile(path3.join(target, "overview.md"), renderItemOverview(entry), { fs });
    await atomicWriteFile(path3.join(target, "progress.md"), renderItemProgress(entry), { fs });
  }
}
async function withRegistry(root, fs, operation, { now } = {}) {
  await ensureRuntimeRoot(root, fs);
  return withRuntimeDirectoryTransaction(registryPath(root), async () => {
    const registry = await readRegistryUnlocked(root, fs, { allowMissing: true, now });
    return operation(registry);
  }, { fs, now });
}
async function readJsonFile(root, target, fs, code) {
  let value;
  try {
    value = JSON.parse((await readSafeRegularFile(root, target, { fs })).toString("utf8"));
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail2(code, `Unable to read ${path3.basename(target)}`);
  }
  return value;
}
async function readPackageDefinition(root, entry, fs) {
  const target = itemPath(root, entry.id);
  const definition = await readJsonFile(target, "baseline.json", fs, "WORK_ITEM_PACKAGE_INVALID");
  const state = await readJsonFile(target, "state.json", fs, "WORK_ITEM_PACKAGE_INVALID");
  const fingerprint = workItemBaselineFingerprint(definition);
  const reviewValid = !definition.developmentPlan || state.review && state.review.schemaVersion === 1 && state.review.baselineFingerprint === fingerprint && (state.stage === "WAITING_FOR_BASELINE_CONFIRMATION" && state.review.status === "WAITING_FOR_HUMAN_REVIEW" && state.review.reviewedBy === null && state.review.reviewedAt === null || state.stage === "BASELINE_FROZEN" && state.review.status === "APPROVED" && state.review.reviewedBy === "user" && typeof state.review.reviewedAt === "string" && !Number.isNaN(Date.parse(state.review.reviewedAt)));
  let generatedFilesValid = true;
  for (const [name, expected] of Object.entries(definitionFiles(definition, state))) {
    if (name === "state.json") continue;
    try {
      const actual = await readSafeRegularFile(root, path3.join(target, name), { fs });
      if (!actual.equals(Buffer.from(expected, "utf8"))) generatedFilesValid = false;
    } catch {
      generatedFilesValid = false;
    }
  }
  const valid = state.schemaVersion === WORK_ITEM_SCHEMA_VERSION && state.id === entry.id && state.baselineFingerprint === fingerprint && state.contractFingerprint === workItemContractFingerprint(definition) && entry.baselineFingerprint === state.baselineFingerprint && entry.contractFingerprint === state.contractFingerprint && reviewValid && generatedFilesValid;
  if (!valid) fail2("WORK_ITEM_PACKAGE_CHANGED", `${entry.id} package changed after preparation`, { id: entry.id });
  return { definition, state, target };
}
async function assertCurrentLineage(root, registry, entry, fs, seen = /* @__PURE__ */ new Set()) {
  if (seen.has(entry.id)) fail2("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle");
  seen.add(entry.id);
  const own = await readPackageDefinition(root, entry, fs);
  if (!entry.parentId) return own;
  const parentEntry = itemById(registry, entry.parentId);
  const parentTarget = itemPath(root, parentEntry.id);
  const parentDefinition = await readJsonFile(parentTarget, "baseline.json", fs, "WORK_ITEM_PACKAGE_INVALID");
  const actualParentContract = workItemChildContractFingerprint(parentDefinition, entry.id);
  if (entry.parentContractFingerprint !== actualParentContract || own.definition.parentContractFingerprint !== actualParentContract) {
    fail2("WORK_ITEM_BASELINE_STALE", `${entry.id} parent contract changed`, {
      id: entry.id,
      parentId: parentEntry.id,
      expected: entry.parentContractFingerprint,
      actual: actualParentContract
    });
  }
  await assertCurrentLineage(root, registry, parentEntry, fs, seen);
  return own;
}
function definitionFiles(definition, state) {
  const files = {
    "baseline.json": json(definition),
    "baseline.md": renderWorkItemBaseline(definition),
    "work-item.json": json({
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
      id: definition.id,
      kind: definition.kind,
      gateLevel: definition.gateLevel,
      authorityKind: definition.authorityKind,
      parentId: definition.parentId
    }),
    "state.json": json(state)
  };
  if (definition.children) files["children.json"] = json({ schemaVersion: WORK_ITEM_SCHEMA_VERSION, children: definition.children });
  if (definition.execution) files["execution.json"] = json({ schemaVersion: WORK_ITEM_SCHEMA_VERSION, ...definition.execution });
  if (definition.developmentPlan) {
    files["development-plan.json"] = json({
      schemaVersion: 1,
      workItemId: definition.id,
      kind: definition.kind,
      baselineFingerprint: state.baselineFingerprint,
      developmentPlan: definition.developmentPlan
    });
    files["development-review.md"] = renderDevelopmentReview(definition, state);
  }
  return files;
}
function rawDefinition(definition) {
  const raw = { ...definition };
  delete raw.authorityKind;
  delete raw.parentContractFingerprint;
  return raw;
}
async function writeNewPackage(target, files, fs) {
  await atomicWriteDirectory(target, async (staging) => {
    for (const [name, contents] of Object.entries(files)) {
      await atomicWriteFile(path3.join(staging, name), contents, { fs });
    }
  }, { fs });
}
function entryFromDefinition(definition, state, at) {
  const rootAcceptance = definition.parentId === null ? { status: "NOT_READY", review: null, userConfirmation: null } : null;
  return {
    id: definition.id,
    kind: definition.kind,
    gateLevel: definition.gateLevel,
    authorityKind: definition.authorityKind,
    parentId: definition.parentId,
    childIds: definition.children?.map(({ id }) => id) ?? [],
    packagePath: itemRelativePath(definition.id),
    developmentReview: Boolean(definition.developmentPlan),
    stage: state.stage,
    status: "PREPARED",
    baselineFingerprint: state.baselineFingerprint,
    contractFingerprint: state.contractFingerprint,
    parentContractFingerprint: state.parentContractFingerprint,
    gate: { status: "NOT_RUN", evidence: null },
    delivery: definition.kind === "DELIVERY" ? { status: "NOT_READY", review: null, userConfirmation: null } : null,
    acceptance: rootAcceptance,
    acceptanceReport: null,
    developmentMode: null,
    claim: null,
    latestEvidence: null,
    latestResult: null,
    recordRevision: 1,
    createdAt: at,
    updatedAt: at
  };
}
function validateTaskDependencies(definition, parent) {
  if (definition.kind !== "TASK") return;
  if (!parent) {
    if (definition.execution.dependsOn.length > 0) {
      fail2("WORK_ITEM_DEPENDENCY_INVALID", "A root Task cannot depend on sibling Tasks; use a Capability root");
    }
    return;
  }
  const siblingIds = new Set(parent.children.map(({ id }) => id));
  if (definition.execution.dependsOn.some((id) => !siblingIds.has(id))) {
    fail2("WORK_ITEM_DEPENDENCY_INVALID", "Task dependsOn must reference planned sibling Tasks");
  }
}
async function validateCapabilityDependencyGraph(root, registry, candidate, fs) {
  if (candidate.kind !== "CAPABILITY") return;
  const graph = /* @__PURE__ */ new Map();
  for (const entry of registry.workItems.filter(({ kind, parentId }) => kind === "CAPABILITY" && parentId === candidate.parentId)) {
    const definition = entry.id === candidate.id ? candidate : (await readPackageDefinition(root, entry, fs)).definition;
    graph.set(definition.id, definition.decomposition.dependsOn);
  }
  graph.set(candidate.id, candidate.decomposition.dependsOn);
  const visiting = /* @__PURE__ */ new Set();
  const visited = /* @__PURE__ */ new Set();
  const visit = (id) => {
    if (visiting.has(id)) fail2("WORK_ITEM_DEPENDENCY_CYCLE", "Capability dependencies contain a cycle");
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dependency of graph.get(id) ?? []) if (graph.has(dependency)) visit(dependency);
    visiting.delete(id);
    visited.add(id);
  };
  for (const id of graph.keys()) visit(id);
}
async function prepareWorkItem({
  root,
  definition,
  hostRuntime: suppliedHostRuntime,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp(now);
  const hostRuntime = requireHostRuntime(suppliedHostRuntime);
  return withRegistry(root, fs, async (registry) => {
    const existing = registry.workItems.find(({ id }) => id === definition?.id);
    if (existing) {
      const current = await readPackageDefinition(root, existing, fs);
      let candidate;
      if (definition.kind === "DELIVERY" || definition.parentId === null) {
        candidate = validateWorkItemDefinition(definition);
      } else {
        const parentEntry = itemById(registry, definition.parentId);
        const parent2 = (await readPackageDefinition(root, parentEntry, fs)).definition;
        candidate = validateWorkItemDefinition(definition, { parent: parent2 });
        validateTaskDependencies(candidate, parent2);
      }
      await validateCapabilityDependencyGraph(root, registry, candidate, fs);
      const candidateFingerprint = workItemBaselineFingerprint(candidate);
      if (candidateFingerprint !== current.state.baselineFingerprint) {
        if (existing.stage !== "WAITING_FOR_BASELINE_CONFIRMATION" || candidate.id !== existing.id || candidate.kind !== existing.kind || candidate.parentId !== existing.parentId) {
          fail2("WORK_ITEM_SOURCE_CHANGED", `${existing.id} prepared baseline differs from the requested definition`);
        }
        const revisedState = {
          schemaVersion: WORK_ITEM_SCHEMA_VERSION,
          id: candidate.id,
          stage: "WAITING_FOR_BASELINE_CONFIRMATION",
          baselineFingerprint: candidateFingerprint,
          contractFingerprint: workItemContractFingerprint(candidate),
          parentContractFingerprint: candidate.parentContractFingerprint,
          hostRuntime,
          createdAt: current.state.createdAt,
          revisedAt: at,
          frozenAt: null,
          review: {
            schemaVersion: 1,
            status: "WAITING_FOR_HUMAN_REVIEW",
            baselineFingerprint: candidateFingerprint,
            reviewedBy: null,
            reviewedAt: null
          }
        };
        await atomicReplaceDirectory(current.target, async (staging) => {
          for (const [name, contents] of Object.entries(definitionFiles(candidate, revisedState))) {
            await atomicWriteFile(path3.join(staging, name), contents, { fs });
          }
        }, { fs });
        existing.gateLevel = candidate.gateLevel;
        existing.childIds = candidate.children?.map(({ id }) => id) ?? [];
        existing.baselineFingerprint = revisedState.baselineFingerprint;
        existing.contractFingerprint = revisedState.contractFingerprint;
        existing.parentContractFingerprint = revisedState.parentContractFingerprint;
        existing.developmentReview = true;
        existing.recordRevision += 1;
        existing.updatedAt = at;
        registry.currentFocus = { workItemId: existing.id, purpose: "BASELINE_CONFIRMATION" };
        registry.revision += 1;
        registry.updatedAt = at;
        await writeRegistryUnlocked(root, registry, fs);
        return {
          created: false,
          idempotent: false,
          revised: true,
          id: existing.id,
          kind: existing.kind,
          stage: existing.stage,
          baselineFingerprint: existing.baselineFingerprint,
          artifactDir: current.target,
          humanArtifacts: itemHumanArtifacts(existing.id, existing.acceptanceReport),
          nextAction: nextAction(existing)
        };
      }
      return {
        created: false,
        idempotent: true,
        id: existing.id,
        kind: existing.kind,
        stage: existing.stage,
        baselineFingerprint: existing.baselineFingerprint,
        artifactDir: itemPath(root, existing.id),
        humanArtifacts: existing.developmentReview ? itemHumanArtifacts(existing.id, existing.acceptanceReport) : null,
        nextAction: nextAction(existing)
      };
    }
    let parent = null;
    if (definition.kind !== "DELIVERY" && definition.parentId !== null) {
      const parentEntry = itemById(registry, definition.parentId);
      if (parentEntry.stage !== "BASELINE_FROZEN") fail2("WORK_ITEM_PARENT_NOT_FROZEN", "Parent baseline must be frozen first");
      parent = (await assertCurrentLineage(root, registry, parentEntry, fs)).definition;
    }
    const normalized = validateWorkItemDefinition(definition, { parent });
    validateTaskDependencies(normalized, parent);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
      id: normalized.id,
      stage: "WAITING_FOR_BASELINE_CONFIRMATION",
      baselineFingerprint: workItemBaselineFingerprint(normalized),
      contractFingerprint: workItemContractFingerprint(normalized),
      parentContractFingerprint: normalized.parentContractFingerprint,
      hostRuntime,
      createdAt: at,
      frozenAt: null,
      review: {
        schemaVersion: 1,
        status: "WAITING_FOR_HUMAN_REVIEW",
        baselineFingerprint: workItemBaselineFingerprint(normalized),
        reviewedBy: null,
        reviewedAt: null
      }
    };
    const target = await assertSafePath(root, path3.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, normalized.id), { fs });
    await writeNewPackage(target, definitionFiles(normalized, state), fs);
    const entry = entryFromDefinition(normalized, state, at);
    registry.workItems.push(entry);
    if (entry.parentId) {
      const parentEntry = itemById(registry, entry.parentId);
      parentEntry.childIds = [.../* @__PURE__ */ new Set([...parentEntry.childIds, entry.id])].sort();
      parentEntry.recordRevision += 1;
      parentEntry.updatedAt = at;
    }
    registry.currentFocus = { workItemId: entry.id, purpose: "BASELINE_CONFIRMATION" };
    registry.revision += 1;
    registry.updatedAt = at;
    try {
      await writeRegistryUnlocked(root, registry, fs);
    } catch (error) {
      await fs.rm(target, { recursive: true, force: true }).catch(() => {
      });
      throw error;
    }
    return {
      created: true,
      idempotent: false,
      id: entry.id,
      kind: entry.kind,
      stage: entry.stage,
      baselineFingerprint: entry.baselineFingerprint,
      artifactDir: target,
      humanArtifacts: itemHumanArtifacts(entry.id),
      nextAction: nextAction(entry)
    };
  }, { now });
}
async function freezeWorkItem({
  root,
  id,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail2("CONFIRMATION_REQUIRED", "Work item baseline freeze requires explicit confirmation");
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (expectedBaselineFingerprint !== void 0 && entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail2("WORK_ITEM_REVISION_CONFLICT", "The confirmed baseline fingerprint is not current");
    }
    const taskPackage = await assertCurrentLineage(root, registry, entry, fs);
    if (entry.stage === "BASELINE_FROZEN") {
      return {
        created: false,
        idempotent: true,
        id,
        stage: entry.stage,
        baselineFingerprint: entry.baselineFingerprint,
        humanArtifacts: entry.developmentReview ? itemHumanArtifacts(id, entry.acceptanceReport) : null,
        nextAction: nextAction(entry)
      };
    }
    if (entry.stage !== "WAITING_FOR_BASELINE_CONFIRMATION") fail2("WORK_ITEM_STAGE_INVALID", `${id} is not ready to freeze`);
    const state = {
      ...taskPackage.state,
      stage: "BASELINE_FROZEN",
      frozenAt: at,
      ...taskPackage.definition.developmentPlan ? {
        review: {
          ...taskPackage.state.review,
          status: "APPROVED",
          reviewedBy: "user",
          reviewedAt: at
        }
      } : {}
    };
    await atomicWriteFile(path3.join(taskPackage.target, "state.json"), json(state), { fs });
    if (taskPackage.definition.developmentPlan) {
      await atomicWriteFile(
        path3.join(taskPackage.target, "development-review.md"),
        renderDevelopmentReview(taskPackage.definition, state),
        { fs }
      );
    }
    entry.stage = "BASELINE_FROZEN";
    entry.status = entry.kind === "TASK" ? "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" : "FROZEN";
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.kind === "TASK" ? "DEVELOPMENT_MODE_SELECTION" : "DECOMPOSITION"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return {
      created: true,
      idempotent: false,
      id,
      stage: entry.stage,
      baselineFingerprint: entry.baselineFingerprint,
      humanArtifacts: entry.developmentReview ? itemHumanArtifacts(id, entry.acceptanceReport) : null,
      nextAction: nextAction(entry)
    };
  }, { now });
}
async function retryBlockedWorkItem({
  root,
  id,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail2("CONFIRMATION_REQUIRED", "Work item retry requires explicit confirmation");
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.status !== "BLOCKED" || entry.claim) {
      fail2("WORK_ITEM_RETRY_INVALID", "Only an unclaimed BLOCKED work item can be retried");
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail2("WORK_ITEM_REVISION_CONFLICT", "The retry baseline fingerprint is not current");
    }
    const own = await assertCurrentLineage(root, registry, entry, fs);
    entry.status = "FROZEN";
    entry.gate = { status: "NOT_RUN", evidence: null };
    if (entry.parentId === null) {
      entry.acceptance = { status: "NOT_READY", review: null, userConfirmation: null };
      if (entry.kind === "DELIVERY") entry.delivery = entry.acceptance;
    }
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.kind === "TASK" ? "EXECUTION_RETRY" : "AGGREGATE_GATE_RETRY"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    if (entry.acceptanceReport) await writeAcceptanceReport(root, entry, own.definition, at, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, baselineFingerprint: entry.baselineFingerprint };
  }, { now });
}
async function retryWorkItem(options = {}) {
  return retryBlockedWorkItem(options);
}
async function copyPackageContents(source, staging, fs) {
  const entries = await fs.readdir(source, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isSymbolicLink()) fail2("WORK_ITEM_PACKAGE_INVALID", "Work item packages cannot contain symbolic links");
    await fs.cp(path3.join(source, entry.name), path3.join(staging, entry.name), {
      recursive: entry.isDirectory(),
      force: false,
      errorOnExist: true
    });
  }
}
async function reviseWorkItem({
  root,
  definition,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail2("CONFIRMATION_REQUIRED", "Work item baseline revision requires explicit confirmation");
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, definition?.id);
    if (entry.stage !== "BASELINE_FROZEN") fail2("WORK_ITEM_STAGE_INVALID", "Only frozen work items can be revised");
    if (entry.status === "VERIFIED") fail2("WORK_ITEM_REVISION_AFTER_VERIFICATION", "Verified work items cannot be revised");
    if (entry.status === "BLOCKED") {
      fail2("WORK_ITEM_RETRY_REQUIRED", "A BLOCKED work item must be explicitly retried before baseline revision");
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail2("WORK_ITEM_REVISION_CONFLICT", "The expected baseline fingerprint is not current");
    }
    const current = await assertCurrentLineage(root, registry, entry, fs);
    let parent;
    if (entry.parentId) {
      const parentEntry = itemById(registry, entry.parentId);
      parent = (await assertCurrentLineage(root, registry, parentEntry, fs)).definition;
    }
    const normalized = validateWorkItemDefinition(definition, { parent });
    if (normalized.id !== entry.id || normalized.kind !== entry.kind) {
      fail2("WORK_ITEM_REVISION_IDENTITY_CHANGED", "A revision cannot change work item identity or kind");
    }
    if (current.definition.children) {
      const revisedIds = new Set(normalized.children.map(({ id }) => id));
      const removed = current.definition.children.filter(({ id }) => !revisedIds.has(id));
      if (removed.length > 0) fail2("WORK_ITEM_CHILD_REMOVAL_FORBIDDEN", "Baseline revisions may append or refine children but cannot remove them");
    }
    const activeDescendants = registry.workItems.filter((candidate) => candidate.claim && isDescendantOf(registry, candidate, entry.id));
    if (entry.kind === "TASK" && activeDescendants.length > 0) {
      fail2("WORK_ITEM_REVISION_ACTIVE_CLAIM", "A claimed Task cannot be revised");
    }
    for (const candidate of activeDescendants) {
      let directChild = candidate;
      while (directChild.parentId && directChild.parentId !== entry.id) {
        directChild = itemById(registry, directChild.parentId);
      }
      const before = workItemChildContractFingerprint(current.definition, directChild.id);
      const after = workItemChildContractFingerprint(normalized, directChild.id);
      if (before !== after) {
        fail2("WORK_ITEM_REVISION_ACTIVE_CLAIM", "A revision cannot invalidate an actively claimed descendant");
      }
    }
    validateTaskDependencies(normalized, parent);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      ...current.state,
      baselineFingerprint: workItemBaselineFingerprint(normalized),
      contractFingerprint: workItemContractFingerprint(normalized),
      parentContractFingerprint: normalized.parentContractFingerprint,
      baselineRevision: (current.state.baselineRevision ?? 1) + 1,
      revisedAt: at,
      review: {
        schemaVersion: 1,
        status: "APPROVED",
        baselineFingerprint: workItemBaselineFingerprint(normalized),
        reviewedBy: "user",
        reviewedAt: at
      }
    };
    const files = definitionFiles(normalized, state);
    await atomicReplaceDirectory(current.target, async (staging) => {
      await copyPackageContents(current.target, staging, fs);
      for (const [name, contents] of Object.entries(files)) {
        await atomicWriteFile(path3.join(staging, name), contents, { fs });
      }
      if (entry.kind === "TASK") {
        for (const name of ["development-mode.json", "context-manifest.json", "development-handoff.md"]) {
          await fs.rm(path3.join(staging, name), { force: true });
        }
      }
      for (const name of ["acceptance-report.json", "acceptance-report.md"]) {
        await fs.rm(path3.join(staging, name), { force: true });
      }
    }, { fs });
    entry.childIds = normalized.children?.map(({ id }) => id) ?? [];
    entry.baselineFingerprint = state.baselineFingerprint;
    entry.contractFingerprint = state.contractFingerprint;
    entry.parentContractFingerprint = state.parentContractFingerprint;
    entry.developmentReview = true;
    entry.status = entry.kind === "TASK" ? "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" : "FROZEN";
    entry.developmentMode = null;
    entry.gate = { status: "NOT_RUN", evidence: null };
    entry.acceptance = entry.parentId === null ? { status: "NOT_READY", review: null, userConfirmation: null } : null;
    if (entry.kind === "DELIVERY") entry.delivery = entry.acceptance;
    entry.acceptanceReport = null;
    entry.latestEvidence = null;
    entry.latestResult = null;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: entry.id,
      purpose: entry.kind === "TASK" ? "DEVELOPMENT_MODE_SELECTION" : "DECOMPOSITION"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id: entry.id,
      kind: entry.kind,
      baselineRevision: state.baselineRevision,
      baselineFingerprint: state.baselineFingerprint,
      status: entry.status
    };
  }, { now });
}
async function promoteWorkItem({
  root,
  id,
  parentId,
  expectedBaselineFingerprint,
  expectedParentBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail2("CONFIRMATION_REQUIRED", "Work item promotion requires explicit confirmation");
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    const parentEntry = itemById(registry, parentId);
    if (entry.id === parentEntry.id) fail2("WORK_ITEM_PROMOTION_INVALID", "A work item cannot promote under itself");
    if (entry.parentId !== null || !["TASK", "CAPABILITY"].includes(entry.kind)) {
      fail2("WORK_ITEM_PROMOTION_ROOT_REQUIRED", "Only a root Task or root Capability can be promoted");
    }
    const expectedParentKind = entry.kind === "TASK" ? "CAPABILITY" : "DELIVERY";
    if (parentEntry.kind !== expectedParentKind || parentEntry.parentId !== null) {
      fail2("WORK_ITEM_PROMOTION_PARENT_INVALID", `${entry.kind} promotion requires a root ${expectedParentKind} parent`);
    }
    if (entry.stage !== "BASELINE_FROZEN" || !["FROZEN", "WAITING_FOR_DEVELOPMENT_MODE_SELECTION"].includes(entry.status) || entry.gate.status !== "NOT_RUN") {
      fail2("WORK_ITEM_PROMOTION_SOURCE_NOT_FROZEN", "Promotion source must be an unblocked, unverified frozen root");
    }
    if (parentEntry.stage !== "BASELINE_FROZEN" || parentEntry.status !== "FROZEN" || parentEntry.gate.status !== "NOT_RUN") {
      fail2("WORK_ITEM_PROMOTION_PARENT_NOT_FROZEN", "Promotion parent baseline must be frozen before attachment");
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint || parentEntry.baselineFingerprint !== expectedParentBaselineFingerprint) {
      fail2("WORK_ITEM_REVISION_CONFLICT", "Promotion fingerprints are not current");
    }
    const active = registry.workItems.find((candidate) => candidate.claim && isDescendantOf(registry, candidate, entry.id));
    if (active) fail2("WORK_ITEM_PROMOTION_ACTIVE_CLAIM", "A promoted subtree cannot contain an active claim");
    const current = await assertCurrentLineage(root, registry, entry, fs);
    const parentPackage = await assertCurrentLineage(root, registry, parentEntry, fs);
    const normalized = validateWorkItemDefinition({
      ...rawDefinition(current.definition),
      parentId: parentEntry.id
    }, {
      parent: parentPackage.definition,
      allowLegacyDevelopmentPlan: !current.definition.developmentPlan
    });
    validateTaskDependencies(normalized, parentPackage.definition);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      ...current.state,
      baselineFingerprint: workItemBaselineFingerprint(normalized),
      contractFingerprint: workItemContractFingerprint(normalized),
      parentContractFingerprint: normalized.parentContractFingerprint,
      baselineRevision: (current.state.baselineRevision ?? 1) + 1,
      revisedAt: at,
      ...normalized.developmentPlan ? {
        review: {
          schemaVersion: 1,
          status: "APPROVED",
          baselineFingerprint: workItemBaselineFingerprint(normalized),
          reviewedBy: "user",
          reviewedAt: at
        }
      } : {}
    };
    const files = definitionFiles(normalized, state);
    await atomicReplaceDirectory(current.target, async (staging) => {
      await copyPackageContents(current.target, staging, fs);
      for (const [name, contents] of Object.entries(files)) {
        await atomicWriteFile(path3.join(staging, name), contents, { fs });
      }
      if (entry.kind === "TASK") {
        for (const name of ["development-mode.json", "context-manifest.json", "development-handoff.md"]) {
          await fs.rm(path3.join(staging, name), { force: true });
        }
      }
      for (const name of ["acceptance-report.json", "acceptance-report.md"]) {
        await fs.rm(path3.join(staging, name), { force: true });
      }
    }, { fs });
    const previousBaselineFingerprint = entry.baselineFingerprint;
    entry.parentId = parentEntry.id;
    entry.baselineFingerprint = state.baselineFingerprint;
    entry.contractFingerprint = state.contractFingerprint;
    entry.parentContractFingerprint = state.parentContractFingerprint;
    entry.developmentReview = Boolean(normalized.developmentPlan);
    entry.status = entry.kind === "TASK" ? "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" : "FROZEN";
    entry.developmentMode = null;
    entry.gate = { status: "NOT_RUN", evidence: null };
    entry.acceptance = null;
    entry.acceptanceReport = null;
    entry.latestEvidence = null;
    entry.latestResult = null;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    parentEntry.recordRevision += 1;
    parentEntry.updatedAt = at;
    registry.promotionHistory.push({
      schemaVersion: 1,
      childId: entry.id,
      childKind: entry.kind,
      parentId: parentEntry.id,
      parentKind: parentEntry.kind,
      previousBaselineFingerprint,
      promotedBaselineFingerprint: entry.baselineFingerprint,
      parentBaselineFingerprint: parentEntry.baselineFingerprint,
      promotedAt: at
    });
    registry.currentFocus = {
      workItemId: entry.id,
      purpose: entry.kind === "TASK" ? "DEVELOPMENT_MODE_SELECTION" : "DECOMPOSITION"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id: entry.id,
      kind: entry.kind,
      gateLevel: entry.gateLevel,
      parentId: entry.parentId,
      baselineRevision: state.baselineRevision,
      baselineFingerprint: entry.baselineFingerprint,
      status: entry.status
    };
  }, { now });
}
function legacyWorkItemContractFingerprint(definition) {
  const legacyContract = {
    schemaVersion: definition.schemaVersion,
    id: definition.id,
    kind: definition.kind,
    goal: definition.goal,
    scope: [...definition.scope].sort(),
    requirements: [...definition.requirements].sort((left, right) => left.id.localeCompare(right.id)),
    acceptance: [...definition.acceptance].sort((left, right) => left.id.localeCompare(right.id)),
    testCommands: definition.testCommands
  };
  if (definition.children) legacyContract.children = [...definition.children].sort((left, right) => left.id.localeCompare(right.id));
  if (definition.decomposition) legacyContract.decomposition = definition.decomposition;
  if (definition.execution) legacyContract.execution = definition.execution;
  return sha256Bytes(Buffer.from(canonicalJson(legacyContract), "utf8"));
}
function validateLegacyRootTaskRegistry(registry, root) {
  const entry = registry?.workItems?.[0];
  const registryValid = registry && typeof registry === "object" && !Array.isArray(registry) && registry.schemaVersion === 2 && registry.coordinationRoot === path3.resolve(root) && Number.isInteger(registry.revision) && registry.revision >= 0 && Array.isArray(registry.workItems) && registry.workItems.length === 1 && (registry.promotionHistory === void 0 || Array.isArray(registry.promotionHistory) && registry.promotionHistory.length === 0) && registry.currentFocus && typeof registry.currentFocus === "object";
  const entryValid = entry && safeWorkItemId(entry.id) && entry.kind === "TASK" && entry.authorityKind === WORK_ITEM_AUTHORITIES.TASK && entry.parentId === null && Array.isArray(entry.childIds) && entry.childIds.length === 0 && entry.packagePath === itemRelativePath(entry.id) && entry.stage === "BASELINE_FROZEN" && ["FROZEN", "WAITING_FOR_DEVELOPMENT_MODE_SELECTION"].includes(entry.status) && typeof entry.baselineFingerprint === "string" && /^[a-f0-9]{64}$/.test(entry.baselineFingerprint) && typeof entry.contractFingerprint === "string" && /^[a-f0-9]{64}$/.test(entry.contractFingerprint) && entry.parentContractFingerprint === null && entry.gate?.status === "NOT_RUN" && entry.gate.evidence === null && entry.claim === null && entry.latestEvidence === null && (entry.delivery === void 0 || entry.delivery === null) && Number.isInteger(entry.recordRevision) && entry.recordRevision >= 1;
  if (!registryValid || !entryValid) {
    fail2(
      "WORK_ITEM_SCHEMA_MIGRATION_UNSUPPORTED",
      "Schema v2 migration currently supports one inactive frozen root Task with no gate result or claim"
    );
  }
  const waitingForMode = entry.status === "WAITING_FOR_DEVELOPMENT_MODE_SELECTION";
  if (waitingForMode !== (entry.developmentMode === null)) {
    fail2("WORK_ITEM_SCHEMA_MIGRATION_UNSUPPORTED", "Legacy Task development mode state is inconsistent");
  }
  if (entry.developmentMode !== null && !validDevelopmentMode(entry.developmentMode, entry)) {
    fail2("WORK_ITEM_SCHEMA_MIGRATION_UNSUPPORTED", "Legacy Task development mode is invalid");
  }
  return entry;
}
async function upgradeWorkItemRegistry({
  root,
  taskGateLevel,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp(now);
  return withRuntimeDirectoryTransaction(registryPath(root), async () => {
    let registryBytes;
    try {
      registryBytes = await readSafeRegularFile(root, registryPath(root), { fs });
    } catch (error) {
      if (error.code === "ENOENT") fail2("WORK_ITEM_REGISTRY_MISSING", "Work item registry does not exist");
      throw error;
    }
    let legacyRegistry;
    try {
      legacyRegistry = JSON.parse(registryBytes.toString("utf8"));
    } catch {
      fail2("WORK_ITEM_REGISTRY_INVALID", "Work item registry is not valid JSON");
    }
    if (legacyRegistry.schemaVersion === WORK_ITEM_REGISTRY_SCHEMA_VERSION) {
      const current = await readRegistryUnlocked(root, fs);
      return {
        migrated: false,
        idempotent: true,
        fromSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
        toSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
        revision: current.revision
      };
    }
    if (confirmed !== true) {
      fail2("CONFIRMATION_REQUIRED", "Schema v2 migration requires explicit confirmation of the Task gate level");
    }
    if (!WORK_ITEM_GATE_LEVELS.includes(taskGateLevel)) {
      fail2("WORK_ITEM_GATE_LEVEL_INVALID", "Schema v2 migration requires taskGateLevel LIGHT or FULL");
    }
    const legacyEntry = validateLegacyRootTaskRegistry(legacyRegistry, root);
    const target = itemPath(root, legacyEntry.id);
    const targetStat = await fs.lstat(target).catch(() => null);
    if (!targetStat?.isDirectory() || targetStat.isSymbolicLink()) {
      fail2("WORK_ITEM_PACKAGE_INVALID", `${legacyEntry.id} package path is invalid`);
    }
    const legacyDefinition = await readJsonFile(target, "baseline.json", fs, "WORK_ITEM_PACKAGE_INVALID");
    const legacyState = await readJsonFile(target, "state.json", fs, "WORK_ITEM_PACKAGE_INVALID");
    const legacyMetadata = await readJsonFile(target, "work-item.json", fs, "WORK_ITEM_PACKAGE_INVALID");
    const legacyBaselineFingerprint = sha256Bytes(Buffer.from(canonicalJson(legacyDefinition), "utf8"));
    const legacyContractFingerprint = legacyWorkItemContractFingerprint(legacyDefinition);
    const packageValid = legacyDefinition.schemaVersion === 2 && legacyDefinition.id === legacyEntry.id && legacyDefinition.kind === "TASK" && legacyDefinition.authorityKind === WORK_ITEM_AUTHORITIES.TASK && legacyDefinition.parentId === null && legacyDefinition.parentContractFingerprint === null && !Object.hasOwn(legacyDefinition, "gateLevel") && legacyState.schemaVersion === 2 && legacyState.id === legacyEntry.id && legacyState.stage === legacyEntry.stage && legacyState.baselineFingerprint === legacyBaselineFingerprint && legacyState.contractFingerprint === legacyContractFingerprint && legacyState.parentContractFingerprint === null && legacyEntry.baselineFingerprint === legacyBaselineFingerprint && legacyEntry.contractFingerprint === legacyContractFingerprint && legacyMetadata.schemaVersion === 2 && legacyMetadata.id === legacyEntry.id && legacyMetadata.kind === legacyEntry.kind && legacyMetadata.parentId === null;
    if (!packageValid) {
      fail2("WORK_ITEM_PACKAGE_CHANGED", `${legacyEntry.id} legacy package does not match its registry`);
    }
    let developmentMode = null;
    if (legacyEntry.developmentMode !== null) {
      const artifact = await readJsonFile(target, "development-mode.json", fs, "WORK_ITEM_DEVELOPMENT_MODE_INVALID");
      if (canonicalJson(artifact) !== canonicalJson(legacyEntry.developmentMode)) {
        fail2("WORK_ITEM_DEVELOPMENT_MODE_CHANGED", `${legacyEntry.id} development-mode.json changed after confirmation`);
      }
      developmentMode = artifact;
    }
    const migratedDefinition = validateWorkItemDefinition({
      ...rawDefinition(legacyDefinition),
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
      gateLevel: taskGateLevel
    }, { allowLegacyDevelopmentPlan: true });
    const migratedState = {
      ...legacyState,
      schemaVersion: WORK_ITEM_SCHEMA_VERSION,
      baselineFingerprint: workItemBaselineFingerprint(migratedDefinition),
      contractFingerprint: workItemContractFingerprint(migratedDefinition),
      parentContractFingerprint: migratedDefinition.parentContractFingerprint,
      baselineRevision: (legacyState.baselineRevision ?? 1) + 1,
      revisedAt: at,
      schemaMigration: {
        fromSchemaVersion: 2,
        toSchemaVersion: WORK_ITEM_SCHEMA_VERSION,
        migratedAt: at
      }
    };
    if (developmentMode) {
      developmentMode = {
        ...developmentMode,
        baselineFingerprint: migratedState.baselineFingerprint
      };
    }
    const migratedEntry = {
      ...legacyEntry,
      gateLevel: taskGateLevel,
      baselineFingerprint: migratedState.baselineFingerprint,
      contractFingerprint: migratedState.contractFingerprint,
      parentContractFingerprint: migratedState.parentContractFingerprint,
      delivery: null,
      acceptance: { status: "NOT_READY", review: null, userConfirmation: null },
      acceptanceReport: null,
      developmentMode,
      latestResult: null,
      recordRevision: legacyEntry.recordRevision + 1,
      updatedAt: at
    };
    const migratedRegistry = {
      ...legacyRegistry,
      schemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
      revision: legacyRegistry.revision + 1,
      workItems: [migratedEntry],
      promotionHistory: legacyRegistry.promotionHistory ?? [],
      migrationHistory: [
        ...legacyRegistry.migrationHistory ?? [],
        {
          schemaVersion: 1,
          fromSchemaVersion: 2,
          toSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
          workItemId: migratedEntry.id,
          taskGateLevel,
          previousBaselineFingerprint: legacyBaselineFingerprint,
          migratedBaselineFingerprint: migratedState.baselineFingerprint,
          previousRegistryFingerprint: sha256Bytes(registryBytes),
          migratedAt: at
        }
      ],
      updatedAt: at
    };
    validateRegistry(migratedRegistry, root);
    await atomicReplaceDirectory(target, async (staging) => {
      await copyPackageContents(target, staging, fs);
      for (const [name, contents] of Object.entries(definitionFiles(migratedDefinition, migratedState))) {
        await atomicWriteFile(path3.join(staging, name), contents, { fs });
      }
      if (developmentMode) {
        await atomicWriteFile(path3.join(staging, "development-mode.json"), json(developmentMode), { fs });
      }
      for (const name of [
        "context-manifest.json",
        "development-handoff.md",
        "acceptance-report.json",
        "acceptance-report.md"
      ]) {
        await fs.rm(path3.join(staging, name), { force: true });
      }
    }, { fs });
    await writeRegistryUnlocked(root, migratedRegistry, fs);
    return {
      migrated: true,
      idempotent: false,
      fromSchemaVersion: 2,
      toSchemaVersion: WORK_ITEM_REGISTRY_SCHEMA_VERSION,
      taskId: migratedEntry.id,
      taskGateLevel,
      previousBaselineFingerprint: legacyBaselineFingerprint,
      baselineFingerprint: migratedState.baselineFingerprint,
      revision: migratedRegistry.revision
    };
  }, { fs, now });
}
async function refreshWorkItemProjections({
  root,
  explicitDogfood = false,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  await ensureRuntimeRoot(root, fs);
  return withRuntimeDirectoryTransaction(registryPath(root), async () => {
    const registry = await readRegistryUnlocked(root, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return {
      revision: registry.revision,
      workspaceOverview: path3.posix.join(GOVERNANCE_DIRECTORY, "workspace-overview.md"),
      workItems: registry.workItems.map(({ id, acceptanceReport, developmentReview }) => ({
        id,
        acceptanceReport: acceptanceReport?.markdownPath ?? null,
        humanArtifacts: developmentReview ? itemHumanArtifacts(id, acceptanceReport) : null
      }))
    };
  }, { fs });
}
async function selectDevelopmentMode({
  root,
  id,
  mode,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) {
    fail2("CONFIRMATION_REQUIRED", "Development mode selection requires explicit user confirmation");
  }
  if (!DEVELOPMENT_MODES.includes(mode)) {
    fail2("WORK_ITEM_DEVELOPMENT_MODE_INVALID", "Development mode must be active or manual");
  }
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== "TASK" || entry.stage !== "BASELINE_FROZEN") {
      fail2("WORK_ITEM_TASK_REQUIRED", "Development mode can only be selected for a frozen Task");
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail2("WORK_ITEM_REVISION_CONFLICT", "The development mode confirmation is not bound to the current baseline");
    }
    if (entry.claim || !["WAITING_FOR_DEVELOPMENT_MODE_SELECTION", "FROZEN"].includes(entry.status)) {
      fail2("WORK_ITEM_DEVELOPMENT_MODE_LOCKED", "Development mode cannot change after Task dispatch begins");
    }
    if (entry.developmentMode?.mode === mode) {
      return {
        created: false,
        idempotent: true,
        id,
        status: entry.status,
        developmentMode: entry.developmentMode
      };
    }
    if (entry.developmentMode !== null) {
      fail2("WORK_ITEM_DEVELOPMENT_MODE_LOCKED", "Development mode is fixed for the current Task baseline");
    }
    const record = {
      schemaVersion: 1,
      taskId: id,
      baselineFingerprint: entry.baselineFingerprint,
      mode,
      confirmedBy: "user",
      confirmedAt: at
    };
    const target = itemPath(root, id);
    await atomicWriteFile(path3.join(target, "development-mode.json"), json(record), { fs });
    entry.developmentMode = record;
    entry.status = "FROZEN";
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: mode === "active" ? "ACTIVE_DISPATCH" : "MANUAL_HANDOFF"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    try {
      await writeRegistryUnlocked(root, registry, fs);
    } catch (error) {
      await fs.rm(path3.join(target, "development-mode.json"), { force: true });
      throw error;
    }
    return {
      created: true,
      idempotent: false,
      id,
      status: entry.status,
      developmentMode: record
    };
  }, { now });
}
function isDescendantOf(registry, entry, ancestorId) {
  let current = entry;
  const visited = /* @__PURE__ */ new Set();
  while (current) {
    if (current.id === ancestorId) return true;
    if (!current.parentId || visited.has(current.id)) return false;
    visited.add(current.id);
    current = registry.workItems.find(({ id }) => id === current.parentId);
  }
  return false;
}
async function taskDefinition(root, entry, fs) {
  return (await readPackageDefinition(root, entry, fs)).definition;
}
async function taskReady(root, registry, entry, fs) {
  if (entry.kind !== "TASK" || entry.stage !== "BASELINE_FROZEN" || entry.status !== "FROZEN" || entry.claim) return false;
  await assertCurrentLineage(root, registry, entry, fs);
  const definition = await taskDefinition(root, entry, fs);
  let capabilitiesReady = true;
  if (entry.parentId !== null) {
    const capabilityEntry = itemById(registry, entry.parentId);
    const capability = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
    capabilitiesReady = capability.decomposition.dependsOn.every((id) => registry.workItems.find((candidate) => candidate.id === id)?.status === "VERIFIED");
  }
  if (!capabilitiesReady) return false;
  const dependenciesReady = definition.execution.dependsOn.every((id) => registry.workItems.find((candidate) => candidate.id === id)?.status === "VERIFIED");
  if (!dependenciesReady) return false;
  for (const claimed of registry.workItems.filter((candidate) => candidate.claim)) {
    const claimedDefinition = await taskDefinition(root, claimed, fs);
    if (scopePatternsOverlap(definition.scope, claimedDefinition.scope)) return false;
  }
  return true;
}
async function listReadyTasks({ root, workItemId, fs = fsPromises2 } = {}) {
  const registry = await readRegistryUnlocked(root, fs);
  itemById(registry, workItemId);
  const ready = [];
  for (const entry of sortedItems(registry.workItems)) {
    if (isDescendantOf(registry, entry, workItemId) && await taskReady(root, registry, entry, fs)) ready.push(entry.id);
  }
  return ready;
}
function safeOperationId(value, field) {
  if (typeof value !== "string" || !/^[a-z0-9][a-z0-9._-]*$/.test(value)) {
    fail2("WORK_ITEM_OPERATION_INVALID", `${field} must be a safe lowercase identifier`);
  }
  return value;
}
async function claimTask({ root, id, owner, operationId, explicitDogfood = false, now, fs = fsPromises2 } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind === "TASK" && entry.developmentMode === null) {
      fail2("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", `${id} requires an explicitly confirmed development mode`);
    }
    if (!await taskReady(root, registry, entry, fs)) fail2("WORK_ITEM_NOT_READY", `${id} is not ready for dispatch`);
    entry.claim = {
      owner: safeOperationId(owner, "owner"),
      operationId: safeOperationId(operationId, "operationId"),
      claimedAt: at
    };
    entry.status = "CLAIMED";
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = { workItemId: id, purpose: "EXECUTION" };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, claim: entry.claim };
  }, { now });
}
function evidenceRecord(value) {
  const valid = value && typeof value === "object" && !Array.isArray(value) && typeof value.path === "string" && value.path.length > 0 && !path3.posix.isAbsolute(value.path.replaceAll("\\", "/")) && !value.path.replaceAll("\\", "/").split("/").includes("..") && typeof value.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.sha256);
  if (!valid) fail2("WORK_ITEM_EVIDENCE_INVALID", "Evidence must contain a safe relative path and sha256");
  return { path: value.path.replaceAll("\\", "/"), sha256: value.sha256 };
}
async function readEvidenceArtifact(root, evidence, fs, {
  missingCode = "WORK_ITEM_EVIDENCE_MISSING",
  changedCode = "WORK_ITEM_EVIDENCE_CHANGED",
  invalidCode = "WORK_ITEM_EVIDENCE_INVALID"
} = {}) {
  const reference = evidenceRecord(evidence);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, reference.path, { fs });
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail2(missingCode, `Unable to read evidence: ${reference.path}`);
  }
  if (sha256Bytes(bytes) !== reference.sha256) {
    fail2(changedCode, `Evidence hash does not match: ${reference.path}`);
  }
  let artifact;
  try {
    artifact = JSON.parse(bytes.toString("utf8"));
  } catch {
    fail2(invalidCode, "Evidence must be valid JSON");
  }
  return { reference, artifact };
}
async function optionalTaskResultArtifact(root, evidence, expected, fs, strict = false) {
  const reference = evidenceRecord(evidence);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, reference.path, { fs });
  } catch {
    if (strict) fail2("WORK_ITEM_RESULT_EVIDENCE_MISSING", `Task result evidence is unavailable: ${reference.path}`);
    return { reference, artifact: null };
  }
  if (sha256Bytes(bytes) !== reference.sha256) {
    fail2("WORK_ITEM_RESULT_EVIDENCE_CHANGED", `Task result evidence hash does not match: ${reference.path}`);
  }
  let artifact;
  try {
    artifact = JSON.parse(bytes.toString("utf8"));
  } catch {
    fail2("WORK_ITEM_RESULT_EVIDENCE_INVALID", "Task result evidence must be valid JSON");
  }
  if (!validTaskResultArtifact(artifact, expected)) {
    fail2("WORK_ITEM_RESULT_EVIDENCE_INVALID", "Task result evidence does not match the active operation");
  }
  return { reference, artifact };
}
function validTaskResultArtifact(value, { id, operationId, status }) {
  return value && typeof value === "object" && !Array.isArray(value) && value.schemaVersion === 1 && value.kind === "TASK_RESULT" && value.taskId === id && value.operationId === operationId && value.status === status && nonEmptyString(value.summary) && Array.isArray(value.changedFiles) && value.changedFiles.every(nonEmptyString) && Array.isArray(value.tests) && value.tests.every((test) => test && typeof test === "object" && Array.isArray(test.argv) && test.argv.every(nonEmptyString) && Number.isInteger(test.exitCode)) && Array.isArray(value.blockers);
}
function validGateArtifact(value, entry, definition) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.schemaVersion !== 1 || value.kind !== "WORK_ITEM_GATE" || value.workItemId !== entry.id || value.baselineFingerprint !== entry.baselineFingerprint || !["PASS", "FAIL"].includes(value.verdict) || !nonEmptyString(value.summary) || !value.scope || typeof value.scope !== "object" || Array.isArray(value.scope) || !Array.isArray(value.scope.changedFiles) || !value.scope.changedFiles.every(nonEmptyString) || !Array.isArray(value.scope.outOfScopeFiles) || !value.scope.outOfScopeFiles.every(nonEmptyString) || !Array.isArray(value.acceptance) || !Array.isArray(value.tests) || !value.findings || typeof value.findings !== "object" || Array.isArray(value.findings) || !Array.isArray(value.findings.p0) || !Array.isArray(value.findings.p1) || !Array.isArray(value.findings.p2)) return false;
  const acceptanceById = new Map(value.acceptance.map((result) => [result?.id, result]));
  const testsByArgv = new Map(value.tests.map((result) => [canonicalJson(result?.argv), result]));
  const acceptanceComplete = definition.acceptance.every(({ id }) => {
    const result = acceptanceById.get(id);
    return result && ["PASS", "FAIL"].includes(result.status) && nonEmptyString(result.evidence);
  });
  const testsComplete = definition.testCommands.every((argv) => {
    const result = testsByArgv.get(canonicalJson(argv));
    return result && Number.isInteger(result.exitCode) && nonEmptyString(result.summary) && (result.testsRun === void 0 || Number.isInteger(result.testsRun) && result.testsRun >= 0);
  });
  if (!acceptanceComplete || !testsComplete) return false;
  if (definition.kind === "TASK" && definition.developmentPlan) {
    const plannedFiles = new Set(definition.developmentPlan.fileChanges.map(({ path: plannedPath }) => plannedPath));
    if (value.scope.changedFiles.some((changedFile) => !plannedFiles.has(changedFile.replaceAll("\\", "/")))) {
      return false;
    }
  }
  if (value.verdict === "PASS") {
    return value.scope.outOfScopeFiles.length === 0 && definition.acceptance.every(({ id }) => acceptanceById.get(id).status === "PASS") && definition.testCommands.every((argv) => testsByArgv.get(canonicalJson(argv)).exitCode === 0) && value.findings.p0.length === 0 && value.findings.p1.length === 0;
  }
  return true;
}
function reportStatus(entry) {
  const acceptance = entry.acceptance ?? (entry.parentId === null ? entry.delivery : null);
  if (acceptance && acceptance.status !== "NOT_READY") return acceptance.status;
  if (entry.status === "IMPLEMENTED") return "WAITING_FOR_GATE";
  if (entry.status === "BLOCKED") return "BLOCKED";
  if (entry.status === "VERIFIED") return "VERIFIED";
  return "NOT_READY";
}
function reportStatusText(status) {
  return {
    NOT_READY: "\u5C1A\u672A\u5C31\u7EEA",
    WAITING_FOR_GATE: "\u7B49\u5F85\u95E8\u7981\u9A8C\u6536",
    BLOCKED: "\u5DF2\u963B\u65AD",
    VERIFIED: "\u95E8\u7981\u5DF2\u901A\u8FC7",
    WAITING_FOR_INDEPENDENT_REVIEW: "\u7B49\u5F85\u72EC\u7ACB\u9A8C\u6536",
    WAITING_FOR_USER_CONFIRMATION: "\u7B49\u5F85\u7528\u6237\u786E\u8BA4",
    COMPLETED: "\u5DF2\u5B8C\u6210"
  }[status] ?? status;
}
function gateStatusText(status) {
  return { NOT_RUN: "\u672A\u8FD0\u884C", PASS: "\u901A\u8FC7", FAIL: "\u672A\u901A\u8FC7" }[status] ?? status;
}
function renderAcceptanceReport(report) {
  const gateArtifact = report.gate.artifact;
  const lines = [
    `# \u9A8C\u6536\u62A5\u544A\uFF1A${report.workItem.title}`,
    "",
    `- \u5DE5\u4F5C\u9879\uFF1A${report.workItem.id}`,
    `- \u7C7B\u578B\uFF1A${report.workItem.kind}`,
    `- \u95E8\u7981\u7B49\u7EA7\uFF1A${report.workItem.gateLevel}`,
    `- \u57FA\u7EBF\u6307\u7EB9\uFF1A${report.workItem.baselineFingerprint}`,
    `- \u6700\u7EC8\u72B6\u6001\uFF1A${reportStatusText(report.status)}`,
    `- \u95E8\u7981\u7ED3\u8BBA\uFF1A${gateStatusText(report.gate.status)}`,
    `- \u751F\u6210\u65F6\u95F4\uFF1A${report.generatedAt}`,
    "",
    "## \u9A8C\u6536\u9879",
    "",
    "| \u7F16\u53F7 | \u9884\u671F\u7ED3\u679C | \u7ED3\u8BBA | \u8BC1\u636E |",
    "| --- | --- | --- | --- |"
  ];
  const results = new Map((gateArtifact?.acceptance ?? []).map((item) => [item.id, item]));
  for (const item of report.criteria) {
    const result = results.get(item.id);
    lines.push(`| ${item.id} | ${item.expectedResult} | ${result ? gateStatusText(result.status) : "\u5F85\u9A8C\u6536"} | ${result?.evidence ?? "\u65E0"} |`);
  }
  lines.push("", "## \u51BB\u7ED3\u5F00\u53D1\u65B9\u6848", "");
  if (report.developmentPlan?.interfaces) {
    lines.push(`- \u5F00\u53D1\u76EE\u7684\uFF1A${report.developmentPlan.purpose}`);
    lines.push(`- \u63A5\u53E3\u5951\u7EA6\uFF1A${report.developmentPlan.interfaces.map(({ action, kind, name }) => `${action} ${kind} ${name}`).join("\uFF1B") || "\u65E0\u63A5\u53E3\u6539\u52A8"}`);
  } else if (report.developmentPlan?.childPlans) {
    lines.push(`- \u534F\u8C03\u76EE\u7684\uFF1A${report.developmentPlan.purpose}`);
    lines.push(`- \u5B50\u7EA7\u5185\u5BB9\uFF1A${report.developmentPlan.childPlans.map(({ id, purpose }) => `${id}\uFF1A${purpose}`).join("\uFF1B")}`);
  } else {
    lines.push("- \u65E7\u7248 baseline \u672A\u8BB0\u5F55\u7ED3\u6784\u5316\u5F00\u53D1\u65B9\u6848\u3002");
  }
  lines.push("", "## \u6D4B\u8BD5\u7ED3\u679C", "");
  const tests = gateArtifact?.tests ?? report.development?.artifact?.tests ?? [];
  if (tests.length === 0) lines.push("- \u5C1A\u65E0\u6D4B\u8BD5\u8BC1\u636E\u3002");
  for (const result of tests) {
    lines.push(`- \`${JSON.stringify(result.argv)}\`\uFF1A\u9000\u51FA\u7801 ${result.exitCode}\uFF1B${result.summary ?? `Tests run: ${result.testsRun ?? "\u672A\u8BB0\u5F55"}`}`);
  }
  lines.push("", "## \u53D8\u66F4\u8303\u56F4", "");
  const scope = gateArtifact?.scope;
  if (report.developmentPlan?.fileChanges) {
    const planned = report.developmentPlan.fileChanges.map(({ path: plannedPath }) => plannedPath);
    const actual = scope?.changedFiles ?? report.development?.artifact?.changedFiles ?? [];
    const actualSet = new Set(actual.map((changedFile) => changedFile.replaceAll("\\", "/")));
    lines.push(`- \u51BB\u7ED3\u8BA1\u5212\u6587\u4EF6\uFF1A${planned.join("\u3001") || "\u65E0"}`);
    lines.push(`- \u8BA1\u5212\u5916\u6587\u4EF6\uFF1A${actual.filter((changedFile) => !planned.includes(changedFile.replaceAll("\\", "/"))).join("\u3001") || "\u65E0"}`);
    lines.push(`- \u8BA1\u5212\u4E2D\u5C1A\u672A\u89C2\u5BDF\u5230\u7684\u6587\u4EF6\uFF1A${planned.filter((plannedPath) => !actualSet.has(plannedPath)).join("\u3001") || "\u65E0"}`);
  } else if (report.developmentPlan?.childPlans) {
    lines.push(`- \u51BB\u7ED3\u5B50\u7EA7\u8BA1\u5212\uFF1A${report.developmentPlan.childPlans.map(({ id }) => id).join("\u3001")}`);
    lines.push(`- \u51BB\u7ED3\u5171\u4EAB\u5951\u7EA6\uFF1A${report.developmentPlan.sharedContracts.map(({ name }) => name).join("\u3001") || "\u65E0"}`);
  }
  lines.push(`- \u5DF2\u8BB0\u5F55\u53D8\u66F4\uFF1A${scope?.changedFiles?.join("\u3001") || report.development?.artifact?.changedFiles?.join("\u3001") || "\u65E0"}`);
  lines.push(`- \u8303\u56F4\u5916\u53D8\u66F4\uFF1A${scope?.outOfScopeFiles?.join("\u3001") || "\u65E0"}`);
  lines.push("", "## \u95EE\u9898\u4E0E\u5EFA\u8BAE", "");
  const findings = gateArtifact?.findings;
  lines.push(`- P0\uFF1A${findings?.p0?.length ?? 0}`);
  lines.push(`- P1\uFF1A${findings?.p1?.length ?? 0}`);
  lines.push(`- P2\uFF1A${findings?.p2?.length ?? 0}`);
  lines.push("", "## \u72EC\u7ACB\u9A8C\u6536", "");
  lines.push(report.review ? `- ${report.review.artifact.reviewer}\uFF1A${report.review.artifact.verdict}` : "- \u5C1A\u672A\u5B8C\u6210\u3002");
  lines.push("", "## \u7528\u6237\u786E\u8BA4", "");
  lines.push(report.userConfirmation ? `- ${report.userConfirmation.artifact.confirmedBy}\uFF1A\u5DF2\u786E\u8BA4` : "- \u5C1A\u672A\u786E\u8BA4\u3002");
  lines.push("");
  return lines.join("\n");
}
async function writeAcceptanceReport(root, entry, definition, at, fs) {
  const acceptance = entry.acceptance ?? (entry.parentId === null ? entry.delivery : null);
  const status = reportStatus(entry);
  const report = {
    schemaVersion: 1,
    workItem: {
      id: entry.id,
      title: definition.title,
      kind: entry.kind,
      gateLevel: entry.gateLevel,
      baselineFingerprint: entry.baselineFingerprint,
      parentId: entry.parentId
    },
    status,
    development: entry.latestResult,
    gate: entry.gate,
    criteria: definition.acceptance,
    developmentPlan: definition.developmentPlan ?? null,
    review: acceptance?.review ?? null,
    userConfirmation: acceptance?.userConfirmation ?? null,
    generatedAt: at
  };
  const directory = itemPath(root, entry.id);
  await atomicWriteFile(path3.join(directory, "acceptance-report.json"), json(report), { fs });
  await atomicWriteFile(path3.join(directory, "acceptance-report.md"), renderAcceptanceReport(report), { fs });
  entry.acceptanceReport = {
    schemaVersion: 1,
    status,
    jsonPath: path3.posix.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, entry.id, "acceptance-report.json"),
    markdownPath: path3.posix.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, entry.id, "acceptance-report.md"),
    generatedAt: at
  };
  return report;
}
async function verifiedDeliveryEvidence(root, evidence, action, fs) {
  const reference = evidenceRecord(evidence);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, reference.path, { fs });
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail2("WORK_ITEM_DELIVERY_EVIDENCE_MISSING", `Unable to read delivery evidence: ${reference.path}`);
  }
  if (sha256Bytes(bytes) !== reference.sha256) {
    fail2("WORK_ITEM_DELIVERY_EVIDENCE_CHANGED", `Delivery evidence hash does not match: ${reference.path}`);
  }
  let artifact;
  try {
    artifact = JSON.parse(bytes.toString("utf8"));
  } catch {
    fail2("WORK_ITEM_DELIVERY_EVIDENCE_INVALID", "Delivery evidence must be valid JSON");
  }
  if (!validDeliveryArtifact(action, artifact)) {
    fail2("WORK_ITEM_DELIVERY_EVIDENCE_INVALID", `Delivery evidence does not prove ${action}`);
  }
  return { reference, artifact };
}
async function recordTaskResult({
  root,
  id,
  operationId,
  status,
  evidence,
  strictEvidence = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!["IMPLEMENTED", "BLOCKED"].includes(status)) fail2("WORK_ITEM_RESULT_INVALID", "Task result must be IMPLEMENTED or BLOCKED");
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== "TASK" || entry.status !== "CLAIMED" || entry.claim?.operationId !== operationId) {
      fail2("WORK_ITEM_OPERATION_INVALID", `${id} does not have the supplied active operation`);
    }
    const own = await assertCurrentLineage(root, registry, entry, fs);
    const verifiedEvidence = await optionalTaskResultArtifact(
      root,
      evidence,
      { id, operationId, status },
      fs,
      strictEvidence
    );
    entry.status = status;
    entry.claim = null;
    entry.latestEvidence = verifiedEvidence.reference;
    entry.latestResult = {
      evidence: verifiedEvidence.reference,
      artifact: verifiedEvidence.artifact,
      recordedAt: at
    };
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.revision += 1;
    registry.updatedAt = at;
    await writeAcceptanceReport(root, entry, own.definition, at, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status, acceptanceReport: entry.acceptanceReport };
  }, { now });
}
function allChildrenVerified(registry, entry, definition) {
  const actual = new Map(registry.workItems.filter(({ parentId }) => parentId === entry.id).map((item) => [item.id, item]));
  return definition.children.length > 0 && definition.children.every(({ id }) => actual.get(id)?.status === "VERIFIED");
}
async function recordWorkItemGate({
  root,
  id,
  status,
  evidence,
  gateArtifact = null,
  strictEvidence = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!["PASS", "FAIL"].includes(status)) fail2("WORK_ITEM_GATE_INVALID", "Gate status must be PASS or FAIL");
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    const taskPackage = await assertCurrentLineage(root, registry, entry, fs);
    let verifiedGate = null;
    if (strictEvidence) {
      verifiedGate = await readEvidenceArtifact(root, evidence, fs, {
        missingCode: "WORK_ITEM_GATE_EVIDENCE_MISSING",
        changedCode: "WORK_ITEM_GATE_EVIDENCE_CHANGED",
        invalidCode: "WORK_ITEM_GATE_EVIDENCE_INVALID"
      });
      if (!validGateArtifact(verifiedGate.artifact, entry, taskPackage.definition) || verifiedGate.artifact.verdict !== status || gateArtifact && canonicalJson(gateArtifact) !== canonicalJson(verifiedGate.artifact)) {
        fail2("WORK_ITEM_GATE_EVIDENCE_INVALID", "Gate evidence does not prove the requested result");
      }
    }
    if (entry.status === "BLOCKED") {
      fail2("WORK_ITEM_RETRY_REQUIRED", `${id} must be explicitly retried before its gate can run again`);
    }
    if (entry.status === "VERIFIED") {
      fail2("WORK_ITEM_GATE_ALREADY_PASSED", `${id} gate has already passed`);
    }
    if (status === "PASS") {
      if (entry.kind === "TASK" && entry.status !== "IMPLEMENTED") {
        fail2("WORK_ITEM_IMPLEMENTATION_INCOMPLETE", `${id} must be implemented before its gate can pass`);
      }
      if (entry.kind !== "TASK") {
        if (taskPackage.definition.decomposition.status !== "SEALED") {
          fail2("WORK_ITEM_DECOMPOSITION_OPEN", `${id} decomposition must be SEALED before its aggregate gate can pass`);
        }
        if (!allChildrenVerified(registry, entry, taskPackage.definition)) {
          fail2("WORK_ITEM_CHILDREN_INCOMPLETE", `${id} children must all be verified before its aggregate gate can pass`);
        }
      }
    }
    entry.gate = {
      status,
      evidence: verifiedGate?.reference ?? evidenceRecord(evidence),
      artifact: verifiedGate?.artifact ?? gateArtifact
    };
    entry.status = status === "PASS" ? "VERIFIED" : "BLOCKED";
    if (entry.parentId === null) {
      entry.acceptance = status === "PASS" ? { status: "WAITING_FOR_INDEPENDENT_REVIEW", review: null, userConfirmation: null } : { status: "NOT_READY", review: null, userConfirmation: null };
    }
    if (entry.kind === "DELIVERY") {
      entry.delivery = entry.acceptance;
    }
    entry.latestEvidence = entry.gate.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: status === "PASS" && entry.parentId === null ? "INDEPENDENT_REVIEW" : status === "PASS" ? "AGGREGATION" : "BLOCKER"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeAcceptanceReport(root, entry, taskPackage.definition, at, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id,
      status: entry.status,
      gate: entry.gate,
      acceptance: entry.acceptance,
      acceptanceReport: entry.acceptanceReport
    };
  }, { now });
}
async function acceptWorkItem({
  root,
  id,
  evidence,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  const registry = await readRegistryUnlocked(root, fs);
  const entry = itemById(registry, id);
  const own = await assertCurrentLineage(root, registry, entry, fs);
  const verified = await readEvidenceArtifact(root, evidence, fs, {
    missingCode: "WORK_ITEM_GATE_EVIDENCE_MISSING",
    changedCode: "WORK_ITEM_GATE_EVIDENCE_CHANGED",
    invalidCode: "WORK_ITEM_GATE_EVIDENCE_INVALID"
  });
  if (!validGateArtifact(verified.artifact, entry, own.definition)) {
    fail2("WORK_ITEM_GATE_EVIDENCE_INVALID", "Gate evidence is incomplete or contradicts the requested verdict");
  }
  return recordWorkItemGate({
    root,
    id,
    status: verified.artifact.verdict,
    evidence: verified.reference,
    gateArtifact: verified.artifact,
    strictEvidence: true,
    explicitDogfood,
    now,
    fs
  });
}
async function recordRootAcceptance({
  root,
  id,
  action,
  evidence,
  deliveryOnly = false,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!["INDEPENDENT_REVIEW_PASS", "HUMAN_REVIEW_ACCEPTED", "USER_CONFIRMED"].includes(action)) {
    fail2("WORK_ITEM_DELIVERY_ACTION_INVALID", "Delivery action is invalid");
  }
  const at = timestamp(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.parentId !== null || entry.status !== "VERIFIED" || deliveryOnly && entry.kind !== "DELIVERY") {
      fail2(
        deliveryOnly ? "WORK_ITEM_DELIVERY_INVALID" : "WORK_ITEM_ACCEPTANCE_INVALID",
        "Only a verified root work item can advance final acceptance"
      );
    }
    const own = await assertCurrentLineage(root, registry, entry, fs);
    entry.acceptance ??= entry.delivery ?? {
      status: "WAITING_FOR_INDEPENDENT_REVIEW",
      review: null,
      userConfirmation: null
    };
    if (action === "USER_CONFIRMED") {
      if (entry.acceptance.status !== "WAITING_FOR_USER_CONFIRMATION") {
        fail2(
          deliveryOnly ? "WORK_ITEM_DELIVERY_STAGE_INVALID" : "WORK_ITEM_ACCEPTANCE_STAGE_INVALID",
          "User confirmation requires a passed independent or accepted human review"
        );
      }
      const verifiedEvidence = await verifiedDeliveryEvidence(root, evidence, action, fs);
      const reviewEvidence = entry.acceptance.review.evidence;
      if (reviewEvidence.path === verifiedEvidence.reference.path || reviewEvidence.sha256 === verifiedEvidence.reference.sha256) {
        fail2(
          deliveryOnly ? "WORK_ITEM_DELIVERY_EVIDENCE_REUSED" : "WORK_ITEM_ACCEPTANCE_EVIDENCE_REUSED",
          "User confirmation evidence must be distinct from review evidence"
        );
      }
      entry.acceptance = {
        ...entry.acceptance,
        status: "COMPLETED",
        userConfirmation: {
          action,
          evidence: verifiedEvidence.reference,
          artifact: verifiedEvidence.artifact,
          recordedAt: at
        }
      };
    } else {
      if (entry.acceptance.status !== "WAITING_FOR_INDEPENDENT_REVIEW") {
        fail2(
          deliveryOnly ? "WORK_ITEM_DELIVERY_STAGE_INVALID" : "WORK_ITEM_ACCEPTANCE_STAGE_INVALID",
          "Work item is not waiting for independent review"
        );
      }
      const verifiedEvidence = await verifiedDeliveryEvidence(root, evidence, action, fs);
      entry.acceptance = {
        ...entry.acceptance,
        status: "WAITING_FOR_USER_CONFIRMATION",
        review: {
          action,
          evidence: verifiedEvidence.reference,
          artifact: verifiedEvidence.artifact,
          recordedAt: at
        }
      };
    }
    if (entry.kind === "DELIVERY") entry.delivery = entry.acceptance;
    entry.latestEvidence = action === "USER_CONFIRMED" ? entry.acceptance.userConfirmation.evidence : entry.acceptance.review.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.acceptance.status === "COMPLETED" ? "ACCEPTANCE_COMPLETE" : "USER_CONFIRMATION"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeAcceptanceReport(root, entry, own.definition, at, fs);
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id,
      action,
      acceptance: entry.acceptance,
      delivery: entry.kind === "DELIVERY" ? entry.delivery : null,
      acceptanceReport: entry.acceptanceReport
    };
  }, { now });
}
async function recordAcceptance(options = {}) {
  return recordRootAcceptance(options);
}
async function recordDelivery(options = {}) {
  return recordRootAcceptance({ ...options, deliveryOnly: true });
}
function parentContractSnapshot(parent, childId) {
  const child = parent.children.find(({ id }) => id === childId);
  return {
    id: parent.id,
    kind: parent.kind,
    contractFingerprint: workItemChildContractFingerprint(parent, childId),
    goal: parent.goal,
    scope: parent.scope,
    childContract: child,
    developmentPlan: parent.developmentPlan ?? null
  };
}
function renderTaskHandoff(context) {
  const resultTemplate = {
    schemaVersion: 1,
    kind: "TASK_RESULT",
    taskId: context.task.id,
    operationId: context.operation?.operationId ?? "<claim-required>",
    status: "IMPLEMENTED|BLOCKED",
    summary: "<development facts>",
    changedFiles: [],
    tests: [{ argv: ["<exact frozen argv>"], exitCode: 0, testsRun: 0 }],
    blockers: []
  };
  return [
    "\u8BF7\u5728\u4E00\u4E2A\u5168\u65B0\u7684\u5F00\u53D1\u4F1A\u8BDD\u4E2D\u5B9E\u73B0\u4EE5\u4E0B\u5DF2\u51BB\u7ED3 Task\u3002",
    "",
    `Task\uFF1A${context.task.id}`,
    `Baseline fingerprint\uFF1A${context.task.baselineFingerprint}`,
    `Gate level\uFF1A${context.gateLevel}`,
    `Development mode\uFF1A${context.developmentMode}`,
    `Operation ID\uFF1A${context.operation?.operationId ?? "\u5C1A\u672A\u8BA4\u9886\uFF1B\u4E0D\u5F97\u5F00\u59CB\u5F00\u53D1"}`,
    "",
    "\u4EE5\u4E0B\u51BB\u7ED3\u4E0A\u4E0B\u6587\u662F\u5B8C\u6574\u6743\u5A01\u3002\u4E0D\u8981\u91CD\u65B0\u5206\u6790\u539F\u59CB\u9700\u6C42\u3001\u6539\u53D8\u9A8C\u6536\u6807\u51C6\u6216\u7EE7\u627F\u5176\u4ED6\u4F1A\u8BDD\u7684\u9690\u542B\u5047\u8BBE\u3002",
    "",
    "\u6267\u884C\u89C4\u5219\uFF1A",
    "- \u53EA\u5B9E\u73B0\u8FD9\u4E2A\u51BB\u7ED3\u7684\u53F6\u5B50 Task\uFF0C\u5E76\u4E14\u53EA\u5199\u5165 Scope \u4E2D\u7684\u8DEF\u5F84\u3002",
    "- \u4E0D\u4FEE\u6539 baseline\u3001registry\u3001\u8FDB\u5EA6\u6295\u5F71\u3001`.git/**` \u6216\u5916\u90E8\u72B6\u6001\u3002",
    "- \u8FD0\u884C\u5217\u51FA\u7684\u6D4B\u8BD5\u547D\u4EE4\uFF0C\u53EA\u62A5\u544A\u771F\u5B9E\u5B58\u5728\u7684\u8BC1\u636E\u3002",
    "- \u4E0D\u63D0\u4EA4\u3001\u63A8\u9001\u3001\u53D1\u5E03\uFF0C\u4E5F\u4E0D\u5F97\u81EA\u884C\u62A5\u544A PASS\u3002",
    "- \u6700\u7EC8\u53EA\u8FD4\u56DE IMPLEMENTED \u6216 BLOCKED\uFF0C\u5E76\u643A\u5E26\u5F53\u524D Operation ID\u3001\u53D8\u66F4\u6587\u4EF6\u548C\u6D4B\u8BD5\u4E8B\u5B9E\u3002",
    "- \u5BBF\u4E3B\u5FC5\u987B\u7528 task-result \u56DE\u6536\u7ED3\u679C\uFF1B\u8FD4\u56DE\u5F00\u53D1\u7ED3\u679C\u540E\u5FC5\u987B\u7EE7\u7EED\u9A8C\u6536\uFF0CIMPLEMENTED \u4E0D\u662F\u5B8C\u6210\u72B6\u6001\u3002",
    "- \u95E8\u7981\u901A\u8FC7\u540E\u4ECD\u9700\u72EC\u7ACB\u9A8C\u6536\u3001\u751F\u6210\u7528\u6237\u9A8C\u6536\u62A5\u544A\u5E76\u53D6\u5F97\u7528\u6237\u786E\u8BA4\u3002",
    "",
    "\u7ED3\u679C\u8FD4\u56DE\u683C\u5F0F\uFF08\u7531\u6CBB\u7406\u5BBF\u4E3B\u4FDD\u5B58\u4E3A evidence\uFF0C\u5E76\u7528\u76F8\u540C Operation ID \u6267\u884C task-result\uFF09\uFF1A",
    "```json",
    JSON.stringify(resultTemplate, null, 2),
    "```",
    "",
    "\u51BB\u7ED3\u4E0A\u4E0B\u6587\uFF1A",
    "```json",
    JSON.stringify(context, null, 2),
    "```",
    ""
  ].join("\n");
}
async function buildTaskContext({ root, id, explicitDogfood = false, fs = fsPromises2 } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const registry = await readRegistryUnlocked(root, fs);
  const entry = itemById(registry, id);
  if (entry.kind !== "TASK" || entry.stage !== "BASELINE_FROZEN") {
    fail2("WORK_ITEM_TASK_REQUIRED", "Independent context can only be built for a frozen Task");
  }
  if (entry.developmentMode === null) {
    fail2("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", `${id} requires an explicitly confirmed development mode`);
  }
  const own = await assertCurrentLineage(root, registry, entry, fs);
  const parents = [];
  let childId = entry.id;
  let parentId = entry.parentId;
  while (parentId) {
    const parentEntry = itemById(registry, parentId);
    const parent = (await readPackageDefinition(root, parentEntry, fs)).definition;
    parents.unshift(parentContractSnapshot(parent, childId));
    childId = parent.id;
    parentId = parent.parentId;
  }
  const dependencies = [];
  for (const dependencyId of own.definition.execution.dependsOn) {
    const dependency = itemById(registry, dependencyId);
    const definition = (await readPackageDefinition(root, dependency, fs)).definition;
    dependencies.push({
      id: dependency.id,
      status: dependency.status,
      outputs: definition.execution.outputs,
      evidence: dependency.latestEvidence
    });
  }
  if (dependencies.some(({ status }) => status !== "VERIFIED")) {
    fail2("WORK_ITEM_NOT_READY", `${id} has unverified Task dependencies`);
  }
  let capabilityDependencies = [];
  if (entry.parentId !== null) {
    const capabilityEntry = itemById(registry, entry.parentId);
    const capabilityDefinition = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
    capabilityDependencies = capabilityDefinition.decomposition.dependsOn.map((dependencyId) => {
      const dependency = itemById(registry, dependencyId);
      return {
        id: dependency.id,
        status: dependency.status,
        contractFingerprint: dependency.contractFingerprint,
        evidence: dependency.latestEvidence
      };
    });
  }
  if (capabilityDependencies.some(({ status }) => status !== "VERIFIED")) {
    fail2("WORK_ITEM_NOT_READY", `${id} has unverified Capability dependencies`);
  }
  const context = {
    schemaVersion: 1,
    gateLevel: own.definition.gateLevel,
    developmentMode: entry.developmentMode.mode,
    operation: entry.claim ? {
      owner: entry.claim.owner,
      operationId: entry.claim.operationId,
      claimedAt: entry.claim.claimedAt
    } : null,
    task: {
      id: own.definition.id,
      title: own.definition.title,
      goal: own.definition.goal,
      scope: own.definition.scope,
      baselineFingerprint: entry.baselineFingerprint,
      developmentPlan: own.definition.developmentPlan ?? null
    },
    parentContracts: parents,
    capabilityDependencies,
    dependencies,
    requirements: own.definition.requirements,
    acceptance: own.definition.acceptance,
    execution: own.definition.execution,
    testCommands: own.definition.testCommands,
    rules: {
      inheritConversation: false,
      allowRequirementChanges: false,
      allowExternalStateChanges: false
    }
  };
  const handoffPrompt = renderTaskHandoff(context);
  await atomicWriteFile(path3.join(own.target, "context-manifest.json"), json(context), { fs });
  await atomicWriteFile(path3.join(own.target, "development-handoff.md"), handoffPrompt, { fs });
  return { ...context, handoffPrompt };
}
async function dispatchTask({
  root,
  id,
  owner,
  operationId,
  explicitDogfood = false,
  now,
  fs = fsPromises2
} = {}) {
  await buildTaskContext({ root, id, explicitDogfood, fs });
  const claim = await claimTask({
    root,
    id,
    owner,
    operationId,
    explicitDogfood,
    now,
    fs
  });
  const context = await buildTaskContext({ root, id, explicitDogfood, fs });
  return { ...claim, ...context };
}

// src/cli/output.mjs
var SENSITIVE = /^(stdout|stderr|env|.*token.*|.*key.*|.*secret.*|.*password.*)$/i;
function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, SENSITIVE.test(key) ? "[REDACTED]" : redact(child)]));
  return value;
}
function renderJson(value) {
  return `${JSON.stringify(redact(value))}
`;
}
function renderError(error) {
  return `ERROR ${error.code}: ${error.message}
`;
}

// src/cli/hierarchical.mjs
var HIERARCHICAL_COMMANDS = Object.freeze([
  "prepare-item",
  "freeze-item",
  "revise-item",
  "promote-item",
  "select-development-mode",
  "ready-tasks",
  "task-context",
  "dispatch-task",
  "claim-task",
  "task-result",
  "retry-item",
  "gate-item",
  "accept-item",
  "acceptance-item",
  "delivery-item",
  "refresh-projections",
  "upgrade-registry"
]);
var VALUE_OPTIONS = /* @__PURE__ */ new Set([
  "--definition",
  "--host-runtime",
  "--item",
  "--parent",
  "--owner",
  "--operation",
  "--status",
  "--evidence",
  "--expected-baseline",
  "--expected-parent-baseline",
  "--action",
  "--development-mode",
  "--task-gate-level"
]);
var FLAG_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--confirmed", "--dogfood"]);
var COMMAND_OPTIONS = Object.freeze({
  "prepare-item": /* @__PURE__ */ new Set(["--json", "--help", "--definition", "--host-runtime", "--dogfood"]),
  "freeze-item": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--expected-baseline", "--confirmed", "--dogfood"]),
  "revise-item": /* @__PURE__ */ new Set(["--json", "--help", "--definition", "--expected-baseline", "--confirmed", "--dogfood"]),
  "promote-item": /* @__PURE__ */ new Set([
    "--json",
    "--help",
    "--item",
    "--parent",
    "--expected-baseline",
    "--expected-parent-baseline",
    "--confirmed",
    "--dogfood"
  ]),
  "ready-tasks": /* @__PURE__ */ new Set(["--json", "--help", "--item"]),
  "task-context": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--dogfood"]),
  "select-development-mode": /* @__PURE__ */ new Set([
    "--json",
    "--help",
    "--item",
    "--development-mode",
    "--expected-baseline",
    "--confirmed",
    "--dogfood"
  ]),
  "claim-task": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--owner", "--operation", "--dogfood"]),
  "dispatch-task": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--owner", "--operation", "--dogfood"]),
  "task-result": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--operation", "--status", "--evidence", "--dogfood"]),
  "retry-item": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--expected-baseline", "--confirmed", "--dogfood"]),
  "gate-item": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--status", "--evidence", "--dogfood"]),
  "accept-item": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--evidence", "--dogfood"]),
  "acceptance-item": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--action", "--evidence", "--dogfood"]),
  "delivery-item": /* @__PURE__ */ new Set(["--json", "--help", "--item", "--action", "--evidence", "--dogfood"]),
  "refresh-projections": /* @__PURE__ */ new Set(["--json", "--help", "--dogfood"]),
  "upgrade-registry": /* @__PURE__ */ new Set(["--json", "--help", "--task-gate-level", "--confirmed", "--dogfood"])
});
var usage = `Usage: hdg <command> [options]

Commands:
${HIERARCHICAL_COMMANDS.map((command) => `  ${command}`).join("\n")}

  prepare-item --definition <file|-> --host-runtime <agent>  # writes human review package
  freeze-item --item <id> --expected-baseline <sha256> --confirmed  # after human review
  revise-item --definition <file|-> --expected-baseline <sha256> --confirmed
  promote-item --item <root-id> --parent <frozen-parent-id> --expected-baseline <sha256> --expected-parent-baseline <sha256> --confirmed
  select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
  ready-tasks --item <root-or-subtree-id>
  task-context --item <task-id>
  dispatch-task --item <task-id> --owner <owner> --operation <id>
  claim-task --item <task-id> --owner <owner> --operation <id>
  task-result --item <task-id> --operation <id> --status IMPLEMENTED|BLOCKED --evidence <file|->
  retry-item --item <id> --expected-baseline <sha256> --confirmed
  gate-item --item <id> --status PASS|FAIL --evidence <file|->
  accept-item --item <id> --evidence <file|->
  acceptance-item --item <root-id> --action INDEPENDENT_REVIEW_PASS|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence <file|->
  delivery-item --item <delivery-id> --action INDEPENDENT_REVIEW_PASS|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence <file|->
  refresh-projections
  upgrade-registry --task-gate-level LIGHT|FULL --confirmed

In the hierarchical-delivery-governance implementation repository, every command that writes control state also requires --dogfood.
`;
function parse(argv) {
  const seen = /* @__PURE__ */ new Set();
  const values = {};
  const positionals = [];
  for (let index = 0; index < argv.length; index++) {
    const item = argv[index];
    if (!item.startsWith("--")) {
      positionals.push(item);
      continue;
    }
    if (!VALUE_OPTIONS.has(item) && !FLAG_OPTIONS.has(item)) {
      throw new GatedLoopError("UNKNOWN_OPTION", `Unknown option: ${item}`);
    }
    if (seen.has(item)) throw new GatedLoopError("DUPLICATE_OPTION", `Duplicate option: ${item}`);
    seen.add(item);
    if (VALUE_OPTIONS.has(item)) {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) {
        throw new GatedLoopError("OPTION_VALUE_REQUIRED", `Missing value for option: ${item}`);
      }
      values[item] = value;
      index += 1;
    }
  }
  const [command, ...extraPositionals] = positionals;
  if (extraPositionals.length > 0) {
    throw new GatedLoopError("UNKNOWN_OPTION", `Unexpected positional argument: ${extraPositionals.at(-1)}`);
  }
  if (COMMAND_OPTIONS[command]) {
    for (const option of seen) {
      if (!COMMAND_OPTIONS[command].has(option)) {
        throw new GatedLoopError("UNKNOWN_OPTION", `Option is not valid for ${command}: ${option}`);
      }
    }
  }
  if (values["--host-runtime"] !== void 0 && !isAgentRuntime(values["--host-runtime"])) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--host-runtime must be a safe lowercase Agent identifier");
  }
  if (values["--development-mode"] !== void 0 && !["active", "manual"].includes(values["--development-mode"])) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--development-mode must be active or manual");
  }
  if (values["--task-gate-level"] !== void 0 && !["LIGHT", "FULL"].includes(values["--task-gate-level"])) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--task-gate-level must be LIGHT or FULL");
  }
  return {
    command,
    json: seen.has("--json"),
    confirmed: seen.has("--confirmed"),
    dogfood: seen.has("--dogfood"),
    values
  };
}
function required(parsed, option) {
  const value = parsed.values[option];
  if (value === void 0) throw new GatedLoopError("OPTION_REQUIRED", `${parsed.command} requires ${option}`);
  return value;
}
async function readStructured(source, kind, { cwd, fs, stdin }) {
  let text2;
  try {
    if (source === "-") {
      if (stdin !== void 0) text2 = typeof stdin === "function" ? await stdin() : stdin;
      else text2 = await fs.readFile(0, "utf8");
    } else {
      const portable = String(source).replaceAll("\\", "/").toLowerCase();
      const basename = portable.split("/").at(-1);
      if (basename.startsWith(".env") || portable.includes("production")) {
        throw new GatedLoopError("INPUT_PATH_FORBIDDEN", "Structured input path is forbidden");
      }
      text2 = (await readSafeRegularFile(cwd, source, { fs })).toString("utf8");
    }
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError(`${kind}_READ`, `Unable to read ${kind.toLowerCase()} JSON`);
  }
  try {
    const value = JSON.parse(String(text2));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not a mapping");
    return value;
  } catch {
    throw new GatedLoopError(`${kind}_PARSE`, `${kind.toLowerCase()} JSON must be a mapping`);
  }
}
async function runWorkItemCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises3;
  const common = { root, fs, now: io.now, explicitDogfood: parsed.dogfood };
  if (["prepare-item", "revise-item"].includes(parsed.command)) {
    const definition = await readStructured(
      required(parsed, "--definition"),
      "WORK_ITEM_DEFINITION",
      { cwd: root, fs, stdin: io.stdin }
    );
    if (parsed.command === "prepare-item") {
      return prepareWorkItem({
        ...common,
        definition,
        hostRuntime: required(parsed, "--host-runtime")
      });
    }
    return reviseWorkItem({
      ...common,
      definition,
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "freeze-item") {
    return freezeWorkItem({
      ...common,
      id: required(parsed, "--item"),
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "promote-item") {
    return promoteWorkItem({
      ...common,
      id: required(parsed, "--item"),
      parentId: required(parsed, "--parent"),
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      expectedParentBaselineFingerprint: required(parsed, "--expected-parent-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "ready-tasks") {
    return listReadyTasks({ ...common, workItemId: required(parsed, "--item") });
  }
  if (parsed.command === "task-context") {
    return buildTaskContext({ ...common, id: required(parsed, "--item") });
  }
  if (parsed.command === "select-development-mode") {
    return selectDevelopmentMode({
      ...common,
      id: required(parsed, "--item"),
      mode: required(parsed, "--development-mode"),
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "claim-task") {
    return claimTask({
      ...common,
      id: required(parsed, "--item"),
      owner: required(parsed, "--owner"),
      operationId: required(parsed, "--operation")
    });
  }
  if (parsed.command === "dispatch-task") {
    return dispatchTask({
      ...common,
      id: required(parsed, "--item"),
      owner: required(parsed, "--owner"),
      operationId: required(parsed, "--operation")
    });
  }
  if (parsed.command === "retry-item") {
    return retryWorkItem({
      ...common,
      id: required(parsed, "--item"),
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "refresh-projections") {
    return refreshWorkItemProjections(common);
  }
  if (parsed.command === "upgrade-registry") {
    return upgradeWorkItemRegistry({
      ...common,
      taskGateLevel: required(parsed, "--task-gate-level"),
      confirmed: parsed.confirmed
    });
  }
  const evidence = await readStructured(
    required(parsed, "--evidence"),
    "WORK_ITEM_EVIDENCE",
    { cwd: root, fs, stdin: io.stdin }
  );
  if (parsed.command === "task-result") {
    return recordTaskResult({
      ...common,
      id: required(parsed, "--item"),
      operationId: required(parsed, "--operation"),
      status: required(parsed, "--status"),
      evidence,
      strictEvidence: true
    });
  }
  if (parsed.command === "delivery-item") {
    return recordDelivery({
      ...common,
      id: required(parsed, "--item"),
      action: required(parsed, "--action"),
      evidence
    });
  }
  if (parsed.command === "acceptance-item") {
    return recordAcceptance({
      ...common,
      id: required(parsed, "--item"),
      action: required(parsed, "--action"),
      evidence
    });
  }
  if (parsed.command === "accept-item") {
    return acceptWorkItem({
      ...common,
      id: required(parsed, "--item"),
      evidence
    });
  }
  return recordWorkItemGate({
    ...common,
    id: required(parsed, "--item"),
    status: required(parsed, "--status"),
    evidence
  });
}
async function runHierarchicalCli(argv, io = {}) {
  const stdout = io.stdout ?? ((value) => process.stdout.write(value));
  const stderr = io.stderr ?? ((value) => process.stderr.write(value));
  const command = argv.find((value) => !value.startsWith("--"));
  const jsonOutput = argv.includes("--json");
  if (!command || argv.includes("--help")) {
    stdout(usage);
    return 0;
  }
  if (!HIERARCHICAL_COMMANDS.includes(command)) {
    const error = new GatedLoopError("UNKNOWN_COMMAND", `Unknown hdg command: ${command}`);
    stderr(jsonOutput ? renderJson({ ok: false, error: { code: error.code, message: error.message, details: error.details } }) : renderError(error));
    return error.exitCode;
  }
  try {
    const parsed = parse(argv);
    const result = await runWorkItemCommand(parsed, io);
    stdout(parsed.json ? renderJson({ ok: true, result }) : renderJson(result));
    return 0;
  } catch (error) {
    const stable = error instanceof GatedLoopError ? error : new GatedLoopError("INTERNAL_ERROR", "Unexpected error");
    stderr(jsonOutput ? renderJson({ ok: false, error: { code: stable.code, message: stable.message, details: stable.details } }) : renderError(stable));
    return stable.exitCode;
  }
}

// scripts/skill-cli-entry.mjs
process.exitCode = await runHierarchicalCli(process.argv.slice(2));
