# 工作流示例

- `PS_Bridge_Roundtrip.json`：可执行的无模型图。先用 PS Images 的“＋”上传图片；默认 Slider=1、Batch=2。
- `PS_Vplugins_Six_Nodes.json`：六种控件的展示图，Send 未连接，不作为可排队算法图。
- `../data/workflows/`：合成的 API、UI 和 manifest 开发示例，保留同源摘要；支持矩阵为空，不是已经验证的 Photoshop 功能包。

工作流本身不包含输入照片、选区、生成结果或模型。两个同名 JPG 仅用于模板浏览器的静态封面，不会作为节点输入；
`six-main.png` 是合成占位文件名，用户需要上传自己的图片。不附带该文件时直接 Queue 会报缺图；上传后再运行。
业务 API 模板的 file_name/selection_file/request_id 为空，
需要兼容控制端通过 prepare 注入本次资源。切勿将 UI JSON 当作 API prompt 提交。

Roundtrip 将原图 ALPHA 连接到 Send，改变 RGB 倍率时必须同步缩放 ALPHA。
Batch 只输出数量，示例里的 RepeatImageBatch 才负责复制批次。本地 Queue 不构成 Photoshop 闭环。
