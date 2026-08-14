from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Lock,
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    Thread,
    WORK_ITEM_DIRECTORY,
    archive_delivery,
    at,
    call_tool,
    cancel_graph_run,
    create_manual_handoff,
    datetime,
    deepcopy,
    delivery_task_hierarchy,
    dispatch_loop,
    freeze_hierarchy,
    get_graph_frontier,
    graph_events,
    graph_status,
    interface_hierarchy,
    json,
    loop_node_id,
    patch,
    prepare_hierarchy,
    preview_hierarchy,
    record_loop_result,
    record_user_confirmation,
    review_node_id,
    review_success,
    sqlite3,
    success,
    task_hierarchy,
    timedelta,
    timezone,
    workspace_status,
)


class SchedulerRuntimeTestsPart3:
    def test_latest_manual_revision_can_enter_automatic_execution(self) -> None:
        hierarchy = delivery_task_hierarchy("d-manual-rev", "t-manual-rev")
        first_preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                first_preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=(
                first_preview["graphFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        revised = deepcopy(hierarchy)
        revised["delivery"]["summary"] = "手动 Revision 2。"
        second_preview = preview_hierarchy(
            root=self.root,
            hierarchy=revised,
            now=at(2),
        )
        create_manual_handoff(
            root=self.root,
            hierarchy=revised,
            expected_hierarchy_fingerprint=(
                second_preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=(
                second_preview["graphFingerprint"]
            ),
            authorized_project_ids=[],
            expected_current_revision=1,
            continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
            revision_reason="修订手动需求。",
            confirmed=True,
            confirmed_by="human",
            now=at(3),
        )

        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=revised,
            now=at(4),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_delivery_revision=2,
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(5),
        )

        self.assertEqual(prepared["deliveryRevision"], 2)
        self.assertEqual(frozen["deliveryRevision"], 2)
        history = SchedulerRepository(self.root).revision_history(
            prepared["rootId"]
        )
        self.assertEqual(
            [item["status"] for item in history["revisions"]],
            ["SUPERSEDED", "FROZEN"],
        )
        self.assertFalse(Path(self.root, "worktrees").exists())

    def test_manual_progress_survives_controller_projection_refresh(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-manual", "t-manual")
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        delivery_root = Path(
            self.root,
            ".layered-delivery",
            "d-manual",
        )
        progress = Path(delivery_root, "progress.md")
        acceptance = Path(delivery_root, "acceptance.md")
        task_progress = Path(
            delivery_root,
            "work-items",
            "t-manual",
            "progress.md",
        )
        task_acceptance = task_progress.with_name("acceptance.md")
        baseline = Path(delivery_root, "baseline.md")
        progress.write_text("manual delivery progress", encoding="utf-8")
        acceptance.write_text(
            "manual delivery acceptance",
            encoding="utf-8",
        )
        task_progress.write_text("manual task progress", encoding="utf-8")
        task_acceptance.write_text(
            "manual task acceptance",
            encoding="utf-8",
        )
        baseline.write_text("tampered baseline", encoding="utf-8")

        SchedulerRepository(self.root).write_projections("d-manual")

        self.assertEqual(
            progress.read_text(encoding="utf-8"),
            "manual delivery progress",
        )
        self.assertEqual(
            acceptance.read_text(encoding="utf-8"),
            "manual delivery acceptance",
        )
        self.assertEqual(
            task_progress.read_text(encoding="utf-8"),
            "manual task progress",
        )
        self.assertEqual(
            task_acceptance.read_text(encoding="utf-8"),
            "manual task acceptance",
        )
        self.assertNotEqual(
            baseline.read_text(encoding="utf-8"),
            "tampered baseline",
        )

    def test_manual_handoff_shares_directory_with_later_projections(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-shared", "t-shared")
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        handoff_path = Path(
            self.root,
            handoff["manualHandoff"]["path"],
        )
        manual_overview = handoff_path.with_name("overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "需求已冻结（手动开发，调度未启动）",
            manual_overview,
        )

        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(2),
        )

        self.assertEqual(prepared["rootId"], "d-shared")
        self.assertTrue(handoff_path.is_file())
        delivery_root = handoff_path.parent
        prepared_overview = Path(
            delivery_root,
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertIn("待冻结", prepared_overview)
        self.assertNotIn(
            "需求已冻结（手动开发，调度未启动）",
            prepared_overview,
        )
        for projection in (
            "overview.md",
            "baseline.md",
            "progress.md",
            "acceptance.md",
            "revisions.md",
            "work-items",
        ):
            with self.subTest(projection=projection):
                self.assertTrue(Path(delivery_root, projection).exists())
        self.assertFalse(
            Path(self.root, ".layered-delivery", "handoffs").exists()
        )

    def test_concurrent_manual_handoffs_share_database_and_root_overview(
        self,
    ) -> None:
        errors: list[BaseException] = []
        results: dict[str, dict[str, object]] = {}
        result_lock = Lock()

        def create_handoff(index: int) -> None:
            try:
                root_id = f"d-manual-{index}"
                hierarchy = delivery_task_hierarchy(
                    root_id,
                    f"t-manual-{index}",
                )
                preview = preview_hierarchy(
                    root=self.root,
                    hierarchy=hierarchy,
                    now=at(index),
                )
                handoff = create_manual_handoff(
                    root=self.root,
                    hierarchy=hierarchy,
                    expected_hierarchy_fingerprint=(
                        preview["hierarchyFingerprint"]
                    ),
                    expected_graph_fingerprint=(
                        preview["graphFingerprint"]
                    ),
                    authorized_project_ids=[],
                    confirmed=True,
                    confirmed_by="human",
                    now=at(index + 10),
                )
                with result_lock:
                    results[root_id] = handoff
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [
            Thread(target=create_handoff, args=(index,))
            for index in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)
        control_root = Path(self.root, ".layered-delivery")
        self.assertTrue(Path(control_root, "scheduler.db").is_file())
        workspace_overview = Path(
            control_root,
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertIn("未归档交付数量：4", workspace_overview)
        for index in range(4):
            root_id = f"d-manual-{index}"
            with self.subTest(root_id=root_id):
                self.assertIn(root_id, workspace_overview)
                self.assertTrue(results[root_id]["controlStateCreated"])
                stored = SchedulerRepository(self.root).hierarchy(root_id)
                self.assertEqual(stored["status"], "HANDOFF_READY")

    def test_manual_and_automatic_delivery_trees_share_structure(
        self,
    ) -> None:
        hierarchy = interface_hierarchy()
        root_id = hierarchy["delivery"]["id"]
        with TemporaryDirectory() as automatic_root:
            prepare_hierarchy(
                root=automatic_root,
                hierarchy=hierarchy,
                now=at(0),
            )
            automatic_delivery = Path(
                automatic_root,
                ".layered-delivery",
                root_id,
            )
            automatic_files = {
                path.relative_to(automatic_delivery).as_posix()
                for path in automatic_delivery.rglob("*")
                if path.is_file()
            }

        with TemporaryDirectory() as manual_root:
            preview = preview_hierarchy(
                root=manual_root,
                hierarchy=hierarchy,
                now=at(0),
            )
            create_manual_handoff(
                root=manual_root,
                hierarchy=hierarchy,
                expected_hierarchy_fingerprint=(
                    preview["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=preview[
                    "graphFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )
            manual_delivery = Path(
                manual_root,
                ".layered-delivery",
                root_id,
            )
            manual_files = {
                path.relative_to(manual_delivery).as_posix()
                for path in manual_delivery.rglob("*")
                if path.is_file()
                and not path.name.startswith("handoff-")
            }
            handoff_files = list(
                manual_delivery.glob("handoff-*.md")
            )

        self.assertEqual(manual_files, automatic_files)
        self.assertEqual(len(handoff_files), 1)
        self.assertTrue(
            any(path.startswith("work-items/") for path in manual_files)
        )
        self.assertTrue(
            any("/interfaces/" in path for path in manual_files)
        )

    def test_manual_handoff_preserves_matching_graph_projections(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy(
            "d-existing",
            "t-existing",
        )
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(2),
        )

        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(3),
        )

        delivery_root = Path(
            self.root,
            ".layered-delivery",
            "d-existing",
        )
        overview = Path(delivery_root, "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("运行中", overview)
        self.assertNotIn(
            "需求已冻结（手动开发，调度未启动）",
            overview,
        )
        self.assertTrue(
            Path(self.root, handoff["manualHandoff"]["path"]).is_file()
        )
        self.assertEqual(
            workspace_status(root=self.root)["status"],
            "ACTIVE",
        )

    def test_task_and_review_are_uniform_loops_until_confirmation(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [loop_node_id("t-service")],
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            owner="agent-1",
            operation_id="op-task-1",
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            operation_id="op-task-1",
            outcome=success("Task Loop completed."),
            now=at(4),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            ["review:task:t-service"],
        )
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id="review:task:t-service",
            owner="task-reviewer-1",
            operation_id="op-task-review-1",
            now=at(6),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id="review:task:t-service",
            operation_id="op-task-review-1",
            outcome=review_success(
                "TASK_REVIEW_LOOP",
                "Task review completed.",
            ),
            now=at(7),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(8),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [review_node_id(root_id)],
        )
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
            owner="reviewer-1",
            operation_id="op-review-1",
            now=at(9),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
            operation_id="op-review-1",
            outcome=review_success(
                "DELIVERY_REVIEW_LOOP",
                "Independent review completed.",
            ),
            now=at(10),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(11),
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
        completed = record_user_confirmation(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            confirmed_by="human",
            summary="Accepted.",
            now=at(12),
        )
        self.assertEqual(completed["status"], "COMPLETED")
        terminal_before = graph_status(
            root=self.root,
            root_id=root_id,
        )
        terminal_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        terminal_after = graph_status(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(terminal_frontier["status"], "COMPLETED")
        self.assertEqual(terminal_frontier["actions"], [])
        self.assertEqual(
            terminal_after["updatedAt"],
            terminal_before["updatedAt"],
        )
        self.assertEqual(
            terminal_after["completedAt"],
            terminal_before["completedAt"],
        )
        completed_overview = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            (
                "| 任务 | t-service | Run t-service | 已成功 | "
                "Task review completed. | "
                "[查看](work-items/t-service/acceptance.md) |"
            ),
            completed_overview,
        )
        self.assertNotIn("agent-1", completed_overview)
        self.assertIn("| 已成功 | reviewer-1 | 1 |", completed_overview)
        self.assertIn(
            "#### Delivery Acceptance/Readiness 结论",
            completed_overview,
        )
        self.assertIn("READY\\_FOR\\_USER\\_CONFIRMATION", completed_overview)
        self.assertNotIn("deliveryReadiness", completed_overview)
        self.assertNotIn("taskAcceptance", completed_overview)
        self.assertNotIn("upstreamLoopResults", completed_overview)
        self.assertNotIn("SUCCEEDED", completed_overview)
        with sqlite3.connect(
            Path(self.root) / ".layered-delivery" / "scheduler.db"
        ) as connection:
            row = connection.execute(
                "SELECT outcome_json FROM node_runs "
                "WHERE run_id = ? AND node_id = ?",
                (terminal_after["runId"], review_node_id(root_id)),
            ).fetchone()
        self.assertIsNotNone(row)
        stored_delivery_result = json.loads(row[0])["result"]
        self.assertIn("deliveryReadiness", stored_delivery_result)
        self.assertNotIn("upstreamLoopResults", stored_delivery_result)
        self.assertNotIn("taskAcceptance", stored_delivery_result)
        self.assertNotIn("groupIntegration", stored_delivery_result)
        task_acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "t-service"
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        self.assertIn("| 已成功 | agent-1 | 1 |", task_acceptance)
        self.assertIn(
            "| 已成功 | task-reviewer-1 | 1 |",
            task_acceptance,
        )
        workspace_overview = (
            Path(self.root)
            / ".layered-delivery"
            / "overview.md"
        ).read_text(encoding="utf-8")
        self.assertIn("| 已完成 |", workspace_overview)
        self.assertNotIn("TASK 进度", workspace_overview)
        self.assertNotIn("GROUP 数量", workspace_overview)
        self.assertNotIn("COMPLETED", workspace_overview)

        event_types = [
            item["eventType"]
            for item in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
        ]
        self.assertIn("LOOP_SUCCEEDED", event_types)
        self.assertIn("USER_CONFIRMED", event_types)
        self.assertNotIn("TASK_IMPLEMENTED", event_types)
        self.assertNotIn("GATE_FAILED", event_types)

    def test_completed_delivery_can_be_archived_idempotently(self) -> None:
        completed = self.complete_task_delivery()
        root_id = completed["rootId"]
        events_before = graph_events(
            root=self.root,
            root_id=root_id,
        )["events"]

        with patch(
            "hdg.controller.verify_runtime_delivery_project_scopes",
            side_effect=AssertionError(
                "Archival must not revalidate a completed Git workspace"
            ),
        ):
            archived = call_tool(
                "archive_delivery",
                {"root_id": root_id},
                root=self.root,
                workspace_root=self.root,
            )

        self.assertEqual(
            archived,
            {
                "rootId": root_id,
                "status": "ARCHIVED",
                "runStatus": "COMPLETED",
                "archivedAt": archived["archivedAt"],
                "alreadyArchived": False,
            },
        )
        self.assertEqual(
            workspace_status(root=self.root)["status"],
            "ABSENT",
        )
        explicit = workspace_status(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(explicit["status"], "ARCHIVED")
        self.assertEqual(explicit["runStatus"], "COMPLETED")
        self.assertEqual(explicit["archivedAt"], archived["archivedAt"])

        history = SchedulerRepository(self.root).revision_history(root_id)
        self.assertEqual(history["revisions"][-1]["status"], "ARCHIVED")
        self.assertEqual(
            history["revisions"][-1]["runStatus"],
            "COMPLETED",
        )
        detail_overview = Path(
            self.root,
            ".layered-delivery",
            root_id,
            "overview.md",
        )
        self.assertTrue(detail_overview.is_file())
        workspace_overview = Path(
            self.root,
            ".layered-delivery",
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertNotIn(root_id, workspace_overview)
        self.assertEqual(
            graph_events(root=self.root, root_id=root_id)["events"],
            events_before,
        )

        detail_overview.write_text("stale projection", encoding="utf-8")
        repeated = archive_delivery(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        self.assertTrue(repeated["alreadyArchived"])
        self.assertEqual(repeated["archivedAt"], archived["archivedAt"])
        repaired_overview = detail_overview.read_text(encoding="utf-8")
        self.assertIn("已归档", repaired_overview)
        archived_display = datetime.fromisoformat(
            archived["archivedAt"].replace("Z", "+00:00")
        ).astimezone(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.assertIn(archived_display, repaired_overview)

    def test_unfinished_delivery_cannot_be_archived(self) -> None:
        prepared = self.prepare_and_freeze(
            delivery_task_hierarchy("d-not-archive", "t-not-archive")
        )

        with self.assertRaises(GatedLoopError) as caught:
            archive_delivery(
                root=self.root,
                root_id=prepared["rootId"],
                now=at(2),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_NOT_COMPLETED",
        )
        repository = SchedulerRepository(self.root)
        self.assertEqual(
            repository.hierarchy(prepared["rootId"])["status"],
            "FROZEN",
        )
        self.assertEqual(repository.run(prepared["rootId"])["status"], "ACTIVE")

    def test_cancelled_delivery_cannot_be_archived(self) -> None:
        prepared = self.prepare_and_freeze(
            delivery_task_hierarchy("d-cancelled-archive", "t-cancelled")
        )
        cancel_graph_run(
            root=self.root,
            root_id=prepared["rootId"],
            cancelled_by="archive-user",
            reason="Cancelled before completion.",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError) as caught:
            archive_delivery(
                root=self.root,
                root_id=prepared["rootId"],
                now=at(3),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_NOT_COMPLETED",
        )
        self.assertEqual(
            SchedulerRepository(self.root).run(prepared["rootId"])["status"],
            "CANCELLED",
        )

    def test_pre_run_delivery_cannot_be_archived(self) -> None:
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=delivery_task_hierarchy(
                "d-prerun-archive",
                "t-prerun",
            ),
            now=at(0),
        )

        with self.assertRaises(GatedLoopError) as caught:
            archive_delivery(
                root=self.root,
                root_id=preview["rootId"],
                now=at(1),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_NOT_COMPLETED",
        )
        self.assertEqual(
            SchedulerRepository(self.root).hierarchy(preview["rootId"])[
                "status"
            ],
            "CHOICE_READY",
        )

    def test_archived_delivery_cannot_be_refrozen(self) -> None:
        completed = self.complete_task_delivery("d-archived-freeze")
        root_id = completed["rootId"]
        archive_delivery(root=self.root, root_id=root_id, now=at(9))
        stored = SchedulerRepository(self.root).hierarchy(root_id)

        with self.assertRaises(GatedLoopError) as caught:
            freeze_hierarchy(
                root=self.root,
                root_id=root_id,
                expected_hierarchy_fingerprint=stored[
                    "hierarchyFingerprint"
                ],
                confirmed=True,
                confirmed_by="archive-user",
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

    def test_archived_delivery_cannot_become_a_manual_handoff(self) -> None:
        completed = self.complete_task_delivery("d-archived-manual")
        root_id = completed["rootId"]
        archive_delivery(root=self.root, root_id=root_id, now=at(9))
        stored = SchedulerRepository(self.root).hierarchy(root_id)

        with self.assertRaises(GatedLoopError) as caught:
            create_manual_handoff(
                root=self.root,
                hierarchy=stored["hierarchy"],
                expected_hierarchy_fingerprint=stored[
                    "hierarchyFingerprint"
                ],
                expected_graph_fingerprint=stored["graphFingerprint"],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="archive-user",
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

    def test_archived_delivery_cannot_be_cancelled(self) -> None:
        completed = self.complete_task_delivery("d-archived-cancel")
        root_id = completed["rootId"]
        archive_delivery(root=self.root, root_id=root_id, now=at(9))

        with self.assertRaises(GatedLoopError) as caught:
            cancel_graph_run(
                root=self.root,
                root_id=root_id,
                cancelled_by="archive-user",
                reason="Must remain archived.",
                now=at(10),
            )

        self.assertEqual(caught.exception.code, "SCHEDULER_RUN_TERMINAL")
        self.assertEqual(
            SchedulerRepository(self.root).hierarchy(root_id)["status"],
            "ARCHIVED",
        )
