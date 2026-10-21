# Bilibili URL/插件技术设计

## 1. 设计目标与非目标

目标是把 Bilibili 接入现有的“可保存 URL + 可检索字幕”契约，并让登录态只停留在用户浏览器中。非目标包括：服务器保存 Bilibili cookie、下载媒体文件后自行 ASR、绕过验证码/风控、弹幕评论索引、整季番剧自动展开。

## 2. 分层边界

### URL 与 connector

- 新增 `app/connectors/bilibili.py`，保持 `Connector` Protocol，不复制 YouTube 的解析/存储逻辑。
- `match()` 只返回经过严格 host/path/id 校验的稳定 ID。优先 BV；旧 av 先在本地规范化为等价 BV（若无需远程解析无法证明映射，则保留 av canonical 并由 connector 在 metadata 阶段解析）。短链不在第一阶段接受。
- `fetch_meta()` 通过 yt-dlp JSON 获取标题、作者、发布时间、时长、简介、标签、章节、封面；对分集/playlist 设单条目策略，禁止隐式批量入库。
- `fetch_text()` 复用 yt-dlp 的字幕字段与受限下载器，输出 SRT/规范化 cue；无字幕返回 `NeedsASR`，明确标记登录字幕返回 `NeedsExtension` 或稳定的可重试分类，不能把空字幕当作 ready。

### 提交与 worker

- `normalize_item_reference()` 改为使用受信任 connector registry（静态 tuple/函数），只做本地校验，不调用网络。
- `_connector()` 根据 platform 选择 Bilibili/YouTube，并保留 CA、超时、代理和子进程环境的安全边界；Bilibili 不复用 `YOUTUBE_PROXY_URL` 命名，若确有网络出口需求另设明确配置。
- `process_item()` 的统一 `TextResult` 路径无需改变；捕获条目继续优先走 `BrowserCapture`，不调用服务器 connector。

### Browser companion

- 扩展和后端共享平台类型扩展为 `youtube | bilibili | ntu_kaltura`。
- `canonicalize_reference/page_url` 只允许 Bilibili 官方 HTTPS host，去掉查询/片段和潜在凭据；cover 只允许 Bilibili 官方静态图片 host，具体 host 需由 smoke test 证实后写入白名单。
- `captureBilibiliPage()` 仅在 Bilibili 视频页注入，优先读取页面公开 initial state/meta 与同源字幕资源；所有资源 URL 在浏览器内消费，最终 payload 只含 cue、语言、公开元数据和 hash。登录态失败返回 `no_caption`/`needs_extension`，不上传 cookie。
- `timestamp_url()` 第一版返回安全 canonical 页面 URL；只有实测可用的 Bilibili 时间参数才加入，避免制造不可点击引用。

### API/Web

- capabilities、OpenAPI schema、前端 platform label/filter 和详情页显示纳入 `bilibili`；生成文件需通过既有导出流程更新，不手写一份漂移的 schema。
- stable error code 只暴露 `invalid_url`、`unsupported_url`、`needs_extension`、`needs_asr`、`rate_limited` 等安全分类；不暴露 yt-dlp stderr、WBI 参数、signed URL。

## 3. 推荐交付顺序

1. 先做服务器 connector + URL 预检 + capability，获得无登录字幕样例的端到端 ready。
2. 再做扩展 Bilibili adapter 与 capture contract，覆盖登录字幕样例。
3. 最后处理多 P/番剧选择、短链、ASR 和更丰富的引用参数。

## 4. 回滚与兼容

- 平台 enum 已含 `bilibili`，无需删除或重命名已有值；新增代码可通过 connector registry 和 capability flag 关闭，保留旧 YouTube/Kaltura 读取。
- 若 Bilibili extractor 变更或风控升高，先关闭服务器 Bilibili admission，仍允许已存储条目检索和扩展捕获条目处理；不要删除历史 `ContentItem`。
- 扩展 manifest/API origin 不变，只增加页面 host/代码路径；旧扩展发送的两平台 payload 必须继续通过 schema。

## 5. 关键决策门

- 真实 BV 样例：公开视频、登录字幕视频、无字幕视频各一条。
- 是否接受旧 av/短链：以本地可验证规范化和安全重定向测试结果决定。
- Bilibili 图片 host、字幕格式和时间戳参数：必须通过 Chrome smoke/响应样本确认后才进入白名单和公共契约。
