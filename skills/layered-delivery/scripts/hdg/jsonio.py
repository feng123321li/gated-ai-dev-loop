from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


MAX_JSON_DEPTH = 128
MAX_JSON_STRUCTURAL_TOKENS = 100_000
_SENSITIVE_KEY_COMPONENTS = frozenset(
    {
        "token",
        "key",
        "secret",
        "password",
        "authorization",
        "cookie",
        "credential",
        "credentials",
    }
)
_SENSITIVE_EXACT_KEYS = frozenset({"stdout", "stderr", "env"})
_CAMEL_ACRONYM_BOUNDARY_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_WORD_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_KEY_COMPONENT_RE = re.compile(r"[A-Za-z0-9]+")
_AUTHORIZATION_RE = re.compile(
    r"(?P<label>\b(?:proxy[-_ ]?authorization|authorization)\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r'(?P<value>"[^"\r\n]*"|\'[^\'\r\n]*\'|'
    r"(?:(?:bearer|basic)\s+)?[^\s,;]+)",
    re.IGNORECASE,
)
_COOKIE_RE = re.compile(
    r"(?P<label>\b(?:set[-_ ]?cookie|cookie)\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r'(?P<value>"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\r\n,]+)',
    re.IGNORECASE,
)
_ENV_SECRET_RE = re.compile(
    r"(?P<label>(?<![A-Za-z0-9_])"
    r"(?:[A-Za-z][A-Za-z0-9]*_)+"
    r"(?:access_key|api_key|auth_token|token|secret|password|"
    r"private_key|client_secret|credentials?)"
    r"(?![A-Za-z0-9_]))"
    r"(?P<separator>\s*[:=]\s*)"
    r'(?P<value>"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;]+)',
    re.IGNORECASE,
)
_LABELED_SECRET_RE = re.compile(
    r"(?P<label>\b(?:"
    r"(?:access|refresh|identity|id|api|auth|session)[-_ ]?token"
    r"|token"
    r"|api[-_ ]?key"
    r"|client[-_ ]?secret"
    r"|private[-_ ]?key"
    r"|key"
    r"|secret"
    r"|password"
    r"|credentials?"
    r")\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r'(?P<value>"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;]+)',
    re.IGNORECASE,
)
_BEARER_RE = re.compile(
    r"(?P<label>\bbearer)(?P<separator>\s+)"
    r"(?P<value>[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r"(?![A-Za-z0-9_-])"
)
_KNOWN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9_-]{16,}"
    r"|glpat-[A-Za-z0-9_-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r")"
    r"(?![A-Za-z0-9_-])"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:])[A-Za-z]:[\\/][^\s<>\"'|]+"
)
_UNC_PATH_RE = re.compile(r"\\\\[^\\/\s<>\"'|]+[\\/][^\s<>\"'|]+")
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w:/])/(?:[^\s<>\"'|:/]+/)+[^\s<>\"'|:,;]+"
)


def canonical_json(value: Any) -> str:
    """Return the byte-compatible canonical form used for persisted fingerprints."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def json_structure_within_limits(
    value: str,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_structural_tokens: int = MAX_JSON_STRUCTURAL_TOKENS,
) -> bool:
    """Bound JSON nesting and collection complexity before decoding."""

    depth = 0
    structural_tokens = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            continue
        if character in "[{":
            depth += 1
            structural_tokens += 1
            if depth > max_depth:
                return False
        elif character in "]}":
            depth -= 1
            structural_tokens += 1
        elif character in ",:":
            structural_tokens += 1
        if structural_tokens > max_structural_tokens:
            return False
    return True


def reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")


def _strict_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite JSON number: {value}")
    return parsed


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, child in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object member: {key!r}")
        result[key] = child
    return result


def _validate_unicode_scalars(value: Any) -> None:
    pending = [value]
    while pending:
        candidate = pending.pop()
        if isinstance(candidate, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in candidate):
                raise ValueError("JSON strings must contain Unicode scalar values")
        elif isinstance(candidate, dict):
            pending.extend(candidate.keys())
            pending.extend(candidate.values())
        elif isinstance(candidate, (list, tuple)):
            pending.extend(candidate)


def strict_json_loads(value: str) -> Any:
    parsed = json.loads(
        value,
        parse_constant=reject_nonstandard_json_constant,
        parse_float=_strict_json_float,
        object_pairs_hook=_strict_json_object,
    )
    _validate_unicode_scalars(parsed)
    return parsed


def pretty_json(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            separators=(",", ": "),
            allow_nan=False,
        )
        + "\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def canonical_relative_path(value: str) -> str:
    portable = str(value).replace("\\", "/")
    normalized = str(PurePosixPath(portable))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        from .errors import GatedLoopError

        raise GatedLoopError("PATH_OUTSIDE_ROOT", f"Path escapes root: {value}")
    return normalized


def _sensitive(key: str) -> bool:
    if key.casefold() in _SENSITIVE_EXACT_KEYS:
        return True
    separated = _CAMEL_ACRONYM_BOUNDARY_RE.sub(r"\1 \2", key)
    separated = _CAMEL_WORD_BOUNDARY_RE.sub(r"\1 \2", separated)
    components = (
        component.casefold()
        for component in _KEY_COMPONENT_RE.findall(separated)
    )
    return any(
        component in _SENSITIVE_KEY_COMPONENTS
        for component in components
    )


def _redact_labeled_value(match: re.Match[str]) -> str:
    value = match.group("value")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        replacement = f"{value[0]}[REDACTED]{value[0]}"
    else:
        replacement = "[REDACTED]"
    return f"{match.group('label')}{match.group('separator')}{replacement}"


def _redact_text(value: str) -> str:
    stripped = value.strip()
    if (
        stripped
        and "://" not in stripped
        and (
            PureWindowsPath(stripped).is_absolute()
            or PurePosixPath(stripped).is_absolute()
        )
    ):
        return "[REDACTED_PATH]"
    safe = _AUTHORIZATION_RE.sub(_redact_labeled_value, value)
    safe = _COOKIE_RE.sub(_redact_labeled_value, safe)
    safe = _ENV_SECRET_RE.sub(_redact_labeled_value, safe)
    safe = _LABELED_SECRET_RE.sub(_redact_labeled_value, safe)
    safe = _BEARER_RE.sub(
        lambda match: (
            f"{match.group('label')}{match.group('separator')}[REDACTED]"
        ),
        safe,
    )
    safe = _JWT_RE.sub("[REDACTED]", safe)
    safe = _KNOWN_TOKEN_RE.sub("[REDACTED]", safe)
    safe = _UNC_PATH_RE.sub("[REDACTED_PATH]", safe)
    safe = _WINDOWS_ABSOLUTE_PATH_RE.sub("[REDACTED_PATH]", safe)
    return _POSIX_ABSOLUTE_PATH_RE.sub("[REDACTED_PATH]", safe)


def redact(value: Any) -> Any:
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _sensitive(str(key)) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, str):
        return _redact_text(value)
    return value


def rendered_json(value: Any) -> str:
    return (
        json.dumps(
            redact(value),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
