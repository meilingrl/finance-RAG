# 软件架构

网页和命令行共用 `ApplicationService`，通过同一资源登记、任务管理和实验内核执行；界面不另外实现评价算法。运行入口与异常处理见[应用指南](../guides/APP_GUIDE.md)。

| 模块 | 职责 |
| --- | --- |
| `scripts/course_entry.py` | 本地 HTTP 服务、CLI 执行与复核入口 |
| `web/`、`app/api.py` | 四场景工作台与应用 API |
| `app/service.py`、`app/resources.py` | 方案选择、预检、冻结、密钥引用与资源 SHA 校验 |
| `app/jobs.py`、`app/workspace.py` | 持久任务、工作进程、状态和输出工作区 |
| `formal_src/contracts.py`、`run_plan.py` | 请求/结果契约与实验矩阵编译 |
| `formal_src/retrieval.py`、`baseline.py`、`evidence.py`、`structure.py` | 检索、规则、证据关联与结构投影 |
| `formal_src/runner.py`、`provider.py`、`http_transport.py` | 真实执行、Responses 传输、尝试记录与封存 |
| `formal_src/retry_policy.py`、`recovery.py`、`budget.py` | 有界重试、未知请求处置与调用预算 |
| `formal_src/verification.py`、`evaluation.py`、`statistics.py` | 独立复核、隔离金标评分与来源簇统计 |
| `formal_src/course_export.py` | 课程白名单导出、结果和配置身份保存 |
| `src/ragpilot/` | 基础检索、规则和早期 CLI 能力，仍被现行内核复用 |

## 数据流与身份

资源目录通过 `resources.json` 登记，文件按相对路径和内容 SHA 绑定。输入投影仅包含推理需要的输入、模型和符合当前协议的结构；隔离金标由封存后的评价流程读取。正式运行冻结代码、方案和资源身份，不允许通过修改旧记录掩盖变化。

任务在工作区 `runs/<run_id>/` 保存 `spec.json`、状态、事件、`tasks.json`、预测和封存证明等。复核任务复制已封存预测并重新评分，不再次调用生成模型。单题演示的完整性复核与正式测试集评分有不同的结果标志。

BM25/E1 不需要神经网络权重；MiniLM 使用固定本地权重，CPU 加载，不在检索时自动下载。E2/E3/E4 需要授权生成服务。密钥通过会话内存或执行环境传入，不作为请求 JSON 或命令行参数保存。

## 协议与边界

`automatic_course` 执行自动校验并标明未人工审核；`human_reviewed` 要求相应审核记录。两者都不允许用任意文件替代 SHA 绑定的资源。完整配方与实际主集范围见[实验协议](../experiments/EXPERIMENT_PROTOCOL.md)。

本地服务默认绑定 `127.0.0.1`，工作进程锁依赖 Unix `fcntl`。当前不是云部署服务。测试中的合成传输用于验证工程行为，不代表生成模型成绩。具体已验证范围见[验证记录](../development/VALIDATION_RECORD.md)。
