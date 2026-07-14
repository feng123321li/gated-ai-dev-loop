# 工作流程图

```mermaid
flowchart TD
    INPUT["任意形式的需求输入"] --> HOST["当前宿主：Codex 或 Claude<br/>采集、分析、审核"]
    HOST --> ROUTE{"选择任务模式"}
    ROUTE -->|None| NONE["只回答，不写文件"]
    NONE --> END["结束"]

    ROUTE -->|Light| LIGHT["四段式 Light 简报"]
    ROUTE -->|Full| FULL["Full 开发基线<br/>R / A / T 追踪"]
    LIGHT --> RUNTIME["统一写入 .ai-dev-loop/task-id/"]
    FULL --> RUNTIME
    RUNTIME --> TRACK["生成 development-overview.md<br/>初始化 progress.md"]
    TRACK --> CONFIRM{"用户确认开发授权？"}
    CONFIRM -->|否| HOST
    CONFIRM -->|是| FREEZE["冻结唯一开发授权与指纹"]

    FREEZE --> MODE_WAIT["WAITING_FOR_DEVELOPMENT_MODE_SELECTION"]
    MODE_WAIT --> DEV_MODE{"用户选择开发方式"}
    DEV_MODE -->|active| ACTIVE_HOST{"当前宿主"}
    ACTIVE_HOST -->|Codex| DEV_RUNTIME["developerRuntime = Codex"]
    ACTIVE_HOST -->|Claude| DEV_RUNTIME_C["developerRuntime = Claude"]
    DEV_MODE -->|manual| MANUAL_RUNTIME["用户选择 Codex 或 Claude 运行时"]
    DEV_RUNTIME --> TOPOLOGY{"执行拓扑"}
    DEV_RUNTIME_C --> TOPOLOGY
    MANUAL_RUNTIME --> TOPOLOGY
    TOPOLOGY -->|single| SINGLE_DEV["按 active/manual 启动一个全新开发上下文"]
    TOPOLOGY -->|parallel，仅合格 Full| PARALLEL_PLAN["确认任务分组、互斥路径、波次和并发数"]
    PARALLEL_PLAN --> PARALLEL_DEV["active 自动按波次派遣<br/>manual 输出多份交接"]
    PARALLEL_DEV --> INTEGRATE["逐 Agent 归属门禁<br/>机械集成无冲突结果"]

    SINGLE_DEV --> ACTIVE_RESULT{"开发调用结果"}
    INTEGRATE --> ACTIVE_RESULT
    ACTIVE_RESULT -->|成功| DEV_RESULT["COMPLETED / BLOCKED 事实"]
    ACTIVE_RESULT -->|失败单元零写入且其他归属明确| RESELECT["展示失败事实，重新选择或分配"]
    RESELECT --> DEV_MODE
    ACTIVE_RESULT -->|已有或无法判断写入| HUMAN["NEED_HUMAN_REVIEW"]

    DEV_RESULT --> GATES["机械门禁<br/>指纹 / diff / 范围 / 测试"]
    GATES --> RECLASS{"真实 diff 仍符合原模式？"}
    RECLASS -->|Light 越界| ESCALATE["升级 Full，重新审核与确认"]
    ESCALATE --> HOST
    RECLASS -->|符合| GATE_RESULT{"机械门禁通过？"}
    GATE_RESULT -->|可修复失败| REPAIR["最小修复交接，最多三轮"]
    REPAIR --> DEV_MODE
    GATE_RESULT -->|证据或归属不清| HUMAN
    GATE_RESULT -->|通过| CODEX{"独立 Codex 可用？"}

    CODEX -->|是| CODEX_REVIEW["全新只读 Codex 验收"]
    CODEX -->|否| CLAUDE_REVIEW["全新空上下文只读 Claude 验收"]
    CODEX_REVIEW --> REVIEW_RESULT{"PASS / FAIL / NEED_HUMAN_REVIEW"}
    CLAUDE_REVIEW --> REVIEW_RESULT
    REVIEW_RESULT -->|FAIL，未超过三轮| REPAIR
    REVIEW_RESULT -->|FAIL 已达上限或证据不足| HUMAN
    REVIEW_RESULT -->|PASS| FINAL_CONFIRM{"用户最终确认？"}
    FINAL_CONFIRM -->|等待反馈| WAIT["保持待确认，不自动发布"]
    FINAL_CONFIRM -->|确认| COMPLETE["完成"]

    classDef host fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef developer fill:#f3e8ff,stroke:#9333ea,color:#3b0764;
    classDef gate fill:#ffedd5,stroke:#ea580c,color:#431407;
    classDef review fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class HOST,ROUTE,LIGHT,FULL,RUNTIME,TRACK host;
    class MODE_WAIT,DEV_MODE,ACTIVE_HOST,DEV_RUNTIME,DEV_RUNTIME_C,MANUAL_RUNTIME,TOPOLOGY,SINGLE_DEV,PARALLEL_PLAN,PARALLEL_DEV,INTEGRATE,ACTIVE_RESULT,RESELECT,DEV_RESULT,REPAIR developer;
    class FREEZE,GATES,RECLASS,ESCALATE,GATE_RESULT gate;
    class CODEX,CODEX_REVIEW,CLAUDE_REVIEW,REVIEW_RESULT review;
    class CONFIRM,HUMAN,FINAL_CONFIRM,WAIT,COMPLETE human;
```

## 阅读重点

- 所有产物统一放在 `.ai-dev-loop/<task-id>/`，CLI 缺失也不改变目录。
- `development-overview.md` 提供稳定任务地图，`progress.md` 在每次状态转换后更新，人工验收从这两个入口查看。
- 需求确认与开发方式选择是两个门禁；冻结后必须由用户明确选择 active 或 manual。
- active 使用宿主同类开发运行时：Codex 启动 Codex，Claude 启动 Claude。
- manual 是正式路径，可把冻结包跨工具交给全新 Codex 或 Claude。
- single/parallel 是独立执行拓扑；只有任务和写入范围可证明互斥的 Full 才能选择 parallel。
- 用户确认 active + parallel 计划后自动派遣，不再逐 Agent 询问；计划变化必须重新确认。
- parallel 先逐 Agent 检查归属并集成，再对最终聚合 diff 运行完整门禁和独立验收。
- 主动调用失败且确认零写入时重新请求选择并推荐 manual；不得自动切换。
- 两种开发方式共用相同冻结授权、机械门禁和独立验收。
- `PASS` 仍需用户最终确认。
