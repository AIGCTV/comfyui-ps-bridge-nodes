# ComfyUI PS Bridge Nodes

<p align="center">
  <img src="js/assets/icon.png" alt="ComfyUI PS Bridge Nodes icon" width="128">
</p>

<p align="center">
  <a href="#chinese">简体中文</a> · <a href="#english">English</a>
</p>

这是为 **Vplugins 本地功能**配套使用的 ComfyUI 自定义节点项目，用于在本机 ComfyUI 与 Photoshop / Vplugins 之间传递图像、蒙版和工作流参数。

> 当前仓库只包含 **ComfyUI 节点端**。完整使用需要配合 [Vplugins](https://github.com/AIGCTV/Vplugins-releases)；不连接 Photoshop 时，也可以先在 ComfyUI 中独立测试节点和工作流。产品介绍与相关信息请访问 [Vplugins 官网](https://ps.aigctv.net)。

<a id="chinese"></a>

## 简体中文

### 功能概览

扩展安装后会在 **`🔷PS Vplugins`** 分类中提供 6 种节点：

| 节点 | 用途 |
| --- | --- |
| **PS Images** | 接收主图或最多 5 张参考图，输出 RGB、透明度、选区蒙版和尺寸 |
| **PS Prompt** | 提供可由工作流或 Vplugins 修改的多行文本 |
| **PS Slider** | 提供带最小值、最大值和步长的浮点参数 |
| **PS Batch** | 提供 1–4 的批次数量 |
| **PS Seed** | 提供固定或随机种子 |
| **Send to PS** | 保存最终 PNG，并在 Bridge 运行中把结果交回 Photoshop |

节点界面会跟随 ComfyUI 的语言设置自动显示中文或英文。README 可通过页面顶部的 **简体中文 / English** 链接切换。

### 配套项目

本项目不是独立的 Photoshop 插件，而是 Vplugins 在本机调用 ComfyUI 工作流时使用的自定义节点扩展。完整链路为：

```text
Photoshop / Vplugins  ⇄  ComfyUI PS Bridge Nodes  ⇄  ComfyUI 工作流
```

- **Vplugins 发布与下载**：[github.com/AIGCTV/Vplugins-releases](https://github.com/AIGCTV/Vplugins-releases)
- **Vplugins 官网**：[ps.aigctv.net](https://ps.aigctv.net)
- **当前仓库职责**：安装在 ComfyUI 的 `custom_nodes` 中，负责接收 Vplugins 传来的图片、选区和参数，并把工作流结果返回给 Vplugins。

### 环境要求

- Python 3.10 或更高版本。
- 支持 ComfyUI V3 自定义节点 API 的较新版本 ComfyUI。
- 已验证环境：ComfyUI core `0.34.0`、frontend `1.49.6`、Python `3.12.10`。
- 推荐使用 ComfyUI 经典画布。Vue Nodes 可保留参数和切换测试模式，但自定义节点布局、图片上传和预览尚未完整支持。
- 如需连接 Photoshop，需要另行安装兼容版本的 Vplugins。

### 安装

#### 方法一：使用 ComfyUI Manager

如果 ComfyUI Manager 中可以搜索到 **ComfyUI PS Bridge Nodes**：

1. 在 Manager 中搜索并安装该扩展。
2. 安装完成后重启 ComfyUI。
3. 浏览器执行一次强制刷新（通常为 `Ctrl+F5`）。

#### 方法二：Git 手动安装

在 ComfyUI 的 `custom_nodes` 目录中执行：

```bash
git clone https://github.com/AIGCTV/comfyui-ps-bridge-nodes.git
cd comfyui-ps-bridge-nodes
python -m pip install -r requirements.txt
```

这里的 `python` 必须是 **启动 ComfyUI 所使用的 Python**，不要误用另一个系统 Python。

Windows 便携版可以从 `ComfyUI_windows_portable` 根目录执行：

```powershell
git clone https://github.com/AIGCTV/comfyui-ps-bridge-nodes.git .\ComfyUI\custom_nodes\comfyui-ps-bridge-nodes
.\python_embeded\python.exe -s -m pip install -r .\ComfyUI\custom_nodes\comfyui-ps-bridge-nodes\requirements.txt
```

完成后重启 ComfyUI，并强制刷新浏览器页面。

#### 方法三：下载 ZIP

1. 下载仓库源码并解压到 `ComfyUI/custom_nodes/comfyui-ps-bridge-nodes`。
2. 确认该目录下可以直接看到 `__init__.py`、`requirements.txt`、`ps_bridge` 和 `js`；不要多套一层同名文件夹。
3. 使用 ComfyUI 的 Python 安装 `requirements.txt`。
4. 重启 ComfyUI，并强制刷新浏览器页面。

### 确认安装成功

在 ComfyUI 画布空白处右键，依次打开：

`Add Node / 添加节点 → 🔷PS Vplugins`

如果能看到 6 种节点，说明后端与前端扩展都已加载。界面语言跟随：

`Settings / 设置 → Comfy → Locale`

### 五分钟本地体验

本地模式不需要 Photoshop，也不需要加载模型：

1. 打开或拖入 [`example_workflows/PS_Bridge_Roundtrip.json`](example_workflows/PS_Bridge_Roundtrip.json)。
2. 在 **PS Images** 节点点击 `＋`，选择一张本地图片。
3. 第一次测试先保持 **PS Slider = 1**，这样示例中的 RGB 与 ALPHA 尺寸一致。
4. 选择 **PS Batch = 1–4**。示例中的 `RepeatImageBatch` 会使用该数量复制图像。
5. 点击 ComfyUI 的 **Queue / 运行**。
6. **Send to PS** 会显示并保存临时 PNG 预览；因为当前没有 Bridge 请求，结果不会发送到 Photoshop，这是正常行为。

另一个示例 [`example_workflows/PS_Vplugins_Six_Nodes.json`](example_workflows/PS_Vplugins_Six_Nodes.json) 用于查看全部节点的外观和控件，它不是一条完整可执行链。

### 从零搭建工作流

最小图像链路如下：

```text
PS Images · RGB  →  你的图像处理节点  →  Send to PS · RGB
```

按需要增加其他连接：

- `PS Images · MASK` → 修复、局部重绘或其他蒙版输入。
- `PS Images · ALPHA` → `Send to PS · ALPHA`，用于保留透明度。
- `PS Seed · seed` → 采样器的种子输入。
- `PS Prompt · text` → 接受 STRING 的提示词或文本处理节点。
- `PS Slider · value` → 强度、降噪或其他 FLOAT 输入。
- `PS Batch · count` → `RepeatImageBatch` 的数量或其他 INT 批次输入。

注意：**PS Batch 只输出数字**，不会自动重复图片，也不会自动提交多次 Queue。必须把 `count` 连接到真正使用批次数量的节点。

### 连接 Photoshop / Vplugins

1. 从 [Vplugins 发布仓库](https://github.com/AIGCTV/Vplugins-releases) 获取配套应用，并确保 ComfyUI PS Bridge Nodes 与 Vplugins 使用兼容版本。
2. 在 ComfyUI 中打开要测试的工作流，并确认末端包含 **Send to PS**。
3. 点击顶部工具栏的 **PS Vplugins** 链接图标开启测试模式。测试目标会跟随当前工作流标签页。
4. 状态为灰色表示测试关闭，红色表示已开启但尚未连通，绿色表示当前目标已连接。
5. 从 Vplugins 发起运行。Bridge 会为本次请求注入图片、选区、参数和请求标识；普通用户不需要手动填写节点中的高级标识字段。
6. 工作流完成后，**Send to PS** 保存 PNG，并把本次请求对应的结果交给兼容的 Vplugins 端。
7. 测试结束后再次点击顶部图标关闭测试模式。

同一时间只有一个浏览器可以持有测试目标。切换工作流结构、断线或重启任一端后，如果状态没有恢复，请关闭再重新开启测试模式。

### 节点详细说明

#### 1. PS Images

用于接收 Photoshop 图片，或在本地模式中手动上传图片。

主要控件：

- **Image Name / 图像名称**：`main`、`IMG2`、`IMG3`、`IMG4`、`IMG5`、`IMG6`。
- **＋**：本地模式下选择并上传图片。
- **Required / 必需**：高级选项；参考图可设为可选，`main` 始终必需。

输出：

| 输出 | 类型 | 说明 |
| --- | --- | --- |
| `RGB` | IMAGE | 三通道彩色图像 |
| `ALPHA` | MASK | 透明度，`1` 为不透明，`0` 为透明 |
| `MASK` | MASK | Photoshop 主图选区；没有选区时为全白 |
| `width` | INT | 图像宽度 |
| `height` | INT | 图像高度 |

使用规则：

- 一个工作流最多放置 6 个 PS Images，且每个图像名称只能使用一次。
- 只有 `main` 可以携带真实选区；参考图的 `MASK` 始终为全白。
- `MASK` 不会自动作用到 RGB，`ALPHA` 也不会自动变成选区，必须连接到需要它们的节点。
- 本地图片支持静态 8 位 RGB、RGBA、灰度、灰度透明和调色板图片，并会应用 EXIF 方向。
- 高位深图片、动画图片、丢失文件或尺寸不匹配的选区会明确报错。
- 本地重新上传图片成功后会清除旧的本地选区绑定；取消上传或上传失败不会改变当前图片。

#### 2. PS Prompt

提供一个多行文本输入和一个 `STRING` 输出。

- 点击文本区域开始编辑。
- `Enter` 换行，`Ctrl+Enter` 提交并结束编辑。
- 支持中文输入法、文本选择、复制和粘贴。
- 文本会按原样输出，不进行动态提示词展开。
- 每个工作流最多放置一个 PS Prompt。

#### 3. PS Slider

提供一个可拖动或手动输入的浮点参数，输出为 `FLOAT`。

| 控件 | 默认值 | 说明 |
| --- | ---: | --- |
| `Value` | `1` | 当前输出值 |
| `MIN` | `0` | 最小值 |
| `MAX` | `1` | 最大值 |
| `STEP` | `0.01` | 调整步长 |

`MIN` 必须小于 `MAX`，`STEP` 必须大于 0，当前值必须位于范围内。每个工作流最多放置一个 PS Slider。修改范围或步长后，如工作流已绑定到 Vplugins，请重新绑定或重新开启测试模式。

#### 4. PS Batch

输出 `1`、`2`、`3` 或 `4` 之一，类型为 `INT`，默认值为 `1`。

它只描述期望的批次数量。要真正产生多张图片，请把 `count` 连接到 `RepeatImageBatch`、潜空间批次或其他支持批次的节点。每个工作流最多放置一个 PS Batch。

#### 5. PS Seed

输出 ComfyUI 可安全保存和跨端传输的整数种子，范围为 `0–9007199254740991`，默认值为 `42`。

- **Fixed / 固定**：每次运行使用当前种子。
- **Random / 随机**：每次新的运行生成一个种子，并在节点上显示实际值。
- **New seed / 单次随机**：立即生成一个新值，然后回到固定模式。

Bridge 会在正式提交前固定本次使用的实际种子，因此同一次请求的重试仍使用相同值。每个工作流最多放置一个 PS Seed。

#### 6. Send to PS

这是工作流的最终输出节点。

输入：

- **RGB**：必需，接受 RGB 或 RGBA IMAGE。
- **ALPHA**：可选，`0` 为透明，`1` 为不透明。

行为：

- 未连接 ALPHA 时，优先使用 RGBA 输入自带的透明度；RGB 输入则使用全不透明 ALPHA。
- 单张 ALPHA 可以广播到同尺寸的整批图像；否则 ALPHA 批次必须与 RGB 批次一致。
- 节点不会拉伸 ALPHA。图像缩放后，必须同步缩放 ALPHA 再连接，否则会出现尺寸不匹配错误。
- RGB 与 ALPHA 会组合为 PNG。Bridge 运行保存到 ComfyUI output，本地 Queue 保存到 temp。
- 节点还会输出 RGB 和 ALPHA，方便继续连接预览或其他下游节点；这些输出不会导致重复发送。

### 工作流规则与兼容性

- `PS Seed`、`PS Slider`、`PS Prompt`、`PS Batch` 每种最多一个。
- `PS Images` 最多 6 个，图像名称不能重复。
- 用于 Bridge 的工作流至少需要一个 `Send to PS`。
- 旧版 `Adv_Request`、`Adv_SendToPS` 和旧端口布局不兼容当前六节点，不会自动迁移；请用当前节点重新搭建。
- 普通使用不要修改高级字段中的 `param_id`、`source_json`、`request_id`、`result_id` 等标识，除非你正在制作与之配套的工作流定义。

### 常见问题

**安装后看不到节点**

- 检查是否出现了双层目录，例如 `custom_nodes/comfyui-ps-bridge-nodes/comfyui-ps-bridge-nodes`。
- 确认依赖安装在 ComfyUI 实际使用的 Python 中。
- 更新 ComfyUI 到支持 V3 节点 API 的版本。
- 完整重启 ComfyUI，并对浏览器执行 `Ctrl+F5`。
- 查看 ComfyUI 启动终端中的 Python 导入错误。

**节点存在，但上传按钮或自定义布局不正常**

请先切回经典画布。Vue Nodes 当前尚未完整支持 PS Images 的上传、预览和所有自定义控件。

**提示图像名称重复或节点数量过多**

每个 PS Images 必须选择不同名称，且总数不能超过 6；四种参数节点每种只能有一个。

**提示 ALPHA 尺寸不匹配**

ALPHA 的宽高必须与 RGB 完全一致。如果处理流程缩放或裁剪了 RGB，请对 ALPHA 做相同变换。

**本地 Queue 成功，但 Photoshop 没收到图片**

本地 Queue 没有 Bridge 请求标识，只会保存临时预览。请确认兼容的 Vplugins 端已连接，并从 Vplugins 发起运行。

**顶部状态一直是红色或离线**

确认 ComfyUI PS Bridge Nodes 与 Vplugins 版本兼容、Vplugins 已启动，并且没有另一个浏览器占用测试目标。必要时重启两端，然后重新开启测试模式。

**旧工作流提示节点或端口不兼容**

当前版本不自动迁移旧节点。删除旧节点并使用 `🔷PS Vplugins` 分类中的 6 种当前节点重新连接。

### 网络与安全

默认连接面向本机 loopback 使用。不要直接把 ComfyUI Bridge 暴露到不可信网络。局域网使用需要服务端和客户端同时适配，并应部署在可信网络或 HTTPS/WSS 后面。

### 示例与许可

- [本地无模型往返示例](example_workflows/PS_Bridge_Roundtrip.json)
- [六节点界面展示](example_workflows/PS_Vplugins_Six_Nodes.json)
- [示例工作流说明](example_workflows/README.md)
- [MIT License](LICENSE)

[返回顶部](#comfyui-ps-bridge-nodes) · [Switch to English](#english)

---

<a id="english"></a>

## English

### Overview

This extension adds six nodes under **`🔷PS Vplugins`**:

| Node | Purpose |
| --- | --- |
| **PS Images** | Receives a main image and up to five reference images; outputs RGB, opacity, selection mask, and dimensions |
| **PS Prompt** | Provides editable multiline text for the workflow or Vplugins |
| **PS Slider** | Provides a floating-point parameter with a configurable range and step |
| **PS Batch** | Provides a batch count from 1 to 4 |
| **PS Seed** | Provides a fixed or random seed |
| **Send to PS** | Saves final PNG files and returns Bridge results to Photoshop |

Node labels automatically follow the ComfyUI locale. Use the **简体中文 / English** links at the top of this README to switch documentation language.

### Companion project

This repository is not a standalone Photoshop plugin. It is the ComfyUI custom-node extension used by the local workflow feature in Vplugins. The complete path is:

```text
Photoshop / Vplugins  ⇄  ComfyUI PS Bridge Nodes  ⇄  ComfyUI workflow
```

- **Vplugins releases and downloads**: [github.com/AIGCTV/Vplugins-releases](https://github.com/AIGCTV/Vplugins-releases)
- **Vplugins website**: [ps.aigctv.net](https://ps.aigctv.net)
- **Role of this repository**: install it under ComfyUI `custom_nodes` to receive images, selections, and parameters from Vplugins and return workflow results to Vplugins.

### Requirements

- Python 3.10 or newer.
- A recent ComfyUI version with the V3 custom-node API.
- Verified environment: ComfyUI core `0.34.0`, frontend `1.49.6`, and Python `3.12.10`.
- The classic ComfyUI canvas is recommended. Vue Nodes can retain parameters and toggle Test Mode, but the custom layout, image upload, and preview are not fully supported yet.
- A compatible version of Vplugins is required for Photoshop round trips.

### Installation

#### Option 1: ComfyUI Manager

If **ComfyUI PS Bridge Nodes** is available in your ComfyUI Manager:

1. Search for and install the extension.
2. Restart ComfyUI.
3. Hard-refresh the browser, usually with `Ctrl+F5`.

#### Option 2: Manual Git installation

Run the following commands inside the ComfyUI `custom_nodes` directory:

```bash
git clone https://github.com/AIGCTV/comfyui-ps-bridge-nodes.git
cd comfyui-ps-bridge-nodes
python -m pip install -r requirements.txt
```

The `python` command must point to the **same Python environment that runs ComfyUI**.

For Windows portable, run this from the `ComfyUI_windows_portable` directory:

```powershell
git clone https://github.com/AIGCTV/comfyui-ps-bridge-nodes.git .\ComfyUI\custom_nodes\comfyui-ps-bridge-nodes
.\python_embeded\python.exe -s -m pip install -r .\ComfyUI\custom_nodes\comfyui-ps-bridge-nodes\requirements.txt
```

Restart ComfyUI and hard-refresh the browser when installation finishes.

#### Option 3: ZIP installation

1. Download and extract the source to `ComfyUI/custom_nodes/comfyui-ps-bridge-nodes`.
2. Confirm that `__init__.py`, `requirements.txt`, `ps_bridge`, and `js` are directly inside that directory. Avoid an extra nested folder.
3. Install `requirements.txt` with the Python environment used by ComfyUI.
4. Restart ComfyUI and hard-refresh the browser.

### Verify the installation

Right-click an empty area of the ComfyUI canvas and open:

`Add Node → 🔷PS Vplugins`

Seeing all six node types confirms that both the backend and frontend extension loaded. Change the node UI language under:

`Settings → Comfy → Locale`

### Five-minute local test

You can test the extension without Photoshop or a model:

1. Open or drag in [`example_workflows/PS_Bridge_Roundtrip.json`](example_workflows/PS_Bridge_Roundtrip.json).
2. Click `＋` on **PS Images** and select a local image.
3. Keep **PS Slider = 1** for the first run so RGB and ALPHA remain the same size in the example.
4. Choose **PS Batch = 1–4**. The example's `RepeatImageBatch` node uses this value to duplicate the image.
5. Click **Queue**.
6. **Send to PS** displays and saves temporary PNG previews. With no Bridge request attached, nothing is sent to Photoshop; this is expected.

[`example_workflows/PS_Vplugins_Six_Nodes.json`](example_workflows/PS_Vplugins_Six_Nodes.json) is a visual showcase of all six node types, not a complete executable graph.

### Build a workflow from scratch

The minimum image path is:

```text
PS Images · RGB  →  your image-processing nodes  →  Send to PS · RGB
```

Add connections as needed:

- `PS Images · MASK` → inpainting, regional processing, or another mask input.
- `PS Images · ALPHA` → `Send to PS · ALPHA` to preserve opacity.
- `PS Seed · seed` → a sampler seed input.
- `PS Prompt · text` → a prompt or text-processing node that accepts STRING.
- `PS Slider · value` → strength, denoise, or another FLOAT input.
- `PS Batch · count` → `RepeatImageBatch` or another INT batch input.

**PS Batch only outputs a number.** It does not duplicate images or submit additional Queue jobs by itself.

### Connect Photoshop / Vplugins

1. Download the companion application from the [Vplugins releases repository](https://github.com/AIGCTV/Vplugins-releases), and make sure ComfyUI PS Bridge Nodes and Vplugins use compatible versions.
2. Open the workflow in ComfyUI and make sure it ends with **Send to PS**.
3. Click the **PS Vplugins** link icon in the top toolbar to enable Test Mode. The target follows the current workflow tab.
4. Gray means Test Mode is off, red means it is enabled but not connected, and green means the current target is connected.
5. Start the run from Vplugins. The Bridge injects request images, selections, parameters, and request identifiers; ordinary users should not edit the advanced identity fields manually.
6. When the graph finishes, **Send to PS** saves PNG files and returns the results for that request to the compatible Vplugins component.
7. Click the toolbar icon again to leave Test Mode.

Only one browser can own the Test Mode target at a time. After a graph structure change, disconnection, or restart, disable and re-enable Test Mode if the connection does not recover.

### Node reference

#### 1. PS Images

Receives Photoshop images or lets you upload images in local mode.

Main controls:

- **Image Name**: `main`, `IMG2`, `IMG3`, `IMG4`, `IMG5`, or `IMG6`.
- **＋**: selects and uploads an image in local mode.
- **Required**: advanced option for reference images; `main` is always required.

Outputs:

| Output | Type | Description |
| --- | --- | --- |
| `RGB` | IMAGE | Three-channel color image |
| `ALPHA` | MASK | Opacity, where `1` is opaque and `0` is transparent |
| `MASK` | MASK | Selection from the Photoshop main image; all white when no selection exists |
| `width` | INT | Image width |
| `height` | INT | Image height |

Rules and behavior:

- A workflow can contain up to six PS Images nodes, and each image name must be unique.
- Only `main` can carry a real selection. Reference-image MASK outputs are always white.
- MASK is not applied to RGB automatically, and ALPHA is not converted to a selection automatically. Connect them explicitly where needed.
- Local files support static 8-bit RGB, RGBA, grayscale, grayscale-with-alpha, and palette images. EXIF orientation is applied.
- High-bit-depth images, animations, missing files, and mismatched selection dimensions produce explicit errors.
- A successful local image replacement clears the previous local selection binding. Canceling or failing an upload keeps the current image unchanged.

#### 2. PS Prompt

Provides one multiline text field and one `STRING` output.

- Click the text area to edit.
- `Enter` inserts a new line; `Ctrl+Enter` commits and closes the editor.
- IME input, selection, copy, and paste are supported.
- Text is returned exactly as entered; dynamic prompt expansion is disabled.
- Only one PS Prompt is allowed per workflow.

#### 3. PS Slider

Provides a draggable or directly editable floating-point value and a `FLOAT` output.

| Control | Default | Description |
| --- | ---: | --- |
| `Value` | `1` | Current output value |
| `MIN` | `0` | Minimum value |
| `MAX` | `1` | Maximum value |
| `STEP` | `0.01` | Adjustment step |

`MIN` must be lower than `MAX`, `STEP` must be positive, and the current value must remain inside the range. Only one PS Slider is allowed per workflow. If a bound workflow's range or step changes, attach it again or re-enable Test Mode.

#### 4. PS Batch

Outputs an `INT` value of `1`, `2`, `3`, or `4`; the default is `1`.

The node only describes the desired count. Connect `count` to `RepeatImageBatch`, a latent batch input, or another batch-aware node to produce multiple images. Only one PS Batch is allowed per workflow.

#### 5. PS Seed

Outputs a safely serializable integer from `0` to `9007199254740991`; the default is `42`.

- **Fixed**: uses the current value on every run.
- **Random**: generates one value for each new run and displays the actual value on the node.
- **New seed**: immediately creates a new value, then returns to Fixed mode.

The Bridge freezes the actual seed before a business submission, so retries of the same request keep the same value. Only one PS Seed is allowed per workflow.

#### 6. Send to PS

The terminal output node for a Bridge workflow.

Inputs:

- **RGB**: required; accepts an RGB or RGBA IMAGE.
- **ALPHA**: optional; `0` is transparent and `1` is opaque.

Behavior:

- Without an ALPHA connection, RGBA input opacity is used when available; RGB input receives a fully opaque alpha channel.
- One ALPHA image can be broadcast across an equal-sized image batch. Otherwise, the ALPHA and RGB batch sizes must match.
- ALPHA is never resized implicitly. If the workflow resizes RGB, apply the same transform to ALPHA before connecting it.
- RGB and ALPHA are combined into PNG files. Bridge runs save to ComfyUI output; local Queue runs save to temp.
- RGB and ALPHA outputs remain available for previews or downstream nodes. Those connections do not cause duplicate sends.

### Workflow rules and compatibility

- At most one each of `PS Seed`, `PS Slider`, `PS Prompt`, and `PS Batch` is allowed.
- Up to six `PS Images` nodes are allowed, with unique image names.
- A Bridge workflow needs at least one `Send to PS` node.
- Legacy `Adv_Request`, `Adv_SendToPS`, and old port layouts are incompatible with the current six-node design and are not migrated automatically.
- Do not edit advanced identity fields such as `param_id`, `source_json`, `request_id`, or `result_id` during normal use.

### Troubleshooting

**The nodes do not appear after installation**

- Check for an extra nested folder such as `custom_nodes/comfyui-ps-bridge-nodes/comfyui-ps-bridge-nodes`.
- Install dependencies into the Python environment that actually runs ComfyUI.
- Update ComfyUI to a version that supports the V3 node API.
- Fully restart ComfyUI and hard-refresh the browser with `Ctrl+F5`.
- Check the ComfyUI startup terminal for Python import errors.

**The nodes appear, but upload or custom layouts do not work correctly**

Switch to the classic canvas. Vue Nodes does not yet fully support PS Images upload, preview, and all custom controls.

**Duplicate image name or too many nodes**

Every PS Images node needs a unique name, with no more than six total. Each of the four parameter-node types is limited to one.

**ALPHA shape mismatch**

ALPHA width and height must exactly match RGB. Apply the same resize or crop operation to ALPHA whenever RGB changes dimensions.

**Local Queue works, but Photoshop receives nothing**

A local Queue run has no Bridge request identity and only saves temporary previews. Connect a compatible Vplugins component and start the run from Vplugins.

**The toolbar status stays red or offline**

Confirm that ComfyUI PS Bridge Nodes and Vplugins are compatible, Vplugins is running, and another browser does not own the Test Mode target. Restart both ends and re-enable Test Mode if needed.

**An old workflow reports incompatible nodes or ports**

There is no automatic legacy migration. Rebuild those parts with the six current nodes under `🔷PS Vplugins`.

### Network and security

The default connection is designed for local loopback use. Do not expose the ComfyUI Bridge directly to an untrusted network. LAN use requires matching support on both ends and should be placed behind a trusted network or HTTPS/WSS.

### Examples and license

- [Local model-free round-trip example](example_workflows/PS_Bridge_Roundtrip.json)
- [Six-node UI showcase](example_workflows/PS_Vplugins_Six_Nodes.json)
- [Example workflow notes](example_workflows/README.md)
- [MIT License](LICENSE)

[Back to top](#comfyui-ps-bridge-nodes) · [切换到简体中文](#chinese)
