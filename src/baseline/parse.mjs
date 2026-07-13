import { GatedLoopError } from '../core/errors.mjs';
import { normalizeTestArgv } from './test-command.mjs';

export const BASELINE_SCHEMA_VERSION = 1;
export const BASELINE_GENERATOR_VERSION = 1;
export const FULL_BASELINE_SECTIONS = Object.freeze([
  'Goal', 'Background', 'Scope', 'Non-Goals', 'Requirements', 'Acceptance',
  'Tasks', 'Risks', 'Test Commands', 'Decisions',
]);

const PLACEHOLDER = /\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?|\blorem\s+ipsum\b/i;
const CONTROL = /[\u0000-\u0009\u000B\u000C\u000E-\u001F\u007F-\u009F]/;
const ID_NUMBER = '(?:00[1-9]|0[1-9]\\d|[1-9]\\d{2})';
const REQUIREMENT = new RegExp(`^### (R-${ID_NUMBER}) (\\S(?:.*\\S)?)$`);
const ACCEPTANCE = new RegExp(`^### (A-${ID_NUMBER}) \\[([^\\]]+)\\]$`);
const TASK = new RegExp(`^- \\[ \\] (T-${ID_NUMBER}) \\[([^\\]]+)\\] \\[([^\\]]+)\\] (\\S(?:.*\\S)?)$`);

function fail(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}

function cleanBlock(lines) {
  let start = 0;
  let end = lines.length;
  while (start < end && lines[start].text.trim() === '') start++;
  while (end > start && lines[end - 1].text.trim() === '') end--;
  return lines.slice(start, end);
}

function content(lines, section) {
  const cleaned = cleanBlock(lines);
  if (cleaned.length === 0 || cleaned.every((line) => line.text.trim() === '')) {
    fail('BASELINE_VALUE_INVALID', `${section} must be nonempty`, { section });
  }
  const text = cleaned.map((line) => line.text).join('\n');
  if (PLACEHOLDER.test(text)) fail('BASELINE_PLACEHOLDER', `${section} contains placeholder content`, { section });
  if (CONTROL.test(text)) fail('BASELINE_VALUE_INVALID', `${section} contains control characters`, { section });
  return { text, lines: cleaned };
}

function parseLinks(value, prefix, line, field) {
  const values = value.split(',').map((entry) => entry.trim());
  const pattern = new RegExp(`^${prefix}-${ID_NUMBER}$`);
  if (values.length === 0 || values.some((entry) => !pattern.test(entry)) || new Set(values).size !== values.length) {
    fail('BASELINE_TRACE_INVALID', `${field} contains malformed or duplicate links`, { line, field });
  }
  return values;
}

function recordTrace(file, line) { return { file, line }; }

function parseRequirements(lines, file, fencedLines) {
  const values = [];
  const ids = new Set();
  let index = 0;
  while (index < lines.length) {
    while (index < lines.length && lines[index].text.trim() === '') index++;
    if (index >= lines.length) break;
    const header = lines[index];
    const match = REQUIREMENT.exec(header.text);
    if (!match) fail('BASELINE_TRACE_INVALID', 'Requirement headings must use R-NNN and a title', { line: header.line });
    if (ids.has(match[1])) fail('BASELINE_TRACE_INVALID', `Duplicate requirement ID: ${match[1]}`, { line: header.line });
    if (PLACEHOLDER.test(match[2])) fail('BASELINE_PLACEHOLDER', `${match[1]} title contains placeholder content`, { line: header.line });
    ids.add(match[1]);
    index++;
    const bodyStart = index;
    while (index < lines.length && (fencedLines.has(lines[index].line - 1) || !lines[index].text.startsWith('### '))) index++;
    const body = content(lines.slice(bodyStart, index), match[1]);
    values.push({ id: match[1], title: match[2], text: body.text, trace: recordTrace(file, header.line) });
  }
  if (values.length === 0) fail('BASELINE_VALUE_INVALID', 'Requirements must contain at least one entry');
  return values;
}

function parseAcceptance(lines, file, fencedLines) {
  const values = [];
  const ids = new Set();
  let index = 0;
  while (index < lines.length) {
    while (index < lines.length && lines[index].text.trim() === '') index++;
    if (index >= lines.length) break;
    const header = lines[index];
    const match = ACCEPTANCE.exec(header.text);
    if (!match) fail('BASELINE_TRACE_INVALID', 'Acceptance headings must use A-NNN [R-NNN,...]', { line: header.line });
    if (ids.has(match[1])) fail('BASELINE_TRACE_INVALID', `Duplicate acceptance ID: ${match[1]}`, { line: header.line });
    ids.add(match[1]);
    const requirementIds = parseLinks(match[2], 'R', header.line, match[1]);
    index++;
    const bodyStart = index;
    while (index < lines.length && (fencedLines.has(lines[index].line - 1) || !lines[index].text.startsWith('### '))) index++;
    const body = content(lines.slice(bodyStart, index), match[1]);
    values.push({ id: match[1], requirementIds, expectedResult: body.text, trace: recordTrace(file, header.line) });
  }
  if (values.length === 0) fail('BASELINE_VALUE_INVALID', 'Acceptance must contain at least one entry');
  return values;
}

function parseTasks(lines, file) {
  const values = [];
  const ids = new Set();
  for (const line of lines) {
    if (line.text.trim() === '') continue;
    const match = TASK.exec(line.text);
    if (!match) fail('BASELINE_TRACE_INVALID', 'Tasks must use unchecked T-NNN traceable checklist entries', { line: line.line });
    if (ids.has(match[1])) fail('BASELINE_TRACE_INVALID', `Duplicate task ID: ${match[1]}`, { line: line.line });
    ids.add(match[1]);
    if (PLACEHOLDER.test(match[4])) fail('BASELINE_PLACEHOLDER', `${match[1]} contains placeholder content`, { line: line.line });
    values.push({
      id: match[1],
      requirementIds: parseLinks(match[2], 'R', line.line, match[1]),
      acceptanceIds: parseLinks(match[3], 'A', line.line, match[1]),
      text: match[4],
      trace: recordTrace(file, line.line),
    });
  }
  if (values.length === 0) fail('BASELINE_VALUE_INVALID', 'Tasks must contain at least one entry');
  return values;
}

function parseTestCommands(lines, file) {
  const records = [];
  const unique = new Set();
  for (const line of lines) {
    if (line.text.trim() === '') continue;
    if (!line.text.startsWith('- ')) fail('BASELINE_TEST_COMMAND_INVALID', 'Test commands must be JSON argv array bullets', { line: line.line });
    let parsed;
    try { parsed = JSON.parse(line.text.slice(2)); }
    catch { fail('BASELINE_TEST_COMMAND_INVALID', 'Test command must contain valid JSON', { line: line.line }); }
    const argv = normalizeTestArgv(parsed);
    if (!argv) {
      fail('BASELINE_TEST_COMMAND_INVALID', 'Test command must be a safe nonempty argv array', { line: line.line });
    }
    const canonical = JSON.stringify(argv);
    if (unique.has(canonical)) fail('BASELINE_TEST_COMMAND_INVALID', 'Duplicate test command', { line: line.line });
    unique.add(canonical);
    records.push({ argv, trace: recordTrace(file, line.line) });
  }
  if (records.length === 0) fail('BASELINE_TEST_COMMAND_INVALID', 'At least one test command is required');
  return records;
}

function validateTrace(requirements, acceptance, tasks) {
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const acceptanceById = new Map(acceptance.map((entry) => [entry.id, entry]));
  for (const entry of acceptance) {
    for (const id of entry.requirementIds) if (!requirementIds.has(id)) {
      fail('BASELINE_TRACE_INVALID', `${entry.id} links unknown requirement ${id}`, { id: entry.id, link: id });
    }
  }
  for (const task of tasks) {
    for (const id of task.requirementIds) if (!requirementIds.has(id)) {
      fail('BASELINE_TRACE_INVALID', `${task.id} links unknown requirement ${id}`, { id: task.id, link: id });
    }
    for (const id of task.acceptanceIds) if (!acceptanceById.has(id)) {
      fail('BASELINE_TRACE_INVALID', `${task.id} links unknown acceptance ${id}`, { id: task.id, link: id });
    }
    const taskRequirements = new Set(task.requirementIds);
    const acceptanceRequirements = new Set();
    for (const id of task.acceptanceIds) {
      for (const requirementId of acceptanceById.get(id).requirementIds) acceptanceRequirements.add(requirementId);
      if (!acceptanceById.get(id).requirementIds.some((requirementId) => taskRequirements.has(requirementId))) {
        fail('BASELINE_TRACE_INVALID', `${task.id} does not overlap the requirements linked by ${id}`, { id: task.id, link: id });
      }
    }
    if (task.requirementIds.some((id) => !acceptanceRequirements.has(id))
        || [...acceptanceRequirements].some((id) => !taskRequirements.has(id))) {
      fail('BASELINE_TRACE_INVALID', `${task.id} has orphan requirement or acceptance links`, { id: task.id });
    }
  }
  const acceptedRequirements = new Set(acceptance.flatMap(({ requirementIds: ids }) => ids));
  const taskedRequirements = new Set(tasks.flatMap(({ requirementIds: ids }) => ids));
  const taskedAcceptance = new Set(tasks.flatMap(({ acceptanceIds: ids }) => ids));
  if (requirements.some(({ id }) => !acceptedRequirements.has(id) || !taskedRequirements.has(id))
      || acceptance.some(({ id }) => !taskedAcceptance.has(id))) {
    fail('BASELINE_TRACE_INVALID', 'Requirements and acceptance entries must be covered by tasks');
  }
}

function stripMarkdownContainers(value) {
  let content = value;
  let previous;
  do {
    previous = content;
    content = content.replace(/^\s{0,3}>\s?/, '').replace(/^\s{0,3}(?:[-+*]|\d+[.)])\s+/, '');
  } while (content !== previous);
  return content;
}

function rejectUnexpectedHeadings(sectionLines, section, fencedLines) {
  for (const line of sectionLines) {
    if (fencedLines.has(line.line - 1)) continue;
    const unwrapped = stripMarkdownContainers(line.text);
    const heading = /^\s{0,3}#{1,6}(?:\s|$)/.test(unwrapped);
    const setext = /^\s{0,3}(?:=+|-+)\s*$/.test(unwrapped);
    if ((heading || setext) && !(['Requirements', 'Acceptance'].includes(section) && line.text.startsWith('### '))) {
      fail('BASELINE_STRUCTURE_INVALID', `Unexpected heading in ${section}`, { line: line.line, section });
    }
  }
}

function fencedLineIndexes(rawLines) {
  const indexes = new Set();
  let fence = null;
  for (let index = 0; index < rawLines.length; index++) {
    const line = rawLines[index];
    if (fence) {
      indexes.add(index);
      const closing = new RegExp(`^ {0,3}${fence.character}{${fence.length},}\\s*$`);
      if (closing.test(line)) fence = null;
      continue;
    }
    const opening = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (opening) {
      fence = { character: opening[1][0], length: opening[1].length };
      indexes.add(index);
    }
  }
  if (fence) fail('BASELINE_STRUCTURE_INVALID', 'Baseline contains an unterminated fenced code block');
  return indexes;
}

export function parseFullBaseline(markdown, { file = 'baseline.md' } = {}) {
  if (typeof markdown !== 'string') fail('BASELINE_STRUCTURE_INVALID', 'Baseline Markdown must be text');
  let normalized = markdown.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
  if (CONTROL.test(normalized)) fail('BASELINE_VALUE_INVALID', 'Baseline contains control characters');
  const rawLines = normalized.split('\n');
  const lines = rawLines.map((text, index) => ({ file, line: index + 1, text, section: null }));
  const fencedLines = fencedLineIndexes(rawLines);
  if (rawLines[0] !== '# Development Baseline') fail('BASELINE_STRUCTURE_INVALID', 'Baseline title must be exact and first');

  const headings = [];
  for (let index = 1; index < rawLines.length; index++) {
    const match = fencedLines.has(index) ? null : /^## (.+)$/.exec(rawLines[index]);
    if (match) headings.push({ name: match[1], index });
    else if (!fencedLines.has(index) && /^#{1,2}(?:\s|$)/.test(rawLines[index])) {
      fail('BASELINE_STRUCTURE_INVALID', 'Malformed top-level baseline heading', { line: index + 1 });
    }
  }
  if (headings.length !== FULL_BASELINE_SECTIONS.length
      || headings.some((heading, index) => heading.name !== FULL_BASELINE_SECTIONS[index])) {
    fail('BASELINE_STRUCTURE_INVALID', 'Baseline sections must be exact, unique, and ordered');
  }
  if (rawLines.slice(1, headings[0].index).some((line) => line.trim() !== '')) {
    fail('BASELINE_STRUCTURE_INVALID', 'Content is not allowed before Goal');
  }

  const sectionMap = new Map();
  for (let index = 0; index < headings.length; index++) {
    const heading = headings[index];
    const end = headings[index + 1]?.index ?? rawLines.length;
    for (let cursor = heading.index; cursor < end; cursor++) lines[cursor].section = heading.name;
    const sectionLines = lines.slice(heading.index + 1, end);
    rejectUnexpectedHeadings(sectionLines, heading.name, fencedLines);
    sectionMap.set(heading.name, sectionLines);
  }

  const goal = content(sectionMap.get('Goal'), 'Goal').text;
  const background = content(sectionMap.get('Background'), 'Background').text;
  const scope = content(sectionMap.get('Scope'), 'Scope').text;
  const nonGoals = content(sectionMap.get('Non-Goals'), 'Non-Goals').text;
  const requirements = parseRequirements(sectionMap.get('Requirements'), file, fencedLines);
  const acceptance = parseAcceptance(sectionMap.get('Acceptance'), file, fencedLines);
  const tasks = parseTasks(sectionMap.get('Tasks'), file);
  const risks = content(sectionMap.get('Risks'), 'Risks').text;
  const testCommandRecords = parseTestCommands(sectionMap.get('Test Commands'), file);
  const decisions = content(sectionMap.get('Decisions'), 'Decisions').text;
  validateTrace(requirements, acceptance, tasks);

  return {
    schemaVersion: BASELINE_SCHEMA_VERSION,
    generatorVersion: BASELINE_GENERATOR_VERSION,
    goal,
    background,
    scope,
    nonGoals,
    requirements,
    acceptance,
    tasks,
    risks,
    testCommands: testCommandRecords.map(({ argv }) => argv),
    testCommandRecords,
    decisions,
    sourceLines: lines,
  };
}

export const parseBaseline = parseFullBaseline;
