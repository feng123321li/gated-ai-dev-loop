const CONTROL = /[\u0000-\u001F\u007F-\u009F]/;
const SHELL_EXECUTABLES = new Set([
  'ash', 'bash', 'csh', 'dash', 'elvish', 'fish', 'hush', 'ksh', 'ksh93', 'mksh', 'nu', 'nushell',
  'osh', 'pdksh', 'sh', 'tcsh', 'xonsh', 'ysh', 'zsh',
  'cmd', 'command.com', 'powershell', 'pwsh',
]);
const STRING_INTERPRETER_FLAGS = new Map([
  ['bun', new Set(['-e', '--eval', '-p', '--print'])],
  ['deno', new Set(['-e', '--eval'])],
  ['lua', new Set(['-e'])],
  ['node', new Set(['-e', '--eval', '-p', '--print'])],
  ['perl', new Set(['-e'])],
  ['php', new Set([
    '-r', '--run', '-B', '--process-begin', '-R', '--process-code', '-E', '--process-end',
  ])],
  ['py', new Set(['-c', '--command'])],
  ['python', new Set(['-c', '--command'])],
  ['pypy', new Set(['-c', '--command'])],
  ['ruby', new Set(['-e', '--eval'])],
]);
const STRING_INTERPRETER_SUBCOMMANDS = new Map([
  ['deno', new Set(['eval'])],
]);
const INTERPRETER_OPTIONS_WITH_VALUES = new Map([
  ['deno', new Set(['--config', '--import-map', '--node-modules-dir'])],
  ['lua', new Set(['-l'])],
  ['node', new Set([
    '-r', '--require', '--import', '--loader', '--experimental-loader', '--conditions',
    '--input-type', '--redirect-warnings', '--env-file', '--env-file-if-exists',
    '--icu-data-dir', '--openssl-config', '--snapshot-blob', '--inspect-port',
    '--diagnostic-dir', '--report-dir', '--report-directory', '--report-filename',
    '--test-concurrency', '--test-name-pattern', '--test-reporter',
    '--test-reporter-destination', '--test-shard', '--test-timeout', '--title',
    '--experimental-default-type', '--dns-result-order', '--unhandled-rejections',
    '--disable-proto', '--trace-event-categories',
  ])],
  ['perl', new Set(['-I'])],
  ['php', new Set(['-c', '-d', '-z'])],
  ['python', new Set(['-W', '-X', '-Q', '--check-hash-based-pycs'])],
  ['pypy', new Set(['-W', '-X', '-Q'])],
  ['ruby', new Set(['-I', '-r', '-C', '-E', '--encoding', '--external-encoding', '--internal-encoding'])],
]);
const INTERPRETER_ENTRYPOINT_OPTIONS = new Map([
  ['node', new Set(['--run'])],
  ['php', new Set(['-f'])],
  ['python', new Set(['-m'])],
  ['pypy', new Set(['-m'])],
  ['ruby', new Set(['-S'])],
]);
const INTERPRETER_BOOLEAN_OPTIONS = new Map([
  ['node', new Set(['-c', '--check', '--no-warnings', '--test'])],
]);
const EXEC_OPTIONS_WITH_VALUES = new Set([
  '-p', '--package', '--cache', '--userconfig', '--registry', '--prefix', '-w', '--workspace', '--loglevel',
]);
const EXEC_BOOLEAN_OPTIONS = new Set([
  '-y', '--yes', '--no', '-q', '--quiet', '-s', '--silent', '--workspaces',
  '--include-workspace-root', '--ignore-existing',
]);
const NPM_OPTIONS_WITH_VALUES = new Set([
  '-C', '--prefix', '--cache', '--userconfig', '--registry', '-w', '--workspace', '--loglevel',
]);
const NPM_BOOLEAN_OPTIONS = new Set([
  '-q', '--quiet', '-s', '--silent', '--verbose', '--workspaces', '--include-workspace-root',
  '--no-progress', '--color', '--no-color',
]);

function executableName(value) {
  return value.toLowerCase().replaceAll('\\', '/').split('/').at(-1).replace(/\.(?:exe|cmd|bat)$/, '');
}

function isShellExecutable(value) { return SHELL_EXECUTABLES.has(executableName(value)); }

function interpreterKind(value) {
  const name = executableName(value);
  if (/^pyw?$/.test(name)) return 'python';
  if (/^(?:pythonw?)\d*(?:\.\d+)*$/.test(name)) return 'python';
  if (/^pypy\d*(?:\.\d+)*$/.test(name)) return 'pypy';
  if (/^node(?:js)?\d*(?:\.\d+)*$/.test(name)) return 'node';
  if (/^rubyw\d*(?:\.\d+)*$/.test(name)) return 'ruby';
  if (/^wperl\d*(?:\.\d+)*$/.test(name)) return 'perl';
  if (/^(?:bun|deno)\d*(?:\.\d+)*$/.test(name)) return name.startsWith('bun') ? 'bun' : 'deno';
  for (const kind of ['ruby', 'perl', 'php', 'lua']) {
    if (new RegExp(`^${kind}\\d*(?:\\.\\d+)*$`).test(name)) return kind;
  }
  return STRING_INTERPRETER_FLAGS.has(name) ? name : undefined;
}

function isStringFlag(argument, kind, flags) {
  if (kind === 'python' || kind === 'pypy') {
    if (!/^-[^-]/.test(argument)) return [...flags].some((flag) => argument === flag
      || (flag.startsWith('--') && argument.startsWith(`${flag}=`)));
    for (const option of argument.slice(1)) {
      if (option === 'c') return true;
      if (['W', 'X', 'Q'].includes(option)) return false;
    }
    return false;
  }
  if (kind === 'perl' && /^-[^-]/.test(argument)) {
    for (const option of argument.slice(1)) {
      if (option === 'e' || option === 'E') return true;
      if (['I', 'M', 'm', 'F', 'C', 'D', 'U'].includes(option)) return false;
    }
  }
  if (kind === 'ruby' && /^-[a-z]*e/.test(argument)) return true;
  return [...flags].some((flag) => argument === flag
    || (flag.startsWith('--') && argument.startsWith(`${flag}=`))
    || (flag.length === 2 && argument.startsWith(flag) && argument.length > flag.length));
}

function invokesInterpreterString(argv, executableIndex, kind, flags) {
  const subcommands = STRING_INTERPRETER_SUBCOMMANDS.get(kind) ?? new Set();
  const valueOptions = INTERPRETER_OPTIONS_WITH_VALUES.get(kind) ?? new Set();
  const entrypointOptions = INTERPRETER_ENTRYPOINT_OPTIONS.get(kind) ?? new Set();
  const booleanOptions = INTERPRETER_BOOLEAN_OPTIONS.get(kind) ?? new Set();
  let unknownOptionMayTakeValue = false;
  for (let index = executableIndex + 1; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === '--') return false;
    if (isStringFlag(argument, kind, flags)) return true;
    if (subcommands.has(argument.toLowerCase())) return true;
    if ([...entrypointOptions].some((option) => argument === option
        || argument.startsWith(`${option}=`)
        || (option.length === 2 && argument.startsWith(option) && argument.length > 2))) return false;
    const valueOption = [...valueOptions].find((option) => argument === option
      || argument.startsWith(`${option}=`)
      || (option.length === 2 && argument.startsWith(option) && argument.length > 2));
    if (valueOption) {
      if (argument === valueOption) index++;
      unknownOptionMayTakeValue = false;
      continue;
    }
    if (booleanOptions.has(argument)) {
      unknownOptionMayTakeValue = false;
      continue;
    }
    if (argument.startsWith('-')) {
      unknownOptionMayTakeValue = kind === 'node' && !argument.includes('=');
      continue;
    }
    if (unknownOptionMayTakeValue) {
      const laterCreatesString = argv.slice(index + 1).some((later) => isStringFlag(later, kind, flags)
        || subcommands.has(later.toLowerCase()));
      if (laterCreatesString) {
        unknownOptionMayTakeValue = false;
        continue;
      }
    }
    return false;
  }
  return false;
}

function optionMatch(argument, options) {
  return [...options].find((value) => argument === value
    || (value.startsWith('--') && argument.startsWith(`${value}=`))
    || (value.length === 2 && argument.startsWith(value) && argument.length > 2));
}

function envCommand(argv, start) {
  const optionsWithValues = new Set(['-u', '--unset', '-C', '--chdir', '-a', '--argv0']);
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === '-S' || argument.startsWith('-S') || argument === '--split-string'
        || argument.startsWith('--split-string=')) return { rejected: true };
    if (argument === '--') return { index: index + 1 };
    const option = optionMatch(argument, optionsWithValues);
    if (option) {
      if (argument === option) index++;
      continue;
    }
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(argument) || argument.startsWith('-')) continue;
    return { index };
  }
  return {};
}

function multiCallApplet(argv, start) {
  const argument = argv[start];
  if (argument === '--') return start + 1;
  if (argument?.startsWith('-')) return undefined;
  return argument === undefined ? undefined : start;
}

function wrapperCommand(argv, start, optionsWithValues = new Set()) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === '--') return index + 1;
    const option = optionMatch(argument, optionsWithValues);
    if (option) {
      if (argument === option) index++;
      continue;
    }
    if (argument.startsWith('-')) continue;
    return index;
  }
  return undefined;
}

function execCommand(argv, start) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === '--') return { index: index + 1 };
    if (argument === '-c' || (argument.startsWith('-c') && !argument.startsWith('--'))
        || argument === '--call' || argument.startsWith('--call=')) return { rejected: true };
    const valueOption = optionMatch(argument, EXEC_OPTIONS_WITH_VALUES);
    if (valueOption) {
      if (argument === valueOption) {
        if (index + 1 >= argv.length) return { rejected: true };
        index++;
      }
      continue;
    }
    if (EXEC_BOOLEAN_OPTIONS.has(argument)
        || [...EXEC_BOOLEAN_OPTIONS].some((option) => option.startsWith('--') && argument.startsWith(`${option}=`))) continue;
    if (argument.startsWith('-')) return { rejected: true };
    return { index };
  }
  return {};
}

function npmCommand(argv, start) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === '--') return {};
    const valueOption = optionMatch(argument, NPM_OPTIONS_WITH_VALUES);
    if (valueOption) {
      if (argument === valueOption) {
        if (index + 1 >= argv.length) return { rejected: true };
        index++;
      }
      continue;
    }
    if (NPM_BOOLEAN_OPTIONS.has(argument)
        || [...NPM_BOOLEAN_OPTIONS].some((option) => option.startsWith('--') && argument.startsWith(`${option}=`))) continue;
    if (argument.startsWith('-')) return { rejected: true };
    return ['exec', 'exe', 'x'].includes(argument.toLowerCase()) ? execCommand(argv, index + 1) : {};
  }
  return {};
}

function inspectExecutableChain(argv, executableIndex = 0, depth = 0) {
  if (executableIndex >= argv.length || depth > 8) return false;
  const name = executableName(argv[executableIndex]);
  if (isShellExecutable(name)) return true;

  const kind = interpreterKind(name);
  const flags = kind && STRING_INTERPRETER_FLAGS.get(kind);
  if (flags) return invokesInterpreterString(argv, executableIndex, kind, flags);

  if (name === 'env') {
    const command = envCommand(argv, executableIndex + 1);
    return command.rejected === true
      || (command.index !== undefined && inspectExecutableChain(argv, command.index, depth + 1));
  }
  if (name === 'busybox' || name === 'toybox') {
    const command = multiCallApplet(argv, executableIndex + 1);
    return command !== undefined && inspectExecutableChain(argv, command, depth + 1);
  }
  if (name === 'wsl') {
    const command = wrapperCommand(
      argv,
      executableIndex + 1,
      new Set(['-d', '--distribution', '-u', '--user', '--cd', '--shell-type']),
    );
    return command === undefined || inspectExecutableChain(argv, command, depth + 1);
  }
  if (name === 'npx') {
    const command = execCommand(argv, executableIndex + 1);
    return command.rejected === true
      || (command.index !== undefined && inspectExecutableChain(argv, command.index, depth + 1));
  }
  if (name === 'npm') {
    const command = npmCommand(argv, executableIndex + 1);
    return command.rejected === true
      || (command.index !== undefined && inspectExecutableChain(argv, command.index, depth + 1));
  }
  return false;
}

export function normalizeTestArgv(value) {
  if (!Array.isArray(value) || value.length === 0 || typeof value[0] !== 'string'
      || value[0].trim().length === 0 || /\s/.test(value[0])
      || value.some((entry) => typeof entry !== 'string' || entry.length === 0 || CONTROL.test(entry))
      || inspectExecutableChain(value)) return null;
  return [...value];
}

export function validateTestArgv(value) { return normalizeTestArgv(value) !== null; }
