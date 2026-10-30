# 分支归档与工作区收尾（2026-10-30）

用户确认新版实跑通过后，统一保留 `main` 作为本地和远端唯一开发分支。收尾前基线为 `f732e33501181a2f1e7d9b211fdf27cf6227037e`。下面每个分支末端均已通过 Git 祖先检查确认属于 main 历史，没有遗漏的独有提交，故本轮无需制造重复合并。

## 分支记录

归档使用带注释的 Git tag，本地与 origin 均保留；先验证远端 tag 指向原提交，再删除分支名。现有 merge commit、PR 与作者历史未重写。

| 原分支 | 原末端提交 | 归档 tag | 集成记录 |
| --- | --- | --- | --- |
| `ui/horizontal-scenes` | `a0ba3b91cde9b010c447a7722f9fe8a10abaafa7` | `archive/2026-10-30/ui/horizontal-scenes` | 固定资料交付，已为 main 祖先 |
| `fix/compatibility-usability` | `67633d6a462122021a7bad3a03dcb996201e8ac8` | `archive/2026-10-30/fix/compatibility-usability` | [PR #1](https://github.com/meilingrl/finance-RAG/pull/1) |
| `windows-compat` | `cab26b99af59ec843de3b46fa33bded516fb6ed0` | `archive/2026-10-30/windows-compat` | [PR #2](https://github.com/meilingrl/finance-RAG/pull/2)，选择性集成 |
| `fix/windows-integration` | `d00bc26af819c9d5acb2cd993376a9e56309a21c` | `archive/2026-10-30/fix/windows-integration` | [PR #2](https://github.com/meilingrl/finance-RAG/pull/2) |
| `fix/windows-test-shutdown` | `54bc1710da8617b749dfe3560a83466bd1d23eaa` | `archive/2026-10-30/fix/windows-test-shutdown` | [PR #3](https://github.com/meilingrl/finance-RAG/pull/3) |

Windows 源分支的原始内容完整保留在 tag 中；main 沿用已确认的取舍，不重新引入被排除的模型目录检索功能。完整验证与取舍见[验证记录](../development/VALIDATION_RECORD.md)。

查看历史：

```sh
git fetch origin --tags
git show archive/2026-10-30/windows-compat
git log --graph --oneline --decorate main
```

## 本机工作区及资产

以下路径相对于 `/Users/meilingluo/School/Courses/2026-Fall/NLP`；这些目录未搬移、未删除。

| 位置 | 收尾后的用途 |
| --- | --- |
| `rag-pilot.worktrees/windows-integration` | 当前 main checkout；新版服务 `http://127.0.0.1:8788` 仍在运行，独立工作区为 `artifacts/windows-runtime-check/workspace/` |
| `rag-pilot` | detached HEAD 保留在 `a0ba3b9`；旧实验源码、资料、结果、环境、未跟踪 `data.zip` 和既有本地文档修改保留，不用于新的开发提交 |
| `rag-pilot.worktrees/compatibility-usability` | detached HEAD 保留在 `67633d6`；保留此前验收截图、数据副本及工作区，不作为活动开发分支 |

旧 checkout 不切换到新源码，以保持旧运行的代码身份。今后修改代码从 main checkout 开始；不要在 detached HEAD 中产生无人引用的新提交。

旧 checkout 的两份未提交文档已额外按字节备份到当前 main checkout 的 `artifacts/branch-closeout-20261030/`，包含补丁、原文件副本和 SHA256；切换 detached HEAD 后核对字节未变。此备份已不参与运行，作为本次收尾保留证据；它不包含数据、密钥或整个工作区备份，也未上传 Git。运行服务、截图和测试日志的用途见验证记录中的最新实跑章节。
