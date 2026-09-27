# 私有云部署包：单用户首版

状态：准备完成，尚未部署。需要已有 Linux Docker 主机、域名 DNS、访问账号。默认所有页面与附件都经 HTTPS 和网关账号密码保护。使用浏览器原生认证框，尚无多用户角色管理、SSO 或邀请流程。

服务边界：研究历史浏览、问题/笔记、任务状态和本地快照校验。云端禁止分析运行校验和任意分析执行；macOS Seatbelt 执行器不能直接当成 Linux 沙箱。没有股票账户连接或自动交易。

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

research/state 命名卷为持久数据；caddy_data 保存证书状态。单实例部署，不支持水平扩容。停止 app 后备份 research 和 state 卷到服务器之外的受控存储，然后启动 app；恢复到新卷并比较笔记数、记录哈希和快照校验。删除容器不会删除命名卷，但 `docker compose down -v` 会删除持久卷，不应用于升级。

尚未验证：Docker 镜像构建、远端 HTTPS/认证、持久卷权限与恢复。当前机器 Docker daemon 不可用，Docker Compose 子命令也不可用，不能把配置文件检查或 Python 测试当成上线验收。源页面与操作路由已有本地测试，新增 HTTPS origin 和云端禁执行测试。

认证参考：https://caddyserver.com/docs/caddyfile/directives/basic_auth 。Caddy 要求配置密码哈希；Basic Auth 必须搭配 HTTPS。
