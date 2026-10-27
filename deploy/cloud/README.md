# 私有云部署包：单用户首版

状态：准备完成，尚未部署。需要已有 Linux Docker 主机、域名 DNS、访问账号。默认所有页面与附件都经 HTTPS 和网关账号密码保护。使用浏览器原生认证框，尚无多用户角色管理、SSO 或邀请流程。

服务边界：研究历史浏览、问题/笔记、市场临床登记与官方披露查询、持久采集队列、可校验CSV导出、任务状态和本地快照校验。云端禁止分析运行校验和任意分析执行；macOS Seatbelt 执行器不能直接当成 Linux 沙箱。没有股票账户连接或自动交易。

## 部署

1. 将源码传到选定服务器，不要包含原始研究数据、API 密钥或未审查的笔记。Docker 构建上下文仅允许源码及依赖锁。
2. 在此目录创建 `.env`，填 `DOMAIN=你的完整域名`，DNS 指向服务器。不要带 https 或路径。
3. 创建 `secrets/access.caddy`（不要提交 Git）。使用 `docker run --rm -it caddy:2.10.2 caddy hash-password` 交互生成密码哈希，不把明文密码写入命令历史。文件格式：

```
basic_auth {
    你的账号 交互生成的密码哈希
}
```

4. `docker compose --env-file .env config --quiet` 验证配置，再执行 `docker compose --env-file .env up -d --build`。
5. 验收未认证访问应返回 401；正确账号可访问；伪造 Origin 的写入应拒绝；保存笔记、重启 app 后记录仍在；后台快照校验可完成。部署完成后固定实际镜像 digest，并记录依赖锁及发布源码哈希。

仅 gateway 发布 80/443。app 8080 不映射到主机，backend 为内部网络。不得直接将 app 暴露公网绕过网关。账号保护依赖完整部署配置，不是 app 独立实现的认证。

## 记录迁移

云端初始为空，不能假装记录已迁移。确认目的地后，仅迁移经审查的研究快照包、历史报告及所需笔记。研究快照用原有 portfolio restore 与记录的 SHA-256 恢复到 /workspace，state-dir 显式指定 /state。后台任务记录和笔记在 /state/workbench.sqlite3；同步导出在 /workspace/artifacts/workbench/history.json。完整迁移须检查记录数、哈希、引用链接及失败记录保留。不要直接上传活跃 SQLite 文件。

## 备份、运维和限制

research/state 命名卷为持久数据；caddy_data 保存证书状态。单实例部署，不支持水平扩容。备份前执行 `docker compose stop app worker`，等待两者退出，并确认没有其他 CLI、手动采集或维护进程写入这两个卷。app 与 worker 都是写者，只停止 app 不能得到一致备份。全部停写后，将 research 和 state 作为同一时间点的配套副本，备份到服务器之外的受控存储；保留 state 中的 workbench.sqlite3、market.sqlite3 及其配套文件，以及 research 中的 artifacts/market、研究快照和报告。不要直接复制运行中的 SQLite。备份完成后执行 `docker compose start app worker`。

恢复演练须在隔离环境的新卷中同时还原配套副本，核对笔记与任务数、记录哈希、原件及快照校验、失败记录和证据链接，再启动服务验证读取与继续执行。当前尚未实际完成新卷／外部主机上的备份恢复验收；已有容器重启持久性检查不能替代恢复演练。删除容器不会删除命名卷，但 `docker compose down -v` 会删除持久卷，不应用于升级。

本地 Docker 镜像构建、UID10001卷写入、SIGTERM与队列恢复、HTTP来源／CSRF和导出已通过隔离运行验收，详见 `docs/CONTAINER_ACCEPTANCE_2026-10-17.md`。本机使用 standalone docker-compose 命令。远端 HTTPS／认证网关、证书、真实客户账号、备份恢复和生产可达性仍未验收；本地容器通过不代表已上线。

认证参考：https://caddyserver.com/docs/caddyfile/directives/basic_auth 。Caddy 要求配置密码哈希；Basic Auth 必须搭配 HTTPS。

## 新增 UC 项目事件的备份范围

UC 扩展的 `programs.sqlite3` 使用独立的 [一致性备份与只读恢复流程](../../docs/PROGRAM_BACKUP_2026-10-27.md)。该流程只恢复项目事件，不覆盖上文 research/state 配套卷、市场数据库、身份配置或生产切换。恢复副本不派发任务、不复制凭据或 worker 锁，HTTP 写入关闭；既有署名仍是历史声明。不能因单数据库恢复成功而标记整云环境迁移完成。
