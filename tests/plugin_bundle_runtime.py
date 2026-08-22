from __future__ import annotations

from .plugin_bundle_support import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    DASHBOARD_RESOURCE_URI,
    DISPATCH_SKILL,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MCP_APP_MIME_TYPE,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    PLANNING_TOOL_PROFILE,
    PLUGIN,
    PLUGIN_SKILL,
    PROTOCOL_VERSION_META_KEY,
    Path,
    ProjectRootBinding,
    REVIEW_SKILL,
    ROOT,
    SKILL,
    SKILL_RUNTIME,
    SOURCE,
    TASK_SKILL,
    TemporaryDirectory,
    _allowed_tools,
    group_hierarchy,
    handle_message,
    hdg,
    io,
    json,
    os,
    patch,
    preview_hierarchy,
    re,
    redirect_stderr,
    runpy,
    subprocess,
    sys,
    tool_definitions,
    tool_names_for_profile,
    unittest,
    validate_hierarchy_definition,
)


class PluginBundleTestsPart2:
    def test_plugin_skill_matches_canonical_skill(self) -> None:
        canonical = {
            path.relative_to(SKILL): path.read_bytes()
            for path in SKILL.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        plugin = {
            path.relative_to(PLUGIN_SKILL): path.read_bytes()
            for path in PLUGIN_SKILL.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        self.assertEqual(plugin, canonical)

    def test_host_manifests_match_runtime_version(self) -> None:
        for relative in (
            ".codex-plugin/plugin.json",
            ".claude-plugin/plugin.json",
            ".zcode-plugin/plugin.json",
        ):
            with self.subTest(relative=relative):
                manifest = json.loads(
                    (PLUGIN / relative).read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["version"], hdg.__version__)
                self.assertIn("GROUP", manifest["description"])
                self.assertIn("TASK", manifest["description"])
                self.assertIn("冻结开发包", manifest["description"])

    def test_explicit_user_choices_do_not_trigger_host_reapproval(
        self,
    ) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        server = manifest["mcpServers"]["delivery-graph"]
        self.assertEqual(server["env"]["HDG_HOST_ADAPTER"], "codex")
        claude_server = json.loads(
            (PLUGIN / ".mcp.json").read_text(encoding="utf-8")
        )["mcpServers"]["delivery-graph"]
        self.assertEqual(
            claude_server["env"]["HDG_HOST_ADAPTER"],
            "claude-code",
        )
        zcode_server = json.loads(
            (PLUGIN / ".zcode-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )["mcpServers"]["delivery-graph"]
        self.assertEqual(
            zcode_server["env"]["HDG_HOST_ADAPTER"],
            "zcode",
        )
        self.assertEqual(zcode_server["cwd"], "${ZCODE_PLUGIN_ROOT}")
        self.assertEqual(
            zcode_server["args"],
            [
                "-X",
                "utf8",
                (
                    "${ZCODE_PLUGIN_ROOT}/skills/delivery-graph/scripts/"
                    "hdg_mcp.py"
                ),
                "--tool-profile",
                "planning",
            ],
        )
        self.assertEqual(
            zcode_server["env"]["HDG_PROJECT_ROOT"],
            "${ZCODE_PROJECT_DIR}",
        )
        self.assertEqual(
            server["default_tools_approval_mode"],
            "approve",
        )
        approvals = server["tools"]
        dispatch_approvals = manifest["mcpServers"][
            "delivery-graph-dispatch"
        ]["tools"]
        self.assertNotIn("freeze_hierarchy", approvals)
        self.assertNotIn("record_user_confirmation", approvals)
        self.assertEqual(
            approvals["close_delivery"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["archive_delivery"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["unfreeze_task_requirement"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["refreeze_task_requirement"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            dispatch_approvals[
                "handoff_ready_automatic_task"
            ]["approval_mode"],
            "prompt",
        )
        self.assertNotIn("update_orchestrator_settings", approvals)

    def test_tool_count_is_the_scheduler_surface(self) -> None:
        tool_count = len(tool_definitions())
        self.assertEqual(tool_count, 35)
        self.assertIn(
            "start_manual_handoff",
            {tool["name"] for tool in tool_definitions()},
        )
        self.assertIn(
            "report_loop_progress",
            {tool["name"] for tool in tool_definitions()},
        )
        self.assertNotIn(
            "recommend_assurance_profile",
            {tool["name"] for tool in tool_definitions()},
        )
        engineering = (ROOT / "docs" / "project-engineering.md").read_text(
            encoding="utf-8"
        )
        documented = re.search(
            r"`mcp_tools\.py` 把 (\d+) 个模型可调用工具映射到 Controller",
            engineering,
        )
        self.assertIsNotNone(documented)
        self.assertEqual(int(documented.group(1)), tool_count)

    def test_bundled_mcp_rejects_python_older_than_3_10_cleanly(
        self,
    ) -> None:
        entries = {
            "canonical-skill": SKILL / "scripts" / "hdg_mcp.py",
            "plugin-copy": PLUGIN_SKILL / "scripts" / "hdg_mcp.py",
        }
        for bundle, entry in entries.items():
            with self.subTest(bundle=bundle):
                stderr = io.StringIO()
                with (
                    patch.object(sys, "version_info", (3, 9, 18)),
                    patch.object(sys, "path", sys.path.copy()),
                    patch("hdg.mcp_server.main", return_value=0),
                    redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as raised,
                ):
                    runpy.run_path(str(entry), run_name="__main__")

                self.assertEqual(raised.exception.code, 1)
                self.assertIn("PLUGIN_PYTHON_UNSUPPORTED", stderr.getvalue())
                self.assertIn("Python 3.10+", stderr.getvalue())

    def test_bundled_mcp_prefers_modern_stdio_discovery(self) -> None:
        entry = SKILL / "scripts" / "hdg_mcp.py"
        request_meta = {
            PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
            CLIENT_CAPABILITIES_META_KEY: {},
            CLIENT_INFO_META_KEY: {
                "name": "bundle-test",
                "version": "1.0.0",
            },
        }
        hierarchy = group_hierarchy()
        with TemporaryDirectory() as project_root:
            preview = preview_hierarchy(
                root=project_root,
                hierarchy=hierarchy,
            )
            messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "preview_hierarchy",
                    "arguments": {"hierarchy": hierarchy},
                    "_meta": request_meta,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "create_manual_handoff",
                    "arguments": {
                        "hierarchy": hierarchy,
                        "expected_hierarchy_fingerprint": (
                            preview["hierarchyFingerprint"]
                        ),
                        "expected_graph_fingerprint": (
                            preview["graphFingerprint"]
                        ),
                        "authorized_project_ids": [],
                        "confirmed_by": "human",
                    },
                    "_meta": request_meta,
                },
            },
            ]
            request = "".join(
                json.dumps(message, separators=(",", ":")) + "\n"
                for message in messages
            )
            environment = dict(os.environ)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(entry),
                    "--project-root",
                    project_root,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            stdout, stderr = process.communicate(
                request,
                timeout=10,
            )
        self.assertEqual(process.returncode, 0, stderr)
        responses = [
            json.loads(line)
            for line in stdout.splitlines()
            if line
        ]
        self.assertEqual(len(responses), 4)
        self.assertEqual(
            responses[0]["result"]["supportedVersions"],
            [
                MODERN_PROTOCOL_VERSION,
                LEGACY_PREFERRED_PROTOCOL_VERSION,
            ],
        )
        self.assertEqual(
            responses[0]["result"]["resultType"],
            "complete",
        )
        self.assertEqual(
            len(responses[1]["result"]["tools"]),
            35,
        )
        preview_result = responses[2]["result"]["structuredContent"][
            "result"
        ]
        self.assertEqual(preview_result["status"], "CHOICE_READY")
        self.assertTrue(preview_result["artifactsReady"])
        handoff = responses[3]["result"]["structuredContent"]["result"]
        self.assertEqual(handoff["status"], "HANDOFF_READY")
        self.assertEqual(handoff["requirementSnapshotStatus"], "FROZEN")
        self.assertFalse(handoff["graphRunCreated"])

    def test_canonical_and_plugin_bundled_mcp_serve_dashboard_resource(
        self,
    ) -> None:
        request_meta = {
            PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
            CLIENT_CAPABILITIES_META_KEY: {},
            CLIENT_INFO_META_KEY: {
                "name": "bundle-resource-test",
                "version": "1.0.0",
            },
        }
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/list",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/read",
                "params": {
                    "uri": DASHBOARD_RESOURCE_URI,
                    "_meta": request_meta,
                },
            },
        ]
        request = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )
        entries = {
            "canonical-skill": SKILL / "scripts" / "hdg_mcp.py",
            "plugin-copy": PLUGIN_SKILL / "scripts" / "hdg_mcp.py",
        }

        for bundle, entry in entries.items():
            with self.subTest(bundle=bundle):
                with TemporaryDirectory() as project_root:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-X",
                            "utf8",
                            str(entry),
                            "--project-root",
                            project_root,
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        env=dict(os.environ),
                    )
                    stdout, stderr = process.communicate(
                        request,
                        timeout=10,
                    )

                self.assertEqual(process.returncode, 0, stderr)
                responses = [
                    json.loads(line)
                    for line in stdout.splitlines()
                    if line
                ]
                self.assertEqual(len(responses), 3)
                self.assertIn(
                    "resources",
                    responses[0]["result"]["capabilities"],
                )
                resources = responses[1]["result"]["resources"]
                self.assertEqual(len(resources), 1)
                self.assertEqual(resources[0]["uri"], DASHBOARD_RESOURCE_URI)
                self.assertEqual(resources[0]["mimeType"], MCP_APP_MIME_TYPE)
                content = responses[2]["result"]["contents"][0]
                self.assertEqual(content["uri"], DASHBOARD_RESOURCE_URI)
                self.assertEqual(content["mimeType"], MCP_APP_MIME_TYPE)
                self.assertIn("<html", content["text"].lower())
                self.assertIn("open_delivery_dashboard", content["text"])

    def test_bundled_mcp_ignores_retired_orchestrator_config(self) -> None:
        entry = SKILL / "scripts" / "hdg_mcp.py"
        request_meta = {
            PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
            CLIENT_CAPABILITIES_META_KEY: {},
            CLIENT_INFO_META_KEY: {
                "name": "retired-config-bundle-test",
                "version": "1.0.0",
            },
        }
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"_meta": request_meta},
            },
        ]
        request = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )
        with TemporaryDirectory() as project_root:
            config = Path(project_root, "user-config", "orchestrator.json")
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "automaticOrchestration": True,
                        "allowCrossAdapterDispatch": False,
                        "allowedAdapters": ["codex", "claude-code"],
                        "maxConcurrentExecutors": 4,
                    }
                ),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["DELIVERY_GRAPH_ORCHESTRATOR_CONFIG"] = str(config)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(entry),
                    "--project-root",
                    project_root,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            stdout, stderr = process.communicate(request, timeout=10)

        self.assertEqual(process.returncode, 0, stderr)
        responses = [
            json.loads(line)
            for line in stdout.splitlines()
            if line
        ]
        self.assertEqual(len(responses), 2)
        tools = responses[1]["result"]["tools"]
        self.assertEqual(len(tools), 35)
        self.assertNotIn(
            "open_orchestrator_settings",
            {tool["name"] for tool in tools},
        )
        self.assertNotIn(
            "update_orchestrator_settings",
            {tool["name"] for tool in tools},
        )

    def test_bundled_mcp_keeps_legacy_initialize_fallback(self) -> None:
        entry = SKILL / "scripts" / "hdg_mcp.py"
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "legacy-bundle-test",
                        "version": "1.0.0",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        ]
        request = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )
        with TemporaryDirectory() as project_root:
            environment = dict(os.environ)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(entry),
                    "--project-root",
                    project_root,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            stdout, stderr = process.communicate(
                request,
                timeout=10,
            )
        self.assertEqual(process.returncode, 0, stderr)
        responses = [
            json.loads(line)
            for line in stdout.splitlines()
            if line
        ]
        self.assertEqual(len(responses), 2)
        self.assertEqual(
            responses[0]["result"]["protocolVersion"],
            "2025-11-25",
        )
        self.assertNotIn("resultType", responses[0]["result"])
        self.assertEqual(
            len(responses[1]["result"]["tools"]),
            35,
        )
