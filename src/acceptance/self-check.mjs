import * as fsPromises from 'node:fs/promises';
import path from 'node:path';

import { loadConfig } from '../config/load-config.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { runProcess } from '../core/process.mjs';
import { normalizeBaselineInputPath } from '../baseline/sources.mjs';
import {
  aggregateDiffBundles, attributeChanges, buildDiffBundle, currentStatus, enrichStatus, fingerprint,
  inspectWorkspace, json, loadFrozenTask, loadWorkspacePlan, matchesAny, normalizeRound, readSnapshot,
  roundDirectory, testCounts, writeRoundFile,
} from './common.mjs';

function iso(now) {
  const value = typeof now === 'function' ? now() : new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError('SELF_CHECK_TIMESTAMP_INVALID', 'Self-check timestamp is invalid');
  return date.toISOString();
}

function display(value) { return value === null ? '未解析' : String(value); }

function policyForbidden(filePath) {
  try { normalizeBaselineInputPath(filePath); return false; }
  catch { return true; }
}

function renderSelfCheck(evidence) {
  const testRows = evidence.tests.length === 0
    ? ['| 未运行 | - | - | - | - | - | BLOCKED |']
    : evidence.tests.map((entry) => `| \`${JSON.stringify(entry.argv)}\` | ${entry.exitCode ?? '-'} | ${display(entry.counts.passed)} | ${display(entry.counts.failed)} | ${display(entry.counts.errors)} | ${display(entry.counts.skipped)} | ${entry.status} |`);
  const changes = evidence.changedFiles.length > 0 ? evidence.changedFiles.map((entry) => `- ${entry}`).join('\n') : '- 无';
  const preExisting = evidence.preExistingUnchanged.length > 0 ? evidence.preExistingUnchanged.map((entry) => `- ${entry}`).join('\n') : '- 无';
  const blockers = evidence.blockers.length > 0 ? evidence.blockers.map((entry) => `- ${entry}`).join('\n') : '- 无';
  const review = evidence.humanReviewReasons.length > 0 ? evidence.humanReviewReasons.map((entry) => `- ${entry}`).join('\n') : '- 无';
  return `# ${evidence.task} ${evidence.round} 机械自检报告

## 结论
${evidence.status}

## 冻结完整性
- 基线指纹：${evidence.checks.frozenFingerprint ? '匹配' : '不匹配'}
- 开发前 commit：${evidence.baseCommit ?? '未知'}
- 当前 commit：${evidence.headCommit ?? '未知'}

## 改动归属与范围
- 本轮真实改动：
${changes}
- 未变化的开发前已有改动：
${preExisting}
- 归属不明确：${evidence.ambiguousPaths.length > 0 ? evidence.ambiguousPaths.join('、') : '无'}
- 范围检查：${evidence.checks.scope ? '通过' : '失败'}

## 保护项检查
- 冻结产物：${evidence.checks.frozenFingerprint ? '通过' : '失败'}
- 受保护路径：${evidence.checks.protectedPaths ? '通过' : '失败'}
- 敏感文件：${evidence.checks.forbiddenPaths ? '未读取且未发现改动' : '发现改动，内容未读取'}

## 测试证据
| argv | exitCode | passed | failed | errors | skipped | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
${testRows.join('\n')}

## 确定性阻断项
${blockers}

## 需要人工判断
${review}
`;
}

function renderMultiSelfCheck(evidence) {
  const workspaceRows = evidence.workspaces.length === 0
    ? ['| 未解析 | - | - | - | - | - | BLOCKED |']
    : evidence.workspaces.map((entry) => `| ${entry.workspaceId} | ${entry.wave} | ${entry.dependsOnWorkspaceIds.join('、') || '-'} | ${entry.baseCommit} | ${entry.headCommit ?? '-'} | ${entry.changedFiles.length} | ${entry.status} |`);
  const testRows = evidence.tests.length === 0
    ? ['| 未运行 | - | - | - | - | - | - | - | BLOCKED |']
    : evidence.tests.map((entry) => `| ${entry.workspaceId} | ${entry.cwd} | ${JSON.stringify(entry.argv)} | ${entry.exitCode ?? '-'} | ${display(entry.counts.passed)} | ${display(entry.counts.failed)} | ${display(entry.counts.errors)} | ${display(entry.counts.skipped)} | ${entry.status} |`);
  const changes = evidence.changedFiles.length > 0
    ? evidence.changedFiles.map((entry) => `- ${entry.workspaceId}:${entry.path}`).join('\n') : '- 无';
  const blockers = evidence.blockers.length > 0 ? evidence.blockers.map((entry) => `- ${entry}`).join('\n') : '- 无';
  const review = evidence.humanReviewReasons.length > 0 ? evidence.humanReviewReasons.map((entry) => `- ${entry}`).join('\n') : '- 无';
  return `# ${evidence.task} ${evidence.round} 多工作区机械自检报告

## 结论
${evidence.status}

## 工作区覆盖与依赖
- 快照：schema v2
- 冻结指纹：${evidence.checks.frozenFingerprint ? '匹配' : '不匹配'}
- 工作区覆盖：${evidence.checks.workspaceCoverage ? '通过' : '失败'}
- 依赖图：${evidence.checks.dependencyGraph ? '无环并已按波次执行' : '失败'}

| 工作区 | 波次 | 前置工作区 | 开发前 commit | 当前 commit | 改动数 | 结论 |
| --- | ---: | --- | --- | --- | ---: | --- |
${workspaceRows.join('\n')}

## 聚合改动
${changes}

## 测试证据
| 工作区 | cwd | argv | exitCode | passed | failed | errors | skipped | 结论 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
${testRows.join('\n')}

## 确定性阻断项
${blockers}

## 需要人工判断
${review}
`;
}

async function executeTests(frozen, { root, runProcessImpl, timeoutMs }) {
  const results = [];
  for (const argv of frozen.testCommands) {
    try {
      const result = await runProcessImpl(argv[0], argv.slice(1), { cwd: root, timeoutMs, captureOutput: true });
      const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`;
      results.push({
        argv, exitCode: result.exitCode, signal: result.signal, counts: testCounts(output), status: 'PASS',
        outputTruncated: Boolean(result.stdoutTruncated || result.stderrTruncated),
      });
    } catch (error) {
      const details = error instanceof GatedLoopError ? error.details : {};
      const output = `${details.stdout ?? ''}\n${details.stderr ?? ''}`;
      results.push({
        argv, exitCode: details.exitCode ?? null, signal: details.signal ?? null,
        counts: testCounts(output), status: 'FAIL', errorCode: error.code ?? 'PROCESS_FAILED',
        outputTruncated: false,
      });
    }
  }
  return results;
}

async function executeWorkspaceTests(workspace, { runProcessImpl, timeoutMs }) {
  const results = [];
  for (const command of workspace.testCommands) {
    try {
      const result = await runProcessImpl(command.argv[0], command.argv.slice(1), {
        cwd: command.cwd, timeoutMs, captureOutput: true,
      });
      const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`;
      results.push({
        workspaceId: workspace.id, wave: workspace.wave, cwd: command.cwd, argv: command.argv,
        exitCode: result.exitCode, signal: result.signal, counts: testCounts(output), status: 'PASS',
        outputTruncated: Boolean(result.stdoutTruncated || result.stderrTruncated),
      });
    } catch (error) {
      const details = error instanceof GatedLoopError ? error.details : {};
      const output = `${details.stdout ?? ''}\n${details.stderr ?? ''}`;
      results.push({
        workspaceId: workspace.id, wave: workspace.wave, cwd: command.cwd, argv: command.argv,
        exitCode: details.exitCode ?? null, signal: details.signal ?? null,
        counts: testCounts(output), status: 'FAIL', errorCode: error.code ?? 'PROCESS_FAILED',
        outputTruncated: false,
      });
    }
  }
  return results;
}

function blockedWorkspaceTests(workspace, dependencyIds) {
  return workspace.testCommands.map((command) => ({
    workspaceId: workspace.id, wave: workspace.wave, cwd: command.cwd, argv: command.argv,
    exitCode: null, signal: null, counts: testCounts(''), status: 'BLOCKED',
    errorCode: 'WORKSPACE_DEPENDENCY_BLOCKED', blockedBy: dependencyIds, outputTruncated: false,
  }));
}

async function runMultiWorkspaceSelfCheck({
  root, task, round, frozen, snapshot, config, directory, fs, runProcessImpl, timeoutMs, now,
}) {
  const blockers = []; const humanReviewReasons = [];
  let plan = []; let coverageValid = false; let dependencyGraph = false;
  try {
    plan = await loadWorkspacePlan({ root, task, round, snapshot, frozen, fs });
    coverageValid = true; dependencyGraph = true;
  } catch (error) {
    humanReviewReasons.push(`${error.code ?? 'WORKSPACE_GATE_INVALID'}：${error.message}`);
  }
  const inspections = []; const workspaceState = new Map();
  for (const workspace of plan) {
    const workspaceBlockers = []; const workspaceReview = [];
    let inspection = null;
    try {
      inspection = await inspectWorkspace({
        coordinatorRoot: root, task, workspace, git: config.tools.git,
        protectedPaths: config.protectedPaths, forbiddenPaths: config.forbiddenPaths,
        isPolicyForbidden: policyForbidden, runProcessImpl, timeoutMs, fs,
      });
      inspections.push(inspection);
      if (inspection.repository.head !== workspace.baseCommit) workspaceBlockers.push('当前 HEAD 与开发前快照 commit 不一致');
      if (inspection.repository.branch !== workspace.branch) workspaceBlockers.push(`当前分支与快照不一致：${inspection.repository.branch}`);
      if (inspection.protectedChanged.length > 0) workspaceBlockers.push(`受保护路径发生改动：${inspection.protectedChanged.map((entry) => entry.path).join('、')}`);
      if (inspection.forbiddenChanged.length > 0) workspaceBlockers.push(`敏感路径发生改动（未读取内容）：${inspection.forbiddenChanged.map((entry) => entry.path).join('、')}`);
      if (inspection.outOfScope.length > 0) workspaceBlockers.push(`本轮改动超出允许范围：${inspection.outOfScope.map((entry) => entry.path).join('、')}`);
      if (inspection.changed.length === 0) workspaceBlockers.push('未检测到可归属于本轮的仓库改动');
      if (inspection.ambiguous.length > 0) workspaceReview.push(`开发前已有改动在本轮发生变化或消失：${inspection.ambiguous.join('、')}`);
      if (inspection.diffBundle.truncated) workspaceReview.push('最终 diff 超过安全上下文上限，无法完整验收');
    } catch (error) {
      workspaceReview.push(`${error.code ?? 'GIT_EVIDENCE_FAILED'}：无法取得完整 Git 证据`);
    }
    for (const message of workspaceBlockers) blockers.push(`[${workspace.id}] ${message}`);
    for (const message of workspaceReview) humanReviewReasons.push(`[${workspace.id}] ${message}`);
    workspaceState.set(workspace.id, {
      workspace, inspection, blockers: workspaceBlockers, humanReviewReasons: workspaceReview,
      tests: [], status: workspaceBlockers.length > 0 ? 'FAIL' : workspaceReview.length > 0 ? 'NEED_HUMAN_REVIEW' : 'PENDING',
    });
  }
  const tests = [];
  for (const workspace of plan) {
    const state = workspaceState.get(workspace.id);
    const failedDependencies = workspace.dependsOnWorkspaceIds.filter((id) => workspaceState.get(id)?.status !== 'PASS');
    if (failedDependencies.length > 0) {
      state.tests = blockedWorkspaceTests(workspace, failedDependencies);
      state.blockers.push(`前置工作区门禁未通过：${failedDependencies.join('、')}`);
      blockers.push(`[${workspace.id}] 前置工作区门禁未通过：${failedDependencies.join('、')}`);
      state.status = 'FAIL';
    } else {
      state.tests = await executeWorkspaceTests(workspace, { runProcessImpl, timeoutMs });
      for (const result of state.tests) {
        if (result.status !== 'PASS') {
          state.blockers.push(`测试失败：${JSON.stringify(result.argv)}`);
          blockers.push(`[${workspace.id}] 测试失败：${JSON.stringify(result.argv)}`);
        }
        if (result.outputTruncated) {
          state.humanReviewReasons.push(`测试输出被截断：${JSON.stringify(result.argv)}`);
          humanReviewReasons.push(`[${workspace.id}] 测试输出被截断：${JSON.stringify(result.argv)}`);
        }
      }
      state.status = state.blockers.length > 0 ? 'FAIL'
        : state.humanReviewReasons.length > 0 ? 'NEED_HUMAN_REVIEW' : 'PASS';
    }
    tests.push(...state.tests);
  }
  const bundle = inspections.length === plan.length ? aggregateDiffBundles(inspections) : null;
  if (bundle?.truncated && !humanReviewReasons.some((entry) => entry.includes('最终 diff'))) {
    humanReviewReasons.push('聚合 diff 超过安全上下文上限，无法完整验收');
  }
  const workspaceEvidence = plan.map((workspace) => {
    const state = workspaceState.get(workspace.id); const inspection = state.inspection;
    return {
      workspaceId: workspace.id, root: workspace.root, branch: workspace.branch,
      wave: workspace.wave, dependsOnWorkspaceIds: workspace.dependsOnWorkspaceIds,
      baseCommit: workspace.baseCommit, headCommit: inspection?.repository.head ?? null,
      currentBranch: inspection?.repository.branch ?? null,
      changedFiles: inspection?.changed.map((entry) => entry.path).sort() ?? [],
      preExistingUnchanged: inspection?.unchangedPreExisting.sort() ?? [],
      ambiguousPaths: inspection?.ambiguous.sort() ?? [],
      diffSha256: inspection?.diffBundle.sha256 ?? null,
      status: state.status, blockers: state.blockers, humanReviewReasons: state.humanReviewReasons,
    };
  });
  const changedFiles = workspaceEvidence.flatMap((entry) => entry.changedFiles.map((filePath) => ({
    workspaceId: entry.workspaceId, path: filePath,
  }))).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId) || left.path.localeCompare(right.path));
  const status = blockers.length > 0 ? 'FAIL' : humanReviewReasons.length > 0 ? 'NEED_HUMAN_REVIEW' : 'PASS';
  const evidence = {
    schemaVersion: 2, task, round, mode: frozen.mode, status,
    frozenFingerprint: frozen.frozenFingerprint, changedFiles,
    diffSha256: bundle?.sha256 ?? null, diffWorkspaces: bundle?.workspaces ?? [],
    checks: {
      frozenFingerprint: true, workspaceCoverage: coverageValid, dependencyGraph,
      protectedPaths: !blockers.some((entry) => entry.includes('受保护路径')),
      forbiddenPaths: !blockers.some((entry) => entry.includes('敏感路径')),
      scope: !blockers.some((entry) => entry.includes('允许范围')),
      attribution: plan.length > 0 && workspaceEvidence.every((entry) => entry.ambiguousPaths.length === 0),
      dependencies: plan.length > 0 && workspaceEvidence.every((entry) => entry.status === 'PASS'),
    },
    workspaces: workspaceEvidence, tests, blockers, humanReviewReasons, createdAt: iso(now),
  };
  const report = renderMultiSelfCheck(evidence);
  evidence.reportFingerprint = fingerprint({ text: report });
  evidence.evidenceFingerprint = fingerprint(evidence);
  const evidencePath = await writeRoundFile(directory, 'gate-evidence.json', json(evidence), { fs });
  const reportPath = await writeRoundFile(directory, 'self-check-report.md', report, { fs });
  return { status, task, round, evidencePath, reportPath, evidenceFingerprint: evidence.evidenceFingerprint };
}

export async function runSelfCheck({
  root, task, round: suppliedRound, snapshot: snapshotSource, timeoutMs = 120_000,
  fs = fsPromises, runProcessImpl = runProcess, now,
} = {}) {
  const round = normalizeRound(suppliedRound);
  const frozen = await loadFrozenTask({ root, task, fs });
  const config = await loadConfig(root);
  const directory = await roundDirectory({ root, task, round, fs });
  const blockers = [];
  const humanReviewReasons = [];
  let snapshot;
  try { snapshot = await readSnapshot({ root, task, round, source: snapshotSource, frozen, fs }); }
  catch (error) { humanReviewReasons.push(`${error.code ?? 'SNAPSHOT_INVALID'}：${error.message}`); }

  if (snapshot?.schemaVersion === 2) {
    return runMultiWorkspaceSelfCheck({
      root, task, round, frozen, snapshot, config, directory, fs, runProcessImpl, timeoutMs, now,
    });
  }

  let repository = null;
  let changed = [];
  let ambiguousPaths = [];
  let preExistingUnchanged = [];
  let diffBundle = null;
  try {
    repository = await currentStatus({ root, git: config.tools.git, runProcessImpl, timeoutMs });
    const runtimePrefix = `.ai-dev-loop/${task}/`;
    const relevant = repository.entries.filter((entry) => !entry.path.startsWith(runtimePrefix));
    const protectedChanged = relevant.filter((entry) => matchesAny(entry.path, config.protectedPaths));
    const forbiddenChanged = relevant.filter((entry) => matchesAny(entry.path, config.forbiddenPaths) || policyForbidden(entry.path));
    if (protectedChanged.length > 0) blockers.push(`受保护路径发生改动：${protectedChanged.map((entry) => entry.path).join('、')}`);
    if (forbiddenChanged.length > 0) blockers.push(`敏感路径发生改动（未读取内容）：${forbiddenChanged.map((entry) => entry.path).join('、')}`);
    const enriched = await enrichStatus(root, relevant, {
      fs, skipPatterns: config.forbiddenPaths, skipPaths: forbiddenChanged.map((entry) => entry.path),
    });
    if (snapshot) {
      if (repository.head !== snapshot.baseCommit) blockers.push('当前 HEAD 与开发前快照 commit 不一致');
      const attributed = attributeChanges(enriched, snapshot);
      changed = attributed.changed;
      ambiguousPaths = attributed.ambiguous;
      preExistingUnchanged = attributed.unchangedPreExisting;
      if (ambiguousPaths.length > 0) humanReviewReasons.push(`开发前已有改动在本轮发生变化或消失：${ambiguousPaths.join('、')}`);
      const outOfScope = changed.filter((entry) => !matchesAny(entry.path, snapshot.allowedPaths));
      if (outOfScope.length > 0) blockers.push(`本轮改动超出允许范围：${outOfScope.map((entry) => entry.path).join('、')}`);
      if (frozen.mode === 'light' && changed.length > 3) blockers.push('Light 模式实际改动超过三个文件，必须升级为 Full');
      if (changed.length === 0) blockers.push('未检测到可归属于本轮的仓库改动');
      const forbiddenSet = new Set(forbiddenChanged.map((entry) => entry.path));
      const safeChanged = changed.filter((entry) => !forbiddenSet.has(entry.path));
      diffBundle = await buildDiffBundle({ root, git: config.tools.git, changed: safeChanged, runProcessImpl, timeoutMs, fs });
      if (diffBundle.truncated) humanReviewReasons.push('最终 diff 超过安全上下文上限，无法完整验收');
    }
  } catch (error) {
    humanReviewReasons.push(`${error.code ?? 'GIT_EVIDENCE_FAILED'}：无法取得完整 Git 证据`);
  }

  const tests = await executeTests(frozen, { root, runProcessImpl, timeoutMs });
  for (const result of tests) {
    if (result.status !== 'PASS') blockers.push(`测试失败：${JSON.stringify(result.argv)}`);
    if (result.outputTruncated) humanReviewReasons.push(`测试输出被截断：${JSON.stringify(result.argv)}`);
  }

  const forbiddenPaths = !blockers.some((entry) => entry.startsWith('敏感路径'));
  const protectedPaths = !blockers.some((entry) => entry.startsWith('受保护路径'));
  const scope = !blockers.some((entry) => entry.includes('允许范围') || entry.includes('超过三个文件'));
  const status = blockers.length > 0 ? 'FAIL' : humanReviewReasons.length > 0 ? 'NEED_HUMAN_REVIEW' : 'PASS';
  const evidence = {
    schemaVersion: 1, task, round, mode: frozen.mode, status,
    baseCommit: snapshot?.baseCommit ?? null, headCommit: repository?.head ?? null,
    frozenFingerprint: frozen.frozenFingerprint,
    changedFiles: changed.map((entry) => entry.path).sort(),
    preExistingUnchanged: [...preExistingUnchanged].sort(), ambiguousPaths: [...ambiguousPaths].sort(),
    diffSha256: diffBundle?.sha256 ?? null,
    checks: { frozenFingerprint: true, protectedPaths, forbiddenPaths, scope, attribution: ambiguousPaths.length === 0 && Boolean(snapshot) },
    tests, blockers, humanReviewReasons, createdAt: iso(now),
  };
  const report = renderSelfCheck(evidence);
  evidence.reportFingerprint = fingerprint({ text: report });
  evidence.evidenceFingerprint = fingerprint(evidence);
  const evidencePath = await writeRoundFile(directory, 'gate-evidence.json', json(evidence), { fs });
  const reportPath = await writeRoundFile(directory, 'self-check-report.md', report, { fs });
  return { status, task, round, evidencePath, reportPath, evidenceFingerprint: evidence.evidenceFingerprint };
}
