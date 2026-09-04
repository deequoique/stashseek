# Bilibili URL/插件可行性调研（2026-08-18）

## 结论

可行性为“服务器导入高、浏览器登录态捕获中高、完整自动化低”。推荐先交付 yt-dlp 服务器 connector，再增加 Bilibili companion adapter 作为登录字幕兜底，把媒体下载、验证码绕过、弹幕/评论抓取和复杂番剧分集留在后续范围之外。

## 一手/代码证据

- 项目 README 明确当前只有 YouTube 具备端到端导入；Bilibili 仅在数据模型预留。
- `app/models.py` 的 Postgres `platform` enum 已包含 `bilibili`；`migrations/versions/6df2e721d7b2_init_schema.py` 也已创建该值，因此新增 connector 不需要新增平台值。
- `app/ingest/submission.py::normalize_item_reference` 目前只接受 YouTube host，`app/ingest/tasks.py::_connector` 目前只构造 `YouTubeConnector`；这是 URL 入口和 worker 选择器的主要缺口。
- `app/browser_capture.py`、`extension/src/protocol.ts` 与 `extension/src/page-capture.ts` 的平台联合类型只有 `youtube`/`ntu_kaltura`，server capability/OpenAPI 也只公开这两个值；这是插件适配的跨层缺口。
- 只读获取的 yt-dlp 官方 `supportedsites.md`（仓库主分支）列出 `BiliBili`、Bilibili category、Bangumi、Cheese、playlist、space、watchlater 等 extractor，说明 Bilibili URL 元数据/媒体信息已有成熟 extractor 覆盖。
- yt-dlp 官方 `yt_dlp/extractor/bilibili.py` 当前实现覆盖 `www.bilibili.com/video/BV...`，测试还包含旧 `av...`、合集多 P、festival `bvid` 和番剧条目；使用 `api.bilibili.com` WBI 签名接口获取 play info/章节/字幕信息；将字幕转换为 SRT，另提供 `comment.bilibili.com/{cid}.xml` 弹幕资源。
- 同一 extractor 在 `need_login_subtitle` 时明确提示“字幕仅登录可用”，并通过浏览器 cookie 中的 `SESSDATA` 判断登录态；官方测试包含“video has subtitles, which requires login”样例。

## 现有架构映射

```text
URL 预检 -> ItemReference(bilibili, id, canonical_url)
 -> create_item/唯一约束 -> BilibiliConnector.fetch_meta
 -> fetch_text(official subtitle, else NeedsExtension/NeedsASR)
 -> existing guard/chunk/embed/object-store/completion path
 -> transcript timestamp_url(bilibili)

登录态浏览器兜底 -> extension Bilibili page adapter
 -> local caption normalization + public metadata
 -> capture.v1 (no cookie/signed URL)
 -> existing BrowserCapture submission + same worker path
```

## 风险与待验证项

1. Bilibili 页面和 API 的 WBI/风控会随时间变化；必须锁定 yt-dlp 版本范围、记录稳定错误分类，并用真实 BV 样例做 canary。
2. 字幕不是每个视频都有，且登录可见字幕需要 Chrome 用户态；服务器 connector 不能借用 Web cookie。扩展需在页面本地读取官方字幕或渲染后的 TextTrack，并只传 cue。
3. 多 P/番剧/互动视频的“一条 URL 对应多个可索引条目”需要产品选择。MVP 只接受一个明确 page/part，遇到 playlist/season 返回可解释的 unsupported/needs_selection，不静默抓整套内容。
4. Bilibili 时间戳深链参数需真实页面验证；未验证前应保留 canonical 页面 URL，不能猜测 query 参数。
5. 本轮无法用 CDP 访问登录态页面（Node 16 且 Chrome 未连接），所以扩展 adapter 的 DOM/API 选择器、字幕格式和重定向行为仍是实现阶段的 smoke-test 门槛。

## 建议的 MVP 边界

- 支持 BV 与旧 av 单视频 URL；规范化到 `https://www.bilibili.com/video/{BV}`。
- 服务器端只获取元数据与官方字幕；不下载音视频、不抓弹幕/评论、不绕过登录。
- 无字幕/登录字幕分别映射到已有 `needs_asr`/`needs_extension` 语义，并在 UI/API 能力中公开 Bilibili 支持状态。
- 扩展先支持普通 Bilibili 视频页，成功读取页面公开元数据和字幕后复用 `capture.v1`；番剧、合集和动态页单独立项。

## 来源

- [yt-dlp supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [yt-dlp Bilibili extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
- [Bilibili video page](https://www.bilibili.com/video/BV1xx411c7mD)（本次只读请求返回 200；页面主体为压缩响应，未依赖其动态字段）

## 2026-08-20 实现期 canary

- 项目锁定的 yt-dlp `2026.07.04` 可无 Cookie 获取 `BV13x41117TL` 和
  `BV12N4y1M7rh` 的公开元数据；前者无字幕并稳定映射为 `NeedsASR`。
- yt-dlp 官方源码仍把 `BV12N4y1M7rh` 标注为“字幕需要登录”，但本次真实未登录响应
  没有返回 `need_login_subtitle`/warning，connector 因而只能观测为无字幕。这证明不能
  仅靠当前服务器响应保证区分“真实无字幕”和“登录后才显示字幕”。
- connector 已保留“上游明确报告登录字幕”时的 `NeedsExtension` 安全分支；浏览器
  companion adapter 仍是可靠覆盖登录字幕的必要后续，而不是服务器端 Cookie 方案。
- 多 P 样例 `BV1bK411W797` 在 `--no-playlist` 下只解析为 `_p1`，验证了当前 MVP
  不会批量展开合集；URL admission 同时拒绝显式 `p>1`。
