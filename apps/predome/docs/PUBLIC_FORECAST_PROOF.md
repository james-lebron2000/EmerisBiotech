# 公开临床资料包的独立时间凭证

公开资料包及凭证位于 `arena_data/public_proofs/clinical-20261023-v1/`。原始临床ZIP包含12项正式试验的48个模型任务记录，以及8项明确标记为历史回放的研究；每项均有临床JSON和可读报告。包内没有原始Prompt、Token、费用、内部评分、管理员信息或患者级私有数据。

## 凭证实际证明什么

FreeTSA签名的时间为 **2026-10-22 18:48:48 UTC，即台北时间2026-10-23 02:48:48**。OpenSSL分别核验资料包的SHA-256消息印记及原始查询nonce，两项均通过。

该凭证证明指定公开ZIP在签发时已经存在。它**不证明更早的原始模型生成时间**，不证明预测准确、专利增益、Agent增益或独立人工科学审核。原登记／运行时间凭证是另外的材料。历史回放不计前瞻命中。ZIP自身manifest生成早于本次外置凭证，因此其中 `independent_timestamp_attestation=false` 仍如实表示“ZIP内没有自带独立凭证”，不是对后来外置签名的否定。

- 临床ZIP SHA-256：`1a0144906b743e03c50b13b2c1e9554ec488381b88e4939d9d8ec53fa116825c`
- TSA响应 SHA-256：`958a61b460e0f642569df9cd2b61882e2dafc18796e88aae90d591a17fcd4ec1`
- 完整验签套件ZIP SHA-256：`321a3f2cd120d0fc467374faca36b6d53bd0634556de35e993cf978b31377ff6`

## 独立离线核验

从可信的仓库副本取得核验脚本，在`apps/predome`执行：

```sh
python scripts/public_snapshot_proof.py verify --directory /path/to/extracted-verification-kit
```

核验不连接TSA、不调用模型、不读取或写入Arena账本。它验证证书固定哈希、真实密码学签名、ZIP消息印记、查询nonce、签名时间、临床对象与报告逐字重建、完整文件清单，以及“未证明原始预测时间／未通过临床验证”的限定字段。改动凭证说明、伪造效度声明、替换报告、增加未声明文件或修改任一被签数据都会失败。

底层命令可单独运行：

```sh
openssl ts -verify -data clinical-records.zip -in timestamp.tsr -CAfile cacert.pem -untrusted tsa.crt
openssl ts -verify -queryfile request.tsq -in timestamp.tsr -CAfile cacert.pem -untrusted tsa.crt
openssl ts -reply -in timestamp.tsr -text
```

下载包附带的CA不能独自充当身份保证；需与可信源码中的固定哈希，或FreeTSA官方HTTPS证书文件核对。证书固定值沿用已核查的2026-10-22来源，签名仅作为研究记录的密码学时间证明。

## 发布方式与边界

发布工具为 `scripts/publish_public_proof.py`。它只使用已提交的三个操作脚本，并在本地及服务器独立验签后发布九个明确命名的公开文件。目标地址为：

`https://8.148.145.28/public-forecast/20261023-clinical-v1/verification-kit.zip`

该URL是否已经可用由外网下载及验签回执证明，不能仅凭本操作文档推断。原始API、内部调试、模型配置、权限及应用版本均不因资料发布而开放或改变。Nginx只为九个固定文件增加精确只读位置，其余该命名空间返回404；只做配置测试与热重载。部署备份、哈希前置条件及并发变化保护避免覆盖其他运维变更。

源码目录中的凭证文件可以离线复核；线上发布成功后，运营材料可分享上述套件URL，不再向公众发送已关闭的原始Arena账本接口。生成本凭证没有新模型调用、账本写入或临床结果裁定。
