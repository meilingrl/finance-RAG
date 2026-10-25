# 金融文档证据问答与实验软件

需要 Python 3.12。此包是源码与聚合结果；数据、PDF/OCR、金标、权重及视频另行提供，资源身份见 `resources.json`。实际冻结配置见 `runtime/configuration.json`。原始作者 TAT-DQA 数据保留 CC BY 4.0 归属。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python scripts/course_entry.py
```

打开 `http://127.0.0.1:8765`，在页面填写渠道完整 Responses 地址、模型名、会话密钥并登记资源目录。E1本地规则无需API；MiniLM需安装 `requirements-encoder-runtime.txt`，权重通过 `scripts/provision_formal_encoder.py` 按固定SHA准备。真实生成需要渠道可用及实际调用费用。

完整操作、资源获取、人工核验要求、结果工作区迁移、CLI与无API复核说明见 [使用说明](docs/APP_GUIDE.md)。当前代码使用 Unix `fcntl` 锁，Windows未验收。

```sh
.venv/bin/python scripts/course_entry.py --workspace /absolute/results verify --source-run RUN_ID
PYTHONPATH=src:scripts:tests:. .venv/bin/python -m unittest discover -s tests -t . -v
```

正式矩阵为6,600逻辑生成＋1,000本地基线，seed20261023；同题三轮先平均，来源簇bootstrap10,000次。软件测试中的合成传输只检验工程行为，不是实验成绩；完整实测结果与实际人工审核状态以本包聚合结果为准。
