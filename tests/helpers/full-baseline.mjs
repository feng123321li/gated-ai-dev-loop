export function validFullBaseline(patch = {}) {
  const sections = {
    Goal: 'Deliver a deterministic requirement package.',
    Background: 'The development gate needs an explicit local source.',
    Scope: '- Parse and freeze the supplied baseline.\n- Preserve traceability to every input line.',
    'Non-Goals': '- Invoking a model.\n- Publishing repository changes.',
    Requirements: [
      '### R-001 Validate explicit inputs',
      'The CLI must reject unsafe paths and malformed baseline content.',
      '',
      '### R-002 Preserve deterministic output',
      'The renderer must produce stable LF-only output with source traceability.',
    ].join('\n'),
    Acceptance: [
      '### A-001 [R-001]',
      'An unsafe or malformed input returns a stable error before artifacts change.',
      '',
      '### A-002 [R-002]',
      'Repeated rendering produces byte-identical output and complete trace links.',
    ].join('\n'),
    Tasks: [
      '- [ ] T-001 [R-001] [A-001] Add strict input and semantic validation.',
      '- [ ] T-002 [R-002] [A-002] Render deterministic artifacts and trace links.',
    ].join('\n'),
    Risks: '- A source may change while it is being read.\n- A partial write could expose mixed artifacts.',
    'Test Commands': '- ["node","--test","tests/unit/baseline.test.mjs"]\n- ["npm","test"]',
    Decisions: '- Use verified file handles and repository-relative paths.\n- Keep model invocation outside this phase.',
    ...patch,
  };
  return [
    '# Development Baseline',
    '',
    ...Object.entries(sections).flatMap(([heading, body]) => [`## ${heading}`, body, '']),
  ].join('\n');
}
