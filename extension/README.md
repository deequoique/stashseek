# StashSeek Chat 搜藏助手（试点）

这是项目唯一的用户界面：一个 Manifest V3 Chrome/Chromium 插件。安装后，插件完成邮箱验证码登录、设备配对、YouTube/NTULearn/Kaltura 字幕捕获、资料库读取和后续对话入口；服务端只提供 API 和渠道运行时，不再构建或托管 React Web 页面。

## 侧边栏

点击工具栏图标会打开侧边栏（没有弹窗，需要 Chrome 114 或更新版本）。侧边栏跟随当前标签页：

- 未登录时先用邮箱验证码登录；登录同时会把这个浏览器配对为字幕捕获设备。已经在工作区登录、但没有配对的浏览器，会在第一次点“保存到资料库”时再确认一次验证码。
- 打开 YouTube 或 NTULearn/Kaltura 视频后，侧边栏在本地读取字幕（不提交），显示标题和字幕状态，之后可以直接就这个视频提问：回答流式显示，引用以 `m:ss · 标题` 的形式给出，点击会让当前标签页的播放器跳到对应时间；标签页已经切换到别的视频时改为在新标签页打开链接。
- 同一个视频的追问共用一个会话并复用已读取的字幕；SPA 切换到另一个视频（YouTube 视频 ID 或 NTULearn 页面地址变化）会开始新会话并重新读取。
- 没有字幕的 YouTube 视频只基于标题、简介和章节回答；没有字幕的 NTULearn 视频提示无法提问，不向服务端发请求。
- “保存到资料库”提交的是已经读取的字幕，经由既有的 capture.v1 接口，不会重新读取页面。
- 问答记录（问题、回答、短引用）会出现在工作区历史中，并标注“视频页”，在工作区只读；字幕本身不会被保存。

侧边栏是 `sidepanel.html`（第二个 Vite 入口）；服务 worker 在安装和浏览器启动时调用 `chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })`。读取页面、播放位置、跳转和保存都经由 worker 命令（`READ_PAGE`、`PLAYBACK`、`SEEK`、`SAVE_CAPTURE`），令牌仍只由 worker 持有。

## 构建与加载

在 `extension/` 中使用项目要求的 Node 版本运行：

```console
pnpm install
pnpm test:coverage
pnpm typecheck
pnpm lint
pnpm build
pnpm run audit
```

然后在 `chrome://extensions` 开启“开发者模式”，选择“加载已解压的扩展程序”，加载 `extension/dist/`。

本地开发服务器运行在精确回环地址时，改用：

```console
pnpm package:local
```

本地构建的 API manifest 只允许 `http://127.0.0.1:8000/*`，生产构建只允许 `https://notebookai.deequoique.tech/*`；两者不会同时出现在同一个产物中。扩展 API 直接走回环 HTTP，避开本地自签名证书；配对批准页面仍由 `https://localhost:8443` 提供，以保留安全 Cookie。切换构建后必须在 `chrome://extensions` 重新加载 unpacked extension。
配对凭据会绑定到构建选择的 API origin；从生产版切换到本地版时会自动清理生产环境的待配对状态和 token，避免跨环境发送旧凭据。首次连接会在插件内请求邮箱验证码并直接批准设备，不依赖网页批准页。

试点 manifest 使用固定公开 key，扩展 ID 是 `omogodipchfidpikpeebgmlplpkjnpfm`。后端必须精确配置：

```dotenv
BROWSER_COMPANION_ALLOWED_ORIGINS=chrome-extension://omogodipchfidpikpeebgmlplpkjnpfm
```

插件从构建产物的精确 API host permission 解析唯一服务地址。默认生产构建连接 `https://notebookai.deequoique.tech`，本地构建连接 `http://127.0.0.1:8000`；请求有 10 秒超时，不会因服务不可达而无限等待。后端仍必须允许该构建对应的固定扩展 origin。

扩展 bearer session 的短期索引当前驻留在单个 API 进程内，并按过期时间和数量上限
清理；生产多 worker 部署必须使用粘性路由或后续接入共享 session store，不能让同一
登录在没有共享状态的 worker 之间随机切换。

NTULearn 的视频播放器可能位于 Kaltura 官方跨域 frame 中，因此 manifest 还包含精确的 `https://cdnapisec.kaltura.com/*` 播放器权限。这个权限只用于在用户点击插件时进入播放器并在浏览器本地读取已授权字幕；播放器的 Cookie、KS、Authorization 和签名资源 URL 都不会进入 StashSeek Chat 请求。

实际授权媒体页 `https://ntulearnv1.ntu.edu.sg/*` 也作为单独的精确 host permission 支持；不会扩大为 `*.ntu.edu.sg` 通配权限。该页面的 `page_url` 只保留 HTTPS origin/path，canonical Kaltura reference 仍使用既有的无秘密 NTULearnVideo 媒体链接。

YouTube 字幕优先读取 JSON3；若同一可信签名字幕端点返回空或非 JSON 内容，会依次尝试 WebVTT 和原始 YouTube XML。若三种 timed-text 表示都为空，最后使用当前页面公开的官方 `youtubei/v1/get_transcript` 端点；其不透明参数必须在解码后包含当前视频 ID，避免 SPA 导航后使用旧转录。所有回退仍在当前页面内完成，字幕 URL、转录参数和签名查询不会提交给 StashSeek Chat。

哔哩哔哩（`https://www.bilibili.com/video/BV…` 或 `av…`，含 `?p=N` 分 P）只需要精确的 `https://www.bilibili.com/*` host permission。适配器在页面内用观看者自己的登录态读取：页面状态（`window.__INITIAL_STATE__`，与 URL 中的视频 ID 不符时改用同一 ID 的公开 `x/web-interface/view` 接口，仍不符则返回 `stale_player_response`）、字幕列表接口 `api.bilibili.com/x/player/wbi/v2?aid=&cid=`（无需 WBI 签名，需要登录的 AI 字幕也由此获得），以及 `*.hdslb.com` 上的字幕 JSON。人工字幕优先于 AI 字幕（`ai-*`，标记为 `auto_caption`），中文优先于其他语言。字幕 JSON 的签名 URL（`auth_key`）只在当前页面用无 Cookie 请求获取，不返回、不记录；Cookie 只随 `api.bilibili.com` 的同站请求发送。因为请求都在页面内发出，不需要 `api.bilibili.com` 或 `hdslb.com` 的扩展 host permission；Chrome Web Store 说明：“读取用户正在观看的哔哩哔哩视频的字幕，仅在页面内完成”。`platform_id` 是与服务端连接器一致的规范化 `BV…`/`av…`，因此扩展保存和 URL 导入指向同一个资料库条目；`page_url` 在 P>1 时保留 `p`，引用链接对 P>1 保留 `p` 并加 `t`。弹幕不会被读取。

## 隐私边界

- 页面中的 Cookie、SAML、Kaltura KS、签名字幕/视频 URL 只在当前页面获取字幕时临时使用，不进入提交数据。
- 插件只提交规范化字幕 cue、平台视频 ID、无秘密的规范 URL 和公开元数据。
- 无字幕时只提交 `unavailable`，不上传音频或视频。
- 提问时会把当前视频的字幕和公开元数据随问题发送到服务端，只用于生成这一次回答：服务端不把字幕写入数据库、对象存储或日志，对话历史只保留不超过 180 字的引用短句；这条请求使用登录会话 Bearer，捕获 Bearer 不能用于提问。
- 捕获 Bearer 只可使用浏览器捕获与自助撤销接口，不能读取资料库、聊天或 Web/MCP API。
- 侧边栏“退出登录”会先请求后端撤销该 Bearer，再清除本地凭据；网页账户页也可以撤销设备。
