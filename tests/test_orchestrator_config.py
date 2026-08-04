from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.orchestrator_config import (
    ORCHESTRATOR_CONFIG_ENV,
    ORCHESTRATOR_CONFIG_SCHEMA_VERSION,
    load_orchestrator_config,
    orchestrator_config_path,
    save_orchestrator_config,
)


def configured_policy(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 2,
        "maxConcurrentExecutors": 4,
        "quotaExhaustionPolicy": "PAUSE_AND_RESUME",
    }
    value.update(overrides)
    return value


class OrchestratorConfigTests(unittest.TestCase):
    def test_missing_file_uses_safe_defaults(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "orchestrator.json")
            config = load_orchestrator_config(
                home=Path(root),
                environ={ORCHESTRATOR_CONFIG_ENV: str(path)},
            )

        self.assertEqual(ORCHESTRATOR_CONFIG_SCHEMA_VERSION, 2)
        self.assertEqual(config.max_concurrent_executors, 4)
        self.assertEqual(config.quota_exhaustion_policy, "PAUSE_AND_RESUME")
        self.assertEqual(config.policy(), configured_policy())
        for legacy_attribute in (
            "automatic_orchestration",
            "allowed_adapters",
            "auto_select_model",
            "allow_cross_adapter_dispatch",
            "prefer_different_adapter_for_review",
        ):
            self.assertFalse(hasattr(config, legacy_attribute))
        self.assertEqual(config.source, "BUILT_IN_DEFAULTS")
        self.assertEqual(config.config_path, str(path))

    def test_user_file_loads_complete_sop_policy(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "orchestrator.json")
            path.write_text(
                json.dumps(
                    configured_policy(
                        maxConcurrentExecutors=7,
                    )
                ),
                encoding="utf-8",
            )
            config = load_orchestrator_config(
                environ={ORCHESTRATOR_CONFIG_ENV: str(path)},
            )

        self.assertEqual(config.max_concurrent_executors, 7)
        self.assertEqual(
            config.quota_exhaustion_policy,
            "PAUSE_AND_RESUME",
        )
        self.assertEqual(config.source, "USER_CONFIG")

    def test_save_atomically_creates_and_reloads_complete_policy(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "nested", "orchestrator.json")
            policy = configured_policy(
                maxConcurrentExecutors=6,
            )
            saved = save_orchestrator_config(
                policy,
                environ={ORCHESTRATOR_CONFIG_ENV: str(path)},
            )
            loaded = load_orchestrator_config(
                environ={ORCHESTRATOR_CONFIG_ENV: str(path)},
            )

            self.assertEqual(saved.policy(), policy)
            self.assertEqual(loaded.policy(), policy)
            self.assertEqual(saved.source, "USER_CONFIG")
            self.assertEqual(saved.config_path, str(path))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                policy,
            )
            self.assertFalse(
                any(item.name.startswith(".orchestrator.json.tmp-") for item in path.parent.iterdir())
            )

    def test_invalid_save_does_not_create_or_replace_config(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "orchestrator.json")
            original = configured_policy()
            path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaises(GatedLoopError) as caught:
                save_orchestrator_config(
                    configured_policy(maxConcurrentExecutors=0),
                    environ={ORCHESTRATOR_CONFIG_ENV: str(path)},
                )

            self.assertEqual(caught.exception.code, "ORCHESTRATOR_CONFIG_INVALID")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_platform_paths_are_user_level_and_shared(self) -> None:
        home = Path("/users/example")
        windows = orchestrator_config_path(
            home=home,
            environ={"APPDATA": "C:/Users/example/AppData/Roaming"},
            platform="win32",
        )
        macos = orchestrator_config_path(
            home=home,
            environ={},
            platform="darwin",
        )
        linux = orchestrator_config_path(
            home=home,
            environ={"XDG_CONFIG_HOME": "/etc/user-config"},
            platform="linux",
        )

        self.assertEqual(
            windows,
            Path(
                "C:/Users/example/AppData/Roaming/layered-delivery/"
                "orchestrator.json"
            ),
        )
        self.assertEqual(
            macos,
            home
            / "Library"
            / "Application Support"
            / "layered-delivery"
            / "orchestrator.json",
        )
        self.assertEqual(
            linux,
            Path("/etc/user-config/layered-delivery/orchestrator.json"),
        )

    def test_relative_explicit_path_is_rejected(self) -> None:
        with self.assertRaises(GatedLoopError) as caught:
            orchestrator_config_path(
                environ={ORCHESTRATOR_CONFIG_ENV: "relative/config.json"}
            )

        self.assertEqual(
            caught.exception.code,
            "ORCHESTRATOR_CONFIG_PATH_INVALID",
        )

    def test_unknown_or_missing_fields_fail_closed(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "orchestrator.json")
            value = configured_policy()
            del value["maxConcurrentExecutors"]
            value["unexpected"] = True
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(GatedLoopError) as caught:
                load_orchestrator_config(
                    environ={ORCHESTRATOR_CONFIG_ENV: str(path)}
                )

        self.assertEqual(
            caught.exception.code,
            "ORCHESTRATOR_CONFIG_INVALID",
        )

    def test_legacy_orchestration_fields_fail_closed(self) -> None:
        legacy_fields = {
            "automaticOrchestration": True,
            "allowedAdapters": ["codex", "claude-code"],
            "autoSelectModel": True,
            "allowCrossAdapterDispatch": False,
            "preferDifferentAdapterForReview": True,
        }
        for field, value in legacy_fields.items():
            with self.subTest(field=field), TemporaryDirectory() as root:
                path = Path(root, "orchestrator.json")
                path.write_text(
                    json.dumps(configured_policy(**{field: value})),
                    encoding="utf-8",
                )

                with self.assertRaises(GatedLoopError) as caught:
                    load_orchestrator_config(
                        environ={ORCHESTRATOR_CONFIG_ENV: str(path)}
                    )

                self.assertEqual(
                    caught.exception.code,
                    "ORCHESTRATOR_CONFIG_INVALID",
                )

    def test_invalid_option_types_and_values_fail_closed(self) -> None:
        invalid_values = (
            {"schemaVersion": 2.0},
            {"maxConcurrentExecutors": 0},
            {"quotaExhaustionPolicy": "GUESS_RESET"},
            {"quotaExhaustionPolicy": "ASK_USER"},
            {"quotaExhaustionPolicy": "SWITCH_ADAPTER"},
            {"quotaExhaustionPolicy": []},
        )
        for overrides in invalid_values:
            with self.subTest(overrides=overrides), TemporaryDirectory() as root:
                path = Path(root, "orchestrator.json")
                path.write_text(
                    json.dumps(configured_policy(**overrides)),
                    encoding="utf-8",
                )
                with self.assertRaises(GatedLoopError) as caught:
                    load_orchestrator_config(
                        environ={ORCHESTRATOR_CONFIG_ENV: str(path)}
                    )
                self.assertEqual(
                    caught.exception.code,
                    "ORCHESTRATOR_CONFIG_INVALID",
                )


if __name__ == "__main__":
    unittest.main()
