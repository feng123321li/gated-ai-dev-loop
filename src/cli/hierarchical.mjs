import * as fsPromises from 'node:fs/promises';

import { GatedLoopError } from '../core/errors.mjs';
import { readSafeRegularFile } from '../core/fs-safe.mjs';
import { isAgentRuntime } from '../mode/host-runtime.mjs';
import {
  buildTaskContext,
  claimTask,
  freezeWorkItem,
  listReadyTasks,
  prepareWorkItem,
  promoteWorkItem,
  recordDelivery,
  recordTaskResult,
  recordWorkItemGate,
  retryWorkItem,
  reviseWorkItem,
  selectDevelopmentMode,
} from '../work-items/runtime.mjs';
import { renderError, renderJson } from './output.mjs';

export const HIERARCHICAL_COMMANDS = Object.freeze([
  'prepare-item',
  'freeze-item',
  'revise-item',
  'promote-item',
  'select-development-mode',
  'ready-tasks',
  'task-context',
  'claim-task',
  'task-result',
  'retry-item',
  'gate-item',
  'delivery-item',
]);

const VALUE_OPTIONS = new Set([
  '--definition', '--host-runtime', '--item', '--parent', '--owner', '--operation',
  '--status', '--evidence', '--expected-baseline', '--expected-parent-baseline',
  '--action', '--development-mode',
]);
const FLAG_OPTIONS = new Set(['--json', '--help', '--confirmed', '--dogfood']);
const COMMAND_OPTIONS = Object.freeze({
  'prepare-item': new Set(['--json', '--help', '--definition', '--host-runtime', '--dogfood']),
  'freeze-item': new Set(['--json', '--help', '--item', '--expected-baseline', '--confirmed', '--dogfood']),
  'revise-item': new Set(['--json', '--help', '--definition', '--expected-baseline', '--confirmed', '--dogfood']),
  'promote-item': new Set([
    '--json', '--help', '--item', '--parent', '--expected-baseline',
    '--expected-parent-baseline', '--confirmed', '--dogfood',
  ]),
  'ready-tasks': new Set(['--json', '--help', '--item']),
  'task-context': new Set(['--json', '--help', '--item', '--dogfood']),
  'select-development-mode': new Set([
    '--json', '--help', '--item', '--development-mode', '--expected-baseline', '--confirmed', '--dogfood',
  ]),
  'claim-task': new Set(['--json', '--help', '--item', '--owner', '--operation', '--dogfood']),
  'task-result': new Set(['--json', '--help', '--item', '--operation', '--status', '--evidence', '--dogfood']),
  'retry-item': new Set(['--json', '--help', '--item', '--expected-baseline', '--confirmed', '--dogfood']),
  'gate-item': new Set(['--json', '--help', '--item', '--status', '--evidence', '--dogfood']),
  'delivery-item': new Set(['--json', '--help', '--item', '--action', '--evidence', '--dogfood']),
});

const usage = `Usage: hdg <command> [options]

Commands:
${HIERARCHICAL_COMMANDS.map((command) => `  ${command}`).join('\n')}

  prepare-item --definition <file> --host-runtime <agent>
  freeze-item --item <id> --expected-baseline <sha256> --confirmed
  revise-item --definition <file> --expected-baseline <sha256> --confirmed
  promote-item --item <root-id> --parent <frozen-parent-id> --expected-baseline <sha256> --expected-parent-baseline <sha256> --confirmed
  select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
  ready-tasks --item <root-or-subtree-id>
  task-context --item <task-id>
  claim-task --item <task-id> --owner <owner> --operation <id>
  task-result --item <task-id> --operation <id> --status IMPLEMENTED|BLOCKED --evidence <file>
  retry-item --item <id> --expected-baseline <sha256> --confirmed
  gate-item --item <id> --status PASS|FAIL --evidence <file>
  delivery-item --item <delivery-id> --action INDEPENDENT_REVIEW_PASS|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence <file>

In the hierarchical-delivery-governance implementation repository, every command that writes control state also requires --dogfood.
`;

function parse(argv) {
  const seen = new Set();
  const values = {};
  const positionals = [];
  for (let index = 0; index < argv.length; index++) {
    const item = argv[index];
    if (!item.startsWith('--')) {
      positionals.push(item);
      continue;
    }
    if (!VALUE_OPTIONS.has(item) && !FLAG_OPTIONS.has(item)) {
      throw new GatedLoopError('UNKNOWN_OPTION', `Unknown option: ${item}`);
    }
    if (seen.has(item)) throw new GatedLoopError('DUPLICATE_OPTION', `Duplicate option: ${item}`);
    seen.add(item);
    if (VALUE_OPTIONS.has(item)) {
      const value = argv[index + 1];
      if (!value || value.startsWith('--')) {
        throw new GatedLoopError('OPTION_VALUE_REQUIRED', `Missing value for option: ${item}`);
      }
      values[item] = value;
      index += 1;
    }
  }
  const [command, ...extraPositionals] = positionals;
  if (extraPositionals.length > 0) {
    throw new GatedLoopError('UNKNOWN_OPTION', `Unexpected positional argument: ${extraPositionals.at(-1)}`);
  }
  if (COMMAND_OPTIONS[command]) {
    for (const option of seen) {
      if (!COMMAND_OPTIONS[command].has(option)) {
        throw new GatedLoopError('UNKNOWN_OPTION', `Option is not valid for ${command}: ${option}`);
      }
    }
  }
  if (values['--host-runtime'] !== undefined && !isAgentRuntime(values['--host-runtime'])) {
    throw new GatedLoopError('OPTION_VALUE_INVALID', '--host-runtime must be a safe lowercase Agent identifier');
  }
  if (values['--development-mode'] !== undefined
      && !['active', 'manual'].includes(values['--development-mode'])) {
    throw new GatedLoopError('OPTION_VALUE_INVALID', '--development-mode must be active or manual');
  }
  return {
    command,
    json: seen.has('--json'),
    confirmed: seen.has('--confirmed'),
    dogfood: seen.has('--dogfood'),
    values,
  };
}

function required(parsed, option) {
  const value = parsed.values[option];
  if (value === undefined) throw new GatedLoopError('OPTION_REQUIRED', `${parsed.command} requires ${option}`);
  return value;
}

async function readStructured(source, kind, { cwd, fs, stdin }) {
  let text;
  try {
    if (source === '-') {
      if (stdin !== undefined) text = typeof stdin === 'function' ? await stdin() : stdin;
      else text = await fs.readFile(0, 'utf8');
    } else {
      const portable = String(source).replaceAll('\\', '/').toLowerCase();
      const basename = portable.split('/').at(-1);
      if (basename.startsWith('.env') || portable.includes('production')) {
        throw new GatedLoopError('INPUT_PATH_FORBIDDEN', 'Structured input path is forbidden');
      }
      text = (await readSafeRegularFile(cwd, source, { fs })).toString('utf8');
    }
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError(`${kind}_READ`, `Unable to read ${kind.toLowerCase()} JSON`);
  }
  try {
    const value = JSON.parse(String(text));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('not a mapping');
    return value;
  } catch {
    throw new GatedLoopError(`${kind}_PARSE`, `${kind.toLowerCase()} JSON must be a mapping`);
  }
}

async function runWorkItemCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises;
  const common = { root, fs, now: io.now, explicitDogfood: parsed.dogfood };
  if (parsed.command === 'prepare-item' || parsed.command === 'revise-item') {
    const definition = await readStructured(
      required(parsed, '--definition'),
      'WORK_ITEM_DEFINITION',
      { cwd: root, fs, stdin: io.stdin },
    );
    if (parsed.command === 'prepare-item') {
      return prepareWorkItem({
        ...common,
        definition,
        hostRuntime: required(parsed, '--host-runtime'),
      });
    }
    return reviseWorkItem({
      ...common,
      definition,
      expectedBaselineFingerprint: required(parsed, '--expected-baseline'),
      confirmed: parsed.confirmed,
    });
  }
  if (parsed.command === 'freeze-item') {
    return freezeWorkItem({
      ...common,
      id: required(parsed, '--item'),
      expectedBaselineFingerprint: required(parsed, '--expected-baseline'),
      confirmed: parsed.confirmed,
    });
  }
  if (parsed.command === 'promote-item') {
    return promoteWorkItem({
      ...common,
      id: required(parsed, '--item'),
      parentId: required(parsed, '--parent'),
      expectedBaselineFingerprint: required(parsed, '--expected-baseline'),
      expectedParentBaselineFingerprint: required(parsed, '--expected-parent-baseline'),
      confirmed: parsed.confirmed,
    });
  }
  if (parsed.command === 'ready-tasks') {
    return listReadyTasks({ ...common, workItemId: required(parsed, '--item') });
  }
  if (parsed.command === 'task-context') {
    return buildTaskContext({ ...common, id: required(parsed, '--item') });
  }
  if (parsed.command === 'select-development-mode') {
    return selectDevelopmentMode({
      ...common,
      id: required(parsed, '--item'),
      mode: required(parsed, '--development-mode'),
      expectedBaselineFingerprint: required(parsed, '--expected-baseline'),
      confirmed: parsed.confirmed,
    });
  }
  if (parsed.command === 'claim-task') {
    return claimTask({
      ...common,
      id: required(parsed, '--item'),
      owner: required(parsed, '--owner'),
      operationId: required(parsed, '--operation'),
    });
  }
  if (parsed.command === 'retry-item') {
    return retryWorkItem({
      ...common,
      id: required(parsed, '--item'),
      expectedBaselineFingerprint: required(parsed, '--expected-baseline'),
      confirmed: parsed.confirmed,
    });
  }
  const evidence = await readStructured(
    required(parsed, '--evidence'),
    'WORK_ITEM_EVIDENCE',
    { cwd: root, fs, stdin: io.stdin },
  );
  if (parsed.command === 'task-result') {
    return recordTaskResult({
      ...common,
      id: required(parsed, '--item'),
      operationId: required(parsed, '--operation'),
      status: required(parsed, '--status'),
      evidence,
    });
  }
  if (parsed.command === 'delivery-item') {
    return recordDelivery({
      ...common,
      id: required(parsed, '--item'),
      action: required(parsed, '--action'),
      evidence,
    });
  }
  return recordWorkItemGate({
    ...common,
    id: required(parsed, '--item'),
    status: required(parsed, '--status'),
    evidence,
  });
}

export async function runHierarchicalCli(argv, io = {}) {
  const stdout = io.stdout ?? ((value) => process.stdout.write(value));
  const stderr = io.stderr ?? ((value) => process.stderr.write(value));
  const command = argv.find((value) => !value.startsWith('--'));
  const jsonOutput = argv.includes('--json');
  if (!command || argv.includes('--help')) {
    stdout(usage);
    return 0;
  }
  if (!HIERARCHICAL_COMMANDS.includes(command)) {
    const error = new GatedLoopError('UNKNOWN_COMMAND', `Unknown hdg command: ${command}`);
    stderr(jsonOutput
      ? renderJson({ ok: false, error: { code: error.code, message: error.message, details: error.details } })
      : renderError(error));
    return error.exitCode;
  }
  try {
    const parsed = parse(argv);
    const result = await runWorkItemCommand(parsed, io);
    stdout(parsed.json ? renderJson({ ok: true, result }) : renderJson(result));
    return 0;
  } catch (error) {
    const stable = error instanceof GatedLoopError
      ? error
      : new GatedLoopError('INTERNAL_ERROR', 'Unexpected error');
    stderr(jsonOutput
      ? renderJson({ ok: false, error: { code: stable.code, message: stable.message, details: stable.details } })
      : renderError(stable));
    return stable.exitCode;
  }
}
