# Software Shell and Experiment Kernel Implementation Plan

执行注记（2026-10-24）：用户已在后续消息授权实施，并确认暂无审核人、先完成软件。以下“本轮仅编制计划”保留起草时事实；实际工作包状态与验证见 [实施账本](../../../artifacts/software-kernel-implementation/progress.md)，使用见 [软件说明](../../APP_GUIDE.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本轮只编制计划，未启动实现、子任务或真实调用。

**Goal:** 用一个本机软件入口完成真实问答、完整实验和结果复核，正式实验与软件共用内核。

**Architecture:** 浏览器与CLI调用同一ApplicationService；应用服务管理资源、凭据和持久后台任务；独立worker调用研究内核，预测封存后由隔离评价进程评分。外壳与方法线从合同起并行，首题和冻结前必须集成。

**Tech Stack:** Python 3.12、FastAPI／Uvicorn、HTML／CSS／JavaScript、文件清单／JSONL／原子状态写入／文件锁；PyMuPDF与本地MiniLM沿现有资源管理路径。新增依赖只在实现时安装、固定和验证，不照抄旧Linux锁宣称macOS可用。

**Spec:** [软件与内核接口设计](../../SOFTWARE_KERNEL_DESIGN_20261024.md)。研究方法与规模来源：[完整流程缩量方案](../../FORMAL_EXPERIMENT_PLAN_SCALED_20261023.md)。执行者必须同时阅读。

## Global Constraints

- 新问题为开发50、验证50、主200、诊断50；辅助S50取自主200；两检索器E1—E4及全部九项辅助实验保持原样。
- E2—E4及辅助生成均3轮；正式与旧E1确定性运行一次。核心4,500＋辅助2,100＝6,600次逻辑生成，不将重复轮次当独立问题。
- 新阶段开发／验证最多500，余量最多900，总尝试上限8,000；所有真实兼容检查、演示、失败和重试均计入对应额度。保留旧185次账本。
- 原UEM、旧指标、四主对比、来源簇10,000次bootstrap、98.75%区间、人工核验及启动门槛均不变。
- 原`src/`、旧执行器／多模态构建器、旧数据和结果冻结；新代码另置。当前无Git仓库，不初始化Git或执行commit／push；用里程碑SHA与验证记录留痕。
- 课程数据、权重、视频、密钥和完整含数据请求不进≤50MB提交ZIP；旧私有工程包不能直接提交。
- 软件测试返回明确标记synthetic；离线重评分明确标记verify；均不伪装真实生成或正式实验成绩。
- 界面不持有测试金标；测试推理封存前不开放测试逐题成绩；看过对应QA的人不得承担该测试描述盲核验。
- 本轮授权范围是确定接口、分工与实施计划。以下清单是后续执行任务，不表示已开始。

## 1. 工作包、文件归属与依赖

| 任务 | 主责 | 依赖 | 可独立验收的交付 |
|---|---|---|---|
| T1 合同与完整计划编译 | A协调，B／C核对 | 无 | 全部条件可编译，三种用途与正式配置不可混淆 |
| T2 应用服务与持久任务 | A | T1 | 使用确定性测试内核跑通状态流、中断与恢复 |
| T3 正式数据与研究方法 | B，独立人员盲审 | T1 | 按既定协议产生可冻结的共同证据、两检索器和四组输入 |
| T4 真实执行与账本 | B | T1；T3输入适配后接通 | 单题与完整矩阵使用同一runner，不重发、不漏组、不泄漏金标 |
| T5 界面与首题集成 | A | T2、T4最小链路 | 用户在界面配置并真实跑通一题，刷新后结果仍在 |
| T6 评价、统计与人工审核 | C | T1；读取T3／T4产物 | 封存后评分、分母正确、独立复核可追溯 |
| T7 冻结与完整实验 | A／B／C交叉核对 | T3、T4、T6；T5基本启动监控 | 相同内核完整执行既定正式方案并保留全部状态 |
| T8 交付与教师运行验收 | A／C | T5、T7 | 新目录复现、真实录屏、课程专用ZIP与资源链接 |

T2与T3／T4／T6可在T1后并行；T5尽早接入T4的最小真实功能，不等待全部新算法。外壳美化不进入正式实验关键路径。执行人员未指定，以角色交接，不能把本表当作真实贡献证据。

## T1. 定义合同并编译完整实验计划

**Files:** 新建`formal_src/__init__.py`、`formal_src/contracts.py`、`formal_src/run_plan.py`、`formal_config/formal-v1.json`、`tests/formal/test_run_plan.py`、`tests/formal/test_contracts.py`。

**Interfaces:** 消费设计第2节的固定研究合同与正式来源清单；产出第4节RunRequest、ResolvedRunSpec、RunSnapshot、RunEvent、AnswerRecord、RunResults，以及`compile_run_plan(profile: dict, cohorts: dict) -> dict`。profile先为draft，只允许编译和开发检查，实际测试清单／模型／资源未验证时禁止标frozen。

- [ ] 写合同边界用例：未知kind、缺失verify源运行、正式配置覆盖字段、在请求中塞金标／密钥、坏UUID均拒绝；所有状态和错误输出可JSON序列化。
- [ ] 写完整编译用例，使用人工生成的200个主题ID、50个诊断ID、50个主集内辅助ID，要求主／诊断不交叉、S50真包含于主集、每条件题数与轮次齐全。
- [ ] 固定条件枚举和调用算式；核心3,600／900；D／L／G／I各150，C0／C1各150，k额外600，关闭关联150，第二模型450；总6,600；本地正式／旧E1总1,000。任务ID含题目、检索器、条件、模型角色、轮次，实际请求复用另记。
- [ ] 实现合同校验与计划编译，运行上述用例；任意正式字段变化必须转custom或被拒绝，不静默补齐缺组。记录合同v1与文件SHA，交给A／B／C共同使用。

**Verification:** `python3 -m unittest discover -s tests/formal -p 'test_run_plan.py' -v`，再运行`test_contracts.py`。这些是待实现测试命令，不是当前通过记录。

## T2. 应用服务、资源登记与后台任务

**Files:** 新建`app/__init__.py`、`app/service.py`、`app/jobs.py`、`app/resources.py`、`app/api.py`、`requirements-app.txt`、`tests/app/test_jobs.py`、`tests/app/test_service.py`、`tests/app/test_resources.py`、`tests/app/fixtures.py`。

**Interfaces:** 精确实现设计第4.2节ApplicationService方法与HTTP映射；jobs调用第4.3节runner签名。测试fixture提供相同接口的确定性worker，只写synthetic状态与记录，不导入真实provider、不读取密钥。

- [ ] 先用确定性worker验证完整状态流：登记任务后迅速返回run_id，逐步追加事件，刷新用after_seq恢复；重复client_request_id只生成一个任务。
- [ ] 实现原子state与顺序events、文件锁、单执行槽、后台进程启动／心跳／存活检查。向pause／cancel发送指令后，在途处理未结束时不能提前显示已暂停／取消。
- [ ] 实现会话内凭据引用和环境传递；重启后引用失效、要求重填；扫描测试输出／argv／日志，确认不存在特定测试密钥。服务只绑定本机并校验变更请求会话与来源。
- [ ] 实现资源目录登记、SHA／相对逻辑路径检查、审核清单导入和preflight阻塞原因。缺输入、缺模型、缺审核、冻结不匹配分别可见；verify不要求API凭据。
- [ ] 模拟进程在发送前、发送后未落结果、已落结果三处中断：前者可续发，中者unknown并阻塞，后者不重发。已完成记录或资产被改则拒绝恢复。用HTTP适配器运行同样边界用例。

**Verification:** `python3 -m unittest discover -s tests/app -p 'test_*.py' -v`。需要FastAPI的测试环境按本任务新增锁安装；不将缺依赖跳过伪报通过。

## T3. 补齐新数据、证据与研究方法

**Files:** 新建`formal_src/data_protocol.py`、`formal_src/evidence.py`、`formal_src/structure.py`、`formal_src/retrieval.py`、`formal_src/baseline.py`；测试分别为`tests/formal/test_data_protocol.py`、`test_evidence.py`、`test_structure.py`、`test_retrieval.py`、`test_baseline.py`。

**Interfaces:** 消费输入资源包和开发／验证清单；产出不含gold的共同证据、来源分组、裁图SHA、关联图、问题盲结构、冻结检索与AnswerRecord。具体方法及超预算／范围资格规则完全使用研究方案第3—5节。

- [ ] 用可追踪的小材料先验证报告／重复页跨集合拒绝、每页≤3／每来源≤20、主与诊断不重复、输入包不含金标。按既定来源池和种子准备50／50／200／50，保留候选资格和替换原因；不依据模型成绩筛题。
- [ ] 实现统一段落续接／脚注一次扩展、原子证据到裁图与文字的共同范围；分别输出种子与扩展后的覆盖。构造“脚注在相邻单元”的用例，要求四组共同收到或共同缺少。
- [ ] 实现分窗MiniLM与k=1／2／4。用答案线索位于长块末尾的人工材料检查分窗有效且不静默截断；核对固定模型字节和CPU环境。BM25 finance／simple参数表保留。
- [ ] 实现正式E1并保留旧E1参照。开发规则和阈值只用开发／验证；歧义用例须弃答，不逐题硬编码。
- [ ] 实现V2短节点和表头／行标签／单位／脚注关系投影；测试生成接口不接受问题／答案，关系不能引用未选择节点，结构失败保留样本。组织合格人员问题盲核验，达到原观察正确率≥95%、验证表格关系覆盖≥60%门槛。
- [ ] 通过全部输入范围和审核门槛后，向资源服务交接真实清单、SHA和待办状态；未通过则交接blocked原因，不能用测试夹具代替正式资源。

**Verification:** `python3 -m unittest discover -s tests/formal -p 'test_*.py' -v`；实际资源、盲审与模型环境另留证据，单元测试不等于人工核验完成。

## T4. 统一真实runner、全部条件与新账本

**Files:** 新建`formal_src/provider.py`、`formal_src/runner.py`、`scripts/run_formal.py`、`tests/formal/test_runner.py`、`tests/formal/test_provider.py`。

**Interfaces:** 实现设计第4.3节`run(spec, emit, control, transport=None)`；消费compile_run_plan结果和冻结共同证据；产出AnswerRecord、尝试事件、封存预测及完成清单。provider与次数账本独立于旧200次执行器。

- [ ] 用确定性transport验证完整6,600逻辑单元及所有本地单元有且仅有一个最终状态；k=2同轮核心输出作为辅助基线，不重复生成；同轮精确payload复用留来源，不跨轮缓存。
- [ ] 实现原协议的模型／schema／上下文／usage检查及交错顺序；保留E2文字、E3同范围图像、E4关系和全部辅助变体。C0、C1、I的输入内容逐项核对，不能混入不属于该条件的文字。
- [ ] 新预算在发送前持久预留，开发、验证、真实兼容检查、演示、重试都计数；8,000处停止派发，未知费率不伪填费用。模拟第8,001次派发被拒绝以及多进程争用不突破限制。
- [ ] 实现传输／服务错误最多一次重试、合法弃答不重试、截断不提高上限重试、滚动阈值暂停；模拟未知发送结果时阻塞并交任务管理器处理。
- [ ] 用专门金标sentinel测试从输入到provider payload都不出现评价答案，推理进程也不取得金标路径。预测封存后才发出允许评价的事件。
- [ ] 在开发额度内验证M1文本／图片与M2接口兼容，保留响应标识。M2不兼容时报告阻塞，不能静默删除第二模型或测试后挑替代模型。完成首题能力后立即交T5接入，不等T3全部新方法完工。

**Verification:** 针对`test_runner.py`和`test_provider.py`运行unittest；真实服务检查与确定性测试结果分开记录，不把模拟响应算正式成绩。

## T5. 配置界面、单题演示与第一次真实集成

**Files:** 新建`web/index.html`、`web/app.js`、`web/styles.css`、`scripts/course_entry.py`、`tests/app/test_entry.py`；完善T2的HTTP适配，不改旧CLI。

**Interfaces:** 默认入口启动serve；execute／verify同样调用ApplicationService。网页只消费设计第4.2节接口，条件与指标由服务端返回，不在前端重新计算研究结果。

- [ ] 完成配置与资源区域：填写服务／模型／会话密钥、登记目录、查看缺项、触发开发兼容检查；开始按钮依据preflight就绪状态显示真实待办，不以默认参数假装就绪。
- [ ] 用旧开发材料通过同一service／runner完成BM25＋E1，再在开发／演示额度内完成真实E2；显示原文／裁图、答案、scale、引用、耗时与live来源。新方法未接通前明确标预实验版本。
- [ ] 增加实验页面的固定正式预设、自定义副本、进度、暂停／恢复／取消；刷新后从run_id恢复，第二个生成任务不能抢占现有任务。
- [ ] 增加结果页面，按summary和逐题接口分主／诊断／辅助／历史／自定义展示；未封存测试不显示成绩，未完成任务不显示全量准确率。verify标记为结果复核，不显示新调用。
- [ ] 在浏览器实际操作填写配置→开始一题→查看引用→刷新→恢复结果；再用同一人工transport通过UI与CLI比较语义一致的ResolvedRunSpec／AnswerRecord／summary，忽略运行ID和时间字段。截图只记录真正操作结果。

**Verification:** `python3 -m unittest discover -s tests/app -p 'test_entry.py' -v`；另记录真实浏览器验收路径。不能只因API测试通过就宣称界面可用。

## T6. 评价、来源簇统计与人工审核

**Files:** 新建`formal_src/evaluation.py`、`formal_src/statistics.py`、`formal_src/audit.py`、`tests/acceptance/test_evaluation.py`、`test_statistics.py`、`test_audit.py`。

**Interfaces:** 消费封存预测、隔离评价包和预先确定的人工审核子集；产出设计第4.1节RunResults。runner在scoring／statistics阶段调用这些模块，app只展示它们的输出。

- [ ] 先用单位、负号、货币空格、百分号、量级冲突和文本关键字的人工用例固定UEM；旧EM／F1与数值诊断并列输出，不重写旧实验分数。
- [ ] 验证弃答／最终技术失败进入全样本分母、未运行任务不伪装0分、主200与诊断50分表；部分运行只能出进度与部分结果，不能出完整主表。
- [ ] 实现同题三轮平均、按来源成对重采样、四对比98.75%与95%区间。用两个极端来源簇的人工数据检查不能把三轮误作三倍独立题、不能逐题独立重采样；记录统计seed。
- [ ] 从冻结清单生成主S30／诊断15／关系30区域审核包，遮蔽规定信息；记录25%随机复核与全部争议处理。人工审核状态单列，不以自动引用编号检查替代语义支持。
- [ ] 同一封存预测经UI和CLI两种verify入口重算，要求主表、分母、区间和来源SHA一致；verify使用拒绝一切网络的测试transport证明没有模型调用。

**Verification:** `python3 -m unittest discover -s tests/acceptance -p 'test_*.py' -v`，独立审核与真实重算另记录；学生承担正式结论解释和课程写作。

## T7. 冻结、完整运行与封存

**Files:** 完成`formal_config/formal-v1.json`；新产物位于`artifacts/formal-v1/`，不覆盖旧目录。冻结前收集代码、资源、模型返回标识、提示、指标、子集、版本与SHA。

**Interfaces:** T2的freeze_profile／preflight／start_run调用T3／T4／T6，使用正式模式完整计划。

- [ ] A／B／C交叉核对全部研究门槛、真实可用数据量、完整矩阵和实际吞吐；以原方案约定处理诊断资格不足，提前明确报告，不在运行中按成绩调整数量。
- [ ] 检查正式profile无法通过UI修改，凭据与资源本机路径可重新绑定但内容与服务身份保持匹配；不满足则blocked。
- [ ] 启动同一内核，交错执行核心与全部辅助三轮，记录新账本与所有状态；监控只看技术状态，不根据测试分数改方法。
- [ ] 全部推理封存后才启动C的评分、统计和人工审核；审核未结束时明确显示未结束。独立复核主表并登记报告关键数字的run_id／文件出处。

**Verification:** 原协议的全量完成性、来源隔离、费用／usage、失败状态、独立重算与审核记录。此任务会产生真实调用；本轮编制计划未执行它。

## T8. 课程交付与新环境验收

**Files:** 新建`formal_src/course_export.py`、`docs/COURSE_SUBMISSION_MANIFEST.json`、`tests/acceptance/test_course_export.py`；更新README的正式启动与复现说明。使用独立课程清单，不修改旧私有工程快照用途。

**Interfaces:** ApplicationService.export_course_bundle调用course_export；结果从RunResults和资源索引生成，不重新向模型请求。

- [ ] 导出聚合主表、配置与资源SHA；剔除数据、PDF／裁图、完整含数据请求、金标、模型、视频、环境和密钥，保持单ZIP≤50MB。API Key测试sentinel和资源扩展名双重核对。
- [ ] README说明环境安装、一个启动入口、预期输出、seed、数据／权重获取、界面配置、单题真实运行、全量execute及无API verify的区别和所需费用／时间。
- [ ] 在新输出目录和干净Python 3.12环境，通过界面真实跑一题并重算封存结果；验证原绝对路径不可用时重新登记资源可运行。实际支持平台按结果声明。
- [ ] 录制≤2分钟真实系统与至少一个难例；学生完成8—12页报告、≤300字摘要、AI与个人贡献声明，数据数字与日志对应。课程文件姓名、学号、贡献和账单由真实资料填写，不编造。
- [ ] 核查外链可访问、包结构和敏感信息；最终提交由实际组长执行，本计划不授权自动上传、发布或发送消息。

**Verification:** 课程专用包检查、浏览器实际录屏、新目录／干净环境验证均留下证据；测试通过只说明相应技术边界，不替代教师课程验收。

## 2. 四个必须交接的检查点

| 检查点 | A提供 | B／C提供 | 通过条件 |
|---|---|---|---|
| 合同 | service／HTTP／任务状态定义 | 全矩阵编译与结果字段 | 同一合同可被双方消费，正式配置不能缺组减轮 |
| 首题 | 真实界面与持久任务 | 旧开发题的真实规则／模型链路 | 点击后有可追查真实结果，刷新不丢失 |
| 冻结 | 固定预设、资源与阻塞展示 | 输入资格、算法、评价与服务全部门槛 | 实际SHA封存，测试答案隔离 |
| 最终结果 | 展示、verify、课程导出 | 完整预测、统计及人工审核 | 关键数字同源、条件齐全、可在新目录验证 |

## 3. 排期与执行记录

10月24日完成本设计和计划；25日优先首题／方法开发／验证，原25日18:00冻结目标保留；正式生成仍以27日前完成为目标；28日分析，29日复现／录屏／交付核查，30日20:00内部提交目标。达不到门槛时报告进度与关键阻塞，不通过减少既定实验或跳过审核制造按时完成。

每个工作包完成后，在`artifacts/software-kernel-implementation/`记录改动文件SHA、验证命令与实际输出、测试返回或真实调用的类别、未完成事项，再交接下一个工作包。实现测试按上述边界新增；只有新改动或失败需要时才重复扩大检查，不添加镜像实现的无效测试。

下一项是T1合同与完整计划编译。两条线的分工与接口已在文档明确，但本轮没有创建这些源码、安装依赖、调度任务或运行实验。
