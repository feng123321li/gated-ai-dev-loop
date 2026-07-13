# 工作流程图

```mermaid
flowchart TD
    INPUT["任意形式的需求输入"] --> HOST["当前宿主：Codex 或 Claude<br/>采集、分析、审核"]
    HOST --> ROUTE{"选择任务模式"}

    ROUTE -->|None| NONE["只回答，不写文件"]
    NONE --> END["结束"]

    ROUTE -->|Light| LIGHT["四段式 Light 简报<br/>Goal / Scope / Acceptance / Risks"]
    ROUTE -->|Full| FULL["Full 开发基线<br/>R-NNN / A-NNN / T-NNN 追踪"]
    LIGHT --> CONFIRM{"用户确认开发授权？"}
    FULL --> CONFIRM
    CONFIRM -->|否| HOST
    CONFIRM -->|是| FREEZE["冻结唯一开发授权与指纹"]

    FREEZE --> DEV["新的 Claude 开发上下文<br/>只实现，不二次分析，不判断 PASS"]
    DEV --> GATES["机械门禁<br/>指纹 / diff / 范围 / 测试"]
    GATES --> RECLASS{"真实 diff 仍符合原模式？"}
    RECLASS -->|Light 越界| ESCALATE["升级为 Full<br/>重新审核与确认"]
    ESCALATE --> HOST
    RECLASS -->|符合| GATE_RESULT{"机械门禁通过？"}

    GATE_RESULT -->|可修复失败| REPAIR["生成最小修复交接"]
    REPAIR --> DEV
    GATE_RESULT -->|证据或归属不清| HUMAN["NEED_HUMAN_REVIEW"]
    GATE_RESULT -->|通过| CODEX{"独立 Codex 可用？"}

    CODEX -->|是| CODEX_REVIEW["新的只读 Codex 验收"]
    CODEX -->|否| CLAUDE_REVIEW["新的空上下文只读 Claude 验收"]
    CODEX_REVIEW --> REVIEW_RESULT{"独立验收结论"}
    CLAUDE_REVIEW --> REVIEW_RESULT

    REVIEW_RESULT -->|FAIL，未超过三轮| REVIEW_REPAIR["只交接阻断项、关联 ID、范围和测试"]
    REVIEW_REPAIR --> DEV
    REVIEW_RESULT -->|FAIL，已达三轮| HUMAN
    REVIEW_RESULT -->|NEED_HUMAN_REVIEW| HUMAN
    REVIEW_RESULT -->|PASS| FINAL_CONFIRM{"用户最终确认？"}
    FINAL_CONFIRM -->|等待反馈| WAIT["保持待确认，不自动发布"]
    FINAL_CONFIRM -->|确认| COMPLETE["完成"]

    classDef host fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef claude fill:#f3e8ff,stroke:#9333ea,color:#3b0764;
    classDef gate fill:#ffedd5,stroke:#ea580c,color:#431407;
    classDef review fill:#dcfce7,stroke:#16a34a,color:#052e16;
    classDef human fill:#f3f4f6,stroke:#4b5563,color:#111827;

    class HOST,ROUTE,LIGHT,FULL host;
    class DEV,REPAIR,REVIEW_REPAIR claude;
    class FREEZE,GATES,RECLASS,ESCALATE,GATE_RESULT gate;
    class CODEX,CODEX_REVIEW,CLAUDE_REVIEW,REVIEW_RESULT review;
    class CONFIRM,HUMAN,FINAL_CONFIRM,WAIT,COMPLETE human;
```

## 阅读重点

- 前期需求来源不限，但写代码前必须归一化并由用户确认。
- 当前 Codex 或 Claude 均可审核并冻结；开发前不强制第二个模型复审。
- Claude 开发上下文只执行冻结任务，不做二次需求分析，也不验收自己。
- 机械门禁先于语义验收；真实 diff 越界时必须重新路由。
- 独立验收优先使用新的只读 Codex；不可用时使用新的空上下文只读 Claude。
- `PASS` 只代表独立审查通过，最终完成仍由用户确认。
