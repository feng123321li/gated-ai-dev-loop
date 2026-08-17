# 宿主兼容矩阵

兼容性分成两层，不能混为一个“支持”：

- **核心契约**：Python Controller、schema、SQLite、生成产物、调度协议测试和 stdio MCP 握手通过。
- **真实宿主**：实际 Codex、Claude Code 或 ZCode 会话加载候选 Plugin，创建原生子 Agent，并完成 claim、progress、heartbeat、result，最后到达待用户确认门禁；冒烟程序不得代替用户确认。真实宿主冒烟按宿主独立实现于 `scripts/host_smoke/`（`codex.py`、`claude.py`、`zcode.py`，共享证据规则在 `common.py`，入口 `python -m scripts.host_smoke`）：Codex 与 Claude Code 由单次无头调用端到端完成；ZCode 无无头契约，采用两段式冒烟——harness 准备一次性工作区并外置提示词，真实 ZCode 会话居中执行，harness 随后复核 `scheduler.db` 证据链。

当前 canonical Plugin/Skill 名为 `delivery-graph`，展示名为“分层交付 Graph 控制面”。`.layered-delivery/` 只是稳定的项目数据目录，不随 Plugin identity 更名。

## 0.42.1 发布候选矩阵

0.42.1 修复 carry-forward Revision 丢失 receiver 来源、导致 GROUP seam Review 无法通过独立性门禁的问题，并对 0.42.0 已有事件按 `fromRevision` 回溯前序来源。Graph 改为只持久化工作区变更清单、base/HEAD 与状态/范围指纹，不再存源码 diff 或生成 `workspace-changes.patch`；GROUP/Delivery Review 同时裁掉 `workspaceChanges` 与 TASK 实现细节，只复用证据引用并补验直接 seam。`heartbeat_loop` 新增可选 `expected_command_seconds`（61–1800）和 120 秒收尾缓冲，为 Maven 等首次依赖预热提供有界长租约；当前有效 reservation 的 decision mismatch 可返回同 reservation 重试凭据。MCP 工具联集仍为 32，schema v3 与 `.layered-delivery/` namespace 不变，旧 Graph 无需迁移。候选已完成 424 项 Python 测试（423 通过、1 项按环境跳过）、全树编译、四个 Skill、Plugin 镜像、Claude Plugin、release candidate 与差异校验。

## 0.42.0 发布候选矩阵

0.42.0 针对短租约下 receiver 同步执行长构建命令（如 Maven）期间心跳停默、被误判 `WORKER_LOST` 的问题硬化执行契约：`executionPolicy.longRunningCommands` 新增 `estimatedOverSecondsRequiresBackground: 60` 与 `preferNarrowCommandScope: true`，TASK Skill 正文、`task-execution.md`、`execution-quickstart.md` 与 receiver MCP server instructions 同步要求先估算耗时、优先收窄命令范围（单模块、指定测试类、离线依赖解析）、预计超过 60 秒转非阻塞执行并保持 60 秒心跳。同时为 ZCode receiver Prompt 补齐宿主专属 Skill 调用文案，不再输出 Codex 双宿主兜底句式；`SCHEDULER_DISPATCH_DECISION_MISMATCH` 增加 expected/submitted details 便于一次定位凭据混搭，TASK/Review SKILL 增加多轮 assignment 只认最新完整凭据组的硬约束。MCP 工具联集仍为 32，三个 Profile、schema v3、租约与心跳数值协议（5 分钟租约、60 秒心跳、2 分钟续租阈值）及持久化均不变；`executionPolicy` 仅新增两个键，已有 Graph 与 `.layered-delivery/` 运行数据无需迁移。候选已完成 418 项 Python 测试（417 通过、1 项按环境跳过）、全树编译、四个 Skill、Plugin 镜像、release candidate 与差异校验。

## 0.41.1 发布候选矩阵

0.41.1 修复自动调度进入 `DELIVERY_REVIEW_LOOP` 时 receiver Skill 路由缺失、导致 `plan_dispatch_batch` 被包装为 `INTERNAL_ERROR` 的问题，并删除路由表与 Review Skill 描述中的两个废弃 Loop 名称。新增合法 Loop 路由全集断言，以及从 TASK、TASK Review 推进到 Delivery Review 实际派遣的回归测试。MCP 工具联集仍为 32，三个 Profile、schema v3、租约、心跳、并发和持久化协议均不变；既有处于 `READY` 的 Delivery Review 升级后可直接重新规划派遣，无需重建 Graph。候选已完成 418 项 Python 测试（417 通过、1 项按环境跳过）、全树编译、四个 Skill、Plugin、release candidate 与差异校验。

## 0.41.0 发布候选矩阵

0.41.0 收敛调度与可观测边界：Loop 租约缩短为 5 分钟，receiver 每 60 秒心跳并只在剩余 2 分钟的续租窗口延长租约；派遣和心跳更新 Agent 主会话实时面板，心跳不重写 Markdown 投影。Controller 删除软额度策略、未接线的硬额度熔断子系统、Worker 模型遥测及风险分类推荐入口；MCP 工具联集为 32，planning Profile 为 15，固定并发 4、资源锁、普通显式暂停/恢复和 schema v3 namespace 保持不变。候选已完成 417 项 Python 测试（416 通过、1 项按环境跳过）、全树编译、四个 Skill、Plugin、release candidate 与差异校验。

## 0.40.3 发布候选矩阵

0.40.3 是不改变外部协议的可维护性版本：按职责拆分 Graph runtime、模型渲染、规划、仓储层级、Git binding、MCP schema/catalog/adapter、层级契约与模型校验，并同步拆分超长测试；所有 `src/hdg/*.py` 与测试 Python 文件均不超过 1000 行，兼容门面继续暴露原 API。Controller 行为、33 个 MCP 工具、四个 Skill、三个 MCP Profile、schema v3、宿主交互及 `.layered-delivery/` 数据均无变化。候选已完成 429 项 Python 测试（428 通过、1 项按环境跳过）、全树编译、四个 Skill、Claude Plugin、release candidate 与差异校验。

## 0.40.2 发布候选矩阵

0.40.2 仅重写 README 信息架构，将首页压缩为项目摘要，并把内部 Marketplace 安装细节路由到团队运维文档。Controller、33 个 MCP 工具、四个 Skill、三个 MCP Profile、schema v3、无 Hook 模式、`CURRENT_WORKSPACE_SERIAL`、Codex/Claude Code/ZCode 宿主协议和 `.layered-delivery/` 数据均不变；真实宿主能力沿用 0.40.1 的验证边界。候选已完成 427 项 Python 测试（426 通过、1 项按环境跳过）、编译、Skill/Plugin、release candidate 与差异校验。

## 0.40.1 发布候选矩阵

0.40.1 保持 33 个 MCP 工具、四个 Skill、三个 MCP Profile、schema v3、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`；控制面与 Plugin 产物无行为变化，仅把 0.39.12/0.39.13 遗留的“真实 ZCode 宿主冒烟结果待回填”落成可重复执行的显式两段式流程，并把 `scripts/host_smoke.py` 拆分为按宿主独立的实现。

- **按宿主拆分**：`scripts/host_smoke/` 包内 `codex.py`、`claude.py`、`zcode.py` 各自拥有提示词、宿主命令与会话执行；`common.py` 承载宿主中立的一次性工作区准备、冒烟产物发现、`scheduler.db` 证据校验与共享提示词框架；入口统一为 `python -m scripts.host_smoke`，CI 与文档同步更新。
- **准备段**：`python -m scripts.host_smoke run --host zcode --scenario light|standard --execute --workspace-dir <空目录>` 在持久目录准备一次性 Git 主 checkout 工作区，并把 ZCode 提示词写到工作区外的 `<name>-prompt.md`，不污染冒烟产物检测。
- **真实会话段**：在真实 ZCode 会话打开该工作区目录，粘贴提示词全文执行：与 Claude Code 相同的主 checkout 串行边界（不建 worktree）、Controller 交互经宿主原生 `AskUserQuestion` 原样作答、`dispatch_loop` 使用 `owner=zcode`、独立宿主原生子 Agent 完成各 assignment，停在 `RECORD_USER_CONFIRMATION`，绝不调用 `record_user_confirmation`。
- **复核段**：`python -m scripts.host_smoke run --host zcode --scenario <同值> --verify-only --workspace-dir <同一目录>` 校验 `scheduler.db`：claim 只含 `zcode`、LIGHT/STANDARD 必需事件（`LOOP_CLAIMED`/`LOOP_SUCCEEDED`，STANDARD 另有 `LOOP_HEARTBEAT`/`LOOP_PROGRESS_REPORTED`）齐备、run 停在待确认门禁且无伪造 `USER_CONFIRMED`。
- **边界**：`--workspace-dir`/`--verify-only` 仅对 zcode 有效，误用于其他宿主 fail closed；ZCode 人工中转段不进入 CI，`host-smoke:codex`/`host-smoke:claude` 手动任务不变。
- **冒烟执行状态**：light=已通过（2026-08-14，真实 ZCode 3.7.7 会话加载 0.40.1；以隔离 userData 第二实例居中执行，提示词提交与 Controller 选择器作答由用户明确授权的 CDP 代操作完成，最终确认未代打）：rootId=`d-light-smoke`，runId=`run-eb59ab622d9742eaaf1b49217db176ec`，run 停在 `RECORD_USER_CONFIRMATION`（ACTIVE），claim 仅含 `zcode`，`LOOP_CLAIMED`/`LOOP_SUCCEEDED` 各 1，`--verify-only` 复核通过；standard=待首次执行。
- **验证**：全量 Python 427 项完成（426 通过、1 项按环境跳过）、`compileall`、Skill/Plugin 镜像重建、release candidate 与差异校验；新增 zcode 提示词、主 checkout 准备、两段式参数守卫与提示词外置共 5 项测试。

## 0.40.0 发布候选矩阵

0.40.0 将职责拆为四个 Skill：`delivery-graph` 负责规划、基线、Revision 与冻结，`delivery-graph-dispatch` 负责 frontier、派遣、等待与恢复，`delivery-graph-task` 只执行 TASK Loop，`delivery-graph-review` 只执行 Review Loop。Plugin 同时注册 planning、dispatch、receiver 三个 MCP server，分别暴露 16、12、7 个工具；Profile 联集保持原有 33 个工具，单个 Agent 不再加载完整目录，越权调用以 `MCP_TOOL_OUTSIDE_PROFILE` 失败关闭。Codex、Claude Code 与 ZCode 使用相同 Profile 边界，敏感审批、schema v3、`CURRENT_WORKSPACE_SERIAL`、Python 3.10+ 标准库运行时及已有 `.layered-delivery/` 数据均不变，无需迁移。核心候选已完成 422 项 Python 测试（421 通过、1 项按环境跳过）、编译、四 Skill/Plugin 镜像、release candidate、Skill、Claude Plugin 与差异校验。

## 0.39.24 发布候选矩阵

0.39.24 保持 33 个 MCP 工具、schema v3、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`。TASK requirement 重冻结现在创建同一 Delivery 的下一不可变 Revision，使新 Graph 指纹同时锚定 Revision 历史、claim、投影与事件重放。AUTOMATIC/MANUAL Run 到达最终用户确认边界后，只要业务 commit、clean、HEAD 和 receiver/reservation 门禁满足，就释放物理 workspace turn 而不提前完成 Delivery；用户可在其他 Delivery 已取得 turn 后按旧 `rootId` 补录确认，验收前修改则以新 Revision 重新排队。`CANCELLED` owner 在同一安全边界释放且不依赖归档，终态查询忽略过期 `workspaceRebase`。核心候选已完成 415 项 Python 测试（414 通过、1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Skill、Claude Plugin 与差异校验。

## 0.39.23 发布候选矩阵

0.39.23 保持 33 个 MCP 工具、schema v3、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`。同一 Delivery 从任意 Revision `N` 冻结到 `N+1` 时，项目集合、checkout、分支和完整 Git binding 未变即可复用最初的 clean `workspaceTurnStart`；tracked、staged 与 untracked 业务改动继续属于同一次物理 workspace turn，不要求删除生成物、stash 或检查点提交，且冻结不授权 commit。原始 turn 历史改写、未解决冲突，或项目、checkout、分支、`baseRef`、`baseCommit`、`integrationTarget` 变化时继续 fail closed。核心候选已完成 407 项 Python 测试（406 通过、1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Skill、Plugin 与差异校验。

## 0.39.22 发布候选矩阵

0.39.22 保持 33 个 MCP 工具、schema v3、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`。宿主规划层生成足以推进各 Loop 的方向、约束、外部契约和验收；Graph 将工作项整理为 hierarchy/DAG，以 `rootId`、不可变 Revision、双 fingerprint、SQLite/事件链和 `loop_context` 统一承担总览、绑定、持久记忆、依赖/资源控制、调度、恢复及进度/结果汇总，但不创作业务需求或决定实现。用户明确指定的 Skill 在规划相关时可预触发，并始终随自动 assignment、手动 TASK action/handoff 与 Loop context 传给相应 receiver；适用且宿主可用时应在相应阶段原生触发，实现类 Skill 通常位于 TASK，阶段不适用或宿主不可用可跳过。Controller 不查询 catalog、不要求使用回执，也不以未使用提示阻断成功。本候选已完成 403 项 Python 测试（402 通过、1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Plugin 与差异校验。

## 0.39.21 发布候选矩阵

0.39.21 保持 33 个 MCP 工具、schema v3、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`。自动 assignment、手动 TASK action、manual handoff 与 `loop_context` 会向 receiver 传递具体 Skill catalog 名及宿主原生软触发提示：Codex 使用 `$skill-name`，Claude Code 使用 Skill tool，其他宿主使用自己的原生入口；receiver 仅在提示适用且 Skill 可用时尽量触发，不适用或不可用可跳过，Controller 不查询 catalog、不要求激活回执，也不把未使用提示判为失败。规划层同时新增阻断式 TASK 切分完整性预检，并在删除、改名、移动或公共签名变更时执行授权范围内的定向符号引用分析；局部 requirement 解冻/再冻结会等待全部未领取 reservation 到期，防止旧 assignment 与新 Graph 指纹竞态。本候选已完成 402 项 Python 测试（401 通过、1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Skill/Plugin 与差异校验；真实宿主的 Skill catalog 可用性仍由各宿主负责。

## 0.39.20 发布候选矩阵

0.39.20 修复 ZCode 宿主下 stdio MCP server 的启动路径：ZCode 启动 Plugin MCP 时的进程 cwd 不保证是 Plugin 根，旧的相对 `skills/.../hdg_mcp.py` 会在请求前直接找不到脚本并表现为 `Connection closed / 0 tools`。`.zcode-plugin/plugin.json` 现在以 `${ZCODE_PLUGIN_ROOT}` 同时锚定绝对脚本参数和 `cwd`，业务工作区由 ZCode 原生 `${ZCODE_PROJECT_DIR}` 独立传给 `HDG_PROJECT_ROOT`；二者不得混用，也不使用未定义的通用 `${PROJECT_DIR}`。Controller/Adapter 内部继续统一为 `project_root/workspace_root`，宿主模板差异不泄漏到 Graph 业务层。当项目根模板未展开或为空时，`_resolve_project_root` 仍提供启动期容错。版本同时加入 Plugin 外只读注册矩阵 Demo、分阶段 stderr 生命周期诊断、确定性的 `recommend_assurance_profile`，工具总数为 33；`LIGHT` 短任务在 claim 建立的初始租约内不要求首次 heartbeat/progress，仍保留基线冻结、定向终态验证、patch 快照和用户最终确认。MCP 使用最新正式 `2026-07-28` 无状态路径并保留 legacy initialize 回退。宿主原生 spawn 日志、内置健康探针和热重连属于宿主 P0 契约，详见[生命周期与注册矩阵](mcp-host-lifecycle-contract.md)；Plugin 自身不能在未挂载时充当健康工具。

## 0.39.19 发布候选矩阵

0.39.19 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`。Codex 握手改用紧凑 Server Instructions，避免宿主重复注入完整公共说明时放大工具注册负担；完整通用说明仍供其他宿主使用。Claude Skill 的 `allowed-tools` 继续显式列出 25 个安全工具，不用通配符扩大敏感工具审批范围。测试套件移除重复和 linked-worktree 专用场景，但保留必要的真实 Git 门禁覆盖。核心候选已完成 371 项 Python 测试（370 通过、1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Skill、Claude Plugin 与差异校验；真实宿主能力继续按本页既有边界验证。

## 0.39.18 发布候选矩阵

0.39.18 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，重新划定 Review 职责：Controller 只执行 Graph 前驱成功门禁、结果契约校验和事件/SQLite/投影持久化；独立 Review receiver 才作当前层技术验收；最终用户只作业务确认。TASK Review 只验本 TASK，GROUP seam Review 按真实组合边界可选，Delivery Acceptance/Readiness 每个 `STANDARD` Delivery 只运行一次且不重验全部下层 Loop。成功 Review outcome 与分层验收投影只保留本层结论和有界证据，未配置 GROUP Review 不生成节点、run/event/outcome 或空投影段落。核心候选已通过 392 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Skill、Codex/Claude Plugin 与差异校验；真实宿主能力继续按本页既有边界验证。

## 0.39.17 发布候选矩阵

0.39.17 仅将三个宿主 manifest 共用的 Delivery Graph PNG 从 1254×1254、1.3 MB 优化为 256×256、约 76 KB，并增加 128 KB 发布上限回归门禁；继续保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`。Graph、运行数据库、宿主审批和 receiver 调度契约均不变，真实宿主能力沿用 0.39.16 的边界。

## 0.39.16 发布候选矩阵

0.39.16 仅在 `.zcode-plugin/plugin.json` 的 `interface` 补齐 `composerIcon` 与 `logo`，使其与 Codex manifest 一致地声明 0.39.15 引入的浅色 Plugin 图标，继续保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`；Graph、运行数据库、宿主审批和 receiver 调度契约均不变。核心候选需通过全量 Python 测试、编译、Skill/Plugin 镜像、release candidate、manifest 与差异校验；真实宿主能力沿用 0.39.15 的边界，不把图标渲染以外的行为表述为新增宿主验证。

## 0.39.15 发布候选矩阵

0.39.15 仅新增浅色 Plugin 图标并更新展示元数据，继续保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`；Graph、运行数据库、宿主审批和 receiver 调度契约均不变。核心候选需通过全量 Python 测试、编译、Skill/Plugin 镜像、release candidate、manifest 与差异校验；真实宿主能力沿用 0.39.14 的边界，不把图标渲染以外的行为表述为新增宿主验证。

## 0.39.14 发布候选矩阵

0.39.14 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，把后续自动 Delivery 建模为持久队列，并允许宿主在队首按一次 `AUTOMATIC` 选择机械完成 stash 与分支准备。

- 只有已记录 `AUTOMATIC` 的后续 Delivery 投影为 `QUEUED`，携带队列位置、owner 和无需再次确认的 continuation；前序 owner 满足可验证 commit、clean、HEAD 与 receiver 释放边界后续调队首。
- 队首若存在非 owner 的既存业务改动，宿主按精确 fingerprint stash tracked/staged/untracked 内容并排除 `.layered-delivery/**`，创建或切换冻结 Delivery 分支后调用 `resume_execution_mode`。未合并冲突继续等待，运行中 owner 的未完成改动不可被 stash 绕过。
- 手动冻结同样持久化完整 Delivery 快照，但保持 `HANDOFF_READY`，不进入自动队列；`start_manual_handoff` 后才创建 manual Run 与 workspace binding。
- 清理残留 worktree 编排协议；primary 与既有 linked checkout 统一按普通 current workspace 串行处理，不自动创建新 worktree。
- 核心候选已通过 388 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Skill/Plugin manifest 与差异校验。实际宿主仍需按本页定义完成原生 child 冒烟，并停在 `RECORD_USER_CONFIRMATION`。

## 0.39.11 发布候选矩阵

0.39.11 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，收紧项目声明的 Python 3.10+ 运行边界，并补齐现有受信任 `zcode` Adapter 的内部映射一致性。

- CPython 3.10.19 可编译并导入源码、canonical Skill 与 Plugin payload；验收投影不再依赖 Python 3.12 才支持的 f-string 语法。vendored stdio MCP 在导入 Controller 前拒绝 Python 3.9 及更早版本，并返回稳定错误码。
- `zcode` 与现有可信 Adapter 一样具有 native receiver、`AskUserQuestion` 交互 selector、同控制根监控权限和 `zcode:default` capacity key；集合不变量确保今后新增可信 Adapter 时不会遗漏容量断路器映射。
- 本节只确认 Adapter 核心契约；0.39.11 不新增 `.zcode-plugin` manifest，也不宣称 ZCode 真实宿主已完成原生 child 冒烟。Codex/Claude 的真实宿主验证要求保持不变。
- 核心候选已在 CPython 3.10.19 与 3.14.6 各通过 383 项 Python 测试（各 1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Skill/Plugin manifest 与差异校验。实际 Codex/Claude 会话仍需按本页定义完成宿主原生 child 冒烟，并停在 `RECORD_USER_CONFIRMATION`。

## 0.39.13 发布候选矩阵

0.39.13 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，修正 0.39.12 的 ZCode 项目根解析假设，使 ZCode 宿主加载 Plugin 后 MCP 请求不再失败关闭于根目录解析。

- **根因**：ZCode 宿主不会像 Codex 那样在每个 MCP 请求的 `_meta` 注入 `codex/sandbox-state-meta`；沿用 `--project-root-from-meta` 时，`workspace_status`、`hierarchy_contract` 等只读工具全部返回 `PROJECT_ROOT_UNAVAILABLE`（"Codex sandbox metadata is required on every MCP request"）。
- **修复**：`.zcode-plugin/plugin.json` 不再使用 `--project-root-from-meta`，改为 `HDG_PROJECT_ROOT=${CLAUDE_PROJECT_DIR}`（ZCode 对 plugin 提供的 MCP server 展开该模板变量，指向当前工作区），与 Claude 侧 `.mcp.json` 的根解析方式一致；`HDG_HOST_ADAPTER=zcode` 保持独立注入。Codex 与 Claude manifest 不受影响。
- **验证**：按 ZCode 启动方式（plugin 根为 cwd、`HDG_HOST_ADAPTER=zcode`、`HDG_PROJECT_ROOT` 指向工作区）端到端拉起 `hdg_mcp.py`，`initialize` 握手成功，`workspace_status` 项目根解析恢复正常；在 `delivery-graph` 源码仓库内按预期触发自托管防护 `SELF_HOSTING_DOGFOOD_REQUIRED`（未显式 `--dogfood` 不产生运行包）。
- 本节只确认 manifest 与核心契约一致；0.39.13 不宣称 ZCode 真实宿主已完成原生 child 冒烟。真实 ZCode 宿主冒烟候选验证中，结果待回填。

## 0.39.12 发布候选矩阵

0.39.12 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，在 0.39.11 已补齐的 `zcode` Adapter 内部映射基础上新增独立 `.zcode-plugin/plugin.json` manifest。

- 新增 `plugins/delivery-graph/.zcode-plugin/plugin.json`，以 `HDG_HOST_ADAPTER=zcode` 独立注入 Adapter 身份，不再借用 `.mcp.json` 的 `claude-code` 身份。ZCode 与 Codex 一样从请求 `_meta` 解析项目根（`--project-root-from-meta`），不依赖 `${CLAUDE_PROJECT_DIR}`。
- ZCode 原生支持 `AskUserQuestion` 交互选择器，与 Claude Code 共用同一 `HOST_NATIVE_QUESTION_TOOLS` 映射；敏感工具审批策略与 Codex manifest 一致。
- ZCode 不继承 Codex Desktop 的 Dashboard legacy bridge（`_dashboard_read_grants` 的 codex-only 复用路径）：非 Codex Adapter 的只读 Dashboard 请求继续按既有 fail-closed 规则处理。
- 本节只确认 manifest 与核心契约一致；0.39.12 不宣称 ZCode 真实宿主已完成原生 child 冒烟。真实 ZCode 宿主冒烟候选验证中，结果待回填。

## 0.39.10 发布候选矩阵

0.39.10 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，将 Graph 协调改为宿主事件优先，并让分层 Review 在独立判断不变的前提下复用与相关代码状态精确绑定的上游验证证据。

- 活跃 receiver 优先使用宿主原生 completion wait；超时只读 `graph_status`，并按首次心跳、heartbeat/progress stale、lease、reservation 与资源容量恢复中的最早有效时刻唤醒。稳定 `changeFingerprint` 排除纯时间倒计时，避免无变化重复播报。
- `graph_frontier` 的 no-op 不再修改 run 时间或重写投影；reservation/lease 精确到点即失效。CLAIMED reservation 的旧短 TTL 不参与后续唤醒，跨 Delivery 的真实资源冲突按 blocker deadline 恢复。
- TASK 验证绑定 affected scope 与 workspace snapshot；TASK Review、已配置的 GROUP seam Review 和 Delivery Acceptance/Readiness 只复用 `PASSED + EXACT_MATCH` 证据，相关代码、环境或高风险边界变化时定向或完整复跑。Review 结果提交前重新计算 freshness，P0/P1 继续要求闭环。
- scope 状态按项目批量捕获、跨证据去重；Review context 使用紧凑 evidence index 与 workspace diff 引用，避免逐文件 Git 子进程和重复大 payload。
- 核心候选已通过 381 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Claude Plugin manifest 与差异校验。实际 Codex/Claude 会话仍需按本页定义完成宿主原生 child 冒烟，并停在 `RECORD_USER_CONFIRMATION`。

## 0.39.9 发布候选矩阵

0.39.9 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，完成旧 worktree setup 协议的物理清理并修复调度优化中的存量状态与重试边界。Controller 不创建 linked worktree；当前目录即使是既有 linked checkout，也只作为普通 current workspace 使用。

- Plugin 不再包含 `delivery-coordinator` Agent，也不公开 worktree setup reservation、progress、lease 或 report 路径。Claude 主会话按冻结 `gitBinding` 在当前 checkout 准备分支、调用 `resume_execution_mode`，再通过 `plan_dispatch_batch` 启动独立 receiver。
- 新提交的 hierarchy、数据库变更、表字段、索引、约束、外键和验证步骤继续执行有界资源限制；同 state contract 下已持久化的数据仍按原指纹与规范形态读取，不因新上限失去可恢复性。
- READY 刷新按 `run_id + node_id + MAX(attempt)` 选择最新尝试，不受 SQLite 索引扫描方向影响；既有 scheduler 数据库在 state contract 校验通过后幂等补齐 run、lease、event 与 dispatch reservation 索引。
- 核心候选已通过 371 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Claude Plugin manifest 与差异校验。实际 Codex/Claude 会话仍需按本页定义完成宿主原生 child 冒烟，并停在 `RECORD_USER_CONFIRMATION`。

## 0.39.8 发布候选矩阵

0.39.8 提供 32 个 MCP 工具、schema v3 与无 Hook 模式。同一物理 checkout 可以绑定多个 Delivery，但状态必须用显式 `rootId` 路由，执行统一为 `CURRENT_WORKSPACE_SERIAL`；Controller 不再公开 worktree setup 工具，也不自动创建 linked worktree。MCP Apps 标准 `tools/call` 失败或精确缺少 project root 时可回退兼容 bridge；服务端只允许同一 Codex legacy 连接、同一 `root_id` 复用此前成功 Dashboard 读取形成的只读 workspace grant。Modern 请求、非 Codex Adapter、显式空 metadata、其他 root、其他只读工具和全部写工具继续失败关闭。

- 看板可见时每 15 秒串行自动刷新，隐藏时暂停；手动刷新仍立即读取 `open_delivery_dashboard`，任何路径都不得调用 `graph_frontier` 推进状态。
- Dashboard Resource 使用 `ui://delivery-graph/dashboard-v2.html`，避免升级后命中旧缓存；无 UI 宿主仍返回相同的文字和 `structuredContent`。
- Graph 宽屏按 rank 横向绘制依赖边；面板空间不足时纵向换行并在节点内显示前置项，不产生水平滚动或节点裁切。
- 同一 checkout 的后续 Delivery 必须等待队首 Run 终态或最终用户确认边界、取消 receiver 租约失效且无 reservation、产生可验证业务 commit、工作树与 index 干净且历史未改写；待用户确认仅释放物理 turn，任何分支、HEAD、scope 或 dirty 漂移都失败关闭。
- TASK/TASK Review 的 Controller 可信 Git 证据投影只包含变更文件清单、base/HEAD 和状态指纹，不生成源码补丁；宿主需要具体内容时从已授权 checkout 或对应提交按需读取。
- 核心候选已通过 369 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、发布与差异校验；真实 Edge 已覆盖 1280/900/600/360 四档宽度。实际 Codex/Claude 会话仍需按本页定义验证面板自动/手动刷新与文本降级。
## 0.39.7 发布候选矩阵

0.39.7 保持 33 个 MCP 工具、schema v3 与无 Hook 模式，修复 Codex Desktop 内嵌进度面板刷新，并把 Graph 改为按实际容器宽度切换布局。MCP Apps 标准 `tools/call` 失败或精确缺少 project root 时可回退兼容 bridge；服务端只允许同一 Codex legacy 连接、同一 `root_id` 复用此前成功 Dashboard 读取形成的只读 workspace grant。Modern 请求、非 Codex Adapter、显式空 metadata、其他 root、其他只读工具和全部写工具继续失败关闭。

- 看板可见时每 15 秒串行自动刷新，隐藏时暂停；手动刷新仍立即读取 `open_delivery_dashboard`，任何路径都不得调用 `graph_frontier` 推进状态。
- Dashboard Resource 使用 `ui://delivery-graph/dashboard-v2.html`，避免升级后命中旧缓存；无 UI 宿主仍返回相同的文字和 `structuredContent`。
- Graph 宽屏按 rank 横向绘制依赖边；面板空间不足时纵向换行并在节点内显示前置项，不产生水平滚动或节点裁切。
- 核心候选已通过 342 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、Claude Plugin、发布与差异校验；真实 Edge 已覆盖 1280/900/600/360 四档宽度。实际 Codex/Claude 会话仍需按本页定义验证面板自动/手动刷新与文本降级。


## 0.39.6 发布候选矩阵

0.39.6 提供 33 个 MCP 工具，用户可选执行模式仍只有 `AUTOMATIC` 和 `MANUAL`。AUTOMATIC 的 TASK 与各级 Review 统一由 `plan_dispatch_batch` 预留，再由独立 child 用 reservation、decision fingerprint、receiver context 和 `operation_id` 调用 `dispatch_loop`。本候选删除生命周期 Hook、`claim_current_task` 和 attestation 持久化；新建状态不创建旧认证表，但 Graph compiler 契约仍为 `schema-v3-graph-compiler-v1`。旧 0.39.5 状态只有在 READY、从未 claim 且没有 reservation 时才承诺无需迁移续跑。

- Plugin manifest 不声明 lifecycle Hook，安装和升级都没有 `/hooks` 信任步骤。
- AUTO claim 必须匹配未过期 reservation、Graph/decision fingerprint、node/attempt 和显式 `operation_id`；同一 reservation 与 operation 的响应丢失重试幂等返回已提交 assignment。
- heartbeat、progress、pause 与 result 都显式携带 claim 返回的 `operation_id`，并继续受 workspace、项目 scope、lease 与资源锁校验。
- 独立 Review child 是宿主编排不变量，不再有真实 session、parent-child 或 reviewer 身份的密码学证明；这是无 Hook 模式的已知能力降级。
- Git Delivery workspace identity 使用 Git 历史 lineage 与冻结分支，不使用仓库或 worktree 绝对路径；移动仓库或重建同分支 worktree可恢复，其他分支继续返回 Git branch mismatch，旧路径哈希绑定在原路径首次访问时升级。

发布候选必须完成 Python 全量测试、compileall、UTF-8 Skill 校验、33 工具与生成镜像发布校验、Claude Plugin manifest 校验和差异检查；真实宿主 smoke 不再传递 Hook 事件或绕过 Hook trust。

## 0.39.2 发布候选矩阵

0.39.2 保持 33 个 MCP 工具，把 MANUAL 与 AUTOMATIC 收敛到同一可信 receiver 身份链，并修复 Codex Plugin Hook 未被 manifest 激活、单仓 runtime `projectScopes=[]` 和失败 reservation 必须等待 TTL 的问题。两种 dispatch 都要求宿主 Adapter 为真实原生 child 签发并一次性消费 attestation；AUTO 必须绑定非空 reservation，MANUAL 的 `reservation_id` 必须为 `NULL`。claim 后的 scope、operation、heartbeat、progress、pause、result 和 lease 门禁完全一致，差异只在授权来源。

Codex 候选包必须在 manifest 中显式声明 `./hooks/hooks.json`。真实宿主验证先在新任务的 `/hooks` 审查并信任该 Plugin 的 Hook，再覆盖以下边界：

- Hook 未加载或未信任时，`plan_dispatch_batch` 在创建任何 reservation 前返回 `SCHEDULER_HOST_HOOK_NOT_READY`；恢复信任后才能重新计划。
- AUTO `SubagentStart` 与 MANUAL `dispatch_loop` PreToolUse 都从宿主可信 `.codex/sessions` 中验证真实 child/parent，root/helper、内部 Worker、自定义 `CODEX_HOME` 和伪造 transcript 均 fail closed。
- 普通单仓 Delivery 从顶层 `gitBinding` 与实际 Delivery workspace 合成唯一 `primary` runtime scope；AUTO claim 和 `loop_context` 必须返回该 scope，多仓仍逐 scope 验证。
- `SubagentStart` 已定位 AUTO reservation、但身份/workspace/scope attestation 失败时，TASK 保持 READY，尚未绑定 receiver 的 reservation 立即释放，child 在任何仓库检查或修改前停止。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Adapter attestation、scope 合成、reservation 原子释放与 Hook 配置回归 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | `/hooks` 信任、AUTO/MANUAL 原生 child、真实 transcript 与 mutation 链 |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；Codex Desktop 的历史故障实例为 0.147.0-alpha.6.5。版本号只用于本轮复现与验证，不构成永久最低版本承诺。

## 0.39.1 发布候选矩阵

0.39.1 保持 33 个 MCP 工具，修复 Codex Desktop `SubagentStart` 先于直接 child transcript 首条 `session_meta` 落盘时的 claim 竞态。核心契约必须模拟 transcript 先为空、随后写入合法 session metadata，并验证 Hook 只在当前 child 文件名、可信 sessions 根、精确 parent/role/task 和有效 reservation 全部匹配后原子 claim；超时、伪造路径、自定义 `CODEX_HOME`、错误角色和过期 reservation 继续 fail closed。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Hook 时序、身份绑定与协议回归 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | Codex 空 transcript 竞态已复现；0.39.1 待重新派遣验证 | 原生 child claim、heartbeat 与后续 Loop |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；Codex Desktop 实际失败实例为 0.147.0-alpha.6.5。版本号用于复现记录，不构成永久最低版本承诺。

## 0.39.0 发布候选矩阵

0.39.0 提供 33 个 MCP 工具和一个静态 MCP Apps Resource，并新增数据库 baseline 强制契约、clean primary feature 的 stacked 子分支基线、Codex Desktop sandbox transcript 识别及未领取自动 TASK 的显式人工恢复。核心契约必须验证数据库结构在执行前生成并冻结、缺失设计或 LIGHT fail closed、Loop 只执行 after，以及 `NEW_FROM_CURRENT_BRANCH` 的 child/base/integration binding 与 hostDispatch 完全一致。`SubagentStart` 必须在 Hook 隔离账户与宿主 profile 不同时仍验证真实 transcript；`handoff_ready_automatic_task` 只允许 clean、READY、从未领取且无有效 reservation 的 TASK，并保持 Review 自动派遣。Modern/Legacy 两种 wire shim 继续共享同一 tools/resources dispatcher，`open_delivery_dashboard` 只读取当前状态，UI 不包含控制面写工具或外部资源，无 UI 宿主仍能使用文字与 `structuredContent`。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Adapter、Resource、只读 snapshot 与 UI 静态契约 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | MCP Apps 渲染、刷新和文本降级 |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；这是本轮验证目标，不是永久最低版本。UI 刷新不得调用 `graph_frontier`，按钮也不得绕过宿主审批或 Controller 权限。

## 0.37.3 发布候选矩阵

0.37.3 提供 31 个 MCP 工具，在 0.37.2 的 worktree setup 监控基础上新增显式的完成后 `archive_delivery`。真实宿主必须验证：归档只接受 `COMPLETED`，归档操作经过敏感工具审批，默认状态发现与根总览隐藏归档项，而显式 `root_id` 仍保留完成 run、Revision 历史、事件链和详情投影。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | 归档状态机、SQLite 契约与协议回归 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | 双宿主敏感审批与显式历史查询 |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；这是本轮验证目标，不是永久最低版本。Controller 仍不执行 Git 或目录写操作。

## 0.37.2 发布矩阵

0.37.2 提供 30 个 MCP 工具，新增 `report_worktree_setup` 和 worktree setup 进度监控。除 0.37.1 的 reservation、精确分支和多项目场景外，真实宿主必须验证：创建阶段与百分比能在主仓 `progressMonitor` 刷新；30 秒 heartbeat 可续 120 秒租约；超时/失败不会自动重发；核对旧进程与半成品后，并发 retry 只有一个获得新 attempt 与 `IMMEDIATE`。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | setup 状态机、SQLite 并发与协议契约 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | 原生 worktree 心跳、失败核对与安全 retry |

该版本的候选宿主基线为 Codex CLI 0.147.0 和 Claude Code 2.1.226。Controller 不执行 Git 或目录写操作。

## 0.37.1 发布候选矩阵

0.37.1 保持 29 个 MCP 工具，在 0.37.0 双宿主协议上新增 worktree setup reservation、精确分支 dispatch 和多项目 worktree 编排。核心门禁除全量测试、`compileall`、镜像一致性和发布校验外，必须真实验证：同一选择并发调用只有一个 `IMMEDIATE`；宿主错分支 clean/dirty 两条路径；两个 Delivery 同仓同分支 fail closed；两个不同仓库可用同名分支；多项目全部 `READ_WRITE` worktree 就绪前不创建 Graph Run，且只有一个 coordinator 向共享控制根报告。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Python 与 SQLite 并发契约 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | 原生 worktree、分支恢复与后台 coordinator |

0.37.1 的候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；这是本轮验证目标，不是永久最低版本。两个宿主都必须确认 Controller 不执行 Git 写操作，且 secondary project setup 不会启动第二 coordinator。

## 0.37.0 发布候选矩阵

0.37.0 保持 29 个 MCP 工具。Python 全量测试、`compileall`、Skill/Plugin 镜像一致性、协议元数据、发布校验和 diff 检查是核心候选门禁；实际结果以对应候选提交和 CI 为准，不可替代真实宿主验证。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动 | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动 | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动 | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex 结果待回填 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Claude Code 结果待回填 | Claude PreToolUse、StopFailure 与子 Agent |

### 0.37.0 真实宿主验证基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.147.0 | 从待验证的 `delivery-graph` 0.37.0 Marketplace 包安装 | `pendingInteraction` 的 `DEVELOPMENT_BASELINE → EXECUTION_MODE` 顺序；dirty 内容或 index 变化使旧指纹失效；primary checkout 创建独立 worktree 项目任务；manual TASK、单仓手动漂移双分支、多仓漂移 fail closed；`SubagentStart`/`PreToolUse`；待用户确认状态 |
| Claude Code | 2.1.226 | `delivery-graph` 0.37.0 `--plugin-dir` 包及最终 Marketplace 安装 | 相同交互与 Git 漂移边界；普通 MCP 工具自动放行且敏感工具仍询问；`delivery-graph:delivery-coordinator` 在稳定 linked worktree 后台运行；Claude 专用 `PreToolUse`/`StopFailure`；receiver attestation 与 progress/heartbeat/result |

两个宿主都必须确认 Controller 不执行 Git 写操作；Codex 默认 Hook 清单不得包含 `StopFailure`，Claude manifest 必须指向独立的 `claude-hooks.json`。上述版本是候选目标，不是永久兼容承诺。

## 0.36.0 历史发布矩阵

以下内容记录 0.36.0 当时的发布与候选状态，不是 0.37.0 的现行能力说明。

源码发布事实：`main` 与 tag `v0.36.0` 指向提交 `ad19c33`；本地核心契约已完成 258 项 Python 测试、`compileall`、Skill/Plugin 镜像一致性和 `validate_release`（29 个 MCP 工具）校验。上述事实不包含模型账户、Keyring、Hook 信任、Marketplace 安装或原生子 Agent 的真实宿主验证。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex 结果待回填 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Claude Code 结果待回填 | Claude PreToolUse、StopFailure 与子 Agent |

发布管理员完成真实冒烟后，应在发布记录中填写准确宿主版本和结果；矩阵中的“CI 自动”不等于已经验证模型账户、Keyring、Hook 信任或原生 Agent 容量。

当前矩阵只验证可信外层 receiver：Claude 宿主的 claim 必须来自受认证的 `claude-code` receiver，Codex 宿主的 claim 必须来自受认证的 `codex` receiver，ZCode 宿主的 claim 必须来自受认证的 `zcode` receiver。PATH 中存在的 CLI 或 Loop 内 Worker 不能取得 Graph 控制面权限。新增外层供应商 Adapter 后必须作为独立矩阵维度验证，不能复用内部 Worker 成功记录宣称支持。

## 0.36.0 历史真实宿主验证基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.146.0 | 从待验证的 0.36.0 Marketplace 包安装 | `DEVELOPMENT_BASELINE → EXECUTION_MODE` 原生选择器顺序与同 Delivery Revision 偏好复用、primary checkout 自动创建 worktree 项目任务且不切换 `main`/`master`、manual TASK 接入、`SubagentStart`、receiver mutation Hook、当前宿主继承策略、待用户确认状态 |
| Claude Code | 2.1.220 | 0.36.0 `--plugin-dir` 发布包及最终 Marketplace 安装 | `DEVELOPMENT_BASELINE → EXECUTION_MODE` 原生选择器顺序与同 Delivery Revision 偏好复用、普通 MCP 工具由 Skill `allowed-tools` 自动放行且敏感 Hook 仍询问、自动 Delivery 在稳定 linked worktree 启动后台 coordinator 且主会话仅监控、PreToolUse Hook 注入工作区 attestation 并同会话续接、manual TASK 接入、receiver attestation、progress/heartbeat/result、StopFailure 兼容 |

上述版本是 0.36.0 当时的真实宿主验证目标，不是永久兼容承诺；文档未记录它们对 0.36.0 的实测通过结果。该版本尚未把脏工作树纳入基线前置交互，`start_manual_handoff` 的 Git 漂移阻断重确认也仍是后续 Phase 2；这些限制已由 0.37.0 的现行契约取代。宿主升级后若 Hook 事件字段、Plugin manifest 或 MCP 工具命名发生变化，应先在自托管 Runner 重跑真实宿主冒烟，再更新矩阵。

## Receiver 扩展边界

Graph 只信任 Plugin 注册的外层 Adapter。内部 helper 对 Controller 不可见，不进入
reservation、decision fingerprint、claim、进度面板或 outcome 专用字段。要让新的宿主
直接领取 Graph，必须增加并验证对应 Adapter、workspace 映射和独立 receiver 编排。

## 支持状态定义

| 状态 | 含义 |
|---|---|
| 已验证 | 当前版本、当前平台真实完成对应门禁 |
| 核心契约通过 | Controller 与 MCP 合约通过，但未启动真实模型宿主 |
| 候选验证中 | 已登记宿主，尚未完成发布候选真实冒烟 |
| 不支持 | 缺少所需 Plugin、Hook、MCP 或原生 Agent 能力 |

团队对外说明只能使用已经取得的状态；不得把 PATH 中存在某个 CLI 写成“真实宿主已验证”。
