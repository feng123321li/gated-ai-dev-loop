import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const skillUrl = new URL('../../skills/hierarchical-delivery-governance/SKILL.md', import.meta.url);
const interfaceUrl = new URL('../../skills/hierarchical-delivery-governance/agents/openai.yaml', import.meta.url);
const maintenanceUrl = new URL('../../AGENTS.md', import.meta.url);

test('Skill defines one stable hierarchy without repository-maintenance instructions', async () => {
  const skill = await readFile(skillUrl, 'utf8');

  assert.match(skill, /Delivery → Capability → Task/);
  assert.match(skill, /独立 `Task`、`Capability → Task` 或完整 `Delivery → Capability → Task`/);
  assert.match(skill, /完整项目、大型模块、子系统或跨服务需求/);
  assert.match(skill, /不表示必须覆盖整个代码仓库或完整产品/);
  assert.doesNotMatch(skill, /用于把完整项目拆为/);
  assert.doesNotMatch(skill, /项目门禁|项目级测试/);
  assert.doesNotMatch(skill, /SELF_HOSTING_MAINTENANCE|dogfood|自举维护|维护本仓库/);
  assert.match(skill, /work-item-registry\.json/);
  assert.match(skill, /work-items\//);
  assert.match(skill, /不读取、迁移或回写其他历史控制目录/);
  assert.match(skill, /`Micro`、`Workstream`、`M-NNN\/W-NNN\/T-NNN` 不进入 `kind` 枚举/);
  assert.match(skill, /它们不拥有 baseline、claim 或 gate/);
  assert.match(skill, /\| 类型 \| 权限性质 \| 作用 \| 子级 \|/);
  assert.match(skill, /交付（`DELIVERY`） \| 协调/);
  assert.match(skill, /任务（`TASK`） \| 执行/);
  assert.doesNotMatch(skill, /\| Kind \| Authority \|/);
  assert.doesNotMatch(skill, /gated-ai-dev-loop/);
  assert.match(skill, /\.hierarchical-delivery-governance/);
  assert.match(skill, /WAITING_FOR_INDEPENDENT_REVIEW/);
  assert.match(skill, /WAITING_FOR_DEVELOPMENT_MODE_SELECTION/);
  assert.match(skill, /development-mode\.json/);
  assert.match(skill, /scripts\/hdg\.mjs/);
  assert.match(skill, /全局 `hdg` 只是可选快捷别名，不是前置条件/);
  assert.match(skill, /schema v3/);
  assert.match(skill, /upgrade-registry/);
  assert.match(skill, /migrationHistory/);
  assert.match(skill, /不得静默改写/);
  assert.match(skill, /`gateLevel` 必须是 `LIGHT` 或 `FULL`/);
  assert.match(skill, /根 `TASK → CAPABILITY` 或根 `CAPABILITY → DELIVERY`/);
  assert.match(skill, /promotionHistory/);
  assert.match(skill, /不导入历史 `route\/start\/prepare\/freeze` CLI/);
  assert.match(skill, /确认 baseline.*不能.*开发方式|baseline.*开发方式/);
  assert.match(skill, /retry-item/);
  assert.match(skill, /真实、hash 匹配、结构合法且不可复用的 evidence/);
  assert.match(skill, /dispatch-task → task-result → accept-item → acceptance-item|dispatch-task.*task-result.*accept-item.*acceptance-item/s);
  assert.match(skill, /acceptance-report\.md/);
  assert.match(skill, /面向用户与协作者的中文工作台/);
  assert.match(skill, /根 Task、根 Capability、Delivery.*COMPLETED/);
  assert.match(skill, /--definition -/);
  assert.match(skill, /直接从 stdin 读取/);
  assert.match(skill, /不得先用 Write 或文件工具写入 `%TEMP%`、`\$TMPDIR` 等系统临时目录/);
  assert.match(skill, /跨卷路径会返回 `PATH_CROSS_VOLUME`/);
  assert.match(skill, /不要把临时输入放进 `.hierarchical-delivery-governance\/` 控制面/);
});

test('repository maintenance constraints live in AGENTS.md instead of the distributed Skill', async () => {
  const maintenance = await readFile(maintenanceUrl, 'utf8');

  assert.match(maintenance, /package\.json\.name.*hierarchical-delivery-governance/);
  assert.match(maintenance, /不为维护工作创建 `.hierarchical-delivery-governance\/\*\*` 运行包/);
  assert.match(maintenance, /只有用户明确要求 dogfood/);
  assert.match(maintenance, /都必须显式携带 `--dogfood`/);
  assert.match(maintenance, /规范 Skill 名为 `hierarchical-delivery-governance`/);
  assert.match(maintenance, /不追加 `v2`/);
});

test('Skill interface uses one baseline approval and emits a reusable manual prompt', async () => {
  const agentInterface = await readFile(interfaceUrl, 'utf8');

  assert.match(agentInterface, /分层式 AI 交付治理/);
  assert.match(agentInterface, /开发回收、门禁、验收报告和最终确认/);
  assert.match(agentInterface, /一次批准冻结 baseline/);
  assert.match(agentInterface, /原子调度 manual\/active 开发/);
  assert.match(agentInterface, /生成用户验收报告/);
  assert.match(agentInterface, /独立验收和用户确认/);
  assert.doesNotMatch(agentInterface, /并为每一级冻结独立 baseline/);
});
