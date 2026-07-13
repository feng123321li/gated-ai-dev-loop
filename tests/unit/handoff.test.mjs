import test from 'node:test';
import assert from 'node:assert/strict';

import { renderDevelopmentHandoff } from '../../src/handoff/render.mjs';

test('shared handoff renderer freezes authority and constrains Claude to implementation facts', () => {
  const handoff = renderDevelopmentHandoff({
    task: 'empty-value',
    reviewer: 'claude',
    authorityFile: 'light-brief.md',
    scope: ['src/input.mjs', 'tests/unit/input.test.mjs'],
    tasks: [{
      id: 'T-001',
      requirementIds: ['R-001'],
      acceptanceIds: ['A-001'],
      text: 'Show a clear empty-value message.',
    }],
    acceptance: [{
      id: 'A-001',
      requirementIds: ['R-001'],
      expectedResult: 'Empty submission displays "Value is required".',
    }],
    testCommands: [['node', '--test', 'tests/unit/input.test.mjs']],
  });

  assert.match(handoff, /Frozen authority: `light-brief\.md`/);
  assert.match(handoff, /only development authority/i);
  assert.match(handoff, /Do not reanalyze, reinterpret, clarify, or rewrite requirements\./);
  assert.match(handoff, /Do not change acceptance criteria or any frozen artifact\./);
  assert.match(handoff, /return `BLOCKED`/);
  assert.match(handoff, /Do not judge or report `PASS`\./);
  assert.match(handoff, /- src\/input\.mjs/);
  assert.match(handoff, /- T-001 \[R-001\] \[A-001\]/);
  assert.match(handoff, /- \["node","--test","tests\/unit\/input\.test\.mjs"\]/);
});
