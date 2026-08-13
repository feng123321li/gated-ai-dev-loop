# 团队 Loop 模板与资源声明

团队模板用于统一常见结构，不替代 `hierarchy_contract` 返回的实时 schema。复制模板后必须根据真实工作区更新 ID、目标、验收、项目范围、Git 绑定和资源声明。

## 选择模板

| 场景 | 模板 | 保障档 |
|---|---|---|
| 单一局部内部修改，真实影响明确且定向测试充分 | [light-change.json](../examples/team-loops/light-change.json) | `LIGHT` |
| 一个需要独立实现和审查的功能 | [single-task-standard.json](../examples/team-loops/single-task-standard.json) | `STANDARD` |
| 多个结果并行、依赖或汇合 | [parallel-group-standard.json](../examples/team-loops/parallel-group-standard.json) | `STANDARD` |

`LIGHT` 不是按代码行数判断。规划 Agent 应基于实际改动内容和影响范围，检查真实代码、预计或已有 diff、调用方、数据、权限、部署和测试影响；不确定时使用 `STANDARD`。LIGHT 只编译一个根 TASK 和用户确认，执行中影响扩大必须 `REPLAN_REQUIRED` 升级为 STANDARD。

跨仓库交付使用 STANDARD 模板，并在实际开发工作区把每个仓库加入 `delivery.projectScopes`。所有可写 Git 项目使用同名 feature 分支，但各自冻结自己的基线提交。模板不写机器相关绝对路径，路径从接收工作区实时生成。

## resource claim 语义

`resourceClaims` 是跨 Delivery 的排他锁键，不是目录权限。当前冲突规则是两个集合存在**完全相同**字符串；`/` 只提高可读性，不表示前缀冲突。

例如：

- `env:test` 与 `env:test` 冲突。
- `env:test` 与 `env:test/database` 不冲突。
- `repo:catalog-service` 与 `repo:catalog-service/module:api` 不冲突。

因此层级写法不表示前缀冲突。需要把多个名字视为同一资源时，所有相关 Loop 必须声明同一个规范键；需要同时锁多个资源时，显式列出多个键。

## 命名规范

键使用小写稳定名称，只包含字母、数字、点、下划线、冒号、斜杠、`@` 和连字符。推荐前缀：

| 资源 | 示例 | 用途 |
|---|---|---|
| 仓库共享写入 | `repo:catalog-service` | 多个 Delivery 会修改同一共享工作树或索引 |
| 数据库/Schema | `db:test/orders` | 会修改同一测试数据库或 Schema |
| 端口 | `port:localhost/8080` | 会独占同一宿主端口 |
| 环境 | `env:staging/shared` | 会部署或重置同一共享环境 |
| 构件坐标 | `artifact:maven/com.example/core` | 会发布同一版本化构件 |
| 外部租户 | `tenant:sandbox/acme` | 会修改同一共享测试租户 |

规范要求：

1. 同一真实资源在所有仓库和团队中使用同一键。
2. 使用最窄但真实排他的粒度；不要用 `repo:all` 把无关任务全部串行化。
3. 不把用户名、临时 worktree 路径、工单号或随机会话 ID 当作资源名。
4. 文件可写范围由 `projectScopes`、sandbox 和 Git 授权控制，不通过 claim 扩权。
5. claim 在 dispatch reservation 阶段就占位；claim 暂停、终态或租约失效后释放。不要在另一个 Delivery 中用同义词绕过已有占位。
6. 团队维护一份公共前缀和资源登记表；新增共享数据库、环境或构件坐标时先登记再进入模板。

## Review 与资源

STANDARD 模板中的 TASK Review 通常复用 TASK 的资源键，因为 Review 可能在授权范围内推动修正；前驱关系本身也保证同一 TASK 的实现和 Review 不并发。纯只读 Review 可以不声明写资源，但不能据此获得未授权项目访问。

已配置的 GROUP seam Review 和 Delivery Acceptance/Readiness 只声明其真正需要独占的共享环境，不机械合并全部子 TASK claim。资源锁用于运行时冲突，依赖和 Review 顺序仍由 Graph edge 表达。

模板不要把“独立 Review”写成“重复运行全量测试”。TASK payload 应允许 receiver 从真实 changed files、依赖和契约界定最小充分测试范围，并在 `verificationEvidence` 中记录 scope 与测试时 workspace 指纹。TASK Review 只负责本 TASK 验收并优先复用状态匹配的证据；GROUP Review 只有存在真实直接子项 seam 时才配置；Delivery Acceptance/Readiness 聚焦顶层覆盖矩阵、整体 smoke/E2E、运行准备度和全局风险。只有影响范围无法界定或冻结验收明确要求时，模板才指定全量复跑。
