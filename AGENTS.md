# AGENTS.md

## 仓库维护约束

本仓库的 `pyproject.toml` 项目名为 `layered-delivery`。项目与 Skill 均由 Python 3.10+ 和标准库驱动。维护源码时直接修改 Skill、CLI、文档和测试，不为维护工作创建 `.layered-delivery/**` 运行包，也不调用 `approve-item`、`prepare-item` 或 `freeze-item`。

只有用户明确要求 dogfood/演练运行任务包时，才可进入标准运行包流程。此时所有会写控制面的层级命令都必须显式携带 `--dogfood`，并继续满足各命令原有的批准或确认条件；“升级 Skill”“优化流程”以及 Delivery、Capability、governance 等词都不是 dogfood 授权。

规范 Skill 名为 `layered-delivery`，不追加版本后缀。只维护当前完整 schema v3，不增加旧 schema 迁移或兼容入口。修改后运行相关测试、全量 `unittest`、Python 编译检查、Skill 校验和 `git diff --check`；更新控制器源码时运行 `python scripts/build_skill.py`，重新构建 `skills/layered-delivery/scripts/hdg.py` 与 `skills/layered-delivery/scripts/hdg/**`。

## Sandbox 与 Git 发布

当前桌面环境的 sandbox 不能可靠访问 Windows Keyring、SSH Agent、远程网络或仓库 `.git` 写权限。sandbox 内的 `gh auth status` 可能错误显示 token 无效，不能据此判定宿主机 GitHub 凭据失效；需要认证事实时，直接在获得只读升级权限后于 sandbox 外运行 `gh auth status`。

只读的 `git status`、`git diff`、`git log` 和本地文件检查留在 sandbox 内。用户已经明确授权提交或发布时，写 `.git` 的 `switch/add/commit/merge`，以及使用凭据或网络的 `fetch/push`、`gh pr` 和内部 GitLab/Marketplace 发布命令，应直接申请 `require_escalated` 在 sandbox 外执行，不先用预期会失败的 sandbox 调用试探。sandbox 外执行不扩大授权范围；提交、推送、合并、创建 PR 或发布仍分别以用户明确要求为前提，不输出或记录凭据内容。
