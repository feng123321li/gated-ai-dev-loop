from __future__ import annotations

from typing import Any

from .constants import (
    MAX_DATABASE_CHANGES_PER_TASK,
    MAX_DATABASE_COLUMNS_PER_TABLE,
    MAX_DATABASE_CONSTRAINTS_PER_TABLE,
    MAX_DATABASE_FOREIGN_KEYS_PER_TABLE,
    MAX_DATABASE_INDEXES_PER_TABLE,
    MAX_DATABASE_VERIFICATION_STEPS,
)
from .errors import fail


DATABASE_CHANGE_TYPES = ("CREATE", "MODIFY", "DELETE")
_CHANGE_FIELDS = {
    "projectId",
    "database",
    "schema",
    "table",
    "summary",
    "changeType",
    "before",
    "after",
    "migration",
    "resourceClaim",
}
_REQUIRED_CHANGE_FIELDS = _CHANGE_FIELDS - {
    "projectId",
    "database",
    "schema",
}
_SNAPSHOT_FIELDS = {
    "comment",
    "columns",
    "primaryKey",
    "uniqueConstraints",
    "indexes",
    "foreignKeys",
}
_COLUMN_FIELDS = {
    "name",
    "type",
    "nullable",
    "default",
    "comment",
    "autoIncrement",
    "generated",
}
_CONSTRAINT_FIELDS = {"name", "columns"}
_INDEX_FIELDS = {
    "name",
    "columns",
    "unique",
    "method",
    "predicate",
}
_FOREIGN_KEY_FIELDS = {
    "name",
    "columns",
    "referencedTable",
    "referencedColumns",
    "onDelete",
    "onUpdate",
}
_MIGRATION_FIELDS = {
    "forward",
    "rollback",
    "backfill",
    "compatibility",
    "verification",
}


def _invalid(message: str, *, field: str) -> None:
    fail("DATABASE_CHANGE_CONTRACT_INVALID", message, field=field)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid("Database contract text fields must be non-empty", field=field)
    return value.strip()


def _exact_object(
    value: object,
    *,
    field: str,
    required: set[str],
    allowed: set[str],
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(allowed)
    ):
        _invalid(
            "Database contract object fields are incomplete or unsupported",
            field=field,
        )
    return value


def _named_columns(
    value: object,
    *,
    field: str,
    enforce_resource_limits: bool = True,
) -> list[str]:
    if not isinstance(value, list) or not value:
        _invalid("Column references must be a non-empty array", field=field)
    if (
        enforce_resource_limits
        and len(value) > MAX_DATABASE_COLUMNS_PER_TABLE
    ):
        _invalid(
            "Column references exceed the per-table column limit",
            field=field,
        )
    result = [_text(item, field=f"{field}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        _invalid("Column references must be unique", field=field)
    return result


def _validate_named_constraints(
    value: object,
    *,
    field: str,
    enforce_resource_limits: bool = True,
) -> None:
    if not isinstance(value, list):
        _invalid("Constraints must be an array", field=field)
    if (
        enforce_resource_limits
        and len(value) > MAX_DATABASE_CONSTRAINTS_PER_TABLE
    ):
        _invalid(
            "Unique constraints exceed the per-table constraint limit",
            field=field,
        )
    names: list[str] = []
    for index, raw in enumerate(value):
        item_field = f"{field}[{index}]"
        item = _exact_object(
            raw,
            field=item_field,
            required=_CONSTRAINT_FIELDS,
            allowed=_CONSTRAINT_FIELDS,
        )
        names.append(_text(item["name"], field=f"{item_field}.name"))
        _named_columns(
            item["columns"],
            field=f"{item_field}.columns",
            enforce_resource_limits=enforce_resource_limits,
        )
    if len(set(names)) != len(names):
        _invalid("Constraint names must be unique", field=field)


def _validate_indexes(
    value: object,
    *,
    field: str,
    enforce_resource_limits: bool = True,
) -> None:
    if not isinstance(value, list):
        _invalid("Indexes must be an array", field=field)
    if (
        enforce_resource_limits
        and len(value) > MAX_DATABASE_INDEXES_PER_TABLE
    ):
        _invalid("Indexes exceed the per-table index limit", field=field)
    names: list[str] = []
    for index, raw in enumerate(value):
        item_field = f"{field}[{index}]"
        item = _exact_object(
            raw,
            field=item_field,
            required={"name", "columns", "unique"},
            allowed=_INDEX_FIELDS,
        )
        names.append(_text(item["name"], field=f"{item_field}.name"))
        _named_columns(
            item["columns"],
            field=f"{item_field}.columns",
            enforce_resource_limits=enforce_resource_limits,
        )
        if not isinstance(item["unique"], bool):
            _invalid("Index unique must be boolean", field=f"{item_field}.unique")
        for optional in ("method", "predicate"):
            if optional in item:
                _text(item[optional], field=f"{item_field}.{optional}")
    if len(set(names)) != len(names):
        _invalid("Index names must be unique", field=field)


def _validate_foreign_keys(
    value: object,
    *,
    field: str,
    enforce_resource_limits: bool = True,
) -> None:
    if not isinstance(value, list):
        _invalid("Foreign keys must be an array", field=field)
    if (
        enforce_resource_limits
        and len(value) > MAX_DATABASE_FOREIGN_KEYS_PER_TABLE
    ):
        _invalid("Foreign keys exceed the per-table foreign-key limit", field=field)
    names: list[str] = []
    for index, raw in enumerate(value):
        item_field = f"{field}[{index}]"
        item = _exact_object(
            raw,
            field=item_field,
            required=_FOREIGN_KEY_FIELDS,
            allowed=_FOREIGN_KEY_FIELDS,
        )
        names.append(_text(item["name"], field=f"{item_field}.name"))
        columns = _named_columns(
            item["columns"],
            field=f"{item_field}.columns",
            enforce_resource_limits=enforce_resource_limits,
        )
        referenced = _named_columns(
            item["referencedColumns"],
            field=f"{item_field}.referencedColumns",
            enforce_resource_limits=enforce_resource_limits,
        )
        if len(columns) != len(referenced):
            _invalid(
                "Foreign-key source and referenced columns must have equal length",
                field=item_field,
            )
        for key in ("referencedTable", "onDelete", "onUpdate"):
            _text(item[key], field=f"{item_field}.{key}")
    if len(set(names)) != len(names):
        _invalid("Foreign-key names must be unique", field=field)


def _validate_snapshot(
    value: object,
    *,
    field: str,
    enforce_resource_limits: bool = True,
) -> None:
    item = _exact_object(
        value,
        field=field,
        required=_SNAPSHOT_FIELDS,
        allowed=_SNAPSHOT_FIELDS,
    )
    if item["comment"] is not None:
        _text(item["comment"], field=f"{field}.comment")
    columns = item["columns"]
    if not isinstance(columns, list) or not columns:
        _invalid("A table snapshot must contain all columns", field=f"{field}.columns")
    if (
        enforce_resource_limits
        and len(columns) > MAX_DATABASE_COLUMNS_PER_TABLE
    ):
        _invalid(
            "Table snapshot columns exceed the per-table column limit",
            field=f"{field}.columns",
        )
    column_names: list[str] = []
    for index, raw in enumerate(columns):
        column_field = f"{field}.columns[{index}]"
        column = _exact_object(
            raw,
            field=column_field,
            required={"name", "type", "nullable", "default", "comment"},
            allowed=_COLUMN_FIELDS,
        )
        column_names.append(_text(column["name"], field=f"{column_field}.name"))
        _text(column["type"], field=f"{column_field}.type")
        if not isinstance(column["nullable"], bool):
            _invalid("Column nullable must be boolean", field=f"{column_field}.nullable")
        if column["default"] is not None and not isinstance(
            column["default"],
            (str, int, float, bool),
        ):
            _invalid(
                "Column default must be a scalar JSON value or null",
                field=f"{column_field}.default",
            )
        if column["comment"] is not None:
            _text(column["comment"], field=f"{column_field}.comment")
        for flag in ("autoIncrement", "generated"):
            if flag in column and not isinstance(column[flag], bool):
                _invalid(f"Column {flag} must be boolean", field=f"{column_field}.{flag}")
    if len(set(column_names)) != len(column_names):
        _invalid("Table column names must be unique", field=f"{field}.columns")
    column_name_set = set(column_names)
    primary_key = item["primaryKey"]
    if primary_key is not None:
        primary = _exact_object(
            primary_key,
            field=f"{field}.primaryKey",
            required=_CONSTRAINT_FIELDS,
            allowed=_CONSTRAINT_FIELDS,
        )
        _text(primary["name"], field=f"{field}.primaryKey.name")
        primary_columns = _named_columns(
            primary["columns"],
            field=f"{field}.primaryKey.columns",
            enforce_resource_limits=enforce_resource_limits,
        )
        if not set(primary_columns).issubset(column_name_set):
            _invalid("Primary key references unknown columns", field=f"{field}.primaryKey")
    _validate_named_constraints(
        item["uniqueConstraints"],
        field=f"{field}.uniqueConstraints",
        enforce_resource_limits=enforce_resource_limits,
    )
    _validate_indexes(
        item["indexes"],
        field=f"{field}.indexes",
        enforce_resource_limits=enforce_resource_limits,
    )
    _validate_foreign_keys(
        item["foreignKeys"],
        field=f"{field}.foreignKeys",
        enforce_resource_limits=enforce_resource_limits,
    )


def _validate_migration(
    value: object,
    *,
    field: str,
    enforce_resource_limits: bool = True,
) -> None:
    item = _exact_object(
        value,
        field=field,
        required=_MIGRATION_FIELDS,
        allowed=_MIGRATION_FIELDS,
    )
    for key in ("forward", "rollback", "backfill", "compatibility"):
        _text(item[key], field=f"{field}.{key}")
    verification = item["verification"]
    if not isinstance(verification, list) or not verification:
        _invalid("Migration verification must be a non-empty array", field=f"{field}.verification")
    if (
        enforce_resource_limits
        and len(verification) > MAX_DATABASE_VERIFICATION_STEPS
    ):
        _invalid(
            "Migration verification exceeds the verification step limit",
            field=f"{field}.verification",
        )
    for index, check in enumerate(verification):
        _text(check, field=f"{field}.verification[{index}]")


def validate_task_database_contract(
    loop: dict[str, Any],
    *,
    field: str,
    enforce_resource_limits: bool = True,
) -> bool:
    """Validate a TASK's explicitly declared frozen database contract."""

    payload = loop["payload"]
    if "databaseChanges" not in payload:
        return False
    changes = payload["databaseChanges"]
    if not isinstance(changes, list) or not changes:
        _invalid(
            "databaseChanges must be a non-empty array",
            field=f"{field}.payload.databaseChanges",
        )
    if (
        enforce_resource_limits
        and len(changes) > MAX_DATABASE_CHANGES_PER_TASK
    ):
        _invalid(
            "databaseChanges exceed the per-task database change limit",
            field=f"{field}.payload.databaseChanges",
        )
    identities: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(changes):
        change_field = f"{field}.payload.databaseChanges[{index}]"
        change = _exact_object(
            raw,
            field=change_field,
            required=_REQUIRED_CHANGE_FIELDS,
            allowed=_CHANGE_FIELDS,
        )
        for optional in ("projectId", "database", "schema"):
            if optional in change:
                _text(change[optional], field=f"{change_field}.{optional}")
        table = _text(change["table"], field=f"{change_field}.table")
        _text(change["summary"], field=f"{change_field}.summary")
        change_type = change["changeType"]
        if change_type not in DATABASE_CHANGE_TYPES:
            _invalid(
                "Database changeType must be CREATE, MODIFY, or DELETE",
                field=f"{change_field}.changeType",
            )
        before = change["before"]
        after = change["after"]
        if change_type == "CREATE" and before is not None:
            _invalid("CREATE before snapshot must be null", field=f"{change_field}.before")
        if change_type == "DELETE" and after is not None:
            _invalid("DELETE after snapshot must be null", field=f"{change_field}.after")
        if change_type in {"MODIFY", "DELETE"} and before is None:
            _invalid(f"{change_type} requires a complete before snapshot", field=f"{change_field}.before")
        if change_type in {"CREATE", "MODIFY"} and after is None:
            _invalid(f"{change_type} requires a complete after snapshot", field=f"{change_field}.after")
        if before is not None:
            _validate_snapshot(
                before,
                field=f"{change_field}.before",
                enforce_resource_limits=enforce_resource_limits,
            )
        if after is not None:
            _validate_snapshot(
                after,
                field=f"{change_field}.after",
                enforce_resource_limits=enforce_resource_limits,
            )
        _validate_migration(
            change["migration"],
            field=f"{change_field}.migration",
            enforce_resource_limits=enforce_resource_limits,
        )
        resource_claim = _text(
            change["resourceClaim"],
            field=f"{change_field}.resourceClaim",
        )
        if resource_claim not in loop["resourceClaims"]:
            _invalid(
                "Each database change resourceClaim must also be declared by the TASK Loop",
                field=f"{change_field}.resourceClaim",
            )
        identity = (
            str(change.get("projectId", "")),
            str(change.get("database", "")),
            str(change.get("schema", "")),
            table,
        )
        if identity in identities:
            _invalid("A TASK may declare each table only once", field=change_field)
        identities.add(identity)
    return True


__all__ = (
    "DATABASE_CHANGE_TYPES",
    "validate_task_database_contract",
)
