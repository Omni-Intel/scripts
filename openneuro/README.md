# OpenNeuro 批量下载

使用 Apptainer/Singularity 容器，通过 Deno OpenNeuro CLI 获取数据集仓库，再由 DataLad 下载数据内容。校验完成后，将文件符号链接转换为独立的实际文件，并删除 `.git`，输出可直接读取的普通数据目录。

## 文件与环境

| 文件 | 用途 |
| --- | --- |
| `download-openneuro.sh` | 使用入口，处理参数并启动容器 |
| `download_openneuro.py` | 容器内执行下载、校验和文件转换 |
| `test_download_openneuro.py` | 使用临时合成数据测试，不下载真实数据集 |

在 **Linux 服务器**上运行，宿主机需要 Bash，以及 Apptainer 或 Singularity。脚本优先使用 Apptainer。

默认容器路径为 `/home/container/download-tools.sif`。容器须包含 Deno、DataLad、Git、git-annex、Python 3.11 或以上版本，以及 `git-annex-remote-openneuro`。现有容器已验证包含所需工具。OpenNeuro CLI 在脚本中固定为 `5.6.0`；这与数据集的版本号无关。

把两个下载脚本放在服务器的同一目录。Windows 上的 `D:\workspace\scripts\openneuro` 是脚本存放目录；下面的命令应在 Linux 服务器执行。脚本通过容器调用 Python，宿主机无需额外安装 Python 或 DataLad。

## 快速使用

在脚本所在目录执行：

```bash
# 下载最新发布快照，不是 draft
bash download-openneuro.sh ds002721

# 批量下载，按输入顺序逐个处理
bash download-openneuro.sh ds002721 ds003505

# 下载指定版本
bash download-openneuro.sh ds002721v1.0.3

# 混合指定版本和最新版本
bash download-openneuro.sh ds002721v1.0.3 ds003505

# 指定输出目录与单个数据集的下载并发数
bash download-openneuro.sh \
  -o /home/lapluis/workspace/dataset-collection \
  -j 4 \
  ds002721v1.0.3 ds003505
```

输入格式为 `ds` 加六位数字，可附加 `v主版本.次版本.修订号`。多个 ID 用空格分隔；所有选项放在第一个 ID 之前。脚本检查 ID 格式，数据集和版本是否存在由 OpenNeuro 返回结果决定。

## 参数

```text
bash download-openneuro.sh [-o OUTPUT] [-j JOBS] ID [ID ...]
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-o` / `--output` | 调用命令时的当前目录 | 输出目录，不存在时创建 |
| `-j` / `--jobs` | `4` | DataLad 下载并发数，必须为正整数；不是同时下载的数据集数量 |
| `-h` / `--help` | — | 显示帮助 |

通过环境变量替换容器路径：

```bash
OPENNEURO_CONTAINER=/path/to/download-tools.sif \
  bash download-openneuro.sh -o /data/openneuro ds002721
```

脚本目录和输出目录不能包含逗号或冒号，因为这些字符用于容器挂载参数。包含空格的路径需要使用引号。

## 版本与输出目录

| 输入 | 下载版本 | 输出目录名 |
| --- | --- | --- |
| `ds002721` | 下载时的最新发布快照 | `ds002721/` |
| `ds002721v1.0.3` | `1.0.3` | `ds002721v1.0.3/` |

指定版本时，脚本在独立临时目录中执行：

```bash
deno run -A jsr:@openneuro/cli@5.6.0 download --version 1.0.3 ds002721 ds002721
```

它还会检查下载仓库的 HEAD 是否对应所请求的版本标签。未指定版本时省略 `--version`。最后将临时目录内的 `ds002721` 移为输出目录中的 `ds002721v1.0.3`。

成功后的输出结构示例：

```text
输出目录/
├── .openneuro-deno-cache/        # 可写的 Deno 缓存
├── ds002721v1.0.3/              # 实际数据文件，无 .git
└── ds003505/                    # 实际数据文件，无 .git
```

日志中的 `/downloads` 是容器内路径，对应宿主机的输出目录。

## 下载和校验流程

1. 在输出目录下建立独立临时目录 `.ID.partial-随机字符/`。
2. 使用 Deno OpenNeuro CLI 下载仓库。
3. 执行 `datalad get -J JOBS .` 补全数据内容。
4. 执行 `git annex fsck --numcopies=1` 检查 annex 数据。
5. 检查文件链接，把链接内容复制为独立文件，并使用 SHA-256 校验复制结果。复制后的文件不是硬链接。
6. 确认数据目录内不再有符号链接，然后删除该临时仓库的 `.git`。
7. 将数据目录移到最终位置，输出 `COMPLETE`。

脚本保留 `.datalad`、`.gitattributes` 等数据集原有文件，只清理 `.git`。完成后的目录不再是 Git/DataLad 仓库，不能继续通过 `datalad get` 或 Git 更新版本。

实体化期间，annex 对象与复制出的文件会同时占用空间。应预留“annex 对象大小 + 最终普通文件大小”，另加仓库、缓存和临时文件空间；常见情况下接近最终数据大小的两倍。

## 重复运行与失败处理

- 已有同名输出目录时，脚本报错，不覆盖，也不检查或更新该目录。需要重新下载时，可选择另一个输出目录。
- 同一次调用中，完全相同的 ID 参数只处理一次。
- 某个数据集下载或校验失败后，脚本保留临时目录并打印位置，继续处理其他数据集。Ctrl+C 会中断整个任务。
- 全部成功时退出码为 `0`；有数据集失败时为 `1`；入口参数错误通常为 `2`。
- **当前不支持自动续传已有临时目录。** 再次调用会新建临时目录；旧目录保留供排查或人工恢复，会继续占用磁盘空间。
- 下载、annex 校验或链接转换失败时不会执行 `.git` 清理；若失败发生在清理或最终移动阶段，应根据日志检查临时目录状态。

当前脚本不支持 Git 子模块/DataLad 子数据集、嵌套仓库、目录符号链接或指向数据集外部的文件链接。发现这些结构会停止处理该数据集，避免输出不完整结果。

## 测试

在 Linux 服务器的脚本目录运行：

```bash
apptainer exec \
  --bind "$PWD:/openneuro-scripts:ro" \
  /home/container/download-tools.sif \
  python3 -B /openneuro-scripts/test_download_openneuro.py
```

只有 Singularity 时，将 `apptainer` 替换为 `singularity`。

测试覆盖版本参数传递、真实 DataLad/git-annex 本地流程、链接实体化、删除 `.git`、已有目录保护、异常链接拒绝，以及下载失败时保留仓库。Deno 下载步骤使用本地合成仓库替代，因此测试通过不代表已验证 OpenNeuro 网络访问或真实数据集下载。
