# 金融文档证据问答与实验软件

需要 Python 3.12 与 Unix 环境；Windows 原生未验收。此包包含源码与聚合结果，数据、PDF/OCR、金标、权重和原运行工作区另行提供；资源身份见 `resources.json`，实际冻结配置见 `runtime/configuration.json`。TAT-DQA 数据保留 CC BY 4.0 归属。

## 无 API 权限也可验证实现

BM25 和 E1 规则抽取没有单独权重，无需 API 密钥。先从包根目录执行：

```sh
CHECK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/finance-rag-check.XXXXXX")"
python3.12 src/pilot.py demo --output "$CHECK_ROOT/fixture"
PYTHONPATH=src:scripts:tests:. python3.12 -m unittest tests.formal.test_methods tests.formal.test_run_plan -v
```

这是合成软件检查，不是实际实验成绩。完整安装、数据与 MiniLM 准备、已有结果复核、审核模式及异常处理，见包内 `docs/guides/APP_GUIDE.md`。

## 应用与工程测试

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/requirements-app.txt
.venv/bin/python scripts/course_entry.py serve --no-browser
PYTHONPATH=src:scripts:tests:. .venv/bin/python -m unittest discover -s tests -t . -v
```

打开 `http://127.0.0.1:8765`。依赖已安装后，启动页面和工程测试不要求真实生成服务。真实 E2/E3/E4 需要授权密钥、兼容服务和数据；MiniLM 需要额外运行依赖与固定权重。下载出来的候选数据不自动具备正式实验资格，不能通过修改审核/哈希字段强行通过。

## 已有结果复核与独立重跑

`verify` 不发送生成请求，但需要完整原运行工作区、匹配资源登记和隔离评价文件。仅凭本包聚合结果不能重新计算逐题成绩。取得这些材料后，按指南登记新位置，再执行：

```sh
.venv/bin/python scripts/course_entry.py --workspace /absolute/result-workspace verify --source-run RUN_ID
```

没有密钥时标明“真实生成未执行：缺少授权”，继续完成可执行的本地检查；没有封存预测或数据时标明具体交付缺项。反之，只有合成测试通过不能称为真实成绩复现。主集与完整计划范围以本包冻结配置和结果为准，未完成的消融或第二模型不计作已运行。实际贡献、AI 使用、数据来源与结果限制均保留。
