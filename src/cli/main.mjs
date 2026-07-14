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

export const COMMANDS = Object.freeze(['route', 'start', 'prepare', 'freeze', 'self-check', 'accept']);
const VALUE_OPTIONS = new Set([
  '--mode', '--signals', '--task', '--brief', '--host-runtime', '--baseline', '--source',
  '--round', '--snapshot', '--timeout-ms', '--reviewer', '--review-result',
]);
const REPEATABLE_OPTIONS = new Set(['--source']);
const FLAG_OPTIONS = new Set(['--json', '--help', '--confirmed']);
const ROUTE_OPTIONS = new Set(['--json', '--help', '--mode', '--signals']);
const START_OPTIONS = new Set(['--json', '--help', '--mode', '--signals', '--task', '--brief', '--host-runtime', '--confirmed']);
const PREPARE_OPTIONS = new Set(['--json', '--help', '--task', '--baseline', '--source']);
const FREEZE_OPTIONS = new Set(['--json', '--help', '--task', '--confirmed']);
const SELF_CHECK_OPTIONS = new Set(['--json', '--help', '--task', '--round', '--snapshot', '--timeout-ms']);
const ACCEPT_OPTIONS = new Set(['--json', '--help', '--task', '--round', '--snapshot', '--timeout-ms', '--reviewer', '--review-result']);
const help = `Usage: gated-loop <command> [options]\n\nCommands:\n${COMMANDS.map((command) => `  ${command}`).join('\n')}\n\nGate commands:\n  self-check --task <id> [--round 1] [--snapshot <file>]\n  accept --task <id> [--round 1] [--reviewer human|auto|codex|claude]\n\nAcceptance defaults to human handling unless a host reviewer result or reviewer capability is supplied.\n`;

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

async function runBaselineCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises;
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
    throw new GatedLoopError('UNKNOWN_COMMAND', `Unknown command: ${parsed.command}`);
  } catch (error) {
    const stable = error instanceof GatedLoopError ? error : new GatedLoopError('INTERNAL_ERROR', 'Unexpected error');
    stderr(jsonOutput ? renderJson({ ok: false, error: { code: stable.code, message: stable.message, details: stable.details } }) : renderError(stable));
    return stable.exitCode;
  }
}
