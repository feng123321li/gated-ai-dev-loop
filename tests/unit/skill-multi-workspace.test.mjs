import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const skillPath = new URL('../../skills/gated-ai-dev-loop/SKILL.md', import.meta.url);
const referencePath = new URL('../../skills/gated-ai-dev-loop/references/multi-workspace.md', import.meta.url);

test('跨工作区交接在生成提示词前 fail closed', async () => {
  const [skill, reference] = await Promise.all([
    readFile(skillPath, 'utf8'),
    readFile(referencePath, 'utf8'),
  ]);

  assert.match(skill, /WAITING_FOR_WORKSPACE_AUTHORIZATION/);
  assert.match(skill, /需要写入多个独立工作区、仓库或微服务/);
  assert.match(skill, /只有覆盖结论为 `PASS` 才能创建 `prompt\.md`/);
  assert.match(reference, /不得生成一个宿主已知必然阻塞的交接/);
  assert.match(reference, /workspace-authorization\.json/);
  assert.match(reference, /workspace-coverage\.json/);
  assert.match(reference, /"schemaVersion": 2/);
});

test('多工作区契约定义逐仓库门禁和依赖波次', async () => {
  const reference = await readFile(referencePath, 'utf8');

  assert.match(reference, /先完成并机械验证提供方，再启动消费方/);
  assert.match(reference, /机械门禁先逐工作区、再整体聚合/);
  assert.match(reference, /`gated-loop self-check` 原生识别 schema v2/);
  assert.match(reference, /前置工作区.*后置工作区测试记录为 `BLOCKED`/);
  assert.match(reference, /"workspaceId": "provider-service"/);

  const examples = [...reference.matchAll(/```json\n([\s\S]*?)\n```/g)].map((match) => match[1]);
  assert.ok(examples.length >= 4);
  for (const example of examples) assert.doesNotThrow(() => JSON.parse(example));
});
