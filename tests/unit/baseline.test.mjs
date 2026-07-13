import test from 'node:test';
import assert from 'node:assert/strict';

import { buildBaselineArtifacts } from '../../src/baseline/artifacts.mjs';
import { parseFullBaseline } from '../../src/baseline/parse.mjs';
import { renderFullBaseline } from '../../src/baseline/render.mjs';
import { normalizeTestArgv, validateTestArgv } from '../../src/baseline/test-command.mjs';
import { validFullBaseline } from '../helpers/full-baseline.mjs';

const parse = (markdown = validFullBaseline()) => parseFullBaseline(markdown, { file: 'requirements/baseline-source.md' });

test('shared test-command policy is pure and leaves error mapping to its caller', () => {
  const input = ['node', '--test', '--test-name-pattern', 'unit|integration'];
  const normalized = normalizeTestArgv(input);
  assert.deepEqual(normalized, input);
  assert.notStrictEqual(normalized, input);
  assert.equal(validateTestArgv(input), true);
  assert.equal(normalizeTestArgv(['npm', 'exec', '-c', 'echo unsafe']), null);
  assert.equal(validateTestArgv(['npm', 'exec', '-c', 'echo unsafe']), false);
});

test('Full baseline parses the exact schema with normalized trace links', () => {
  const model = parse();
  assert.equal(model.schemaVersion, 1);
  assert.equal(model.generatorVersion, 1);
  assert.equal(model.goal, 'Deliver a deterministic requirement package.');
  assert.deepEqual(model.requirements.map(({ id, title }) => ({ id, title })), [
    { id: 'R-001', title: 'Validate explicit inputs' },
    { id: 'R-002', title: 'Preserve deterministic output' },
  ]);
  assert.deepEqual(model.acceptance[0].requirementIds, ['R-001']);
  assert.deepEqual(model.tasks[1].acceptanceIds, ['A-002']);
  assert.deepEqual(model.testCommands, [
    ['node', '--test', 'tests/unit/baseline.test.mjs'],
    ['npm', 'test'],
  ]);
  assert.deepEqual(model.requirements[0].trace, { file: 'requirements/baseline-source.md', line: 18 });
  assert.equal(model.sourceLines.length, validFullBaseline().split('\n').length);
  assert.deepEqual(model.sourceLines[17], {
    file: 'requirements/baseline-source.md', line: 18,
    text: '### R-001 Validate explicit inputs', section: 'Requirements',
  });
});

test('Full baseline renderer is deterministic, canonical, and LF-only', () => {
  const windowsInput = validFullBaseline().replaceAll('\n', '\r\n');
  const first = renderFullBaseline(parseFullBaseline(windowsInput, { file: 'requirements/input.md' }));
  const second = renderFullBaseline(parseFullBaseline(first, { file: 'generated/baseline.md' }));
  assert.equal(first, second);
  assert.equal(first.includes('\r'), false);
  assert.equal(first.endsWith('\n'), true);
  assert.deepEqual([...first.matchAll(/^## (.+)$/gm)].map((match) => match[1]), [
    'Goal', 'Background', 'Scope', 'Non-Goals', 'Requirements', 'Acceptance',
    'Tasks', 'Risks', 'Test Commands', 'Decisions',
  ]);
});

test('Markdown headings inside a fenced code block are preserved as content', () => {
  const markdown = validFullBaseline({
    Background: 'The baseline must preserve examples.\n\n```markdown\n## Example only\n### R-999 Not a record\n```',
  });
  const rendered = renderFullBaseline(parse(markdown));
  assert.match(rendered, /```markdown\n## Example only\n### R-999 Not a record\n```/);
  assert.throws(
    () => parse(validFullBaseline({ Background: 'Unclosed example.\n\n```markdown\n## Example only' })),
    { code: 'BASELINE_STRUCTURE_INVALID' },
  );
});

test('acceptance and task artifacts carry versions and complete trace links', () => {
  const { acceptance, tasks } = buildBaselineArtifacts(parse());
  assert.equal(acceptance.schemaVersion, 1);
  assert.equal(acceptance.generatorVersion, 1);
  assert.deepEqual(acceptance.acceptance[0], {
    id: 'A-001',
    requirementIds: ['R-001'],
    expectedResult: 'An unsafe or malformed input returns a stable error before artifacts change.',
    trace: { file: 'requirements/baseline-source.md', line: 25 },
  });
  assert.equal(tasks.schemaVersion, 1);
  assert.equal(tasks.generatorVersion, 1);
  assert.deepEqual(tasks.tasks[0], {
    id: 'T-001',
    requirementIds: ['R-001'],
    acceptanceIds: ['A-001'],
    text: 'Add strict input and semantic validation.',
    trace: { file: 'requirements/baseline-source.md', line: 32 },
  });
});

for (const [name, mutate] of [
  ['wrong title', (value) => value.replace('# Development Baseline', '# Project Notes')],
  ['missing section', (value) => value.replace(/## Background\n[^\n]+\n\n/, '')],
  ['reordered sections', (value) => value.replace('## Goal', '## Background TEMP').replace('## Background', '## Goal').replace('## Background TEMP', '## Background')],
  ['duplicate section', (value) => value.replace('## Background', '## Goal\nDuplicate goal.\n\n## Background')],
  ['unknown section', (value) => value.replace('## Background', '## Surprise\nNo.\n\n## Background')],
  ['malformed nested heading', (value) => value.replace('The development gate', '#### Unexpected\nThe development gate')],
  ['nested blockquote heading', (value) => value.replace('The development gate', '> > ### Hidden\nThe development gate')],
  ['ordered-list heading', (value) => value.replace('The development gate', '1. ### Hidden\nThe development gate')],
  ['setext heading injection', (value) => value.replace('The development gate needs', 'Injected\n===\nThe development gate needs')],
  ['single-hyphen setext heading', (value) => value.replace('The development gate needs', 'Injected\n-\nThe development gate needs')],
]) {
  test(`Full baseline rejects ${name}`, () => {
    assert.throws(() => parse(mutate(validFullBaseline())), { code: 'BASELINE_STRUCTURE_INVALID' });
  });
}

for (const section of ['Goal', 'Background', 'Scope', 'Non-Goals', 'Requirements', 'Acceptance', 'Tasks', 'Risks', 'Test Commands', 'Decisions']) {
  test(`Full baseline requires a value in ${section}`, () => {
    assert.throws(() => parse(validFullBaseline({ [section]: '   ' })), { code: /BASELINE_(?:VALUE|STRUCTURE|TEST_COMMAND)_INVALID/ });
  });
}

for (const [name, replacement, code = 'BASELINE_TRACE_INVALID'] of [
  ['malformed requirement ID', '### R-1 Validate explicit inputs'],
  ['zero requirement ID', '### R-000 Validate explicit inputs'],
  ['duplicate requirement ID', '### R-002 Validate explicit inputs'],
  ['malformed acceptance links', '### A-001 R-001'],
  ['duplicate acceptance ID', '### A-002 [R-001]'],
  ['unknown acceptance requirement', '### A-001 [R-999]'],
  ['checked task', '- [x] T-001 [R-001] [A-001] Add strict input and semantic validation.'],
  ['malformed task ID', '- [ ] T-1 [R-001] [A-001] Add strict input and semantic validation.'],
  ['unknown task requirement', '- [ ] T-001 [R-999] [A-001] Add strict input and semantic validation.'],
  ['unknown task acceptance', '- [ ] T-001 [R-001] [A-999] Add strict input and semantic validation.'],
]) {
  test(`Full baseline rejects ${name}`, () => {
    const original = name.includes('acceptance')
      ? '### A-001 [R-001]'
      : name.includes('task') || name === 'checked task'
        ? '- [ ] T-001 [R-001] [A-001] Add strict input and semantic validation.'
        : '### R-001 Validate explicit inputs';
    assert.throws(() => parse(validFullBaseline().replace(original, replacement)), { code });
  });
}

test('Full baseline rejects orphan requirements and acceptance entries', () => {
  assert.throws(
    () => parse(validFullBaseline({ Acceptance: '### A-001 [R-001]\nA valid input returns a stable result.' })),
    { code: 'BASELINE_TRACE_INVALID' },
  );
  assert.throws(
    () => parse(validFullBaseline({ Tasks: '- [ ] T-001 [R-001] [A-001] Implement validation.' })),
    { code: 'BASELINE_TRACE_INVALID' },
  );
  assert.throws(
    () => parse(validFullBaseline().replace(
      '- [ ] T-001 [R-001] [A-001] Add strict input and semantic validation.',
      '- [ ] T-001 [R-001,R-002] [A-001] Add strict input and semantic validation.',
    )),
    { code: 'BASELINE_TRACE_INVALID' },
  );
});

for (const placeholder of ['TODO', 'TBD', 'FIXME', '<fill me>', '{{value}}', '???', 'Lorem ipsum']) {
  test(`Full baseline rejects placeholder content: ${placeholder}`, () => {
    assert.throws(() => parse(validFullBaseline({ Goal: placeholder })), { code: 'BASELINE_PLACEHOLDER' });
  });
}

test('requirements need a non-placeholder title and nonempty body', () => {
  assert.throws(
    () => parse(validFullBaseline().replace('### R-001 Validate explicit inputs', '### R-001')),
    { code: 'BASELINE_TRACE_INVALID' },
  );
  assert.throws(
    () => parse(validFullBaseline().replace('### R-001 Validate explicit inputs', '### R-001 TODO')),
    { code: 'BASELINE_PLACEHOLDER' },
  );
});

test('requirements accept language-neutral normative text', () => {
  const model = parse(validFullBaseline().replace(
    'The CLI must reject unsafe paths and malformed baseline content.',
    '系统必须拒绝不安全路径和格式错误的基线。',
  ));
  assert.equal(model.requirements[0].text, '系统必须拒绝不安全路径和格式错误的基线。');
});

test('acceptance and task entries require nonempty observable text', () => {
  assert.throws(
    () => parse(validFullBaseline().replace('An unsafe or malformed input returns a stable error before artifacts change.', '   ')),
    { code: 'BASELINE_VALUE_INVALID' },
  );
  assert.throws(
    () => parse(validFullBaseline().replace('Add strict input and semantic validation.', '')),
    { code: 'BASELINE_TRACE_INVALID' },
  );
});

for (const [name, command] of [
  ['shell string', '- "node --test"'],
  ['empty argv', '- []'],
  ['empty executable', '- [""]'],
  ['blank executable', '- ["   "]'],
  ['non-string argument', '- ["node",3]'],
  ['shell executable', '- ["sh","-c","node --test"]'],
  ['POSIX shell path', '- ["/bin/sh","-c","echo pwned"]'],
  ['Almquist shell path', '- ["/bin/ash","-c","echo pwned"]'],
  ['fish shell path', '- ["/usr/bin/fish","-c","echo pwned"]'],
  ['POSIX shell executable suffix', '- ["/bin/sh.exe","-c","echo pwned"]'],
  ['Windows shell path', '- ["C:\\\\Windows\\\\System32\\\\cmd.exe","/c","echo pwned"]'],
  ['one-element command string', '- ["npm test"]'],
  ['control byte', '- ["node","\\u0000"]'],
  ['non-bullet JSON', '["node","--test"]'],
  ['malformed JSON', '- ["node",]'],
]) {
  test(`Full baseline rejects ${name} test command`, () => {
    assert.throws(() => parse(validFullBaseline({ 'Test Commands': command })), { code: 'BASELINE_TEST_COMMAND_INVALID' });
  });
}

test('Full baseline rejects duplicate test commands and control bytes in prose', () => {
  assert.throws(
    () => parse(validFullBaseline({ 'Test Commands': '- ["npm","test"]\n- ["npm","test"]' })),
    { code: 'BASELINE_TEST_COMMAND_INVALID' },
  );
  assert.throws(() => parse(validFullBaseline({ Goal: 'Goal\u001bvalue' })), { code: 'BASELINE_VALUE_INVALID' });
});

for (const [name, argv] of [
  ['env to POSIX shell', ['env', 'bash', '-c', 'npm test']],
  ['absolute env with options', ['/usr/bin/env', '-i', 'sh', '-c', 'npm test']],
  ['env argv0 option to shell', ['env', '--argv0', 'safe-name', 'sh', '-c', 'npm test']],
  ['env split-string command', ['env', '-S', 'bash -c npm test']],
  ['env long attached split-string', ['env', '--split-string=bash -c npm test']],
  ['env short attached split-string', ['env', '-Sbash -c npm test']],
  ['nested env wrapper', ['npx', 'env', 'bash', '-lc', 'npm test']],
  ['npx call string', ['npx', '--call', 'bash -lc whoami']],
  ['npx unknown value option before a shell', ['npx', '--future-option', 'value', 'bash', '-lc', 'whoami']],
  ['npm exec call string', ['npm', 'exec', '-c', 'bash -lc whoami']],
  ['npm abbreviated exec call string', ['npm', 'exe', '-c', 'bash -lc whoami']],
  ['npm x long call string', ['npm', 'x', '--call', 'bash -lc whoami']],
  ['env to Windows command shell', ['env', 'cmd.exe', '/d', '/c', 'npm test']],
  ['busybox shell applet', ['busybox', 'sh', '-c', 'npm test']],
  ['busybox hush applet', ['busybox', 'hush', '-c', 'npm test']],
  ['env to toybox shell applet', ['env', 'toybox.exe', 'ash', '-c', 'npm test']],
  ['WSL POSIX shell', ['wsl.exe', '--', 'bash', '-lc', 'npm test']],
  ['WSL shell type option', ['wsl.exe', '--shell-type', 'standard', 'bash', '-lc', 'npm test']],
  ['env to WSL shell', ['env', 'wsl', 'sh', '-c', 'npm test']],
  ['versioned Korn shell', ['ksh93', '-c', 'npm test']],
  ['Python command string', ['python3', '-c', 'print(1)']],
  ['Python clustered command string', ['python3', '-Ec', 'print(1)']],
  ['Python multi-flag clustered command string', ['python3', '-IEc', 'print(1)']],
  ['Python command string after option value', ['python3', '-W', 'ignore', '-c', 'print(1)']],
  ['Python launcher attached command string', ['py.exe', '-cprint(1)']],
  ['Python GUI launcher command string', ['pyw.exe', '-c', 'print(1)']],
  ['Pythonw versioned command string', ['pythonw3.12', '--command=print(1)']],
  ['env to Python command string', ['env', '/usr/bin/python', '--command', 'print(1)']],
  ['Ruby command string', ['ruby.exe', '-e', 'system("npm test")']],
  ['Ruby GUI command string', ['rubyw.exe', '-e', 'puts(1)']],
  ['Ruby command string after include path', ['ruby', '-I', 'lib', '-e', 'puts(1)']],
  ['versioned Ruby attached command string', ['ruby3.3', '-eputs(1)']],
  ['clustered Perl command string', ['perl5.40', '-weprint(1)']],
  ['Windows Perl command string', ['wperl.exe', '-e', 'print(1)']],
  ['feature-enabled Perl command string', ['perl', '-E', 'say 1']],
  ['clustered taint-enabled Perl command string', ['perl', '-TE', 'say 1']],
  ['versioned PHP command string', ['php8.3', '-recho 1']],
  ['PHP begin command string', ['php', '-B', 'echo 1']],
  ['PHP per-line command string', ['php', '--process-code', 'echo 1']],
  ['PHP end command string', ['php', '--process-end=echo 1']],
  ['PHP command string after ini setting', ['php', '-d', 'display_errors=1', '-r', 'echo 1']],
  ['versioned Lua command string', ['lua5.4', '-eprint(1)']],
  ['Node command string', ['node', '--eval', 'require("child_process").execSync("npm test")']],
  ['Node command string after preload', ['node', '--require', 'loader.mjs', '--eval', 'process.exit(0)']],
  ['Node command string after input type', ['node', '--input-type', 'module', '--eval', 'process.exit(0)']],
  ['Node command string after warning redirect', ['node', '--redirect-warnings', 'warnings.log', '--eval', 'process.exit(0)']],
  ['Node command string after generic default-type option', ['node', '--experimental-default-type', 'module', '--eval', 'process.exit(0)']],
  ['Node command string after generic DNS option', ['node', '--dns-result-order', 'verbatim', '--eval', 'process.exit(0)']],
  ['Node command string after generic rejection option', ['node', '--unhandled-rejections', 'strict', '--eval', 'process.exit(0)']],
  ['Node command string after disable-proto option', ['node', '--disable-proto', 'delete', '--eval', 'process.exit(0)']],
  ['Node command string after trace categories option', ['node', '--trace-event-categories', 'node', '--eval', 'process.exit(0)']],
  ['Nodejs attached command string', ['nodejs', '--eval=process.exit(0)']],
  ['Deno eval subcommand', ['deno', 'eval', 'console.log(1)']],
]) {
  test(`Full baseline rejects indirect shell wrapper: ${name}`, () => {
    assert.throws(
      () => parse(validFullBaseline({ 'Test Commands': `- ${JSON.stringify(argv)}` })),
      { code: 'BASELINE_TEST_COMMAND_INVALID' },
    );
  });
}

for (const [name, argv] of [
  ['direct npm test', ['npm', 'test']],
  ['Node test-name regular expression', ['node', '--test', '--test-name-pattern', 'unit|integration']],
  ['Node named-capture regular expression', ['node', '--test', '--test-name-pattern', '(?<suite>unit|integration)']],
  ['literal placeholder-like test argument', ['node', '--test', 'tests/TODO/<suite>.test.mjs']],
  ['Go test-name regular expression', ['go', 'test', './...', '-run', 'TestUnit|TestIntegration']],
  ['literal shell operators passed without a shell', ['node', '--test', '&&', 'literal|value']],
  ['npm exec package argument after command boundary', ['npm', 'exec', 'vitest', '--', '--testNamePattern', 'unit|integration']],
  ['Python module invocation', ['python3', '-m', 'pytest', 'tests/env_test.py']],
  ['Python module option resembling command mode', ['python3', '-m', 'pytest', '-c', 'pytest.ini']],
  ['Python module after warning option', ['python3', '-W', 'ignore', '-m', 'pytest', '-c', 'pytest.ini']],
  ['Node test path containing shell words', ['node', '--test', 'tests/bash/env.test.mjs']],
  ['Node test path named like a shell', ['node', '--test', 'tests/sh']],
  ['Node script argument resembling eval', ['node', 'scripts/runner.mjs', '--eval', 'literal']],
  ['Node boolean option before eval-like script argument', ['node', '--no-warnings', 'scripts/runner.mjs', '--eval', 'literal']],
  ['Node script after preload with eval-like argument', ['node', '--require', 'loader.mjs', 'scripts/runner.mjs', '--eval', 'literal']],
  ['Node run target with eval-like argument', ['node', '--run', 'test', '--eval', 'literal']],
  ['PHP file target with command-like argument', ['php', '-f', 'scripts/runner.php', '-r', 'literal']],
  ['Ruby search target with eval-like argument', ['ruby', '-S', 'runner', '-e', 'literal']],
  ['Ruby separate encoding option', ['ruby', '-E', 'UTF-8', 'scripts/runner.rb']],
  ['Ruby attached encoding option', ['ruby', '-EUTF-8', 'scripts/runner.rb']],
  ['Lua ignore-environment option', ['lua', '-E', 'scripts/runner.lua']],
  ['Python script argument resembling command mode', ['python3', 'scripts/runner.py', '-c', 'literal']],
  ['busybox non-shell applet', ['busybox', 'ls', '-la']],
  ['busybox help for shell applet', ['busybox', '--help', 'sh']],
  ['toybox help for shell applet', ['toybox', '--help', 'sh']],
  ['WSL direct tool', ['wsl', 'git', 'status']],
  ['unknown executable shell-like data', ['eslint', 'sh']],
  ['wrapper-like executable name', ['cmdlint', '/c', 'rules.json']],
]) {
  test(`Full baseline preserves legitimate direct argv: ${name}`, () => {
    const model = parse(validFullBaseline({ 'Test Commands': `- ${JSON.stringify(argv)}` }));
    assert.deepEqual(model.testCommands, [argv]);
  });
}
