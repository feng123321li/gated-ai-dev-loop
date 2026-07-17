from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .constants import SCHEMA_VERSION
from .errors import GatedLoopError, fail
from .evidence import (
    FINGERPRINT,
    safe_work_item_id,
    valid_acceptance,
    valid_acceptance_report,
    valid_development_mode,
    valid_evidence_reference,
    valid_review_artifact,
    valid_timestamp,
)
from .fs_safe import atomic_create_directory, atomic_replace_directory, atomic_write, read_regular_file, runtime_lock, safe_path
from .host_runtime import is_agent_runtime
from .jsonio import canonical_json, pretty_json, sha256_bytes
from .model import (
    WORK_ITEM_AUTHORITIES,
    WORK_ITEM_GATE_LEVELS,
    WORK_ITEM_KINDS,
    WORK_ITEM_SCHEMA_VERSION,
    render_development_plan,
    render_work_item_baseline,
    resolve_self_hosting_policy,
    work_item_baseline_fingerprint,
    work_item_child_contract_fingerprint,
    work_item_contract_fingerprint,
)
from .projections import (
    render_acceptance_report,
    render_development_review,
    render_item_overview,
    render_item_progress,
    render_workspace_overview,
    report_status,
)


WORK_ITEM_REGISTRY_FILE = "work-item-registry.json"
WORK_ITEMS_DIRECTORY = "work-items"
GOVERNANCE_DIRECTORY = ".hierarchical-delivery-governance"
WORK_ITEM_REGISTRY_SCHEMA_VERSION = SCHEMA_VERSION
ENTRY_FIELDS = {
    "id", "kind", "gateLevel", "authorityKind", "parentId", "childIds", "packagePath",
    "developmentPlan", "stage", "status", "baselineFingerprint", "contractFingerprint",
    "parentContractFingerprint", "gate", "acceptance", "acceptanceReport", "developmentMode",
    "claim", "latestEvidence", "latestResult", "recordRevision", "createdAt", "updatedAt", "progress",
}
STATE_FIELDS = {
    "schemaVersion", "id", "stage", "baselineFingerprint", "contractFingerprint",
    "parentContractFingerprint", "hostRuntime", "createdAt", "frozenAt", "baselineRevision", "revisedAt", "review",
}


def _plain_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _valid_progress(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"directChildren", "descendants"}:
        return False
    for counts in value.values():
        if not isinstance(counts, dict) or set(counts) != {"total", "verified", "blocked", "active"}:
            return False
        if not all(_plain_int(count) for count in counts.values()):
            return False
        if counts["verified"] + counts["blocked"] + counts["active"] > counts["total"]:
            return False
    return True


def _valid_gate(value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") not in {"NOT_RUN", "PASS", "FAIL"}:
        return False
    if value["status"] == "NOT_RUN":
        return set(value) == {"status", "evidence"} and value["evidence"] is None
    return (
        set(value) == {"status", "evidence", "artifact"}
        and valid_evidence_reference(value["evidence"])
        and (value["artifact"] is None or isinstance(value["artifact"], dict))
    )


def _valid_claim(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"owner", "operationId", "claimedAt"}
        and isinstance(value.get("owner"), str)
        and bool(value["owner"])
        and isinstance(value.get("operationId"), str)
        and bool(value["operationId"])
        and valid_timestamp(value.get("claimedAt"))
    )


def _valid_latest_result(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"evidence", "artifact", "recordedAt"}
        and valid_evidence_reference(value.get("evidence"))
        and (value.get("artifact") is None or isinstance(value.get("artifact"), dict))
        and valid_timestamp(value.get("recordedAt"))
    )


def timestamp(now: object = None) -> str:
    value = now() if callable(now) else now
    if value is None:
        date = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        date = value
    elif isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            fail("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid")
    else:
        fail("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid")
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class GovernanceRepository:
    """Own safe persistence, package integrity and registry projections."""

    def __init__(self, root: str | os.PathLike[str], *, now: object = None) -> None:
        self.root = Path(root).absolute()
        self.now = now

    @property
    def governance_root(self) -> Path:
        return self.root / GOVERNANCE_DIRECTORY

    @property
    def registry_path(self) -> Path:
        return self.governance_root / WORK_ITEM_REGISTRY_FILE

    def item_path(self, entry: dict[str, Any]) -> Path:
        return self.governance_root / entry["packagePath"]

    def assert_self_hosting_dogfood(self, explicit_dogfood: bool) -> None:
        project_name = None
        pyproject = self.root / "pyproject.toml"
        try:
            text = read_regular_file(self.root, pyproject).decode("utf-8")
            project_match = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
            name_match = re.search(r'(?m)^name\s*=\s*["\']([^"\']+)["\']\s*$', project_match.group(1) if project_match else "")
            if name_match:
                project_name = name_match.group(1)
        except (FileNotFoundError, UnicodeDecodeError):
            pass
        policy = resolve_self_hosting_policy(project_name=project_name, explicit_dogfood=explicit_dogfood)
        if not policy["createsRuntimePackage"]:
            fail(
                "SELF_HOSTING_DOGFOOD_REQUIRED",
                "The hierarchical governance implementation repository requires explicit dogfood for runtime mutations",
            )

    def ensure_runtime_root(self) -> None:
        try:
            root_stat = self.root.lstat()
        except FileNotFoundError:
            fail("WORK_ITEM_ROOT_INVALID", "Coordination root must already exist")
        if not self.root.is_dir() or self.root.is_symlink():
            fail("WORK_ITEM_ROOT_INVALID", "Coordination root must be a regular directory")
        runtime = safe_path(self.root, GOVERNANCE_DIRECTORY)
        runtime.mkdir(parents=True, exist_ok=True)
        safe_path(self.root, GOVERNANCE_DIRECTORY)
        items = safe_path(self.root, f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}")
        items.mkdir(parents=True, exist_ok=True)
        safe_path(self.root, f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}")

    def empty_registry(self) -> dict[str, Any]:
        return {
            "schemaVersion": WORK_ITEM_REGISTRY_SCHEMA_VERSION,
            "coordinationRoot": str(self.root),
            "revision": 0,
            "currentFocus": {"workItemId": None, "purpose": None},
            "workItems": [],
            "updatedAt": timestamp(self.now),
        }

    def item_by_id(self, registry: dict[str, Any], item_id: str) -> dict[str, Any]:
        for item in registry["workItems"]:
            if item["id"] == item_id:
                return item
        fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {item_id}", id=item_id)

    def validate_registry(self, registry: object) -> dict[str, Any]:
        valid = (
            isinstance(registry, dict)
            and registry.get("schemaVersion") == WORK_ITEM_REGISTRY_SCHEMA_VERSION
            and registry.get("coordinationRoot") == str(self.root)
            and isinstance(registry.get("revision"), int)
            and not isinstance(registry.get("revision"), bool)
            and registry["revision"] >= 0
            and isinstance(registry.get("workItems"), list)
            and isinstance(registry.get("currentFocus"), dict)
            and set(registry["currentFocus"]) == {"workItemId", "purpose"}
            and valid_timestamp(registry.get("updatedAt"))
            and set(registry) == {
                "schemaVersion", "coordinationRoot", "revision", "currentFocus", "workItems",
                "updatedAt",
            }
        )
        if not valid:
            fail("WORK_ITEM_REGISTRY_INVALID", "Work item registry is invalid")
        ids = [item.get("id") for item in registry["workItems"] if isinstance(item, dict)]
        if len(ids) != len(registry["workItems"]) or len(set(ids)) != len(ids) or any(not safe_work_item_id(item) for item in ids):
            fail("WORK_ITEM_REGISTRY_INVALID", "Work item registry contains duplicate or unsafe IDs")
        by_id = {item["id"]: item for item in registry["workItems"]}
        for entry in registry["workItems"]:
            valid_entry = (
                set(entry) == ENTRY_FIELDS
                and entry.get("kind") in WORK_ITEM_KINDS
                and entry.get("authorityKind") == WORK_ITEM_AUTHORITIES.get(entry.get("kind"))
                and entry.get("gateLevel") in WORK_ITEM_GATE_LEVELS
                and (entry.get("kind") == "TASK" or entry.get("gateLevel") == "FULL")
                and (entry.get("parentId") is None or safe_work_item_id(entry.get("parentId")))
                and isinstance(entry.get("childIds"), list)
                and all(safe_work_item_id(item) for item in entry["childIds"])
                and isinstance(entry.get("packagePath"), str)
                and entry["packagePath"].replace("\\", "/") == entry["packagePath"]
                and entry["packagePath"].startswith(f"{WORK_ITEMS_DIRECTORY}/")
                and ".." not in entry["packagePath"].split("/")
                and entry.get("developmentPlan") is True
                and bool(FINGERPRINT.fullmatch(str(entry.get("baselineFingerprint", ""))))
                and bool(FINGERPRINT.fullmatch(str(entry.get("contractFingerprint", ""))))
                and (
                    entry.get("parentContractFingerprint") is None
                    if entry.get("parentId") is None
                    else bool(FINGERPRINT.fullmatch(str(entry.get("parentContractFingerprint", ""))))
                )
                and entry.get("stage") in {"WAITING_FOR_BASELINE_CONFIRMATION", "BASELINE_FROZEN"}
                and entry.get("status") in {
                    "PREPARED", "WAITING_FOR_DEVELOPMENT_MODE_SELECTION", "FROZEN",
                    "CLAIMED", "IMPLEMENTED", "BLOCKED", "VERIFIED",
                }
                and _valid_gate(entry.get("gate"))
                and _plain_int(entry.get("recordRevision"), minimum=1)
                and valid_timestamp(entry.get("createdAt"))
                and valid_timestamp(entry.get("updatedAt"))
                and _valid_progress(entry.get("progress"))
                and (
                    entry.get("latestEvidence") is None
                    or valid_evidence_reference(entry.get("latestEvidence"))
                )
                and (
                    entry.get("latestResult") is None
                    or _valid_latest_result(entry.get("latestResult"))
                )
            )
            if not valid_entry:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item registry entry is invalid: {entry['id']}")
            mode = entry.get("developmentMode")
            if entry["kind"] == "TASK":
                if mode is not None and not valid_development_mode(mode, entry):
                    fail("WORK_ITEM_REGISTRY_INVALID", f"Work item development mode is invalid: {entry['id']}")
                waiting = entry.get("status") == "WAITING_FOR_DEVELOPMENT_MODE_SELECTION"
                if waiting != (mode is None and entry.get("stage") == "BASELINE_FROZEN"):
                    fail("WORK_ITEM_REGISTRY_INVALID", f"Task development mode state is inconsistent: {entry['id']}")
            elif mode is not None:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item development mode is invalid: {entry['id']}")
            if entry["stage"] == "WAITING_FOR_BASELINE_CONFIRMATION":
                if entry["status"] != "PREPARED" or mode is not None:
                    fail("WORK_ITEM_REGISTRY_INVALID", f"Work item prepared state is inconsistent: {entry['id']}")
            elif entry["status"] == "PREPARED":
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item frozen state is inconsistent: {entry['id']}")
            if entry["kind"] != "TASK" and entry["status"] in {
                "WAITING_FOR_DEVELOPMENT_MODE_SELECTION", "CLAIMED", "IMPLEMENTED",
            }:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Coordination work item status is invalid: {entry['id']}")
            claim = entry.get("claim")
            if (entry["status"] == "CLAIMED") != _valid_claim(claim):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item claim is inconsistent: {entry['id']}")
            gate_status = entry["gate"]["status"]
            if (entry["status"] == "VERIFIED") != (gate_status == "PASS"):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item PASS state is inconsistent: {entry['id']}")
            if gate_status == "FAIL" and entry["status"] != "BLOCKED":
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item FAIL state is inconsistent: {entry['id']}")
            if entry["parentId"] is None:
                if not valid_acceptance(entry.get("acceptance")):
                    fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance state is invalid: {entry['id']}")
            elif entry.get("acceptance") is not None:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance state is invalid: {entry['id']}")
            if not valid_acceptance_report(entry.get("acceptanceReport"), entry):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance report is invalid: {entry['id']}")
            if entry["kind"] == "DELIVERY" and entry["parentId"] is not None:
                fail("WORK_ITEM_REGISTRY_INVALID", "Delivery entries cannot have parents")
            expected_package = (
                f"{WORK_ITEMS_DIRECTORY}/{entry['id']}"
                if entry["parentId"] is None
                else f"{by_id[entry['parentId']]['packagePath']}/children/{entry['id']}"
                if entry["parentId"] in by_id
                else None
            )
            if entry["packagePath"] != expected_package:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item package path is invalid: {entry['id']}")
            if any(child_id not in by_id for child_id in entry["childIds"]):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item hierarchy is not fully materialized: {entry['id']}")
            if entry["kind"] != "DELIVERY" and entry["parentId"] is not None:
                parent = by_id.get(entry["parentId"])
                expected_kind = "DELIVERY" if entry["kind"] == "CAPABILITY" else "CAPABILITY"
                if not parent or parent["kind"] != expected_kind or entry["id"] not in parent["childIds"]:
                    fail("WORK_ITEM_REGISTRY_INVALID", f"Work item parent relation is invalid: {entry['id']}")
        focus_id = registry["currentFocus"].get("workItemId")
        if focus_id is not None and (not safe_work_item_id(focus_id) or focus_id not in by_id):
            fail("WORK_ITEM_REGISTRY_INVALID", "Current focus references an unknown work item")
        focus_purpose = registry["currentFocus"].get("purpose")
        if (focus_id is None) != (focus_purpose is None) or (
            focus_purpose is not None and (not isinstance(focus_purpose, str) or not focus_purpose)
        ):
            fail("WORK_ITEM_REGISTRY_INVALID", "Current focus is invalid")
        return registry

    def _assert_persisted_acceptance_evidence(self, registry: dict[str, Any]) -> None:
        for entry in registry["workItems"]:
            acceptance = entry.get("acceptance") if entry["parentId"] is None else None
            if not acceptance or acceptance["status"] in {"NOT_READY", "WAITING_FOR_INDEPENDENT_REVIEW"}:
                continue
            records = [acceptance["review"]]
            if acceptance["status"] == "COMPLETED":
                records.append(acceptance["userConfirmation"])
            for record in records:
                try:
                    data = read_regular_file(self.root, record["evidence"]["path"])
                except Exception:
                    fail("WORK_ITEM_ACCEPTANCE_EVIDENCE_MISSING", f"Persisted acceptance evidence is unavailable: {record['evidence']['path']}")
                if sha256_bytes(data) != record["evidence"]["sha256"]:
                    fail("WORK_ITEM_ACCEPTANCE_EVIDENCE_CHANGED", f"Persisted acceptance evidence changed: {record['evidence']['path']}")
                try:
                    artifact = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    fail("WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID", f"Persisted acceptance evidence is invalid JSON: {record['evidence']['path']}")
                if not valid_review_artifact(record["action"], artifact) or canonical_json(artifact) != canonical_json(record["artifact"]):
                    fail("WORK_ITEM_ACCEPTANCE_EVIDENCE_CHANGED", f"Persisted acceptance evidence no longer matches its registry snapshot: {record['evidence']['path']}")

    def _assert_persisted_development_modes(self, registry: dict[str, Any]) -> None:
        for entry in registry["workItems"]:
            if entry["kind"] != "TASK" or entry.get("developmentMode") is None:
                continue
            try:
                artifact = self.read_json(self.item_path(entry), "development-mode.json", "WORK_ITEM_DEVELOPMENT_MODE_INVALID")
            except Exception:
                fail("WORK_ITEM_DEVELOPMENT_MODE_INVALID", f"{entry['id']} development-mode.json is missing or unreadable")
            if not valid_development_mode(artifact, entry) or canonical_json(artifact) != canonical_json(entry["developmentMode"]):
                fail("WORK_ITEM_DEVELOPMENT_MODE_CHANGED", f"{entry['id']} development-mode.json changed after confirmation")

    def read_registry(self, *, allow_missing: bool = False) -> dict[str, Any]:
        try:
            data = read_regular_file(self.root, self.registry_path)
        except FileNotFoundError:
            if allow_missing:
                return self.empty_registry()
            fail("WORK_ITEM_REGISTRY_MISSING", "Work item registry does not exist")
        try:
            registry = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            fail("WORK_ITEM_REGISTRY_INVALID", "Work item registry is not valid JSON")
        validated = self.validate_registry(registry)
        self._assert_persisted_acceptance_evidence(validated)
        self._assert_persisted_development_modes(validated)
        return validated

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        self.ensure_runtime_root()
        with runtime_lock(self.registry_path):
            yield self.read_registry(allow_missing=True)

    def read_json(self, root: Path, target: str | os.PathLike[str], code: str) -> dict[str, Any]:
        candidate = root / target
        try:
            value = json.loads(read_regular_file(root, candidate).decode("utf-8"))
        except GatedLoopError:
            raise
        except Exception:
            fail(code, f"Unable to read {Path(target).name}")
        if not isinstance(value, dict):
            fail(code, f"Unable to read {Path(target).name}")
        return value

    @staticmethod
    def package_files(
        definition: dict[str, Any],
        state: dict[str, Any],
        *,
        human_plan: str | None = None,
    ) -> dict[str, str]:
        files = {
            "baseline.json": pretty_json(definition),
            "baseline.md": render_work_item_baseline(definition),
            "work-item.json": pretty_json({
                "schemaVersion": WORK_ITEM_SCHEMA_VERSION,
                "id": definition["id"],
                "kind": definition["kind"],
                "gateLevel": definition["gateLevel"],
                "authorityKind": definition["authorityKind"],
                "parentId": definition["parentId"],
            }),
            "state.json": pretty_json(state),
            "development-plan.json": pretty_json({
                "schemaVersion": SCHEMA_VERSION,
                "workItemId": definition["id"],
                "kind": definition["kind"],
                "baselineFingerprint": state["baselineFingerprint"],
                "developmentPlan": definition["developmentPlan"],
            }),
            "development-plan.md": human_plan or render_development_plan(definition, state),
        }
        if "children" in definition:
            files["children.json"] = pretty_json({"schemaVersion": WORK_ITEM_SCHEMA_VERSION, "children": definition["children"]})
        if "execution" in definition:
            files["execution.json"] = pretty_json({"schemaVersion": WORK_ITEM_SCHEMA_VERSION, **definition["execution"]})
        return files

    def write_new_package(self, target: Path, files: dict[str, str]) -> None:
        def populate(staging: Path) -> None:
            for name, contents in files.items():
                atomic_write(staging / name, contents)

        atomic_create_directory(target, populate)

    def write_hierarchy_package(
        self,
        target: Path,
        packages: list[tuple[Path, dict[str, str]]],
        *,
        replace: bool = False,
    ) -> None:
        """Atomically write one complete requirement tree below its root directory."""
        def populate(staging: Path) -> None:
            for relative, files in packages:
                directory = staging / relative
                directory.mkdir(parents=True, exist_ok=True)
                for name, contents in files.items():
                    atomic_write(directory / name, contents)

        if replace:
            atomic_replace_directory(target, populate)
        else:
            atomic_create_directory(target, populate)

    def replace_package(
        self,
        target: Path,
        files: dict[str, str],
        *,
        preserve_existing: bool = False,
        remove: tuple[str, ...] = (),
    ) -> None:
        def populate(staging: Path) -> None:
            if preserve_existing:
                for source in target.iterdir():
                    if source.is_symlink():
                        fail("WORK_ITEM_PACKAGE_INVALID", "Work item packages cannot contain symbolic links")
                    destination = staging / source.name
                    if source.is_dir():
                        shutil.copytree(source, destination)
                    else:
                        shutil.copy2(source, destination)
            for name, contents in files.items():
                atomic_write(staging / name, contents)
            for name in remove:
                candidate = staging / name
                if candidate.is_dir():
                    shutil.rmtree(candidate)
                else:
                    try:
                        candidate.unlink()
                    except FileNotFoundError:
                        pass

        atomic_replace_directory(target, populate)

    def read_package(self, registry: dict[str, Any], entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Path]:
        target = self.item_path(entry)
        definition = self.read_json(target, "baseline.json", "WORK_ITEM_PACKAGE_INVALID")
        state = self.read_json(target, "state.json", "WORK_ITEM_PACKAGE_INVALID")
        baseline_fingerprint = work_item_baseline_fingerprint(definition)
        review = state.get("review")
        review_valid = (
            isinstance(review, dict)
            and set(review) == {"schemaVersion", "status", "baselineFingerprint", "reviewedBy", "reviewedAt"}
            and review.get("schemaVersion") == SCHEMA_VERSION
            and review.get("baselineFingerprint") == baseline_fingerprint
            and (
                (
                    state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
                    and review.get("status") == "WAITING_FOR_HUMAN_REVIEW"
                    and review.get("reviewedBy") is None
                    and review.get("reviewedAt") is None
                )
                or (
                    state.get("stage") == "BASELINE_FROZEN"
                    and review.get("status") == "APPROVED"
                    and review.get("reviewedBy") == "user"
                    and valid_timestamp(review.get("reviewedAt"))
                )
            )
        )
        generated_valid = True
        generated_files = self.package_files(definition, state)
        if entry["parentId"] is None:
            generated_files.pop("development-plan.md")
        for name, expected in generated_files.items():
            if name == "state.json":
                continue
            try:
                generated_valid = generated_valid and read_regular_file(target, target / name) == expected.encode("utf-8")
            except Exception:
                generated_valid = False
        valid = (
            set(state) == STATE_FIELDS
            and state.get("schemaVersion") == WORK_ITEM_SCHEMA_VERSION
            and state.get("id") == entry["id"]
            and state.get("stage") == entry["stage"]
            and state.get("baselineFingerprint") == baseline_fingerprint
            and state.get("contractFingerprint") == work_item_contract_fingerprint(definition)
            and entry["baselineFingerprint"] == state["baselineFingerprint"]
            and entry["contractFingerprint"] == state["contractFingerprint"]
            and is_agent_runtime(state.get("hostRuntime"))
            and valid_timestamp(state.get("createdAt"))
            and _plain_int(state.get("baselineRevision"), minimum=1)
            and (state.get("revisedAt") is None or valid_timestamp(state.get("revisedAt")))
            and (
                state.get("frozenAt") is None
                if state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
                else valid_timestamp(state.get("frozenAt"))
            )
            and review_valid
            and generated_valid
        )
        if not valid:
            fail("WORK_ITEM_PACKAGE_CHANGED", f"{entry['id']} package changed after preparation", id=entry["id"])
        return definition, state, target

    def assert_current_lineage(
        self,
        registry: dict[str, Any],
        entry: dict[str, Any],
        seen: set[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        visited = seen or set()
        if entry["id"] in visited:
            fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
        visited.add(entry["id"])
        own = self.read_package(registry, entry)
        if not entry["parentId"]:
            return own
        parent_entry = self.item_by_id(registry, entry["parentId"])
        parent_definition, _, _ = self.read_package(registry, parent_entry)
        actual = work_item_child_contract_fingerprint(parent_definition, entry["id"])
        if entry["parentContractFingerprint"] != actual or own[0]["parentContractFingerprint"] != actual:
            fail(
                "WORK_ITEM_BASELINE_STALE",
                f"{entry['id']} parent contract changed",
                id=entry["id"],
                parentId=parent_entry["id"],
                expected=entry["parentContractFingerprint"],
                actual=actual,
            )
        self.assert_current_lineage(registry, parent_entry, visited)
        return own

    @staticmethod
    def _progress_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(entries),
            "verified": sum(item.get("status") == "VERIFIED" for item in entries),
            "blocked": sum(item.get("status") == "BLOCKED" for item in entries),
            "active": sum(item.get("status") in {"CLAIMED", "IMPLEMENTED"} for item in entries),
        }

    def recompute_progress(self, registry: dict[str, Any]) -> None:
        by_id = {item["id"]: item for item in registry["workItems"]}

        def descendants(entry: dict[str, Any], visited: set[str] | None = None) -> list[dict[str, Any]]:
            path = visited or set()
            if entry["id"] in path:
                fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
            next_path = path | {entry["id"]}
            result = []
            for child_id in entry["childIds"]:
                child = by_id.get(child_id, {"id": child_id, "status": "PLANNED", "childIds": []})
                result.append(child)
                if child_id in by_id:
                    result.extend(descendants(child, next_path))
            return result

        for entry in registry["workItems"]:
            direct = [by_id.get(item, {"id": item, "status": "PLANNED"}) for item in entry["childIds"]]
            entry["progress"] = {
                "directChildren": self._progress_counts(direct),
                "descendants": self._progress_counts(descendants(entry)),
            }

    def write_registry(self, registry: dict[str, Any]) -> None:
        self.recompute_progress(registry)
        self.validate_registry(registry)
        registry["workItems"] = sorted(registry["workItems"], key=lambda item: item["id"])
        by_id = {item["id"]: item for item in registry["workItems"]}
        atomic_write(self.registry_path, pretty_json(registry))
        atomic_write(self.governance_root / "workspace-overview.md", render_workspace_overview(registry))
        for entry in registry["workItems"]:
            target = self.item_path(entry)
            if not target.exists():
                continue
            if not target.is_dir() or target.is_symlink():
                fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} package path is invalid")
            atomic_write(target / "overview.md", render_item_overview(entry, by_id))
            atomic_write(target / "progress.md", render_item_progress(entry))

    def write_acceptance_report(
        self,
        entry: dict[str, Any],
        definition: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
        acceptance = entry.get("acceptance") if entry["parentId"] is None else None
        report = {
            "schemaVersion": SCHEMA_VERSION,
            "workItem": {
                "id": entry["id"],
                "title": definition["title"],
                "kind": entry["kind"],
                "gateLevel": entry["gateLevel"],
                "baselineFingerprint": entry["baselineFingerprint"],
                "parentId": entry["parentId"],
            },
            "status": report_status(entry),
            "development": entry.get("latestResult"),
            "gate": entry["gate"],
            "criteria": definition["acceptance"],
            "developmentPlan": definition["developmentPlan"],
            "review": acceptance.get("review") if acceptance else None,
            "userConfirmation": acceptance.get("userConfirmation") if acceptance else None,
            "generatedAt": at,
        }
        directory = self.item_path(entry)
        atomic_write(directory / "acceptance-report.json", pretty_json(report))
        atomic_write(directory / "acceptance-report.md", render_acceptance_report(report))
        base = f"{GOVERNANCE_DIRECTORY}/{entry['packagePath']}"
        entry["acceptanceReport"] = {
            "schemaVersion": SCHEMA_VERSION,
            "status": report["status"],
            "jsonPath": f"{base}/acceptance-report.json",
            "markdownPath": f"{base}/acceptance-report.md",
            "generatedAt": at,
        }
        return report

    def write_development_review(
        self,
        entry: dict[str, Any],
        definition: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
        report = {
            "schemaVersion": SCHEMA_VERSION,
            "workItem": {
                "id": entry["id"],
                "title": definition["title"],
                "kind": entry["kind"],
                "gateLevel": entry["gateLevel"],
                "baselineFingerprint": entry["baselineFingerprint"],
                "parentId": entry["parentId"],
            },
            "status": entry["status"],
            "developmentPlan": definition["developmentPlan"],
            "result": entry.get("latestResult"),
            "generatedAt": at,
        }
        directory = self.item_path(entry)
        atomic_write(directory / "development-review.json", pretty_json(report))
        atomic_write(directory / "development-review.md", render_development_review(report))
        return report


def entry_from_definition(
    definition: dict[str, Any],
    state: dict[str, Any],
    at: str,
    *,
    package_path: str | None = None,
) -> dict[str, Any]:
    return {
        "id": definition["id"],
        "kind": definition["kind"],
        "gateLevel": definition["gateLevel"],
        "authorityKind": definition["authorityKind"],
        "parentId": definition["parentId"],
        "childIds": [item["id"] for item in definition.get("children", [])],
        "packagePath": package_path or f"{WORK_ITEMS_DIRECTORY}/{definition['id']}",
        "developmentPlan": True,
        "stage": state["stage"],
        "status": "PREPARED",
        "baselineFingerprint": state["baselineFingerprint"],
        "contractFingerprint": state["contractFingerprint"],
        "parentContractFingerprint": state["parentContractFingerprint"],
        "gate": {"status": "NOT_RUN", "evidence": None},
        "acceptance": {"status": "NOT_READY", "review": None, "userConfirmation": None}
        if definition["parentId"] is None
        else None,
        "acceptanceReport": None,
        "developmentMode": None,
        "claim": None,
        "latestEvidence": None,
        "latestResult": None,
        "recordRevision": 1,
        "createdAt": at,
        "updatedAt": at,
    }
