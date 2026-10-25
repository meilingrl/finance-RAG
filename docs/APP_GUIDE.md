# 金融文档问答软件使用说明

本机浏览器与命令行使用同一应用服务、实验计划、执行器和评分器。首次启动需安装 Python 3.12 环境；此后 API、模型方案、资料位置与结果工作区在页面配置。

## 安装与启动

在本项目目录执行：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python scripts/course_entry.py
```

默认监听 `http://127.0.0.1:8765`，服务就绪后打开系统浏览器。自动化环境使用 `serve --no-browser`；端口可用 `serve --port 8766` 修改。Windows 未验收；当前代码的进程锁使用 Unix `fcntl`。

macOS arm64 的实际依赖闭包见 `requirements-runtime-macos-py312.txt`，包含可选编码器。仅外壳的干净环境锁为 `requirements-app-lock-macos-py312.txt`；跨平台安装首先使用上面的直接依赖清单。不要把旧 Linux 环境的锁当作本机安装证明。

## 资料与本地编码器

资料须来自 TAT-DQA，保留 CC BY 4.0 归属。候选包的 `resources.json` 记录相对路径与 SHA；登记时检查实际文件。数据、PDF/OCR、裁图、金标和权重均不在课程源码 ZIP 内。

新建候选资料包（公共网络下载，仅准备阶段；不是运行正式实验）：

```sh
.venv/bin/python scripts/prepare_formal.py --output data/formal-v1
.venv/bin/python scripts/provision_formal_encoder.py --output data/formal-v1
```

第一条命令的 curator 接触官方问答，只负责抽样、隔离金标与资料获取；随后另起只读文档/PDF/OCR的构建进程产生问题盲证据和结构草稿。必须按输出的缺项继续审核，不能把退出码0解释为正式就绪。已审核或冻结的资料拒绝重建，使用新候选目录；下载中断留下的 `.part` 文件须先检查，不会静默覆盖。

MiniLM 为 `all-MiniLM-L6-v2`，固定 revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`，11项文件逐一核对大小和 SHA。它随资源包登记，不在运行时从 Hub 下载。安装可选依赖：

```sh
.venv/bin/python -m pip install -r requirements-encoder-runtime.txt
```

Linux CPU 可先从官方 CPU index 安装 `torch==2.14.1+cpu`，再安装上面的清单；该平台编码器仍需独立烟雾验证。macOS 实测为 CPU / 22,713,216参数 / 384维，断网加载与合成句检索通过。依赖安装不属于 API 模型调用。

## 页面操作

页面按四个步骤分页：配置与资源 → 单题演示 → 正式实验 → 结果复核。顶部步骤栏显示每步当前状态，下方显示当前模型方案、资料和运行 ID；地址栏 `#config`、`#demo`、`#experiment`、`#results` 可直接打开对应步骤。结果工作区、审核记录导入和重建输入收在「配置与资源」的“高级”中。

1. 填完整 Responses 地址（以 `/v1/responses` 结尾）、渠道实际模型名和会话密钥，保存为自定义方案。密钥只在服务内存；重启服务需重新输入，浏览器不保存密钥。
2. 登记资源包目录。已有旧开发材料可用快捷按钮；正式候选包也只展示其开发问题，不向演示页面开放主测试问题或金标。
3. 选择问题、BM25或MiniLM、E1至E4，点击预检并开始。E1无需API；E2/E3/E4产生真实调用。裁图与结构未有合格盲审核记录时，在发送前阻止相应条件。
4. 结果显示答案、量级、引用、耗时、状态与实际尝试。规则不能唯一判断时弃答；接口失败与模型弃答分别记录。原文和同范围裁图可展开查看。
5. 模型连通检查使用合成 `READY` 文本，不是研究题成绩，也不代表图像/M2/吞吐已合格。失败、重试及兼容检查进入新账本；费用未知显示未知。
6. “保存结果目录”在下次启动生效；原输出保留原处。显式 CLI `--workspace` 优先。切换工作区后重新登记资料、选择方案或加载运行ID。

正式预设固定为开发50、验证50、主200、诊断50、S50；两检索器、E1至E4、三轮和全部辅助条件不变。编译结果为6,600个逻辑生成单元与1,000个本地基线单元。推理去重仍保留全部7,600条最终记录；来源簇bootstrap10,000次，四个主对比的区间为98.75%，同时报告95%。新阶段最多8,000次实际尝试，旧185次账本保留。

正式冻结需实际数据资格、人工范围/关系核验、预注册审核子集、开发/验证、两个模型图文兼容、吞吐与预算证据。页面导入真实审核记录后，显式采用所选模型方案检查冻结，再检查正式预设并启动。当前没有审核人时，这些门槛保持待办；软件测试不会替代它们。暂停/取消等待在途结果落盘，未知发送结果须检查，不自动重发。

## 命令行与无 API 复核

```sh
.venv/bin/python scripts/course_entry.py --workspace /absolute/result-workspace execute --request request.json
.venv/bin/python scripts/course_entry.py --workspace /absolute/result-workspace verify --source-run RUN_ID
```

`execute` 从环境变量 `FORMAL_API_KEY` 获取会话密钥，不把密钥写入请求JSON。请求使用登记后的 `resource_set_id` 和配置 `profile_id`；字段合同见 `formal_src/contracts.py`。常规使用直接在页面创建请求即可。

`verify` 不重新生成答案，不需要API密钥。完整实验在预测、任务清单与输入身份封存后才读取隔离评价包；复核拒绝改动的文件。单题演示只复核完整性，不声称测试集准确率。界面与CLI输出使用同一分母与统计实现。人工语义审核状态独立于自动评分。

迁移已有结果时，将外部提供的运行工作区完整复制到新位置（含 `runs/RUN_ID`），从软件选择该工作区并重启；登记同内容SHA的新资源目录，再加载原运行ID进行复核。不要编辑封存的 `spec.json`、`inputs.json` 或预测来替换旧机器路径；运行内部保留的旧路径字符串仅作身份依据，评价资料通过新登记的根目录定位。

## 课程导出

“导出课程专用包”要求完整正式运行、封存评分及实际人工审核全部完成，并提供真实报告、摘要、成员/贡献与AI说明。所需文件由缺项提示列出；软件不会编造姓名、学号、贡献或审核。最后提交由组长完成。

课程包≤50MB，包含源码、必要测试/配置、聚合结果与实际冻结配置身份；不含数据、金标、模型、完整请求、密钥、环境或视频。资源另行提供有SHA的可访问位置；仅有SHA索引不能代替实际外链。两分钟演示视频与8—12页报告由真实系统/实验产生后制作。

## 验证与范围

```sh
PYTHONPATH=src:scripts:tests:. .venv/bin/python -m unittest discover -s tests -t . -v
```

最小Linux容器运行完整测试需安装`procps`，用于检查进程参数是否泄漏凭据；软件启动本身不依赖该命令。软件用例中的合成传输和包装夹具明确不是研究证据。实际测试平台、浏览器运行ID、依赖与未完成项记录在 `artifacts/software-kernel-implementation/`；该私有工程记录不直接放进课程包。课程导出的运行配置与结果是教师复现的依据。
