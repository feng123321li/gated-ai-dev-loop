import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const skillPath = new URL('../../skills/gated-ai-dev-loop/SKILL.md', import.meta.url);
const routingPath = new URL('../../skills/gated-ai-dev-loop/references/routing-profiles.md', import.meta.url);
const projectPlanningPath = new URL('../../skills/gated-ai-dev-loop/references/project-planning.md', import.meta.url);
const trackingPath = new URL('../../skills/gated-ai-dev-loop/references/tracking.md', import.meta.url);

async function readContracts() {
  return Promise.all([
    readFile(skillPath, 'utf8'),
    readFile(routingPath, 'utf8'),
    readFile(projectPlanningPath, 'utf8'),
    readFile(trackingPath, 'utf8'),
  ]);
}

test('工作规模判定留下包含完整规模事实的人可读记录', async () => {
  const [skill, routing, projectPlanning, tracking] = await readContracts();

  for (const document of [skill, routing, projectPlanning, tracking]) {
    assert.match(document, /工作规模判定记录|规模事实/);
  }

  for (const field of [
    /交付对象/,
    /完整交付/,
    /独立能力/,
    /验收/,
    /里程碑/,
    /依赖波次/,
    /命中规则/,
    /为什么不是更小一级/,
  ]) {
    assert.match(tracking, field);
  }
});

test('Capability 和 Project 使用稳定规则 ID 记录判级依据', async () => {
  const [, routing, projectPlanning] = await readContracts();

  assert.match(routing, /^.*WS-P01(?=[^\r\n]*(?:用户明确确认|用户确认))(?=[^\r\n]*(?:完整系统|系统、平台|平台、应用|应用或大模块))(?=[^\r\n]*(?:从零|从现状))(?=[^\r\n]*完整交付)(?=[^\r\n]*(?:验收|可验收)).*$/m);
  assert.match(routing, /^.*WS-P02(?=[^\r\n]*(?:多个|至少两个))(?=[^\r\n]*独立[^\r\n]*能力).*$/m);
  assert.match(routing, /^.*WS-P03(?=[^\r\n]*(?:多个|至少两个))(?=[^\r\n]*用户可验收)(?=[^\r\n]*(?:里程碑|阶段交付|阶段边界)).*$/m);
  assert.match(routing, /^.*WS-P04(?=[^\r\n]*明确)(?=[^\r\n]*项目级)(?=[^\r\n]*总体(?:规划|方案))(?=[^\r\n]*阶段交付).*$/m);
  assert.match(routing, /^.*WS-P05(?=[^\r\n]*(?:强 Project 信号|Project 强信号))(?=[^\r\n]*事实[^\r\n]{0,12}(?:未知|未确认|不完整))(?=[^\r\n]*暂按[^\r\n]*Project)(?=[^\r\n]*(?:等待[^\r\n]*确认|WAITING_FOR_REQUIREMENT_CONFIRMATION)).*$/m);
  assert.match(routing, /^.*WS-C01(?=[^\r\n]*(?:仅|只有)[^\r\n]*一个[^\r\n]*能力)(?=[^\r\n]*一个[^\r\n]{0,12}聚合验收)(?=[^\r\n]*(?:未命中|不命中|没有命中)[^\r\n]*WS-P01[^\r\n]*WS-P04)(?=[^\r\n]*Capability).*$/m);

  for (const ruleId of ['WS-P01', 'WS-P02', 'WS-P03', 'WS-P04', 'WS-P05']) {
    assert.match(projectPlanning, new RegExp(ruleId));
  }
});

test('实现数量和 Full 风险信号不能单独把工作规模升级为 Project', async () => {
  const [, routing, projectPlanning] = await readContracts();

  assert.match(
    routing,
    /^.*(?=[^\r\n]*接口(?:数|数量))(?=[^\r\n]*文件(?:数|数量))(?=[^\r\n]*服务(?:数|数量))(?=[^\r\n]*公共契约)(?=[^\r\n]*状态机)(?=[^\r\n]*幂等)(?=[^\r\n]*(?:内部[^\r\n]*波次|开发波次))(?=[^\r\n]*(?:不是|不能|不得)[^\r\n]*单独[^\r\n]*Project).*$/m,
  );
  assert.match(projectPlanning, /服务(?:数|数量)[^\r\n]*(?:不是|不能作为|不构成)[^\r\n]*(?:独立|单独|自动)[^\r\n]*Project/);
  assert.doesNotMatch(projectPlanning, /^- 多个服务、客户端、数据层、基础设施或发布阶段必须按依赖顺序集成；$/m);
});

test('tracking 为 Capability 使用 W/T/S，为 Project 使用 M/W/T/S', async () => {
  const [, , , tracking] = await readContracts();

  assert.match(tracking, /^.*Capability(?=[^\r\n]*W(?:-NNN)?)(?=[^\r\n]*T(?:-NNN)?)(?=[^\r\n]*S(?:-NNN)?).*$/m);
  assert.match(tracking, /^.*Project(?=[^\r\n]*M(?:-NNN)?)(?=[^\r\n]*W(?:-NNN)?)(?=[^\r\n]*T(?:-NNN)?)(?=[^\r\n]*S(?:-NNN)?).*$/m);
  assert.doesNotMatch(tracking, /里程碑与工作流（Capability \/ Project）/);
});
