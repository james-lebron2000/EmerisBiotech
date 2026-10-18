# 受控本地试用与源码部署包

本交付面向一名操作人员在本机回环地址使用临床资产研究工作台，查看公司、资产、来源证据及历史记录。交付物是固定 Git 提交的源码包，包含部署模板和依赖锁；不包含预构建镜像、Python 运行环境、依赖安装包、真实数据库、研究原始数据、既往生产记录或密钥。工程运行成功、来源核验、科学审查、人工签署和临床预测有效性仍分别报告；本包不证明二/三期预测能力或投资收益。

## 生成与验证交付物

维护者先将审核通过的源码、脚本、测试和文档提交到 Git，再以完整提交号构建。以下命令在源码仓库执行；将占位符替换为明确提交号。脚本只读 Git 对象，不联网、不安装依赖、不调用 Docker、不访问生产状态。

```sh
python3.11 scripts/build_local_trial_bundle.py \
  --commit <完整的已提交Git哈希> \
  --output-dir artifacts/releases/local-trial \
  --expected-vbt-modules 61
```

脚本拒绝 `HEAD`、分支名等可移动引用，也拒绝待打包目录中的已修改、已暂存或未跟踪源码。被忽略的运行产物不参与打包。若有未提交的公开 QA 脚本或文档，维护者须先确定其提交范围，不能把工作区文件冒充指定提交。可以从干净的较新工作区打包指定旧提交，实际内容始终取自指定提交的 Git blobs。

生成的三个文件共享 `EmerisBiotech-source-<提交前12位>` 名称前缀：

| 文件 | 内容 |
| --- | --- |
| `.tar.gz` | 白名单目录中的 tracked 文本源码、配置、测试、公开示例和文档 |
| `.manifest.json` | 完整提交号、构建器版本、61 个模块计数，以及包内每个文件的路径、字节数、Git 对象号、SHA-256、权限 |
| `.SHA256SUMS` | 压缩包及外置 manifest 的 SHA-256 |

manifest 是包外清单，覆盖压缩包内的每一个普通文件，不把自身放入压缩包。固定成员排序、权限、提交时间和 gzip 时间字段后，同一提交使用同一 Python/zlib 工具链可生成相同字节；跨压缩工具链应比较完整文件清单，不能仅凭提交号假定压缩字节一致。已有同名输出不会覆盖。

脚本只选择根目录的必要文件，以及 `src/`、`tests/`、`scripts/`、`docs/`、`examples/`、`deploy/`。`artifacts/`、`data/`、`private/`、数据库及 sidecar、`.env*`、密钥目录、虚拟环境、二进制数据与归档不进入包。源码符号链接、Git LFS 占位文件、非文本文件和可识别的私钥/API token 会阻止打包；这些规则是发布门禁，不能代替维护者对提交内容的凭据审查。公开示例必须保留其合成/历史回放标识。

接收方在新目录中核对三文件，再查看 manifest 中的提交和范围：

```sh
cd /path/to/received-bundle
shasum -a 256 -c EmerisBiotech-source-<提交前12位>.SHA256SUMS
tar -tzf EmerisBiotech-source-<提交前12位>.tar.gz
mkdir trial-extracted
tar -xzf EmerisBiotech-source-<提交前12位>.tar.gz -C trial-extracted
```

仅解压已核对来源和校验值的交付物，解压到空的新目录。SHA-256 检查传输后的字节一致性；预期清单/哈希仍须来自可信交付渠道，它不构成作者签名。

## 独立本地启动

准备 Python 3.11、`uv` 及适用于当前操作系统和架构的依赖。`uv sync --frozen` 按 `uv.lock` 安装；首次安装可能需要访问依赖仓库，或者由操作者提供可信依赖缓存。源码包本身不是离线依赖包。

在解压后的源码根目录执行。以下状态目录和端口独立于现有 VirtualBiotech 正式工作台：

```sh
cd /path/to/trial-extracted/EmerisBiotech-source-<提交前12位>
unset UV_PROJECT_ENVIRONMENT
uv sync --frozen --python 3.11
TRIAL_STATE="$HOME/Library/Application Support/EmerisBiotechTrial/<提交前12位>"
mkdir -p "$TRIAL_STATE"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/vbt \
  --workspace "$PWD" --state-dir "$TRIAL_STATE" \
  workbench --port 18765
```

在浏览器打开 `http://127.0.0.1:18765/assets`。程序只监听 `127.0.0.1`，请求 Host 与端口须匹配；使用上述地址。SQLite 状态必须位于本机磁盘，不能放在 `/Volumes`。工作区中的新记录及附件写入该解压目录的 `artifacts/`，不指向生产工作区；不要把真实生产数据库复制进试用包。

本地试用使用解压目录自己的 `.venv`。已有 `setup.sh` 和根目录 `vbt` 包装器默认使用共享的 `~/Library/Application Support/VirtualBiotech/venv`，因此上述独立试用流程直接使用 `.venv/bin/vbt`。不要为试用安装 `scripts/local_workbench_service.py` 的固定同名 LaunchAgent，它可能与当前用户已有的正式服务冲突。

新状态没有生产市场快照、真实 PDF 归档或生产研究历史；界面中的未知/尚未采集状态不能解释成没有管线。新状态未启用自动调度，也没有预存待办采集任务；启动工作台并不等于完成市场覆盖。若操作者主动启用采集或提交任务，会访问代码允许的公开 listing/registry/披露来源，并产生新的元数据与证据记录；研究数据下载仍由独立流程负责。模型 API 密钥不是本地 UI、历史记录和确定性工程测试的前提。

本地写操作同时检查精确 Origin 和进程生成的 CSRF token；重启后 token 会变化。它们用于阻挡跨站写入，不提供多用户认证或公网访问控制。不要将这个回环试用入口直接暴露到公网或随意加隧道。平台提供研究和记录功能，不连接证券经纪账户，不自动下单或交易。

停止时在前台终端按 `Ctrl+C`。若已经主动运行采集，先在界面关闭调度、取消或等待在途任务，再停止；中断任务不能按成功计数，下一次正常启动应按恢复流程重新核查。保留独立状态目录和工作区即可继续查看本次试用记录；需要全新试用时另建新目录，不清理或覆盖生产目录。

## 可部署模板的实际范围

`deploy/cloud/Dockerfile` 以 Python 3.11 和 uv 0.8.22 安装 `uv.lock` 的 frozen 依赖，使用非 root 用户。`compose.yaml` 是 HTTPS 云部署模板：应用位于内部网络的 8080，独立 worker 负责公开来源采集，Caddy 网关发布 80/443，并要求操作者配置域名和访问密码 secret。它不是上述回环试用的启动命令。`deploy/cloud/secrets/access.caddy`、`.env`、域名凭据和 TLS 私钥不在源码包中，必须由目标环境操作者另行配置。

当前 Dockerfile 的基础镜像及 Caddy 使用标签，apt 软件包源也未固定到快照；依赖锁不意味着镜像构建完全可复现。本轮不构建或验收新镜像。请按 `deploy/cloud/README.md` 在目标环境另行验证实际镜像摘要/架构、依赖安装、挂载权限、网关认证、Origin/CSRF、退出与恢复、证据下载以及采集网络边界，再决定是否发布。macOS 的分析执行沙箱不能视为 Linux 容器沙箱已经验收，不得增加无沙箱回退。

## 本轮交付验收

打包脚本的专属测试使用小型临时 Git 仓库，核验确定性归档、完整文件清单、Git 对象/文件哈希、61 模块检查、路径边界、未提交源码拒绝、运行文件排除、凭据/二进制门禁及禁止覆盖。它们不启动正式服务、不复制虚拟环境或真实数据库，也不证明真实管线复现或投资效果。

本轮基线提交 `94e6f1d0ac8620564f44a5f09595560425c39196` 有 61 个 `src/vbt/*.py` 模块；该数量也在实际 Git 对象只读测试中核对。新脚本和本文必须经维护者审核提交后，才能产生最终正式交付包。最终包的提交号、成员数、大小及哈希以生成的 manifest/SHA256SUMS 为准，不能以本文的基线描述代替发布验收。
