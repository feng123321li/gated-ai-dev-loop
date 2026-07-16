# Hierarchical Delivery Governance

面向 AI Agent 的分层交付治理 Skill。它按最小必要深度把可独立交付的软件工作组织为：

```text
Task
Capability → Task
Delivery → Capability → Task
```

Delivery 和 Capability 管协调与聚合，Task 是唯一执行叶子。实际存在的每一级都有独立 baseline、门禁和进度；每个 Task 使用磁盘化独立上下文，不继承前期对话。

## 核心能力

- 小需求可直接使用根 Task，不虚构 Capability 或 Delivery；
- `gateLevel` 作为 schema v3 机器契约进入 baseline、registry、上下文和投影；仅 Task 可为 `LIGHT`，协调层固定 `FULL`；
- 多 Task 能力使用根 Capability，并可受控持续追加 Task；
- 多 Capability 交付才创建 Delivery；它可以是完整项目、大型模块、子系统或跨服务需求；
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
        ├── state.json
        ├── overview.md
        ├── progress.md
        ├── children.json | execution.json
        └── development-mode.json # Task 显式选择后生成
```

完整规则见 [Skill 入口](skills/hierarchical-delivery-governance/SKILL.md)、[工作流与流程图](skills/hierarchical-delivery-governance/references/workflow.md) 和 [可变深度规划](skills/hierarchical-delivery-governance/references/delivery-planning.md)。

## 安装 Skill

只安装 Skill 是主路径；安装目录内已经包含自给自足的 `scripts/hdg.mjs` 机械控制器，不要求全局 CLI。

```text
npm install
npm run skill:install -- --target both --scope user --dry-run
npm run skill:install -- --target both --scope user
```

安装后的宿主从 `SKILL.md` 所在目录执行：

```text
node <skill-root>/scripts/hdg.mjs --help
node <skill-root>/scripts/hdg.mjs prepare-item --definition task.json --host-runtime claude
node <skill-root>/scripts/hdg.mjs freeze-item --item t-example --expected-baseline <sha256> --confirmed
node <skill-root>/scripts/hdg.mjs promote-item --item t-example --parent c-example --expected-baseline <sha256> --expected-parent-baseline <sha256> --confirmed
node <skill-root>/scripts/hdg.mjs select-development-mode --item t-example --development-mode active --expected-baseline <sha256> --confirmed
node <skill-root>/scripts/hdg.mjs ready-tasks --item t-example
node <skill-root>/scripts/hdg.mjs task-context --item t-example
```

开发本仓库时，修改运行时代码后重新生成 Skill 控制器并验证：

```text
npm run skill:bundle
npm test
npm run test:coverage
```

内置控制器只打包层级 runtime，不再引入历史 `route/start/prepare/freeze` CLI 及其 YAML 配置链。`npm install -g .` 提供可选的 `hdg` 快捷别名，但不是 Skill 工作流的依赖。

Skill、npm 包和运行控制目录统一使用 `hierarchical-delivery-governance`，不追加 `v2`，也不读取或迁移旧控制目录。
