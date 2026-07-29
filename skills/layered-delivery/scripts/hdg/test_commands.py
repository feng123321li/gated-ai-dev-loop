from __future__ import annotations

import re
from typing import Iterable


CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
SHELL_EXECUTABLES = {
    "ash", "bash", "csh", "dash", "elvish", "fish", "hush", "ksh", "ksh93", "mksh",
    "nu", "nushell", "osh", "pdksh", "sh", "tcsh", "xonsh", "ysh", "zsh", "cmd",
    "command.com", "powershell", "pwsh",
}
STRING_INTERPRETER_FLAGS = {
    "bun": {"-e", "--eval", "-p", "--print"},
    "deno": {"-e", "--eval"},
    "lua": {"-e"},
    "node": {"-e", "--eval", "-p", "--print"},
    "perl": {"-e"},
    "php": {"-r", "--run", "-B", "--process-begin", "-R", "--process-code", "-E", "--process-end"},
    "python": {"-c", "--command"},
    "pypy": {"-c", "--command"},
    "ruby": {"-e", "--eval"},
}
STRING_INTERPRETER_SUBCOMMANDS = {"deno": {"eval"}}
INTERPRETER_OPTIONS_WITH_VALUES = {
    "deno": {"--config", "--import-map", "--node-modules-dir"},
    "lua": {"-l"},
    "node": {
        "-r", "--require", "--import", "--loader", "--experimental-loader", "--conditions",
        "--input-type", "--redirect-warnings", "--env-file", "--env-file-if-exists",
        "--icu-data-dir", "--openssl-config", "--snapshot-blob", "--inspect-port",
        "--diagnostic-dir", "--report-dir", "--report-directory", "--report-filename",
        "--test-concurrency", "--test-name-pattern", "--test-reporter",
        "--test-reporter-destination", "--test-shard", "--test-timeout", "--title",
        "--experimental-default-type", "--dns-result-order", "--unhandled-rejections",
        "--disable-proto", "--trace-event-categories",
    },
    "perl": {"-I"},
    "php": {"-c", "-d", "-z"},
    "python": {"-W", "-X", "-Q", "--check-hash-based-pycs"},
    "pypy": {"-W", "-X", "-Q"},
    "ruby": {"-I", "-r", "-C", "-E", "--encoding", "--external-encoding", "--internal-encoding"},
}
INTERPRETER_ENTRYPOINT_OPTIONS = {
    "node": {"--run"}, "php": {"-f"}, "python": {"-m"}, "pypy": {"-m"}, "ruby": {"-S"},
}
INTERPRETER_BOOLEAN_OPTIONS = {"node": {"-c", "--check", "--no-warnings", "--test"}}
EXEC_OPTIONS_WITH_VALUES = {
    "-p", "--package", "--cache", "--userconfig", "--registry", "--prefix", "-w",
    "--workspace", "--loglevel",
}
EXEC_BOOLEAN_OPTIONS = {
    "-y", "--yes", "--no", "-q", "--quiet", "-s", "--silent", "--workspaces",
    "--include-workspace-root", "--ignore-existing",
}
NPM_OPTIONS_WITH_VALUES = {
    "-C", "--prefix", "--cache", "--userconfig", "--registry", "-w", "--workspace", "--loglevel",
}
NPM_BOOLEAN_OPTIONS = {
    "-q", "--quiet", "-s", "--silent", "--verbose", "--workspaces",
    "--include-workspace-root", "--no-progress", "--color", "--no-color",
}


def executable_name(value: str) -> str:
    name = value.lower().replace("\\", "/").split("/")[-1]
    return re.sub(r"\.(?:exe|cmd|bat)$", "", name)


def interpreter_kind(value: str) -> str | None:
    name = executable_name(value)
    if re.fullmatch(r"pyw?", name) or re.fullmatch(r"pythonw?\d*(?:\.\d+)*", name):
        return "python"
    if re.fullmatch(r"pypy\d*(?:\.\d+)*", name):
        return "pypy"
    if re.fullmatch(r"node(?:js)?\d*(?:\.\d+)*", name):
        return "node"
    if re.fullmatch(r"rubyw\d*(?:\.\d+)*", name):
        return "ruby"
    if re.fullmatch(r"wperl\d*(?:\.\d+)*", name):
        return "perl"
    if re.fullmatch(r"(?:bun|deno)\d*(?:\.\d+)*", name):
        return "bun" if name.startswith("bun") else "deno"
    for kind in ("ruby", "perl", "php", "lua"):
        if re.fullmatch(rf"{kind}\d*(?:\.\d+)*", name):
            return kind
    return name if name in STRING_INTERPRETER_FLAGS else None


def _option_match(argument: str, options: Iterable[str]) -> str | None:
    for value in options:
        if argument == value or (value.startswith("--") and argument.startswith(value + "=")):
            return value
        if len(value) == 2 and argument.startswith(value) and len(argument) > 2:
            return value
    return None


def _is_string_flag(argument: str, kind: str, flags: set[str]) -> bool:
    if kind in {"python", "pypy"}:
        if not re.match(r"^-[^-]", argument):
            return any(argument == flag or (flag.startswith("--") and argument.startswith(flag + "=")) for flag in flags)
        for option in argument[1:]:
            if option == "c":
                return True
            if option in {"W", "X", "Q"}:
                return False
        return False
    if kind == "perl" and re.match(r"^-[^-]", argument):
        for option in argument[1:]:
            if option in {"e", "E"}:
                return True
            if option in {"I", "M", "m", "F", "C", "D", "U"}:
                return False
    if kind == "ruby" and re.match(r"^-[a-z]*e", argument):
        return True
    return any(
        argument == flag
        or (flag.startswith("--") and argument.startswith(flag + "="))
        or (len(flag) == 2 and argument.startswith(flag) and len(argument) > len(flag))
        for flag in flags
    )


def _invokes_interpreter_string(argv: list[str], index: int, kind: str, flags: set[str]) -> bool:
    subcommands = STRING_INTERPRETER_SUBCOMMANDS.get(kind, set())
    value_options = INTERPRETER_OPTIONS_WITH_VALUES.get(kind, set())
    entrypoint_options = INTERPRETER_ENTRYPOINT_OPTIONS.get(kind, set())
    boolean_options = INTERPRETER_BOOLEAN_OPTIONS.get(kind, set())
    unknown_option_may_take_value = False
    cursor = index + 1
    while cursor < len(argv):
        argument = argv[cursor]
        if argument == "--":
            return False
        if _is_string_flag(argument, kind, flags) or argument.lower() in subcommands:
            return True
        if _option_match(argument, entrypoint_options):
            return False
        option = _option_match(argument, value_options)
        if option:
            if argument == option:
                cursor += 1
            unknown_option_may_take_value = False
            cursor += 1
            continue
        if argument in boolean_options:
            unknown_option_may_take_value = False
            cursor += 1
            continue
        if argument.startswith("-"):
            unknown_option_may_take_value = kind == "node" and "=" not in argument
            cursor += 1
            continue
        if unknown_option_may_take_value and any(
            _is_string_flag(later, kind, flags) or later.lower() in subcommands
            for later in argv[cursor + 1 :]
        ):
            unknown_option_may_take_value = False
            cursor += 1
            continue
        return False
    return False


def _env_command(argv: list[str], start: int) -> tuple[int | None, bool]:
    options = {"-u", "--unset", "-C", "--chdir", "-a", "--argv0"}
    cursor = start
    while cursor < len(argv):
        argument = argv[cursor]
        if argument == "-S" or argument.startswith("-S") or argument == "--split-string" or argument.startswith("--split-string="):
            return None, True
        if argument == "--":
            return cursor + 1, False
        option = _option_match(argument, options)
        if option:
            if argument == option:
                cursor += 1
            cursor += 1
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argument) or argument.startswith("-"):
            cursor += 1
            continue
        return cursor, False
    return None, False


def _wrapper_command(argv: list[str], start: int, options: set[str] | None = None) -> int | None:
    cursor = start
    options = options or set()
    while cursor < len(argv):
        argument = argv[cursor]
        if argument == "--":
            return cursor + 1
        option = _option_match(argument, options)
        if option:
            if argument == option:
                cursor += 1
            cursor += 1
            continue
        if argument.startswith("-"):
            cursor += 1
            continue
        return cursor
    return None


def _exec_command(argv: list[str], start: int) -> tuple[int | None, bool]:
    cursor = start
    while cursor < len(argv):
        argument = argv[cursor]
        if argument == "--":
            return cursor + 1, False
        if argument == "-c" or (argument.startswith("-c") and not argument.startswith("--")) or argument == "--call" or argument.startswith("--call="):
            return None, True
        option = _option_match(argument, EXEC_OPTIONS_WITH_VALUES)
        if option:
            if argument == option:
                if cursor + 1 >= len(argv):
                    return None, True
                cursor += 1
            cursor += 1
            continue
        if argument in EXEC_BOOLEAN_OPTIONS or any(
            item.startswith("--") and argument.startswith(item + "=") for item in EXEC_BOOLEAN_OPTIONS
        ):
            cursor += 1
            continue
        if argument.startswith("-"):
            return None, True
        return cursor, False
    return None, False


def _npm_command(argv: list[str], start: int) -> tuple[int | None, bool]:
    cursor = start
    while cursor < len(argv):
        argument = argv[cursor]
        if argument == "--":
            return None, False
        option = _option_match(argument, NPM_OPTIONS_WITH_VALUES)
        if option:
            if argument == option:
                if cursor + 1 >= len(argv):
                    return None, True
                cursor += 1
            cursor += 1
            continue
        if argument in NPM_BOOLEAN_OPTIONS or any(
            item.startswith("--") and argument.startswith(item + "=") for item in NPM_BOOLEAN_OPTIONS
        ):
            cursor += 1
            continue
        if argument.startswith("-"):
            return None, True
        return _exec_command(argv, cursor + 1) if argument.lower() in {"exec", "exe", "x"} else (None, False)
    return None, False


def _inspect_executable_chain(argv: list[str], executable_index: int = 0, depth: int = 0) -> bool:
    if executable_index >= len(argv) or depth > 8:
        return False
    name = executable_name(argv[executable_index])
    if name in SHELL_EXECUTABLES:
        return True
    kind = interpreter_kind(name)
    if kind and kind in STRING_INTERPRETER_FLAGS:
        return _invokes_interpreter_string(argv, executable_index, kind, STRING_INTERPRETER_FLAGS[kind])
    if name == "env":
        command, rejected = _env_command(argv, executable_index + 1)
        return rejected or (command is not None and _inspect_executable_chain(argv, command, depth + 1))
    if name in {"busybox", "toybox"}:
        command = executable_index + 1
        if command < len(argv) and argv[command] == "--":
            command += 1
        return command < len(argv) and not argv[command].startswith("-") and _inspect_executable_chain(argv, command, depth + 1)
    if name == "wsl":
        command = _wrapper_command(
            argv,
            executable_index + 1,
            {"-d", "--distribution", "-u", "--user", "--cd", "--shell-type"},
        )
        return command is None or _inspect_executable_chain(argv, command, depth + 1)
    if name == "npx":
        command, rejected = _exec_command(argv, executable_index + 1)
        return rejected or (command is not None and _inspect_executable_chain(argv, command, depth + 1))
    if name == "npm":
        command, rejected = _npm_command(argv, executable_index + 1)
        return rejected or (command is not None and _inspect_executable_chain(argv, command, depth + 1))
    return False


def normalize_test_argv(value: object) -> list[str] | None:
    if not isinstance(value, list) or not value or not isinstance(value[0], str):
        return None
    if not value[0].strip() or re.search(r"\s", value[0]):
        return None
    if any(not isinstance(item, str) or not item or CONTROL.search(item) for item in value):
        return None
    normalized = list(value)
    return None if _inspect_executable_chain(normalized) else normalized


def validate_test_argv(value: object) -> bool:
    return normalize_test_argv(value) is not None
