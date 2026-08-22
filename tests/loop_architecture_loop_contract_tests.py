from __future__ import annotations

from .loop_architecture_support import (
    GatedLoopError,
    deepcopy,
    loop_completion_policy,
    loop_descriptor,
    loop_execution_policy,
    unittest,
    validate_loop_descriptor,
    validate_loop_outcome,
    validate_review_result_contract,
)


class LoopContractTests(unittest.TestCase):
    def test_loop_descriptor_is_opaque_but_resource_claims_are_normalized(
        self,
    ) -> None:
        source = loop_descriptor(
            claims=[
                "project:erp/module:core",
                "project:erp/module:api",
            ],
        )

        descriptor = validate_loop_descriptor(source)

        self.assertEqual(
            descriptor["resourceClaims"],
            [
                "project:erp/module:api",
                "project:erp/module:core",
            ],
        )
        self.assertEqual(descriptor["payload"], source["payload"])

    def test_loop_descriptor_rejects_duplicate_or_unsafe_claims(self) -> None:
        duplicate = loop_descriptor(claims=["module:core", "module:core"])
        with self.assertRaises(GatedLoopError):
            validate_loop_descriptor(duplicate)

        unsafe = loop_descriptor(claims=["../outside"])
        with self.assertRaises(GatedLoopError):
            validate_loop_descriptor(unsafe)

    def test_loop_outcome_exposes_only_scheduler_terminal_semantics(
        self,
    ) -> None:
        outcome = validate_loop_outcome(
            {
                "status": "SUCCEEDED",
                "summary": "The internal loop completed.",
                "result": {
                    "tests": {"passed": 12},
                    "skills": ["project-java-loop"],
                },
            }
        )
        self.assertEqual(outcome["status"], "SUCCEEDED")
        self.assertEqual(outcome["result"]["tests"]["passed"], 12)

        invalid = deepcopy(outcome)
        invalid["status"] = "GATE_FAILED"
        with self.assertRaises(GatedLoopError):
            validate_loop_outcome(invalid)

    def test_review_success_result_has_layer_specific_acceptance(self) -> None:
        common = {
            "validationDecision": {
                "decision": "REUSED",
                "reusedEvidenceRefs": [
                    {
                        "nodeId": "loop:upstream",
                        "attempt": 1,
                        "evidenceId": "upstream-check",
                    }
                ],
                "executedEvidenceRefs": [],
                "riskTriggers": [],
                "rationale": "The accepted evidence is current and sufficient.",
            },
            "reviewFindings": [],
        }
        task_result = {
            **common,
            "taskAcceptance": {
                "acceptanceChecks": [
                    {
                        "acceptancePoint": "The frozen TASK contract is met.",
                        "status": "SATISFIED",
                        "evidenceRefs": ["upstream-check"],
                    }
                ],
                "localBehavior": "VERIFIED",
                "publicContract": "NOT_APPLICABLE",
                "targetedRegression": "VERIFIED",
                "decision": "ACCEPTED",
                "rationale": "Only the TASK-owned boundary was reviewed.",
            },
        }
        group_result = {
            **common,
            "groupIntegration": {
                "seams": [
                    {
                        "seam": "API to core handoff",
                        "participants": ["t-api", "t-core"],
                        "status": "VERIFIED",
                        "evidenceRefs": ["upstream-check"],
                    }
                ],
                "decision": "INTEGRATED",
                "rationale": "Only direct-child composition was reviewed.",
            },
        }
        delivery_result = {
            **common,
            "deliveryReadiness": {
                "requirementCoverage": [
                    {
                        "acceptancePoint": "The complete user flow is delivered.",
                        "ownerRefs": ["g-service"],
                        "status": "COVERED",
                        "evidenceRefs": ["upstream-check"],
                    }
                ],
                "integrationEvidence": "SUFFICIENT",
                "operationalReadiness": "NOT_APPLICABLE",
                "openBlockingRisks": [],
                "acceptedRisks": [],
                "decision": "READY_FOR_USER_CONFIRMATION",
                "rationale": "Coverage and final evidence are complete.",
            },
        }

        self.assertEqual(
            validate_review_result_contract("TASK_REVIEW_LOOP", task_result),
            task_result,
        )
        self.assertEqual(
            validate_review_result_contract("GROUP_REVIEW_LOOP", group_result),
            group_result,
        )
        self.assertEqual(
            validate_review_result_contract(
                "DELIVERY_REVIEW_LOOP",
                delivery_result,
            ),
            delivery_result,
        )
        duplicated = dict(task_result)
        duplicated["upstreamLoopResults"] = [
            {"nodeId": "task:t-api", "outcome": {"result": {}}}
        ]
        with self.assertRaises(GatedLoopError) as caught:
            validate_review_result_contract("TASK_REVIEW_LOOP", duplicated)
        self.assertEqual(caught.exception.code, "LOOP_REVIEW_RESULT_INVALID")

    def test_review_success_result_rejects_cross_layer_or_open_blockers(
        self,
    ) -> None:
        invalid = {
            "validationDecision": {
                "decision": "TARGETED_RERUN",
                "reusedEvidenceRefs": [],
                "executedEvidenceRefs": ["review-check"],
                "riskTriggers": ["A gap was found."],
                "rationale": "A targeted check was executed.",
            },
            "reviewFindings": [
                {
                    "severity": "P1",
                    "summary": "A blocking defect remains.",
                    "status": "OPEN",
                    "resolution": "Not resolved.",
                    "evidence": "The failing check is still reproducible.",
                }
            ],
            "deliveryReadiness": {
                "requirementCoverage": [],
                "integrationEvidence": "SUFFICIENT",
                "operationalReadiness": "READY",
                "openBlockingRisks": ["The P1 defect remains."],
                "acceptedRisks": [],
                "decision": "READY_FOR_USER_CONFIRMATION",
                "rationale": "This result must not be accepted.",
            },
        }

        with self.assertRaises(GatedLoopError) as caught:
            validate_review_result_contract("DELIVERY_REVIEW_LOOP", invalid)
        self.assertEqual(caught.exception.code, "LOOP_REVIEW_RESULT_INVALID")

    def test_review_result_rejects_unresolved_executed_evidence(self) -> None:
        result = {
            "validationDecision": {
                "decision": "TARGETED_RERUN",
                "reusedEvidenceRefs": [],
                "executedEvidenceRefs": ["review-check"],
                "riskTriggers": ["A targeted check was required."],
                "rationale": "The Review executed a focused check.",
            },
            "reviewFindings": [],
            "taskAcceptance": {
                "acceptanceChecks": [
                    {
                        "acceptancePoint": "The TASK contract is met.",
                        "status": "SATISFIED",
                        "evidenceRefs": ["review-check"],
                    }
                ],
                "localBehavior": "VERIFIED",
                "publicContract": "NOT_APPLICABLE",
                "targetedRegression": "VERIFIED",
                "decision": "ACCEPTED",
                "rationale": "The TASK boundary is accepted.",
            },
        }

        with self.assertRaises(GatedLoopError) as caught:
            validate_review_result_contract("TASK_REVIEW_LOOP", result)

        self.assertEqual(caught.exception.code, "LOOP_REVIEW_RESULT_INVALID")
        self.assertEqual(
            caught.exception.details["field"],
            "loopOutcome.result.validationDecision.executedEvidenceRefs[0]",
        )

        result["verificationEvidence"] = [
            {
                "evidenceId": evidence_id,
                "kind": "INSPECTION",
                "check": f"Independent check {evidence_id}",
                "command": "independent review inspection",
                "scope": "The TASK Review boundary",
                "status": "PASSED",
                "completedAt": "2030-01-01T00:00:00Z",
            }
            for evidence_id in ("review-check", "undeclared-check")
        ]
        result["taskAcceptance"]["acceptanceChecks"][0][
            "evidenceRefs"
        ] = ["undeclared-check"]

        with self.assertRaises(GatedLoopError) as caught:
            validate_review_result_contract("TASK_REVIEW_LOOP", result)

        self.assertEqual(
            caught.exception.details["field"],
            "loopOutcome.result.taskAcceptance.acceptanceChecks[0].evidenceRefs[0]",
        )

    def test_review_policies_assign_one_non_overlapping_boundary_per_layer(
        self,
    ) -> None:
        execution_policy = loop_execution_policy()
        self.assertEqual(
            execution_policy["reviewTopology"],
            "TASK_REVIEWS_OPTIONAL_GROUP_SEAM_REVIEWS_AND_DELIVERY_ACCEPTANCE",
        )
        self.assertEqual(
            execution_policy["responsibilityBoundaries"],
            {
                "controller": {
                    "owns": [
                        "GRAPH_STATE_TRANSITIONS",
                        "PREDECESSOR_SUCCESS_GATING",
                        "RESULT_CONTRACT_VALIDATION",
                        "EVENT_AND_PROJECTION_PERSISTENCE",
                    ],
                    "mustNotPerform": [
                        "TECHNICAL_ACCEPTANCE",
                        "EVIDENCE_SUFFICIENCY_JUDGMENT",
                        "OPERATIONAL_READINESS_JUDGMENT",
                    ],
                },
                "loopReceiver": {
                    "owns": [
                        "LOOP_EXECUTION",
                        "LOOP_OWNED_JUDGMENT",
                        "EVIDENCE_SELECTION_AND_VERIFICATION",
                        "FINDING_CLOSURE",
                    ],
                    "mustNotPerform": [
                        "GRAPH_READINESS_TRANSITION",
                        "UPSTREAM_COMPLETION_GATING",
                        "USER_CONFIRMATION",
                    ],
                },
                "user": {
                    "owns": ["FINAL_BUSINESS_CONFIRMATION"],
                    "mustNotReplace": [
                        "GRAPH_PRECONDITION_GATING",
                        "DELIVERY_TECHNICAL_ACCEPTANCE",
                    ],
                },
            },
        )
        expected = {
            "TASK_REVIEW_LOOP": (
                "TASK",
                "taskAcceptance",
                "GROUP_INTEGRATION",
            ),
            "GROUP_REVIEW_LOOP": (
                "GROUP",
                "groupIntegration",
                "TASK_INTERNAL_IMPLEMENTATION",
            ),
            "DELIVERY_REVIEW_LOOP": (
                "DELIVERY",
                "deliveryReadiness",
                "LOWER_LAYER_CODE_REREVIEW",
            ),
        }
        for loop_kind, (layer, result_field, excluded) in expected.items():
            with self.subTest(loop_kind=loop_kind):
                boundary = loop_completion_policy(
                    loop_kind=loop_kind,
                )["reviewBoundary"]
                self.assertEqual(boundary["layer"], layer)
                self.assertEqual(
                    boundary["requiredResultField"],
                    result_field,
                )
                self.assertIn(excluded, boundary["mustNotRepeat"])
                persistence = loop_completion_policy(
                    loop_kind=loop_kind,
                )["reviewResultPersistence"]
                self.assertEqual(
                    persistence["contractValidator"],
                    "CONTROLLER",
                )
                self.assertEqual(
                    persistence["acceptanceDecisionOwner"],
                    "INDEPENDENT_LOOP_RECEIVER",
                )
                self.assertEqual(
                    persistence["controllerValidationScope"],
                    "STRUCTURE_AND_DECLARED_TERMINAL_CONSISTENCY_ONLY",
                )

    def test_loop_completion_policy_keeps_verification_with_receiver(
        self,
    ) -> None:
        policy = loop_completion_policy()

        self.assertEqual(
            policy["verificationScope"],
            "AFFECTED_SCOPE_SUFFICIENT_FOR_DECLARED_ACCEPTANCE",
        )
        self.assertEqual(
            policy["verificationStrategy"]["mode"],
            "AFFECTED_SCOPE_FIRST",
        )
        self.assertEqual(
            policy["verificationStrategy"]["default"],
            "RUN_MINIMUM_SUFFICIENT_CHECKS",
        )
        self.assertEqual(
            policy["actionableFinding"],
            "RESOLVE_AND_REEVALUATE_IN_CURRENT_LOOP",
        )
        self.assertEqual(
            policy["workspaceChanges"]["source"],
            "CONTROLLER_CAPTURED_AT_RESULT",
        )
