# 多工作区与多微服务交接

## 目录

- [适用范围](#适用范围)
- [协调工作区](#协调工作区)
- [交接覆盖门禁](#交接覆盖门禁)
- [工作区授权模板](#工作区授权模板)
- [覆盖结果模板](#覆盖结果模板)
- [多工作区开发快照](#多工作区开发快照)
- [依赖顺序与并行](#依赖顺序与并行)
- [交接提示词](#交接提示词)
- [开发结果与机械门禁](#开发结果与机械门禁)
- [变更与阻断处理](#变更与阻断处理)

## 适用范围

只要一个冻结任务需要在两个或更多仓库、工作区、模块根目录或微服务中写入，就使用本契约。它同样适用于前后端分仓、SDK 与服务端联动、基础设施与应用联动，不依赖特定语言、Agent 产品或操作系统。

不要把“开发 Agent 缺少另一个仓库后返回 `BLOCKED`”当成正常交接。宿主在生成 `prompt.md`、选择执行拓扑或派遣开发 Agent 前，必须先证明所有会产生写入的 `T-NNN` 都有完整、明确且经用户确认的工作区授权。不得生成一个宿主已知必然阻塞的交接。

## 协调工作区

多工作区任务只选择一个协调工作区保存 `.ai-dev-loop/<task-id>/`。通常选择用户发起任务时所在的仓库，或最能代表整体交付的仓库。其他业务工作区只保存业务改动，不复制任务包，也不允许开发 Agent 在其中创建 `.ai-dev-loop/**`。

协调工作区不是默认的唯一写入范围。每个参与工作区都必须在当前轮次的授权清单中单独列出。schema v2 中的每个 `root` 必须是对应 Git worktree 的顶层根目录；同一仓库内的前端、后端或模块子目录通过 `allowedPaths` 和测试命令 `cwd` 表达，不能伪装成两个仓库。所有根路径都使用规范化绝对路径；业务允许路径使用相对于对应根目录的路径或安全 glob。不要依赖当前工作目录、盘符假设、符号链接别名或“相邻仓库”等隐含约定。

## 交接覆盖门禁

冻结后、开发方式选择前执行以下检查：

1. 从冻结任务中找出全部会产生写入的 `T-NNN`，并标明提供方、消费方、共享契约和依赖关系。
2. 为每个任务绑定一个或多个稳定的 `workspaceId`、规范化绝对根路径、仓库相对允许路径和测试工作目录。
3. 验证每个根路径存在、可由接收开发者读取和写入；需要 Git 归属时验证它是预期仓库，并记录分支、HEAD 和已有改动。
4. 验证允许路径非空、位于对应根目录内、不包含 `.git/**`、`.ai-dev-loop/**`、凭据或其他受保护路径。
5. 验证每条测试命令的 `cwd` 位于已授权工作区内；不得用一个仓库的测试结果代表另一个仓库。
6. 验证提供方到消费方的依赖图可执行且无环；未冻结或尚不存在的公共契约不能假装已就绪。
7. 把用户对精确工作区和写入范围的确认写入 `workspace-authorization.json`，再派生 `workspace-coverage.json`。

只有 `workspace-coverage.json.status` 为 `PASS` 时，才能进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`、生成 `prompt.md` 或启动任何开发 Agent。否则状态为 `WAITING_FOR_WORKSPACE_AUTHORIZATION`，在 `progress.md` 列出缺少的工作区、任务 ID、所需路径和解除条件，然后等待用户补充或授权。

单工作区任务可以继续使用原有快照格式，不强制生成这两个文件；一旦冻结范围明确涉及其他工作区，就必须使用本门禁。宿主无法验证手动接收端的实际权限时，至少要验证当前已提供的绝对路径和用户授权，并要求接收端在首次写入前做同样的只读预检。

## 工作区授权模板

由宿主创建 `rounds/round-NN/workspace-authorization.json`。它是当前轮次的操作授权，不改写冻结需求：

```json
{
  "schemaVersion": 1,
  "task": "task-id",
  "round": "round-01",
  "coordinatorWorkspaceId": "consumer-service",
  "status": "CONFIRMED",
  "confirmedBy": "user",
  "workspaces": [
    {
      "id": "provider-service",
      "root": "/absolute/path/to/provider-service",
      "access": "read-write",
      "taskIds": ["T-001"],
      "allowedPaths": ["provider-api/src/**", "provider-core/src/**"],
      "testCommands": [
        {
          "cwd": "/absolute/path/to/provider-service",
          "argv": ["tool", "test", "provider-module"]
        }
      ]
    },
    {
      "id": "consumer-service",
      "root": "/absolute/path/to/consumer-service",
      "access": "read-write",
      "taskIds": ["T-002"],
      "allowedPaths": ["consumer-core/src/**"],
      "testCommands": [
        {
          "cwd": "/absolute/path/to/consumer-service",
          "argv": ["tool", "test", "consumer-module"]
        }
      ]
    }
  ]
}
```

绝对路径按当前操作系统表示即可；不得硬编码 Windows 或 Unix 专属规则。授权文件中不保存口令、令牌、私钥或文件内容。用户只授权部分工作区时保持 `WAITING_FOR_WORKSPACE_AUTHORIZATION`，不得把未授权任务静默移出本轮。

## 覆盖结果模板

宿主根据冻结任务和授权清单创建 `rounds/round-NN/workspace-coverage.json`：

```json
{
  "schemaVersion": 1,
  "task": "task-id",
  "round": "round-01",
  "status": "PASS",
  "taskCoverage": [
    {
      "taskId": "T-001",
      "workspaceIds": ["provider-service"],
      "dependsOn": [],
      "status": "COVERED"
    },
    {
      "taskId": "T-002",
      "workspaceIds": ["consumer-service"],
      "dependsOn": ["T-001"],
      "status": "COVERED"
    }
  ],
  "missing": []
}
```

`PASS` 要求每个写入任务均为 `COVERED`、每个引用的工作区都已确认且可用、允许路径和测试目录合法、依赖关系可执行。任一项不满足时使用 `BLOCKED`，并在 `missing` 中提供结构化的 `taskId`、`workspaceId`、`reason` 和 `requiredAction`。

## 多工作区开发快照

覆盖门禁通过后，在任何写入前创建 `development-snapshot.json` schema v2。每个工作区独立记录 HEAD 和已有改动：

```json
{
  "schemaVersion": 2,
  "task": "task-id",
  "round": "round-01",
  "frozenFingerprint": "sha256",
  "workspaces": [
    {
      "id": "provider-service",
      "root": "/absolute/path/to/provider-service",
      "branch": "feature/example",
      "baseCommit": "40-or-64-character-git-object-id",
      "taskIds": ["T-001"],
      "allowedPaths": ["provider-api/src/**", "provider-core/src/**"],
      "preExistingChanges": []
    }
  ]
}
```

`preExistingChanges` 的条目沿用单工作区 schema v1：保存路径、状态码和可用的工作树哈希，不读取敏感文件内容。`gated-loop self-check` 原生识别 schema v2，并要求同轮 `workspace-authorization.json` 与 `workspace-coverage.json` 同时有效。授权文件中的全部测试 argv 必须与冻结基线测试命令精确分割匹配，每个 `cwd` 必须位于对应工作区；遗漏、重复、替换命令或目录越界都会关闭门禁。

## 依赖顺序与并行

多工作区不等于可以并行。默认按依赖波次执行：

- 如果消费方依赖本轮新建或修改的提供方契约，先完成并机械验证提供方，再启动消费方。
- 只有公共契约已经冻结且可由双方读取，或任务之间没有接口、生成物、数据库和语义依赖时，才允许同波并行。
- 一个开发 Agent 可以跨多个工作区执行 `single`，前提是覆盖门禁已通过且提示词列出全部授权。
- 多 Agent 开发时，每个 assignment 只获得自身所需工作区和路径；共享工作区仍需路径互斥。

依赖波次属于执行计划。新增工作区、改变任务归属或调整依赖顺序后必须重新展示计划并取得用户确认。

CLI 从 `workspace-coverage.json.taskCoverage[].dependsOn` 校验任务依赖图并计算工作区波次。依赖图存在环时不运行测试并返回 `NEED_HUMAN_REVIEW`；前置工作区的 Git、范围、归属或测试门禁失败时，后置工作区测试记录为 `BLOCKED`，不得继续执行。无依赖的同波工作区可以按确定性顺序检查，但只有全部工作区和冻结命令通过才产生聚合 `PASS`。

前后端或上下游存在安装、构建产物、代码生成、契约或接口依赖时，把相关本地构建与验证命令作为冻结 argv 分配给提供方工作区，把消费方任务的 `dependsOn` 指向提供方任务。CLI 先执行提供方波次，只有其 Git、范围和冻结命令全部通过才执行消费方波次。会下载依赖、访问网络、发布制品或改写工作区的安装命令不应伪装成只读门禁；它们需要在开发计划中单独获得用户授权，并在门禁中使用可重复、无交互的验证方式。

## 交接提示词

无论 active 还是 manual，交接卡片和 `prompt.md` 都必须逐个列出：

- 协调任务目录的绝对路径；
- 每个 `workspaceId`、绝对根路径、读写状态、任务 ID 和仓库相对允许路径；
- 每条测试命令的工作区、`cwd` 和 argv；
- 提供方/消费方依赖和执行波次；
- `workspace-authorization.json`、`workspace-coverage.json` 和 schema v2 快照路径。

接收 Agent 在第一次写入前只读验证全部根路径和授权文件。如果任一工作区不可访问、不是预期仓库、范围冲突或依赖前置条件未满足，必须在没有部分写入的情况下返回 `BLOCKED`。这项接收端预检是对环境差异的保护，不能替代宿主的交接覆盖门禁。

## 开发结果与机械门禁

多工作区结果使用带工作区标识的路径，避免同名文件歧义：

```json
{
  "status": "COMPLETED",
  "changedFiles": [
    { "workspaceId": "provider-service", "path": "provider-api/src/example.ext" },
    { "workspaceId": "consumer-service", "path": "consumer-core/src/example.ext" }
  ],
  "facts": ["可观察的实现事实"],
  "blockers": []
}
```

机械门禁先逐工作区、再整体聚合：

1. 逐工作区验证冻结指纹、根路径、HEAD、已有改动和真实 diff 归属。
2. 逐工作区拒绝越界、敏感、受保护、无关或无法归属的写入。
3. 在授权 `cwd` 中执行该工作区的冻结测试 argv，记录命令、退出码和测试数量。
4. 按依赖顺序执行跨服务契约、编译或集成检查。
5. 聚合为本轮 `gate-evidence.json` 和 `self-check-report.md`；任一工作区失败则整体失败。
6. 只把最终聚合 diff 和完整证据交给无开发上下文的语义验收者。

单工作区结果仍可使用字符串形式的 `changedFiles`，保持向后兼容。

## 变更与阻断处理

如果缺少的只是已冻结服务的实际绝对路径或写权限，用户可在新轮次补充授权，无需改写冻结需求。如果新增工作区会扩大目标、行为、任务或验收范围，则返回需求确认阶段，更新基线并重新冻结。

开发开始后发现遗漏工作区、错误仓库、契约不可用或依赖顺序错误时，立即停止后续写入并返回 `BLOCKED`；不要在当前轮次临时扩大白名单。已经发生部分写入时保留现场、记录每个工作区的归属并转 `NEED_HUMAN_REVIEW`，不得自动回滚或把改动归给错误的 Agent。
