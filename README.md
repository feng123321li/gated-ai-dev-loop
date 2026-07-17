# Hierarchical Delivery Governance

面向 AI Agent 的分层交付治理 Skill。它按最小必要深度把可独立交付的软件工作组织为：

```text
Task
Capability → Task
Delivery → Capability → Task
```

Delivery 和 Capability 管协调与聚合，Task 是唯一执行叶子。实际存在的每一级都有独立 baseline、门禁和进度；每个 Task 使用磁盘化独立上下文，不继承前期对话。

每个用户需求只生成一个嵌套根目录。需求根的 `development-plan.md` 一次展示完整的 Task、Capability→Task 或 Delivery→Capability→Task 树，是统一人工冻结评审入口；每个实际 Capability/Task 节点也在自己的嵌套目录保留独立 `development-plan.md` 和 `progress.md`。人工评审根级整树方案并选择开发方式后，只确认一次；Agent 负责携带层级指纹和所选方式完成整树冻结。

## 核心能力

- 小需求可直接使用根 Task，不虚构 Capability 或 Delivery；
- `gateLevel` 作为 schema v3 机器契约进入 baseline、registry、上下文和投影；仅 Task 可为 `LIGHT`，协调层固定 `FULL`；
- 多 Task 能力使用根 Capability，所有当前计划 Task 在冻结前一次物化；
- 多 Capability 交付才创建 Delivery；它可以是完整项目、大型模块、子系统或跨服务需求；
- `prepare-hierarchy` 一次写入完整树和 `development-plan.json/md`，人工评审并选择开发方式后由 `freeze-hierarchy` 一次冻结全部节点；
- 人工确认不要求抄写 SHA256；层级指纹由 Agent 从准备结果传给控制器，过期方案会机械拒绝；
- 同一次冻结确认在需求根记录 active/manual；不再要求冻结后二次批准；
- active 由 Agent 自主选择多子 Agent、单 Agent 或当前 Agent 串行，并循环实现、回归、修复和复测；
- manual 在需求根生成一份 `requirement-handoff.md`，人工只需一次复制到新会话，接收 Agent 自行推进整树，不逐 Task 要求启动；
- 按依赖、claim 和写入范围计算 READY Task，支持多人并行；
- Task、Capability、Delivery 各自通过 gate；同一冻结契约内失败后由 Agent 按当前 baseline 自动重试；
- 开发结果写回后生成 `development-review.json/md` 对照计划与实际；门禁后再生成并更新 `acceptance-report.json/md`；
- 需求根 `progress.md` 以 Markdown 表格展示整树明细，第一列保留与 `development-plan.md` 相同的节点 ID、父子顺序和 Delivery→Capability→Task 层级，每次状态写回自动刷新；
- 治理根 gate 后仍需隔离/人工审查和用户确认；
- 维护本仓库默认进入 self-hosting maintenance，只有用户明确 dogfood 才创建运行包。

Micro、Workstream 和 M/W/T 可作为规模特征、规划视图和人类可读编号，但不进入机器 `kind`，也不拥有 baseline、claim 或 gate。

## 运行目录

```text
.hierarchical-delivery-governance/
├── work-item-registry.json
├── workspace-overview.md
└── work-items/
    └── <requirement-root-id>/       # 一个需求只有一个顶层目录
        ├── hierarchy.json
        ├── baseline.json
        ├── baseline.md
        ├── development-plan.json
        ├── development-plan.md      # 整棵树唯一人工评审入口
        ├── state.json
        ├── overview.md
        ├── progress.md              # 与开发方案对应的整树进度明细
        ├── children.json | execution.json
        ├── development-review.json/md # 开发结果写回后生成
        ├── development-mode.json      # 同一次冻结确认记录根级开发方式
        ├── requirement-handoff.md     # manual 模式的整树一次性交接
        └── children/
            └── <child-id>/            # 按 Delivery→Capability→Task 递归嵌套
                ├── development-plan.json/md # 子节点独立开发方案
                ├── progress.md             # 子节点独立进度
                └── children/               # 按实际层级继续嵌套
```

完整规则见 [Skill 入口](skills/hierarchical-delivery-governance/SKILL.md)、[工作流与流程图](skills/hierarchical-delivery-governance/references/workflow.md) 和 [可变深度规划](skills/hierarchical-delivery-governance/references/delivery-planning.md)。

## 安装 Skill

只安装 Skill 是主路径；安装目录内已经包含模块化、纯标准库的 Python 控制器，不要求 Node、npm、pip 包或全局 CLI。运行环境需要 Python 3.10+。

```text
python scripts/install_skill.py --target both --scope user --dry-run
python scripts/install_skill.py --target both --scope user
```

安装后的宿主从 `SKILL.md` 所在目录执行：

```text
python -X utf8 <skill-root>/scripts/hdg.py --help
python -X utf8 <skill-root>/scripts/hdg.py prepare-hierarchy --definition - --host-runtime claude-code --json
python -X utf8 <skill-root>/scripts/hdg.py freeze-hierarchy --item c-example --expected-hierarchy <sha256> --development-mode active --confirmed --json
python -X utf8 <skill-root>/scripts/hdg.py ready-tasks --item c-example
python -X utf8 <skill-root>/scripts/hdg.py task-context --item t-example
```

`--definition -` 表示从 stdin 读取 JSON。层级 definition 固定为 `{"schemaVersion":3,"root":{"definition":{...},"children":[...]}}`，每个子节点继续使用同样的 `definition + children` 结构；协调节点声明的每个 child 都必须在本次树中完整物化。单次 definition/evidence 输入优先使用 stdin；控制器只接受当前工作区内的文件路径，不要把这类输入写入系统 `TEMP` 或工作区之外。

开发本仓库时，修改运行时代码后重新生成 Skill 控制器并验证：

```text
python scripts/build_skill.py
python -m unittest discover -s tests -t . -v
python -m compileall -q src scripts tests
```

内置控制器直接打包 `src/hdg` Python 包。仓库与 Skill 使用同一份模块源码，运行时只依赖 Python 标准库。

Skill、Python 项目和运行控制目录统一使用 `hierarchical-delivery-governance`。所有持久化文件与 evidence 只接受当前完整 schema v3，不读取、迁移或解释其他版本。
