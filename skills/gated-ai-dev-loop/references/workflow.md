# 工作流程图

本页先给出端到端总览，再按阶段拆成四张子图。跨图连接使用“来自图 N”或“进入图 N”表示，不新增业务状态。

## 目录

- [总览](#总览)
- [图 1：任务恢复、路由与授权冻结](#图-1任务恢复路由与授权冻结)
- [图 2：工作区覆盖与隔离开发](#图-2工作区覆盖与隔离开发)
- [图 3：机械门禁与语义验收](#图-3机械门禁与语义验收)
- [图 4：最终确认与反馈分流](#图-4最终确认与反馈分流)
- [阅读重点](#阅读重点)

## 总览

```mermaid
flowchart LR
    INPUT["需求输入"] --> RECOVER["① 恢复任务<br/>路由与冻结"]
    RECOVER --> DEV["② 工作区覆盖<br/>与隔离开发"]
    DEV --> GATE["③ 机械门禁<br/>与语义验收"]
    GATE --> CONFIRM["④ 最终确认<br/>与反馈分流"]
    CONFIRM --> COMPLETE["完成"]

    RECOVER -->|None| ANSWER["只回答，不写文件"]
    GATE -->|修复| DEV
    GATE -->|Light 越界| RECOVER
    GATE -->|证据不清| HUMAN["NEED_HUMAN_REVIEW"]
    CONFIRM -->|同任务修复| DEV
    CONFIRM -->|修订或新任务| RECOVER
    CONFIRM -->|等待| WAIT["等待人工反馈"]

    classDef host fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef developer fill:#f3e8ff,stroke:#9333ea,color:#3b0764;
    classDef gate fill:#ffedd5,stroke:#ea580c,color:#431407;
    classDef review fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class INPUT,RECOVER host;
    class DEV developer;
    class GATE gate;
    class CONFIRM review;
    class ANSWER,COMPLETE,HUMAN,WAIT human;
```

任务恢复后的 `nextAction` 来自根级 `task-registry.json`，可以直接进入任一对应阶段，不要求从图 1 重新开始。

## 图 1：任务恢复、路由与授权冻结

```mermaid
flowchart TD
    INPUT["任意形式的需求输入"] --> HOST["任意宿主 Agent<br/>采集、分析、审核"]
    HOST --> REGISTRY["只读校验 task-registry.json 与单层目录<br/>写入前再强制 Git ignore 门禁"]
    REGISTRY --> READ_ONLY{"只读 None 且<br/>不修改任务状态？"}
    READ_ONLY -->|是| NONE["只回答，不写文件<br/>不改变 currentFocus"]
    NONE --> END["结束"]
    READ_ONLY -->|否| RECOVER{"精确 ID / 当前焦点 /<br/>唯一合格候选？"}

    RECOVER -->|选中| RESUME["读取注册表 phase / nextAction、<br/>冻结授权和最新轮次证据"]
    RECOVER -->|多个| TASK_SELECT["WAITING_FOR_TASK_SELECTION<br/>用户明确选择"]
    TASK_SELECT --> RESUME
    RECOVER -->|UNKNOWN 或不一致| CLASSIFY["NEED_RESUME_CLASSIFICATION<br/>不得按时间或名称猜测"]
    CLASSIFY -->|用户完成分类并落盘 event| REGISTRY
    RESUME --> RECOVERED_ACTION["读取注册表 nextAction<br/>不得重新 start 或替换 baseline"]
    RECOVERED_ACTION --> NEXT_ACTION{"nextAction 指向哪个阶段？"}
    NEXT_ACTION -->|分析、路由或待确认| STAGE1["在原任务内执行图 1 对应动作<br/>完成后刷新 nextAction"]
    NEXT_ACTION -->|工作区、开发或修复| STAGE2["进入图 2 对应节点"]
    NEXT_ACTION -->|机械门禁或语义验收| STAGE3["进入图 3 对应节点"]
    NEXT_ACTION -->|最终确认或反馈| STAGE4["进入图 4 对应节点"]

    RECOVER -->|无合格候选且已确认新任务| ROUTE{"写入门禁等级"}
    ROUTE -->|Light| MICRO_LIGHT["Light · Micro<br/>四段式简报"]
    ROUTE -->|Full| SCALE_FACTS["抽取规模事实<br/>能力与验收、里程碑与阶段映射"]
    SCALE_FACTS --> SCALE{"工作规模"}
    SCALE -->|Micro| MICRO_FULL["Full · Micro<br/>高风险局部改动"]
    SCALE -->|Task| TASK["Full · Task<br/>一个独立验收结果"]
    SCALE -->|Capability| CAPABILITY["Full · Capability<br/>一个完整能力 + 一个聚合验收<br/>W / T / S"]
    SCALE -->|Project| PROJECT["Full · Project<br/>完整大模块或多能力 / 多里程碑<br/>总纲、M / W / T / S 与 project-plan"]
    SCALE -->|事实未知| SCALE_WAIT["暂定 Full · Project<br/>WAITING_FOR_REQUIREMENT_CONFIRMATION<br/>补齐规模事实后再确认"]
    SCALE_WAIT --> HOST

    MICRO_LIGHT --> KIND["记录主要变更类型<br/>和固定/具体代表说明"]
    MICRO_FULL --> KIND
    TASK --> KIND
    CAPABILITY --> KIND
    PROJECT --> KIND
    KIND --> RUNTIME["staging 写创建 event<br/>登记 PROVISIONAL task registry"]
    RUNTIME --> AUTHORIZE["起草 Full baseline 或 Light brief<br/>Full 可 prepare，尚不 confirmed"]
    AUTHORIZE --> TRACK["刷新 workspace-overview.md<br/>再在 staging 生成总览、计划与 progress"]
    TRACK --> CONFIRM{"用户确认开发授权？"}
    CONFIRM -->|否| HOST
    CONFIRM -->|是| FREEZE["冻结唯一开发授权与指纹<br/>物化 staging、改为 HEALTHY，进入图 2"]

    classDef host fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef gate fill:#ffedd5,stroke:#ea580c,color:#431407;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class INPUT,HOST,REGISTRY,READ_ONLY,RECOVER,RESUME,TASK_SELECT,CLASSIFY,RECOVERED_ACTION,NEXT_ACTION,STAGE1,STAGE2,STAGE3,STAGE4,ROUTE,SCALE_FACTS,SCALE,MICRO_LIGHT,MICRO_FULL,TASK,CAPABILITY,PROJECT,KIND,RUNTIME,AUTHORIZE,TRACK host;
    class FREEZE gate;
    class NONE,END,SCALE_WAIT,CONFIRM human;
```

## 图 2：工作区覆盖与隔离开发

```mermaid
flowchart TD
    START["来自图 1<br/>已冻结唯一开发授权"] --> WORKSPACE_CHECK{"所有写入任务的工作区<br/>路径、权限、测试与依赖已覆盖？"}
    WORKSPACE_CHECK -->|否| WORKSPACE_WAIT["WAITING_FOR_WORKSPACE_AUTHORIZATION<br/>列出缺失目录、任务与解除条件"]
    WORKSPACE_WAIT --> WORKSPACE_CHECK
    WORKSPACE_CHECK -->|是| WORKSPACE_PASS["生成 workspace-authorization.json<br/>与 workspace-coverage.json（跨工作区）"]

    WORKSPACE_PASS --> MODE_WAIT["WAITING_FOR_DEVELOPMENT_MODE_SELECTION"]
    MODE_WAIT --> DEV_MODE{"用户选择开发方式"}
    REPAIR_IN["来自图 3 / 图 4<br/>最小修复交接"] --> DEV_MODE

    DEV_MODE -->|active| ACTIVE_SELECTED["记录 active"]
    DEV_MODE -->|manual| MANUAL_SELECTED["记录 manual"]
    ACTIVE_SELECTED --> TOPOLOGY{"执行拓扑"}
    MANUAL_SELECTED --> TOPOLOGY
    TOPOLOGY -->|single| SINGLE_PLAN["确认 single"]
    TOPOLOGY -->|parallel，仅合格 Full| PARALLEL_PLAN["确认任务分组、互斥路径、<br/>波次和并发数"]
    SINGLE_PLAN --> DISPATCH{"按已选开发方式交付"}
    PARALLEL_PLAN --> DISPATCH
    DISPATCH -->|active| ACTIVE_HOST["宿主按已确认拓扑派遣<br/>全新隔离开发 Agent"]
    DISPATCH -->|manual| MANUAL_RUNTIME["按已确认拓扑返回<br/>一份或多份通用提示词"]
    ACTIVE_HOST --> EXECUTION{"按已确认拓扑执行"}
    MANUAL_RUNTIME --> EXECUTION
    EXECUTION -->|single| SINGLE_DEV["启动一个全新开发上下文"]
    EXECUTION -->|parallel| PARALLEL_DEV["按确认波次执行或交接"]
    PARALLEL_DEV --> INTEGRATE["逐 Agent 归属门禁<br/>机械集成无冲突结果"]

    SINGLE_DEV --> ACTIVE_RESULT{"开发调用结果"}
    INTEGRATE --> ACTIVE_RESULT
    ACTIVE_RESULT -->|成功| DEV_RESULT{"开发 Agent 返回的事实"}
    ACTIVE_RESULT -->|失败单元零写入<br/>且其他归属明确| RESELECT["展示失败事实<br/>重新选择或分配"]
    RESELECT --> DEV_MODE
    ACTIVE_RESULT -->|已有或无法判断写入| HUMAN["NEED_HUMAN_REVIEW"]

    DEV_RESULT -->|COMPLETED| WRITEBACK["实现调用结束，不是任务终态<br/>event → registry(PENDING) → overview(PENDING)<br/>task projections → ack → overview(CURRENT)"]
    DEV_RESULT -->|BLOCKED| BLOCKED["回写阻断证据与解除条件<br/>event → registry(PENDING) → overview(PENDING)<br/>task projections → ack → overview(CURRENT)"]
    BLOCKED --> RESUME_BLOCKED["解除后回图 1 恢复任务<br/>按 registry nextAction 续接"]
    SINGLE_DEV -.-> PROGRESS_NOTE["每次 T / SOP 状态转换<br/>event → registry(PENDING) → overview(PENDING)<br/>task projections → ack → overview(CURRENT)"]
    PARALLEL_DEV -.-> PROGRESS_NOTE
    INTEGRATE -.-> PROGRESS_NOTE
    WRITEBACK --> NEXT["进入图 3<br/>机械门禁与语义验收"]

    classDef developer fill:#f3e8ff,stroke:#9333ea,color:#3b0764;
    classDef gate fill:#ffedd5,stroke:#ea580c,color:#431407;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class DEV_MODE,REPAIR_IN,ACTIVE_SELECTED,MANUAL_SELECTED,TOPOLOGY,SINGLE_PLAN,PARALLEL_PLAN,DISPATCH,ACTIVE_HOST,MANUAL_RUNTIME,EXECUTION,SINGLE_DEV,PARALLEL_DEV,INTEGRATE,ACTIVE_RESULT,RESELECT,DEV_RESULT developer;
    class START,WORKSPACE_CHECK,WORKSPACE_WAIT,WORKSPACE_PASS,MODE_WAIT,PROGRESS_NOTE,WRITEBACK,NEXT gate;
    class HUMAN,BLOCKED,RESUME_BLOCKED human;
```

## 图 3：机械门禁与语义验收

```mermaid
flowchart TD
    START["来自图 2<br/>开发结果与最新 registry revision"] --> GATES["机械门禁并生成 self-check-report.md<br/>按依赖波次逐工作区检查并聚合"]
    GATES --> RECLASS{"真实 diff 仍符合原模式？"}

    RECLASS -->|Light 越界| ESCALATE["升级 Full"]
    ESCALATE --> HOST["回图 1<br/>重新审核、确认与冻结"]
    RECLASS -->|Full 范围、规模<br/>或授权越界| REBASELINE["暂停当前门禁<br/>回图 1 修订并重新冻结"]
    RECLASS -->|符合| GATE_RESULT{"机械门禁通过？"}

    GATE_RESULT -->|可修复失败| REPAIR["最小修复交接，最多三轮<br/>回图 2"]
    GATE_RESULT -->|证据或归属不清| HUMAN_BLOCK["NEED_HUMAN_REVIEW<br/>等待人工补证或处置"]
    GATE_RESULT -->|通过| REVIEW_PLAN["生成 review-plan.json<br/>按可用能力选择验收路径"]

    REVIEW_PLAN --> REVIEWER{"有与开发者分离的其他 Agent？"}
    REVIEWER -->|是| OTHER_REVIEW["全新只读其他 Agent<br/>无开发上下文"]
    REVIEWER -->|否| SUBAGENT{"宿主能创建全新子 Agent？"}
    SUBAGENT -->|是| SUBAGENT_REVIEW["同产品也可以<br/>全新只读、无开发上下文"]
    SUBAGENT -->|否| HUMAN_REVIEW["生成完整人工语义验收包<br/>不声称独立 PASS"]

    OTHER_REVIEW --> REVIEW_RESULT{"生成轮次 P0 / P1 / P2 报告<br/>刷新任务根级最终验收报告"}
    SUBAGENT_REVIEW --> REVIEW_RESULT
    HUMAN_REVIEW --> HUMAN_DECISION{"人工语义审查结论？"}
    HUMAN_DECISION -->|PASS| FINAL
    HUMAN_DECISION -->|FAIL，未超过三轮| REPAIR
    HUMAN_DECISION -->|FAIL 已达上限| HUMAN_BLOCK
    REVIEW_RESULT -->|FAIL，未超过三轮| REPAIR
    REVIEW_RESULT -->|FAIL 已达上限<br/>或证据不足| HUMAN_BLOCK
    REVIEW_RESULT -->|PASS| FINAL["registry=WAITING_USER<br/>进入图 4 等待最终确认"]
    HUMAN_BLOCK -->|补齐机械证据后| GATES

    classDef host fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef developer fill:#f3e8ff,stroke:#9333ea,color:#3b0764;
    classDef gate fill:#ffedd5,stroke:#ea580c,color:#431407;
    classDef review fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class HOST host;
    class REPAIR developer;
    class START,GATES,RECLASS,ESCALATE,REBASELINE,GATE_RESULT gate;
    class REVIEW_PLAN,REVIEWER,OTHER_REVIEW,SUBAGENT,SUBAGENT_REVIEW,REVIEW_RESULT,HUMAN_DECISION,FINAL review;
    class HUMAN_REVIEW,HUMAN_BLOCK human;
```

## 图 4：最终确认与反馈分流

```mermaid
flowchart TD
    START["来自图 3<br/>独立 PASS 或人工语义审查 PASS"] --> FINAL_CONFIRM{"用户最终确认？"}
    FINAL_CONFIRM -->|确认完成| COMPLETE["registry=TERMINAL / COMPLETED<br/>刷新当前焦点与工作区总纲"]
    FINAL_CONFIRM -->|等待反馈| WAIT["保持待确认<br/>不自动发布"]
    FINAL_CONFIRM -->|修改、建议或新目标| FEEDBACK{"先恢复原任务并分类<br/>WAITING_FOR_FEEDBACK_CONFIRMATION"}

    FEEDBACK -->|冻结 R / A / T 已要求| REPAIR_CURRENT["REPAIR_CURRENT<br/>终态任务先确认 REOPEN_CURRENT，再回图 2"]
    FEEDBACK -->|改变目标、范围或验收| REVISION_CONFIRM["确认 REVISION_OF 与新基线<br/>保留原任务包"]
    REVISION_CONFIRM -->|确认| HOST["回图 1 建立关联修订包<br/>冻结后旧任务 SUPERSEDED"]
    REVISION_CONFIRM -->|不确认| FINAL_CONFIRM

    FEEDBACK -->|P2 或建议| SUGGESTION{"DEFER / DISMISS /<br/>IMPLEMENT_AS_REVISION / FOLLOW_UP"}
    SUGGESTION -->|DEFER / DISMISS| FINAL_CONFIRM
    SUGGESTION -->|IMPLEMENT_AS_REVISION| REVISION_CONFIRM
    SUGGESTION -->|FOLLOW_UP| NEW_TASK_CONFIRM["确认原任务处置、关系、<br/>task ID 与工作区"]

    FEEDBACK -->|独立新目标| NEW_TASK_CONFIRM
    NEW_TASK_CONFIRM -->|确认| HOST
    NEW_TASK_CONFIRM -->|不确认| FINAL_CONFIRM
    WAIT -->|确认完成| COMPLETE
    WAIT -->|收到修改、建议或新目标| RECOVER["回图 1 恢复原任务<br/>按 nextAction 返回本图"]
    RECOVER --> FEEDBACK

    classDef host fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef developer fill:#f3e8ff,stroke:#9333ea,color:#3b0764;
    classDef review fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class HOST,RECOVER host;
    class REPAIR_CURRENT developer;
    class START,FEEDBACK,SUGGESTION review;
    class FINAL_CONFIRM,COMPLETE,WAIT,REVISION_CONFIRM,NEW_TASK_CONFIRM human;
```

## 阅读重点

### 入口、恢复与规划

- 根级 `task-registry.json` 保存完整索引、生命周期、当前焦点、周期与完成计数，`workspace-overview.md` 是人可读总纲；冻结包和轮次证据仍放在 `.ai-dev-loop/<task-id>/`。
- 每次开发类消息按精确 ID / 路径、有效当前焦点、唯一 `ACTIVE/WAITING_USER` 候选恢复；多个候选让用户选择，`BLOCKED/DEFERRED/TERMINAL/UNKNOWN` 不参与自动唯一选择。
- 恢复任务后按注册表 `phase` 和 `nextAction` 进入对应阶段，不得按目录时间或名称选择，也不得重新 `start` 或替换冻结 baseline。
- 路由分别记录门禁等级、Micro / Task / Capability / Project 工作规模和主要变更类型；CLI 仍只使用 None / Light / Full，执行拓扑另行选择。
- Full 工作规模判定前先形成可读的规模事实记录，至少列出完整交付边界、独立能力及其聚合验收、用户可验收里程碑或阶段，以及工作流和依赖到这些边界的映射。
- 接口、文件或服务数量，以及公共契约、状态机、幂等、多工作区等 Full 风险信号，都不能单独推出 Project；它们只决定门禁强度或触发规模复核。
- 一个完整能力和一个聚合验收使用 Capability，并按 W/T/S 跟踪工作流、任务与 SOP；完整大模块或多个能力、多个用户可验收里程碑使用 Project，并提供开发总纲、project-plan 和 M/W/T/S 看板。
- 规模事实未知时保守暂定 Project，保持 `WAITING_FOR_REQUIREMENT_CONFIRMATION` 并展示缺失事实；补齐并确认前不得冻结。
- 总览和进度必须显示规模的固定中文代表说明、当前任务的具体说明和上述判定事实。

### 工作区与开发执行

- AI 分析发现跨目录、跨仓库或跨微服务时，只在一个协调工作区保存任务包；必须先证明全部写入任务的工作区、路径、测试目录和依赖已覆盖，才允许生成交接提示词。
- 需求确认、跨工作区覆盖与开发方式选择是独立门禁；冻结后先补齐工作区，再由用户明确选择 active 或 manual。
- active 表示由宿主自动派遣全新隔离开发 Agent，不要求与宿主同类。
- manual 是正式路径，只返回任意 Agent 可接收的通用后续提示词，不预选工具或输出专属 CLI 命令。
- 两种方式都使用同一 `development-handoff.md` 和轮次提示词；开发结束后任意新宿主可读取 `gate-continuation.md` 接管机械门禁，不绑定原对话。
- single / parallel 是独立执行拓扑；只有任务和写入范围可证明互斥的 Full 才能选择 parallel。
- 用户确认 active + parallel 计划后自动派遣，不再逐 Agent 询问；计划变化必须重新确认。
- parallel 先逐 Agent 检查归属并集成，再对最终聚合 diff 运行完整门禁和能力驱动的语义验收。
- 每个执行任务和 SOP 步骤开始、完成、阻断或跳过后必须立即按“不可变 event → registry(PENDING) → workspace-overview(PENDING) → task projections → projection ack → workspace-overview(CURRENT)”回写，再进入下一步；不能在整轮结束后批量补写。

### 机械门禁与语义验收

- `workspace-overview.md` 提供工作区级任务地图，任务内 `development-overview.md` 和 `progress.md` 展示单任务详情；人工验收再优先查看任务根级 `final-acceptance-report.md`。
- 机械门禁生成 `self-check-report.md`；验收先生成 `review-plan.json`，再生成轮次级 `acceptance-report.md` 和 `review.json`，并刷新任务根级 `final-acceptance-report.md`；P0 / P1 阻断、P2 非阻断但必须展示。
- 验收优先使用与开发者分离的其他 Agent；没有其他产品时允许启动同一宿主的全新验收子 Agent，两者都不能继承分析或开发上下文。
- 没有任何隔离 Agent 或子 Agent 时仍完成开发和机械门禁，并生成完整人工验收包；该路径不得声称独立语义验收 PASS。
- 主动调用失败且确认零写入时重新请求选择并推荐 manual；不得自动切换。
- active 与 manual 共用相同冻结授权、机械门禁和验收能力路由。

### 最终确认与后续反馈

- `PASS` 只把生命周期置为等待用户；最终确认后才能写 `TERMINAL / COMPLETED`，且不得自动发布。
- 最终确认阶段的修改、建议或新目标必须先恢复原任务并分类。
- 只有冻结 R / A / T 已要求的缺口才能直接进入同任务修复轮次；机械门禁失败、验收 FAIL 未超三轮和同任务缺口修复都回到图 2。
- 授权变化必须保留原任务包并重新冻结；P2 和新任务都需要单独确认。
- 流程保留四类可见结果：只回答后结束、完成、等待人工反馈、`NEED_HUMAN_REVIEW`。
