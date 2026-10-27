# 验证记录

## 历史运行证据

已有 `artifacts/development-demo-repair/network-resume-tests.log` 末尾记录：

```text
Ran 14 tests in 0.152s

OK
```

该片段来自历史断网恢复验证，不是本次补录新执行结果。完整日志和封存运行文件留在本地。

## 本次入库验证

2026-10-27 对补录前最终源码树执行：

```sh
PYTHONPATH=src:scripts:tests:. .venv-app/bin/python -m unittest discover -s tests -t . -v
node --test tests/web/test_state.cjs tests/web/test_progress.cjs
git diff --check
```

- Python：342 项，340 通过，2 项跳过，耗时 242.857 秒。
- 前端：2 个测试文件通过，0 失败。
- Git 差异空白检查通过。
- 原有 3 条历史与待提交文件进行常见凭据模式扫描，未检出；本次未把 `.local-secrets/`、数据、权重、虚拟环境或完整运行目录加入仓库。模式扫描不是对所有潜在敏感内容的形式化证明。

这些结果针对最终源码树，不将补录的中间提交视为独立验证版本。测试使用模拟调用，不是新增模型实验或 Word/WPS、浏览器的人工验收。
