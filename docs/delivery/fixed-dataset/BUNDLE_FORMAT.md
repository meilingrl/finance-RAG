# 固定课程数据包

这个目录保存固定数据交付的来源与许可说明。数据包由已登记的 `formal-main-course` 和 `development-reviewed-v1` 生成；原 `data/`、运行工作区和未就绪的 full 候选不作为发布目录整体复制。

## 安装

在仓库根目录执行：

```sh
python scripts/setup_data.py
```

默认从 `configs/fixed_dataset.json` 指定的 HTTPS Release 地址下载，校验 ZIP SHA256 后安装。离线时执行：

```sh
python scripts/setup_data.py --archive /path/to/fixed-course-data-v1.zip
python scripts/setup_data.py --check
```

公开交付也支持多个按顺序排列的分片。分片模式最多使用 8 个并发请求，各片先核对字节数及 SHA256，再按配置顺序拼接为原 ZIP；之后仍须通过整个 ZIP 的 SHA256 和安全解压检查。网络中断或任一片不匹配会结束本次安装、清理临时分片并保留现有数据。

配置格式为：

```json
{
  "archive_bytes": 264275858,
  "archive_sha256": "完整 ZIP 的 SHA256",
  "archive_parts": [
    {"archive_url": "https://example.org/fixed-course-data-v1.zip.part0000", "bytes": 1048576, "sha256": "第一片的 SHA256"},
    {"archive_url": "https://example.org/fixed-course-data-v1.zip.part0001", "bytes": 1048576, "sha256": "第二片的 SHA256"}
  ]
}
```

上例省略其他既有配置字段及后续分片，仅展示分片结构。`archive_parts` 数组顺序就是拼接顺序，各片 `bytes` 之和须等于 `archive_bytes`。分片模式无需顶层 `archive_url`；未配置分片时，顶层 `archive_url` 仍为必填的单 ZIP HTTPS 地址。两种模式的离线安装都使用同一个完整 ZIP，不要求用户手工拼接。

安装位置为当前仓库的 `data/fixed-course-v1/`，其中 `formal/` 是固定课程资源根，`demo/` 是已审核开发演示资源根。所有资源清单均使用根内相对路径；换机器、换用户目录和换 checkout 路径不需要修改数据。安装器会完整核对现有文件，已通过的安装直接复用。有内容冲突时保留原资产并返回明确错误。

`--check` 只检查，不创建目录、不下载、不注册资源。Python 接口为：

```python
from app.datasets import ensure_fixed_dataset, inspect_fixed_dataset

status = ensure_fixed_dataset(project_root, archive_path=None, progress=None)
status = inspect_fixed_dataset(project_root)
```

返回值包含 `bundle_id`、`formal_root`、`demo_root`、`formal_relative_root`、`demo_relative_root`、`installed`、`ready`、`state`。绝对路径由传入的 `project_root` 计算；配置与 ZIP 内不保存作者绝对路径。`installed=True` 仅表示本次调用创建了安装；幂等复用时为 `False`。`inspect` 的缺失/冲突状态为 `ready=False`，包含可读的 `error`。安装失败抛出 `FixedDatasetError`。

进度回调接收字典，`stage` 为 `download`、`verify_archive`、`install`、`ready`。下载事件提供 `downloaded_bytes`、`total_bytes`；安装事件提供 `completed_files`、`total_files`。

## 归档结构与完整性

ZIP 只有 `bundle.json` 和 `objects/<sha256>` 普通文件。相同 SHA 的模型、PDF 等资产仅储存一次；安装时按清单恢复到每个资源的原相对位置，不在安装树创建链接。

`bundle.json` 的 `files` 白名单列出安装路径、SHA256 和字节数；两个资源根各自保留其登记 manifest 的 `resources.json`。安装器同时验证 ZIP SHA256、清单 SHA256、对象 SHA256、资源 manifest 绑定以及安装后的完整树。归档中不在白名单内的条目、重复路径、绝对路径、路径逃逸、Windows 路径和链接都会拒绝。所有内容在同盘临时目录核验后一次安装。

正式包保持 `execution_scope=main_only`、`review_policy=automatic_course`，不重写既有问答、证据、金标、分组与模型。其现有输入文件包含冻结的 development/validation/main 分组；运行器仍按 `main_only` 选择原固定主集，交付时不另抽样、不把全量候选变成正式包。

## 生成发布候选

```sh
python scripts/package_fixed_dataset.py
```

默认新建 `artifacts/dataset-release-20261027/`，包含 ZIP、ZIP SHA256 文件、冻结清单、待发布配置和完整性记录。已存在的输出目录会被拒绝，不在原地重打包。原登记资源每个文件的 SHA256 必须先匹配，作者数据中的其他文件不进入包。

要同时生成 1 MiB 分片，选择一个新的输出目录：

```sh
python scripts/package_fixed_dataset.py --output artifacts/new-release --part-bytes 1048576
```

分片存于新输出目录的 `parts/`，文件名为 `fixed-course-data-v1.zip.part0000` 等。生成的 `fixed_dataset.json` 使用 `archive_parts`，不填写整 ZIP 下载地址；`--archive-url` 参数在此模式中用作分片 URL 的文件名前缀。完整 ZIP 的内容和 SHA256 不因切片而改变，仍保留为离线安装资产。生成后的配置须由发布者核对、复制到仓库配置位置；打包脚本不会改写当前 `configs/fixed_dataset.json`。

新版本应选新的输出目录、bundle 标识和发布地址，并重新审核固定范围。生成操作不会上传、发布或覆盖源数据。发布后应验证配置中的实际公开地址，再以全新仓库执行安装与就绪检查。

来源与处理范围见 [ATTRIBUTION.md](ATTRIBUTION.md)。完整许可文本：[CC BY 4.0](CC-BY-4.0.txt)、[Apache 2.0](APACHE-2.0.txt)。
