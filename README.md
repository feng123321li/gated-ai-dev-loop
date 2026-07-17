# Hierarchical Delivery Governance

面向 AI Agent 的分层交付治理 Skill。它按最小必要深度把可独立交付的软件工作组织为：

```text
Task
Capability → Task
Delivery → Capability → Task
```

Delivery 和 Capability 管协调与聚合，Task 是唯一执行叶子。实际存在的每一级都有独立 baseline、门禁和进度；每个 Task 使用磁盘化独立上下文，不继承前期对话。

冻结前会先生成真实可点击的开发评审文件：Task 展示精确文件、接口/函数和实现逻辑；Capability 展示 Task 内容、共享契约与波次；Delivery 展示 Capability 内容、跨能力契约与交付波次。人工评审当前指纹后才能冻结和开始开发。

## 核心能力

- 小需求可直接使用根 Task，不虚构 Capability 或 Delivery；
- `gateLevel` 作为 schema v3 机器契约进入 baseline、registry、上下文和投影；仅 Task 可为 `LIGHT`，协调层固定 `FULL`；
- 多 Task 能力使用根 Capability，并可受控持续追加 Task；
- 多 Capability 交付才创建 Delivery；它可以是完整项目、大型模块、子系统或跨服务需求；
- `prepare-item` 先写 `development-review.md/development-plan.json`，人工评审后再由 `freeze-item` 冻结；不存在跳过评审的一步 approve；
- 已冻结且尚未执行的根 Task/Capability 可在独立准备并冻结父 baseline 后，分别受控升层到 Capability/Delivery；
- Task baseline 冻结后必须由用户显式选择 active/manual；
- 按依赖、claim 和写入范围计算 READY Task，支持多人并行；
- Task、Capability、Delivery 各自通过 gate，失败后绑定当前 baseline 显式重试；
- Delivery gate 后仍需隔离/人工审查和用户确认；
- 维护本仓库默认进入 self-hosting maintenance，只有用户明确 dogfood 才创建运行包。

Micro、Workstream 和 M/W/T 可作为规模特征、规划视图和人类可读编号，但不进入机器 `kind`，也不拥有 baseline、claim 或 gate。

## 运行目录

```text
.hierarchical-delivery-governance/
├── work-item-registry.json
├── workspace-overview.md
└── work-items/
    └── <id>/
        ├── baseline.json
        ├── baseline.md
        ├── development-plan.json
        ├── development-review.md
        ├── state.json
        ├── overview.md
        ├── progress.md
        ├── children.json | execution.json
        └── development-mode.json # Task 显式选择后生成
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
python -X utf8 <skill-root>/scripts/hdg.py prepare-item --definition - --host-runtime claude-code --json
python -X utf8 <skill-root>/scripts/hdg.py freeze-item --item t-example --expected-baseline <sha256> --confirmed --json
python -X utf8 <skill-root>/scripts/hdg.py promote-item --item t-example --parent c-example --expected-baseline <sha256> --expected-parent-baseline <sha256> --confirmed
python -X utf8 <skill-root>/scripts/hdg.py select-development-mode --item t-example --development-mode active --expected-baseline <sha256> --confirmed
python -X utf8 <skill-root>/scripts/hdg.py ready-tasks --item t-example
python -X utf8 <skill-root>/scripts/hdg.py task-context --item t-example
```

`--definition -` 表示从 stdin 读取 JSON。单次 definition/evidence 输入优先使用 stdin；控制器只接受当前工作区内的文件路径，不要把这类输入写入系统 `TEMP` 或工作区之外。

开发本仓库时，修改运行时代码后重新生成 Skill 控制器并验证：

```text
python scripts/build_skill.py
python -m unittest discover -s tests -t . -v
python -m compileall -q src scripts tests
```

内置控制器直接打包 `src/hdg` Python 包。仓库与 Skill 使用同一份模块源码，运行时只依赖 Python 标准库；不提供历史 CLI、旧 schema 迁移或兼容别名。

Skill、Python 项目和运行控制目录统一使用 `hierarchical-delivery-governance`。所有持久化文件与 evidence 只接受当前完整 schema v3，不读取、迁移或解释其他版本。
