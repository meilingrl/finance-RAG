# 财报 RAG：无大模型基础设施原型

这份交付实现既有计划的工程骨架，不是课程实验设计或结果分析。当前不包含任何大模型执行入口，不下载模型、不调用付费 API；E2/E3/E4 只生成禁用的请求草稿。原始计划及课程任务书保持不变。

## 已验证的环境与一条命令

Python 3.12.14。核心 BM25、E1、指标、fixture 和测试使用标准库。当前云端已有 pypdf/PyMuPDF，仅真实 PDF 导入/渲染使用它们；版本见 requirements-optional.txt。没有安装新依赖。

在本目录运行：

```bash
python src/pilot.py demo --output /tmp/rag-demo-new
```

输出 offline_complete、2 个明确标注的人工软件测试问题、6 份未发送的模型请求草稿、0 次模型调用。该 fixture 只验证代码，不是 TAT-DQA 实验数据，不能作为模型实验结果。输出目录不能已经冻结；复跑时换一个新目录。

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
```

## 真实数据复跑

随附最小官方样本副本：3 份 train 文档和 1 份 dev 文档，各最多 3 道原始 span/非比较问题。不是完整试跑目标，不足 10 份/30 题最低比较规模。数据出处与文件校验和见 data/raw/tatdqa/PROVENANCE.json；许可按发布者声明为 CC BY 4.0，保留归属。未下载整个数据集。

```bash
python src/pilot.py import-tatdqa \
  --qa train=data/raw/tatdqa/qa/train_selected_records.json \
  --qa dev=data/raw/tatdqa/qa/dev_selected_record.json \
  --docs-dir data/raw/tatdqa/documents \
  --output /tmp/rag-real-input-new --dev-docs 3 --eval-docs 1 --render

python src/pilot.py run-offline --data /tmp/rag-real-input-new \
  --output /tmp/rag-development-new --split development
python src/pilot.py run-offline --data /tmp/rag-real-input-new \
  --output /tmp/rag-evaluation-new --split pilot_evaluation

python src/pilot.py development-check --data /tmp/rag-real-input-new \
  --output /tmp/rag-dev-k-new
```

项目内生成的路径是相对项目根目录的；外部输出目录会保留绝对路径。移动交付目录后，在其根目录运行。不要直接修改已冻结的 processed 数据/结果；新版本使用新的输出目录。

## 实现边界

- 官方 QA 导入：仅单页 PDF/JSON 配对、span、req_comparison=false；最多 3 题/材料、种子 42；优先 train→开发、dev/test→试评。同一 source_report 不能跨两组。记录候选、筛选理由、来源哈希。
- 金标隔离：推理问题文件无答案/推导/事实/映射；评价金标另存。构建证据与描述的接口不接收 QA。评分阶段才打开 evaluation_gold.jsonl。
- 统一证据：默认整页，避免未经核验地拆坏表格。可输入问题盲的区域提案；裁剪边界扩至完整相交文字块，并纳入所有新增文字。当前真实样本全部为 whole_page 诊断。
- 图像：使用本地 PDF 渲染，记录 OCR→PDF 比例、CropBox/MediaBox 和图像 SHA-256。官方 224×224 PNG 仅是缩略图，不能直接套用 OCR 坐标。Cisco 样本有非零 CropBox 偏移；PyMuPDF page.rect/get_pixmap 使用 CropBox 相对坐标。字框本身可能不准，因此尚未批准局部裁图。
- BM25：纯 Python Okapi 实现，k1=1.5、b=0.75；finance/simple 分词可选，保留数字形式/百分号/金融缩写/连字符。文档内检索，k=1/2，稳定平局排序，候选不足不补齐。参数/实际 k/计时写入结果。
- 句向量：仅已有本地 sentence-transformers 模型目录的可选适配。local_files_only=true、trust_remote_code=false、无安装/下载路径；记录模型指纹、版本、池化、归一化、余弦相似度、截断信息。当前依赖和模型均未准备，未执行真实句向量检索；测试仅模拟接口与缺失路径。
- 冻结：问题、全部候选、选择结果、文字/元数据和图片字节均校验；之后 E1–E4 共享一次检索结果。数据变化被拒绝，不静默刷新。
- E1：保守的固定 label/value、关键词和年份规则；不能唯一抽取就“无法确定”。规则仅完成软件测试，未完成开发材料校准，不能把低覆盖用于证明其他方案优越。
- E2/E3/E4：问题、公共文本、顺序、system、输出结构和生成设置共用；仅图像/描述附件不同。无执行命令，状态 not_run。上下文只做字符预检，真正 token/图像预算未知，不悄悄截断。
- 描述：问题无关的坐标模板，每个证据单元一次；不臆造表头/列关系。未经核验的描述不具备主比较资格；question_exposure=true 只允许诊断。
- 指标：明确标为自定义、非官方 EM/F1，保留金额符号、负号、百分号和量级；数值与单位另报。错误、拒答、弃答、未执行分开，未跑模型不记 0 分。映射未核验时不计算证据命中率；完整支持覆盖还要求 support_complete=true。只自动检查引用存在性，不自动声称语义支持。
- 日志：仅有白名单元数据，不读取/保存环境变量、凭据或 Authorization；无真实 API 原始响应，无假造响应。

## 输出

最新交付位置见 artifacts/ARTIFACT_INDEX.json。

每个运行目录包含 data_manifest.json、frozen_config.json、evidence_units.jsonl、descriptions.jsonl、retrieval.jsonl、predictions.jsonl、prepared_requests.jsonl、metrics.json、results.csv、cases.md、events.jsonl、run.json。

results.csv/metrics.json 属于评价产物，允许含金标；不得反向送入检索、描述或请求。prepared_requests.jsonl 只含推理输入；存在请求草稿不代表执行、模型支持或费用授权。

## 下一阶段的具体缺口

当前基础设施已完成可复跑验证；句向量和模型实验均暂停。后续需要学生先核验/批准区域、证据映射和规则，再选择本地句向量模型及固定版本。如要启用真实生成，还需同一图文模型入口、明确预算、输出适配、上下文/token 与计费核查。本原型刻意不包含付费调用执行器。

这不是可直接提交的完整课程作业。AI 辅助范围是工程实现、公开来源核验和测试；学生须自行审核实验设计、完成结果分析、说明人工修改与个人贡献。没有初始化 Git、推送、部署或发布。
