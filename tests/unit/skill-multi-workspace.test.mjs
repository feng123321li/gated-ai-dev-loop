import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const skillPath = new URL('../../skills/hierarchical-delivery-governance/SKILL.md', import.meta.url);
const referencePath = new URL('../../skills/hierarchical-delivery-governance/references/multi-workspace.md', import.meta.url);

test('cross-workspace handoff fails closed before Task context generation', async () => {
  const [skill, reference] = await Promise.all([
    readFile(skillPath, 'utf8'),
    readFile(referencePath, 'utf8'),
  ]);

  assert.match(reference, /WAITING_FOR_WORKSPACE_AUTHORIZATION/);
  assert.match(reference, /不得生成一个宿主已知必然阻塞的交接/);
  assert.match(reference, /workspaceId/);
  assert.match(reference, /覆盖结论 `PASS`/);
  assert.match(skill, /跨仓库工作只选择一个协调根/);
});

test('multi-workspace contracts define repository gates and dependency waves', async () => {
  const reference = await readFile(referencePath, 'utf8');

  assert.match(reference, /提供方契约 Task 必须先完成并机械验证，再启动消费方 Task/);
  assert.match(reference, /机械门禁先逐工作区，再整体聚合/);
  assert.match(reference, /后置工作区测试记录为 `BLOCKED`/);
  assert.match(reference, /"workspaceId": "provider-service"/);

  const examples = [...reference.matchAll(/```json\n([\s\S]*?)\n```/g)].map((match) => match[1]);
  assert.ok(examples.length >= 4);
  for (const example of examples) assert.doesNotThrow(() => JSON.parse(example));
});
