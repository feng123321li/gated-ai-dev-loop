from __future__ import annotations

from .scheduler_runtime_support import (
    Path,
    WORK_ITEM_DIRECTORY,
    at,
    auditable_recursive_hierarchy,
    database_hierarchy,
    hierarchical_work_item_paths,
    hierarchy_nodes,
    interface_hierarchy,
    prepare_hierarchy,
    skill_hint,
    task_hierarchy,
)


class SchedulerRuntimeTestsPart8:
    def test_prepare_projection_is_namespaced_and_auditable(
        self,
    ) -> None:
        hierarchy = auditable_recursive_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint("springboot-tdd", "Prefer TDD when applicable.")
        ]
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        control = Path(self.root) / ".layered-delivery"
        projections = control / prepared["rootId"]
        artifact_prefix = f".layered-delivery/{prepared['rootId']}"
        nodes = hierarchy_nodes(hierarchy)
        task_nodes = [
            current
            for current in nodes
            if current["definition"]["kind"] == "TASK"
        ]
        item_paths = hierarchical_work_item_paths(hierarchy)
        expected_work_items = {}
        for current in nodes:
            definition = current["definition"]
            item_prefix = f"{artifact_prefix}/{item_paths[definition['id']]}"
            expected_work_items[definition["id"]] = {
                "kind": definition["kind"],
                "baseline": f"{item_prefix}/baseline.md",
                "progress": f"{item_prefix}/progress.md",
                "acceptance": f"{item_prefix}/acceptance.md",
            }
        expected_task_baselines = {
            current["definition"]["id"]: expected_work_items[
                current["definition"]["id"]
            ]["baseline"]
            for current in task_nodes
        }

        self.assertEqual(
            prepared["humanArtifacts"],
            {
                "workspaceOverview": ".layered-delivery/overview.md",
                "overview": f"{artifact_prefix}/overview.md",
                "baseline": f"{artifact_prefix}/baseline.md",
                "progress": f"{artifact_prefix}/progress.md",
                "acceptance": f"{artifact_prefix}/acceptance.md",
                "revisions": f"{artifact_prefix}/revisions.md",
                "taskBaselines": expected_task_baselines,
                "workItems": expected_work_items,
            },
        )
        self.assertTrue((control / "scheduler.db").is_file())
        self.assertTrue((control / "overview.md").is_file())
        for filename in (
            "overview.md",
            "baseline.md",
            "progress.md",
            "acceptance.md",
        ):
            self.assertTrue((projections / filename).is_file())
        self.assertFalse((projections / "interfaces.md").exists())
        for filename in (
            "hierarchy.json",
            "graph.json",
            "state.json",
        ):
            self.assertFalse((control / filename).exists())
            self.assertFalse((projections / filename).exists())
        overview = (projections / "overview.md").read_text(
            encoding="utf-8"
        )
        delivery_baseline = (projections / "baseline.md").read_text(
            encoding="utf-8"
        )
        acceptance = (projections / "acceptance.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("hierarchyFingerprint", overview)
        self.assertNotIn(prepared["hierarchyFingerprint"], overview)
        self.assertIn(
            prepared["hierarchyFingerprint"],
            delivery_baseline,
        )
        self.assertNotIn("graphFingerprint", overview)
        self.assertNotIn(prepared["graphFingerprint"], overview)
        self.assertIn(prepared["graphFingerprint"], delivery_baseline)
        self.assertIn(
            (
                "| 交付标识 | 标题 | 当前状态 | TASK 进度 | "
                "GROUP 数量 | 最近更新（UTC+8） |"
            ),
            overview,
        )
        self.assertIn(
            (
                f"| {prepared['rootId']} | {hierarchy['delivery']['title']} "
                "| 待冻结 | 已完成 0/6 | 4 |"
            ),
            overview,
        )
        self.assertIn("[需求基线](baseline.md)", overview)
        self.assertIn("[执行进展](progress.md)", overview)
        self.assertIn("[验收记录](acceptance.md)", overview)
        self.assertNotIn("[接口契约](interfaces.md)", overview)
        self.assertNotIn(
            "[查看接口契约](interfaces.md)",
            delivery_baseline,
        )
        self.assertIn("## GROUP/TASK 清单", delivery_baseline)
        self.assertIn(
            "| 层级路径 | 节点类型 | 上级 | 前置依赖 | "
            "标题 | 需求基线 | 执行进展 | 验收记录 | 接口契约 |",
            delivery_baseline,
        )
        self.assertIn("| 分组 |", delivery_baseline)
        self.assertIn("| 任务 |", delivery_baseline)
        self.assertIn("springboot-tdd", delivery_baseline)
        self.assertNotIn(hierarchy["delivery"]["summary"], overview)
        self.assertIn(
            hierarchy["delivery"]["summary"],
            delivery_baseline,
        )
        self.assertNotIn("```json", overview)
        self.assertNotIn("（PREPARED）", overview)

        workspace_overview = (control / "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# 未归档交付调度与进度总览", workspace_overview)
        self.assertIn("未归档交付数量：1", workspace_overview)
        self.assertIn(
            (
                "| 交付标识 | 需求标题 | 当前状态 | "
                "最近更新（UTC+8） | 交付详情 |"
            ),
            workspace_overview,
        )
        self.assertNotIn("需求摘要", workspace_overview)
        self.assertNotIn("TASK 进度", workspace_overview)
        self.assertNotIn("GROUP 数量", workspace_overview)
        self.assertNotIn(hierarchy["delivery"]["summary"], workspace_overview)
        self.assertIn(prepared["rootId"], workspace_overview)
        self.assertIn(
            f"({prepared['rootId']}/overview.md)",
            workspace_overview,
        )
        self.assertIn("待冻结", workspace_overview)
        self.assertNotIn("PREPARED", workspace_overview)

        work_item_root = projections / WORK_ITEM_DIRECTORY
        self.assertTrue(work_item_root.is_dir())
        self.assertEqual(
            {
                path.name
                for path in work_item_root.iterdir()
                if path.is_dir()
            },
            {hierarchy["root"]["definition"]["id"]},
        )
        root_item = work_item_root / hierarchy["root"]["definition"]["id"]
        self.assertEqual(
            {
                path.name
                for path in (root_item / "children").iterdir()
                if path.is_dir()
            },
            {
                child["definition"]["id"]
                for child in hierarchy["root"]["children"]
            },
        )
        self.assertTrue(
            (root_item / "children" / "g-backend").is_dir()
        )
        self.assertTrue(
            (root_item / "children" / "g-quality").is_dir()
        )
        for current in nodes:
            definition = current["definition"]
            item_id = definition["id"]
            item_root = projections / item_paths[item_id]
            with self.subTest(work_item_id=item_id):
                self.assertIn(item_id, delivery_baseline)
                self.assertEqual(
                    {
                        path.name
                        for path in item_root.iterdir()
                        if path.is_file()
                    },
                    {
                        "baseline.md",
                        "progress.md",
                        "acceptance.md",
                    },
                )
                self.assertIn(
                    f"{item_paths[item_id]}/baseline.md",
                    delivery_baseline,
                )
                item_progress = (item_root / "progress.md").read_text(
                    encoding="utf-8"
                )
                item_acceptance = (
                    item_root / "acceptance.md"
                ).read_text(encoding="utf-8")
                self.assertNotIn("投影模板版本", item_progress)
                self.assertIn("|", item_progress)
                self.assertIn("未启动", item_progress)
                self.assertNotIn("\n- 当前进度：", item_progress)
                self.assertNotIn("投影模板版本", item_acceptance)
                if definition["kind"] == "TASK":
                    loop = definition["execution"]["loop"]
                    baseline = (item_root / "baseline.md").read_text(
                        encoding="utf-8"
                    )
                    self.assertNotIn("投影模板版本", baseline)
                    self.assertIn(
                        prepared["hierarchyFingerprint"],
                        baseline,
                    )
                    self.assertIn(
                        prepared["graphFingerprint"],
                        baseline,
                    )
                    self.assertIn(definition["summary"], baseline)
                    for dependency in definition["execution"][
                        "dependsOn"
                    ]:
                        self.assertIn(dependency, baseline)
                    self.assertIn(loop["ref"], baseline)
                    self.assertIn(
                        loop["resourceClaims"][0],
                        baseline,
                    )
                    self.assertIn(
                        loop["payload"]["rawAuditMarker"],
                        baseline,
                    )
                    self.assertIn("### 验收标准", baseline)
                    self.assertIn("### 业务规则", baseline)
                    self.assertIn("：是", baseline)
                    self.assertIn(
                        r"首行<br>\# 不能改变模板 \| \`原样文本\`",
                        baseline,
                    )
                    self.assertNotIn("```json", baseline)
                    self.assertNotIn('"acceptance"', baseline)
                    self.assertNotIn('"rawAuditMarker"', baseline)
                    self.assertIn("springboot-tdd", baseline)
                    self.assertNotIn("## 关联接口契约", baseline)
                    self.assertNotIn(definition["summary"], delivery_baseline)
                    self.assertNotIn(loop["ref"], overview)
                    self.assertNotIn(
                        loop["payload"]["rawAuditMarker"],
                        overview,
                    )
                else:
                    group_baseline = (
                        item_root / "baseline.md"
                    ).read_text(encoding="utf-8")
                    self.assertIn(
                        definition["summary"],
                        group_baseline,
                    )
                    for dependency in definition["decomposition"][
                        "dependsOn"
                    ]:
                        self.assertIn(dependency, group_baseline)
                    review = current["reviewLoop"]
                    self.assertIn(review["ref"], group_baseline)
                    self.assertIn(
                        review["resourceClaims"][0],
                        group_baseline,
                    )
                    self.assertIn(
                        review["payload"]["rawAuditMarker"],
                        group_baseline,
                    )
                    self.assertIn("### 审查重点", group_baseline)
                    for child in current["children"]:
                        child_id = child["definition"]["id"]
                        self.assertIn(
                            f"children/{child_id}/baseline.md",
                            group_baseline,
                        )
                    self.assertNotIn(
                        review["payload"]["rawAuditMarker"],
                        delivery_baseline,
                    )

        delivery_review = hierarchy["delivery"]["reviewLoop"]
        self.assertIn(delivery_review["ref"], delivery_baseline)
        self.assertIn(
            delivery_review["resourceClaims"][0],
            delivery_baseline,
        )
        self.assertIn(
            delivery_review["payload"]["rawAuditMarker"],
            delivery_baseline,
        )
        self.assertIn("##### 原始审计标记", delivery_baseline)
        self.assertIn(
            delivery_review["payload"]["rawAuditMarker"],
            acceptance,
        )
        root_item_id = hierarchy["root"]["definition"]["id"]
        self.assertIn("## 根工作项验收", acceptance)
        self.assertIn(
            (
                f"[查看]({WORK_ITEM_DIRECTORY}/{root_item_id}/"
                "acceptance.md)"
            ),
            acceptance,
        )
        for current in nodes:
            definition = current["definition"]
            if definition["kind"] == "TASK":
                self.assertNotIn(
                    (
                        definition["execution"]["loop"]["payload"][
                            "acceptance"
                        ][0]
                    ),
                    acceptance,
                )
            else:
                self.assertNotIn(
                    current["reviewLoop"]["payload"][
                        "rawAuditMarker"
                    ],
                    acceptance,
                )
        self.assertNotIn('"rawAuditMarker"', delivery_baseline)

        work_item_ids = {
            current["definition"]["id"]
            for current in nodes
        }
        for item_id in work_item_ids:
            self.assertFalse((control / item_id).exists())
            self.assertFalse((projections / item_id).exists())
        self.assertFalse(
            any(
                path.name == "development-plan.md"
                for path in control.rglob("*")
            )
        )

    def test_delivery_human_projections_separate_baseline_progress_and_acceptance(
        self,
    ) -> None:
        hierarchy = interface_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )

        overview = (projection_root / "overview.md").read_text(
            encoding="utf-8"
        )
        baseline = (projection_root / "baseline.md").read_text(
            encoding="utf-8"
        )
        progress = (projection_root / "progress.md").read_text(
            encoding="utf-8"
        )
        acceptance = (projection_root / "acceptance.md").read_text(
            encoding="utf-8"
        )
        task_root = (
            projection_root
            / WORK_ITEM_DIRECTORY
            / "t-service"
        )
        interfaces = (task_root / "interfaces.md").read_text(
            encoding="utf-8"
        )
        interface_directory = task_root / "interfaces"
        interface_documents = sorted(interface_directory.glob("*.md"))
        self.assertEqual(
            [path.name for path in interface_documents],
            [
                "001-http-post-api-orders.md",
                (
                    "002-dubbo-com-example-order-orderservice-"
                    "createorder.md"
                ),
                (
                    "003-grpc-order-v1-legacyorderservice-"
                    "getorder.md"
                ),
            ],
        )
        interface_details = {
            path.name: path.read_text(encoding="utf-8")
            for path in interface_documents
        }
        all_interface_details = "\n".join(interface_details.values())
        create_order_detail = interface_details[
            "001-http-post-api-orders.md"
        ]
        task_baseline = (task_root / "baseline.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("[需求基线](baseline.md)", overview)
        self.assertIn("[执行进展](progress.md)", overview)
        self.assertIn("[验收记录](acceptance.md)", overview)
        self.assertNotIn("[接口契约](interfaces.md)", overview)
        self.assertEqual(
            prepared["humanArtifacts"]["workItems"]["t-service"][
                "interfaces"
            ],
            (
                f".layered-delivery/{prepared['rootId']}/"
                f"{WORK_ITEM_DIRECTORY}/t-service/interfaces.md"
            ),
        )
        self.assertNotIn("interfaces", prepared["humanArtifacts"])
        self.assertNotIn("## TASK 执行进度", overview)
        self.assertNotIn("## GROUP 协调与审查", overview)

        self.assertIn("# 交付需求基线", baseline)
        self.assertIn(hierarchy["delivery"]["summary"], baseline)
        self.assertIn(
            f"{WORK_ITEM_DIRECTORY}/t-service/baseline.md",
            baseline,
        )
        self.assertIn(
            f"{WORK_ITEM_DIRECTORY}/t-service/interfaces.md",
            baseline,
        )

        self.assertIn("# 交付执行进展", progress)
        self.assertIn("t-service", progress)
        self.assertIn(
            (
                "| 层级路径 | 阶段 | 当前进度 | 执行代理 | 认领身份 | 执行轮次 | "
                "最近更新时间（UTC+8） | 结果摘要 | 节点进展 |"
            ),
            progress,
        )
        self.assertIn("| t-service | TASK | 未启动 |", progress)
        self.assertNotIn("\n- 当前进度：", progress)
        self.assertNotIn("Deliver one observable result.", progress)

        self.assertIn("# 交付验收记录", acceptance)
        self.assertIn("## 职责边界", acceptance)
        self.assertIn(
            "Controller：Graph 门禁、结果契约校验与持久化",
            acceptance,
        )
        self.assertIn(
            "Delivery receiver：顶层技术验收与运行准备度判断",
            acceptance,
        )
        self.assertIn("用户：最终业务确认", acceptance)
        self.assertIn(
            (
                "| 当前进度 | 认领身份 | 执行轮次 | "
                "结束时间（UTC+8） | 结果摘要 |"
            ),
            acceptance,
        )
        self.assertIn("The loop returns verified evidence.", acceptance)
        self.assertIn("最终用户确认", acceptance)

        self.assertIn("# TASK 接口契约", interfaces)
        for label in (
            "来源 TASK",
            "协议",
            "接口名称",
            "变更类型",
            "修改前调用标识",
            "修改后调用标识",
            "简介",
        ):
            with self.subTest(chinese_label=label):
                self.assertIn(label, interfaces)
        self.assertNotIn("## 接口详情", interfaces)
        self.assertNotIn("legacyCustomerNo", interfaces)
        self.assertIn("创建订单", interfaces)
        self.assertIn("修改", interfaces)
        self.assertIn("POST /api/v1/orders", interfaces)
        self.assertIn("POST /api/orders", interfaces)
        self.assertIn(
            "[创建订单](interfaces/001-http-post-api-orders.md)",
            interfaces,
        )
        self.assertIn("legacyCustomerNo", all_interface_details)
        self.assertIn("customerId", all_interface_details)
        self.assertIn("必填", create_order_detail)
        self.assertIn("类型", create_order_detail)
        self.assertIn("说明", create_order_detail)
        self.assertIn("字段路径", create_order_detail)
        self.assertIn("[返回接口清单](../interfaces.md)", create_order_detail)
        self.assertIn(
            (
                "| ~~legacyCustomerNo~~ | 删除 | ~~string~~ | "
                "~~是~~ | ~~原客户编号~~ | ~~—~~ |"
            ),
            create_order_detail,
        )
        self.assertIn(
            (
                "| customerId | 新增 | string | "
                "是 | 客户标识 | — |"
            ),
            create_order_detail,
        )
        self.assertIn(
            (
                "| quantity | 修改 | integer | 否 → 是 | "
                "商品数量 → 必须大于零的商品数量 | — |"
            ),
            create_order_detail,
        )
        self.assertIn(
            (
                "| channel | 未变 | string | 否 | 下单渠道 | — |"
            ),
            create_order_detail,
        )
        response_section = create_order_detail.split("## 出参", 1)[1]
        self.assertNotIn("必填", response_section)
        self.assertIn(
            (
                "| ~~orderNo~~ | 删除 | ~~string~~ | "
                "~~原订单编号~~ | ~~—~~ |"
            ),
            response_section,
        )
        self.assertIn(
            "| orderId | 新增 | string | 订单标识 | — |",
            response_section,
        )
        self.assertNotIn("— →", all_interface_details)
        self.assertNotIn("→ —", all_interface_details)
        self.assertIn("orderId", all_interface_details)
        self.assertIn("创建订单服务", interfaces)
        self.assertIn("新增", interfaces)
        self.assertIn("不适用 →", all_interface_details)
        self.assertIn(
            "com.example.order.OrderService.createOrder",
            interfaces,
        )
        self.assertIn("CreateOrderRequest", all_interface_details)
        self.assertIn("CreateOrderResponse", all_interface_details)
        self.assertIn("旧版订单查询服务", interfaces)
        self.assertIn("GRPC", interfaces)
        self.assertIn("删除", interfaces)
        self.assertIn(
            (
                "~~[旧版订单查询服务](interfaces/"
                "003-grpc-order-v1-legacyorderservice-getorder.md)~~"
            ),
            interfaces,
        )
        self.assertIn(
            "order.v1.LegacyOrderService/GetOrder",
            interfaces,
        )
        self.assertIn("LegacyOrderResponse", all_interface_details)
        self.assertIn("→ 不适用", all_interface_details)
        self.assertNotIn("#### 修改前", all_interface_details)
        self.assertNotIn("#### 修改后", all_interface_details)
        self.assertIn("## 入参", all_interface_details)
        self.assertIn("## 出参", all_interface_details)
        self.assertNotIn("```json", all_interface_details)
        self.assertNotIn("PREPARED", interfaces)
        self.assertIn(
            "[查看本 TASK 的接口契约](interfaces.md)",
            task_baseline,
        )
        self.assertNotIn("创建订单", task_baseline)
        self.assertNotIn("legacyCustomerNo", task_baseline)

    def test_database_contract_projects_frozen_table_design_before_execution(
        self,
    ) -> None:
        hierarchy = database_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        task_root = projection_root / WORK_ITEM_DIRECTORY / "t-service"
        database_index = (task_root / "database-changes.md").read_text(
            encoding="utf-8"
        )
        details = sorted((task_root / "database-changes").glob("*.md"))
        self.assertEqual(
            [path.name for path in details],
            ["001-erp-service-erp-public-orders.md"],
        )
        detail = details[0].read_text(encoding="utf-8")
        task_baseline = (task_root / "baseline.md").read_text(
            encoding="utf-8"
        )
        delivery_baseline = (projection_root / "baseline.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("# TASK 数据库变更契约", database_index)
        self.assertIn("erp-service.erp.public.orders", database_index)
        self.assertIn("修改后字段数", database_index)
        self.assertIn("db-schema-orders", database_index)
        self.assertIn("# 数据库表：erp-service.erp.public.orders", detail)
        self.assertIn("## 字段级比较", detail)
        self.assertIn(r"| cancel\_reason | 新增 |", detail)
        self.assertIn("## 修改前完整结构", detail)
        self.assertIn("## 修改后完整结构（执行事实源）", detail)
        self.assertIn('"name": "cancel_reason"', detail)
        self.assertIn("先执行向后兼容迁移", detail)
        self.assertIn("必须返回 `REPLAN_REQUIRED`", database_index)
        self.assertIn(
            "[查看本 TASK 的数据库变更契约](database-changes.md)",
            task_baseline,
        )
        self.assertNotIn(r"cancel\_reason", task_baseline)
        self.assertIn(
            f"{WORK_ITEM_DIRECTORY}/t-service/database-changes.md",
            delivery_baseline,
        )
        self.assertEqual(
            prepared["humanArtifacts"]["workItems"]["t-service"][
                "databaseChanges"
            ],
            (
                f".layered-delivery/{prepared['rootId']}/"
                f"{WORK_ITEM_DIRECTORY}/t-service/database-changes.md"
            ),
        )

    def test_interface_projection_renders_actual_wrapped_http_contract(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["interfaces"] = [
            {
                "protocol": "HTTP",
                "name": "蛋白制备系统公共异常暂停数量",
                "summary": "无入参，只返回系统异常暂停数量。",
                "changeType": "CREATE",
                "before": None,
                "after": {
                    "method": "GET",
                    "path": "/taskReminder/preparation",
                    "request": {
                        "headers": [],
                        "pathParameters": [],
                        "queryParameters": [],
                        "body": None,
                        "businessParameters": [],
                        "contextDependencies": [],
                    },
                    "response": {
                        "controllerReturnType": (
                            "PreparationTaskMenuReminderRespVO"
                        ),
                        "controllerReturnFields": [
                            {
                                "name": "abnormalPausedCount",
                                "type": "Long",
                                "description": (
                                    "系统全部蛋白制备任务中的异常暂停数量，"
                                    "无数据时为 0。"
                                ),
                            }
                        ],
                        "wireType": (
                            "Rs<PreparationTaskMenuReminderRespVO>"
                        ),
                        "frameworkEnvelope": "Rs",
                        "wrapping": (
                            "Controller 直接返回 RespVO，由框架自动包装。"
                        ),
                    },
                },
            }
        ]

        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        detail = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / WORK_ITEM_DIRECTORY
            / "t-service"
            / "interfaces"
            / "001-http-get-taskreminder-preparation.md"
        ).read_text(encoding="utf-8")
        request_section, response_section = detail.split("## 出参", 1)

        self.assertIn("## 入参\n\n无", request_section)
        for metadata_key in (
            "headers",
            "pathParameters",
            "queryParameters",
            "body |",
            "businessParameters",
            "contextDependencies",
        ):
            with self.subTest(request_metadata=metadata_key):
                self.assertNotIn(metadata_key, request_section)
        self.assertIn(
            "- 返回类型：PreparationTaskMenuReminderRespVO",
            request_section,
        )
        self.assertNotIn("| （整体） |", response_section)
        self.assertIn("### 响应参数", response_section)
        self.assertIn(
            (
                "| abnormalPausedCount | 新增 | Long | "
                "系统全部蛋白制备任务中的异常暂停数量，无数据时为 0。 | — |"
            ),
            response_section,
        )
        self.assertNotIn("Rs&lt;", response_section)
        for metadata_key in (
            "controllerReturnType",
            "controllerReturnFields",
            "wireType",
            "frameworkEnvelope",
            "wrapping",
        ):
            with self.subTest(response_metadata=metadata_key):
                self.assertNotIn(metadata_key, response_section)

    def test_interface_projection_flattens_real_request_locations(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["interfaces"] = [
            {
                "protocol": "HTTP",
                "name": "查询菜单异常暂停数量",
                "summary": "按调用方实际提供的菜单编码查询。",
                "changeType": "CREATE",
                "before": None,
                "after": {
                    "method": "GET",
                    "path": "/taskReminder/{menuCode}",
                    "request": {
                        "pathParameters": [
                            {
                                "name": "menuCode",
                                "type": "String",
                                "required": True,
                                "description": "菜单编码。",
                            }
                        ],
                        "queryParameters": [
                            {
                                "name": "includeChildren",
                                "type": "Boolean",
                                "required": False,
                                "description": "是否包含子菜单。",
                            }
                        ],
                        "body": None,
                    },
                    "response": {
                        "type": "TaskMenuReminderRespVO",
                        "fields": [
                            {
                                "name": "abnormalPausedCount",
                                "type": "Long",
                                "description": "对应菜单的异常暂停数量。",
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
        request_section = detail.split("## 出参", 1)[0]

        self.assertIn(
            "### Path 参数",
            request_section,
        )
        self.assertIn(
            (
                "| menuCode | 新增 | String | 是 | 菜单编码。 | — |"
            ),
            request_section,
        )
        self.assertIn("### Query 参数", request_section)
        self.assertIn(
            (
                "| includeChildren | 新增 | Boolean | 否 | "
                "是否包含子菜单。 | — |"
            ),
            request_section,
        )
        self.assertNotIn("pathParameters", request_section)
        self.assertNotIn("queryParameters", request_section)
