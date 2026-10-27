# 依赖清单

从仓库根目录使用 `python -m pip install -r requirements/<文件名>`。

| 清单 | 用途 |
| --- | --- |
| `requirements-course.txt` | 固定课程实验完整依赖，首次安装入口（含网页与 MiniLM） |
| `requirements-app.txt` | 仅网页应用依赖，用于无需编码器的轻量工程检查 |
| `requirements-encoder-runtime.txt` | 可选 MiniLM 编码器直接依赖 |
| `requirements-app-lock-macos-py312.txt` | macOS Python 3.12 应用依赖锁 |
| `requirements-app-lock-linux-aarch64-py312.txt` | Linux aarch64 Python 3.12 应用依赖锁 |
| `requirements-runtime-macos-py312.txt` | macOS 含编码器的完整依赖闭包 |
| `requirements.txt` | 早期标准库核心依赖说明 |
| `requirements-optional.txt` | 早期 PDF 工具依赖 |
| `requirements-encoder.txt` | 早期 Linux x86_64 CPU 编码器环境锁 |

平台锁不是跨平台通用安装文件，不能将不同平台与不同时期的锁合并安装。详细步骤见[应用指南](../docs/guides/APP_GUIDE.md)。
