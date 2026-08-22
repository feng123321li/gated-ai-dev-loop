from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.entry_routing import decide_entry_route
from hdg.errors import GatedLoopError
from hdg.jsonio import pretty_json
from hdg.supervisor_profiles import (
    SUPERVISOR_REGISTRY_FILE,
    built_in_supervisor_registry,
    build_supervisor_routing,
    load_supervisor_registry,
    validate_supervisor_registry,
)


class SupervisorProfileTests(unittest.TestCase):
    def test_built_in_registry_is_optional_and_decision_only(self) -> None:
        registry = built_in_supervisor_registry()
        routing = build_supervisor_routing(
            registry,
            explicit_intent="CONTINUE_DELIVERY",
            route_decision={
                "intent": "DISPATCH_ACTIVE",
                "targetSkill": "delivery-graph-dispatch",
                "requiresClarification": False,
            },
        )

        self.assertFalse(registry["enabled"])
        self.assertFalse(routing["shouldInvoke"])
        self.assertEqual(
            routing["selectedSupervisorId"],
            "execution-supervisor",
        )
        self.assertEqual(routing["boundary"]["toolAccess"], "NONE")
        self.assertFalse(routing["boundary"]["generatesUserResponse"])
        self.assertFalse(routing["boundary"]["executesRoute"])

    def test_ambiguous_only_mode_invokes_only_the_fallback_classifier(
        self,
    ) -> None:
        registry = built_in_supervisor_registry()
        registry.pop("registryFingerprint")
        registry.pop("configurationSource")
        registry["enabled"] = True
        validated = validate_supervisor_registry(registry)

        ambiguous = build_supervisor_routing(
            validated,
            explicit_intent="AMBIGUOUS",
            route_decision={
                "intent": "AMBIGUOUS",
                "targetSkill": None,
                "requiresClarification": True,
            },
        )
        stable = build_supervisor_routing(
            validated,
            explicit_intent="NEW_DELIVERY",
            route_decision={
                "intent": "NEW_DELIVERY",
                "targetSkill": "delivery-graph",
                "requiresClarification": False,
            },
        )

        self.assertTrue(ambiguous["shouldInvoke"])
        self.assertEqual(
            ambiguous["selectedSupervisorId"],
            "entry-supervisor",
        )
        self.assertFalse(stable["shouldInvoke"])

    def test_always_advisory_selects_specialist_without_claiming_enforcement(
        self,
    ) -> None:
        registry = built_in_supervisor_registry()
        registry.pop("registryFingerprint")
        registry.pop("configurationSource")
        registry["enabled"] = True
        registry["activationMode"] = "ALWAYS_ADVISE"
        validated = validate_supervisor_registry(registry)

        decision = decide_entry_route(
            request_text="恢复执行",
            workspace_state={"status": "PAUSED", "rootId": "d-paused"},
            supervisor_registry=validated,
        )

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["intent"], "RESUME_PAUSED")
        self.assertTrue(decision["supervisorRouting"]["shouldInvoke"])
        self.assertEqual(
            decision["supervisorRouting"]["selectedSupervisorId"],
            "execution-supervisor",
        )
        self.assertEqual(
            decision["supervisorRouting"]["enforcement"],
            "HOST_ADVISORY_NO_DECISION_RECEIPT",
        )
        self.assertFalse(
            decision["supervisorRouting"]["decisionReceiptRequired"]
        )

    def test_project_registry_can_enable_multi_supervisor(self) -> None:
        with TemporaryDirectory() as root:
            custom = built_in_supervisor_registry()
            custom.pop("registryFingerprint")
            custom.pop("configurationSource")
            custom["enabled"] = True
            Path(root, SUPERVISOR_REGISTRY_FILE).write_text(
                pretty_json(custom),
                encoding="utf-8",
            )

            loaded = load_supervisor_registry(root)

        self.assertTrue(loaded["enabled"])
        self.assertEqual(loaded["configurationSource"], "PROJECT_JSON")

    def test_missing_intent_coverage_is_rejected(self) -> None:
        registry = built_in_supervisor_registry()
        registry.pop("registryFingerprint")
        registry.pop("configurationSource")
        broken = deepcopy(registry)
        broken["profiles"][0]["handles"].remove("NEW_DELIVERY")

        with self.assertRaises(GatedLoopError) as caught:
            validate_supervisor_registry(broken)

        self.assertEqual(caught.exception.code, "SUPERVISOR_REGISTRY_INVALID")

    def test_documented_registry_example_is_valid(self) -> None:
        example = (
            Path(__file__).parents[1]
            / "docs"
            / "examples"
            / SUPERVISOR_REGISTRY_FILE
        )

        loaded = load_supervisor_registry(example.parent)

        self.assertTrue(loaded["enabled"])
        self.assertEqual(loaded["activationMode"], "AMBIGUOUS_ONLY")


if __name__ == "__main__":
    unittest.main()
