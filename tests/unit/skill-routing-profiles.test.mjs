import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const skillPath = new URL('../../skills/hierarchical-delivery-governance/SKILL.md', import.meta.url);
const routingPath = new URL('../../skills/hierarchical-delivery-governance/references/routing-profiles.md', import.meta.url);
const deliveryPlanningPath = new URL('../../skills/hierarchical-delivery-governance/references/delivery-planning.md', import.meta.url);
const trackingPath = new URL('../../skills/hierarchical-delivery-governance/references/tracking.md', import.meta.url);

async function readContracts() {
  return Promise.all([
    readFile(skillPath, 'utf8'),
    readFile(routingPath, 'utf8'),
    readFile(deliveryPlanningPath, 'utf8'),
    readFile(trackingPath, 'utf8'),
  ]);
}

test('层级判断留下包含完整交付事实的人可读记录', async () => {
  const documents = await readContracts();

  for (const document of documents) {
    assert.match(document, /层级事实卡/);
  }

  const tracking = documents[3];
  for (const field of [
    /交付对象/,
    /独立验收边界/,
    /Capability/,
    /聚合验收/,
    /可执行叶子/,
    /依赖/,
    /集成波次/,
    /命中规则/,
    /为什么不是更小一级/,
    /缺失事实/,
  ]) {
    assert.match(tracking, field);
  }
});

test('Delivery、Capability 和 Task 由交付边界与聚合责任决定', async () => {
  const [, routing, deliveryPlanning] = await readContracts();

  assert.match(routing, /一个可独立执行结果使用 Task/);
  assert.match(routing, /多个 Task 共同形成一个聚合能力时使用 Capability/);
  assert.match(routing, /多个 Capability 共同形成一个独立交付目标且需要顶层聚合门禁时才使用 Delivery/);
  assert.match(deliveryPlanning, /独立交付目标、多个 Capability 和顶层聚合验收/);
});

test('实现数量和 Full 风险信号不能单独把工作项升级为 Delivery', async () => {
  const [skill, routing, deliveryPlanning] = await readContracts();

  for (const document of [skill, routing, deliveryPlanning]) {
    assert.match(document, /文件、接口(?:或|、)服务数量|文件、接口、服务数量/);
    assert.match(document, /不能单独(?:决定升级为|决定|推出) Delivery/);
  }
});

test('事实不足时只停留在草案，不创建或冻结工作项', async () => {
  const [skill, routing, deliveryPlanning, tracking] = await readContracts();

  assert.match(skill, /事实不足时只保留草案并等待确认，不准备工作项包、不冻结 baseline/);
  assert.match(routing, /不得保守默认 Delivery，不得准备工作项包或冻结 baseline/);
  assert.match(deliveryPlanning, /不把 Delivery 当作保守默认值/);
  assert.match(tracking, /事实不足时保持草案，不生成 ID、不准备包、不冻结 baseline/);
});
