# Bridge contract 3 — 六节点机器契约

版本组合：protocol 2 / contract 3 / manifest 1 / parameter sync 2 / test mode 3；向量 fixtureVersion=2。节点目录由 node-catalog.json 与 Python NODE_CATALOG 对比；**输出顺序是契约**。握手必须核对六节点目录摘要，不能只看 contractVersion=3 就接受旧九节点客户端。

## 验证层次

1. UTF-8 严格 JSON：拒绝重复键、NaN/Infinity、非法 Unicode、不安全整数。
2. 使用自包含 Draft2020-12 schema 的具名定义：bridge.schema.json#/$defs/<name>。根对象没有默认实例类型。
3. manifest / parameters / parameter_sync / asset_registry / run_preparation 执行语义与权限校验；schema 不是授权，也不是连线正确的证明。
4. apiSha256 是原始 API 字节摘要；definitionSha256/preparedPromptSha256 为 RFC8785 JCS + SHA256。不能用普通排序 JSON 替换 JCS。

manifest / runtimeManifest 允许空 verifiedEnvironments，publishedManifest 不允许。日常 API 注册采用 runtime profile；发布证明不是本地执行门槛。开发环境观察不等于 Photoshop 支持矩阵。

日常本地工作流允许同 workflowVersion 更新内容；完整 feature scope 中的 definitionSha256 标识执行定义。登记相同定义可更新名称、API 排版与 UI，不覆盖已准备的运行。Vplugins 在点击运行时先读取选中的 API、重新绑定，再提交参数和 prepare；不预先试运行，也不要求手动发布、升版或清空草稿。

Bridge 只校验自身节点、参数、图片/结果绑定、资源限制、身份与摘要。普通 ComfyUI 节点的动态输入、输入类型/范围及全图连线可执行性由 ComfyUI 在提交时校验；Bridge 不重建普通节点字段白名单，也不要求绑定参数必须通向 PS 输出。编辑后通过现有 draft 更新目标，导出期间变更自动重新导出，旧运行继续使用冻结快照。

## 六节点关键变更

- VP_Image 输出 RGB/ALPHA/MASK/width/height；唯一 main/ref1…ref5，界面 main/img2…img6。selection.nodeId 为 main VP_Image，missingPolicy 固定 full。
- Seed、Slider、Prompt、Batch 每类单例且单值，默认绑定 canonical seed/strength/prompt/count。Slider/Prompt 的 source_json 保留为内部参数映射字段，随工作流保存，不提供节点配置入口。
- Batch 为真正 INT1–4。业务 patches、prepare 和节点 execute 都验证；native /prompt 另在本插件 aiohttp middleware 中读取原始值，阻止核心预先把小数/字符串转换为 INT，不覆盖核心路由或 queue。
- Image 的 request_id 注入 runId，file_name/selection_file 清空；Send 只注入 request_id。旧多槽输入不能在 candidate 中被静默补齐。
- Send 必需 images（显示 RGB）、可选 alpha（显示 ALPHA），输出 RGB IMAGE / ALPHA MASK。缺少 alpha 优先 RGBA 内嵌透明度，否则全1；只允许单张广播，不拉伸。
- 可包括独立调试参数分支；VP 参数单例、绑定唯一性和数值规则保持不变。Batch 的默认 maxResults 为数量上限乘 Send 个数，仍受总64结果上限约束。

## 连接与资源预览

POST /clients 发放角色绑定凭证。HTTP 使用 X-PS-Bridge-Client-Id / X-PS-Bridge-Client-Token；WS hello 携带 role/clientToken。若配置共享访问 token，两种连接都必须附带。凭证不属于工作流。

prepare 成功后只向当前 scope 附着 editor 发 media.preview；POST /editor/preview 使用 editorPreviewRequest，包括 sessionId/serverEpoch/attachToken 及 payload.scope/runId/nodeId。服务端再次核验控制端归属、当前运行和资源摘要，然后返回缩略 PNG；最多保留32条摘要缓存，缓存命中也先鉴权。URL 不带 token，前端用可撤销 blob URL；切图、解绑、断线、定义改变清空。业务 Image 不额外生成公开 /view 预览文件。

普通上传文件测试走明确 file_name，不读取 latest；缺失可选参考图阻断三个张量输出而尺寸为0。ALPHA 与 MASK 不是同一语义。

## 快照与回执

- prepare.attachToken 每次按当前附着验证，但不进入持久化请求指纹；相同 runId 重试不重新随机或生成。
- claim.firstClaim 仅诊断。重复 claim 不授权另一次 /prompt；提交方使用持久化发送记录与恢复流程。
- parameter.applied 表示可见控件/真实值已应用；parameter.durable 表示持久化完成，不增加参数 revision。
- test.heartbeat / test.invalidate 仅测试图 owner 可发；切图不得捕获另一张图作为 fallback。
- 结果 received/placed 分开记录，只允许运行 controller 确认；不改变执行成功与失败，不触发重跑。
- ComfyUI 执行时派生的节点顶层 is_changed 可含 NaN；校验冻结 prompt/PNG metadata 时只移除这一核心缓存字段，用户 inputs 仍严格禁止 NaN。

## 示例

见 [工作流示例](../../../example_workflows/README.md)。本包不包含内部交接文档和测试记录。
