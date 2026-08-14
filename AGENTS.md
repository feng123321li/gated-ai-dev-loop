# AGENTS.md

## 仓库维护约束

本仓库的 `pyproject.toml` 项目名为 `delivery-graph`。项目与 Plugin 均由 Python 3.10+ 和标准库驱动。维护源码时直接修改 Plugin、Skill、MCP、文档和测试，不为维护工作创建 `.layered-delivery/**` 运行包，也不调用 `approve-item`、`prepare-item` 或 `freeze-item`。`.layered-delivery` 是兼容的运行数据 namespace，不随项目改名迁移。

只有用户明确要求 dogfood/演练运行任务包时，才可进入标准运行包流程。此时所有会写控制面的层级命令都必须显式携带 `--dogfood`，并继续满足各命令原有的批准或确认条件；“升级 Skill”“优化流程”以及 Delivery、Capability、governance 等词都不是 dogfood 授权。

规范 Skill 名为 `delivery-graph`，不追加版本后缀。只维护当前完整 schema v3，不增加旧 schema 迁移或兼容入口。

修改和发布前先按 [docs/release-strategy.md](docs/release-strategy.md) 以业务源文件分类；生成镜像和纯版本号文件不单独抬高验证等级。所有改动至少运行相关测试与 `git diff --check`，正式版本还必须运行 `python scripts/validate_release.py`。只有 Controller/MCP/持久化/schema、共享生成链、共享测试基础设施发生行为变化，或影响范围无法可靠判断时才运行全量 `unittest`；纯文档、发布元数据、Skill 文案、宿主脚本和局部测试按矩阵执行定向验证。

仅在以下情况运行 `python scripts/build_skill.py`：`src/hdg/**` 运行时发生变化、canonical `skills/**` 发生变化、`scripts/build_skill.py` 发生变化，或正式版本号变化需要同步 vendored `__init__.py`。纯 `README.md`、`CHANGELOG.md`、`docs/**`、测试、CI、宿主冒烟脚本和 manifest 文案变化不单独触发构建。构建后必须确认 `skills/delivery-graph/scripts/hdg_mcp.py`、`skills/delivery-graph/scripts/hdg/**` 与 Plugin Skill 镜像一致，Plugin 产物不得恢复 CLI 入口。

## Sandbox 与 Git 发布

当前桌面环境的 sandbox 不能可靠访问 Windows Keyring、SSH Agent、远程网络或仓库 `.git` 写权限。sandbox 内的 `gh auth status` 可能错误显示 token 无效，不能据此判定宿主机 GitHub 凭据失效；需要认证事实时，直接在获得只读升级权限后于 sandbox 外运行 `gh auth status`。

只读的 `git status`、`git diff`、`git log` 和本地文件检查留在 sandbox 内。用户已经明确授权提交或发布时，写 `.git` 的 `switch/add/commit/merge`，以及使用凭据或网络的 `fetch/push`、`gh pr` 和内部 GitLab/Marketplace 发布命令，应直接申请 `require_escalated` 在 sandbox 外执行，不先用预期会失败的 sandbox 调用试探。sandbox 外执行不扩大授权范围；提交、推送、合并、创建 PR 或发布仍分别以用户明确要求为前提，不输出或记录凭据内容。
