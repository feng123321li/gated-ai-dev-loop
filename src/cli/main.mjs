import * as fsPromises from 'node:fs/promises';

import { routeTask } from '../commands/route.mjs';
import { startTask } from '../commands/start.mjs';
import { GatedLoopError } from '../core/errors.mjs';
import { readSafeRegularFile } from '../core/fs-safe.mjs';
import { freezeFullBaseline } from '../full/freeze.mjs';
import { prepareFullBaseline } from '../full/prepare.mjs';
import { renderError, renderJson } from './output.mjs';
import { runSelfCheck } from '../acceptance/self-check.mjs';
import { runAcceptance } from '../acceptance/accept.mjs';
import { isAgentRuntime } from '../mode/host-runtime.mjs';
import { resolveSelfHostingPolicy } from '../work-items/model.mjs';
import {
  buildTaskContext,
  claimTask,
  freezeWorkItem,
  listReadyTasks,
  prepareWorkItem,
  recordDelivery,
  recordTaskResult,
  recordWorkItemGate,
  retryWorkItem,
  reviseWorkItem,
} from '../work-items/runtime.mjs';

export const COMMANDS = Object.freeze([
  'route', 'start', 'prepare', 'freeze', 'self-check', 'accept',
  'prepare-item', 'freeze-item', 'revise-item', 'ready-tasks', 'task-context',
  'claim-task', 'task-result', 'retry-item', 'gate-item', 'delivery-item',
]);
export const HIERARCHICAL_COMMANDS = Object.freeze([
  'prepare-item', 'freeze-item', 'revise-item', 'ready-tasks', 'task-context',
  'claim-task', 'task-result', 'retry-item', 'gate-item', 'delivery-item',
]);
const VALUE_OPTIONS = new Set([
  '--mode', '--signals', '--task', '--brief', '--host-runtime', '--baseline', '--source',
  '--round', '--snapshot', '--timeout-ms', '--reviewer', '--review-result',
  '--definition', '--item', '--delivery', '--owner', '--operation', '--status', '--evidence',
  '--expected-baseline', '--action',
]);
const REPEATABLE_OPTIONS = new Set(['--source']);
const FLAG_OPTIONS = new Set(['--json', '--help', '--confirmed', '--dogfood']);
const ROUTE_OPTIONS = new Set(['--json', '--help', '--mode', '--signals']);
const START_OPTIONS = new Set(['--json', '--help', '--mode', '--signals', '--task', '--brief', '--host-runtime', '--confirmed', '--dogfood']);
const PREPARE_OPTIONS = new Set(['--json', '--help', '--task', '--baseline', '--source', '--dogfood']);
const FREEZE_OPTIONS = new Set(['--json', '--help', '--task', '--confirmed', '--dogfood']);
const SELF_CHECK_OPTIONS = new Set(['--json', '--help', '--task', '--round', '--snapshot', '--timeout-ms', '--dogfood']);
const ACCEPT_OPTIONS = new Set(['--json', '--help', '--task', '--round', '--snapshot', '--timeout-ms', '--reviewer', '--review-result', '--dogfood']);
const PREPARE_ITEM_OPTIONS = new Set(['--json', '--help', '--definition', '--host-runtime', '--dogfood']);
const FREEZE_ITEM_OPTIONS = new Set(['--json', '--help', '--item', '--expected-baseline', '--confirmed', '--dogfood']);
const REVISE_ITEM_OPTIONS = new Set(['--json', '--help', '--definition', '--expected-baseline', '--confirmed', '--dogfood']);
const READY_TASKS_OPTIONS = new Set(['--json', '--help', '--delivery']);
const TASK_CONTEXT_OPTIONS = new Set(['--json', '--help', '--item', '--dogfood']);
const CLAIM_TASK_OPTIONS = new Set(['--json', '--help', '--item', '--owner', '--operation', '--dogfood']);
const TASK_RESULT_OPTIONS = new Set(['--json', '--help', '--item', '--operation', '--status', '--evidence', '--dogfood']);
const GATE_ITEM_OPTIONS = new Set(['--json', '--help', '--item', '--status', '--evidence', '--dogfood']);
const RETRY_ITEM_OPTIONS = new Set(['--json', '--help', '--item', '--expected-baseline', '--confirmed', '--dogfood']);
const DELIVERY_ITEM_OPTIONS = new Set(['--json', '--help', '--item', '--action', '--evidence', '--dogfood']);
const help = `Usage: gated-loop <command> [options]\n\nCommands:\n${COMMANDS.map((command) => `  ${command}`).join('\n')}\n\nHierarchical work items:\n  prepare-item --definition <file> --host-runtime <agent>\n  freeze-item --item <id> --expected-baseline <sha256> --confirmed\n  revise-item --definition <file> --expected-baseline <sha256> --confirmed\n  ready-tasks --delivery <id>\n  task-context --item <id>\n  claim-task --item <id> --owner <owner> --operation <id>\n  task-result --item <id> --operation <id> --status IMPLEMENTED|BLOCKED --evidence <file>\n  retry-item --item <id> --expected-baseline <sha256> --confirmed\n  gate-item --item <id> --status PASS|FAIL --evidence <file>\n  delivery-item --item <delivery-id> --action INDEPENDENT_REVIEW_PASS|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence <file>\n\nIn the implementation repository, add --dogfood to every hierarchical command that writes runtime state.\n\nGate commands:\n  self-check --task <id> [--round 1] [--snapshot <file>]\n  accept --task <id> [--round 1] [--reviewer human|auto|codex|claude]\n\nThe historical start/prepare/freeze commands are v1 compatibility surfaces.\nAcceptance defaults to human handling unless a host reviewer result or reviewer capability is supplied.\n`;
const hierarchicalHelp = `Usage: hdg <command> [options]\n\nCommands:\n${HIERARCHICAL_COMMANDS.map((command) => `  ${command}`).join('\n')}\n\n  prepare-item --definition <file> --host-runtime <agent>\n  freeze-item --item <id> --expected-baseline <sha256> --confirmed\n  revise-item --definition <file> --expected-baseline <sha256> --confirmed\n  ready-tasks --delivery <id>\n  task-context --item <id>\n  claim-task --item <id> --owner <owner> --operation <id>\n  task-result --item <id> --operation <id> --status IMPLEMENTED|BLOCKED --evidence <file>\n  retry-item --item <id> --expected-baseline <sha256> --confirmed\n  gate-item --item <id> --status PASS|FAIL --evidence <file>\n  delivery-item --item <delivery-id> --action INDEPENDENT_REVIEW_PASS|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence <file>\n\nIn the hierarchical-delivery-governance implementation repository, every command that writes control state also requires --dogfood.\n`;

function parse(argv) {
  const seen = new Set();
  const values = {};
  const positionals = [];
  for (let index = 0; index < argv.length; index++) {
    const item = argv[index];
    if (!item.startsWith('--')) { positionals.push(item); continue; }
    if (!VALUE_OPTIONS.has(item) && !FLAG_OPTIONS.has(item)) throw new GatedLoopError('UNKNOWN_OPTION', `Unknown option: ${item}`);
    if (seen.has(item) && !REPEATABLE_OPTIONS.has(item)) throw new GatedLoopError('DUPLICATE_OPTION', `Duplicate option: ${item}`);
    seen.add(item);
    if (VALUE_OPTIONS.has(item)) {
      const value = argv[index + 1];
      if (!value || value.startsWith('--')) throw new GatedLoopError('OPTION_VALUE_REQUIRED', `Missing value for option: ${item}`);
      if (REPEATABLE_OPTIONS.has(item)) (values[item] ??= []).push(value);
      else values[item] = value;
      index++;
    }
  }
  const [command, ...extraPositionals] = positionals;
  const acceptsDescription = command === 'route' || command === 'start';
  if (extraPositionals.length > (acceptsDescription ? 1 : 0)) {
    throw new GatedLoopError('UNKNOWN_OPTION', `Unexpected positional argument: ${extraPositionals.at(-1)}`);
  }
  const commandOptions = {
    route: ROUTE_OPTIONS, start: START_OPTIONS, prepare: PREPARE_OPTIONS, freeze: FREEZE_OPTIONS,
    'self-check': SELF_CHECK_OPTIONS, accept: ACCEPT_OPTIONS,
    'prepare-item': PREPARE_ITEM_OPTIONS, 'freeze-item': FREEZE_ITEM_OPTIONS,
    'revise-item': REVISE_ITEM_OPTIONS,
    'ready-tasks': READY_TASKS_OPTIONS, 'task-context': TASK_CONTEXT_OPTIONS,
    'claim-task': CLAIM_TASK_OPTIONS, 'task-result': TASK_RESULT_OPTIONS,
    'retry-item': RETRY_ITEM_OPTIONS,
    'gate-item': GATE_ITEM_OPTIONS,
    'delivery-item': DELIVERY_ITEM_OPTIONS,
  };
  if (commandOptions[command]) {
    for (const option of seen) if (!commandOptions[command].has(option)) {
      throw new GatedLoopError('UNKNOWN_OPTION', `Option is not valid for ${command}: ${option}`);
    }
  }
  if (values['--mode'] !== undefined && !['full', 'light'].includes(values['--mode'])) {
    throw new GatedLoopError('OPTION_VALUE_INVALID', '--mode must be full or light');
  }
  if (values['--host-runtime'] !== undefined && !isAgentRuntime(values['--host-runtime'])) {
    throw new GatedLoopError('OPTION_VALUE_INVALID', '--host-runtime must be a safe lowercase Agent identifier');
  }
  if (values['--reviewer'] !== undefined && !['human', 'auto', 'codex', 'claude'].includes(values['--reviewer'])) {
    throw new GatedLoopError('OPTION_VALUE_INVALID', '--reviewer must be human, auto, codex, or claude');
  }
  if (values['--timeout-ms'] !== undefined && (!/^\d+$/.test(values['--timeout-ms']) || Number(values['--timeout-ms']) < 1)) {
    throw new GatedLoopError('OPTION_VALUE_INVALID', '--timeout-ms must be a positive integer');
  }
  return {
    command,
    description: extraPositionals[0],
    json: seen.has('--json'),
    help: seen.has('--help'),
    confirmed: seen.has('--confirmed'),
    dogfood: seen.has('--dogfood'),
    values,
  };
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

async function runModeCommand(parsed, io) {
  if (typeof parsed.description !== 'string' || parsed.description.trim().length === 0) {
    throw new GatedLoopError('DESCRIPTION_REQUIRED', `${parsed.command} requires a task description`);
  }
  const cwd = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises;
  if (parsed.command === 'start' && parsed.values['--signals'] === '-' && parsed.values['--brief'] === '-') {
    throw new GatedLoopError('INPUT_STDIN_CONFLICT', 'Signals and brief cannot both use stdin');
  }
  const supplied = parsed.values['--signals']
    ? await readStructured(parsed.values['--signals'], 'MODE_INPUT', { cwd, fs, stdin: io.stdin })
    : {};
  const signals = { ...supplied, description: parsed.description };
  if (parsed.values['--mode']) signals.requestedMode = parsed.values['--mode'];
  if (parsed.command === 'route') return routeTask(signals);
  const brief = parsed.values['--brief']
    ? await readStructured(parsed.values['--brief'], 'LIGHT_BRIEF', { cwd, fs, stdin: io.stdin })
    : undefined;
  return startTask({
    root: cwd,
    task: parsed.values['--task'],
    signals,
    brief,
    confirmed: parsed.confirmed,
    explicitDogfood: parsed.dogfood,
    hostRuntime: parsed.values['--host-runtime'],
    generateTaskId: io.generateTaskId,
    now: io.now,
    beforeCommit: io.beforeCommit,
    fs,
  });
}

function required(parsed, option) {
  const value = parsed.values[option];
  if (value === undefined) throw new GatedLoopError('OPTION_REQUIRED', `${parsed.command} requires ${option}`);
  return value;
}

async function assertCliMutationAllowed(root, explicitDogfood, fs) {
  let packageName;
  try {
    const packageJson = JSON.parse((await readSafeRegularFile(root, 'package.json', { fs })).toString('utf8'));
    if (typeof packageJson?.name === 'string') packageName = packageJson.name;
  } catch (error) {
    if (error?.code !== 'ENOENT' && error?.code !== 'PATH_MISSING' && !(error instanceof SyntaxError)) throw error;
  }
  const policy = resolveSelfHostingPolicy({ packageName, explicitDogfood });
  if (policy.createsRuntimePackage === false) {
    throw new GatedLoopError(
      'SELF_HOSTING_DOGFOOD_REQUIRED',
      'The hierarchical governance implementation repository requires explicit dogfood for runtime mutations',
    );
  }
}

async function runBaselineCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises;
  await assertCliMutationAllowed(root, parsed.dogfood, fs);
  const task = required(parsed, '--task');
  if (parsed.command === 'prepare') {
    return prepareFullBaseline({
      root,
      task,
      baseline: required(parsed, '--baseline'),
      sources: parsed.values['--source'] ?? [],
      now: io.now,
      beforeCommit: io.beforeCommit,
      fs,
    });
  }
  return freezeFullBaseline({
    root,
    task,
    confirmed: parsed.confirmed,
    now: io.now,
    beforeCommit: io.beforeCommit,
    fs,
  });
}

async function runGateCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises;
  await assertCliMutationAllowed(root, parsed.dogfood, fs);
  const common = {
    root, task: required(parsed, '--task'), round: parsed.values['--round'],
    snapshot: parsed.values['--snapshot'], timeoutMs: parsed.values['--timeout-ms'] ? Number(parsed.values['--timeout-ms']) : undefined,
    fs, runProcessImpl: io.runProcess, now: io.now,
  };
  if (parsed.command === 'self-check') return runSelfCheck(common);
  const reviewResult = parsed.values['--review-result']
    ? await readStructured(parsed.values['--review-result'], 'REVIEW_RESULT', { cwd: root, fs, stdin: io.stdin })
    : undefined;
  return runAcceptance({
    ...common, reviewer: parsed.values['--reviewer'], reviewResult,
    reviewerInvoker: io.reviewerInvoker,
  });
}

async function runWorkItemCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises;
  const common = { root, fs, now: io.now, explicitDogfood: parsed.dogfood };
  if (parsed.command === 'prepare-item') {
    const definition = await readStructured(
      required(parsed, '--definition'),
      'WORK_ITEM_DEFINITION',
      { cwd: root, fs, stdin: io.stdin },
    );
    return prepareWorkItem({
      ...common,
      definition,
      hostRuntime: required(parsed, '--host-runtime'),
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
  if (parsed.command === 'revise-item') {
    const definition = await readStructured(
      required(parsed, '--definition'),
      'WORK_ITEM_DEFINITION',
      { cwd: root, fs, stdin: io.stdin },
    );
    return reviseWorkItem({
      ...common,
      definition,
      expectedBaselineFingerprint: required(parsed, '--expected-baseline'),
      confirmed: parsed.confirmed,
    });
  }
  if (parsed.command === 'ready-tasks') {
    return listReadyTasks({ ...common, deliveryId: required(parsed, '--delivery') });
  }
  if (parsed.command === 'task-context') {
    return buildTaskContext({ ...common, id: required(parsed, '--item') });
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

export async function runCli(argv, io = {}) {
  const stdout = io.stdout ?? ((value) => process.stdout.write(value));
  const stderr = io.stderr ?? ((value) => process.stderr.write(value));
  let jsonOutput = argv.includes('--json');
  try {
    const parsed = parse(argv);
    jsonOutput = parsed.json;
    if (parsed.help || !parsed.command) { stdout(help); return 0; }
    if (!COMMANDS.includes(parsed.command)) throw new GatedLoopError('UNKNOWN_COMMAND', `Unknown command: ${parsed.command}`);
    if (parsed.command === 'route' || parsed.command === 'start') {
      const result = await runModeCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: true, result }) : renderJson(result));
      return 0;
    }
    if (parsed.command === 'prepare' || parsed.command === 'freeze') {
      const result = await runBaselineCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: true, result }) : renderJson(result));
      return 0;
    }
    if (parsed.command === 'self-check' || parsed.command === 'accept') {
      const result = await runGateCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: result.status === 'PASS', result }) : renderJson(result));
      return result.status === 'PASS' ? 0 : 2;
    }
    if ([
      'prepare-item', 'freeze-item', 'revise-item', 'ready-tasks', 'task-context',
      'claim-task', 'task-result', 'retry-item', 'gate-item', 'delivery-item',
    ].includes(parsed.command)) {
      const result = await runWorkItemCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: true, result }) : renderJson(result));
      return 0;
    }
    throw new GatedLoopError('UNKNOWN_COMMAND', `Unknown command: ${parsed.command}`);
  } catch (error) {
    const stable = error instanceof GatedLoopError ? error : new GatedLoopError('INTERNAL_ERROR', 'Unexpected error');
    stderr(jsonOutput ? renderJson({ ok: false, error: { code: stable.code, message: stable.message, details: stable.details } }) : renderError(stable));
    return stable.exitCode;
  }
}

export async function runHierarchicalCli(argv, io = {}) {
  const stdout = io.stdout ?? ((value) => process.stdout.write(value));
  const stderr = io.stderr ?? ((value) => process.stderr.write(value));
  const command = argv.find((value) => !value.startsWith('--'));
  if (!command || argv.includes('--help')) {
    stdout(hierarchicalHelp);
    return 0;
  }
  if (!HIERARCHICAL_COMMANDS.includes(command)) {
    const error = new GatedLoopError('UNKNOWN_COMMAND', `Unknown hdg command: ${command}`);
    const jsonOutput = argv.includes('--json');
    stderr(jsonOutput
      ? renderJson({ ok: false, error: { code: error.code, message: error.message, details: error.details } })
      : renderError(error));
    return error.exitCode;
  }
  return runCli(argv, io);
}
