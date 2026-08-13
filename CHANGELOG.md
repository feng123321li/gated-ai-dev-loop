# 版本更新记录

本文档汇总 `delivery-graph` 各正式版本的主要变化；0.10.0–0.36.0 的 canonical 名为 `layered-delivery`。版本边界以项目清单中的版本号和对应 Git 提交为准；同一版本发布前的连续改动合并记录在该版本下。

后续发布新版本时，应在版本提交中同步更新本文档，按“最新版本在前”的顺序记录发布日期、发布提交、核心能力、兼容性或迁移影响以及主要验证结果。

## 0.39.21 — 2026-08-13

发布提交：以 tag `v0.39.21` 指向的提交为准

- **Skill Hint 软触发增强**：保持 `root.skillHints` 建议性、可跳过且不形成成功门禁；自动 assignment、手动 TASK action、manual handoff 与 `loop_context` 现在传递具体 catalog 名和原生触发提示。Codex receiver 获得 `$skill-name`，Claude Code receiver 获得 Skill tool 文案，其他宿主使用自己的原生 Skill 入口；仅在当前 Loop 适用且宿主可用时尽量触发。
- **TASK 切分完整性预检**：规划层在候选 hierarchy preview 或局部 refreeze 前执行阻断式 L0 独立可验证性检查；删除、改名、移动及公共签名变更进一步触发授权项目范围内的 L1 定向符号引用分析，要求破坏性变更与最后一个引用更新归入同一 TASK，分析不明时保守调整边界。
- **需求修订 reservation 门禁**：`unfreeze_task_requirement` 与 `refreeze_task_requirement` 先清理到期 reservation，再拒绝任何仍有未领取 assignment 的 Graph Run，并返回完整冲突列表和最早 `retryAfter`，避免旧 assignment 与新 requirement 指纹并存。
- **兼容性**：保持 schema v3、33 个 MCP 工具、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`；Skill Hint 不查询 catalog、不保存激活凭据，也不因提示未使用而使 Loop 失败。
- **验证**：新增 receiver 提示传播、软触发非门禁、TASK 切分预检与需求修订 reservation 竞态回归；本地 Python 全量 402 项完成（401 通过、1 项按环境跳过），重新构建 canonical/Plugin 生成镜像，并完成 `compileall`、release、Skill/Plugin 与差异校验。

## 0.39.20 — 2026-08-13

发布提交：以 tag `v0.39.20` 指向的提交为准

- **ZCode MCP 启动路径与容错**：ZCode 启动 Plugin MCP 时 cwd 不保证是 Plugin 根，旧相对脚本路径会在协议请求前直接失败为 `Connection closed / 0 tools`；manifest 现以 `${ZCODE_PLUGIN_ROOT}` 同时锚定 `cwd` 和 `hdg_mcp.py` 绝对路径，以 ZCode 原生 `${ZCODE_PROJECT_DIR}` 独立提供业务工作区，不再使用 Claude 兼容别名。`_resolve_project_root` 在该项目根模板未展开或为空时仍提供启动期回退，避免 `PROJECT_ROOT_INVALID` 早退。
- **MCP 生命周期可观测性**：stdio server 以结构化 stderr 发布启动、modern discovery/list、legacy initialize、EOF 与响应投递失败阶段，并给出不含业务 payload/路径的可读诊断。新增只读注册矩阵探针，按 workspace/session/Agent role 区分完整、缺失、部分和不可观察目录，且不调用模型、MCP 或 `scheduler.db`。
- **会话外动态目录参考**：新增 `EXTERNAL_SUPERVISOR_PER_TURN` Demo，演示宿主 registry 在 Agent 之外重连，完整目录原子发布到下一模型 turn，活动 turn 快照不漂移，新 child 获取最新目录，部分注册 fail closed；宿主内置健康诊断与真正热重连仍是平台 P0，不由未挂载 Plugin 自证。
- **确定性保障档与 LIGHT 减负**：新增只读 `recommend_assurance_profile`，从显式范围、风险和验证事实按 `assurance-v1` 返回 LIGHT/STANDARD，工具总数增至 33。短时 LIGHT 可在 claim 建立的初始租约内省略 heartbeat/progress，仍保留基线冻结、定向验证、`workspace-changes.patch` 和用户最终确认，并增加 5 分钟 Quickstart。
- **协议边界**：继续优先使用最新正式 MCP `2026-07-28` 无状态 `server/discover` 路径并保留 legacy initialize 回退。动态重连采用每-turn typed catalog，不引入削弱逐工具 schema、审批与 allowlist 的通用 `mcp_call` 代理。
- **验证**：新增根解析、生命周期、注册矩阵、动态目录、保障档和 LIGHT 租约回归；本地 Python 全量 396 项完成（395 通过、1 项按环境跳过），重新构建 canonical/Plugin 生成镜像，并完成 `compileall`、release、Skill/Plugin 与差异校验。

## 0.39.19 — 2026-08-13

发布提交：以 tag `v0.39.19` 指向的提交为准

- **Codex MCP 握手瘦身**：Codex 宿主改用 821 字节的紧凑 Server Instructions，完整通用说明继续供其他宿主使用；32 个工具及 schema v3 保持不变，工具目录为 138,761 字节并增加 144 KiB 上限门禁，避免宿主把约 16 KiB 公共说明重复注入每个工具后放大注册负担。
- **工具审批边界锁定**：Skill 继续只显式列出 Claude Plugin 命名空间下的 25 个安全工具，不增加 Codex aliases，也不使用会自动放行归档、取消、重建和需求解冻等敏感工具的 MCP 通配符；新增回归测试锁定该边界。
- **测试套件精简**：移除 21 个重复或 linked-worktree 专用场景，删除已退出产品面的 manual baseline reconfirmation worktree 测试组，并把非 Git 策略与终态断言下沉为快速控制器测试；必要的分支绑定、dirty/unmerged 状态、提交门禁、历史改写和证据快照仍使用真实 Git 验证。
- **验证**：本地 Python 全量 371 项测试完成（370 通过、1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Skill、Claude Plugin 与 `git diff --check` 均通过。

## 0.39.18 — 2026-08-13

发布提交：以 tag `v0.39.18` 指向的提交为准

- **Review 职责单一化**：严格分离 Controller、独立 Review receiver 与最终用户确认。Controller 只负责 Graph 前驱成功门禁、Review result 契约校验和事件/SQLite/投影持久化；技术验收、证据充分性与 finding 闭环由对应 receiver 独立负责。
- **分层验收收敛**：TASK Review 只验本 TASK，GROUP Review 改为按真实直接子项 seam 可选，Delivery Acceptance/Readiness 每个 `STANDARD` Delivery 只执行一次并聚焦顶层需求覆盖、整体集成/E2E 证据、运行准备度和全局风险；`LIGHT` 继续由唯一 TASK 直接进入用户确认。
- **结果与投影精简**：成功 Review 只持久化共同字段、本层唯一结论和有界证据，不复制 `upstreamLoopResults` 或下层 result body；未配置 GROUP Review 时不生成 Graph 节点、SQLite run/event/outcome 或空投影段落。验收投影明确展示 Controller、Delivery receiver 与用户的职责边界。
- **内部模块边界**：新增独立 `review_contracts.py`，把 Review 结果结构与声明终态一致性校验从通用 Loop 合约中拆出，并以机器可读执行策略公开责任归属。
- **验证**：本地 Python 全量 392 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Skill、Codex/Claude Plugin 与 `git diff --check` 均通过。

## 0.39.17 — 2026-08-13

发布提交：以 tag `v0.39.17` 指向的提交为准

- **Marketplace 图标瘦身**：将 Delivery Graph 品牌图标从 1254×1254、1.3 MB 的生成原图缩放为 256×256、约 76 KB 的发布资源，视觉内容和 manifest 路径保持不变，显著降低 Marketplace 清单与 Plugin 安装包负担。
- **资源回归门禁**：新增 PNG 头、尺寸和文件大小断言，要求发布图标固定为 256×256 且不超过 128 KB，防止后续误把高分辨率生成稿重新打入 Plugin。
- **运行契约不变**：本版本仅优化静态展示资源并增加测试，不改变 32 个 MCP 工具、schema v3、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`。
- **验证**：本地 Python 全量 389 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Skill/Plugin manifest 与 `git diff --check` 均通过。

## 0.39.16 — 2026-08-13

发布提交：以 tag `v0.39.16` 指向的提交为准

- **ZCode Plugin 图标**：`.zcode-plugin/plugin.json` 的 `interface` 同步声明 `composerIcon` 与 `logo`，指向 0.39.15 引入的浅色 Delivery Graph 品牌图标；ZCode 宿主现在与 Codex 一致地渲染 Plugin 图标。
- **运行契约不变**：本版本仅补齐 ZCode manifest 的展示资源字段，未改变 32 个 MCP 工具、schema v3、无 Hook 模式与 `CURRENT_WORKSPACE_SERIAL`，Graph、运行数据库或宿主审批语义均不变。
- **验证**：本地 Python 全量测试、`compileall`、canonical/Plugin 生成镜像、release candidate、Skill/Plugin manifest 与 `git diff --check` 均通过。

## 0.39.15 — 2026-08-13

发布提交：以 tag `v0.39.15` 指向的提交为准

- **Plugin 自定义图标**：新增浅色 Delivery Graph 品牌图标，并通过 Codex manifest 的 `composerIcon` 与 `logo` 字段发布；浅天蓝背景、分层 Graph、门禁检查点与审批节点在紧凑视图中保持可辨识。
- **运行契约不变**：本版本仅调整 Plugin 展示资源与发布元数据，继续保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，不改变 Graph、数据库或宿主审批语义。
- **验证**：本地 Python 全量 388 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Skill/Plugin manifest 与 `git diff --check` 均通过。

## 0.39.14 — 2026-08-12

发布提交：以 tag `v0.39.14` 指向的提交为准

- **自动 Delivery 持久队列**：`AUTOMATIC` 选择在当前 workspace 已有调度 owner 时持久标记为 `QUEUED`，返回队列位置、owner 与无需再次确认的 continuation；根概览和 Delivery 概览统一显示“排队中（等待自动调度）”。前序 Delivery 达到可验证 commit、working tree/index clean、HEAD 未漂移且 receiver 安全释放边界后，队首自动续调。
- **脏工作区机械准备**：选择自动执行同时授权宿主在队首精确复核 dirty fingerprint，stash tracked、staged 与 untracked 业务改动并排除 `.layered-delivery/**`，再创建或切换独立 Delivery 分支并调用 `resume_execution_mode`。未合并冲突保持等待，且绝不 stash 正在运行 owner 的未完成改动。
- **手动交接边界**：手动冻结继续持久化 Delivery、不可变 Revision、完整 hierarchy、双 fingerprint 和人类投影，状态保持 `HANDOFF_READY`；它不进入自动队列，也不创建 Graph Run 或 workspace binding，接收方显式调用 `start_manual_handoff` 后才尝试取得串行 turn。
- **当前 workspace 串行化**：清理残留的 worktree 编排命名和并行入口；primary 与既有 linked checkout 统一作为普通 current workspace，每个 Delivery 使用独立分支，同一物理 checkout 一次只推进一个 Delivery。
- **验证**：本地 Python 全量 388 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Skill/Plugin manifest 与 `git diff --check` 均通过。

## 0.39.13 — 2026-08-12

发布提交：以 tag `v0.39.13` 指向的提交为准

- **ZCode MCP 注册修复**：修正 0.39.12 的宿主绑定假设。ZCode 宿主不会像 Codex 那样在 MCP 请求 `_meta` 注入 sandbox 元数据，`--project-root-from-meta` 模式导致每个请求都返回 `PROJECT_ROOT_UNAVAILABLE`。`.zcode-plugin/plugin.json` 改为显式 `HDG_PROJECT_ROOT=${CLAUDE_PROJECT_DIR}`（ZCode 对 plugin 提供的 MCP server 支持该模板变量），与 Claude 侧 `.mcp.json` 的根解析方式一致；Codex 与 Claude manifest 不受影响。
- **真实验证**：按 ZCode 启动方式（plugin 根为 cwd、`HDG_HOST_ADAPTER=zcode`、`HDG_PROJECT_ROOT` 指向工作区）端到端拉起 `hdg_mcp.py`，`initialize` 握手成功，`workspace_status` 项目根解析恢复正常；在 `delivery-graph` 源码仓库内按预期触发自托管防护 `SELF_HOSTING_DOGFOOD_REQUIRED`（未显式 `--dogfood` 不产生运行包）。
- **验证**：本地 Python 全量 383 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Skill/Plugin manifest 与 `git diff --check` 均通过。

## 0.39.12 — 2026-08-12

发布提交：以 tag `v0.39.12` 指向的提交为准

- **ZCode 独立 Plugin manifest**：新增 `plugins/delivery-graph/.zcode-plugin/plugin.json`，以 `HDG_HOST_ADAPTER=zcode` 独立注入 Adapter 身份，不再借用 `.mcp.json` 的 `claude-code` 身份。ZCode 与 Codex 一样从请求 `_meta` 解析项目根（`--project-root-from-meta`），不依赖 `${CLAUDE_PROJECT_DIR}`。
- **边界明确**：ZCode 原生支持 `AskUserQuestion`，与 Claude Code 共用同一交互选择器映射；敏感工具审批策略与 Codex manifest 一致。ZCode 不继承 Codex Desktop 的 Dashboard legacy bridge，非 Codex Adapter 的只读 Dashboard 请求继续 fail closed。
- **核心契约**：本版本只确认 manifest 与核心契约一致，不宣称 ZCode 真实宿主已完成原生 child 冒烟；真实 ZCode 宿主冒烟候选验证中，结果待回填。
- **验证**：本地 Python 全量测试、`compileall`、canonical/Plugin 生成镜像、release candidate、Skill/Plugin manifest 与 `git diff --check` 均通过。

## 0.39.11 — 2026-08-12

发布提交：以 tag `v0.39.11` 指向的提交为准

- **Python 3.10 真实兼容**：移除验收投影中的 Python 3.12+ f-string 语法，源码、生成 Skill 与 Plugin payload 均可由 CPython 3.10.19 编译和导入。
- **入口版本边界**：vendored stdio MCP 在导入 Controller 前检查 Python 版本；低于 3.10 时以稳定的 `PLUGIN_PYTHON_UNSUPPORTED` 错误退出，避免落入难以诊断的语法或导入错误。
- **Adapter 契约一致性**：补齐现有受信任 `zcode` Adapter 的 native receiver、交互工具、同控制根监控与容量键映射，并以集合不变量测试防止可信 Adapter 缺少 capacity key。此版本不新增独立 ZCode Plugin manifest，也不把核心契约测试表述为真实宿主冒烟通过。
- **验证**：CPython 3.10.19 与 3.14.6 均通过全量 383 项测试（各 1 项按环境条件跳过）和 `compileall`；canonical/Plugin 生成镜像、release candidate、Skill/Plugin validator 与 `git diff --check` 均通过。

## 0.39.10 — 2026-08-12

发布提交：以 tag `v0.39.10` 指向的提交为准

- **事件优先等待**：Graph 监控改为优先等待宿主原生 receiver 事件，并按首次心跳、心跳/进度陈旧阈值、租约和资源恢复时间设置稳定 deadline；无变化时不再每 10 秒滑动轮询，也不重复输出完整进度。
- **空转与截止修复**：只读状态刷新不推进 Graph；无状态变化的 frontier 不再更新时间或重写投影。统一租约与 reservation 的截止边界，并修复已 CLAIMED reservation 的旧短 TTL 长期落在过去、诱发 `graph_frontier` 自旋的问题。
- **按影响范围验证**：TASK 声明受影响路径并产出绑定 workspace 状态的结构化验证证据；TASK、GROUP 与 Delivery Review 可独立审查并复用仍为 `PASSED + EXACT_MATCH` 的证据，只对缺口、相关变更或高风险边界定向复跑。
- **证据安全与性能**：Review 提交时重新校验证据新鲜度，相关代码变化会使复用失败关闭；scope 快照按项目批量捕获和去重，上游大 diff 使用紧凑引用，避免验证优化反而产生大量 Git 子进程和上下文膨胀。
- **验证**：本地 Python 全量 381 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Claude Plugin manifest 与 `git diff --check` 均通过。

## 0.39.9 — 2026-08-12

发布提交：以 tag `v0.39.9` 指向的提交为准

- **worktree setup 清理完成**：删除自动 linked-worktree setup 的 reservation、进度上报、租约、SQLite 表与公开 Repository/MCP 路径，并移除 Claude `delivery-coordinator` Agent；既有 linked checkout 仍只作为普通 current workspace 使用。
- **调度与存储优化**：为 hierarchy 与数据库变更契约增加有界资源限制，READY 刷新改用轻量状态快照和每节点最新 attempt，常用 run、lease、event 与 dispatch reservation 查询增加索引。
- **兼容性修复**：同 state contract 的既有 hierarchy 不会因新增资源上限而失去可读性；既有 scheduler 数据库会在契约校验通过后幂等补齐兼容索引；重试节点不会再因查询计划变化误读旧 attempt 而永久停在 `PENDING`。
- **宿主协议同步**：Claude 官方冒烟在当前 checkout 按冻结 `gitBinding` 准备分支并调用 `resume_execution_mode`，随后由主会话规划并派遣独立 receiver；文档和测试不再依赖旧 `hostDispatch`、后台 coordinator 或新建 linked worktree。
- **验证**：本地 Python 全量 371 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Claude Plugin manifest 与 `git diff --check` 均通过。


## 0.39.8 — 2026-08-11

发布提交：以 tag `v0.39.8` 指向的提交为准

- **多 Delivery workspace**：同一物理 checkout 可绑定多个 Delivery，Graph、Revision、Run 与验收继续按显式 `rootId` 隔离；无参状态遇到多个未结束绑定时返回 `DELIVERY_SELECTION_REQUIRED`，未绑定草稿只能按创建响应中的 `rootId` 恢复。
- **当前 workspace 串行执行**：公开执行策略统一为 `CURRENT_WORKSPACE_SERIAL`，用户选择仍只有 `AUTOMATIC` / `MANUAL`。删除公开 `report_worktree_setup` 与自动 linked-worktree 创建、reservation、host dispatch 路径；已存在 linked checkout 只视为普通 current workspace。
- **提交与冲突门禁**：选择、workspace 绑定和排队在同一 `BEGIN IMMEDIATE` 事务完成；coordinator 与 secondary `READ_WRITE` checkout 都只能由一个 Delivery 持有。前序 Run 必须终态、取消 receiver 租约结束、存在 turn-start 之后的非空业务 commit、历史未改写且工作树/index 干净，后序才可切分支并 resume；错分支、dirty、HEAD/scope 漂移与 FROZEN 重派遣全部失败关闭。
- **验收可见性**：Controller 从已验证的可写 Git scope 捕获 committed、staged、unstaged 与 untracked 变化，持久化有界快照，并在主控制目录生成由 `acceptance.md` 相对链接的 `workspace-changes.patch`，无需先 commit 或打开实际 checkout 即可审核。
- **协议与文档**：MCP 工具面收敛为 32 个；Skill、双宿主 Plugin、执行/验收 references、团队运维与兼容矩阵全部同步串行语义。删除两个已废弃的 linked-worktree 正向测试。
- **验证**：本地 Python 全量 369 项测试通过（1 项按环境条件跳过）；`compileall`、canonical/Plugin 生成镜像、release candidate、Claude Plugin manifest 与 `git diff --check` 均通过。


## 0.39.7 — 2026-08-11

发布提交：以 tag `v0.39.7` 指向的提交为准

- **看板刷新恢复**：Codex legacy MCP Apps 连接只在此前成功、带 sandbox metadata 的 `open_delivery_dashboard` 调用后，为同一连接、同一 `root_id` 记录只读 workspace grant。内嵌 `tools/call` 缺失 metadata 时可复用该精确 grant；未授权 root、显式空/畸形 metadata、Modern 请求、非 Codex Adapter、其他只读工具和全部写工具继续 fail closed。
- **桥接兼容与自动更新**：标准 MCP Apps `tools/call` 发生传输失败或精确返回 `PROJECT_ROOT_UNAVAILABLE` 时，可一次回退 `window.openai.callTool`，业务错误不重复调用。看板可见时每 15 秒串行自动刷新，隐藏时暂停，手动按钮仍可立即读取；刷新始终只调用只读 Dashboard 工具。
- **响应式 Graph**：移除固定 190px 列、`min-width:max-content` 和内部横向滚动。宽面板按 rank 横向布局并绘制依赖曲线；空间不足时按 rank 纵向换行，在节点内显示前置项，避免移动端连线穿过卡片或丢失依赖语义。
- **缓存隔离**：Dashboard Resource 升级为 `ui://delivery-graph/dashboard-v2.html`，UI 版本升至 1.1.0，避免 Plugin 更新后继续命中旧的一小时资源缓存。MCP 工具数、schema v3、Graph、事件链和运行数据库均不变。
- **验证**：本地 Python 全量 342 项测试通过（1 项按环境条件跳过）；内嵌 JavaScript 与 Python 编译、canonical/Plugin Skill 镜像、Claude Plugin 严格校验、release candidate 和差异校验均通过。真实 Edge 在 1280/900/600/360 四档宽度执行 Dashboard，Graph 与页面无水平溢出，节点未裁切，并同时覆盖横向连线和纵向前置项布局。

## 0.39.6 — 2026-08-11

发布提交：以 tag `v0.39.6` 指向的提交为准

- **无 Hook Plugin**：Codex 与 Claude manifest 不再声明生命周期 Hook，删除完整 Hook 目录、`/hooks` 信任步骤、Claude 429 StopFailure 处理和宿主 transcript/session attestation。
- **统一派遣路径**：删除 `claim_current_task`。AUTOMATIC 的 TASK、TASK Review、GROUP Review 与 Delivery Review 全部由 `plan_dispatch_batch` 预留，再由独立 receiver 使用 reservation、decision fingerprint、receiver context 与显式 `operation_id` 调用 `dispatch_loop`。
- **能力边界**：Graph/attempt/fingerprint 新鲜度、workspace/project scope、reservation、资源锁、lease 与 operation mutation 门禁保留；不再密码学证明真实宿主 session、parent-child 或独立 reviewer 身份。
- **持久化精简**：新建 scheduler 状态不再创建四张 attestation/receiver identity 表；Graph compiler 契约仍为 `schema-v3-graph-compiler-v1`。旧 0.39.5 状态中的同名表不再读取或写入；READY、从未 claim 且没有 reservation 的 Graph 无需迁移即可按新流程继续。
- **工具审批**：Codex 敏感工具继续使用 manifest `prompt`；Claude Skill 用非敏感工具白名单替代 MCP wildcard，敏感操作与最终确认交给宿主逐次审批。MCP 工具面回到 33 个。
- **发布验证**：336 项 Python 测试通过（1 项 skipped），并通过 `compileall`、canonical/Plugin Skill、Claude Plugin、release candidate 和差异校验。

## 0.39.5 — 2026-08-11

发布提交：以 tag `v0.39.5` 指向的提交为准

- **Repository 持久化边界**：执行模式/worktree setup、hierarchy/revision/run 生命周期、Graph event 状态、dispatch reservation/receiver identity 与人类投影分别迁移到职责独立的 store；加上已有 workspace binding 和 host attestation store，`SchedulerRepository` 收缩为 SQLite 连接/事务、共享定义校验与兼容 facade，公开方法签名与 schema v3 均保持不变。
- **架构回归门禁**：新增 Store 所有权与 facade 签名一致性测试，并限制 `repository.py` 少于 1,800 行，避免持久化职责重新集中到单文件。
- **验证**：相关 242 项行为与架构测试通过；本地 Python 全量 369 项测试通过（1 项按环境条件跳过），并完成 compileall、UTF-8 Skill 校验、34 工具生成一致性校验、Claude Plugin manifest 校验与 `git diff --check`。

## 0.39.4 — 2026-08-10

发布提交：以 tag `v0.39.4` 指向的提交为准

- **Desktop 当前任务认证回退**：`claim_current_task` 加入顶层 Codex Desktop 的 PreToolUse attestation。当可信 Plugin Hook 已加载但 Desktop 未触发 `SessionStart` 时，可在领取当前 READY TASK 的工具调用中补发受 session、workspace、Graph、项目 scope 与时效约束的 capability；Review 仍必须使用独立 `SubagentStart` receiver。
- **稳定 workspace identity**：Git Delivery 不再使用 worktree 或 `.git` 目录的绝对路径哈希，而使用 Git 历史根提交组成的 repository lineage 与冻结分支生成 key。仓库或同分支 worktree 删除、移动、重建后可以恢复原 Delivery，不同分支仍 fail closed；已有绝对路径绑定在原路径首次访问时原子升级，不增加 schema 迁移入口。
- **Repository 职责拆分**：workspace 绑定/发现以及宿主 attestation 持久化分别迁移到独立 store，`SchedulerRepository` 保留兼容 facade，降低单文件承载并保持外部 API 不变。
- **兼容性**：继续只维护 schema v3 和 34 个 MCP 工具。Hook 内容变更后需审查一次新哈希；Desktop 不保证弹出信任窗口，普通执行审批也不等于 Hook trust。
- **验证**：本地 Python 全量 363 项测试通过（1 项按环境条件跳过），并完成 compileall、UTF-8 Skill 校验、34 工具发布一致性校验、Claude Plugin manifest 校验与 `git diff --check`。

## 0.39.3 — 2026-08-10

发布提交：以 tag `v0.39.3` 指向的提交为准

- **Codex 当前会话执行 TASK**：AUTOMATIC Delivery worktree 任务由可信 `SessionStart` Hook 绑定精确 Codex session、workspace、Graph 与项目 scope；READY `TASK_LOOP` 通过新增 `claim_current_task` 在同一 AUTOMATIC 模式内直接由当前会话实现，不再为实现 TASK 启动 Subagent。
- **Review 独立性保留**：`claim_current_task` 硬拒绝 `TASK_REVIEW_LOOP`、`GROUP_REVIEW_LOOP` 与 `DELIVERY_REVIEW_LOOP`。Review 继续由 `plan_dispatch_batch` 预留并通过独立原生 receiver 与 `SubagentStart` attestation 执行；上游为 Hook-attested current-session TASK 时，允许其只创建新的 Review child，但 receiver context 必须不同。
- **Code-mode Hook 兼容**：顶层 Delivery task 的 `SessionStart` 发放 `DELIVERY_COORDINATOR` capability，AUTO Review 的 `SubagentStart` 只发放 `LOOP_RECEIVER` capability；两者都有时效、可轮换且数据库只保存哈希。普通 subagent 不取得 coordinator role，Review receiver 不能领取 TASK 或规划下一批。planning、claim、heartbeat、progress、pause 和 result 都由 Adapter 校验 capability、role、session 与可信 workspace 后解析 operation。即使 Codex 0.147.0 未对 code-mode 内嵌 MCP 触发 nested PreToolUse，也不需要切换模型 tool mode 或配置 `model_catalog_json`。
- **Hook 信任体验**：用户可在 `/hooks` 选择始终信任当前精确 Hook 定义；相同内容哈希在后续任务中不再弹窗，只有 Plugin 升级改变 Hook 内容后重新审查。普通执行权限不会自动信任未来 Hook 版本。
- **兼容性**：继续只维护 schema v3，不迁移现有 scheduler 数据；复用现有哈希 attestation 存储。MCP 工具面增加到 34 个。Claude 与 MANUAL child 流程保持原有 receiver attestation 语义。
- **验证**：本地 Python 全量 356 项测试通过（1 项按环境条件跳过），并完成 compileall、UTF-8 Skill 校验、34 工具发布一致性校验、Claude Plugin manifest 校验与 `git diff --check`。系统通用 Plugin validator 仍使用拒绝 `hooks` 字段的旧 schema，与当前 Codex Hook manifest 契约不兼容，因此未作为本版本的发布门禁。

## 0.39.2 — 2026-08-10

发布提交：以 tag `v0.39.2` 指向的提交为准

- **AUTO/MANUAL 统一 receiver 身份链**：两种模式都必须由当前可信宿主 Adapter 为真实原生 child 签发并一次性消费 receiver attestation，再进入相同的 claim、项目 scope、operation、heartbeat、progress、pause 和 result 授权链。AUTO attestation 必须绑定非空 dispatch reservation；MANUAL attestation 的 reservation 固定为 `NULL`，其授权来源是完整 manual Graph 或指定自动 TASK 的显式人工接管事件，而不是较弱的无认证 claim。
- **Codex Hook 激活前置门禁**：Codex manifest 现在显式声明 `./hooks/hooks.json`。安装或升级后必须在新任务的 `/hooks` 中审查并信任该 Plugin 的 Hook；`plan_dispatch_batch` 的 PreToolUse preflight 会签发一次性宿主工作区证明，Hook 未加载、未信任或未注入证明时，Controller 在创建任何 reservation 前返回 `SCHEDULER_HOST_HOOK_NOT_READY`。
- **真实 child transcript 绑定**：Codex AUTO 的 `SubagentStart` 和 MANUAL 的 `dispatch_loop` PreToolUse 都只接受宿主可信 `.codex/sessions` 根内的真实 child transcript，并精确核对 child、parent、角色、task/reservation 与 Delivery workspace。模型输入、root/helper、内部 Worker、自定义 `CODEX_HOME` 和伪造 transcript 不能取得 receiver 或 operation 权限。
- **单仓运行 scope 修复**：未显式声明 `delivery.projectScopes` 的普通单仓 Delivery 会从顶层 `delivery.gitBinding` 与已绑定工作区合成并验证唯一 `primary` runtime scope；AUTO claim 与 `loop_context.projectScopes` 不再返回空数组。多仓冻结规则保持不变，仍要求每个 scope 显式完整 binding。
- **启动失败立即释放**：Codex `SubagentStart` 已定位 reservation 但身份、workspace 或 scope attestation 失败时，在 TASK 仍为 READY 且尚未签发 receiver 身份的前提下立即把该 reservation 标记为过期，并记录 `DISPATCH_RECEIVER_START_FAILED`；child 收到只报告协调器、不得检查或修改仓库的稳定错误上下文，不再等待 300 秒 TTL。
- **兼容性**：继续只维护 schema v3，MCP 工具面保持 33 个；新增行为属于宿主 Adapter、Hook 和运行时 scope 校验收紧，不增加旧协议旁路。
- **验证**：本地 Python 全量 351 项测试通过（1 项按环境条件跳过），并完成编译、Skill/Plugin 生成镜像、发布门禁、Claude Plugin 与差异校验。

## 0.39.1 — 2026-08-10

发布提交：以 tag `v0.39.1` 指向的提交为准

- **Codex Desktop transcript 竞态修复**：`SubagentStart` 事件可能先于 child transcript 的首条 `session_meta` 落盘；当事件路径已精确指向当前 child 时，Hook 现在以 50ms 间隔有界等待最多 2 秒，再完成 parent/child/task/reservation 校验与原子 claim。
- **安全边界不变**：等待只适用于文件名绑定当前 `receiver_context_id` 且位于宿主可信 `.codex/sessions` 根的 transcript；parent transcript 仍使用原 sibling 查找，自定义 `CODEX_HOME`、错误角色、错误任务名和过期 reservation 继续静默拒绝。
- **真实故障证据**：Codex Desktop 0.147.0-alpha.6.5 的失败会话在 child 启动后约 70ms 才写入 `session_meta`，0.39.0 因读取到空 transcript 未 claim；Loop 未读取或修改业务仓库，reservation 到期后安全回到 `READY`。
- **验证**：新增直接 child transcript 延迟写入回归；本地 Python 全量 342 项测试通过（1 项按环境条件跳过），并完成编译、Skill/Plugin、发布与差异校验。

## 0.39.0 — 2026-08-10

发布提交：以 tag `v0.39.0` 指向的提交为准

- **数据库 baseline 门禁**：涉及建表、改表或删表的 TASK 必须在 preview 前声明结构化 `payload.databaseChanges`，完整冻结表级 before/after、全部字段、主键、唯一约束、索引、外键，以及正向迁移、回滚、回填、发布兼容和验证要求；每项资源锁必须与 TASK `resourceClaims` 精确对应，数据库变更强制使用 `STANDARD`。
- **执行边界**：数据库 TASK Loop 只应用和验证冻结 after，不再承担表结构设计；任何必要偏离返回 `REPLAN_REQUIRED`，由同一 Delivery 的新 Revision 重新展示和确认 baseline。
- **Stacked Delivery 基线**：primary 位于干净 feature 分支时，开发基线交互新增并默认推荐 `NEW_FROM_CURRENT_BRANCH`。Controller 冻结新的子分支名、父 feature HEAD 以及以父 feature 为 `baseRef/integrationTarget` 的 binding，AUTOMATIC 直接创建独立子分支 worktree，无需先把 primary 切回 main/master；dirty primary 不提供该路径。
- **Codex Desktop attestation 修复**：Windows Hook 运行在 `CodexSandboxOffline` 隔离账户时，改由宿主 `USERPROFILE` 与生命周期事件 transcript 路径共同验证真实 `.codex/sessions` 根；继续拒绝自定义 `CODEX_HOME`，实际失败会话可恢复 child/parent/task 与 reservation 绑定。
- **未领取自动 TASK 人工恢复**：新增 `handoff_ready_automatic_task`。只有 active AUTOMATIC Graph 中 READY、当前 attempt 从未领取、无有效 reservation、Delivery worktree 干净且用户确认无代码改动的 TASK 才能切换为 MANUAL receipt；Graph、Revision、基线和双 fingerprint 不变，AUTO 不再派遣该 TASK，后续 Review 仍自动执行。
- **人类投影**：投影模板升级到版本 16。每个数据库 TASK 生成 `database-changes.md` 索引和每表详情，Delivery/TASK baseline 与 MCP `humanArtifacts` 串联可审阅契约。
- **兼容性**：继续只维护 schema v3，不增加旧 schema 迁移入口；未声明数据库变更的现有 Delivery 不受影响。
- **协议面**：MCP 工具数由 32 增至 33。
- **验证**：本地 Python 全量 341 项测试通过（1 项按环境条件跳过）；`compileall`、canonical Skill/Plugin 镜像一致性、Claude Plugin 校验、发布校验与 `git diff --check` 均通过。

## 0.38.0 — 2026-08-09

发布提交：以 tag `v0.38.0` 指向的提交为准

- **只读 MCP Apps 运行看板**：新增 `open_delivery_dashboard` 与 `ui://delivery-graph/dashboard.html` Resource。看板展示当前 Delivery/Revision、真实 Graph 依赖、节点状态、活动 Loop、进度/心跳/租约告警和 Revision 元数据；不展示控制凭证，不读取本地文件或网络，也不提供调度写按钮。
- **无 Node 运行时**：UI 由随 Plugin 发布的自包含 HTML/CSS/原生 JavaScript 实现，MCP Server 继续只依赖 Python 3.10+ 标准库。无 UI 的宿主仍获得文字与 `structuredContent` 降级结果。
- **Adapter 单一分发路径**：Modern `2026-07-28` 与 Legacy `2025-11-25` 保留各自 discovery/initialize wire shim，初始化后的 tools/resources list/read/call 统一进入一个协议中立 dispatcher，避免双协议功能漂移。
- **只读边界**：Dashboard snapshot 只使用 `graph_status` 和只读 repository 查询，不调用会先执行 `advance_graph` 的 `graph_frontier`。刷新不会推进 Graph、回收租约或改变任何控制面状态。
- **协议面**：MCP 工具数增至 32；两代协议都广告静态 Resources，并支持 `resources/list`、`resources/read`。Modern 响应继续发布 complete/TTL/cache metadata，Legacy envelope 保持原形。
- **验证**：本地 Python 全量 327 项测试通过（1 项因当前解释器未安装离线 wheel 构建后端而明确跳过）；`compileall`、canonical Skill/Plugin 镜像一致性、bundled stdio Resource 冒烟、Claude Plugin 校验、双宿主本地 probe、发布校验、桌面/移动视觉 QA 与 `git diff --check` 均通过。

## 0.37.3 — 2026-08-08

发布提交：以 tag `v0.37.3` 指向的提交为准

- **完成 Delivery 显式归档**：新增 `archive_delivery`。只有当前 run 为 `COMPLETED` 时才能归档；hierarchy 与当前 Revision 标为 `ARCHIVED`，run、事件链、历史、详情投影、workspace binding 和 `requirementKey` 均保留。默认 `workspace_status` 与工作区根总览隐藏归档项，显式 `root_id` 继续提供审计查询。
- **生命周期失败关闭**：归档可安全幂等重放并修复投影；归档后的 cancel、freeze、manual handoff、preview 与 Revision 入口均明确拒绝。Codex manifest 与 Claude Hook 把归档纳入敏感操作审批。
- **存储契约收束**：既有 SQLite 库在 WAL 或 DDL 前只读校验 state contract；删除旧 schema 自动升级与兼容迁移入口。schema bootstrap/contract 校验提取到独立的 `storage_schema.py`，不引入迁移框架。
- **正确性与性能修复**：重复 freeze 复用同一连接，避免自锁；Graph event 分页改为索引范围查询并保持从零连续分页的完整链审计；意外内部错误输出脱敏、可关联的单行诊断日志，客户端只接收通用错误与 opaque diagnostic ID。
- **协议与验证**：MCP 工具数增至 31，投影模板版本增至 15。Python 全量 307 项测试、`compileall`、canonical Skill/Plugin 镜像一致性、发布校验与 `git diff --check` 通过。

## 0.37.2 — 2026-08-08

发布提交：以 tag `v0.37.2` 指向的提交为准

- **worktree setup 实时监控**：新增 `report_worktree_setup` MCP 工具。宿主在创建目录、分支、linked worktree、checkout 和验证阶段上报有界摘要、百分比与心跳；`workspace_status.worktreeSetup.progressMonitor` 在单仓和多仓中统一返回项目行、健康状态和告警，主仓建议每 10 秒刷新。
- **setup 租约与失败关闭**：每个 reservation/attempt 拥有 120 秒租约，按 30 秒间隔上报即可续租。超时进入 `WORKTREE_SETUP_LEASE_EXPIRED`，显式失败进入 `WORKTREE_SETUP_FAILED`；两种状态都阻止自动重发，避免旧宿主仍在运行时产生第二个创建者。
- **安全重试闭环**：`RETRY_CONFIRMED` 必须同时确认旧创建进程已经停止、半成品目录/worktree 已安全核对；Controller 随后在 SQLite 事务中只授予一个新 attempt。并发重试只有一个获得 `IMMEDIATE`，其余以 `SCHEDULER_WORKTREE_SETUP_ATTEMPT_STALE` 拒绝。
- **协议与验证**：MCP 工具数增至 30；新增 setup progress、心跳续租、过期阻断、失败阻断与并发 retry 回归覆盖。最终全量数量以候选提交的发布校验结果为准。

## 0.37.1 — 2026-08-08（未单独发布）

发布提交：未单独打 tag；本节能力合并随 `v0.37.2` 发布

- **worktree 创建去重**：AUTOMATIC 选择在同一 SQLite 事务中为每个项目记录 worktree setup reservation。同一 Delivery/Revision 的并发或重复选择只有一个 `IMMEDIATE` 派发，其余返回 `DO_NOT_REISSUE / WAIT_FOR_EXISTING_WORKTREE_SETUP`，避免同一路径并发创建和半成品目录。
- **仓库感知的分支排他**：reservation 以 Git common directory identity + `branchRef` 为键；同仓同分支跨 Delivery 原子拒绝为 `SCHEDULER_WORKTREE_BRANCH_RESERVED`，不同仓库仍可安全使用同名 feature 分支。显式 hierarchy binding 不再绕过排他检查。
- **精确宿主分支恢复**：`hostDispatch` 新增 `branchRef`、完整 `gitBinding`、repository/project identity 和派发状态。宿主生成其他 feature 分支时，干净 worktree 返回 `FROZEN_DELIVERY_BRANCH_REQUIRED`；已有改动返回 `FROZEN_DELIVERY_BRANCH_DIRTY` 并 fail closed。
- **多项目自动工作区**：每个 `READ_WRITE` Git scope 都进入 `projectWorktreeSetups`；全部项目 worktree 验证为 `READY` 后才允许 `resume_execution_mode` 创建 Graph Run。secondary scope 只准备 worktree，不启动第二 coordinator；所有项目继续向同一控制根报告进度。
- **验证**：新增 7 项 worktree setup 回归测试，覆盖真实并发重复选择、显式同分支冲突、不同仓同名分支、宿主错分支 clean/dirty 双分支，以及多项目完整恢复。最终全量数量以候选提交的发布校验结果为准。

## 0.37.0 — 2026-08-08

发布提交：以 tag `v0.37.0` 指向的提交为准

- **身份收束**：项目、Plugin 与 Skill 的 canonical 机器名从 `layered-delivery` 更新为 `delivery-graph`，展示名统一为“分层交付 Graph 控制面”；Claude coordinator namespace 同步为 `delivery-graph:delivery-coordinator`。安装或升级时需要按新 Plugin 名重新加载会话。项目运行数据目录继续使用 `.layered-delivery/`，已有 schema v3 Delivery 无需迁移或改名。
- **单一待办交互**：规划、恢复和手动接管统一以 `pendingInteraction` 暴露当前唯一等待用户处理的交互；先完成 `DEVELOPMENT_BASELINE`，再进入 `EXECUTION_MODE`。`developmentBaseline` 与 `executionChoice` 仅作为同一对象的兼容别名，宿主可调用原生选择器时不得改写为自由文本确认。
- **精确 dirty 确认**：缺少 Git binding 的脏工作树同样进入开发基线确认。`workingTree.stateFingerprint` 同时覆盖 porcelain 状态、变化路径的 worktree blob 与 index state；内容或暂存状态变化都会使旧确认失效，`.layered-delivery/**` 仍不计入业务 dirty 状态。
- **手动交接 Git 漂移闭环**：`start_manual_handoff` 在任何 workspace、binding 或 Run 写入前检测漂移。单仓返回 `DEVELOPMENT_BASELINE` 重确认：若选择恢复为原 binding，则保留当前 Revision 并要求恢复分支后重试；若确认新的 binding，则生成下一不可变手动 Revision 和新双 fingerprint。多仓漂移因单工作区选择器无法完整改写全部仓库而 fail closed，要求带完整 project bindings 创建手动 Revision。
- **Agent Plugins 协议**：29 个 MCP 工具补齐标题、根对象输入/输出 schema 与 annotations；Codex 使用 `hooks/hooks.json` 注册 `SubagentStart`/`PreToolUse`，Claude manifest 单独使用 `hooks/claude-hooks.json` 注册 `PreToolUse`/`StopFailure`，避免把宿主不支持的事件混入同一 Hook 清单。
- **文档收束**：README、Skill 与当前工程文档围绕“规划 → 冻结 → 调度 → 分层 Review → 用户验收”主链重写；`graph-engineering-upgrade.md` 明确为历史 ADR，现行实现以 Skill、MCP schema 与 `project-engineering.md` 为准。
- **验证**：本地 Python 全量 277 项测试、`compileall`、canonical Skill/Plugin 镜像一致性、Skill/Plugin 校验、`validate_release` 与 `git diff --check` 均通过；真实 Codex/Claude Code 状态以宿主兼容矩阵的候选实测记录为准。

## 0.36.0 — 2026-08-08

发布提交：`ad19c33`（`main`，tag `v0.36.0`）

- feat（调度前置基线确认）：在确认开发方式（EXECUTION_MODE）之前新增控制器拥有的「开发基线」交互（`DEVELOPMENT_BASELINE`）。当工作树干净、且无已记忆基线、层级未带 `gitBinding` 时，`preview_hierarchy` 返回 `developmentBaseline`（仅枚举本地分支 + 「从主线创建新分支」）并暂不发 `executionChoice`；宿主经 `confirm_development_baseline` 记录该选择、只读计算 `gitBinding` 并将其冻结回层级，再返回 `executionChoice`。同一 Delivery 的后续 Revision 自动复用已记忆基线，不再重复询问；`prepare_delivery_revision`/`prepare_hierarchy` 在缺省 `gitBinding` 时自动注入。
- fix（AUTOMATIC 基线默认 main）：修复 AUTOMATIC 默认从 mainline（origin/HEAD→main→master）建 worktree、导致 feature 分支上的目标代码缺失、Loop 无法执行的问题。
- 工具与存储：新增 `confirm_development_baseline` MCP 工具（MCP 工具数 28→29）与 `delivery_preferences` 表（`root_id`、`branch_ref`、`base_ref`、`base_commit`、`integration_target`、`source`、`chosen_by`、`chosen_at`）。Controller 不执行任何 Git 写操作，分支/worktree 仍由宿主创建。
- 边界：手动交接（`start_manual_handoff`）在 git 漂移时阻断并要求重新确认基线的能力留待后续版本（Phase 2）。
- 验证：本地 Python 标准库全量 258 项测试、`compileall`、Skill/Plugin 镜像重建与 `git diff --exit-code`、`validate_release` 通过（工具数 29、版本 0.36.0）。这些结果属于核心契约与发布产物校验，不代表 Codex、Claude Code 的 0.36.0 真实宿主冒烟已经完成；真实宿主状态以兼容矩阵的实测记录为准。

## 0.35.0 — 2026-08-07

发布提交：`df7314b`

- 新增**基线陈旧 rebase 恢复 advisory**：当某 Delivery 冻结的 `baseCommit` 落后于其 `integrationTarget`（主线已前进）时，`workspace_status` 在 `gitWorkspace.worktreeRebase` 带出可恢复 advisory（`required`、`frozenBaseCommit`、`currentBaseCommit`、`integrationTarget`、`nextAction=REBASE_DELIVERY_WORKTREE_ONTO_CURRENT_BASE_THEN_PREPARE_DELIVERY_REVISION`）。Controller 只检测并发出信号，不做 git；宿主 rebase worktree 后用 `prepare_delivery_revision` 重锚 `baseCommit`（既有治理路径，`preparing=True` 重验），旧 run 被 supersede。
- 文档：planning-quickstart 把 rebase 恢复从“计划中”改为已实现，写明宿主恢复流程（暂停在途 Loop → rebase → `prepare_delivery_revision` → 恢复执行）与约束。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 255 项测试、编译检查、0.35.0 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.14 — 2026-08-07

发布提交：`9fd0ac4`

- 文档：planning-quickstart 新增「并行 Delivery 与同资源串行化」——并行 Delivery 各自独立 worktree 共享调度库；同文件/同区域用 `resourceClaims` 声明同一锁键即三层全局串行（声明式预防，非运行时检测）；含锁键命名约定与示例。SKILL/README 增加指引。
- 设计（未实现）：同节记录 future 0.35.0 的「基线陈旧 rebase 恢复」设计——陈旧基线检测 → 宿主 rebase → `prepare_delivery_revision` 重锚，及 Controller 不做 git、重锚走 Revision 等约束。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 253 项测试、编译检查、0.34.14 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.13 — 2026-08-07

发布提交：`53a4702`

- `cancel_graph_run` 可终态化 pre-run 草稿（CHOICE_READY/automatic_pending/PREPARED/HANDOFF_READY、无 FROZEN run、无 workspace 绑定）：标记 `ABANDONED` 并释放 `requirementKey`，而非返回 `SCHEDULER_DELIVERY_WORKSPACE_MISSING`。卡住的 AUTOMATIC 草稿（coordinator 从未绑定工作区）现可干净退役、需求键可被新 Delivery 复用；FROZEN run 仍走原取消路径。
- 控制器允许 `cancel_graph_run` 对未绑定 CHOICE_READY 草稿放行；requirementKey 冲突扫描跳过 ABANDONED；abandoned 草稿上报终态 nextAction。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 253 项测试、编译检查、0.34.13 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.12 — 2026-08-07

发布提交：`6f1c9d5`

- `preview_hierarchy` 的 `executionChoice` 新增 `baseRef`/`integrationTarget`：宿主在“自动/手动”选择时即可看到并确认基线分支（基于 master 还是某个进行中分支），在 AUTOMATIC 派发前明确基线；hierarchy 无 gitBinding 时为 null（基线在 worktree setup 时发现）。
- 文档：planning-quickstart 指出基于进行中分支修 bug 时应显式指定 `base_ref`/`gitBinding` 并与用户确认基线分支。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 252 项测试、编译检查、0.34.12 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.11 — 2026-08-07

发布提交：`66f0516`

- `select_execution_mode(AUTOMATIC)` 派发前硬校验 `gitBinding.branchRef` 是否已被其他 worktree 占用：若被 primary 等占用（git 不允许两个 worktree 共用同一分支），立即以 `SCHEDULER_GIT_BRANCH_IN_USE_BY_OTHER_WORKTREE` 拒绝；此前会让 coordinator 在 resume 撞 `SCHEDULER_GIT_BRANCH_MISMATCH` 卡死，并留下无法清理的 pre-run 草稿。
- 文档：planning-quickstart 增加“别从旧 Delivery 拷 gitBinding”“单文件小修不必套 AUTOMATIC”的指引。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 252 项测试、编译检查、0.34.11 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.10 — 2026-08-07

发布提交：`bb93e34`

- `preview_hierarchy`、`prepare_hierarchy`、`create_manual_handoff`、`prepare_delivery_revision` 新增可选 `hierarchy_file`：层级较大或 payload 详细时，模型可先把 JSON 写到工作区文件并校验，再传文件路径代替内联 `hierarchy`，控制器在工作区沙箱内读取解析，避免内联大 JSON 括号错配被吞。`hierarchy` 改为可选，校验改为二选一；拒绝路径穿越/符号链接/跨盘和非对象 JSON。
- SKILL/planning-quickstart 增加 Write → 校验 → `hierarchy_file` 的标准流程说明。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 249 项测试、编译检查、0.34.10 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.9 — 2026-08-07

发布提交：`d6e090d`

- `host_smoke` 的 Claude 路径改为监控主会话 + 后台 `delivery-coordinator`（稳定 linked worktree），替代已移除的独占 primary checkout；并改为跨 `git worktree list` 发现 smoke 产物。同步更新对应发布就绪测试。
- Hook 修复：`attest_claude_dispatch_receiver` 比对前剥离宿主注入的 `_host_workspace_attestation`，恢复 `actual_model_id` 检测；`attest_claude_workspace` 对审批门禁的敏感工具跳过工作区 attestation，避免 60 秒 TTL 在用户审批期间过期；`handle_claude_rate_limit` 增加异常保护，确保限流暂停始终生效。
- 文档：README 使用方式改写为 linked worktree + 后台 coordinator 模型，补齐 `delivery-coordinator` 与 `attest_claude_workspace` 说明和结构表；references 补全 `failure_class` 枚举、`record_user_confirmation.summary`、Revision `confirmed_by`、`workerTelemetry.provenance`、`actual_model_id`，并对 planning 的 Git 工作区段做分组重构。
- `delivery-coordinator` Agent 移除不存在的 `PowerShell` 工具。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 242 项测试、编译检查、0.34.9 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.8 — 2026-08-06

发布提交：`f23ed5e`

- Claude 与 Codex 自动 Git Delivery 统一改走 `HOST_NATIVE_LINKED_WORKTREE`：宿主创建或复用一个稳定 Delivery worktree 并启动后台 coordinator，主会话只监控并从共享控制根消费进度；移除独占 primary checkout 路径，不再要求用户新开顶层会话或手动 `cd`。
- 新增 Claude `PreToolUse` Hook `attest_claude_workspace.py`：为每次 MCP 调用注入一次性、与工具绑定的 workspace attestation，使固定插件根解析到宿主实际观测的 cwd；模型与普通 MCP 客户端必须忽略该内部字段。新增 `delivery-coordinator` 后台 Agent 与 `host_workspace_attestations` SQLite 表（60 秒有效期、一次性消费）。
- 控制器允许 `graph_frontier`/`graph_status`/`graph_events` 等监控工具从共享控制根安全读取，主会话无需进入执行 worktree 即可观察后台 Delivery。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 242 项测试、编译检查、0.34.8 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.7 — 2026-08-05

发布提交：`d84896a`

- Claude Code 裸 CLI 支持从 `cd project; claude` 启动的独占 primary checkout：有效 feature 分支可直接执行，主线或 detached 状态只需在当前 checkout 建立 Delivery feature 分支后无二次确认续接；同一 primary checkout 仍只绑定一个未结束 Delivery。
- Codex primary checkout 继续返回机器可消费的 `hostDispatch` 并创建独立 worktree 项目任务；并行或已占用的 Claude Delivery 也继续使用 linked worktree，不降低隔离边界。
- 工作区 dirty 指纹排除 `.layered-delivery/**` 控制面产物，业务改动仍要求用户按精确状态指纹确认。
- 恢复 Claude Skill 的 MCP wildcard `allowed-tools`：普通 Layered Delivery 工具自动放行，重建、取消和需求解冻/再冻结等敏感操作继续由 PreToolUse Hook 强制询问或拒绝。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 239 项测试、编译检查、0.34.7 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.6 — 2026-08-05

发布提交：`30a64fa`

- `loop_context` 把冻结的仓库锚点与运行时有效 worktree 分离：receiver 只在本 Delivery 已验证的 `projectScopes` 路径工作，并明确禁止自行创建或切换分支，避免同仓库并行 Delivery 互相抢占主检出。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 234 项测试、编译检查、0.34.6 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.5 — 2026-08-05

发布提交：`60d2da9`

- AUTOMATIC 选择改为先持久记录再校验 worktree：primary checkout 返回宿主迁移动作，新会话通过 `resume_execution_mode` 延续同一次确认；`projectScopes` 可按 Git common directory 与冻结分支解析实际 linked worktree，同一业务目标覆盖多个本地仓库时不再误拆 Delivery。
- 多会话 Review 支持在无活跃 claim 的安全 frontier 边界轮换同一可信 Adapter 的编排根；长时间命令契约要求心跳与阻塞进程解耦，`SUSPECT_LOST` 增加不猜测宿主存活或具体根因的诊断字段。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 233 项测试、编译检查、0.34.5 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.4 — 2026-08-05

发布提交：`047c124`

- 新 Delivery 采用宿主原生 linked worktree 契约：宿主可显式选择基线；否则优先使用有效的 `origin/HEAD`，再回退本地 `main`、`master`，不硬编码 `develop`。
- `workspace_status` 新增可冻结的 worktree provenance，校验独立分支、其他 worktree/Delivery 占用、主 worktree 误绑定和已有 diff 精确指纹确认，确保并行会话不会互相污染。
- 执行方式交互升级为 schema v2：Codex 与 Claude Code 在原生选择器可调用时必须直接映射 Controller 的固定选项；只有能力不可用时才允许原样显示 Controller Markdown，不再改写成“回复自动/手动”的文本交互。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 228 项测试、编译检查、0.34.4 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.3 — 2026-08-05

发布提交：`dd226ff`

- 后台 Graph 进度推荐轮询间隔由 30 秒缩短为 10 秒，90 秒继续只作为首次独立心跳缺失的健康告警阈值。
- MCP 宿主指令禁止把首次心跳告警窗口当作 sleep 或轮询间隔；宿主收到原生 child 完成通知时立即刷新 frontier。
- STANDARD Loop 在根因确认、修改完成、测试开始与结束、修复、复审和最终验证等阶段立即上报进度，长时间测试或构建在开始前后分别上报；LIGHT 上报规则保持不变。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 215 项测试、编译检查、0.34.3 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.2 — 2026-08-04

发布提交：`968ea53`

- 修复 Codex 与 Claude Code 因共享用户级 `orchestrator.json` 仍为 schema v1 而在工具注册前同时退出的问题；MCP Server 不再读取该外部文件，残留配置不参与启动、派遣、授权或指纹。
- 移除中央设置 MCP Apps、`open_orchestrator_settings`、`update_orchestrator_settings` 及配置读写实现；外层 receiver 最大并发 4 和固定 `PAUSE_AND_RESUME` 策略改由 Plugin 内置，工具面收敛为 27 个。
- 保留 Controller 的中央协调事务：reservation、已 claim receiver、资源锁和容量断路器仍约束重复、冲突与超量派遣；新增真实旧配置 stdio 启动回归。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 215 项测试、编译检查、0.34.2 发布候选校验、Skill/Plugin 校验、双宿主本地探测和 `git diff --check` 通过。

## 0.34.1 — 2026-08-04

发布提交：`eac0435`

- 修复 manual Graph 在 TASK 实现完成后没有继续进入分层审查的问题：Codex `SubagentStart` Hook 接受受治理的 active/manual Delivery，宿主未报告模型时仍可完成 Review receiver 认证；手动交接响应明确要求接收 CLI 先启动同一 Graph，后续 TASK/GROUP/Delivery Review 完整复用自动派遣流程。
- 彻底移除 `available_agents`、三份 `agent_discovery.py`、Plugin 发现关键词、外部进程 transport 常量和旧模型路由字段；MCP 工具总数收敛为 29，SQLite 自动清除当前 schema v3 中已经废弃的模型列，宿主实际模型只保留为非权威观测遥测。
- 重新构建规范 Skill runtime 和 Plugin 内嵌 Skill，Python 全量 230 项测试、编译检查、0.34.1 发布候选校验、Skill/Plugin 校验和 `git diff --check` 通过。

## 0.34.0 — 2026-08-04

发布提交：`6bba7a3`

- 修复手动交接在 TASK 实现后脱离治理 Graph 的流程缺口：新增 `start_manual_handoff`，接收 CLI 必须在代码工作前用双 fingerprint 启动同一 Revision；只有 TASK 实现使用 `MANUAL` claim，TASK/GROUP/Delivery Review、findings 闭环和最终用户确认均与自动执行保持一致。
- manual Graph 支持启动响应未知后的幂等恢复、HANDOFF → PREPARED 中断恢复和事件重放；Claude/Codex 独立 TASK receiver 的 Hook 可继续授权 heartbeat、进度与结果操作，但不能把任何 Review 降级为 MANUAL。
- 中央派遣升级为 `HOST_NATIVE_RECEIVER_ROUTING_V5`：Controller 只为当前可信宿主预留独立 receiver，receiver 继承当前宿主模型；移除 `recommend_executors`、模型推荐、reasoning class 和 30 秒路由调整窗口，Loop 内 Worker 的模型与 effort 只作为非权威遥测。
- 中央编排器配置升级为 schema v2，只保留全局 receiver 并发上限和固定的 `PAUSE_AND_RESUME` 额度策略。已有 schema v1 文件必须按运维文档显式改写；旧字段 fail closed，不做静默兼容或迁移。
- MCP 工具总数保持 30：删除模型推荐工具并新增手动 Graph 启动工具；同步更新双宿主 Hook、Plugin 元数据、Skill、参考文档、设置面板与 host smoke 契约。
- Python 全量 226 项测试、编译检查、0.34.0 发布候选校验、Skill/Plugin 校验、双宿主无模型本地探测和 `git diff --check` 通过。

## 0.33.4 — 2026-08-04

发布提交：`3b94213`

- 修复共享 `scheduler.db` 中一个 Delivery 的损坏状态阻断其他健康 Delivery 的跨域故障：当前 `graph_frontier`、状态查询和投影刷新只以目标 `rootId` 的完整性作为成败边界。
- 全局 `overview.md` 对每个 Delivery 独立校验，损坏记录显示“调度状态异常”；其他 Delivery 的数据库或投影目录问题通过 `projectionIssues` 报告，不再拖死当前 Delivery。
- `Stored scheduler graph changed` 等存储校验错误补充实际损坏的 `rootId`，避免把其他需求的坏记录误判为当前 Graph 或普通 Git HEAD 前进问题。
- 新增 schema v3 Graph 生成契约标识 `scheduler_metadata.state_contract`，不兼容生成器共同访问同一数据库时 fail closed；现有 schema v3 数据库自动登记当前契约，无需人工修改 SQLite。
- 投影模板升级到版本 14；新增跨 Delivery 数据损坏、投影文件损坏和生成契约不匹配回归。Python 全量 267 项测试、编译检查、发布候选校验、Skill 校验和 `git diff --check` 通过。

## 0.33.3 — 2026-08-04

发布提交：`348922c`

- 规划交互改由 Controller 唯一拥有：`preview_hierarchy` 在返回选项前登记 `CHOICE_READY`，生成共享 `scheduler.db`、根总览、Delivery 基线/关联文档及递归 work-items，再返回固定的自动/手动双选项和自由输入行为；Skill 与宿主只允许原样显示或机械映射。
- 新增 `select_execution_mode`：默认 `AUTOMATIC` 一次完成 prepare、freeze 并要求宿主立即进入自动派遣，不增加第二次确认；`MANUAL` 生成 handoff，并把同一 `receiverPrompt` 同时写入响应和文件，供任意 CLI 直接开发。
- Codex 与 Claude Code 使用相同选项顺序、默认项、精简说明和状态转换；自由文本直接继续需求沟通，不再制造第三个“其他”选项。MCP 工具面由 29 项增至 30 项。
- Python 全量 264 项测试、编译检查、0.33.3 发布候选校验、Skill/Plugin 校验、host smoke 和 `git diff --check` 通过。

## 0.33.2 — 2026-08-04

发布提交：`063198b`

- 修复同一工单可通过更换 `delivery.id` 重复生成冻结目录的卡控缺口：新增可选 `delivery.requirementKey`，并从 ID/标题兜底识别常见工单号，在 preview 与最终事务写入两层拒绝重复映射；同一 Delivery 的 requirementKey 在 Revision 间保持不可变。
- `HANDOFF_READY` 手动需求现在可在原目录创建不可变 Revision：调用方显式提交当前 Revision、`USER_EXPLICIT_SAME_DELIVERY` 和修订原因，旧 Revision 标记为 `SUPERSEDED`，新 handoff、baseline 与 work-items 继续使用原 `delivery.id`，不再被迫换 ID。
- 接口文档生成器现在按实际 request/response 字段投影：空字段列表明确显示“无入参/无出参”，HTTP 请求位置容器只展开非空字段，Controller 返回类型和字段还原为 VO 契约，不再生成 `pathParameters`、`controllerReturnFields` 等伪字段；`wireType`、`frameworkEnvelope`、`wrapping` 与 `Rs` 包装信息一律忽略。
- HTTP 详情按 Torna 的 Path、Query、请求头、请求体和响应参数分区，Dubbo 详情按接口、方法、调用参数和返回结果分区；支持字段必填、最大长度、说明和示例值，并可由类型与参数名生成 Dubbo 方法签名。
- 冻结 baseline 的 after 明确成为实际开发接口和后续 Torna 发布的唯一事实来源，方法、路径或签名以及字段层级和属性必须一致，不再从另一套输入生成不同接口文档。
- 投影模板升级到版本 13；新增无参 VO、路径/查询参数位置展开、HTTP 包装忽略和 Dubbo 协议版式回归，实时 `hierarchy_contract` 同步公开支持的字段形状、别名和包装忽略策略。
- Python 全量 256 项测试、编译检查、0.33.2 发布候选校验、Skill/Plugin 校验、host smoke 和 `git diff --check` 通过。

## 0.33.1 — 2026-08-04

发布提交：`2df5b73`

- 手动开发冻结快照现在必须登记到共享 `scheduler.db`，状态为 `HANDOFF_READY`，并同步生成根级 `overview.md`；多个窗口同时创建手动需求时通过 scheduler lock 串行提交并汇总到同一总览。
- 手动路径返回 `controlStateCreated=true`，但仍保持 `graphRunCreated=false`、`workspaceCreated=false`，不创建 `runs`、事件链、workspace 绑定、Agent、任务或 worktree。
- SQLite 成为自动与手动需求状态的统一机器权威；控制器重建手动投影时保留接收 CLI 写入的 progress/acceptance，同时恢复被冻结的 handoff、overview、baseline、revisions 与接口输入。
- Python 全量 246 项测试、编译检查、0.33.1 发布候选校验、Skill 校验和 `git diff --check` 通过。

## 0.33.0 — 2026-08-04

发布提交：`4104083`

- 手动开发由单一交接文件升级为无控制状态的冻结内容包：`create_manual_handoff` 复用自动开发渲染器，在稳定 `.layered-delivery/<delivery-id>/` 下生成 overview、baseline、progress、acceptance、revisions、递归 work-items 和接口详情，并保留自包含 handoff 文件。
- 新增 `requirementSnapshotStatus=FROZEN`，明确区分“需求内容已由双 fingerprint 锁定”和 Graph `FROZEN/ACTIVE`；手动路径继续保持 `controlStateCreated=false`、`graphRunCreated=false`、`workspaceCreated=false`，不选择 Agent/模型、不创建任务或 worktree。
- 接收方可切换到任意 CLI 直接按冻结 baseline/work-items 开发，仅维护 progress/acceptance；handoff、overview、baseline、revisions、接口契约和双 fingerprint 保持只读，需求变化必须生成新快照。
- 同一 fingerprint 后续进入自动开发时，控制器原位接管标准投影并保留 handoff；已有匹配的 Active Delivery 只补交接文件并重建权威投影，不会被“手动调度未启动”状态覆盖。相同 `delivery.id` 但 fingerprint 不一致时 fail closed，要求新 Delivery 或显式 Revision。
- 新增完整目录、接口树、MCP 返回、已有 Active 状态保护和自动/手动结构一致性回归；Python 全量 244 项测试、编译检查、发布候选校验、Skill/Plugin 校验、双宿主只读探测和 `git diff --check` 通过。

## 0.32.0 — 2026-08-03

发布提交：`158f7a7`

- 手动交接改为纯开发内容导出：新增只读 `preview_hierarchy` 与 `create_manual_handoff`，生成一个同时包含中文需求、全部 GROUP/TASK/Review 输入和机器可读 schema v3 附录的 Markdown 文件；不 prepare、不 freeze、不创建 Graph Run。
- 交接前不再发现、推荐或绑定接收 Agent/模型，也不创建接收任务、会话或 worktree。`recommend_executors` 只接受 `AUTOMATIC`；MCP `freeze_hierarchy` 只用于自动执行，宿主不再提交 `execution_mode`。
- manual Graph run 从 Python 领域入口和派遣契约同步移除：`freeze_hierarchy`/Repository 不再接收执行模式，`dispatch_loop` 只允许显式 `AUTO` 并强制 reservation/decision；手动交接继续是完全独立的文件导出流程。
- worktree 初始化延后到实际开发开始：自动执行在 prepare 前创建或选择开发工作区，手动接收方在读取文件后再处理。异步宿主只返回 `clientThreadId` 时明确为 `WORKTREE_SETUP_QUEUED`，不得当作真实任务 ID、Graph 已启动或重复创建依据。
- 手动交接返回 `controlStateCreated=false`、`graphRunCreated=false`、`workspaceCreated=false` 和确定性文件路径；双 fingerprint 与精确项目授权在写文件前校验，避免交接内容与用户确认的预览漂移。
- `report_loop_progress` 不再强制摘要、里程碑和下一步包含简体中文字符，改为接受用户当前语言，同时保留非空、长度、控制字符、结构化测试计数及原始日志禁入边界；跨项目文档示例改用中性项目名和路径。
- 新增基于实际改动内容和影响范围的 `LIGHT`/`STANDARD` 保障档：不确定时默认 STANDARD；LIGHT 仅允许一个根 TASK，省略全部独立 Review、执行定向验证并直接等待用户确认，影响扩大时必须以 `REPLAN_REQUIRED` 升级同一 Delivery 的下一 Revision。
- 新增三份可校验团队 hierarchy 模板、跨团队 `resourceClaims` 精确键规范，以及安装、升级、恢复、卸载、回滚和宿主兼容矩阵文档；文档中的 MCP 工具数量由契约测试固定为 29。
- 新增 Python 3.10/3.12/3.14 GitLab CI 契约矩阵、无网络发布候选一致性校验器，以及默认不调用模型、真实运行必须显式 `--execute` 的 Codex/Claude 双宿主冒烟入口。真实冒烟在独立临时仓库验证 claim、progress、heartbeat/result 与待用户确认门禁，不伪造最终用户接受。
- 双宿主冒烟补齐自动预批准的精确工具白名单、隔离 feature 分支夹具和失败日志尾部；`dispatch_loop.owner` 的 MCP schema 现在公开完整可移植身份字符集，并明确无宿主标签时直接使用原生 `agent_id`，避免接收方用 `#` 拼接节点名后在 claim 前失败。
- 0.32.0 宿主冒烟只验证当前 Agent 的原生子上下文：Claude 运行只允许 `claude-code` claim，Codex 运行只允许 `codex` claim，并从事件库硬校验 claimed Agent 集合。跨 Agent 冒烟留到可信多 Adapter 桥接实际实现后再新增，不因本机发现另一 CLI 提前宣称支持。
- 真实 Claude 冒烟暴露的 reservation 延迟与字段猜测一并收紧：取得 300 秒预留后必须立即创建 child，child 在实现前先 claim；MCP schema 允许模型省略由 PreToolUse 注入的 receiver context/attestation，并明确禁止伪造占位值。
- Windows 真实宿主完成后允许候选 MCP 进程短暂释放 SQLite 句柄；临时目录清理改为两秒等待和 best-effort，不再把已经取得完整成功事件的运行因瞬时 `WinError 32` 误判成业务失败。
- Codex 真实宿主冒烟不再使用会隐藏父 rollout 的 `--ephemeral`；mutation Hook 同时识别 Codex 0.146 提供的父 `session_id` 与子 `agent_id`，再以受信 child transcript、reservation task name 和已消费身份注入 operation。拒绝信息附带稳定调度错误码，便于区分 owner、lease 与身份记录问题。
- 原生接收协议要求 claim 后读取一次 `loop_context` 并立即提交首次独立 heartbeat，再开始代码检查、分析、读写或测试；Codex `SubagentStart` additional context 直接携带该顺序，避免短 Loop 虽已执行却被 90 秒监控标为“疑似未启动”。

## 0.31.0 — 2026-08-03

发布提交：`ed56e67`

- 自动建议与正式派遣统一使用当前宿主 Agent 的 Claude/Codex 原生模型分档；Codex 使用 Luna/Terra/Sol 等原生 selector，Claude Code 使用 Haiku/Sonnet/Opus 等原生 selector。GLM、DeepSeek 等转发后的实际模型继续只通过 `actualModelId` 展示，不参与 tier、决策指纹、reservation 或 claim。
- `plan_dispatch_batch` 新增持久化 30 秒路由调整窗口：首次稳定路由返回无预留的 `HOST_NATIVE_ROUTE_REVIEW` 和中文表格所需结构；无需第二次确认，到期后宿主自动重调并原子预留。用户可在 claim 前用 `USER_POLICY + preferredNativeModelId` 精确修改原生模型，变化节点重新计时。
- 自动建议限制在当前执行 Agent，不再根据本机发现结果虚构跨 Agent 建议；手动交接才允许改变 TASK 开发 Agent，并要求宿主创建目标 Agent 的独立接收会话。交给 Codex 时进入新的 Codex 任务，交给 Claude Code 时进入新的 Claude 会话，总调度会话只负责跟踪。
- 路由预览和正式计划复用相同选择函数与决策指纹；新增持久化 route review 存储、宿主强制窗口、原生模型覆盖校验及配套回归测试。当前 schema 仍为 v3，不增加旧 schema 兼容入口。

## 0.30.0 — 2026-08-03

发布提交：`ede8782`

- 派遣模型身份拆分为权威原生 `modelId` 与展示用 `actualModelId`：inventory、tier、Review 多样性、reservation、决策指纹和 claim 授权全部只使用 Claude/Codex 原生模型名；本机配置或任意修改器在原生调用后的转发行为不进入编排。
- `modelOverrideSupported` 更名为 `nativeModelSelectionSupported`，`EXPLICIT_OVERRIDE` 更名为 `NATIVE_MODEL_SELECTOR`，避免把宿主原生角色选择误解为对 GLM、DeepSeek 等实际提供方模型做 override；路由策略升级为 `HOST_NATIVE_MODEL_ROUTING_V4`。
- Claude dispatch Hook 与 Codex `SubagentStart` Hook 可把宿主观测到的运行模型记录为 `actualModelId` / `HOST_REPORTED`，但实际模型变化不再导致 reservation、claim 或后续 Loop mutation 授权失败；中文进度表展示“原生模型 → 实际模型”，未知时明确显示“未报告”。
- 修复 Claude `PreToolUse` 早于当前 `tool_use` transcript 落盘时误报 `not host-attested` 的竞态：后续 heartbeat、进度、暂停和结果登记改用领取时已消费的 receiver attestation 与宿主 child/parent 上下文绑定授权；未领取的子 Agent 和父上下文仍保持拒绝。共享授权 Hook 同步更名为 `authorize_loop_operation.py`，准确反映 Claude/Codex 双宿主职责。
- 这是当前 schema v3 内的派遣协议更新，不新增旧字段兼容入口。调用 `plan_dispatch_batch` 的宿主 inventory 必须改用 `nativeModelSelectionSupported`，并确保 `model.id` 是宿主原生 Agent API 接受的模型名或角色选择器。

## 0.29.0 — 2026-08-03

发布提交：`17aea7f`

- 新增 `report_loop_progress`：claimed Loop 可在领取、代码检查、测试、问题分析、修复、复审和最终验证等阶段提交有界的简体中文结构化进度；事件进入哈希审计链，但不改变 Graph 状态、不续租，也不保存原始终端日志或内部推理。
- `graph_status` 与 `graph_frontier` 新增 `progressMonitor`，默认提供面向主 Agent 的中文 Markdown 表格，汇总 attempt、实际 Agent/模型、阶段、摘要、里程碑、下一步、测试、心跳、剩余租约和健康状态；MCP 文本结果直接展示该表格，原始事件只用于展开诊断。
- 新增后台失联预警：领取后 90 秒无首次心跳提示“疑似未启动”，心跳正常但 5 分钟无进度提示“存活但无可见进展”，心跳和进度均停止提示“疑似失联”；`graph_frontier` 继续在租约到期时自动按 `WORKER_LOST` 回收并重试。
- Claude/Codex mutation Hook 扩展到进度上报；Skill 要求主 Agent 按 30 秒建议间隔持续刷新表格，并在认证失败时禁止主上下文代交结果或伪造 operation。

## 0.28.9 — 2026-08-03

发布提交：`4ea128c`

- 修复 Claude Code `PreToolUse` 只检查主会话 transcript、无法识别真实子 Agent sidechain `tool_use` 的问题；Hook 现在同时核对主 transcript 与 `<sessionId>/subagents/agent-<agentId>.jsonl`，继续严格绑定 child、父会话、模型、工具名和完整输入。

## 0.28.8 — 2026-08-03

发布提交：`cbcd883`

- 修复中央编排器设置工具在 Codex MCP Apps 保存时错误要求项目 sandbox metadata 的问题；设置读写保持用户级、与 Delivery 工作区无关，其他 Graph 工具仍严格校验工作区身份。
- 在中央编排器面板和保存接口中显式锁定尚不可用的跨 Adapter 调度与 `SWITCH_ADAPTER` 策略，并返回 `ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE`；README 新增配置路径、刷新语义和当前能力边界。
- 修复 `WORKER_LOST` 后新编排会话无法接管同一 Adapter 重试 attempt 的 receiver-root 连续性问题。信任根只在首次成功 claim 时固定；满足严格失联重试条件时可原子轮换并记录 `RECEIVER_ROOT_ROTATED`，无需重冻或直接修改调度数据库，跨 Adapter 和并发活跃接收方仍保持拒绝。

## 0.28.7 — 2026-08-03

发布提交：`4ec6dd6`

- 修复 Claude Code Loop mutation Hook 对不存在的 PreToolUse `model` 字段的依赖；heartbeat、pause 与 result 现在从当前宿主 transcript 的精确 `tool_use_id` 提取实际模型。
- transcript 校验同时绑定 child `agentId`、编排根 `sessionId`、工具名与完整输入，并继续与已消费 receiver attestation 和 claim 事件核对；伪造顶层模型、错模型、错上下文或错工具调用保持 fail closed。
- 真实 Claude 2.1.220 事件形状回归测试覆盖无顶层模型的正常授权与 transcript 模型不匹配拒绝。Python 全量 205 项测试、编译检查、Skill 校验和 `git diff --check` 通过。

## 0.28.6 — 2026-08-03

发布提交：`fb85e17`

- 重构项目 README，聚焦“需求确认 → Graph 计划 → 用户冻结 → 自动/手动调度 → 分层 Review → 人工验收”主流程；完整 schema、协议、Hook 与投影实现细节改由专题文档承载。
- 按渐进披露原则精简规范 Skill 主文件，只保留入口状态路由、硬安全边界、规划冻结、调度循环和恢复规则；六份现有 reference 继续保存精确契约，并从主文件一层直达。
- 测试改为验证入口文档长度、reference 可达性、规划文档中的真实 schema 示例和 Plugin 副本一致性，避免再次用正文重复来维持合同。schema v3、MCP 工具和运行语义均未改变。
- Python 全量 205 项测试、三份运行包编译、Markdown 本地链接、Skill/Plugin 校验和 `git diff --check` 通过。

## 0.28.5 — 2026-08-01

发布提交：`e8591da`

- 自动派遣推理分类增加 `ROUTINE`，由 Agent 为明确、低歧义、可重复且具备确定验证路径的 Loop 选择，并映射到宿主动态 inventory 中的 `EFFICIENT` 模型；`STANDARD → BALANCED` 与 `HIGH → FRONTIER` 保持不变。
- 新增用户级中央编排器配置：默认开启自动编排与自动选模、关闭跨 Adapter，允许手动配置 Adapter 白名单、跨 Delivery 全局并发上限、额度耗尽策略和 Review Adapter 偏好；同机 Codex/Claude Code 共享配置，Marketplace 升级不覆盖，非法配置 fail closed。
- 新增中央编排器 MCP Apps 配置面板和无 UI 降级工具：可视化查看真实 Adapter 能力并经审批原子保存用户级策略；支持 MCP Apps 的宿主内嵌呈现，不支持的 Codex/CLI 继续使用结构化工具结果。
- 自动派遣只接受当前宿主证明为 `HOST_NATIVE` 的容量；跨 Adapter 开关是显式授权而非可用性证明，终端发现继续保持 `EXTERNAL_PROCESS`。Python 全量 204 项测试、三份运行包编译、Skill/Plugin 校验和 `git diff --check` 通过。

## 0.28.4 — 2026-08-01

发布提交：`19d2885`

- 自动派遣为每个 Codex assignment 生成 reservation 派生的唯一 `hostTaskName`，宿主按 Agent 分析产生的 `STANDARD` / `HIGH` 路由结果显式覆盖模型并按 `concurrentDispatchGroups` 并发创建独立上下文；普通 helper、错误模型、过期预留和跨 Delivery 工作区不能串换 claim。
- Codex Plugin 新增原生 `SubagentStart` 生命周期适配，在单一 SQLite 事务内签发并消费内部 identity、固定成功编排根、消费 reservation 并写入 Loop claim；receiver 与 operation bearer 不进入 child 上下文，重复 Hook 和投影后处理异常可从已提交状态幂等恢复。
- heartbeat、pause 与 result 使用共享 mutation `PreToolUse` 授权：Codex 校验默认账户 session transcript，Claude Code 校验真实 `agent_id`、已消费 attestation 与 claim 事件；缺失 transcript、自定义 `CODEX_HOME`、root/helper 自带 operation 以及未知宿主统一 fail closed。Hook 仍遵循宿主 guardrail 边界，不把可执行 CLI 当作宿主原生 Agent。
- Python 全量 185 项测试、编译检查、Skill/Plugin 校验和 `git diff --check` 通过；独立安全复审在正常宿主 Hook/MCP 路径下无 CRITICAL/HIGH finding。

## 0.28.3 — 2026-07-31

发布提交：`cec4e59`

- TASK 接口投影改为 `interfaces.md` 索引加 `interfaces/` 详情目录，每个显式声明的 HTTP、Dubbo、gRPC 或其他协议接口均生成独立文档；索引保留协议、变更类型、调用标识、简介和稳定详情链接。
- 入参表继续比较类型、必填性和说明，出参表移除无业务意义的必填列。新增或删除字段只显示实际存在的一侧，不再展示 `— →` / `→ —`；删除字段与删除接口使用 Markdown 删除线，真正修改的属性仍保留 before/after 箭头。
- 投影模板升级到版本 11。已有 schema v3 Delivery 无需数据库或 Graph 迁移，后续合法状态刷新会从 SQLite 权威状态重建新的接口索引和详情目录。
- Python 全量 173 项测试、编译检查、Skill/Plugin 校验和 `git diff --check` 通过。

## 0.28.2 — 2026-07-31

发布提交：`52a1961`

- 自动派遣改为逐 Loop 消费 Agent 推理分析，并按 TASK、Review 与高推理等级选择宿主原生 Agent/模型；任一节点缺少分析时回退当前执行 Agent/模型。派遣预留和已 claim Loop 共同占用跨 Delivery 并发槽位，实际接收 Agent/模型继续以调度器落库事实为准。
- 新增宿主签发的一次性 receiver attestation。每个 run 固定唯一编排根，多级子上下文及首次切换另一平台 adapter 都不能另建信任根；标准 Codex Plugin 没有原生生命周期签发回调时 fail closed，Claude Code 由 PreToolUse Hook 注入真实子 Agent 身份。
- Claude `unfreeze_task_requirement` / `refreeze_task_requirement` 恢复敏感操作确认。`StopFailure(rate_limit)` 在模型无法反馈时由宿主按结构化错误暂停，可信 reset 到点自动恢复；共享额度断路器覆盖同 Agent 的跨 Delivery Loop，旧 run 重建不能覆盖更新的 report。
- Delivery/Revision 继续按独立对话工作区与 linked worktree 隔离，新需求不会因恢复旧 Delivery 而误生成 Revision；Revision 迭代只保留冻结时的一次业务确认。
- MCP 工具面保持 24 个，schema 继续只维护完整 v3。Python 全量 173 项测试、编译检查、Skill/Plugin 校验、`git diff --check` 与独立上下文复核通过，无 CRITICAL/HIGH finding。

## 0.28.1 — 2026-07-31

发布提交：`1dffd62`

- 自动派遣新增宿主通道边界：终端发现候选固定为 `LOCAL_TERMINAL / EXTERNAL_PROCESS`，只有宿主明确提供的 `HOST_NATIVE` 槽位才能进入 assignment；禁止通过 CLI、subprocess 或 `codex-companion` 绕过宿主权限创建自治 Agent。
- `plan_dispatch_batch` 在宿主创建 Agent 前为每个 assignment 原子签发短租约 `dispatchReservationId`；其他调度器看到 `WAIT_FOR_DISPATCH_RECEIVER`，无法重复派遣同一 node/attempt。预留同时占用精确资源声明，接收方凭票 claim 或等待过期后重新计划。
- 自动决策指纹绑定 Graph、节点、Agent、实际模型、推理等级与派遣通道；计划模型与接收方实际模型不一致时保持 Ready 并拒绝 claim。Review 无法实现异构 Agent/模型时明确降级为 `diversityLevel=CONTEXT_ONLY`。
- 新用户需求默认建立独立 Delivery；工作区已有未结束 Delivery 时在写入前要求新的 linked worktree 任务，不再误生成旧 Delivery Revision。`prepare_delivery_revision` 只准备候选且不触发通用宿主确认，自动执行/手动交接仍是每个 Revision 唯一一次业务确认。
- MCP 工具面保持 24 个，schema 继续只维护完整 v3。Python 全量 153 项测试、编译检查、Skill/Plugin 校验和 `git diff --check` 通过。

## 0.28.0 — 2026-07-31

发布提交：`ab4e6bc`

- 一个业务需求现在保持稳定的 `delivery.id`，用户最终验收前通过不可变 Delivery Revision 继续调整范围；`prepare_delivery_revision` 与 `delivery_revision_history` 显式准备、冻结和追溯各 Revision，新 Revision 冻结后旧运行进入 `SUPERSEDED`，不再为同一需求创建无关 Delivery。
- Revision 会继承定义与 Review Loop 均未改变且已经通过 TASK Loop 和 TASK Review 的任务结果；受影响 TASK、全部 GROUP Review 与 Delivery Review 重新执行。已经误取消但尚未最终验收的运行也可以显式进入下一 Revision。
- `delivery.projectScopes` 支持一个 Delivery 精确授权多个本地仓库。所有可写 Git 项目必须使用同名 feature 分支，同时分别冻结各仓库自己的 `baseRef`、`baseCommit` 与 `integrationTarget`；冻结调用必须提交与准备结果完全一致的项目 ID 集合，项目范围不会隐式授予提交、推送、合并或发布权限。
- SQLite 当前调度库增加 Revision 历史、旧运行升级和携带结果事件；新增 `revisions.md` 与跨项目授权投影，MCP 工具面增至 23 个。schema 继续只维护完整 v3。Python 全量 128 项测试、编译检查、Skill/Plugin 校验和 `git diff --check` 通过。

## 0.27.0 — 2026-07-31

发布提交：`195d50a`

- `prepare_hierarchy` 的 MCP 工具定义现在直接暴露完整 schema v3，以 `oneOf` 约束 GROUP/TASK 根节点；Adapter 在进入 Controller 前执行结构和领域预检，非法顶层字段、父子关系或依赖不会形成事后拒绝的 `PREPARED` 结果。
- `dispatch_loop` 要求接收方提交实际 Agent 与模型，claim 事件和 `progress.md` 使用中文列展示执行代理、执行模型、认领身份与执行轮次；推荐结果仍保持 `ADVISORY`，不会自动冒充实际派遣。
- TASK 接口详情改为请求/响应字段级变更表，在完整契约内直接标记新增、修改、删除和未变，并以“修改前 → 修改后”展示类型、必填性和说明。
- schema 继续只维护完整 v3；本版本没有实现按推荐结果自动派遣。Python 全量 122 项测试、编译检查、Skill/Plugin 校验和 `git diff --check` 通过。

## 0.26.0 — 2026-07-31

发布提交：`b1914e2`

- 同一共享控制根现在支持多个对话窗口并行维护多个 Delivery：每个 Active Delivery 绑定独立 `workspaceKey`，linked Git worktree 共享主 checkout 的统一 `scheduler.db`，但隔离工作目录、Git index 与未提交改动；每个工作区最多运行一个未结束 Delivery。
- Git hierarchy 新增冻结的 `delivery.gitBinding`，记录 Delivery feature 分支、本地主线（优先 `main`，不存在时回退 `master`）、不可变 fork commit 与最终集成目标。准备和运行入口只读校验 worktree、当前分支、HEAD 对 fork commit 的继承关系以及主线仍包含该基线，切错分支时拒绝运行，切回后可继续；控制器不自动创建、切换、提交、合并或推送分支。
- Git 模型收敛为“一 Delivery、一 linked worktree、一 feature 分支”。同一 Delivery 的全部 TASK 共享该分支，不创建 TASK 级 Git binding；获得相应 Git 写入授权后，各 TASK 可只暂存并提交自身 scope 的变更，在 Delivery 分支上形成独立 commit，共享 Git index 的写入必须串行。
- 初次冻结会把全部 TASK requirement 置为 revision 1 冻结态。开发期间，只有从未 claim 的 TASK 可经用户明确授权单独解冻，替换 `title`、`summary` 和不透明 `payload` 后再次冻结并递增 revision；依赖、资源声明、Loop、Review 和拓扑仍不可局部修改。
- `resourceClaims` 在共享控制根内跨 Delivery 全局生效，确保 worktree 文件隔离不会掩盖数据库、端口、部署环境或共享模块冲突。MCP 工具面保持 21 个，schema 继续只维护完整 v3，不增加旧结构迁移入口。
- Python 全量 117 项测试、编译检查、Skill 校验和 `git diff --check` 通过。

## 0.25.0 — 2026-07-30

发布提交：`ef0042b`

- 每个 TASK 现在都必须显式配置 `reviewLoop`，并编译为 `TASK_LOOP → TASK_REVIEW_LOOP`；兄弟依赖、GROUP 完成点和 Delivery Review 只消费 TASK Review 后的终态。GROUP 仍按实际协调需要创建、可完全省略或多层递归，但每个已创建 GROUP 都必须经过自己的 `GROUP_JOIN → GROUP_REVIEW_LOOP`，逐层审查整体集成结果。
- 验收投影改为严格按层归属：TASK、GROUP、Delivery 只完整展开本层结果与 Review；GROUP 对直接子节点、Delivery 对根工作项仅保留状态、简要结果和报告链接，不再向上复制下层输入、证据或 Review findings。
- 宿主明确报告剩余额度不高于 5% 且提供真实 `resetAt` 时，可定时暂停同一 attempt，并显式区分 `EXECUTOR` 与 `HOST` 容量范围。Claude Code 使用当前会话一次性 Cron，Codex Desktop 使用当前任务计划，在恢复窗口后唤醒原 Agent；控制器由下一次 frontier 调用恢复同一 attempt，不实现 Python Supervisor、操作系统定时器或自动换 Agent。
- 直接收到 429、宿主原生计划不可用或宿主被关闭时只做人工恢复，不补建定时任务、不猜测恢复时间。`recommend_executors` 继续保持纯 `ADVISORY`，不参与限额恢复。
- 当前完整 schema v3 契约要求 TASK 与已创建 GROUP 都具有 Review。版本不增加旧结构迁移或兼容入口；仍在运行且不满足新契约的旧冻结 Graph 应在升级前完成。
- Python 全量 102 项测试、编译检查、Skill 校验、Plugin 校验和 `git diff --check` 通过。

## 0.24.0 — 2026-07-30

发布提交：`a26b100`

- `work-items/` 从根节点开始按 `children/<child-id>/` 递归镜像真实 GROUP/TASK 父子关系；GROUP 可多层、平行或不存在，根 TASK 不再需要形式化 GROUP。Delivery 与节点 baseline、progress、acceptance 和按需 `interfaces.md` 链接同步适配递归路径。
- 精简工作区与 Delivery 总览：工作区只列 Delivery 标识、标题、状态、更新时间和详情入口，TASK 进度与 GROUP 数量留在单个 Delivery；删除人类正文中的投影模板版本，标明 UTC+8 的时间统一为 `YYYY-MM-DD HH:mm:ss`。
- Delivery 和节点 progress 改用状态表格；acceptance 的状态摘要、直接子节点结果与 Review 问题改用表格，长验收输入和证据继续使用结构化列表。
- `loop_context.completionPolicy.reviewFindings` 明确 P0/P1/P2：P0/P1 必须在当前 Review Loop 内修复、验证并独立复审后才可成功，P2 非阻断但必须通过 `result.reviewFindings` 逐项进入验收报告；问题不新增外层 Graph 节点或状态。
- 规划规范明确 GROUP 是动态、可选的真实协调/审查边界，并增加并行 TASK 使用独立 Git worktree 的可选隔离建议；SQLite、事件链、schema v3 和接口显式契约来源保持不变。
- Python 全量 90 项测试、编译检查、Skill 校验、Plugin 校验和 `git diff --check` 通过。

## 0.23.1 — 2026-07-30

发布提交：`e32ce33`

- SQLite 与事件链保持唯一机器权威，不再生成 `hierarchy.json`、`graph.json` 或 `state.json`；`prepare_hierarchy.humanArtifacts` 同步移除这三个无效路径。
- 状态刷新和 `workspace_status` 会清理 Delivery 目录中的旧机器 JSON，同时继续从 SQLite 重建中文 baseline、progress、acceptance 和节点投影；schema v3、Graph、指纹、事件链与恢复行为不变。
- Python 全量 89 项测试、编译检查、Skill 校验和 Plugin 校验通过。

## 0.23.0 — 2026-07-30

发布提交：`005d66e`

- 将 Delivery 的人类控制面从集中式 `overview.md` 拆分为导航总览、`baseline.md` 需求基线、`progress.md` 执行进展和 `acceptance.md` 验收记录；Delivery baseline 作为基线树入口，串联所有 GROUP/TASK 节点但不重复其 Loop 输入。
- 新增 `work-items/<node-id>/` 节点投影树：每个 GROUP/TASK 都有独立 baseline、progress 和 acceptance，GROUP baseline 链接直接子节点；`prepare_hierarchy.humanArtifacts.workItems` 与 `loop_context.humanArtifacts.workItem` 返回对应路径，TASK 的既有 baseline 便捷字段继续指向新位置。
- 新增 TASK 级通用接口投影约定：`protocol` 使用开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等协议均可通过 `payload.interfaces` 显式提交 `CREATE` / `MODIFY` / `DELETE`、接口信息及完整 before/after 快照；控制器只在该 TASK 目录生成中文 `interfaces.md`，代码可辅助提取与校验但不成为动态投影源，接口内容也不影响 Graph 调度。
- 重新 prepare 会原子替换完整 `work-items/` 并清理旧 `task-baselines/`、删除节点或失效接口文件；`workspace_status` 会从 SQLite 为早期 schema v3 Delivery 补建当前投影树，不迁移 hierarchy、Graph、事件链或运行状态。
- schema v3、Graph 节点和 SQLite 格式保持不变；Python 全量 89 项测试、编译检查、Skill 校验和 Plugin 校验通过。

## 0.22.0 — 2026-07-30

发布提交：`c613946`

- 新增只读 `available_agents`，从当前 PATH、常见终端 `--version`、Codex/Claude Code 非敏感模型字段和用户本地 Agent Profile 动态发现 Agent + 当前模型；兼容 CC-Switch 对 GLM、DeepSeek 等任意模型的即时切换，不返回凭据、Base URL 或绝对路径。
- 新增只读 `recommend_executors`，为已准备或冻结 Graph 的全部 TASK、GROUP Review 和 Delivery Review 返回建议 Agent + Model、最多三个备选、置信度、结构化原因与 Review 异构 Agent 独立性状态。
- 所有建议固定为 `ADVISORY` 且 `dispatchAllowed=false`：不启动外部 CLI、不切换模型、不 claim/派遣、不修改 owner，也不写入 schema v3、Frozen Graph、SQLite、事件链或投影；推荐器继续不解析不透明 Loop payload/result。
- Plugin 保持通用：内置可移植探针覆盖常见开发终端，未知终端通过用户本地 JSON Profile 扩展，允许任意安全 Agent/模型 ID，不硬编码个人路径或配置。
- schema v3、Graph 节点模型、SQLite 格式、既有自动开发/人工交接/独立上下文 Review 行为均不改变；MCP 工具面由 17 个增至 19 个。
- 新增单元、Controller/MCP 集成与真实 bundled stdio 回归；Python 全量 84 项测试、编译检查、Skill 校验和 Plugin 校验通过。

## 0.21.2 — 2026-07-30

发布提交：`3982935`

- 明确 payload 只提供目标、约束和已知验收点，Loop 必须从真实代码、契约与数据链路推导 scope 内必要条件；冻结 Graph 不冻结 Loop 内部实现计划。
- 新增 `loop_context.completionPolicy`，要求可修复的实现、测试、数据完整性、边界与 Review finding 留在当前 Loop 内完成方案调整、修正、验证和重新 Review，不再把“Review 未通过”提升为外层终态。
- 收紧 `BLOCKED` 终态：必须显式提供 failure class，并且只用于当前 scope 和权限内没有继续路径的具体条件；`REPLAN_REQUIRED` 继续只表示冻结依赖、资源声明或拓扑必须改变。
- 同步更新 MCP Server instructions、工具描述、canonical Skill、双宿主 Plugin 载荷与回归测试；不改变 schema v3、SQLite 格式或 Graph 节点模型。

## 0.21.1 — 2026-07-30

发布提交：`a74e934`

- 优化冻结前交互：只展示“自动执行 / 手动交接”两个确认选项；需要调整时提示用户直接回复修改意见，不再把“其他内容”或“其他反馈”伪装成第三个选项。
- 收紧 Skill 状态路由：`PREPARED` 需求未变时不重复 prepare；未知写响应按 workspace 状态选择恢复工具；同批无冲突 Loop 可立即分别派遣；`REPLAN_HIERARCHY` 必须先取得取消旧 run 的用户授权，再以新的 Delivery ID 评审替代图。
- 本次补丁只更新 Skill、按需 reference、双宿主 Plugin 载荷和文档契约测试，不改变 Controller、schema v3、SQLite 或既有运行包。

## 0.21.0 — 2026-07-30

发布提交：`9c1e371`

- 将入口正式收口为共享 Python Controller、Host Policy、MCP Adapter 与 stdio Transport：Graph、schema v3、SQLite 和事件链不依赖 MCP/Codex/Claude，双宿主继续复用同一 Controller 与权威状态。
- MCP 优先支持稳定版 `2026-07-28`：新增 `server/discover`，按请求校验协议版本与客户端能力，所有现代成功结果携带 `resultType` 和 server info，`tools/list`/discovery 携带缓存提示；不支持的版本返回标准 `-32022`。
- 保留 `2025-11-25` 初始化式兼容，正式收口为 `2026-07-28` 与 `2025-11-25` 双栈双版本；legacy `initialize` 不会协商出无会话的 `2026-07-28` 语义。Codex 现代项目根按请求解析，Claude/Codex 的审批与兼容策略位于 Adapter 边界。
- Tasks 保持为未声明的可选扩展；现有 Graph/Loop 长任务继续通过显式 `root_id`、`node_id`、`operation_id`、lease 和 SQLite 状态管理，不引入第二套异步状态。

## 0.20.1 — 2026-07-29

发布提交：`a644bf1`

- 修复用户已经明确接受完整验收报告后，`record_user_confirmation` 仍被 Codex manifest、Claude `PreToolUse` Hook 与 MCP `requiresUserInteraction` 重复触发权限弹窗的问题；用户的明确接受现在直接授权控制器写入最终确认事件。
- 保留最终用户确认边界、`confirmed: true` 严格布尔校验和 Review 成功前置条件；本次调整不会自动接受交付，也不授权提交、推送、合并、迁移或发布。
- 收口终态行为：Graph `COMPLETED` / `CANCELLED` 后只返回简短摘要，不擅自更新宿主记忆、触发持续学习或保留 schema v1/v2 旧操作笔记；可移植 ASCII 调度身份作为正式 Skill 契约说明。

## 0.20.0 — 2026-07-29

发布提交：`1393a14`

- 新增 `.layered-delivery/overview.md` 工作区总览，由控制器从 SQLite 汇总全部 Delivery 的中文状态、TASK 完成数量、GROUP 数量、更新时间和详情链接；任一 Delivery 状态变化时同步刷新。
- TASK baseline、GROUP Review 和 Delivery Review 的不透明 payload 改为固定模板的递归 Markdown：常用字段映射为中文标题，对象和数组展开为层级列表，未知字段保留原名，不再向人类投影输出 JSON 代码块。
- Delivery 总览中的状态、节点类型、依赖、资源、运行结果和审查信息统一使用中文标签，不再附带 `PREPARED`、`ACTIVE` 等机器枚举；SQLite、事件链及三类 JSON 文件继续保留完整机器字段。
- 人类投影中的领域文本统一进行 Markdown 转义，MCP 输入只能提供领域数据，不能改变模板结构；根总览、Delivery 总览和全部 TASK baseline 均可从 SQLite 确定性重建。
- 收敛 `loop_context.executionPolicy`：未 claim 且无 Agent 容量时人工交接，已 claim 且租约有效的上下文/Hook 压力才使用 pause/handoff，租约过期固定交给 `advance_graph`；删除 `rules` 中重复的 Capacity 布尔字段，避免宿主将 Capacity 与 lease 错误合并为同一运行提示。

## 0.19.0 — 2026-07-29

发布提交：`df2955c`

- 每个 TASK、GROUP Review 和 Delivery Review Loop 默认路由到独立接收上下文；宿主支持原生 Agent 时优先自动派遣，没有可用容量时才人工交接，总调度上下文不再内联执行 Loop。
- 上下文容量压力或高轮次 Hook 摩擦统一走 `pause_loop → 新接收上下文 resume_loop → 重新 dispatch`；frontier 新增暂停 Loop 与恢复 action，这类执行容量问题不再误报为 `BLOCKED`、`WORKER_LOST` 或 `REPLAN_REQUIRED`。
- 将 TASK 详细调度基线从 `overview.md` 拆分为 `.layered-delivery/<delivery-id>/task-baselines/<task-id>.md`；每份 baseline 通过固定模板展示双指纹、summary、dependsOn、Loop、资源锁、原始 payload 和共享 Skill Hint。
- `task-baselines/` 由控制器从 SQLite 权威状态整体原子替换；重新 prepare 删除或改名 TASK 时自动移除旧文件。overview 只保留 Delivery 状态、GROUP/TASK 清单、TASK 运行快照及 Review/最终进度。

## 0.18.1 — 2026-07-29

发布提交：`f9ef70b`

- 修复 `freeze_hierarchy.confirmed` 在 MCP 宿主间被序列化成 `"true"` 或 `1` 后触发严格身份校验失败的问题：冻结工具不再暴露内部确认布尔值，由适配器在已验证的用户方式选择后注入 Python `True`。
- 恢复单次冻结交互：自动执行与手动交接都是完整授权并确认开发，选择本身即为冻结确认；调整需求及其他反馈不确认、不冻结，继续交互并重新 prepare。冻结工具在宿主权限层统一自动批准，不再追加通用 Yes/No 或任何冻结弹窗。
- `record_user_confirmation.confirmed` 的 JSON Schema 显式声明为 boolean，控制器验证器拒绝字符串和整数伪布尔值。
- 新增 MCP 实际 prepare→freeze 回归测试与双宿主权限配置测试；本次修复不改变 schema v3、SQLite 数据格式或既有 PREPARED hierarchy。

## 0.18.0 — 2026-07-29

发布提交：`ea1f109`

- 用递归 `GROUP` / `TASK` 模型替换固定 Delivery / Capability / Task 三层：TASK 是唯一执行叶子，GROUP 可混合包含直接子 GROUP/TASK；Delivery 保留为顶层 Graph/run 与最终验收边界，不属于 work item kind。
- hierarchy 最外层收敛为 `delivery` 与 `root`；schema 版本和共享 `skillHints` 归入根包装节点，嵌套节点只保存自己的 definition、Review 与 children。
- 每个 GROUP 编译为 `GROUP_JOIN → GROUP_REVIEW_LOOP`；子 GROUP 只有在自己的 Review 成功后才向父层贡献终态，根工作项最终进入 `DELIVERY_REVIEW_LOOP → USER_CONFIRMATION`。
- `dependsOn` 改为直接兄弟 GROUP/TASK 的启动屏障，支持 TASK→TASK、TASK→GROUP、GROUP→TASK 与 GROUP→GROUP；GROUP 依赖会阻止目标子树入口，直到来源 GROUP Review 成功。
- 保留外层调度边界：TASK、GROUP Review 与 Delivery Review Loop 各自负责实现方法、测试、Gate、修正和实际 Skill 选择；共享 Skill Hint 只作为晚绑定优先提示。
- 递归 GROUP/TASK 结构仅存在于 hierarchy 与编译 Graph；工作区共享 `.layered-delivery/scheduler.db`，并按稳定 `delivery.id` 写入 `.layered-delivery/<delivery-id>/{hierarchy.json,graph.json,state.json,overview.md}`，允许多份需求交付目录并存且不继续展开 GROUP/TASK 目录。
- 恢复可核对的人类投影：`overview.md` 绑定 hierarchy/graph 指纹和冻结/运行状态，包含完整 GROUP/TASK 清单及每个节点的摘要、依赖、Loop、资源锁、原始 payload 和进度；文案使用中文，时间统一显示为 UTC+8。
- 投影收敛为控制器从 SQLite 权威状态通过固定版本模板原子生成的四类文件；Agent 只能通过 MCP 读取调度数据，不能直连 SQLite、选择模板或直接创建、修补投影。
- 投影刷新、事件快照重放和物化状态重建纳入统一 scheduler lock；所有运行变更在锁内取得单调提交时间，避免并发请求让 SQLite、`state.json` 或 `overview.md` 回退到旧状态。
- 写操作在事务内重新校验 Delivery namespace、hierarchy 指纹及 hierarchy→graph 精确编译绑定，避免成对篡改图和指纹后产生部分提交；COMPLETED/CANCELLED Graph 的 frontier 与审计时间保持稳定终态。
- 此版本是 schema v3 的破坏性语义替换，不提供 0.17.x hierarchy 或更早运行包的迁移/兼容入口；升级前需要归档旧 `.layered-delivery` 运行包。

## 0.17.0 — 2026-07-29

发布提交：`55b13ad`

- 将 `layered-delivery` 收敛为外层 Graph Scheduler：Task 和最终审查统一为可插拔 Loop，Capability/Delivery 只保留 Join，最终仍由用户确认。
- 删除外层 `scope`、`developmentPlan`、test command、Gate level、required Skill stage、文件授权、evidence hydration、remediation 和 Gate→development 路由；实现、测试、Gate、修正及 Skill 调用由各 Task Loop 内部协议负责。
- schema v3 新增不透明 `loop.ref/payload/resourceClaims` 与标准 `SUCCEEDED/BLOCKED/REPLAN_REQUIRED/CANCELLED` outcome；资源声明改为多项目、多模块可用的精确排他锁键。
- schema v3 在 hierarchy 顶层新增共享 `skillHints`：用户给出的 Skill 只作为建议性的运行时优先提示，需求阶段不分配到 Task/阶段、不编译进 Graph 节点；每个 Task/Review Loop 根据真实上下文与宿主可用 Skill 独立选择，调度器不校验激活或生命周期。
- MCP 面收敛为 17 个调度工具，覆盖层级准备/冻结、frontier、Loop claim/heartbeat/pause/resume/result、租约推进、事件、重建、取消与最终确认。
- SQLite 权威收敛为 scheduler hierarchy/run/node attempt/event；基础设施故障预算内自动重试，业务阻断不自动重跑；可从哈希事件链重建物化状态。
- 强化 Loop 边界：过期 lease 的旧 operation 不得 pause 或提交结果；`loop_context` 额外返回传递上游 Task Loop 结果，使根 Join 后的 Review Loop 能审查实际 Loop evidence，而不要求 Join 解释业务内容。
- 重写 Skill、references、Codex/Claude Plugin 描述和敏感工具策略；删除旧控制器模块与旧协议测试，不提供兼容入口。
- 此版本是 schema v3 的破坏性语义替换：检测到旧 `governance.sqlite3` 时明确阻断，不迁移、不并存；创建新 Graph 前需要先归档旧运行包。

## 0.16.6 — 2026-07-29

发布提交：`207046e`

- 修复 Codex MCP 初始化后立即断开：兼容 `notifications/initialized` 的 `params=null`，并允许 `tools/list` 携带标准对象型 `_meta`，避免工具目录请求被误判为 `Invalid params` 后出现 `has_cached_tools=false`。
- 新增 Codex 真实 stdio 握手回归，覆盖空参数初始化通知、带请求元数据的工具列表以及非法 `_meta` 类型；schema v3、38 个 MCP 工具和权限边界保持不变，全量 249 项测试通过。

## 0.16.5 — 2026-07-29

发布提交：`6e1d16e`

- 新增只读 `hierarchy_contract` MCP 工具，按根类型与输入模式返回完整 schema v3 JSON Schema、可直接提交的有效示例和核心不变量；规划 Skill 在 `prepare_hierarchy` 前按需读取契约，不再从失败响应或控制器源码试探内部类型。
- 新增根 `compactTask` 输入，以显式 `gateLevel` 同时覆盖 `LIGHT` 与 `FULL` 单 Task；控制器仍只持久化完整 schema v3，原 `compactLightTask` 行为保持不变。
- hierarchy、node、definition、execution、development plan 及其嵌套记录的结构错误统一返回字段路径、必需/可选/实际/缺失/未知键和允许枚举；MCP 工具数由 37 增至 38，全量 248 项测试通过。

## 0.16.4 — 2026-07-29

发布提交：`2f1d7ef`

- 将内部依赖从聚合 façade 全部改为直接指向职责模块，44 处间接实现导入降为 0；`evidence`、`model`、`graph_runtime`、`repository` 与 `operations` 仅以显式 `__all__` 保留合计 29 个稳定公共入口。
- 将 `repository.py` 从 46,271 字符压缩到 3,766 字符，并继续分离工作区、层级查询、registry 契约校验、SQLite 事务/投影调度和 package 物化职责；各职责模块都有独立源码上下文预算，避免把大型聚合文件简单搬家。
- 新增公共 API、内部导入方向与职责模块体积回归；源码、canonical Skill 和双宿主 Plugin 运行包保持逐文件一致。schema v3、37 个 MCP 工具及权限边界保持不变，Python 全量 239 项测试及 83% 分支覆盖率通过。

## 0.16.3 — 2026-07-29

发布提交：`051564c`

- 将 repository、Graph runtime、evidence、model 和 MCP operations 的聚合实现拆分为职责单一的内部模块，同时保留原公开导入面，降低 Agent 按文件检索和 MCP 精确处理时需要加载的无关上下文。
- 精简 37 个 MCP 工具的注册 schema，移除重复顶层展示元数据并压缩 `payloadRef` 声明；工具目录紧凑 JSON 从 40,928 字节降至 31,078 字节，约减少 24.1% 的注册上下文。
- 新增模块体积、公开兼容面、MCP schema 大小和源/Skill/Plugin 镜像一致性预算回归；schema v3、工具数量、运行时严格校验、MCP-only 与权限边界保持不变。Python 全量 236 项测试及 83% 分支覆盖率通过。

## 0.16.2 — 2026-07-28

发布提交：`ab183cc`

- 面向用户生成的 Markdown、SVG、进度、警告和验收报告默认统一使用简体中文；双语标题、表头和内部英文状态码不再直接进入普通展示，技术标识仅保留在必要的审计位置。
- 新增集中式展示时间渲染，用户文档默认使用东八区，并在字段名或文档说明中统一标注，时间值不再重复附加 `UTC+08:00`；MCP、SQLite、事件链和 JSON 的机器字段继续使用英文，机器时间继续使用 UTC。
- Skill 增加中文展示与结构化 `userPrompt` 转述规则；schema v3、MCP-only、固定项目根、图调度和权限边界保持不变。Python 全量 229 项测试通过。

## 0.16.1 — 2026-07-28

发布提交：`f2aaf07`

- 从运行时 `layered-delivery` Skill 移除维护专用 `dogfood` 说明；该授权边界只保留在仓库级 `AGENTS.md`、控制器自托管保护与相关回归测试中。
- Plugin MCP-only、固定项目根和通用确认参数限制保持不变；canonical Skill 与双宿主 Plugin 载荷继续一致。

## 0.16.0 — 2026-07-28

发布提交：`65ac6bd`

- 新增根 Task `compactLightTask` 快速输入，由控制器扩展并只保存完整 schema v3；模块级 Scope 与 ADD-only `generatedFileRoots` 在降低规划成本的同时继续保持精确修改/删除授权。
- Graph frontier、Task context、handoff 与 MCP 响应默认使用紧凑模式；迁移结果携带 `nextFrontier`，等待轮询支持 revision 去重，详细 blocked 状态只在诊断时按需读取。
- result、gate、review 与 confirmation 支持 `evidenceDelta`，由控制器从冻结契约补齐测试 argv、需求追踪和授权信息，再保存完整 canonical evidence。
- SQLite 投影改为按实际变化节点及受影响需求树增量刷新；最终用户验收阶段的同契约修正继续回到原 Task，不再全量重建无关需求投影。
- 压缩 MCP output schema 和调度上下文，减少工具注册、长任务恢复、Agent handoff 与多轮门禁的上下文占用，并新增上下文预算和增量投影性能回归。
- Skill 入口与 references 从 23 个相关文件、1,636 行精简为 6 个文件、226 行；删除由工具 schema、Graph、SQLite 和 Plugin 权限机械保证的重复说明，只保留规划、执行、验收与异常传输核心边界。
- schema v3 是唯一标准，不增加旧 schema 兼容入口；Plugin 继续保持双宿主、MCP-only、一次冻结确认和最终用户确认边界。Python 全量 227 项测试通过。

## 0.15.5 — 2026-07-28

发布提交：`78d18f7`

- 收敛用户显式开发 Skill 的规划语义：不在需求分析阶段预读或递归展开 Skill，不从 Skill 内容派生业务需求、Task 或门禁；直接按用户给出的 catalog 名登记为仅含 `DEVELOPMENT` 的执行约束，并在实际 worker 开发时原生调用。只有用户另行明确指定其他阶段时才进入 GATE/FINAL_REVIEW。
- `prepare_hierarchy` 新增宿主级 root 与项目级 project 双来源 `available_skills` 预检；自定义 required Skill 不存在或疑似拼错时在写入治理状态前阻断，同时返回机器可处理的 `skillOptions` 和可直接展示的中文 `userPrompt`，其中包含带来源的近似候选与修正、安装兜底指引。
- 调整 Scope 规划粒度：按最小可用模块边界使用 `module/**`，为同模块必要文件生成保留空间，同时继续由 `developmentPlan.fileChanges` 冻结精确写授权；禁止全仓库 `**`，并明确重叠 Scope 会限制 Graph 并行。
- 瘦身 Task 开发交接：`development-handoff.md` 不再复制完整 `dispatch_task` 上下文、父级开发计划、完整 Skill policy、lease policy 和后续 evidence 模板，只保留开发方案链接与 worker 开工所需字段；完整机器上下文继续由 SQLite/MCP 权威保存。
- 明确速度优先的 LIGHT 策略：低风险单目标需求默认使用根 Task，允许简洁说明、定向测试和按需读取，同时保留独立验收、精确文件授权、真实测试、P0/P1 与最终用户确认。
- 将方案确认、`active|manual` 方式选择与 `freeze_hierarchy` 合并为一次用户授权：用户选择方式后 Agent 必须紧邻调用冻结工具，不再出现第二个工具批准弹窗，也不得从旧对话推断或重放选择。
- Claude `PreToolUse` Hook、Codex manifest prompt 和 `anthropic/requiresUserInteraction` 仅保留 Graph 重建、Graph 取消、人工审查接受和最终用户确认这 4 个独立敏感动作；旧版 Claude Code 的服务端拒绝范围同步收敛到这 4 个工具。
- 冻结仍由专用 MCP 操作注入领域确认并以层级指纹 compare-and-swap，工具参数继续不暴露通用 `confirmed` 布尔值；单次确认减少重复交互，不放宽方案指纹、最终验收或外部权限边界。

## 0.15.4 — 2026-07-28

发布提交：`d611faf`

- Claude Plugin 新增 `hooks/hooks.json` 与失败关闭的 `PreToolUse` Hook；Skill 可用一个 MCP Server 通配符预批准常规调用，同时对方案冻结、Graph 重建、Graph 取消、人工审查接受和最终用户确认继续逐次强制 `ask`。Codex 仍由自身 Plugin manifest 对同一组工具保持 `prompt`。
- Claude Hook 对非对象事件、非字符串工具名、JSON 解码错误和内部输出异常统一以退出码 2 失败关闭，不会因异常退出码 1 被宿主当作非阻断故障继续执行。
- 修复 Claude Code 已连接 MCP 后工具获取失败：所有工具的 `outputSchema` 根节点显式声明 `type: object`，兼容当前 MCP schema 和 Claude 工具注册校验；诊断文档区分“进程未启动”与 `Connected · tools fetch failed`。
- 将交付形态收敛为 Plugin-only：单个 `layered-delivery` Plugin 同时携带一个 Skill 和一个 MCP Server；移除全部 Python console scripts、`bin/hdg.py`、Skill `scripts/hdg.py` 与 `python -m hdg` 入口，Plugin 运行包不再包含 `cli.py` 或 `__main__.py`。宿主直接运行 Plugin 内的 `hdg_mcp.py`，用户不需安装 Python package。
- MCP 未安装、未注册、未连接或工具注册失败时立即返回 `PLUGIN_MCP_UNAVAILABLE` 并停止，不开始或恢复治理写入，不允许 Shell、直接 Python API 或 SQLite 降级绕过。
- 开发中 stdio 连接意外终止时明确报告 `PLUGIN_MCP_DISCONNECTED`；响应未送达的写操作标记为提交状态未知，重连后从 `workspace_status`、`graph_frontier` 核对 SQLite 权威状态，再继续 claim 或按 `WORKER_LOST` 自动恢复。
- Graph frontier、租约策略、evidence contract 和生成的 handoff 全部改为结构化 `mcpCall`/`submitMcpCalls`，不再返回已删除的 CLI `commandHint`；新增回归扫描，阻止旧 kebab-case CLI 提示重新进入源码、Plugin 载荷或交接文档。
- Claude、Codex、Cursor 或其他 Agent 仍可跨宿主规划和接续同一 frozen graph，但接收宿主必须同时提供兼容 Plugin MCP 与真实原生 Skill 调用入口；`requiredSkills` 继续支持任意 catalog 名，也继续兼容省略或空数组。
- 移除遗留 CLI harness 与 CLI 专属测试，将图查询、心跳性能和独立审查回归改为 MCP/应用服务路径；新增 Claude Hook 权限、双宿主真实 stdio 握手和 MCP-only 提示回归后全量 216 项测试通过。

## 0.15.3 — 2026-07-28

发布提交：`f3ebf4f`

- 修复 active/manual 的 required Skill 二次确认缺陷：用户批准整树与开发方式时已完成一次授权，frontier action 改为执行适配器自动原生调用指令；策略与缺失激活错误明确 `userActionRequired=false`，禁止要求用户再次输入 `$skill`、确认 Skill 或复制触发文本，同时保留逐 attempt/operation 的激活、符合性和真实产物审计。
- 分离方案创建宿主与当前阶段执行宿主：frozen `hostRuntime` 只保留规划审计和宿主自动化提示，不再限制 required Skill 的实际执行宿主；Claude、Codex、Cursor 或其他 Agent CLI 均可恢复同一 frozen graph，无需重新 prepare/freeze。
- required Skill 新激活统一使用 `HOST_NATIVE_SKILL`，不再硬编码 Claude/Codex 机制分支。Plugin MCP 从当前连接的 sandbox metadata 或标准 `clientInfo.name` 生成安全的实际 Agent 标识；CLI fallback 的 Skill activation/conformance 显式要求任意合法 `--host-runtime`。既有 schema v3 的 Claude/Codex 激活事件仍可验证和投影。
- MCP/CLI/直接 Python 生命周期入口均要求明确的当前执行宿主，不再回退到 frozen planning host。会话身份和 native invocation ID 属于宿主上报凭证；控制器验证绑定、唯一性与符合性，但不宣称在缺少宿主签名/回调时提供密码学调用证明。
- `record_skill_conformance` 要求由原 activation 的同一执行宿主写入；门禁从当前 node attempt 的有效 Graph 事件判断，不再按方案创建宿主过滤真实凭证。既有 0.15.1/0.15.2 frozen delivery 可直接由另一宿主接续。
- 增加 Claude/Codex/Cursor/其他 Agent 的规划开发组合、错误原生机制、跨宿主 conformance、防伪事件、MCP 客户端归一化、CLI fallback 和 manual 交接回归；全量 231 项测试通过。

## 0.15.2 — 2026-07-28

发布提交：`4ff9304`

- 精简 manual 冻结返回的 `handoffCommand`，只保留需求 ID、`graph_frontier` 恢复、完整 Graph 执行、开发测试门禁、禁止重复冻结和最终确认边界，降低人工复制与理解成本。
- Claude Auto、MCP/CLI fallback、required Skill 激活与符合性、时区展示和权限约束继续由结构化返回、`requirement-handoff.md` 与 Skill 契约承载，不削弱现有治理门禁。
- 增加交接命令固定文案长度与实现细节隔离回归；Python 3.14 全量 220 项测试通过。

## 0.15.1 — 2026-07-28

发布提交：`01af219`

- required Skill 不设控制器白名单：需求冻结的任意合法 catalog 名都逐项执行。Claude 必须以 Skill tool-use、Codex 必须以显式 `$skill` 原生触发形成 `SKILL_ACTIVATED` Graph 凭证；Read/load 不算激活，同一原生调用 ID 不得复用。
- MCP 工具由 35 个增至 37 个，新增 `record_skill_activation` 与 `record_skill_conformance`。result、gate 和 review 成功前须提交绑定当前 node attempt 的非空检查并全部 PASS，artifact 中的 `skillUsage` 自述不能替代原生激活与实际符合性。
- 开发复核与验收报告新增“实际 Skill 原生调用与符合性”，直接投影 Graph 中的 host/mechanism、attempt、native invocation ID、调用/符合性状态、命名检查和凭证 hash，不从 baseline 或文件读取记录推断；恢复时重新校验已存 artifact 对应的 activation/conformance。
- schema v3 的 `requiredSkills` 可省略或显式传 `[]`，两者都规范化为空数组并保持无门禁兼容；不新增旧 schema 迁移入口。
- 增加任意 Skill 名、Claude/Codex 原生调用、load 拒绝、凭证唯一性、符合性门禁、报告真实性和恢复校验回归；Python 3.12 全量 219 项测试通过。

## 0.15.0 — 2026-07-28

发布提交：`64879e4`

- 新增 Python 标准库实现的单进程 stdio MCP Server，以 35 个结构化工具覆盖工作区状态识别、分层规划、Graph 推进、执行、门禁、审查、确认、恢复和超限 payload 暂存；CLI 保留为 MCP 不可用时的 fallback。
- MCP 与 CLI 改为共用应用服务和 SQLite repository，不通过 MCP 包装或解析 CLI 子进程。
- 项目根在 Server 生命周期内绑定一次：Claude 使用项目环境变量，Codex 使用宿主注入的可信 sandbox cwd；`root`、维护专用 `dogfood` 和确认布尔值 `confirmed` 不进入工具参数，根发生漂移时拒绝调用。
- Codex/Claude Plugin 增加各自的 MCP 配置与内嵌启动器，权限从任意 Bash/Python 通配规则收窄为 MCP tool 级控制；30 个中段工具可自动执行，冻结、重建、取消、人工审查接受和最终确认保持人工 prompt。识别到低于 2.1.199 的 Claude Code 时，Server 拒绝可能被旧宿主忽略强制交互元数据的敏感调用。
- 明确 active/manual 契约：用户确认开发方案后，当前窗口或新运行窗口从同一 graph run 自动完成范围内开发、测试、门禁、预算内重试和租约恢复，直到最终验收阶段；`USER_CONFIRMED` 及 Git、发布、迁移和新增外部权限仍需用户授权。
- schema v3 baseline 新增 `requiredSkills`：按 `DEVELOPMENT/GATE/FINAL_REVIEW` 冻结可移植 Skill 名和使用目的，根级要求向后代继承；frontier、Task context 和 evidence contract 持续投影，成功迁移必须逐项提交具体 `skillUsage`。最终验收报告按 Task、operation 和 result 状态聚合实际开发调用，并另列 gate/review 使用审计。
- required Skill evidence 拒绝控制器模板占位符；存在只读隔离后代时机械阻断祖先聚合 gate、根 review 和最终用户确认，同时保持无关需求与有效兄弟 Task 可继续。
- 冻结的 `FINAL_REVIEW` Skill 不可用时可用 `REVIEW_BLOCKED` 持久化具体阻断 evidence；Graph 明确路由到人工干预，并可在问题消除后通过 `retry-item` 创建新的 review attempt，不能绕过为 PASS。
- 补齐 MCP 生命周期、请求 ID、已知工具参数错误、输入深度/复杂度限制和自由文本脱敏；超出 8 MiB 的输入行只报错一次、限块排空后继续，环境变量凭据、常见服务 token 和宿主/容器绝对路径不回传模型。
- 新增目标绑定的无损 payload 暂存：64 MiB 单包、1 MiB 分块、每项目 16 个未过期 upload / 256 MiB 配额、128 字符 upload ID 上限和 Server 生成的 generation fencing；逐块及整包 SHA-256、严格 JSON/UTF-8、重复键/孤立代理项/非有限数字拒绝、紧凑无键名回显状态与一小时逻辑过期。finalize 不修改业务状态，仍须调用原业务工具并经过原权限与事务门禁；分块解决传输，不宣称宿主上下文压缩。
- 新增无参数 MCP `workspace_status` 与 CLI `workspace-status`，机械区分 `ABSENT`、`STAGING_ONLY` 与 `ACTIVE`；Graph/interaction MCP 日志改为最多 200 项的 cursor 分页，并把查询分页下推到 SQLite/事件流，避免把整份历史保留在响应内存。
- 强化数据库与输出边界：治理库拒绝符号链接、跨路径硬链接、缺失 payload `CHECK`/复合键/级联外键/过期索引的伪 schema v3；dogfood 检测覆盖源码仓库子目录和带 TOML 行内注释的项目名；自由文本脱敏覆盖带空格 Windows 路径及常见容器路径。
- 增加 MCP/CLI、严格 JSON、payload 并发与配额、权限、schema、分页和 required Skill 审计回归；Python 3.14 全量测试 213 项通过。
- 只读查询改用 SQLite `mode=ro`，不再持久改写数据库日志模式；提交后投影增加可重入的跨线程、跨进程轻量锁，并在锁内追赶最新 revision，消除并行写入时的旧投影覆盖和 Windows 文件替换竞争。

## 0.14.1 — 2026-07-27

发布提交：`cca5765`

- 将硬过期 claim 从 frontier 的阻断建议升级为正式 `ADVANCE_GRAPH` 动作，使执行循环能够确定性回收 `WORKER_LOST` 并自动创建下一 attempt。
- 强化执行适配器的心跳与收尾契约：当前会话在没有独立适配器时负责续租，代码和测试完成后必须提交 `task-result` 并继续消费 gate/review。
- manual 交接明确硬过期恢复无需人工重置；新 operation 可重新认领并提交已经完成的工作。
- 增加“租约硬过期 → 自动推进 → 新 operation → `IMPLEMENTED`”端到端回归；Python 3.14 全量测试 118 项通过。

## 0.14.0 — 2026-07-27

发布提交：`b14c858`

- 将 Markdown/SVG 投影移出 SQLite `BEGIN IMMEDIATE` 写事务，数据库提交并关闭写连接后再生成投影，缩短写锁持有时间。
- 将高频 `heartbeat-task` 改为增量路径，只更新当前 Task 和必要 graph run 数据，并仅刷新 execution graph、timeline 与 frontier。
- Registry 改为只更新实际变化节点及必要祖先；内容未变化的行跳过 `UPDATE`。
- 投影文件写入增加内容比较，相同内容不再执行临时文件替换和 `fsync`。
- 投影失败时返回 `WORK_ITEM_PROJECTION_REFRESH_REQUIRED`，保留已提交机器状态，并可通过 `refresh-projections` 修复。
- 增加 revision 追赶和交互日志唯一 revision，防止并发提交时旧投影覆盖新状态。
- 新增全局 `--timing`，在 stderr 输出 SQLite、投影和文件写入的分阶段耗时，不改变 stdout JSON 契约。
- 增加性能、并发和投影恢复回归测试；Python 3.14 全量测试 117 项通过。

## 0.13.0 — 2026-07-27

发布提交：`b5bc9f9`

- 将验收模型收紧为 requirement scoped acceptance，每个需求必须拥有独立、可观察的验收条件。
- 跨需求验收只允许作为追加的集成验收，不能替代任一需求自己的通过条件。
- Gate evidence 增加需求追踪信息，验收项、工作项和证据之间保持明确绑定。
- 强化 hierarchy、remediation、runtime FSM 和 SQLite 存储中的验收一致性校验。

## 0.12.0 — 2026-07-27

发布提交：`50f15b0`

- 为 `task-result` 增加按当前 operationId 查询的 result evidence contract，提供 `IMPLEMENTED` 与 `BLOCKED` 模板及逐字段验证。
- 完整结果 artifact 通过 stdin 提交并保存在 SQLite，控制器计算并保存规范摘要。
- 引入可靠心跳、软租约、竞争宽限和硬到期语义，由执行适配器按 `nextWakeAt` 自动续租。
- 增加 `WORKER_LOST` 回收、旧 operation fencing、结构化失败分类和预算内自动重试。
- 将 attempt、租约、心跳和恢复状态纳入 graph frontier、timeline 与可视化投影。

## 0.11.1 — 2026-07-24

发布提交：`5566bcd`

- 精简 `SKILL.md` 入口，只保留核心契约、入口选择、推进流程和按动作读取规则。
- 将详细协议继续保留在按需 references 中，减少首次加载的上下文占用。
- 调整 Codex/Claude 的 Skill 元数据，并增加入口体积和内容路由回归检查。

## 0.11.0 — 2026-07-24

发布提交：`d7c93e8`

- 完善 Task gate 失败后的恢复路由：执行修复、重新认领、复测和再次门禁，不在错误状态下循环 gate。
- 为开发结果、Task gate、聚合 gate、独立审查和用户确认建立更严格的 evidence contract。
- 强化 evidence 与 run、node、attempt、graph fingerprint 和 baseline 的绑定。
- 完善 retry budget、失败分类、图事件回放及修正后的下游失效逻辑。
- 简化公开安装方式，并将 Plugin 源仓库与内部 Marketplace 版本映射拆分维护。

## 0.10.0 — 2026-07-23

发布提交：`7872d25`

- 项目和 Skill 正式更名为 `layered-delivery`。
- 将执行模型升级为 Graph Engineering：编译执行图、治理图、关键路径、frontier、attempt 和事件回放。
- 将 Task 选择、并行数量、调度顺序、重试和失败恢复收归控制器管理。
- 增加 graph runtime 的暂停、恢复、取消、重建和可观察性，并生成 SVG 图形投影。
- 将 evidence artifact 改为通过 stdin 写入 SQLite，隔离仅 evidence 引用过期的历史节点。
- 增加同 Task 验证修正、根节点独立进度、月度 workspace overview、直接导航和可复制 manual handoff。
- 统一工作区级状态迁移图，增强 Claude 自动执行和跨宿主可移植调用。
- 增加交付响应契约以及 Codex/Claude 双宿主 Plugin 载荷和 Marketplace 清单。

## 0.9.0 — 2026-07-17

发布提交：`225f078`

- 将 `.layered-delivery/governance.sqlite3` 确立为唯一机器权威，Markdown 降为可重建的人类可读投影。
- 增加事务化 registry、定义、状态、上下文、报告和交互审计存储。
- 增加层级进度投影、表格化整树进度和明确的当前执行状态。
- 增加一次性 manual requirement handoff，并保留 active/manual 两种开发方式。
- 强化数据库损坏、schema 不符、投影丢失和并发写入时的恢复边界。

## 0.8.0 — 2026-07-17

发布提交：`78a1bc9`

- 精简冻结后的自治交付循环，由控制器持续返回下一步动作和响应契约。
- 统一 active/manual 的 graph 推进语义，减少逐 Task 人工确认。
- 将 schema version 保持为控制器输入和机器契约，不要求用户在自然语言中维护版本信息。
- 完善门禁、审查、用户确认和失败恢复的端到端路由。

## 0.7.0 — 2026-07-17

发布提交：`8607170`

- 改为从一份根级 `development-plan.md` 评审并一次冻结完整需求树。
- 完整物化 Task、Capability 和 Delivery 的 definition、state、baseline 与目录结构。
- 增加 hierarchy fingerprint compare-and-swap，方案变化后旧确认自动失效。
- 统一整树准备、冻结、开发、门禁和最终验收入口。

## 0.6.0 — 2026-07-17

发布提交：`f93282a`

- 将治理控制器从 Node.js 全面迁移到 Python 3.10+ 标准库实现。
- 建立 `pyproject.toml`、`hdg` Python CLI、源码包、Skill 内嵌载荷和构建脚本。
- 移除 Node/npm 运行时和旧安装脚本依赖。
- 将模型、规划、执行、验收、证据、投影和安全文件操作迁移为 Python 测试体系。

## 0.5.0 — 2026-07-17

发布提交：`53332ae`

- 简化 Skill 方案审批和 Agent handoff，减少重复人工确认。
- 补全 Task、Capability、Delivery 的分层验收闭环和根级最终确认。
- 结构化 CLI 输入统一改为 stdin，避免大 JSON 经命令行参数传输。
- 增加冻结前开发方案复核，强化文件、接口、测试和验收项的可评审性。

## 0.4.0 — 2026-07-16

发布提交：`f723c24`

- 引入当前完整 schema v3 层级模型。
- 支持最浅合法的根 Task、Capability→Task 和 Delivery→Capability→Task。
- 恢复 active/manual 开发方式的机械门禁，禁止从自然语言推断执行授权。
- 将控制器构建为随 Skill 分发的内嵌 CLI，减少宿主安装耦合。

## 0.3.0 — 2026-07-16

发布提交：`2b54245`

- 从 gated workflow 升级为 hierarchical delivery governance。
- 增加确定性的 workspace task registry、生命周期恢复和项目规划。
- 根据真实工作规模选择 Task、Capability 或 Delivery 层级。
- 增加分层 work-item 模型、运行时状态和端到端层级流程测试。
- 将长工作流拆为分阶段图示和按需参考文档。

## 0.2.0 — 2026-07-14

发布提交：`2c95ceb`

- 增加可视化 gated workflow、显式开发方式门禁和人类可读进度跟踪。
- 增加自动并行 Agent 调度、自检、分级验收和根级最终验收报告。
- 泛化 Agent handoff 与验收能力路由，支持多工作区开发交接。
- 引入原生 schema v2 workspace gate，并加强跨工作区证据校验。

## 0.1.0 — 2026-07-13

发布提交：`1c7c9d3`

- 首次发布中文 gated AI development Skill。
- 提供 light/full 工作模式、baseline 冻结、开发交接和验收流程。
- 建立安全文件操作、命令校验、模式识别、CLI、安装脚本和完整测试基础。
- 提供最初的 Skill references、配置模板和 Codex UI 元数据。
