# 工作流程图

```mermaid
flowchart TD
    INPUT["任意形式的需求输入"] --> HOST["任意宿主 Agent<br/>采集、分析、审核"]
    HOST --> RECOVER{".ai-dev-loop 中有<br/>应续接的非终态任务？"}
    RECOVER -->|一个| RESUME["读取冻结授权、progress、<br/>最终报告和最新轮次证据"]
    RECOVER -->|多个| TASK_SELECT["WAITING_FOR_TASK_SELECTION<br/>用户明确选择"]
    TASK_SELECT --> RESUME
    RESUME --> RECOVERED_ACTION["按磁盘 nextAction 续接<br/>不得重新 start 或替换 baseline"]
    RECOVER -->|没有或已确认新任务| ROUTE{"门禁等级"}
    ROUTE -->|None| NONE["只回答，不写文件"]
    NONE --> END["结束"]

    ROUTE -->|Light| MICRO_LIGHT["Light · Micro<br/>四段式简报"]
    ROUTE -->|Full| SCALE{"工作规模"}
    SCALE -->|micro| MICRO_FULL["Full · Micro<br/>高风险局部改动"]
    SCALE -->|task| TASK["Full · Task<br/>一个独立验收结果"]
    SCALE -->|capability| CAPABILITY["Full · Capability<br/>多任务协同的完整能力"]
    SCALE -->|project| PROJECT["Full · Project<br/>总纲、M / W / T 与 project-plan"]
    MICRO_LIGHT --> KIND["记录主要变更类型<br/>和固定/具体代表说明"]
    MICRO_FULL --> KIND
    TASK --> KIND
    CAPABILITY --> KIND
    PROJECT --> KIND
    KIND --> RUNTIME["统一写入 .ai-dev-loop/task-id/"]
    RUNTIME --> TRACK["生成 development-overview.md<br/>初始化业务任务与 SOP progress"]
    TRACK --> CONFIRM{"用户确认开发授权？"}
    CONFIRM -->|否| HOST
    CONFIRM -->|是| FREEZE["冻结唯一开发授权与指纹"]

    FREEZE --> WORKSPACE_CHECK{"所有写入任务的工作区<br/>路径、权限、测试与依赖已覆盖？"}
    WORKSPACE_CHECK -->|否| WORKSPACE_WAIT["WAITING_FOR_WORKSPACE_AUTHORIZATION<br/>列出缺失目录、任务与解除条件"]
    WORKSPACE_WAIT --> WORKSPACE_CHECK
    WORKSPACE_CHECK -->|是| WORKSPACE_PASS["生成 workspace-authorization.json<br/>与 workspace-coverage.json（跨工作区）"]
    WORKSPACE_PASS --> MODE_WAIT["WAITING_FOR_DEVELOPMENT_MODE_SELECTION"]
    MODE_WAIT --> DEV_MODE{"用户选择开发方式"}
    DEV_MODE -->|直接运行 active| ACTIVE_HOST["宿主自动派遣可用的<br/>全新隔离开发 Agent"]
    DEV_MODE -->|手动运行 manual| MANUAL_RUNTIME["返回任意 Agent 可接收的<br/>通用后续提示词"]
    ACTIVE_HOST --> TOPOLOGY{"执行拓扑"}
    MANUAL_RUNTIME --> TOPOLOGY
    TOPOLOGY -->|single| SINGLE_DEV["按 active/manual 启动一个全新开发上下文"]
    TOPOLOGY -->|parallel，仅合格 Full| PARALLEL_PLAN["确认任务分组、互斥路径、波次和并发数"]
    PARALLEL_PLAN --> PARALLEL_DEV["active 自动按波次派遣<br/>manual 输出多份交接"]
    PARALLEL_DEV --> INTEGRATE["逐 Agent 归属门禁<br/>机械集成无冲突结果"]

    SINGLE_DEV --> ACTIVE_RESULT{"开发调用结果"}
    INTEGRATE --> ACTIVE_RESULT
    ACTIVE_RESULT -->|成功| DEV_RESULT["COMPLETED / BLOCKED 事实<br/>任意宿主可从 gate-continuation 接管"]
    ACTIVE_RESULT -->|失败单元零写入且其他归属明确| RESELECT["展示失败事实，重新选择或分配"]
    RESELECT --> DEV_MODE
    ACTIVE_RESULT -->|已有或无法判断写入| HUMAN["NEED_HUMAN_REVIEW"]

    DEV_RESULT --> GATES["机械门禁并生成 self-check-report.md<br/>按依赖波次逐工作区检查；前置失败则后置 BLOCKED，再聚合"]
    GATES --> RECLASS{"真实 diff 仍符合原模式？"}
    RECLASS -->|Light 越界| ESCALATE["升级 Full，重新审核与确认"]
    ESCALATE --> HOST
    RECLASS -->|符合| GATE_RESULT{"机械门禁通过？"}
    GATE_RESULT -->|可修复失败| REPAIR["最小修复交接，最多三轮"]
    REPAIR --> DEV_MODE
    GATE_RESULT -->|证据或归属不清| HUMAN
    GATE_RESULT -->|通过| REVIEW_PLAN["生成 review-plan.json<br/>按可用能力选择验收路径"]

    REVIEW_PLAN --> REVIEWER{"有与开发者分离的其他 Agent？"}
    REVIEWER -->|是| OTHER_REVIEW["全新只读其他 Agent<br/>无开发上下文"]
    REVIEWER -->|否| SUBAGENT{"宿主能创建全新子 Agent？"}
    SUBAGENT -->|是| SUBAGENT_REVIEW["同产品也可以<br/>全新只读、无开发上下文"]
    SUBAGENT -->|否| HUMAN_REVIEW["生成完整人工语义验收包<br/>NEED_HUMAN_REVIEW<br/>不声称独立 PASS"]
    OTHER_REVIEW --> REVIEW_RESULT{"生成轮次 P0/P1/P2 报告<br/>刷新根级最终验收报告<br/>PASS / FAIL / NEED_HUMAN_REVIEW"}
    SUBAGENT_REVIEW --> REVIEW_RESULT
    HUMAN_REVIEW --> HUMAN
    REVIEW_RESULT -->|FAIL，未超过三轮| REPAIR
    REVIEW_RESULT -->|FAIL 已达上限或证据不足| HUMAN
    REVIEW_RESULT -->|PASS| FINAL_CONFIRM{"用户最终确认？"}
    FINAL_CONFIRM -->|等待反馈| WAIT["保持待确认，不自动发布"]
    FINAL_CONFIRM -->|提出修改、建议或新目标| FEEDBACK{"先恢复原任务并分类<br/>WAITING_FOR_FEEDBACK_CONFIRMATION"}
    FEEDBACK -->|冻结 R/A/T 已要求| REPAIR
    FEEDBACK -->|改变目标、范围或验收| REVISION_CONFIRM["确认 REVISION_OF 与新基线<br/>保留原任务包"]
    REVISION_CONFIRM -->|确认| HOST
    FEEDBACK -->|P2 或建议| SUGGESTION{"DEFER / DISMISS /<br/>IMPLEMENT_AS_REVISION / FOLLOW_UP"}
    SUGGESTION -->|延期或拒绝| FINAL_CONFIRM
    SUGGESTION -->|现在实现| REVISION_CONFIRM
    SUGGESTION -->|后续任务| NEW_TASK_CONFIRM["确认原任务处置、关系、task ID 与工作区"]
    FEEDBACK -->|独立新目标| NEW_TASK_CONFIRM
    NEW_TASK_CONFIRM -->|确认| HOST
    FINAL_CONFIRM -->|确认| COMPLETE["完成"]

    classDef host fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef developer fill:#f3e8ff,stroke:#9333ea,color:#3b0764;
    classDef gate fill:#ffedd5,stroke:#ea580c,color:#431407;
    classDef review fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class HOST,RECOVER,RESUME,TASK_SELECT,RECOVERED_ACTION,ROUTE,SCALE,MICRO_LIGHT,MICRO_FULL,TASK,CAPABILITY,PROJECT,KIND,RUNTIME,TRACK host;
    class MODE_WAIT,DEV_MODE,ACTIVE_HOST,MANUAL_RUNTIME,TOPOLOGY,SINGLE_DEV,PARALLEL_PLAN,PARALLEL_DEV,INTEGRATE,ACTIVE_RESULT,RESELECT,DEV_RESULT,REPAIR developer;
    class FREEZE,WORKSPACE_CHECK,WORKSPACE_WAIT,WORKSPACE_PASS,GATES,RECLASS,ESCALATE,GATE_RESULT gate;
    class REVIEW_PLAN,REVIEWER,SUBAGENT,OTHER_REVIEW,SUBAGENT_REVIEW,REVIEW_RESULT review;
    class CONFIRM,HUMAN_REVIEW,HUMAN,FINAL_CONFIRM,WAIT,FEEDBACK,REVISION_CONFIRM,SUGGESTION,NEW_TASK_CONFIRM,COMPLETE human;
```

## 阅读重点

- 所有产物统一放在 `.ai-dev-loop/<task-id>/`，CLI 缺失也不改变目录。
- 每次开发类消息先恢复已有非终态任务；一个任务直接续接，多个任务让用户选择，没有候选或用户明确批准新任务后才重新路由。
- 路由分别记录门禁等级、Micro/Task/Capability/Project 工作规模和主要变更类型；CLI 仍只使用 None/Light/Full，执行拓扑另行选择。
- 总览和进度必须显示规模的固定中文代表说明及当前任务的具体说明；Capability 展示工作流、依赖和集成门禁，Project 提供开发总纲、project-plan、M/W/T 拆解和 SOP 进度看板。
- AI 分析发现跨目录、跨仓库或跨微服务时，只在一个协调工作区保存任务包；必须先证明全部写入任务的工作区、路径、测试目录和依赖已覆盖，才允许生成交接提示词。
- `development-overview.md` 提供稳定任务地图，`progress.md` 在每次状态转换后更新；独立验收后人工优先查看根级 `final-acceptance-report.md`，再按需追溯这两个视图和轮次证据。
- 需求确认、跨工作区覆盖与开发方式选择是独立门禁；冻结后先补齐工作区，再由用户明确选择 active 或 manual。
- active 表示由宿主自动派遣全新隔离开发 Agent，不要求与宿主同类。
- manual 是正式路径，只返回任意 Agent 可接收的通用后续提示词，不预选工具或输出专属 CLI 命令。
- 两种方式都使用同一 `development-handoff.md` 和轮次提示词；开发 Agent 不需要前期对话。开发结束后任意新宿主可读取 `gate-continuation.md` 接管机械门禁，不绑定原对话。
- single/parallel 是独立执行拓扑；只有任务和写入范围可证明互斥的 Full 才能选择 parallel。
- 用户确认 active + parallel 计划后自动派遣，不再逐 Agent 询问；计划变化必须重新确认。
- parallel 先逐 Agent 检查归属并集成，再对最终聚合 diff 运行完整门禁和能力驱动的语义验收。
- 机械门禁生成 self-check-report.md；验收先生成 review-plan.json，再生成轮次级 acceptance-report.md 和 review.json，并刷新根级 final-acceptance-report.md；P0/P1 阻断、P2 非阻断但必须展示。
- 验收优先使用与开发者分离的其他 Agent；没有其他产品时允许启动同一宿主的全新验收子 Agent，两者都不能继承分析或开发上下文。
- 没有任何隔离 Agent 或子 Agent 时仍完成开发和机械门禁，并生成完整人工验收包；该路径不得声称独立语义验收 PASS。
- 主动调用失败且确认零写入时重新请求选择并推荐 manual；不得自动切换。
- 两种开发方式共用相同冻结授权、机械门禁和验收能力路由。
- `PASS` 仍需用户最终确认。
- 最终确认阶段的修改、建议或新目标必须先恢复原任务并分类；只有冻结 R/A/T 已要求的缺口才能直接进入同任务修复轮次，授权变化要保留原任务包并重新冻结，P2 和新任务都需要单独确认。
- 每个执行任务和 SOP 步骤开始、完成、阻断或跳过后必须立即回写 `progress.md` 及证据，再进入下一步；不能在整轮结束后批量补写。
