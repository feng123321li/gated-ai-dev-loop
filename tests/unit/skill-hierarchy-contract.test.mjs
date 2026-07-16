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
  assert.match(skill, /`gateLevel` 必须是 `LIGHT` 或 `FULL`/);
  assert.match(skill, /根 `TASK → CAPABILITY` 或根 `CAPABILITY → DELIVERY`/);
  assert.match(skill, /promotionHistory/);
  assert.match(skill, /不导入历史 `route\/start\/prepare\/freeze` CLI/);
  assert.match(skill, /确认 baseline.*不能.*开发方式|baseline.*开发方式/);
  assert.match(skill, /retry-item/);
  assert.match(skill, /真实、hash 匹配、结构合法且不可复用的 evidence/);
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
  assert.match(agentInterface, /可独立交付工作单元/);
  assert.match(agentInterface, /根 Task、Capability→Task 或 Delivery→Capability→Task/);
  assert.match(agentInterface, /LIGHT\/FULL/);
  assert.match(agentInterface, /只请求一次覆盖具体 ID、内容以及持久化并冻结的批准/);
  assert.match(agentInterface, /批准前不落盘/);
  assert.match(agentInterface, /manual 模式返回可直接粘贴到新会话的完整提示词/);
  assert.doesNotMatch(agentInterface, /并为每一级冻结独立 baseline/);
});
