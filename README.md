# Hierarchical Delivery Governance

面向 AI Agent 的分层交付治理 Skill 与确定性 CLI。它把可独立交付的软件工作组织为 `Delivery → Capability → Task`：顶层 Delivery 可以是完整项目、大型模块、子系统或跨服务需求，不要求覆盖整个仓库或产品；Delivery 和 Capability 管协调，Task 是唯一可执行叶子，每一级都有独立 baseline、门禁和进度。

## 核心能力

- 为完整项目或可独立交付的大型模块生成 Delivery 总览，并拆分为多个 Capability；
- Capability 可通过受控 baseline 修订持续追加 Task；
- 每个 Delivery、Capability、Task 都冻结自己的 baseline；
- Task 只携带磁盘化独立上下文，不继承前期对话；
- 按依赖、claim 和写入范围计算 READY Task，支持多人并行开发；
- Task、Capability、Delivery 分级验收，父级必须通过自己的聚合门禁；
- 任一层级门禁失败后都必须绑定当前 baseline 显式重试；
- Delivery 门禁通过后仍要完成隔离/人工审查和用户确认，才算最终交付；
- 维护本仓库时默认进入 self-hosting maintenance，不创建 `.hierarchical-delivery-governance`；只有用户明确 dogfood，且每个会写控制面的层级命令都显式带 `--dogfood`，才允许演练运行包。

## 目录

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
        └── children.json | execution.json
```

完整规则见 [Skill 入口](skills/hierarchical-delivery-governance/SKILL.md) 和 [层级规划](skills/hierarchical-delivery-governance/references/delivery-planning.md)。

## 安装与验证

```text
npm install
npm test
npm run test:coverage
```

预览 Skill 安装：

```text
npm run skill:install -- --target both --scope user --dry-run
```

安装到当前用户的 Codex 与 Claude Code：

```text
npm run skill:install -- --target both --scope user
```

全局安装 CLI，或直接使用仓库入口：

```text
npm install -g .
hdg --help

node bin/hdg.mjs --help
```

## 层级 CLI

```text
hdg prepare-item --definition delivery.json --host-runtime codex
hdg freeze-item --item d-example --expected-baseline <sha256> --confirmed
hdg ready-tasks --delivery d-example
hdg task-context --item t-example
hdg retry-item --item c-example --expected-baseline <sha256> --confirmed
hdg delivery-item --item d-example --action INDEPENDENT_REVIEW_PASS --evidence review.json
hdg delivery-item --item d-example --action USER_CONFIRMED --evidence confirmation.json
```

查看完整命令：

```text
hdg --help
```

Skill、npm 包和运行控制目录统一使用 `hierarchical-delivery-governance`，CLI 使用 `hdg`，不追加 `v2`，也不读取或迁移旧控制目录。
