function links(values) {
  return Array.isArray(values) ? values.join(',') : '';
}

export function renderDevelopmentHandoff({
  task,
  reviewer,
  authorityFile,
  scope = [],
  tasks,
  acceptance,
  testCommands,
} = {}) {
  const scopeLines = Array.isArray(scope)
    ? scope.map((entry) => `- ${entry}`)
    : (typeof scope === 'string' && scope.length > 0 ? [scope] : []);
  const lines = [
    '# Development Handoff',
    '',
    `Task: ${task}`,
    `Reviewed by: ${reviewer}`,
    `Frozen authority: \`${authorityFile}\``,
    '',
    `The frozen \`${authorityFile}\` is the only development authority.`,
    '',
    '## Development Rules',
    '- Implement only the listed tasks within the frozen Scope.',
    '- Do not reanalyze, reinterpret, clarify, or rewrite requirements.',
    '- Do not change acceptance criteria or any frozen artifact.',
    '- If the frozen authority is incomplete or contradictory, return `BLOCKED`; do not resolve it by analysis.',
    '- Do not judge or report `PASS`.',
    '',
  ];
  if (scopeLines.length > 0) {
    lines.push('## Scope', ...scopeLines, '');
  }
  lines.push(
    '## Tasks',
    ...tasks.map((entry) => `- ${entry.id} [${links(entry.requirementIds)}] [${links(entry.acceptanceIds)}] ${entry.text}`),
    '',
    '## Acceptance',
    ...acceptance.map((entry) => `- ${entry.id} [${links(entry.requirementIds)}] ${entry.expectedResult.replaceAll('\n', ' ')}`),
    '',
    '## Test Commands',
    ...testCommands.map((argv) => `- ${JSON.stringify(argv)}`),
    '',
  );
  return lines.join('\n');
}
