# 按变更范围验证与发布

本策略用于本地维护和正式发布。目标是让验证强度匹配实际风险：低风险文档改动快速发布，运行时和协议改动保持完整门禁。

## 先判断业务改动

分类时只看本次主动修改的业务源文件。以下派生变化不单独提高等级：

- `scripts/build_skill.py` 生成的 Skill/Plugin 镜像；
- 正式发版产生的版本号、CHANGELOG 和候选矩阵同步；
- 纯换行或格式变化。

若一次改动跨多个类别，使用最高等级。影响范围无法可靠判断时按“核心运行时”处理。

## 验证矩阵

| 变更类别 | 常见路径 | 是否构建 | 必需验证 | 是否全量测试 |
|---|---|---|---|---|
| 文档/策略 | `README.md`、`CHANGELOG.md`、`docs/**`、`AGENTS.md` | 否；正式版本号变化除外 | 相关文档测试、`git diff --check`；正式发布加 `validate_release.py` | 否 |
| 发布元数据 | `pyproject.toml` 版本、三个 Plugin manifest 版本、vendored `__init__.py` | 是，用构建同步版本镜像 | `validate_release.py`、Plugin manifest 校验、版本/入口文档定向测试、`git diff --check` | 否 |
| Skill/Plugin 契约 | canonical `skills/**`、Plugin manifest 工具/Profile/审批配置 | 是；仅 manifest 文案除外 | 四个 Skill 校验、Plugin 校验、`test_plugin_bundle`、`test_mcp_tool_profiles`、相关契约测试 | 仅触及 receiver/工具协议或范围不明时 |
| 宿主与发布脚本 | `scripts/host_smoke/**`、注册探针、发布校验、CI 配置 | 仅改变生成链时 | 对应脚本测试、`test_release_readiness`、必要的本地 probe | 仅触及共享生成链、CI 测试入口或公共 fixture 时 |
| 局部 Controller | 可明确隔离的 `src/hdg/**` 实现 | 是 | 相关测试模块、编译、Skill/Plugin 校验、release candidate | 正式发布前是 |
| 核心协议/状态 | MCP catalog/tools、Controller operation、Graph runtime、repository、schema、调度身份/租约、构建器 | 是 | 相关测试、全量测试、编译、四 Skill、Plugin、release candidate、差异校验 | 是 |
| 仅测试 | 单个测试模块 | 否 | 修改的测试模块 | 修改公共 fixture、发现入口或测试基础设施时 |

## 构建判定

运行 `python scripts/build_skill.py` 的条件：

1. `src/hdg/**` 运行时代码变化；
2. canonical `skills/**` 变化；
3. `scripts/build_skill.py` 变化；
4. 正式版本号变化，需要同步源码、Skill 和 Plugin 中的版本镜像。

纯文档、单元测试、CI、宿主冒烟脚本或非 payload manifest 文案变化不需要构建。构建后必须检查生成目录没有遗漏或额外漂移。

## 正式发布的共同门禁

无论类别，正式版本发布都必须完成：

```text
python scripts/validate_release.py
git diff --check
```

此外按矩阵补充定向测试、构建、编译、Skill/Plugin 校验或全量测试。失败后先修复，再从受影响层级重新验证；不要为了快速发布跳过已被实际改动触发的高等级门禁。

CI 可以继续执行跨 Python 版本的完整防线；本地已经完成同一提交的充分验证时，不因 CI 存在而机械重复无关测试。
