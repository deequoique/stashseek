# 使用浏览器伴侣

浏览器伴侣是可选的 Chrome/Chromium Manifest V3 获取入口。它在你当前打开的
YouTube 或 NTULearn/Kaltura 页面中读取字幕，然后只提交规范化字幕 cue 和公开元数据。
它不会替换服务器端 YouTube 获取，也不是任意网站抓取器。

## 1. 选择正确的安装包

生产环境从 Web 的“浏览器伴侣”页面下载 production zip。用于本机 loopback 服务时，
使用 local 构建；两种产物的 API host permission 不同，不能把两者混在同一个扩展
目录或同一个 manifest 中。

如果你需要自行构建或审计，进入 `extension/` 后执行：

```bash
pnpm install
pnpm package                 # production origin
pnpm package:local           # http://127.0.0.1:8000
```

在 `chrome://extensions` 开启“开发者模式”，选择“加载已解压的扩展程序”，加载
`extension/dist/`。local 构建切换后要重新加载 unpacked extension。扩展包的权限、
平台适配和构建审计详见 [extension README](../../extension/README.md)。

安装前确认 API origin 与当前资料库一致。不要安装一个同时允许生产和本机 API 的
未知 manifest；切换 origin 时扩展会清理不属于当前 target 的 pairing/grant 状态。

## 2. 配对设备

1. 登录 Web 资料库，打开“账号 → 浏览器伴侣”。
2. 在扩展弹窗点击“连接 StashSeek Chat”。
3. 新打开的 Web 页面会显示配对请求；确认当前账户和 origin 后点击“允许连接”。
4. 回到扩展弹窗，点击“我已批准，完成连接”。
5. 弹窗显示“已连接”后，再打开需要保存的视频页面。

配对请求有时效，过期或已经使用后请重新开始，不要复用旧链接。Web 页面只能批准
当前已登录租户的设备；扩展拿到的 grant 只有 `capture:write`，不能读取资料库、聊天、
普通 Web API 或 MCP。

## 3. 从当前页面保存字幕

支持的页面包括：

- YouTube 当前视频页；
- NTULearn/Kaltura 中用户已经获准访问的播放器页面。

打开视频并等待播放器完成加载，然后在扩展弹窗点击“保存当前视频”。扩展会先在
浏览器内读取受信任的字幕表示，再提交规范化 cue。成功后会显示 `已提交到资料库`，
资料库中先出现 `等待整理`/`正在整理`，完成后才可搜索。没有可用字幕时会显示
“需要语音转写”状态；扩展不会上传音频或视频。

如果页面刚切换视频、播放器尚未加载或字幕读取失败，刷新页面后再试。不要在弹窗
卡住时连续点击；请求有界超时，队列暂时不可用时保留一次可重试的确定性提交。

## 4. 检查和撤销设备

回到 Web 的“浏览器伴侣”页面，在“已连接的插件”中检查设备名称和版本。停止使用
某台设备时点击“断开连接”；扩展弹窗的“断开连接”也会先请求服务端撤销 Bearer，
再清除本地凭据。撤销后，旧设备不能继续提交 capture。

设备列表和撤销操作属于当前租户；不要把 device ID 或扩展 Bearer 当成普通工单信息。

## 隐私边界

扩展不会把 YouTube/NTULearn 登录 Cookie、SAML、Kaltura KS、Authorization、签名
字幕 URL 或播放凭据提交给 StashSeek Chat。服务端只接收受校验的 `capture.v1` 内容。
浏览器捕获 token 与 Web session、MCP grant 完全隔离；发现其中任何一个出现在日志或
代理 URL 中，应立即撤销对应设备并按[排查与响应](troubleshoot.md)处理。
