from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    WORK_ITEM_DIRECTORY,
    archive_delivery,
    at,
    close_delivery,
    closing,
    delivery_task_hierarchy,
    dispatch_loop,
    get_graph_frontier,
    graph_events,
    group_hierarchy,
    group_review_node_id,
    join_node_id,
    loop_context,
    loop_descriptor,
    loop_execution_policy,
    loop_node_id,
    pause_loop,
    preview_hierarchy,
    record_loop_result,
    recursive_hierarchy,
    resume_loop,
    review_node_id,
    review_success,
    skill_hint,
    sqlite3,
    success,
    success_for_node,
    task_hierarchy,
    workspace_status,
)
class SchedulerRuntimeTestsPart4:
    def test_archived_delivery_cannot_be_previewed_again(self) -> None:
        completed = self.complete_task_delivery("d-archived-preview")
        root_id = completed["rootId"]
        close_delivery(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            closed_by="archive-user",
            summary="Production delivery completed.",
            now=at(9),
        )
        archive_delivery(root=self.root, root_id=root_id, now=at(9))
        stored = SchedulerRepository(self.root).hierarchy(root_id)

        with self.assertRaises(GatedLoopError) as caught:
            preview_hierarchy(
                root=self.root,
                hierarchy=stored["hierarchy"],
                now=at(10),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_ARCHIVED",
        )
        self.assertEqual(
            SchedulerRepository(self.root).hierarchy(root_id)["status"],
            "ARCHIVED",
        )

    def test_archived_delivery_retains_external_requirement_identity(
        self,
    ) -> None:
        completed = self.complete_task_delivery(
            "d-archived-requirement",
            requirement_key="ORDER-443",
        )
        close_delivery(
            root=self.root,
            root_id=completed["rootId"],
            confirmed=True,
            closed_by="archive-user",
            summary="Production delivery completed.",
            now=at(9),
        )
        archive_delivery(
            root=self.root,
            root_id=completed["rootId"],
            now=at(9),
        )
        replacement = delivery_task_hierarchy(
            "d-replacement-requirement",
            "t-replacement-requirement",
        )
        replacement["delivery"]["requirementKey"] = "order-443"

        with self.assertRaises(GatedLoopError) as caught:
            preview_hierarchy(
                root=self.root,
                hierarchy=replacement,
                now=at(10),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_REQUIREMENT_CONFLICT",
        )
        self.assertEqual(
            caught.exception.details["existingRootId"],
            completed["rootId"],
        )
        self.assertEqual(
            caught.exception.details["nextAction"],
            "CREATE_NEW_REQUIREMENT_AND_DELIVERY",
        )

    def test_archived_delivery_state_is_checked_fail_closed(self) -> None:
        completed = self.complete_task_delivery("d-archived-corrupt")
        root_id = completed["rootId"]
        close_delivery(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            closed_by="archive-user",
            summary="Production delivery completed.",
            now=at(9),
        )
        archive_delivery(root=self.root, root_id=root_id, now=at(9))
        repository = SchedulerRepository(self.root)
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE delivery_revisions SET status = 'FROZEN' "
                "WHERE root_id = ? AND revision = 1",
                (root_id,),
            )

        with self.assertRaises(GatedLoopError) as caught:
            workspace_status(root=self.root, root_id=root_id)

        self.assertEqual(caught.exception.code, "SCHEDULER_STATE_INVALID")
        self.assertEqual(caught.exception.details["rootId"], root_id)

    def test_root_task_review_runs_before_delivery_review(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["reviewLoop"] = loop_descriptor(
            "task/independent-review-loop@1"
        )
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        task_id = loop_node_id("t-service")
        task_review_id = "review:task:t-service"

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_id,
            owner="task-agent",
            operation_id="op-task-with-review",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_id,
            operation_id="op-task-with-review",
            outcome=success("Task implementation completed."),
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [task_review_id],
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
        )
        self.assertEqual(context["kind"], "TASK_REVIEW_LOOP")
        self.assertEqual(context["workItemId"], "t-service")
        self.assertEqual(context["humanArtifacts"]["workItem"]["kind"], "TASK")
        self.assertEqual(
            context["loop"]["ref"],
            "task/independent-review-loop@1",
        )
        verification_strategy = context["completionPolicy"][
            "verificationStrategy"
        ]
        self.assertEqual(
            verification_strategy["mode"],
            "EVIDENCE_FIRST_TARGETED_RERUN",
        )
        self.assertEqual(
            verification_strategy["independence"],
            "INDEPENDENT_JUDGMENT_NOT_AUTOMATIC_FULL_RERUN",
        )
        self.assertEqual(
            verification_strategy["rerunDefault"],
            "TARGET_GAPS_FINDINGS_AND_HIGH_RISK_BOUNDARIES",
        )
        self.assertIn(
            "RELEVANT_WORKSPACE_CHANGED_AFTER_EVIDENCE",
            verification_strategy["evidenceInvalidationTriggers"],
        )
        self.assertIn(
            "AFFECTED_SCOPE_CANNOT_BE_BOUNDED",
            verification_strategy["fullRerunTriggers"],
        )
        self.assertTrue(
            context["rules"]["reuseValidUpstreamVerificationEvidence"]
        )
        self.assertTrue(
            context["rules"][
                "reviewIndependenceDoesNotRequireFullSuiteRerun"
            ]
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
            owner="task-reviewer",
            operation_id="op-task-review",
            now=at(5),
        )
        with self.assertRaises(GatedLoopError) as caught:
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=task_review_id,
                operation_id="op-task-review",
                outcome=success("Unscoped review result."),
                now=at(6),
            )
        self.assertEqual(
            caught.exception.code,
            "LOOP_REVIEW_RESULT_INVALID",
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
            operation_id="op-task-review",
            outcome=review_success(
                "TASK_REVIEW_LOOP",
                "Task review completed.",
            ),
            now=at(6),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(7),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [review_node_id(root_id)],
        )
        projection_root = Path(self.root, ".layered-delivery", root_id)
        task_root = projection_root / WORK_ITEM_DIRECTORY / "t-service"
        baseline = (task_root / "baseline.md").read_text(encoding="utf-8")
        progress = (task_root / "progress.md").read_text(encoding="utf-8")
        acceptance = (task_root / "acceptance.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("task/independent-review-loop@1", baseline)
        self.assertIn("TASK Review", progress)
        self.assertIn("Task review completed.", acceptance)

    def test_group_without_seam_review_prepares_join_as_terminal(self) -> None:
        hierarchy = group_hierarchy()
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)

        graph = SchedulerRepository(self.root).hierarchy(
            prepared["rootId"]
        )["graph"]
        self.assertNotIn(
            group_review_node_id("g-service"),
            {item["id"] for item in graph["nodes"]},
        )
        self.assertIn(
            (
                join_node_id("g-service"),
                review_node_id(prepared["rootId"]),
            ),
            {
                (edge["source"], edge["target"])
                for edge in graph["edges"]
            },
        )
        get_graph_frontier(
            root=self.root,
            root_id=prepared["rootId"],
            now=at(2),
        )
        run = SchedulerRepository(self.root).run(prepared["rootId"])
        self.assertNotIn(
            group_review_node_id("g-service"),
            {item["nodeId"] for item in run["nodes"]},
        )
        with closing(sqlite3.connect(
            Path(self.root, ".layered-delivery", "scheduler.db")
        )) as connection:
            stored_review_rows = connection.execute(
                "SELECT COUNT(*) FROM node_runs "
                "WHERE run_id = ? AND node_id = ?",
                (run["runId"], group_review_node_id("g-service")),
            ).fetchone()[0]
        self.assertEqual(stored_review_rows, 0)
        self.assertFalse(
            any(
                event["nodeId"] == group_review_node_id("g-service")
                for event in graph_events(
                    root=self.root,
                    root_id=prepared["rootId"],
                )["events"]
            )
        )
        group_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / WORK_ITEM_DIRECTORY
            / "g-service"
        )
        baseline = (group_root / "baseline.md").read_text(encoding="utf-8")
        progress = (group_root / "progress.md").read_text(encoding="utf-8")
        acceptance = (group_root / "acceptance.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("GROUP 完成点是本节点终态", baseline)
        self.assertNotIn("| GROUP seam Review |", progress)
        self.assertIn("GROUP 完成点即本 GROUP 终态", acceptance)
        self.assertNotIn("GROUP seam Review 输入", acceptance)
        self.assertNotIn("GROUP seam Review 结果", acceptance)

    def test_group_review_policy_reuses_task_evidence_and_limits_commands(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(group_hierarchy())
        context = loop_context(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=group_review_node_id("g-service"),
        )

        strategy = context["completionPolicy"]["verificationStrategy"]
        command_policy = strategy["layerCommandPolicy"]
        self.assertEqual(
            command_policy["defaultDecision"],
            "REUSE_EXACT_MATCH_UPSTREAM_EVIDENCE",
        )
        self.assertEqual(
            command_policy["newCommandScope"],
            "DIRECT_CHILD_SEAMS_ONLY",
        )
        self.assertEqual(
            command_policy["taskLocalSuites"],
            "DO_NOT_RERUN_BY_DEFAULT",
        )
        self.assertEqual(
            command_policy["fullBuild"],
            "REQUIRE_EXPLICIT_FULL_RERUN_TRIGGER",
        )
        self.assertEqual(
            command_policy["specializedCommandWorker"],
            "SEAM_GAP_ONLY",
        )

    def test_task_and_review_select_shared_skill_hints_at_runtime(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD when the active Loop is a Spring task.",
            )
        ]
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]

        for node_id in (
            loop_node_id("t-service"),
            review_node_id(root_id),
        ):
            with self.subTest(node_id=node_id):
                context = loop_context(
                    root=self.root,
                    root_id=root_id,
                    node_id=node_id,
                )
                self.assertEqual(
                    context["skillHints"],
                    hierarchy["root"]["skillHints"],
                )
                self.assertTrue(
                    context["rules"]["skillHintsAreAdvisory"]
                )
                self.assertTrue(
                    context["rules"][
                        "explicitSkillHintsShouldRunWhenApplicableAndAvailable"
                    ]
                )
                self.assertTrue(
                    context["rules"][
                        "skipSkillHintOnlyWhenStageInapplicableOrHostUnavailable"
                    ]
                )
                self.assertTrue(
                    context["rules"]["selectSkillsAtRuntime"]
                )
                self.assertTrue(
                    context["rules"][
                        "prioritizeApplicableSkillHints"
                    ]
                )
                self.assertIn(
                    "`$springboot-tdd`",
                    context["skillHintPrompt"],
                )
                self.assertIn("多数在 TASK 阶段使用", context["skillHintPrompt"])
                self.assertIn("才跳过", context["skillHintPrompt"])

    def test_advisory_skill_hint_does_not_gate_loop_success(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD when the active Loop is Spring Boot work.",
            )
        ]
        prepared = self.prepare_and_freeze(hierarchy)
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=node_id,
            owner="agent-advisory-skill-success",
            operation_id="op-advisory-skill-success",
            now=at(2),
        )

        recorded = record_loop_result(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=node_id,
            operation_id="op-advisory-skill-success",
            outcome=success("Loop completed without a Skill receipt gate."),
            now=at(3),
        )

        self.assertEqual(recorded["outcome"]["status"], "SUCCEEDED")

    def test_recursive_review_context_contains_all_upstream_loop_results(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(group_hierarchy())
        root_id = prepared["rootId"]
        for minute, item_id in ((2, "t-api"), (6, "t-core")):
            node_id = loop_node_id(item_id)
            operation_id = f"op-{item_id}"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="agent",
                receiver_context_id=f"context-{item_id}",
                operation_id=operation_id,
                now=at(minute),
            )
            task_outcome = success(f"{item_id} completed.")
            task_outcome["result"]["workspaceChanges"] = [
                {
                    "projectId": item_id,
                    "changedFiles": [{"path": f"{item_id}.py"}],
                    "diff": "task implementation diff",
                }
            ]
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=task_outcome,
                now=at(minute + 1),
            )
            review_id = f"review:task:{item_id}"
            review_operation = f"op-review-{item_id}"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=review_id,
                owner="task-reviewer",
                receiver_context_id=f"context-review-{item_id}",
                operation_id=review_operation,
                now=at(minute + 2),
            )
            task_review_outcome = review_success(
                "TASK_REVIEW_LOOP",
                f"{item_id} review completed.",
            )
            task_review_outcome["result"]["workspaceChanges"] = [
                {
                    "projectId": item_id,
                    "changedFiles": [{"path": f"{item_id}.py"}],
                    "diff": "task review snapshot diff",
                }
            ]
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=review_id,
                operation_id=review_operation,
                outcome=task_review_outcome,
                now=at(minute + 3),
            )

        group_review_id = group_review_node_id("g-service")
        group_context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=group_review_id,
        )
        omitted_implementation_detail = False
        for upstream in group_context["upstreamLoopResults"]:
            result = upstream["outcome"]["result"]
            self.assertNotIn("workspaceChanges", result)
            self.assertTrue(result["workspaceChangesOmittedFromReviewContext"])
            self.assertNotIn("evidence", result)
            omitted_implementation_detail = (
                omitted_implementation_detail
                or "evidence"
                in result["resultDetailsOmittedFromReviewContext"]
            )
        self.assertTrue(omitted_implementation_detail)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=group_review_id,
            owner="group-reviewer",
            receiver_context_id="context-review-g-service",
            operation_id="op-group-review",
            now=at(10),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=group_review_id,
            operation_id="op-group-review",
            outcome=review_success(
                "GROUP_REVIEW_LOOP",
                "g-service review completed.",
            ),
            now=at(11),
        )

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
        )

        self.assertEqual(
            [
                item["nodeId"]
                for item in context["upstreamLoopResults"]
            ],
            [
                loop_node_id("t-api"),
                loop_node_id("t-core"),
                group_review_id,
                "review:task:t-api",
                "review:task:t-core",
            ],
        )
        self.assertEqual(
            [
                item["outcome"]["summary"]
                for item in context["upstreamLoopResults"]
            ],
            [
                "t-api completed.",
                "t-core completed.",
                "g-service review completed.",
                "t-api review completed.",
                "t-core review completed.",
            ],
        )

    def test_reviews_progress_recursively_from_groups_to_delivery(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(recursive_hierarchy())
        root_id = prepared["rootId"]
        minute = 2

        def complete(node_id: str) -> None:
            nonlocal minute
            operation_id = f"op-{node_id.replace(':', '-')}"
            frontier = get_graph_frontier(
                root=self.root,
                root_id=root_id,
                now=at(minute),
            )
            self.assertIn(
                node_id,
                [item["nodeId"] for item in frontier["readyLoops"]],
            )
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="recursive-agent",
                receiver_context_id=f"context-{node_id}",
                operation_id=operation_id,
                now=at(minute),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success_for_node(node_id, f"{node_id} completed."),
                now=at(minute + 1),
            )
            minute += 2

        ordered_loops = [
            loop_node_id("t-bootstrap"),
            "review:task:t-bootstrap",
            loop_node_id("t-model"),
            "review:task:t-model",
            loop_node_id("t-repository"),
            "review:task:t-repository",
            group_review_node_id("g-domain"),
            loop_node_id("t-api"),
            "review:task:t-api",
            group_review_node_id("g-backend"),
            loop_node_id("t-e2e"),
            "review:task:t-e2e",
            group_review_node_id("g-quality"),
            loop_node_id("t-docs"),
            "review:task:t-docs",
            group_review_node_id("g-root"),
        ]
        for node_id in ordered_loops:
            complete(node_id)

        delivery_review_id = review_node_id(root_id)
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=delivery_review_id,
        )
        self.assertEqual(
            {
                item["nodeId"]
                for item in context["upstreamLoopResults"]
            },
            set(ordered_loops),
        )
        complete(delivery_review_id)
        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(minute),
        )
        self.assertEqual(
            frontier["actions"],
            [
                {
                    "action": "RECORD_USER_CONFIRMATION",
                    "nodeId": f"confirm:{root_id}",
                }
            ],
        )

    def test_expired_worker_cannot_pause_or_submit_a_result(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent",
            operation_id="op-expired",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError):
            pause_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-expired",
                now=at(40),
            )
        with self.assertRaises(GatedLoopError):
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-expired",
                outcome=success("Too late."),
                now=at(40),
            )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(40),
        )
        self.assertEqual(
            frontier["readyLoops"][0]["attempt"],
            2,
        )

    def test_loop_context_handoff_separates_expired_lease_recovery(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        policy = loop_execution_policy()
        self.assertEqual(
            policy["claimedLoopHandoff"],
            {
                "trigger": "CONTEXT_PRESSURE",
                "requiresLiveLease": True,
                "action": "PAUSE_AND_HANDOFF",
                "loopOutcome": "NONE",
            },
        )
        self.assertEqual(
            policy["expiredLeaseRecovery"],
            {
                "action": "ADVANCE_GRAPH",
                "pauseAllowed": False,
                "reuseOperationId": False,
            },
        )
        self.assertNotIn("capacityPressure", repr(policy))

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        dispatch_action = next(
            action
            for action in frontier["actions"]
            if action["action"] == "DISPATCH_LOOP"
        )
        self.assertEqual(
            dispatch_action["executionPolicy"],
            policy,
        )

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        self.assertEqual(context["executionPolicy"], policy)

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-original",
            operation_id="op-original",
            now=at(3),
        )
        paused = pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-original",
            now=at(4),
        )
        self.assertEqual(paused["status"], "PAUSED")
        self.assertEqual(paused["executionPolicy"], policy)
        self.assertEqual(
            paused["handoff"]["resumeSequence"],
            [
                "graph_frontier",
                "resume_loop",
                "graph_frontier",
                "loop_context",
                "dispatch_loop",
            ],
        )
        self.assertTrue(paused["handoff"]["reuseFrozenGraph"])
        self.assertFalse(paused["handoff"]["reprepare"])
        self.assertFalse(paused["handoff"]["refreeze"])

        paused_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(
            [item["nodeId"] for item in paused_frontier["pausedLoops"]],
            [node_id],
        )
        self.assertIn(
            {
                "action": "RESUME_LOOP_IN_INDEPENDENT_CONTEXT",
                "nodeId": node_id,
                "executionPolicy": policy,
            },
            paused_frontier["actions"],
        )

        resumed = resume_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            now=at(6),
        )
        self.assertEqual(resumed["status"], "READY")
        self.assertEqual(resumed["executionPolicy"], policy)
        self.assertIn("REDISPATCH", resumed["nextAction"])
        ready_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(7),
        )
        self.assertEqual(ready_frontier["pausedLoops"], [])
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready_frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )

    def test_light_assurance_keeps_safety_but_reduces_process_reporting(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": (
                    "The actual change is confined to one internal helper "
                    "with targeted tests and no boundary impact."
                ),
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        dispatch_action = next(
            action
            for action in frontier["actions"]
            if action["action"] == "DISPATCH_LOOP"
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        execution_policy = context["executionPolicy"]
        completion_policy = context["completionPolicy"]

        self.assertEqual(execution_policy["assuranceProfile"], "LIGHT")
        self.assertEqual(
            execution_policy["reviewTopology"],
            "NO_INDEPENDENT_REVIEW_LOOPS",
        )
        self.assertEqual(
            execution_policy["progressReporting"]["reportAt"],
            ["ISSUE_FOUND", "FINAL_VERIFICATION"],
        )
        self.assertTrue(
            execution_policy["progressReporting"][
                "shortLoopMayReportOnlyFinal"
            ]
        )
        self.assertTrue(
            execution_policy["progressReporting"][
                "initialHeartbeatRequiredBeforeWork"
            ]
        )
        self.assertFalse(
            execution_policy["progressReporting"][
                "shortLoopMayCompleteWithoutExplicitHeartbeat"
            ]
        )
        self.assertEqual(
            execution_policy["contextIsolation"],
            "REQUIRED",
        )
        self.assertEqual(
            dispatch_action["executionPolicy"],
            execution_policy,
        )
        self.assertEqual(
            completion_policy["verificationScope"],
            "TARGETED_FOR_DECLARED_CHANGE",
        )
        self.assertEqual(
            completion_policy["reviewCycle"],
            "FOCUSED_REVIEW_RESOLVE_VERIFY_AND_REREVIEW_IF_NEEDED",
        )
        self.assertEqual(
            completion_policy["reviewFindings"]["p0p1"],
            "RESOLVE_AND_REREVIEW_BEFORE_SUCCEEDED",
        )
