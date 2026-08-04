from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping

from .jsonio import fingerprint, redact


AGENT_PROFILE_ENV = "LAYERED_DELIVERY_AGENT_PROFILES"
PROFILE_FILE = "agent-profiles.json"
PROFILE_CAPABILITIES = frozenset(
    {"development", "planning", "review"}
)
SAFE_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SAFE_COMMAND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f]{1,256}$")
TOML_STRING = re.compile(
    r"^\s*(model|model_reasoning_effort)\s*=\s*(.+?)\s*$"
)


@dataclass(frozen=True)
class AgentAdapter:
    id: str
    display_name: str
    commands: tuple[str, ...]
    capabilities: tuple[str, ...] = (
        "development",
        "planning",
        "review",
    )


AGENT_ADAPTERS = (
    AgentAdapter("codex", "Codex", ("codex",)),
    AgentAdapter("claude-code", "Claude Code", ("claude",)),
    AgentAdapter("cursor", "Cursor", ("cursor-agent", "agent")),
    AgentAdapter("opencode", "OpenCode", ("opencode",)),
    AgentAdapter("aider", "Aider", ("aider",)),
    AgentAdapter("gemini-cli", "Gemini CLI", ("gemini",)),
    AgentAdapter("grok-cli", "Grok CLI", ("grok",)),
    AgentAdapter("glm-cli", "GLM CLI", ("glm",)),
    AgentAdapter("deepseek-cli", "DeepSeek CLI", ("deepseek",)),
    AgentAdapter("qwen-cli", "Qwen CLI", ("qwen",)),
)


def _first_line(value: str) -> str | None:
    for line in value.splitlines():
        normalized = "".join(
            character
            for character in line.strip()
            if character == "\t" or ord(character) >= 32
        )
        if normalized:
            return normalized[:160]
    return None


def _probe_version(executable: str) -> str | None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = _first_line(completed.stdout) or _first_line(completed.stderr)
    return redact(line) if line is not None else None


def _safe_model_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if SAFE_TEXT.fullmatch(normalized) is None:
        return None
    return normalized if redact(normalized) == normalized else None


def _decode_toml_string(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith('"') and candidate.endswith('"'):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, str) else None
    if candidate.startswith("'") and candidate.endswith("'"):
        return candidate[1:-1]
    return None


def _codex_model(
    *,
    home: Path,
    environ: Mapping[str, str],
) -> dict[str, str | None]:
    configured_home = environ.get("CODEX_HOME")
    codex_home = (
        Path(configured_home).expanduser()
        if configured_home
        else home / ".codex"
    )
    path = codex_home / "config.toml"
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            break
        match = TOML_STRING.match(line)
        if match is None:
            continue
        decoded = _decode_toml_string(match.group(2))
        if decoded:
            values[match.group(1)] = decoded
    environment_model = _safe_model_value(environ.get("OPENAI_MODEL"))
    model_id = environment_model or _safe_model_value(values.get("model"))
    source = (
        "ENVIRONMENT"
        if environment_model
        else "CODEX_CONFIG"
        if model_id
        else "UNRESOLVED"
    )
    return {
        "id": model_id,
        "provider": _model_provider(model_id),
        "reasoningEffort": _safe_model_value(
            values.get("model_reasoning_effort")
        ),
        "source": source,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _claude_model(
    *,
    home: Path,
    environ: Mapping[str, str],
) -> dict[str, str | None]:
    model_id = _safe_model_value(environ.get("ANTHROPIC_MODEL"))
    source = "ENVIRONMENT" if model_id else "UNRESOLVED"
    if model_id is None:
        for name in ("settings.json", "settings.local.json"):
            settings = _read_json_object(home / ".claude" / name)
            configured = settings.get("model")
            settings_env = settings.get("env")
            if isinstance(settings_env, dict):
                configured = settings_env.get(
                    "ANTHROPIC_MODEL",
                    configured,
                )
            configured_model = _safe_model_value(configured)
            if configured_model:
                model_id = configured_model
                source = "CLAUDE_SETTINGS"
    return {
        "id": model_id,
        "provider": _model_provider(model_id),
        "reasoningEffort": None,
        "source": source,
    }


def _environment_model(
    adapter_id: str,
    environ: Mapping[str, str],
) -> dict[str, str | None]:
    keys = {
        "aider": ("AIDER_MODEL", "OPENAI_MODEL"),
        "cursor": ("CURSOR_MODEL",),
        "deepseek-cli": ("DEEPSEEK_MODEL",),
        "gemini-cli": ("GEMINI_MODEL",),
        "glm-cli": ("GLM_MODEL",),
        "grok-cli": ("GROK_MODEL",),
        "opencode": ("OPENCODE_MODEL",),
        "qwen-cli": ("QWEN_MODEL",),
    }.get(adapter_id, ())
    model_id = next(
        (
            _safe_model_value(environ[key])
            for key in keys
            if _safe_model_value(environ.get(key)) is not None
        ),
        None,
    )
    return {
        "id": model_id,
        "provider": _model_provider(model_id),
        "reasoningEffort": None,
        "source": "ENVIRONMENT" if model_id else "UNRESOLVED",
    }


def _model_provider(model_id: str | None) -> str | None:
    if model_id is None:
        return None
    normalized = model_id.casefold()
    providers = (
        (("deepseek",), "deepseek"),
        (("gemini",), "google"),
        (("glm", "zhipu"), "zhipu"),
        (("grok",), "xai"),
        (("claude",), "anthropic"),
        (("gpt", "codex", "o1", "o3", "o4"), "openai"),
        (("qwen",), "alibaba"),
    )
    return next(
        (
            provider
            for markers, provider in providers
            if any(marker in normalized for marker in markers)
        ),
        None,
    )


def _active_model(
    adapter_id: str,
    *,
    home: Path,
    environ: Mapping[str, str],
) -> dict[str, str | None]:
    if adapter_id == "codex":
        return _codex_model(home=home, environ=environ)
    if adapter_id == "claude-code":
        return _claude_model(home=home, environ=environ)
    return _environment_model(adapter_id, environ)


def _default_profile_path(
    *,
    home: Path,
    environ: Mapping[str, str],
) -> Path:
    if os.name == "nt" and environ.get("APPDATA"):
        return (
            Path(environ["APPDATA"])
            / "layered-delivery"
            / PROFILE_FILE
        )
    config_home = environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else home / ".config"
    return base / "layered-delivery" / PROFILE_FILE


def _profile_path(
    *,
    home: Path,
    environ: Mapping[str, str],
) -> tuple[Path, bool]:
    explicit = environ.get(AGENT_PROFILE_ENV)
    if explicit:
        return Path(explicit).expanduser(), True
    return _default_profile_path(home=home, environ=environ), False


def _valid_profile(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "id",
        "displayName",
        "command",
        "model",
        "reasoningEffort",
        "capabilities",
        "priority",
    }
    required = {"id", "displayName", "command", "capabilities"}
    if set(value) - allowed or not required <= set(value):
        return None
    profile_id = value["id"]
    display_name = value["displayName"]
    command = value["command"]
    capabilities = value["capabilities"]
    priority = value.get("priority", 0)
    model = value.get("model")
    reasoning_effort = value.get("reasoningEffort")
    if (
        not isinstance(profile_id, str)
        or SAFE_PROFILE_ID.fullmatch(profile_id) is None
        or not isinstance(display_name, str)
        or SAFE_TEXT.fullmatch(display_name) is None
        or not isinstance(command, str)
        or SAFE_COMMAND.fullmatch(command) is None
        or not isinstance(capabilities, list)
        or not capabilities
        or any(
            capability not in PROFILE_CAPABILITIES
            for capability in capabilities
        )
        or len(set(capabilities)) != len(capabilities)
        or isinstance(priority, bool)
        or not isinstance(priority, int)
        or not -100 <= priority <= 100
        or (
            model is not None
            and (
                not isinstance(model, str)
                or SAFE_TEXT.fullmatch(model) is None
            )
        )
        or (
            reasoning_effort is not None
            and (
                not isinstance(reasoning_effort, str)
                or SAFE_TEXT.fullmatch(reasoning_effort) is None
            )
        )
    ):
        return None
    return {
        "id": profile_id,
        "displayName": display_name.strip(),
        "command": command,
        "model": model.strip() if isinstance(model, str) else None,
        "reasoningEffort": (
            reasoning_effort.strip()
            if isinstance(reasoning_effort, str)
            else None
        ),
        "capabilities": sorted(capabilities),
        "priority": priority,
    }


def _load_profiles(
    *,
    home: Path,
    environ: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    path, explicit = _profile_path(home=home, environ=environ)
    if not path.is_file():
        warning = (
            [
                {
                    "code": "AGENT_PROFILE_FILE_MISSING",
                    "message": (
                        "The explicitly configured Agent Profile file "
                        "is unavailable."
                    ),
                }
            ]
            if explicit
            else []
        )
        return [], warning
    value = _read_json_object(path)
    raw_profiles = value.get("profiles")
    if not isinstance(raw_profiles, list):
        return [], [
            {
                "code": "AGENT_PROFILE_FILE_INVALID",
                "message": "Agent Profile file must contain profiles array.",
            }
        ]
    profiles: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_profile in enumerate(raw_profiles):
        profile = _valid_profile(raw_profile)
        if profile is None or profile["id"] in seen:
            warnings.append(
                {
                    "code": "AGENT_PROFILE_INVALID",
                    "message": (
                        "Ignored invalid or duplicate Agent Profile at "
                        f"index {index}."
                    ),
                }
            )
            continue
        seen.add(profile["id"])
        profiles.append(profile)
    return profiles, warnings


def _auto_agents(
    *,
    home: Path,
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
    version_reader: Callable[[str], str | None],
) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for adapter in AGENT_ADAPTERS:
        found = next(
            (
                (command, executable)
                for command in adapter.commands
                if (executable := which(command)) is not None
            ),
            None,
        )
        if found is None:
            continue
        command, executable = found
        agents.append(
            {
                "id": adapter.id,
                "displayName": adapter.display_name,
                "command": command,
                "version": version_reader(executable),
                "source": "AUTO",
                "capabilities": list(adapter.capabilities),
                "priority": 0,
                "model": _active_model(
                    adapter.id,
                    home=home,
                    environ=environ,
                ),
            }
        )
    return agents


def _profile_agents(
    profiles: list[dict[str, Any]],
    *,
    which: Callable[[str], str | None],
    version_reader: Callable[[str], str | None],
) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for profile in profiles:
        executable = which(profile["command"])
        if executable is None:
            continue
        model_id = _safe_model_value(profile["model"])
        agents.append(
            {
                "id": profile["id"],
                "displayName": profile["displayName"],
                "command": profile["command"],
                "version": version_reader(executable),
                "source": "USER_PROFILE",
                "capabilities": profile["capabilities"],
                "priority": profile["priority"],
                "model": {
                    "id": model_id,
                    "provider": _model_provider(model_id),
                    "reasoningEffort": profile["reasoningEffort"],
                    "source": (
                        "USER_PROFILE"
                        if model_id is not None
                        else "UNRESOLVED"
                    ),
                },
            }
        )
    return agents


def discover_available_agents(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    version_reader: Callable[[str], str | None] = _probe_version,
) -> dict[str, Any]:
    """Discover local terminal Agents without launching development work."""

    resolved_home = home or Path.home()
    resolved_environ = dict(os.environ if environ is None else environ)
    profiles, warnings = _load_profiles(
        home=resolved_home,
        environ=resolved_environ,
    )
    agents_by_id = {
        agent["id"]: agent
        for agent in _auto_agents(
            home=resolved_home,
            environ=resolved_environ,
            which=which,
            version_reader=version_reader,
        )
    }
    agents_by_id.update(
        {
            agent["id"]: agent
            for agent in _profile_agents(
                profiles,
                which=which,
                version_reader=version_reader,
            )
        }
    )
    agents = sorted(
        agents_by_id.values(),
        key=lambda agent: (-agent["priority"], agent["id"]),
    )
    snapshot = {
        "agents": agents,
        "warnings": warnings,
    }
    return {
        **snapshot,
        "summary": {
            "availableAgents": len(agents),
            "knownModels": sum(
                agent["model"]["id"] is not None
                for agent in agents
            ),
            "userProfiles": sum(
                agent["source"] == "USER_PROFILE"
                for agent in agents
            ),
        },
        "discoveryFingerprint": fingerprint(snapshot),
        "rules": {
            "localOnly": True,
            "secretsReturned": False,
            "developmentCommandsStarted": False,
            "profilesPersisted": False,
        },
    }


def available_agents(
    *,
    root: str,
    explicit_dogfood: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Expose local discovery as a diagnostic controller operation."""

    del root, explicit_dogfood
    return discover_available_agents()


__all__ = (
    "AGENT_PROFILE_ENV",
    "available_agents",
    "discover_available_agents",
)
