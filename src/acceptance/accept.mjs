import * as fsPromises from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';

import { loadConfig } from '../config/load-config.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { readSafeRegularFile } from '../core/fs-safe.mjs';
import { runProcess } from '../core/process.mjs';
import { normalizeBaselineInputPath } from '../baseline/sources.mjs';
import { isAgentRuntime } from '../mode/host-runtime.mjs';
import {
  aggregateDiffBundles, attributeChanges, buildDiffBundle, currentStatus, enrichStatus, fingerprint,
  inspectWorkspace, json, loadFrozenTask, loadWorkspacePlan, matchesAny, normalizeRound, readSnapshot,
  roundDirectory, stableJson, writeRoundFile,
} from './common.mjs';

export const REVIEW_SCHEMA = Object.freeze({
  type: 'object',
  additionalProperties: false,
  required: ['status', 'reviewer', 'reviewerKind', 'isolation', 'checkedAcceptanceIds', 'counts', 'findings', 'suggestedTests', 'repairInstructions'],
  properties: {
    status: { enum: ['PASS', 'FAIL', 'NEED_HUMAN_REVIEW'] },
    reviewer: { type: ['string', 'null'], pattern: '^[a-z][a-z0-9._-]{0,63}$' },
    reviewerKind: { enum: ['independent-agent', 'fresh-subagent', 'human-review'] },
    isolation: { enum: ['fresh-read-only-no-development-context', 'not-available'] },
    checkedAcceptanceIds: { type: 'array', items: { type: 'string' }, uniqueItems: true },
    counts: {
      type: 'object', additionalProperties: false, required: ['p0', 'p1', 'p2'],
      properties: { p0: { type: 'integer', minimum: 0 }, p1: { type: 'integer', minimum: 0 }, p2: { type: 'integer', minimum: 0 } },
    },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'severity', 'title', 'relatedIds', 'file', 'line', 'evidence', 'impact', 'remediation'],
        properties: {
          id: { type: 'string', pattern: '^F-[0-9]{3}$' }, severity: { enum: ['P0', 'P1', 'P2'] },
          title: { type: 'string', minLength: 1 }, relatedIds: { type: 'array', items: { type: 'string' }, uniqueItems: true },
          file: { type: ['string', 'null'] }, line: { type: ['integer', 'null'], minimum: 1 },
          evidence: { type: 'string', minLength: 1 }, impact: { type: 'string', minLength: 1 }, remediation: { type: 'string', minLength: 1 },
        },
      },
    },
    suggestedTests: { type: 'array', items: { type: 'string' } },
    repairInstructions: { type: 'array', items: { type: 'string' } },
  },
});

function nonempty(value) { return typeof value === 'string' && value.trim().length > 0; }
function policyForbidden(filePath) {
  try { normalizeBaselineInputPath(filePath); return false; }
  catch { return true; }
}
function exactKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && stableJson(Object.keys(value).sort()) === stableJson([...keys].sort());
}
function sameSet(left, right) {
  return left.length === right.length && [...left].sort().every((entry, index) => entry === [...right].sort()[index]);
}

export function validateReview(value, frozen, expectedReviewer, expectedKind) {
  const topKeys = ['status', 'reviewer', 'reviewerKind', 'isolation', 'checkedAcceptanceIds', 'counts', 'findings', 'suggestedTests', 'repairInstructions'];
  const humanReview = value?.reviewerKind === 'human-review';
  const validIdentity = humanReview
    ? value.status === 'NEED_HUMAN_REVIEW' && value.reviewer === null && value.isolation === 'not-available'
    : isAgentRuntime(value?.reviewer) && ['independent-agent', 'fresh-subagent'].includes(value.reviewerKind)
      && value.isolation === 'fresh-read-only-no-development-context';
  const validTop = exactKeys(value, topKeys)
    && ['PASS', 'FAIL', 'NEED_HUMAN_REVIEW'].includes(value.status)
    && validIdentity
    && (expectedReviewer === undefined || value.reviewer === expectedReviewer)
    && (expectedKind === undefined || value.reviewerKind === expectedKind)
    && Array.isArray(value.checkedAcceptanceIds) && Array.isArray(value.findings)
    && Array.isArray(value.suggestedTests) && value.suggestedTests.every(nonempty)
    && Array.isArray(value.repairInstructions) && value.repairInstructions.every(nonempty)
    && exactKeys(value.counts, ['p0', 'p1', 'p2'])
    && Object.values(value.counts).every((count) => Number.isInteger(count) && count >= 0);
  if (!validTop) throw new GatedLoopError('REVIEW_INVALID', 'Reviewer result does not match the acceptance schema');
  const acceptanceIds = frozen.acceptance.map((entry) => entry.id);
  if (new Set(value.checkedAcceptanceIds).size !== value.checkedAcceptanceIds.length
      || value.checkedAcceptanceIds.some((id) => !acceptanceIds.includes(id))) {
    throw new GatedLoopError('REVIEW_INVALID', 'Reviewer checkedAcceptanceIds are invalid');
  }
  if (value.status !== 'NEED_HUMAN_REVIEW' && !sameSet(value.checkedAcceptanceIds, acceptanceIds)) {
    throw new GatedLoopError('REVIEW_INVALID', 'PASS or FAIL must cover every frozen acceptance ID');
  }
  const allowedRelated = new Set(['SAFETY']);
  for (const entry of frozen.acceptance) {
    allowedRelated.add(entry.id); for (const id of entry.requirementIds) allowedRelated.add(id);
  }
  for (const entry of frozen.tasks) {
    allowedRelated.add(entry.id); for (const id of entry.requirementIds) allowedRelated.add(id); for (const id of entry.acceptanceIds) allowedRelated.add(id);
  }
  const findingKeys = ['id', 'severity', 'title', 'relatedIds', 'file', 'line', 'evidence', 'impact', 'remediation'];
  const ids = new Set(); const actual = { p0: 0, p1: 0, p2: 0 };
  for (const finding of value.findings) {
    const valid = exactKeys(finding, findingKeys) && /^F-\d{3}$/.test(finding.id) && ['P0', 'P1', 'P2'].includes(finding.severity)
      && nonempty(finding.title) && Array.isArray(finding.relatedIds) && new Set(finding.relatedIds).size === finding.relatedIds.length
      && finding.relatedIds.every((id) => allowedRelated.has(id))
      && (finding.file === null || nonempty(finding.file))
      && (finding.line === null || (Number.isInteger(finding.line) && finding.line >= 1))
      && nonempty(finding.evidence) && nonempty(finding.impact) && nonempty(finding.remediation);
    if (!valid || ids.has(finding.id) || (['P0', 'P1'].includes(finding.severity) && finding.relatedIds.length === 0)) {
      throw new GatedLoopError('REVIEW_INVALID', 'Reviewer finding is invalid or untraceable');
    }
    ids.add(finding.id); actual[finding.severity.toLowerCase()]++;
  }
  if (stableJson(actual) !== stableJson(value.counts)) throw new GatedLoopError('REVIEW_INVALID', 'Reviewer severity counts do not match findings');
  if (value.status === 'PASS' && (actual.p0 > 0 || actual.p1 > 0)) throw new GatedLoopError('REVIEW_INVALID', 'PASS cannot contain P0 or P1 findings');
  if (value.status === 'FAIL' && actual.p0 + actual.p1 === 0) throw new GatedLoopError('REVIEW_INVALID', 'FAIL requires at least one P0 or P1 finding');
  if (value.status === 'NEED_HUMAN_REVIEW' && value.repairInstructions.length === 0) {
    throw new GatedLoopError('REVIEW_INVALID', 'NEED_HUMAN_REVIEW must explain the missing evidence or isolation');
  }
  return structuredClone(value);
}

function reviewerPrompt(frozen, evidence, report, diffText) {
  return `你是与开发者分离的全新只读验收 Agent，不得继承需求分析或开发会话上下文。若你是宿主创建的全新子 Agent，reviewerKind 输出 fresh-subagent；否则输出 independent-agent。isolation 必须输出 fresh-read-only-no-development-context。不得修改文件、修复代码、提交、推送、合并或发布。
根据冻结授权审查最终改动，逐项检查全部验收 ID，并检查边界、异常、权限、安全、数据、兼容性、并发和测试充分性。
按给定 JSON schema 输出。P0/P1 必须 FAIL；只有 P2 可以 PASS；证据、隔离或归属不足时 NEED_HUMAN_REVIEW。

# 冻结授权（${frozen.authorityName}）
${frozen.authority}

# acceptance.json
${JSON.stringify(frozen.acceptance, null, 2)}

# tasks.json
${JSON.stringify(frozen.tasks, null, 2)}

# gate-evidence.json
${JSON.stringify(evidence, null, 2)}

# self-check-report.md
${report}

# 最终真实 diff
${diffText}
`;
}

async function invokeCodex({ command, prompt, fs, runProcessImpl, timeoutMs }) {
  const temporary = await fs.mkdtemp(path.join(tmpdir(), 'gated-loop-review-'));
  const schemaPath = path.join(temporary, 'schema.json');
  const outputPath = path.join(temporary, 'review.json');
  try {
    await fs.writeFile(schemaPath, json(REVIEW_SCHEMA), { flag: 'wx' });
    await runProcessImpl(command, [
      'exec', '--sandbox', 'read-only', '--ephemeral', '--ignore-rules', '--skip-git-repo-check', '--color', 'never',
      '--output-schema', schemaPath, '--output-last-message', outputPath, '-C', temporary, '-',
    ], { cwd: temporary, timeoutMs, captureOutput: true, input: prompt });
    return JSON.parse(await fs.readFile(outputPath, 'utf8'));
  } finally { await fs.rm(temporary, { recursive: true, force: true }).catch(() => {}); }
}

async function invokeClaude({ command, prompt, fs, runProcessImpl, timeoutMs }) {
  const temporary = await fs.mkdtemp(path.join(tmpdir(), 'gated-loop-review-'));
  try {
    const result = await runProcessImpl(command, [
      '-p', '--safe-mode', '--no-session-persistence', '--permission-mode', 'plan',
      '--tools', '', '--output-format', 'json', '--json-schema', JSON.stringify(REVIEW_SCHEMA),
    ], { cwd: temporary, timeoutMs, captureOutput: true, input: prompt });
    const parsed = JSON.parse(result.stdout);
    return parsed.structured_output ?? parsed.result ?? parsed;
  } finally { await fs.rm(temporary, { recursive: true, force: true }).catch(() => {}); }
}

function unavailable(error) { return error?.code === 'PROCESS_SPAWN_FAILED' && error.details?.causeCode === 'ENOENT'; }

async function autoReview({ preference, prompt, config, fs, runProcessImpl, timeoutMs }) {
  if (preference === 'codex') return { reviewer: 'codex', reviewerKind: 'independent-agent', value: await invokeCodex({ command: config.tools.codex, prompt, fs, runProcessImpl, timeoutMs }) };
  if (preference === 'claude') return { reviewer: 'claude', reviewerKind: 'independent-agent', value: await invokeClaude({ command: config.tools.claude, prompt, fs, runProcessImpl, timeoutMs }) };
  try { return { reviewer: 'codex', reviewerKind: 'independent-agent', value: await invokeCodex({ command: config.tools.codex, prompt, fs, runProcessImpl, timeoutMs }) }; }
  catch (error) {
    if (!unavailable(error)) throw error;
    return { reviewer: 'claude', reviewerKind: 'independent-agent', value: await invokeClaude({ command: config.tools.claude, prompt, fs, runProcessImpl, timeoutMs }) };
  }
}

function initialReviewPlan({ reviewer, reviewResult, reviewerInvoker }) {
  if (reviewResult) return {
    schemaVersion: 1, requested: 'review-result', route: 'provided-result', status: 'PLANNED',
    selectedReviewer: reviewResult.reviewer ?? null, reviewerKind: reviewResult.reviewerKind ?? null,
    isolation: reviewResult.isolation ?? null, reason: '宿主提供了已完成的结构化验收结果。',
  };
  if (reviewer === 'human') return {
    schemaVersion: 1, requested: 'human', route: 'human', status: 'PLANNED', selectedReviewer: null,
    reviewerKind: 'human-review', isolation: 'not-available', reason: '用户明确选择人工语义验收。',
  };
  if (reviewerInvoker) return {
    schemaVersion: 1, requested: reviewer ?? 'host-capability', route: 'host-agent', status: 'PLANNED',
    selectedReviewer: null, reviewerKind: null, isolation: 'fresh-read-only-no-development-context',
    reason: '宿主提供了可创建全新独立 Agent 或子 Agent 的验收能力。',
  };
  if (reviewer === 'codex' || reviewer === 'claude') return {
    schemaVersion: 1, requested: reviewer, route: 'external-cli', status: 'PLANNED', selectedReviewer: reviewer,
    reviewerKind: 'independent-agent', isolation: 'fresh-read-only-no-development-context',
    reason: `用户明确选择可选的 ${reviewer} CLI 验收适配器。`,
  };
  if (reviewer === 'auto') return {
    schemaVersion: 1, requested: 'auto', route: 'external-cli-auto', status: 'PLANNED', selectedReviewer: null,
    reviewerKind: 'independent-agent', isolation: 'fresh-read-only-no-development-context',
    reason: '用户明确允许 CLI 按 Codex、Claude 的顺序探测可选验收适配器。',
  };
  return {
    schemaVersion: 1, requested: 'default', route: 'human', status: 'PLANNED', selectedReviewer: null,
    reviewerKind: 'human-review', isolation: 'not-available',
    reason: '未提供隔离验收能力；默认不扫描或启动外部 Agent，转入人工语义验收。',
  };
}

function completedReviewPlan(plan, invoked) {
  return {
    ...plan, status: 'COMPLETED', selectedReviewer: invoked.reviewer,
    reviewerKind: invoked.reviewerKind, isolation: invoked.value.isolation,
    reason: '已取得并校验全新只读无开发上下文的语义验收结果。',
  };
}

function failedReviewPlan(plan, error, stage) {
  const reason = `${error.code ?? 'ACCEPTANCE_FAILED'}：${error.message ?? '语义验收无法完成'}`;
  return { ...plan, status: stage === 'evidence' ? 'BLOCKED' : 'UNAVAILABLE', reason };
}

function fallbackReview(reason) {
  return {
    status: 'NEED_HUMAN_REVIEW', reviewer: null, reviewerKind: 'human-review', isolation: 'not-available', checkedAcceptanceIds: [],
    counts: { p0: 0, p1: 0, p2: 0 }, findings: [], suggestedTests: [], repairInstructions: [reason],
  };
}

function findingSection(findings, severity) {
  const selected = findings.filter((entry) => entry.severity === severity);
  if (selected.length === 0) return '- 无';
  return selected.map((entry) => {
    const location = entry.file ? `${entry.file}${entry.line ? `:${entry.line}` : ''}` : '无固定位置';
    return `- **${entry.id} ${entry.title}**（${entry.relatedIds.join('、') || '无关联 ID'}；${location}）\n  - 证据：${entry.evidence}\n  - 影响：${entry.impact}\n  - 修复：${entry.remediation}`;
  }).join('\n');
}

function renderAcceptance(task, round, review) {
  const tests = review.suggestedTests.length > 0 ? review.suggestedTests.map((entry) => `- ${entry}`).join('\n') : '- 无';
  const repairs = review.repairInstructions.length > 0 ? review.repairInstructions.map((entry) => `- ${entry}`).join('\n') : '- 无需修复';
  const title = review.reviewerKind === 'human-review' ? '人工语义验收待办报告' : '独立语义验收报告';
  const reviewer = review.reviewer ?? '未启动（人工验收）';
  return `# ${task} ${round} ${title}

## 结论
${review.status}

## 审查身份
- reviewer: ${reviewer}
- reviewerKind: ${review.reviewerKind}
- isolation: ${review.isolation}

## 严重级别汇总
| P0 | P1 | P2 |
| ---: | ---: | ---: |
| ${review.counts.p0} | ${review.counts.p1} | ${review.counts.p2} |

## 已检查内容
- 验收 ID：${review.checkedAcceptanceIds.join('、') || '未完成'}
- 机械自检：[self-check-report.md](self-check-report.md)
- 测试证据：[gate-evidence.json](gate-evidence.json)

## P0 严重问题
${findingSection(review.findings, 'P0')}

## P1 阻断问题
${findingSection(review.findings, 'P1')}

## P2 非阻断建议
${findingSection(review.findings, 'P2')}

## 建议补充测试
${tests}

## 给开发 Agent 的修复指令
${repairs}
`;
}

function manualAcceptanceStatus(status) {
  if (status === 'PASS') return 'WAITING_FOR_MANUAL_ACCEPTANCE';
  if (status === 'FAIL') return 'BLOCKED_BY_P0_P1';
  return 'NEED_HUMAN_REVIEW';
}

function renderFinalAcceptance(task, round, frozen, review, verified) {
  const roundPath = `rounds/${round}`;
  const authority = frozen.authorityName;
  const tests = review.suggestedTests.length > 0 ? review.suggestedTests.map((entry) => `- ${entry}`).join('\n') : '- 无';
  const repairs = review.repairInstructions.length > 0 ? review.repairInstructions.map((entry) => `- ${entry}`).join('\n') : '- 无需修复';
  const reviewer = review.reviewer ?? '未启动（人工验收）';
  const operation = review.status === 'PASS'
    ? '独立验收已通过，等待用户人工确认。PASS 不授权自动提交、推送、合并或发布。'
    : review.status === 'FAIL'
      ? '存在 P0/P1 阻断项，修复并重新完成机械门禁和独立验收前，不能进入人工完成确认。'
      : review.reviewerKind === 'human-review'
        ? '机械门禁不因此失效，但尚未完成独立语义验收。请由用户人工审查冻结验收项、真实 diff、机械证据和本报告；不得把此状态表述为独立验收 PASS。'
        : '证据、隔离或审查过程不足，需要人工审查后决定重试、修复或终止。';
  return `# ${task} 最终验收报告

> 当前验收结论：**${review.status}**
>
> 当前验收轮次：**${round}**
>
> 人工确认状态：**${manualAcceptanceStatus(review.status)}**

## 验收摘要

| 项目 | 结果 |
| --- | --- |
| 任务模式 | ${frozen.mode} |
| 机械门禁 | ${verified?.evidence?.status ?? 'UNVERIFIED'} |
| 独立审查者 | ${reviewer} |
| 审查者类型 | ${review.reviewerKind} |
| 上下文隔离 | ${review.isolation} |
| P0 / P1 / P2 | ${review.counts.p0} / ${review.counts.p1} / ${review.counts.p2} |
| 已检查验收 ID | ${review.checkedAcceptanceIds.join('、') || '未完成'} |

## P0 严重问题
${findingSection(review.findings, 'P0')}

## P1 阻断问题
${findingSection(review.findings, 'P1')}

## P2 非阻断建议
${findingSection(review.findings, 'P2')}

## 建议补充测试
${tests}

## 修复指令
${repairs}

## 人工操作结论

${operation}

## 证据导航

- 冻结授权：[${authority}](${authority})
- 开发总览：[development-overview.md](development-overview.md)
- 开发进度：[progress.md](progress.md)
- 本轮机械自检：[self-check-report.md](${roundPath}/self-check-report.md)
- 本轮机械证据：[gate-evidence.json](${roundPath}/gate-evidence.json)
- 本轮验收路由：[review-plan.json](${roundPath}/review-plan.json)
- 本轮语义验收：[acceptance-report.md](${roundPath}/acceptance-report.md)
- 本轮结构化审查：[review.json](${roundPath}/review.json)

本文件由 \`gated-loop accept\` 根据当前轮次的已校验结果自动刷新。轮次报告与 JSON 是原始证据，本文件是给人工查看的最新汇总入口。
`;
}

async function verifiedEvidence({ root, task, round, frozen, config, snapshotSource, fs, runProcessImpl, timeoutMs }) {
  const relative = path.join('.ai-dev-loop', task, 'rounds', round);
  const evidence = JSON.parse((await readSafeRegularFile(root, path.join(relative, 'gate-evidence.json'), { fs })).toString('utf8'));
  const { evidenceFingerprint, ...unsigned } = evidence;
  if (evidence.status !== 'PASS' || evidence.task !== task || evidence.round !== round
      || evidence.frozenFingerprint !== frozen.frozenFingerprint || evidenceFingerprint !== fingerprint(unsigned)) {
    throw new GatedLoopError('SELF_CHECK_NOT_PASS', 'A valid PASS self-check is required before acceptance');
  }
  const snapshot = await readSnapshot({ root, task, round, source: snapshotSource, frozen, fs });
  if (snapshot.schemaVersion === 2) {
    const plan = await loadWorkspacePlan({ root, task, round, snapshot, frozen, fs });
    const inspections = [];
    for (const workspace of plan) {
      const inspection = await inspectWorkspace({
        coordinatorRoot: root, task, workspace, git: config.tools.git,
        protectedPaths: config.protectedPaths, forbiddenPaths: config.forbiddenPaths,
        isPolicyForbidden: policyForbidden, runProcessImpl, timeoutMs, fs,
      });
      if (inspection.repository.head !== workspace.baseCommit
          || inspection.repository.branch !== workspace.branch
          || inspection.protectedChanged.length > 0 || inspection.forbiddenChanged.length > 0
          || inspection.outOfScope.length > 0 || inspection.ambiguous.length > 0
          || inspection.changed.length === 0 || inspection.diffBundle.truncated) {
        throw new GatedLoopError('ACCEPTANCE_EVIDENCE_CHANGED', `Workspace evidence changed after self-check: ${workspace.id}`);
      }
      inspections.push(inspection);
    }
    const bundle = aggregateDiffBundles(inspections);
    const currentPaths = inspections.flatMap((inspection) => inspection.changed.map((entry) => ({
      workspaceId: inspection.workspace.id, path: entry.path,
    }))).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId) || left.path.localeCompare(right.path));
    const currentWorkspaces = inspections.map((inspection) => ({
      workspaceId: inspection.workspace.id,
      headCommit: inspection.repository.head,
      currentBranch: inspection.repository.branch,
      changedFiles: inspection.changed.map((entry) => entry.path).sort(),
      diffSha256: inspection.diffBundle.sha256,
    })).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId));
    const evidenceWorkspaces = [...(evidence.workspaces ?? [])].map((entry) => ({
      workspaceId: entry.workspaceId,
      headCommit: entry.headCommit,
      currentBranch: entry.currentBranch,
      changedFiles: entry.changedFiles,
      diffSha256: entry.diffSha256,
    })).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId));
    if (bundle.truncated || bundle.sha256 !== evidence.diffSha256
        || stableJson(currentPaths) !== stableJson(evidence.changedFiles)
        || stableJson(currentWorkspaces) !== stableJson(evidenceWorkspaces)) {
      throw new GatedLoopError('ACCEPTANCE_EVIDENCE_CHANGED', 'Multi-workspace evidence changed after self-check');
    }
    const report = (await readSafeRegularFile(root, path.join(relative, 'self-check-report.md'), { fs })).toString('utf8');
    if (evidence.reportFingerprint !== fingerprint({ text: report })) {
      throw new GatedLoopError('ACCEPTANCE_EVIDENCE_CHANGED', 'Self-check report changed after the mechanical gate');
    }
    return { evidence, report, bundle };
  }
  const repository = await currentStatus({ root, git: config.tools.git, runProcessImpl, timeoutMs });
  const runtimePrefix = `.ai-dev-loop/${task}/`;
  const relevant = repository.entries.filter((entry) => !entry.path.startsWith(runtimePrefix));
  const forbiddenChanged = relevant.filter((entry) => matchesAny(entry.path, config.forbiddenPaths) || policyForbidden(entry.path));
  if (forbiddenChanged.length > 0) {
    throw new GatedLoopError('ACCEPTANCE_EVIDENCE_CHANGED', 'Sensitive changed paths prevent independent acceptance');
  }
  const enriched = await enrichStatus(root, relevant, {
    fs, skipPatterns: config.forbiddenPaths, skipPaths: forbiddenChanged.map((entry) => entry.path),
  });
  const attributed = attributeChanges(enriched, snapshot);
  const bundle = await buildDiffBundle({ root, git: config.tools.git, changed: attributed.changed, runProcessImpl, timeoutMs, fs });
  const currentPaths = attributed.changed.map((entry) => entry.path).sort();
  if (repository.head !== evidence.headCommit || attributed.ambiguous.length > 0 || bundle.truncated
      || !sameSet(currentPaths, evidence.changedFiles) || bundle.sha256 !== evidence.diffSha256) {
    throw new GatedLoopError('ACCEPTANCE_EVIDENCE_CHANGED', 'Repository evidence changed after self-check');
  }
  const report = (await readSafeRegularFile(root, path.join(relative, 'self-check-report.md'), { fs })).toString('utf8');
  if (evidence.reportFingerprint !== fingerprint({ text: report })) {
    throw new GatedLoopError('ACCEPTANCE_EVIDENCE_CHANGED', 'Self-check report changed after the mechanical gate');
  }
  return { evidence, report, bundle };
}

export async function runAcceptance({
  root, task, round: suppliedRound, snapshot: snapshotSource, reviewer, reviewResult,
  timeoutMs = 300_000, fs = fsPromises, runProcessImpl = runProcess, reviewerInvoker,
} = {}) {
  const round = normalizeRound(suppliedRound);
  const frozen = await loadFrozenTask({ root, task, fs });
  const config = await loadConfig(root);
  const directory = await roundDirectory({ root, task, round, fs });
  let plan = initialReviewPlan({ reviewer, reviewResult, reviewerInvoker });
  let planPath = await writeRoundFile(directory, 'review-plan.json', json(plan), { fs });
  let stage = 'evidence';
  let verified;
  let review;
  try {
    verified = await verifiedEvidence({ root, task, round, frozen, config, snapshotSource, fs, runProcessImpl, timeoutMs });
    stage = 'review';
    const prompt = reviewerPrompt(frozen, verified.evidence, verified.report, verified.bundle.text);
    let invoked;
    if (reviewResult) invoked = { reviewer: reviewResult.reviewer, reviewerKind: reviewResult.reviewerKind, value: reviewResult };
    else if (reviewer === 'human' || (!reviewer && !reviewerInvoker)) {
      throw new GatedLoopError('INDEPENDENT_REVIEW_UNAVAILABLE', '未提供全新隔离 Agent 或子 Agent；已转入人工语义验收');
    }
    else if (reviewerInvoker) invoked = await reviewerInvoker({ prompt, schema: REVIEW_SCHEMA, preference: reviewer });
    else invoked = await autoReview({ preference: reviewer, prompt, config, fs, runProcessImpl, timeoutMs });
    review = validateReview(invoked.value, frozen, invoked.reviewer, invoked.reviewerKind);
    plan = completedReviewPlan(plan, invoked);
  } catch (error) {
    plan = failedReviewPlan(plan, error, stage);
    review = fallbackReview(plan.reason);
  }
  planPath = await writeRoundFile(directory, 'review-plan.json', json(plan), { fs });
  const reviewPath = await writeRoundFile(directory, 'review.json', json(review), { fs });
  const reportPath = await writeRoundFile(directory, 'acceptance-report.md', renderAcceptance(task, round, review), { fs });
  const finalReportPath = await writeRoundFile(
    frozen.taskPackage.target,
    'final-acceptance-report.md',
    renderFinalAcceptance(task, round, frozen, review, verified),
    { fs },
  );
  return { status: review.status, task, round, reviewer: review.reviewer, counts: review.counts, planPath, reviewPath, reportPath, finalReportPath };
}
