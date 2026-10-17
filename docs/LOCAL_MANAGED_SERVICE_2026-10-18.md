# 本地受管工作台 — 2026-10-18

此前本地工作台依附一个终端进程。2026-10-18 的现场检查确认原 PID 已不存在、原执行会话已不存在，且回环 HTTP 连接被拒绝；这不是一次观察超时。现已使用当前用户的 macOS LaunchAgent 启动工作台，让进程由服务管理器维护。

这是本地单用户研究工作区，不是公网部署或客户认证服务。服务器仍只监听 `127.0.0.1`。关闭终端不会直接停止服务，用户登录后自动启动，异常退出后由 launchd 重启。电脑关机、休眠或源站不可达期间没有实时采集保证。

## 安装与操作

需要已经存在的 Python 3.11 虚拟环境、工作区源码与本机状态目录。以下示例在仓库根目录执行，活动 SQLite 自动放在本机应用数据目录：

```sh
.venv/bin/python -S scripts/local_workbench_service.py install --workspace "$PWD" --python "$PWD/.venv/bin/python"
.venv/bin/python -S scripts/local_workbench_service.py status
.venv/bin/python -S scripts/local_workbench_service.py restart
.venv/bin/python -S scripts/local_workbench_service.py uninstall
```

已有迁移状态目录时用 `--state-dir` 显式指定，避免切换到一个空的状态库。建议将运行源码和状态均放在本机磁盘；外置盘断开后不能继续运行依赖其文件的服务。`--port` 可指定其他非特权端口。不要再并行启动同一工作区的终端服务。

服务名称固定为 `com.emerisbiotech.workbench`，仅写入当前用户的 `~/Library/LaunchAgents/`，不调用 sudo 或安装系统级服务。它以参数数组直接执行，不通过 shell 拼接；运行环境只提供固定 PATH、HOME 和禁止字节码写入的选项，不继承模型密钥。安装器拒绝不同配置的既有 LaunchAgent，拒绝抢占已有未受管进程的端口。

`status` 分别报告服务是否加载、PID、`configuration_matches`、`pid_owns_port` 和实际应用 HTTP 是否就绪。只有已加载参数、工作目录及 plist 路径与已安装配置相符、所报唯一 PID 实际持有指定端口，且 `/assets` 返回正确应用页面时，才报告 `http_ready=true`。存在 plist、另一个进程的正常 HTTP 或只有 `launchctl print` 的 PID 都不构成就绪。损坏／非对象 plist、模糊 PID、服务管理器读取失败均保守报告未就绪。

安装及状态检查拒绝活动 SQLite、WAL/SHM/journal 侧文件以及服务日志的符号链接、断链、硬链接和非普通文件；拒绝使用链接的状态目录或 LaunchAgent 路径。已安装 plist 采用有界读取，不能借畸形内容绕过受管实例识别。重启和卸载也先核对已加载实例，配置不一致时拒绝操作。标准输出与错误日志在选定的本机状态目录，文件权限受 `umask 077` 限制；日志和状态不上传公开 Git。

## 恢复与验收范围

受管服务启动后确认 PID 的父进程为 launchd，`/assets` 实际返回应用页面。持久市场队列继续使用既有所有权、心跳、租约和原子发布机制；退出不构成采集成功，未完成任务通过原有恢复流程处理，旧的失败尝试继续保留。

本轮补强只运行隔离工程测试；所有 launchctl、lsof、HTTP 调用均使用明确模拟，未安装、重启、卸载或探测实际服务。覆盖参数数组、干净环境、端口范围、外置盘与链接拒绝、重复安装、不覆盖其他 plist、不抢占端口、加载配置不一致、PID 与端口所有权、错误应用页面，以及 malformed plist 的 status/install/restart/uninstall 拒绝。先前受管启动的现场验证仍是独立检查点；本轮测试没有扩展为长期运行、自动恢复全过程、远端 HTTPS、客户认证或源站 SLA 通过。云端继续使用独立的 Compose/网关方案。

定向测试命令（现有本机运行环境，不加载用户 site 配置）：

```sh
"/Users/lijinming/Library/Application Support/VirtualBiotech/venv/bin/python" -S /tmp/emeris_ui_test_bootstrap.py -q tests/test_local_workbench_service.py
```

本轮结果：**87 passed，0.43 秒**。这是隔离契约与拒绝路径验收；测试不会调用实际服务管理器或修改运行中的服务。
