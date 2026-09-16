# ComfyUI PS Bridge Nodes

ComfyUI 端的六个自定义节点，分类为 **🔷PS Vplugins**。使用 ComfyUI V3 API 和 Bridge contract 3。
本仓库仅包含节点代码；VPlugins 和 Photoshop 端为独立项目。

| 节点 | 功能 |
| --- | --- |
| PS Images (`VP_Image`) | 最多六个唯一图像槽，输出 RGB、ALPHA、MASK、宽和高 |
| PS Seed (`VP_Seed`) | 固定/随机种子，输出 INT |
| PS Slider (`VP_Slider`) | 数值范围和步幅，输出 FLOAT |
| PS Prompt (`VP_Prompt`) | 多行原样文本，输出 STRING |
| PS Batch (`VP_Batch`) | 1–4 的数量参数，不自行复制图片或排队 |
| Send to PS (`VP_SendToPS`) | 接收 RGB 和可选 ALPHA，保存 PNG 与运行回执 |

## 安装

在 `ComfyUI/custom_nodes` 中执行：

```bash
git clone https://github.com/AIGCTV/comfyui-ps-bridge-nodes.git
```

使用**运行 ComfyUI 的 Python** 安装本节点目录中的 `requirements.txt`：

```bash
python -m pip install -r requirements.txt
```

上面的依赖命令在克隆得到的节点目录中运行。Windows 便携版请使用该 ComfyUI 的嵌入式 Python。
重启 ComfyUI，并刷新浏览器。额外依赖为 `rfc8785==0.1.4` 和 `jsonschema>=4.20,<5`；
torch、numpy、Pillow、aiohttp 由 ComfyUI 环境提供。前端不需要 npm 安装或构建。

## 使用

打开 [无模型执行示例](example_workflows/PS_Bridge_Roundtrip.json)，在 PS Images 点击“＋”上传自己的图片，
先保持 Slider=1、Batch=2，再 Queue。模板中的合成图片文件名是占位引用，本仓库不附带图片。
本地 Queue 保存 ComfyUI 临时 PNG，不向无关的 Photoshop 运行发送结果。
另有[六节点展示图](example_workflows/PS_Vplugins_Six_Nodes.json)和[示例说明](example_workflows/README.md)。

Seed、Slider、Prompt、Batch 每类最多一个；Images 最多六个，main/img2…img6 名称不重复。
main 无真实选区时 MASK 全白；参考图不借用 main 的选区。ALPHA 中 1 表示不透明、0 表示透明。
缩放 RGB 时应同步缩放 ALPHA，不进行隐式拉伸。

## 兼容性与连接

验证基线为 ComfyUI core 0.34.0、frontend 1.49.6、Python 3.12.10。
经典画布为完整支持目标；Vue Nodes 的自定义布局和 Images 上传/预览尚未完整支持。
不提供旧 Adv 两节点、九节点中间版本的兼容别名或自动迁移。

接口根路径为 `/ps-bridge/v3`。协议组合和机器契约见 [Bridge contract 3](docs/contracts/bridge-v3/README.md)。
完整 Photoshop 工作流需要兼容的 VPlugins/控制端完成资源注册、capture、prepare、submit 与结果置入。
本节点测试不构成 Photoshop/VPlugins 端到端验证声明。

默认允许本机 loopback。客户端凭证自动申请并保存在编辑器内存，不写入工作流。
服务端可配置 `PS_BRIDGE_AUTH_TOKEN`；LAN 还需要 `PS_BRIDGE_ALLOW_LAN=1`。
当前编辑器没有共享口令输入功能，无法连接启用共享口令的服务端；LAN 需要配套客户端适配。
共享口令不加密流量，远程使用应通过可信网络或 HTTPS/WSS。

运行数据在首次使用时生成于 `data/bridge-v3/`（可由 `PS_BRIDGE_DATA_DIR` 指定），不随源码发布。
公开文件边界见 [RELEASE_MANIFEST.md](RELEASE_MANIFEST.md)。

## License

本节点采用 [MIT](LICENSE)。VPlugins 和其他伴随软件适用各自的分发条款。
