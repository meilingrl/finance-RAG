# 大模型续接：现行服务与运行方式

## 现行软件入口（2026-10-25）

后续实验通过[软件工作台](APP_GUIDE.md)及同一CLI/runner执行，不再从下面的历史试评命令重跑旧任务。当前服务与模型保持Pinaic Responses、gpt-6.1-sol/gpt-6-astra、low、stream=true、2048输出和store=false。

本轮502已修复：当前代理链路对Python urllib的隐式客户端标识返回上游拒绝；软件在`formal_src/http_transport.py`显式设置`User-Agent: rag-pilot/1.0`。相同payload下已取得两模型文本/合成图响应及软件内真实E2结果；不需要换密钥或改macOS系统代理。具体服务端历史变化尚无法从客户端记录确定，不能把这次诊断推回为以前从未成功。

[排查与验证记录](../artifacts/provider-debug-20261025/REPORT.md)保留失败、请求身份和调用账本信息；正式实验门槛未自动放行。以下为各日期原有执行记录。

2026-10-23：本轮BM25的同时期E2复测、E3图文、E4结构描述已执行并评分完成。结果与32／33／36题分母见 [PILOT_STATUS.md](../PILOT_STATUS.md)；本轮累计调用185/200。E3/E4入口是独立新增构建器，原E2执行器及冻结src未改。

## 本轮E3/E4与同时期对照

固定原BM25 finance k2、共同文本、模型与提示。先由未接触问题／答案的独立核验者检查图文范围和结构描述，再在开发题验证接口，最后执行提前封存的试评任务。BCE3题仅从图像组排除；一项E2/E3上游失败单列，不当作模型回答错误。

- 输入核验及叠加文件：`artifacts/llm-phase4/blind-review/`。旧证据文件和描述不改。
- 构建器：`scripts/multimodal_jobs.py`；E3添加原PNG的Responses `input_image`（base64，detail=high），E4添加原几何模板及统一原文索引。可读原文索引会重复文字，效果不能单独归因于几何关系。图像字段实现参考[官方图像输入规范](https://developers.openai.com/api/docs/guides/images-vision)。
- 同时期E2复测与E3/E4统一追加 `metadata.experiment_cohort=phase4-20261023-v1`，仅区分审计请求，不改模型可见提示；原10月21日E2结果完整保留。不得去掉标识来伪装复用，也不要再次更换标识重发已完成请求。
- 新任务为`artifacts/llm-phase4/e2-evaluation-v1`、`e3-evaluation-v1`、`e4-evaluation-v1`，均已完成。开发v1是未发请求的准备快照，见各目录SUPERSEDED.json；有效开发为v2。
- 执行仍使用原`llm_execution.py execute`、同一个密钥文件和`artifacts/llm-phase3/budget.jsonl`。上游失败已检查，仅续发未发送题，失败记录保留，未重发。
- 新任务内部输入预留131072，不改变服务端生成参数；每张图片32768是操作性预留，不是已验证的代理tokenizer上界。请求保持原裁图，不静默裁剪／缩放／截断；返回usage超预留会停机。
- 推理完成后才由`scripts/summarize_multimodal.py`调用原评分器，另行生成完整分母、共同题集、共同正常返回子集及整页诊断。现有`RESULTS.json`不允许覆盖，所有记录已保留，不需要重复启动。

以下保留10月21日E2阶段的配置与历史执行说明；其中“E3/E4未开放”仅指当时的原E2入口。

## 10月21日E2阶段记录

2026-10-21：用户授权保持原样本规模，开展真实大模型实验及必要模型对照，不设金额限额。本批已完成BM25与MiniLM检索下的E1/E2及同一回答模型的检索对比；E3/E4仍待证据核验。全部36题口径结果与逐题CSV见 [PILOT_STATUS.md](../PILOT_STATUS.md)。

## 当前配置

用户提供的服务是 `https://api.pinaic.com/v1`，使用 Responses API。本机同一密钥在这个服务的 `GET /v1/models` 认证通过。此前请求发到 OpenAI 官方接口而401，原错误保留；不再要求用户更换密钥。

服务列表中没有配置示例里的 `gpt-5.5`，有 `gpt-6-luna` 和 `gpt-6.1-sol`。Luna非流式/流式请求分别返回502；Sol的3道开发题成功返回结构化答案，因此本批选择 **gpt-6.1-sol**。这项选择基于开发链路可用性，未使用试评得分选择模型。

现行无密钥配置：`config.llm.pinaic-sol.json`。

| 参数 | 固定值 |
|---|---|
| endpoint | https://api.pinaic.com/v1/responses |
| model | gpt-6.1-sol |
| wire_api / stream | responses / true |
| reasoning effort | low |
| max output tokens | 2048 |
| store | false |
| 回答格式 | 最短原文答案、固定scale枚举、证据编号、可弃答 |

OpenAI原模型的[能力说明](https://developers.openai.com/api/docs/models/gpt-6.1-sol)和[Responses结构化输出规范](https://developers.openai.com/api/docs/guides/structured-outputs)仅用于实现参考。代理服务返回的模型名与usage保留在原始响应；没有独立验证其底层模型身份，也不能把OpenAI官方价格套作该服务账单。

## 密钥与费用

密钥已在本机 `.local-secrets/openai-api-key.txt` 配置并通过服务认证；不要发送到聊天。该目录从Git和打包清单排除，也可通过环境变量 `OPENAI_API_KEY` 提供密钥。

用户已经授权无金额上限，因此使用显式 `--no-cost-limit`；不重复要求预算确认。该服务费率尚未知，配置中两项费率均为null，实际费用记录为未知；保留每次真实响应的输入/输出/缓存/推理token。固定全局账本 `artifacts/llm-phase3/budget.jsonl` 仍限制最多200次生成尝试，认证与上游失败也保留，不能删除账本重置次数。

旧官方接口尝试按旧价格产生的$0.0035328只是程序预留，不是实际账单；所有无usage的失败请求及中断请求费用均不可据此核实。

## 本批数据和记录

- 原始16份/48题不扩大，原4份/12题开发、12份/36题试评角色不变。
- 复用 `artifacts/nonllm-phase2/final-v5/evaluation/bm25-finance-k2` 的检索结果和原E1；原 `src/`、原金标、原评分器不改。
- v3开发核对发现回答扩成长句、scale写years等输出口径问题。只依据开发题加入通用短答案指导和scale枚举，不加入题目特例、具体答案或金标。
- v3已启动的部分试评停止，保留7条完整输出及在途预留，写 `ABANDONED.json` 排除主比较；没有用其预测内容或试评答案调提示。v3源码保留在 `artifacts/llm-phase3/protocol-v3-source/`，与v3 job的冻结SHA一致。
- v4开发3题返回3个可解析答案，严格EM为2/3；只是开发校准，不代替试评。
- 现行主试评：`artifacts/llm-phase3/pinaic-sol-bm25-evaluation36-v4/`及`pinaic-sol-minilm-evaluation36-v4/`，均已完成独立score。严格匹配分别17/36、16/36；仅答案22/36、21/36。每组弃答均保留。
- 旧readiness与authentication-status都是注明时间的阶段快照，当前状态优先看 `PILOT_STATUS.md` 和本批运行目录。

E3图像范围、E4结构描述仍未完成核验，本批入口不开放这两组；不能移除阻断标志来生成主比较。后续若更换模型，需按完整配对范围另行冻结，不能只重跑答错题。

## 执行与评分

从 `rag-pilot/` 根目录运行，当前两组任务已完成；以下是复现入口，不要为覆盖旧结果而重复启动。请求前锁定源码、输入、检索、模型参数和完整payload。已有结果不会再次发送；认证/模型/usage错误默认暂停，检查后 `--resume-after-inspection` 只继续尚未发送题目，不重发已失败题。

```sh
python3 -B scripts/llm_execution.py execute \
  --job artifacts/llm-phase3/pinaic-sol-bm25-evaluation36-v4 \
  --key-file .local-secrets/openai-api-key.txt \
  --no-cost-limit
```

仅在完整推理完成后评分：

```sh
python3 -B scripts/llm_execution.py score \
  --job artifacts/llm-phase3/pinaic-sol-bm25-evaluation36-v4 \
  --reviewed-gold data/phase2/isolated_gold/gold_semantic_verified.jsonl \
  --output artifacts/llm-phase3/pinaic-sol-bm25-evaluation36-v4-scores
```

评分复用原 `ragpilot.metrics.evaluate`，严格答案/scale规则不变。所有题保留在总体结果中，另列弃答、API错误、解析错误、拒绝、截断。仅答案EM、严格EM和数值诊断分母分别说明；引用存在不等于语义支持，正式课程分析由学生完成。

MiniLM组有15项完整请求与BM25相同。先用`python3 -B scripts/reuse_llm_responses.py --source-job <完整BM25运行目录> --destination-job <新MiniLM job目录>`复用完全相同的既有响应，再执行其余21项。复用验证同题、同endpoint、完整payload、源结果hash，保留目标检索元数据与源响应链接；顶层usage不重复计数。

## 软件验证

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest discover -s tests -v
```

新增测试包含人工fixture、模拟API与本地HTTP/SSE传输。prepare/execute不读gold；score在结果密封后单独读答案。测试还验证持久化调用计数、未知价格不伪称美元限额、错误暂停、重定向拒绝、Responses拒绝/截断与无换行流的大小/时间边界。源码改变或目录迁移后旧job仅作历史记录，在新目录重新prepare，不覆盖历史产物。
