# Send to PS

RGB必需、ALPHA可选；输出RGB三通道及同尺寸同批次ALPHA。ALPHA缺省优先用RGBA输入自带透明度，否则全1；允许一张ALPHA广播，不拉伸。PNG保存时组合透明度。输出可继续连下游，发送不会因此再触发一次。无业务request_id的本地Queue仅保存临时预览。

类别：🔷PS Vplugins。旧节点/端口布局不兼容，不自动迁移。
