import * as fsPromises from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';

import { loadConfig } from '../config/load-config.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { readSafeRegularFile } from '../core/fs-safe.mjs';
import { runProcess } from '../core/process.mjs';
import { normalizeBaselineInputPath } from '../baseline/sources.mjs';
import {
  attributeChanges, buildDiffBundle, currentStatus, enrichStatus, fingerprint, json,
  loadFrozenTask, matchesAny, normalizeRound, readSnapshot, roundDirectory, stableJson,
  writeRoundFile,
} from './common.mjs';

export const REVIEW_SCHEMA = Object.freeze({
  type: 'object',
  additionalProperties: false,
  required: ['status', 'reviewer', 'reviewerKind', 'isolation', 'checkedAcceptanceIds', 'counts', 'findings', 'suggestedTests', 'repairInstructions'],
  properties: {
    status: { enum: ['PASS', 'FAIL', 'NEED_HUMAN_REVIEW'] },
    reviewer: { enum: ['codex', 'claude'] },
    reviewerKind: { enum: ['independent-agent', 'fresh-subagent'] },
    isolation: { const: 'fresh-read-only-no-development-context' },
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
  const validTop = exactKeys(value, topKeys)
    && ['PASS', 'FAIL', 'NEED_HUMAN_REVIEW'].includes(value.status)
    && ['codex', 'claude'].includes(value.reviewer)
    && (!expectedReviewer || value.reviewer === expectedReviewer)
    && ['independent-agent', 'fresh-subagent'].includes(value.reviewerKind)
    && (!expectedKind || value.reviewerKind === expectedKind)
    && value.isolation === 'fresh-read-only-no-development-context'
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
  return `你是与开发者分离的其他 Agent，是全新且只读的独立验收者，不得继承需求分析或开发会话上下文。reviewerKind 必须输出 independent-agent，isolation 必须输出 fresh-read-only-no-development-context。不得修改文件、修复代码、提交、推送、合并或发布。
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

async function autoReview({ root, preference, prompt, config, fs, runProcessImpl, timeoutMs }) {
  if (preference === 'codex') return { reviewer: 'codex', reviewerKind: 'independent-agent', value: await invokeCodex({ command: config.tools.codex, prompt, fs, runProcessImpl, timeoutMs }) };
  if (preference === 'claude') return { reviewer: 'claude', reviewerKind: 'independent-agent', value: await invokeClaude({ command: config.tools.claude, prompt, fs, runProcessImpl, timeoutMs }) };
  try { return { reviewer: 'codex', reviewerKind: 'independent-agent', value: await invokeCodex({ command: config.tools.codex, prompt, fs, runProcessImpl, timeoutMs }) }; }
  catch (error) {
    if (!unavailable(error)) throw error;
    return { reviewer: 'claude', reviewerKind: 'independent-agent', value: await invokeClaude({ command: config.tools.claude, prompt, fs, runProcessImpl, timeoutMs }) };
  }
}

function fallbackReview(reviewer, reviewerKind, reason) {
  return {
    status: 'NEED_HUMAN_REVIEW', reviewer, reviewerKind, isolation: 'fresh-read-only-no-development-context', checkedAcceptanceIds: [],
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
  return `# ${task} ${round} 独立验收报告

## 结论
${review.status}

## 审查身份
- reviewer: ${review.reviewer}
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

async function verifiedEvidence({ root, task, round, frozen, config, snapshotSource, fs, runProcessImpl, timeoutMs }) {
  const relative = path.join('.ai-dev-loop', task, 'rounds', round);
  const evidence = JSON.parse((await readSafeRegularFile(root, path.join(relative, 'gate-evidence.json'), { fs })).toString('utf8'));
  const { evidenceFingerprint, ...unsigned } = evidence;
  if (evidence.status !== 'PASS' || evidence.task !== task || evidence.round !== round
      || evidence.frozenFingerprint !== frozen.frozenFingerprint || evidenceFingerprint !== fingerprint(unsigned)) {
    throw new GatedLoopError('SELF_CHECK_NOT_PASS', 'A valid PASS self-check is required before acceptance');
  }
  const snapshot = await readSnapshot({ root, task, round, source: snapshotSource, frozen, fs });
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
  root, task, round: suppliedRound, snapshot: snapshotSource, reviewer = 'auto', reviewResult,
  timeoutMs = 300_000, fs = fsPromises, runProcessImpl = runProcess, reviewerInvoker,
} = {}) {
  const round = normalizeRound(suppliedRound);
  const frozen = await loadFrozenTask({ root, task, fs });
  const config = await loadConfig(root);
  const directory = await roundDirectory({ root, task, round, fs });
  let actualReviewer = reviewer === 'claude' ? 'claude' : 'codex';
  let reviewerKind = 'independent-agent';
  let review;
  try {
    const verified = await verifiedEvidence({ root, task, round, frozen, config, snapshotSource, fs, runProcessImpl, timeoutMs });
    const prompt = reviewerPrompt(frozen, verified.evidence, verified.report, verified.bundle.text);
    let invoked;
    if (reviewResult) invoked = { reviewer: reviewResult.reviewer, reviewerKind: reviewResult.reviewerKind, value: reviewResult };
    else if (reviewerInvoker) invoked = await reviewerInvoker({ prompt, schema: REVIEW_SCHEMA, preference: reviewer });
    else invoked = await autoReview({ root, preference: reviewer, prompt, config, fs, runProcessImpl, timeoutMs });
    actualReviewer = invoked.reviewer;
    reviewerKind = invoked.reviewerKind;
    review = validateReview(invoked.value, frozen, actualReviewer, reviewerKind);
  } catch (error) {
    review = fallbackReview(actualReviewer, reviewerKind, `${error.code ?? 'ACCEPTANCE_FAILED'}：${error.message ?? '独立验收无法完成'}`);
  }
  const reviewPath = await writeRoundFile(directory, 'review.json', json(review), { fs });
  const reportPath = await writeRoundFile(directory, 'acceptance-report.md', renderAcceptance(task, round, review), { fs });
  return { status: review.status, task, round, reviewer: review.reviewer, counts: review.counts, reviewPath, reportPath };
}
