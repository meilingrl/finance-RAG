# 软件发布说明

## v1.0.0 · 2026-10-30

首个软件版本：[GitHub Release](https://github.com/meilingrl/finance-RAG/releases/tag/v1.0.0)。源码功能基线为 `f732e33501181a2f1e7d9b211fdf27cf6227037e`，随后仅更新收尾与发布文档。

### 主要变化

- 集成网页工作台、资料准备与后台操作恢复改进，保留最新主线交互。
- 加入 Windows 原生文件锁、UTF-8 启动、文件替换/读取重试、跨平台清单路径以及进程身份检查。
- 修复任务结束时事件尚未排空导致误报中断的问题，并补充并发恢复与退出回归验证。
- 开发分支统一为 `main`，旧分支提交和来源保留在合并历史及[归档标签](../history/BRANCH_ARCHIVE_20261030.md)中。

### 下载与安装

下载附件 `finance-RAG-v1.0.0-source.zip` 和 `SHA256SUMS.txt`。这是源码发行包，需要 Python 3.12；不是免安装可执行程序。解压后按[应用指南](../guides/APP_GUIDE.md)安装依赖并启动，Windows 使用其中的 PowerShell 命令。

校验 ZIP：macOS/Linux 使用 `shasum -a 256 finance-RAG-v1.0.0-source.zip`，Windows PowerShell 使用 `Get-FileHash .\finance-RAG-v1.0.0-source.zip -Algorithm SHA256`，对照 `SHA256SUMS.txt`。

源码包由发布标签对应的 Git 受跟踪文件生成，不包含本机数据、模型权重、运行工作区、依赖环境或密钥。首次启动所需约 252 MiB 固定资料与 MiniLM 权重继续从独立的 `fixed-data-v1` 下载；该数据版本与固定下载地址保持不变。离线安装见应用指南。

### 已验证范围

- macOS：安装固定数据后完整 Python 测试 484 项，482 通过、2 项可选 pypdf 测试跳过；前端与 Chrome 测试 19 项全部通过。
- Windows / Ubuntu 原生 CI：各运行 69 项兼容性回归，加上 30 项重复并发、暂停/恢复和退出检查。功能基线 [CI](https://github.com/meilingrl/finance-RAG/actions/runs/41062681611) 与收尾文档基线 [CI](https://github.com/meilingrl/finance-RAG/actions/runs/41067593337) 均通过；发布提交的 CI 以仓库 Actions 为准。
- 本地网页使用真实固定资料完成 BM25 + E1、MiniLM + E1 单题运行及刷新恢复；未调用外部模型服务，未产生新的正式实验结论。详细记录见[验证记录](../development/VALIDATION_RECORD.md)。

### 限制

Windows 验证覆盖上述原生 CI 子集，不代表全量 Windows 桌面或正式实验验收。Windows 目录 fsync 不提供断电时目录项持久性保证；缺少 `O_NOFOLLOW` 时诊断 HTTP 接口保守返回空对象，磁盘诊断仍保留。外部生成模型需要独立配置并通过就绪检查。

本版本未纳入旧 Windows 分支中的模型自动目录功能。软件源码发行不替代课程提交材料、封存工作区或研究结果复核。

本地 `artifacts/releases/v1.0.0/` 用于保存本次打包、校验和发布核验材料；它不进入源码包，作为发布证据保留。
