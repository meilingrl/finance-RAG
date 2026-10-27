# 金融文档证据问答与实验软件

面向财报文档的问答与对照实验工具。支持 BM25 / MiniLM 检索，以及规则抽取、文本、图文和结构辅助四种回答条件；提供本地网页工作台、CLI、结果复核与课程导出。

## 验证与复现入口

没有 API 密钥或外部模型权限，仍可执行标准库合成演示、算法测试与工程测试；BM25 和 E1 不需要模型权重。MiniLM 才需要固定本地权重。真实生成、已有结果复核和资料资格的前提各不相同。

先读[详尽应用指南](docs/guides/APP_GUIDE.md)：包含无密钥最小命令、数据与模型准备、自动/人工审核区别、封存结果复核、网络恢复及异常对照表。没有原始资源或授权时，明确记录哪些检查已执行、哪一层验证受阻。

## 快速开始

需要 Python 3.12。在仓库根目录执行：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/requirements-course.txt
.venv/bin/python scripts/course_entry.py
```

默认打开 `http://127.0.0.1:8765`。首次启动会自动下载、校验并登记固定课程资料与 MiniLM 权重（约252 MiB，分片并行传输），放在当前仓库的 `data/fixed-course-v1/`，页面显示进度。无需复制作者机器路径或手工选择候选包。填写模型服务地址、模型名称、并发与会话密钥，在正式实验页点击“准备并检查”，程序自动完成数据检查、文本/图像/并发验证和配置冻结；通过后点击“启动”。

单题演示始终串行。服务重启后需要重新填写会话密钥；密钥不包含在资源包中。离线安装、平台依赖和详细操作见[应用指南](docs/guides/APP_GUIDE.md)。当前进程锁依赖 Unix `fcntl`，Windows 尚未验收。

## 目录与阅读入口

| 入口 | 用途 |
| --- | --- |
| `app/`、`web/` | 应用服务与网页界面 |
| `formal_src/` | 正式实验协议、执行、评分与导出 |
| `src/` | 早期试验 CLI、检索与规则抽取；现行模块仍有依赖 |
| `scripts/` | 启动、资料准备、运行、汇总和打包工具 |
| `configs/` | 示例、服务配置与正式实验协议，见[配置说明](configs/README.md) |
| `requirements/` | 直接依赖与平台锁定清单，见[依赖说明](requirements/README.md) |
| `tests/` | 应用、实验、导出与前端测试 |
| [文档导航](docs/NAVIGATION.md) | 使用指南、设计、实验结果、课程报告及历史材料 |

本地 `data/`、`artifacts/`、虚拟环境和 `.local-secrets/` 不纳入 Git。固定主集与开发包由[版本资源](https://github.com/meilingrl/finance-RAG/releases/tag/fixed-data-v1)交付，仓库中的 `configs/fixed_dataset.json` 固定下载地址、分片及整体 SHA256。原始数据、冻结结果与恢复材料保持原位置；目录整理不会改变已有实验记录。

## 结果与课程材料

- [主测试集聚合结果](docs/experiments/MAIN_RESULTS.md)：已保存运行的结果与统计口径。
- [课程报告入口](docs/course-report/README.md)：报告技术副本、贡献声明与原始模板；个人主稿归属见该入口。
- [验证记录](docs/development/VALIDATION_RECORD.md)：工程验证及其限制。
- [课程交付说明](docs/delivery/README.md)：课程导出范围与外部资源要求。

课程报告须如实保留数据来源、AI 使用、实际贡献和实验限制。完整消融与第二模型结果不能由主测试集结果替代。

## 运行与复现的区别

| 要验证的内容 | 入口与额外条件 |
| --- | --- |
| 程序实现与工程行为 | 下方自动化测试；使用合成资料和模拟接口，无需真实 API 密钥 |
| 本地网页能否启动 | 上方启动命令；固定资料自动准备，真实生成另需服务凭据 |
| 已有结果能否独立复核 | 应用指南中的 `verify` 命令；需要原已封存运行目录和匹配的资源文件，源码克隆不含这些文件 |
| 真实主测试集能否重跑 | 使用自动安装的固定主集与编码器，填写API后点击“准备并检查”；会产生实际调用，结果可能受外部服务变化影响 |

已完成主集采用 `main_only` 范围；完整实验方案还包含未执行项目。核对报告时以[实际主集结果及边界](docs/experiments/MAIN_RESULTS.md)为准，不能把方案中的计划项目当作已完成结果。

## 本地验证

```sh
PYTHONPATH=src:scripts:tests:. .venv/bin/python -m unittest discover -s tests -t . -v
node --test tests/web/*.cjs
```

软件测试含合成资料与模拟接口，用于验证程序行为，不代表新增真实实验结果。
