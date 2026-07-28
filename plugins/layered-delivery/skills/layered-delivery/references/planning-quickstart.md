# 规划快速路径（schema v3）

只在 `workspace_status` 返回 `ABSENT` 或 `STAGING_ONLY`，并且用户要求开发新需求时使用。

## 选结构与门禁

- 单一低风险目标：根 `Task` + `LIGHT`。
- 需要多个可独立验证的 Task：`Capability → Task`。
- 需要多个 Capability：`Delivery → Capability → Task`。
- 安全、权限、迁移、兼容、事务、并发、外部契约、依赖或未知写路径使用 `FULL`；协调层固定 `FULL`。
- 文件或 Skill 数量本身不升级层级。不要创建空父级。

每个实际节点必须独立说明目标、范围、非目标、需求、验收、测试、风险和决策。每个 requirement 至少有一个只覆盖自身的可观察 acceptance；跨需求 acceptance 只能追加集成验收。

`scope` 按最小可用模块适当放宽，优先 `module/**`，不得使用全仓库 `**`。已知新增、修改、删除使用精确 `developmentPlan.fileChanges`；批量生成可用非重叠的 `generatedFileRoots`，但只授权新增文件。兄弟 Scope 重叠会限制并行。

测试命令使用 argv 数组。Task 是执行叶子；依赖只引用合法兄弟，父级 development plan 覆盖全部直接子级及其需求/验收映射。完整 definition 以 `prepare_hierarchy` 的当前工具 schema 为准，不复制固定模板。

多个仓库或服务仍只使用一个协调根；路径必须是该根下的安全相对路径，并明确测试 cwd、提供/消费依赖和共享契约。目标不可安全访问时先阻断。

## LIGHT 简写

普通单 Task 优先向 `prepare_hierarchy.hierarchy` 提交 `compactLightTask`：

```json
{
  "schemaVersion": 3,
  "compactLightTask": {
    "id": "t-example",
    "title": "Example task",
    "goal": "Deliver one observable result.",
    "scope": ["module/**", "tests/test_example.py"],
    "requirements": [
      {"id": "R-001", "text": "Describe required behavior."}
    ],
    "acceptance": [
      {
        "id": "A-001",
        "requirementIds": ["R-001"],
        "expectedResult": "Describe observable success."
      }
    ],
    "testCommands": [
      ["python", "-m", "unittest", "tests.test_example"]
    ],
    "fileChanges": [
      {
        "path": "tests/test_example.py",
        "action": "ADD",
        "purpose": "Verify the behavior."
      }
    ],
    "logic": ["Implement the frozen behavior."],
    "requiredSkills": []
  }
}
```

至少提供一个精确 `fileChanges` 或一个 `generatedFileRoots`。控制器扩展后只存完整 schema v3；这不是旧 schema 兼容入口。校验失败时根据结构化字段错误补齐，不从源码猜 schema。

## 用户指定开发 Skill

- 不预读或分析 Skill 内容，不递归其内部 Skill，不派生业务 requirement/Task，也不加入 `GATE`。
- 从宿主级 `root` 和项目级 `project` catalog 取得真实名称，以 `available_skills={"root":[...],"project":[...]}` 传给 `prepare_hierarchy`。
- 精确存在时登记 `requiredSkills=[{"name":"...","stages":["DEVELOPMENT"],"purpose":"..."}]`，到实际 worker 开发时才原生调用。
- `requiredSkills` 可省略或为空；空值不触发 Skill 门禁。
- 名称不存在或疑似拼错时停止准备。优先原样展示控制器返回的中文 `userPrompt`（标题、说明、带来源选项和安装/修正兜底）；`skillOptions` 只供机器处理，不静默改名。
- 只有用户明确指定其他阶段时，才增加 `GATE` 或根级 `FINAL_REVIEW`。

## 一次评审和冻结

1. 调用 `prepare_hierarchy`。
2. 提供根级 `developmentPlan` 入口，并简述完整树、目的、范围、精确文件授权、接口/共享契约、依赖波次和测试；同时展示 `active` / `manual` 两个选项。
   选择 `active` 前先确认宿主已具备冻结范围内的代码编辑与测试权限；Skill 不能自行切换宿主权限模式。
3. 用户要求调整时，以同一根 ID 重新准备完整树；旧 fingerprint 不再使用。
4. 用户同意当前方案并选择方式的同一回复就是冻结确认。紧邻该回复使用返回的 `hierarchyFingerprint` 调用一次 `freeze_hierarchy`，不再询问 Skill 或逐 Task 确认。
5. `manual` 冻结后向用户提供响应要求的一次性纯文本 handoff；接收会话从 `graph_frontier` 继续，不重新 prepare/freeze。
