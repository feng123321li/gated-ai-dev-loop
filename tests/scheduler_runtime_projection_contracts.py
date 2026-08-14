from __future__ import annotations

from .scheduler_runtime_support import (
    Event,
    GatedLoopError,
    Lock,
    PROJECTION_TEMPLATES,
    PROJECTION_TEMPLATE_VERSION,
    Path,
    SchedulerRepository,
    Thread,
    WORK_ITEM_DIRECTORY,
    at,
    contextmanager,
    current_thread,
    datetime,
    deepcopy,
    delivery_task_hierarchy,
    disjoint_parallel_hierarchy,
    dispatch_loop,
    freeze_hierarchy,
    get_graph_frontier,
    interface_hierarchy,
    json,
    loop_node_id,
    node,
    patch,
    prepare_hierarchy,
    shutil,
    sqlite3,
    task_hierarchy,
    timedelta,
    timezone,
    workspace_status,
)


class SchedulerRuntimeTestsPart9:
    def test_dubbo_interface_projection_matches_torna_contract_sections(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["interfaces"] = [
            {
                "protocol": "DUBBO",
                "name": "获取核酸孔位列表",
                "summary": "获取核酸孔位列表。",
                "changeType": "CREATE",
                "before": None,
                "after": {
                    "service": (
                        "com.majorbio.service.erp.scs.api.api.box."
                        "BoxDubboService"
                    ),
                    "method": "getBoxNucleicPosition",
                    "request": {
                        "name": "boxNucleicPositionReqVO",
                        "type": "BoxNucleicPositionReqVO",
                        "required": True,
                        "fields": [
                            {
                                "name": "systemSource",
                                "type": "Integer",
                                "required": False,
                                "maxLength": 16,
                                "description": "系统来源。",
                                "example": "SCS",
                            }
                        ],
                    },
                    "response": {
                        "type": "BoxNucleicPositionRespVO",
                        "fields": [
                            {
                                "name": "boList",
                                "type": "List<BoxNucleicPositionBO>",
                                "required": False,
                                "description": "核酸孔位列表。",
                                "fields": [
                                    {
                                        "name": "positionCode",
                                        "type": "String",
                                        "required": True,
                                        "maxLength": 32,
                                        "description": "孔位编码。",
                                        "example": "A01",
                                    }
                                ],
                            }
                        ],
                    },
                },
            }
        ]

        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        detail = next(
            (
                Path(self.root)
                / ".layered-delivery"
                / prepared["rootId"]
                / WORK_ITEM_DIRECTORY
                / "t-service"
                / "interfaces"
            ).glob("*.md")
        ).read_text(encoding="utf-8")

        self.assertIn(
            (
                "- 接口：com.majorbio.service.erp.scs.api.api.box."
                "BoxDubboService"
            ),
            detail,
        )
        self.assertIn(
            (
                "- 方法：BoxNucleicPositionRespVO "
                "getBoxNucleicPosition("
                "BoxNucleicPositionReqVO boxNucleicPositionReqVO)"
            ),
            detail,
        )
        self.assertIn("### 调用参数", detail)
        self.assertIn(
            (
                "| 字段路径 | 变更 | 类型（修改前 → 修改后） | "
                "必填（修改前 → 修改后） | 最大长度（修改前 → 修改后） | "
                "说明（修改前 → 修改后） | 示例值（修改前 → 修改后） |"
            ),
            detail,
        )
        self.assertIn(
            (
                "| boxNucleicPositionReqVO | 新增 | "
                "BoxNucleicPositionReqVO | 是 | — | 未声明 | — |"
            ),
            detail,
        )
        self.assertIn(
            (
                "| boxNucleicPositionReqVO.systemSource | 新增 | Integer | "
                "否 | 16 | 系统来源。 | SCS |"
            ),
            detail,
        )
        self.assertIn(
            (
                "| boList.positionCode | 新增 | String | 是 | 32 | "
                "孔位编码。 | A01 |"
            ),
            detail,
        )
        self.assertIn("### 返回结果", detail)
        self.assertIn(
            (
                "| boList | 新增 | List&lt;BoxNucleicPositionBO&gt; | "
                "否 | — | 核酸孔位列表。 | — |"
            ),
            detail,
        )

    def test_workspace_status_backfills_projection_files_for_stored_deliveries(
        self,
    ) -> None:
        first = prepare_hierarchy(
            root=self.root,
            hierarchy=interface_hierarchy(),
            now=at(0),
        )
        second_hierarchy = task_hierarchy()
        second_hierarchy["delivery"].update(
            {
                "id": "d-older-projection",
                "title": "另一个历史交付",
                "summary": "验证全部已有 Delivery 都会补建投影。",
            }
        )
        second = prepare_hierarchy(
            root=self.root,
            hierarchy=second_hierarchy,
            now=at(1),
        )
        projection_roots = [
            Path(self.root) / ".layered-delivery" / prepared["rootId"]
            for prepared in (first, second)
        ]
        for projection_root in projection_roots:
            for filename in (
                "baseline.md",
                "progress.md",
                "acceptance.md",
            ):
                path = projection_root / filename
                if path.exists():
                    path.unlink()
            work_items = projection_root / WORK_ITEM_DIRECTORY
            if work_items.exists():
                shutil.rmtree(work_items)
            (projection_root / "overview.md").write_text(
                "# 旧版总览\n",
                encoding="utf-8",
            )

        status = workspace_status(root=self.root)

        self.assertEqual(
            status["status"],
            "DELIVERY_SELECTION_REQUIRED",
        )
        self.assertNotIn("rootId", status)
        self.assertEqual(
            {
                item["rootId"]
                for item in status["candidateDeliveries"]
            },
            {first["rootId"], second["rootId"]},
        )
        for prepared in (first, second):
            selected = workspace_status(
                root=self.root,
                root_id=prepared["rootId"],
            )
            self.assertEqual(selected["rootId"], prepared["rootId"])
        for index, projection_root in enumerate(projection_roots):
            for filename in (
                "overview.md",
                "baseline.md",
                "progress.md",
                "acceptance.md",
            ):
                with self.subTest(
                    root_id=projection_root.name,
                    filename=filename,
                ):
                    content = (projection_root / filename).read_text(
                        encoding="utf-8"
                    )
                    self.assertNotIn("投影模板版本", content)
            item_root = (
                projection_root / WORK_ITEM_DIRECTORY / "t-service"
            )
            for filename in (
                "baseline.md",
                "progress.md",
                "acceptance.md",
            ):
                with self.subTest(
                    root_id=projection_root.name,
                    work_item_file=filename,
                ):
                    content = (item_root / filename).read_text(
                        encoding="utf-8"
                    )
                    self.assertNotIn("投影模板版本", content)
            interface_projection = item_root / "interfaces.md"
            if index == 0:
                self.assertNotIn(
                    "投影模板版本",
                    interface_projection.read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    len(list((item_root / "interfaces").glob("*.md"))),
                    3,
                )
            else:
                self.assertFalse(interface_projection.exists())
                self.assertFalse((item_root / "interfaces").exists())

    def test_invalid_delivery_does_not_block_another_delivery_frontier(
        self,
    ) -> None:
        healthy_workspace = Path(self.root, "healthy-workspace")
        healthy_workspace.mkdir()
        damaged = prepare_hierarchy(
            root=self.root,
            hierarchy=delivery_task_hierarchy(
                "d-damaged",
                "t-damaged",
            ),
            now=at(0),
        )
        healthy = prepare_hierarchy(
            root=self.root,
            hierarchy=delivery_task_hierarchy(
                "d-healthy",
                "t-healthy",
            ),
            workspace_root=str(healthy_workspace),
            now=at(1),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=healthy["rootId"],
            workspace_root=str(healthy_workspace),
            expected_hierarchy_fingerprint=(
                healthy["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(2),
        )
        database = Path(
            self.root,
            ".layered-delivery",
            "scheduler.db",
        )
        connection = sqlite3.connect(database)
        try:
            row = connection.execute(
                "SELECT graph_json FROM hierarchies WHERE root_id = ?",
                (damaged["rootId"],),
            ).fetchone()
            graph = json.loads(row[0])
            graph["runtime"]["retryPolicy"]["maxAttempts"] = 99
            connection.execute(
                "UPDATE hierarchies SET graph_json = ? "
                "WHERE root_id = ?",
                (
                    json.dumps(graph, separators=(",", ":")),
                    damaged["rootId"],
                ),
            )
            connection.commit()
        finally:
            connection.close()

        frontier = get_graph_frontier(
            root=self.root,
            root_id=healthy["rootId"],
            now=at(3),
        )
        status = workspace_status(
            root=self.root,
            root_id=healthy["rootId"],
            workspace_root=str(healthy_workspace),
        )

        self.assertEqual(frontier["rootId"], healthy["rootId"])
        self.assertEqual(
            status["projectionIssues"],
            [
                {
                    "rootId": damaged["rootId"],
                    "code": "SCHEDULER_STATE_INVALID",
                    "message": "Stored scheduler graph changed",
                }
            ],
        )
        workspace_overview = Path(
            self.root,
            ".layered-delivery",
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertIn(damaged["rootId"], workspace_overview)
        self.assertIn("调度状态异常", workspace_overview)
        self.assertIn("SCHEDULER\\_STATE\\_INVALID", workspace_overview)

        with self.assertRaises(GatedLoopError) as caught:
            SchedulerRepository(self.root).hierarchy(damaged["rootId"])
        self.assertEqual(
            caught.exception.details["rootId"],
            damaged["rootId"],
        )

    def test_foreign_projection_damage_does_not_block_workspace_status(
        self,
    ) -> None:
        foreign = prepare_hierarchy(
            root=self.root,
            hierarchy=delivery_task_hierarchy(
                "d-foreign-files",
                "t-foreign-files",
            ),
            now=at(0),
        )
        current = prepare_hierarchy(
            root=self.root,
            hierarchy=delivery_task_hierarchy(
                "d-current-files",
                "t-current-files",
            ),
            now=at(1),
        )
        foreign_baseline = Path(
            self.root,
            ".layered-delivery",
            foreign["rootId"],
            "baseline.md",
        )
        foreign_baseline.unlink()
        foreign_baseline.mkdir()

        status = workspace_status(
            root=self.root,
            root_id=current["rootId"],
        )

        self.assertEqual(status["rootId"], current["rootId"])
        self.assertEqual(
            status["projectionIssues"],
            [
                {
                    "rootId": foreign["rootId"],
                    "code": "SCHEDULER_PROJECTION_REFRESH_FAILED",
                    "message": (
                        "Controller could not refresh this Delivery "
                        "projection"
                    ),
                }
            ],
        )

    def test_reprepare_without_interfaces_removes_optional_projection(
        self,
    ) -> None:
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=interface_hierarchy(),
            now=at(0),
        )
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        task_root = (
            projection_root / WORK_ITEM_DIRECTORY / "t-service"
        )
        self.assertTrue((task_root / "interfaces.md").is_file())
        self.assertTrue((task_root / "interfaces").is_dir())

        replacement = prepare_hierarchy(
            root=self.root,
            hierarchy=task_hierarchy(),
            now=at(1),
        )

        self.assertNotIn(
            "interfaces",
            replacement["humanArtifacts"]["workItems"]["t-service"],
        )
        self.assertFalse((task_root / "interfaces.md").exists())
        self.assertFalse((task_root / "interfaces").exists())
        overview = (projection_root / "overview.md").read_text(
            encoding="utf-8"
        )
        baseline = (projection_root / "baseline.md").read_text(
            encoding="utf-8"
        )
        task_baseline = (task_root / "baseline.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("[接口契约](interfaces.md)", overview)
        self.assertNotIn(
            f"{WORK_ITEM_DIRECTORY}/t-service/interfaces.md",
            baseline,
        )
        self.assertNotIn("## 关联接口契约", task_baseline)

    def test_projection_set_is_fixed_and_rebuilt_from_sqlite(
        self,
    ) -> None:
        self.assertEqual(
            set(PROJECTION_TEMPLATES),
            {
                "overview.md",
                "baseline.md",
                "progress.md",
                "acceptance.md",
                "revisions.md",
            },
        )
        self.assertGreaterEqual(PROJECTION_TEMPLATE_VERSION, 7)
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=interface_hierarchy(),
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
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        workspace_overview_path = (
            Path(self.root) / ".layered-delivery" / "overview.md"
        )
        original_workspace_overview = (
            workspace_overview_path.read_bytes()
        )
        filenames = set(PROJECTION_TEMPLATES)
        original = {
            filename: (projection_root / filename).read_bytes()
            for filename in filenames
        }
        work_item_root = projection_root / WORK_ITEM_DIRECTORY
        original_work_items = {
            path.relative_to(work_item_root).as_posix(): path.read_bytes()
            for path in work_item_root.rglob("*")
            if path.is_file()
        }
        for filename in filenames:
            (projection_root / filename).write_text(
                f"agent-authored replacement: {filename}\n",
                encoding="utf-8",
            )
        for filename in original_work_items:
            (work_item_root / filename).write_text(
                f"agent-authored replacement: {filename}\n",
                encoding="utf-8",
            )
        (work_item_root / "stale-agent-file.md").write_text(
            "not controller data\n",
            encoding="utf-8",
        )
        workspace_overview_path.write_text(
            "agent-authored workspace summary\n",
            encoding="utf-8",
        )
        for filename in (
            "hierarchy.json",
            "graph.json",
            "state.json",
        ):
            (projection_root / filename).write_text(
                "legacy machine projection\n",
                encoding="utf-8",
            )

        repository = SchedulerRepository(self.root)
        repository.write_projections(prepared["rootId"])

        rebuilt = {
            filename: (projection_root / filename).read_bytes()
            for filename in filenames
        }
        rebuilt_work_items = {
            path.relative_to(work_item_root).as_posix(): path.read_bytes()
            for path in work_item_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(rebuilt, original)
        self.assertEqual(
            workspace_overview_path.read_bytes(),
            original_workspace_overview,
        )
        self.assertEqual(rebuilt_work_items, original_work_items)
        self.assertNotIn(
            "stale-agent-file.md",
            rebuilt_work_items,
        )
        for filename in (
            "hierarchy.json",
            "graph.json",
            "state.json",
        ):
            self.assertFalse((projection_root / filename).exists())
        shutil.rmtree(work_item_root)
        work_item_root.write_text(
            "agent replaced the controller directory\n",
            encoding="utf-8",
        )
        repository.write_projections(prepared["rootId"])
        self.assertTrue(work_item_root.is_dir())
        self.assertEqual(
            {
                path.relative_to(work_item_root).as_posix(): path.read_bytes()
                for path in work_item_root.rglob("*")
                if path.is_file()
            },
            original_work_items,
        )
        self.assertNotIn(
            "投影模板版本",
            rebuilt["overview.md"].decode("utf-8"),
        )

    def test_reprepare_replaces_the_exact_work_item_projection_set(
        self,
    ) -> None:
        original_hierarchy = task_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=original_hierarchy,
            now=at(0),
        )
        work_item_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / WORK_ITEM_DIRECTORY
        )
        self.assertTrue(
            (work_item_root / "t-service" / "baseline.md").is_file()
        )

        replacement = task_hierarchy()
        replacement["root"]["definition"]["id"] = "t-replacement"
        replacement["root"]["definition"]["title"] = "Replacement task"
        replacement["root"]["definition"]["summary"] = (
            "Execute the replacement Task Loop."
        )
        updated = prepare_hierarchy(
            root=self.root,
            hierarchy=replacement,
            now=at(1),
        )

        self.assertEqual(updated["rootId"], prepared["rootId"])
        self.assertFalse((work_item_root / "t-service").exists())
        replacement_baseline = (
            work_item_root / "t-replacement" / "baseline.md"
        )
        self.assertTrue(replacement_baseline.is_file())
        self.assertTrue(
            (work_item_root / "t-replacement" / "progress.md").is_file()
        )
        self.assertTrue(
            (
                work_item_root
                / "t-replacement"
                / "acceptance.md"
            ).is_file()
        )
        self.assertIn(
            "Execute the replacement Task Loop.",
            replacement_baseline.read_text(encoding="utf-8"),
        )

    def test_concurrent_disjoint_dispatch_projection_does_not_regress(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        earlier_waiting = Event()
        release_earlier = Event()
        later_finished = Event()
        clock_lock = Lock()
        errors: list[BaseException] = []
        expected_machine_time = (
            at(3).isoformat().replace("+00:00", "Z")
        )
        clock_values = iter(
            [
                at(2).isoformat().replace("+00:00", "Z"),
                expected_machine_time,
            ]
        )
        original_transaction = SchedulerRepository.transaction

        def ordered_timestamp(now: object = None) -> str:
            del now
            with clock_lock:
                return next(clock_values)

        @contextmanager
        def coordinated_transaction(
            repository: SchedulerRepository,
        ):
            if current_thread().name == "earlier-dispatch":
                earlier_waiting.set()
                if not release_earlier.wait(timeout=5):
                    raise AssertionError(
                        "Timed out releasing the earlier dispatch"
                    )
            with original_transaction(repository) as connection:
                yield connection

        def claim(
            *,
            item_id: str,
            operation_id: str,
            finished: Event | None = None,
        ) -> None:
            try:
                dispatch_loop(
                    root=self.root,
                    root_id=root_id,
                    node_id=loop_node_id(item_id),
                    owner=current_thread().name,
                    operation_id=operation_id,
                )
            except BaseException as error:
                errors.append(error)
            finally:
                if finished is not None:
                    finished.set()

        with (
            patch(
                "hdg.graph_runtime_common.timestamp",
                new=ordered_timestamp,
            ),
            patch.object(
                SchedulerRepository,
                "transaction",
                new=coordinated_transaction,
            ),
        ):
            earlier = Thread(
                target=claim,
                kwargs={
                    "item_id": "t-api",
                    "operation_id": "op-concurrent-earlier",
                },
                name="earlier-dispatch",
            )
            later = Thread(
                target=claim,
                kwargs={
                    "item_id": "t-core",
                    "operation_id": "op-concurrent-later",
                    "finished": later_finished,
                },
                name="later-dispatch",
            )
            earlier.start()
            self.assertTrue(earlier_waiting.wait(timeout=5))
            later.start()
            try:
                self.assertTrue(later_finished.wait(timeout=5))
            finally:
                release_earlier.set()
            earlier.join(timeout=5)
            later.join(timeout=5)

        self.assertFalse(earlier.is_alive())
        self.assertFalse(later.is_alive())
        self.assertEqual(errors, [])

        run = SchedulerRepository(self.root).run(root_id)
        claimed_at = {
            node["nodeId"]: node["claimedAt"]
            for node in run["nodes"]
            if node["nodeId"]
            in {
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            }
        }
        self.assertEqual(
            set(claimed_at),
            {
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            },
        )
        self.assertTrue(all(claimed_at.values()))
        self.assertEqual(run["updatedAt"], expected_machine_time)
        run_updated = datetime.fromisoformat(
            run["updatedAt"].replace("Z", "+00:00")
        )
        claimed_times = [
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in claimed_at.values()
        ]
        self.assertGreaterEqual(run_updated, max(claimed_times))

        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
        )
        self.assertFalse((projection_root / "state.json").exists())
        human_time = at(3).astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M:%S")
        overview = (projection_root / "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(human_time, overview)
        self.assertNotIn("T08:03:00+08:00", overview)

    def test_delivery_ids_retain_separate_requirement_projections(
        self,
    ) -> None:
        first = task_hierarchy()
        second = deepcopy(first)
        second["delivery"].update(
            {
                "id": "d-secondary",
                "title": "第二个交付需求",
                "summary": "保留独立的需求投影目录。",
            }
        )

        first_prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=first,
            now=at(0),
        )
        second_prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=second,
            now=at(1),
        )

        control = Path(self.root) / ".layered-delivery"
        first_overview = (
            control / first_prepared["rootId"] / "overview.md"
        )
        second_overview = (
            control / second_prepared["rootId"] / "overview.md"
        )
        self.assertTrue(first_overview.is_file())
        self.assertTrue(second_overview.is_file())
        self.assertIn(
            first_prepared["rootId"],
            first_overview.read_text(encoding="utf-8"),
        )
        self.assertIn(
            second_prepared["rootId"],
            second_overview.read_text(encoding="utf-8"),
        )
        workspace_overview = (
            control / "overview.md"
        ).read_text(encoding="utf-8")
        self.assertIn("未归档交付数量：2", workspace_overview)
        self.assertIn(first_prepared["rootId"], workspace_overview)
        self.assertIn(second_prepared["rootId"], workspace_overview)
        self.assertIn(
            f"({first_prepared['rootId']}/overview.md)",
            workspace_overview,
        )
        self.assertIn(
            f"({second_prepared['rootId']}/overview.md)",
            workspace_overview,
        )
