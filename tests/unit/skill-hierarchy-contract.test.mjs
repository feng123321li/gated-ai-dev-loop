import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const skillUrl = new URL('../../skills/hierarchical-delivery-governance/SKILL.md', import.meta.url);
const interfaceUrl = new URL('../../skills/hierarchical-delivery-governance/agents/openai.yaml', import.meta.url);

test('Skill defines one stable hierarchy and a non-keyword self-hosting boundary', async () => {
  const skill = await readFile(skillUrl, 'utf8');

  assert.match(skill, /Delivery → Capability → Task/);
  assert.match(skill, /完整项目、大型模块、子系统或跨服务需求/);
  assert.match(skill, /不表示必须覆盖整个代码仓库或完整产品/);
  assert.doesNotMatch(skill, /用于把完整项目拆为/);
  assert.doesNotMatch(skill, /项目门禁|项目级测试/);
  assert.match(skill, /SELF_HOSTING_MAINTENANCE/);
  assert.match(skill, /不是 dogfood 授权/);
  assert.match(skill, /work-item-registry\.json/);
  assert.match(skill, /work-items\//);
  assert.match(skill, /不读取、迁移或回写其他历史控制目录/);
  assert.match(skill, /`Workstream`、`M-NNN\/W-NNN\/T-NNN` 不再是新流程实体/);
  assert.match(skill, /规范 Skill 名是 `hierarchical-delivery-governance`/);
  assert.match(skill, /不追加 `v2`/);
  assert.doesNotMatch(skill, /gated-ai-dev-loop/);
  assert.match(skill, /\.hierarchical-delivery-governance/);
  assert.match(skill, /WAITING_FOR_INDEPENDENT_REVIEW/);
  assert.match(skill, /retry-item/);
  assert.match(skill, /真实、hash 匹配、结构合法且不可复用的 evidence/);
});

test('Skill interface drafts broad delivery units without authorizing persistence or freeze', async () => {
  const agentInterface = await readFile(interfaceUrl, 'utf8');

  assert.match(agentInterface, /分层式 AI 交付治理/);
  assert.match(agentInterface, /项目或大型模块/);
  assert.match(agentInterface, /起草 Delivery→Capability→Task/);
  assert.match(agentInterface, /明确批准具体 ID、内容及持久化或冻结动作前，不创建或冻结工作项/);
  assert.doesNotMatch(agentInterface, /并为每一级冻结独立 baseline/);
});
