# 财报证据问答软件与实验

当前入口：已完成 200 题主测试集，聚合结果见[主集结果](docs/MAIN_RESULTS.md)；报告待补写见[课程报告](docs/course-report/README.md)。后续功能按当前时间补录入 Git，说明见[Git 历史与补录说明](docs/GIT_HISTORY.md)，本次验证见[验证记录](docs/VALIDATION_RECORD.md)。

源码仓库不包含 `data/`、`artifacts/`、模型权重、虚拟环境或 API 密钥。新机器需按[应用指南](docs/APP_GUIDE.md)配置依赖与对应资源。下文带日期的内容为历史快照，未运行状态不代表当前主集状态；完整消融与第二模型仍未执行。

## 历史实施记录

2026-10-24 实施：已建立本机工作台、统一CLI/HTTP服务、持久任务、完整矩阵执行器与独立复核/课程导出。当前先完成软件；真实正式实验尚未冻结，人工审核保留待办。启动与配置见 [软件使用说明](docs/APP_GUIDE.md)，实现/验证状态见 [当前状态](PILOT_STATUS.md)。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python scripts/course_entry.py
```

此前规划方案：[完整流程缩量方案](docs/FORMAL_EXPERIMENT_PLAN_SCALED_20261023.md)（只缩小题量，保留两检索器四组、三轮、全部消融／对照／第二模型及统计；第15节结合现有代码说明流程，正式运行尚未启动）。[原大规模方案](docs/FORMAL_LARGE_SCALE_EXPERIMENT_PLAN_20261023.md)及[保守收尾方案](docs/COURSE_PROJECT_PLAN_20261023.md)保留为历史记录。

软件与实验共用内核的设计安排：[接口与分工设计](docs/SOFTWARE_KERNEL_DESIGN_20261024.md)、[实施计划](docs/superpowers/plans/2026-10-24-software-kernel.md)（本机界面、配置与资源、持久任务及同一内核；实验方案不变）。

四组实验的集中阅读入口：[实验汇总报告](docs/EXPERIMENT_SUMMARY_20261023.md)（组别定义、统一分母、检索对比与诊断结论）。

2026-10-23 续接：已在原样本上完成BM25的纯文本复测、图文和结构描述配对实验。正常返回的共同32题中严格正确为14/15/13，图像仅净增1题、仅答案无增益；未观察到明确视觉提升。现行结果、分母与证据入口见 [PILOT_STATUS.md](PILOT_STATUS.md)。

2026-10-21 续接：已通过用户提供的Pinaic Responses服务，用同一个gpt-6.1-sol完成原36题的BM25/MiniLM文本RAG对比。严格匹配分别由规则基线4/36提高到17/36、16/36。现行结果与逐题CSV见 [PILOT_STATUS.md](PILOT_STATUS.md)，配置见 [大模型配置说明](docs/LLM_SETUP.md)。E3/E4未运行。下面保留2026-10-15非生成式阶段记录。

按原计划继续了可独立执行的非大模型部分：16 份 TAT-DQA 单页材料 / 48 道原始问题，问题盲区域、BM25、真实本地 MiniLM 句向量、固定抽取规则及分离的开发/试评。未运行任何生成式大模型；没有 API 费用、Git 推送、部署或发布。

这是工程源码、原始实验输出与复现记录，不是课程正式实验设计、结果分析或可直接提交的作业。课程要求学生自行审核设计、完成分析并声明 AI 使用范围；原始计划和任务书未改动。

## 2026-10-15非生成式阶段运行记录

- 官方 train：4 份 / 12 题开发；官方 dev：12 份 / 36 题试评。报告来源互不重复，随机种子 42；未按预测效果挑样本。
- 共 97 个证据单元；13 页使用局部区域，3 页因多栏布局使用整页诊断。逐块完整覆盖，无重复块；不把整页诊断与局部证据收益混称。
- BM25：finance/simple 分词，k=1/2 有限开发检查，k1=1.5、b=0.75。
- 真实 all-MiniLM-L6-v2：22,713,216 参数、384 维、mean pooling、余弦，固定版本，无微调。CPU 本地运行，推理时阻断 socket 网络。
- 固定 E1：年份/具名表头、标签值、有限叙述句和单位规则，只在开发材料校准。不能唯一确定时弃答。
- 冻结源代码、模型字节/依赖、规则、输入和两项 k=2 配置后，各执行一次试评。依据开发已核验证据覆盖和计时，后续扩展默认选 BM25 finance k=2；没有使用试评分数选择。
- E2/E3/E4 仍 disabled/not_run。请求草稿从未发送，未执行分数为 null。

最新有效目录：artifacts/nonllm-phase2/final-v5。早期 calibration、final、v2/v3/v4 是保留的开发/修复快照，不能混入最终试评。

## 环境与复现

核心 BM25 / E1 / 测试使用 Python 3.12 标准库。真实 PDF 准备/裁图另需 PyMuPDF；已在当前云环境验证。MiniLM 运行时与权重置于项目外，不应打包入课程 ZIP。模型版本/依赖与独立断网验证见 docs/ENCODER_SETUP.md 和 artifacts/encoder-provisioning/。

当前云环境已经安装并实际运行：

- Python：/workspace/shared/rag-encoder-venv/bin/python
- 模型：/workspace/shared/rag-models/all-MiniLM-L6-v2-1110a243fdf4
- 固定 revision：1110a243fdf4706b3f48f1d95db1a4f5529b4d41

在项目根目录的组合复跑入口（组成步骤已分阶段验证；输出目录必须尚不存在）：

```bash
OMP_NUM_THREADS=2 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false \
/workspace/shared/rag-encoder-venv/bin/python scripts/run_nonllm.py \
  --data data/processed/nonllm-pilot-v5 \
  --output /tmp/rag-nonllm-reproduce \
  --model-path /workspace/shared/rag-models/all-MiniLM-L6-v2-1110a243fdf4
```

这条命令按预定范围重跑 5 项开发配置、冻结、2 项试评；不下载、不调用远程服务、不生成文本。首次在其他环境复现时，按 docs/ENCODER_SETUP.md 准备同版本模型和依赖，再替换 Python/模型路径。时间会随机器而变。

不需要句向量运行时的标准库测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python src/pilot.py demo --output /tmp/rag-software-fixture-new
```

fixture 是明确标注的人工软件测试，不是 TAT-DQA 实验。

## 数据与金标隔离

输入来源、完整候选筛选、排除原因、种子和逐文件 SHA-256：data/phase2/PROVENANCE.json、sample_manifest.json 和 ACQUISITION.md。公开数据许可按当前官方发布页为 CC BY 4.0；保留归属与许可声明。原始 QA、PDF、OCR 未改写；只下载选中 16 对 PDF/OCR，未下载整套 ZIP。

- data/phase2/inputs/：输入 PDF/OCR 与文档清单
- data/phase2/development/：开发问题、原金标及独立核验金标
- data/phase2/evaluation/questions.jsonl：试评问题，无答案
- data/phase2/isolated_gold/：试评答案，仅冻结后的评分/核验可读
- data/processed/nonllm-pilot-v5/：固定推理问题、证据、裁图、只含金标路径/哈希的 manifest

prepare-inputs 完全不打开金标。推理和请求准备结束才评分；试评必须给 --freeze。score-frozen 用独立核验后的证据映射重新评分，不重新推理，不允许修改官方答案、量级或问题集合。

复建输入的实际命令：

```bash
python src/pilot.py prepare-inputs \
  --documents data/phase2/inputs/documents.jsonl \
  --questions data/phase2/development/questions.jsonl \
  --questions data/phase2/evaluation/questions.jsonl \
  --gold-manifest data/phase2/gold_sources.json \
  --output /tmp/rag-inputs-new
```

源数据需要重新获取时，scripts/phase2_acquire.py 提供固定版本的有界下载路径；先阅读其 --help 和 data/phase2/ACQUISITION.md。不要把未核验的新数据覆盖已冻结目录。

## 原始结果与指标口径

最终原始数值由 artifacts/nonllm-phase2/RAW_RESULTS.json 汇总，逐题记录在各运行的 results.csv、predictions.jsonl、retrieval.jsonl。指标是自定义，不是官方 TAT-DQA EM/F1。

务必区分：

1. 自定义严格 EM：答案匹配且独立 scale 字段一致；当前试评两检索器均 4/36。
2. 仅答案 EM：BM25 9/36、MiniLM 7/36，弃答仍在总体分母；metrics.json 内同名字段的分母是已回答数，汇总额外给出总体分母。
3. 覆盖率：BM25 12/36、MiniLM 11/36。数值/单位诊断只对已回答且原答案为数值的子集计分。
4. 证据命中：answer-bearing support_block_ids 与完整支持 complete_support_block_ids 分开；未核验、矛盾的原标注不计入有效检索分母。

试评独立核验的原映射覆盖：BM25 命中 32/35、完整覆盖 27/31；MiniLM 33/35、29/31。局部区域单列为 24/27、20/24 与 25/27、22/24。原映射不是所有可能支持证据的穷举，所以这些数字不是穷尽语义 Recall。一题时间指代不清未核验，四题量级表头不确定，只计至少命中、不计完整覆盖。

BM25 的五个严格 EM / 答案 EM 差异中，三个是等价的行内单位/空 scale 字段表示差异，另两个确实漏了 thousand 量级。保持冻结评分器，不把这些差异全部解释成金融数值错误。开发中发现一题原标注取了 Cost 列而问题问 Fair Value，原答案保留，证据核验明确标记冲突。

重评分实际命令（已验证，换新输出目录复跑）：

```bash
python src/pilot.py score-frozen \
  --run artifacts/nonllm-phase2/final-v5/evaluation/bm25-finance-k2 \
  --gold data/phase2/isolated_gold/gold_semantic_verified.jsonl \
  --output /tmp/rag-bm25-reviewed-new
```

交付打包清单：docs/PACKAGING_MANIFEST.json；脚本 scripts/package_snapshot.py 会排除权重、虚拟环境、完整 QA 元数据和已被替代的开发快照，逐文件计算哈希并检查 ZIP <50MB。保留旧阶段关键产物与修复记录。这是工程快照；含选中公开样本，不能直接按课程提交。

## 已知限制

- 规则是有限抽取基线，试评覆盖仍低；开发结果不能代替试评结果，也不能仅据规则失败宣称大模型更强。
- MiniLM 仅用最多 256 token 编码。试评 67 个候选单元中 9 个超长，63 次被选中的单元出现中 16 次被截断；E1 和共有证据文本仍完整。逐单元原 token 数与截断标志保留于 encoder_diagnostics，不能声称完整长表都进入了向量。
- 3 个整页文档 / 9 题单列为诊断，实际 k=1；局部部分是 9 文档 / 27 题。
- 盲审检查了 8 个原始页面、39/97 个单元，58 个单元未目视核验。部分裁图缺表格最下边框但未发现截断字形；Oracle 有原 OCR 大小写差异。
- BCE 整页图像的旋转侧栏在官方 OCR 中缺失，存在图文范围不一致。没有补建 OCR；E3 主比较资格保持关闭。
- 引用自动检查仅保证编号存在；语义支持不能由编号存在性推导。
- 小样本结果、非官方评分、未执行的大模型组不能支持课程验收或普遍效果结论。

完整过程中的开发修复、核验范围和盲审警告见 docs/NONLLM_ENGINEERING_LOG.md、artifacts/phase2-visual-review/、artifacts/nonllm-phase2/independent-audit/。

## 本地版本管理

项目根目录使用 Git 管理源码、测试、配置样例和文档。用 `git status` 查看改动，`git log --oneline` 查看历史，`git diff` 比较未提交改动。密钥、数据、权重、虚拟环境和实验输出由 `.gitignore` 排除，须另行保留；不使用 `git add -f` 将它们加入版本库。当前版本与导入边界见 [状态主记录](PILOT_STATUS.md)。
