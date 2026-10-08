# OpenNeuro 批量下载与续传

通过 Apptainer/Singularity 容器，用 Deno OpenNeuro CLI 获取仓库，再由 DataLad 补齐数据。校验完成后，把符号链接复制为独立文件并删除 `.git`。中断后重新执行相同命令，即可从保存的阶段继续。

## 文件与环境

| 文件 | 用途 |
| --- | --- |
| `download-openneuro.sh` | 使用入口，处理参数并启动容器 |
| `download_openneuro.py` | 下载、恢复、校验和文件转换 |
| `test_download_openneuro.py` | 容器内合成数据测试，不下载真实数据集 |

在 **Linux 服务器**上运行，宿主机需要 Bash 和 Apptainer 或 Singularity。脚本优先使用 Apptainer。可只把 Bash 入口放到服务器。若同目录缺少 `download_openneuro.py`，首次运行会从本仓库 GitHub `main` 分支下载；本地已存在时直接使用，不自动覆盖或更新。也可以手动将两个脚本放在同一目录；Windows 目录 `D:\workspace\scripts\openneuro` 用于存放代码，下面的命令应在 Linux 执行。

默认容器为 `/home/container/download-tools.sif`，其中需要 Deno、DataLad、Git、git-annex、Python 3.11 或以上版本，以及 `git-annex-remote-openneuro`。宿主机无需安装 Python 或 DataLad。脚本直接调用容器 PATH 中已安装的 `openneuro`（Deno 安装的启动器），不固定或自动升级 CLI 版本。启动时执行 `openneuro --version` 并显示实际版本；容器更换 CLI 后，会重新导入对应的 Deno 缓存。对于 Deno 安装生成的启动器，还会把配置、`deno.lock` 和相关依赖复制到可写缓存，并让下载命令与 annex 后端使用同一启动器，避免向只读 SIF 写入锁文件。CLI 版本与数据集版本号无关。缺少 `openneuro` 时会明确报错。

## 使用示例

在脚本所在目录执行：

```bash
# 最新发布快照
bash download-openneuro.sh ds002721

# 按顺序批量下载
bash download-openneuro.sh ds002721 ds003505

# 指定版本
bash download-openneuro.sh ds002721v1.0.3

# 指定输出目录、单个数据集的下载并发数；可混合版本形式
bash download-openneuro.sh \
  -o /home/lapluis/workspace/dataset-collection \
  -j 4 \
  ds002721v1.0.3 ds003505
```

中断后，在相同输出目录重新执行原命令。无须添加续传选项；可以调整 `-j`。

```bash
# 第一次运行，中途断网或 Ctrl+C
bash download-openneuro.sh -o /data/openneuro ds002721

# 恢复同一任务
bash download-openneuro.sh -o /data/openneuro ds002721
```

输入是 `ds` 加六位数字，可附加 `v主版本.次版本.修订号`。多个 ID 用空格分隔，同一次调用中的重复输入只处理一次。脚本检查格式，OpenNeuro 检查数据集和版本是否存在。

## 自动获取 Python 脚本

本地缺少 `download_openneuro.py` 时，Bash 入口从以下地址下载并保存到自身所在目录：

<https://raw.githubusercontent.com/Omni-NCC/scripts/main/openneuro/download_openneuro.py>

下载使用容器中的 `curl`，宿主机无需安装 curl 或 Python。脚本目录须可写，容器须能访问 GitHub。下载成功且通过 Python 语法检查后才安装文件；下载失败、空文件或语法错误会停止运行并清理临时文件。已有文件不会被自动更新。GitHub 上可下载的是已推送到 `main` 的版本，本地未推送的改动不会包含在内。
## 参数

```text
bash download-openneuro.sh [-o OUTPUT] [-j JOBS] ID [ID ...]
```

所有选项放在第一个 ID 前面。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-o` / `--output` | 调用时的当前目录 | 输出目录，不存在时创建 |
| `-j` / `--jobs` | `4` | DataLad 下载并发数，必须为正整数；数据集之间仍顺序执行 |
| `-h` / `--help` | — | 显示帮助 |

替换容器路径：

```bash
OPENNEURO_CONTAINER=/path/to/download-tools.sif \
  bash download-openneuro.sh -o /data/openneuro ds002721
```

脚本目录和输出目录不能含逗号或冒号，含空格的路径须加引号。默认连接 `https://openneuro.org`；若设置 `OPENNEURO_URL`，恢复时须保持同一地址。需要认证时，通过 `OPENNEURO_API_KEY` 环境变量提供密钥，版本查询和 CLI 均可使用它；不要把密钥写进脚本或提交到 Git。

## 版本与输出

| 输入 | 下载版本 | 输出目录名 |
| --- | --- | --- |
| `ds002721` | 首次成功查询到的最新发布快照 | `ds002721/` |
| `ds002721v1.0.3` | `1.0.3` | `ds002721v1.0.3/` |

对于 `latest`，脚本先通过 OpenNeuro API 查询并保存实际版本，再开始下载。此后恢复仍使用已保存的版本，即使服务器发布了更新版本。没有正式版本快照时会报错，不自动转为 draft。

所有下载都传入明确版本。例如，在工作目录内执行：

```bash
openneuro download --version 1.0.3 ds002721 ds002721
```

完成后将内部 `ds002721` 目录移到最终位置。脚本校验版本标签与 HEAD，并保存提交号；后续恢复会检查提交号没有改变。

```text
输出目录/
├── .openneuro-deno-cache/          # 可写的 Deno 缓存
├── .openneuro-work/
│   └── ds002721v1.0.3/
│       ├── lock                   # 防止同一任务同时运行
│       ├── state.json             # 阶段、实际版本、提交号和任务标识
│       ├── manifest.json          # 数据路径、大小和 SHA-256 清单
│       └── ds002721/              # 处理中存在，成功后移到最终位置
└── ds002721v1.0.3/
    ├── .openneuro-download.json    # 下载完成凭据和版本记录
    └── ...                        # 实际数据文件，无 .git
```

工作目录可能另外含有复制缓冲目录和临时状态文件。成功后保留状态和清单，供再次运行时识别已完成任务；缓存也会保留。日志中的 `/downloads` 对应宿主机的输出目录。

## 恢复行为

| 中断阶段 | 再次运行时的行为 |
| --- | --- |
| 查询版本 | 重试查询；保存成功后固定版本 |
| Deno 下载仓库 | 在相同目录重试固定版本的 CLI 命令，利用 CLI 对已有仓库的处理 |
| DataLad 下载数据 | 跳过 Deno，重新运行 `datalad get`，复用已完成的 annex 对象 |
| annex 校验 | 重试校验；校验命令失败后，下次先重新运行 `get`，补回被隔离的损坏对象 |
| 链接实体化 | 校验已转换文件，继续复制剩余链接，不再下载数据 |
| 删除 `.git` | 依据已保存清单验证实体文件，再继续清理；支持 `.git` 已部分或全部删除的情况 |
| 移到最终目录 | 继续移动，或通过完成凭据识别上次已完成的移动 |
| 已完成 | 检查完成凭据，跳过下载；不会自动更新到新版本，也不会重新执行全量哈希检查 |

这是**文件级恢复**。单个文件是否支持 HTTP Range 续传取决于容器实际安装的 CLI 及其传输后端；脚本不保证字节级续传，下载到一半的文件可能从头重下。仓库克隆若留下 CLI 无法处理的损坏状态，仍可能需要人工排查；脚本会保留现场。

旧版脚本留下的 `.ID.partial-随机字符/` 没有恢复状态，**不会自动接管**。它们不会被本脚本删除。

## 校验、空间与保护

处理顺序为：获取仓库 → DataLad 补全 → `git annex fsck --numcopies=1` → 保存 SHA-256 清单 → 链接复制为实际文件 → 校验 → 删除 `.git` → 移到最终目录。

复制出的文件彼此独立，不是硬链接。删除 `.git` 前会检查所有数据文件的路径、大小和哈希。恢复实体化时，如果已经转换的文件被改动，会停止处理，不删除 `.git`。完整性检查会多次读取数据，大数据集的校验和转换需要时间。

实体化期间，annex 对象和最终文件同时占用空间。预留“annex 对象大小 + 最终文件大小”，另加仓库、缓存和临时文件空间；常见情况下接近最终数据大小的两倍。

- 没有匹配恢复记录的同名输出目录不会被覆盖。
- 同一输出目录中的同一 ID 有进程锁，重复启动会报错；锁文件保留不代表锁仍被占用。
- 某个数据集失败后保留恢复记录，继续处理其他 ID；Ctrl+C 中断整个任务。
- 全部成功时退出码为 `0`，有数据集失败时为 `1`；入口参数错误通常为 `2`。
- 要下载新的 latest，可指定新的输出目录；不要修改现有任务的状态文件来切换版本。
- 成功后保留 `.datalad`、`.gitattributes` 等原有文件，并新增 `.openneuro-download.json`；删除 `.git` 后，该目录不再支持 Git/DataLad 更新。
- 不支持 Git 子模块/DataLad 子数据集、嵌套仓库、目录符号链接和指向数据集外部的文件链接，遇到这些结构会报错。

## 测试

在 Linux 服务器的脚本目录运行：

```bash
apptainer exec \
  --bind "$PWD:/openneuro-scripts:ro" \
  /home/container/download-tools.sif \
  python3 -B /openneuro-scripts/test_download_openneuro.py
```

只有 Singularity 时，将 `apptainer` 替换为 `singularity`。

测试使用临时合成仓库，执行真实的 DataLad/git-annex 本地操作，覆盖版本固定、恢复阶段、实体化校验、已有输出保护和并发锁。OpenNeuro CLI 下载与 latest 查询由测试替代，因此不代表已验证真实数据集的完整网络下载。
