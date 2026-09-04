# 适配 Bilibili URL 插件

## Goal

让用户可以把受支持的 Bilibili 视频 URL 当作知识库条目保存，并沿用现有的“元数据 → 字幕/文本 → 分段 → 向量索引 → 可引用时间戳”流程；浏览器 companion 插件在需要用户登录态时提供本地捕获兜底。

## Requirements

- 在不触发远程请求的 URL 预检阶段识别 Bilibili 视频，至少覆盖 `https://www.bilibili.com/video/BV...` 与已验证的旧 `av...` 形式；短链只在安全、可验证的重定向策略确定后纳入范围。
- 服务器 connector 必须复用现有 yt-dlp 运行时边界，输出统一的 `ItemMeta`、`Cue`、`TextResult`/`NeedsASR`/`NeedsExtension`，不把 cookie、签名 URL 或原始第三方错误传入数据库、队列或日志。
- 字幕优先使用 Bilibili 官方字幕；明确处理“字幕仅登录可用”、无字幕、429/风控、单视频多 P/番剧等情况。MVP 不下载媒体文件，不绕过登录或验证码。
- 将 Bilibili 纳入浏览器 companion 的平台协议、URL/封面白名单、时间戳链接、扩展 page adapter 和能力声明；浏览器捕获只上传规范化 cue 与公开元数据。
- 保持现有 YouTube、NTULearn/Kaltura 路径的行为、租户隔离、幂等、限额和错误码不变；迁移必须确认已有 `platform` enum 值可用且无需破坏性改表。

## Acceptance Criteria

- [ ] 设计文档明确服务器直连、浏览器捕获和 ASR 兜底的职责边界，并给出可回滚的分阶段交付顺序。
- [ ] 正常 BV URL 可在本地预检中得到稳定的 `platform=bilibili`、规范化 ID 和去查询参数 canonical URL；恶意 host、凭据、模糊 ID、未支持链接安全失败。
- [ ] 有官方字幕且无需登录的样例可走服务器 connector 到 `ready`；字幕仅登录或无字幕时得到稳定的 `needs_extension`/`needs_asr`，不会伪装成成功。
- [ ] 需要登录态的样例可由配对扩展在 Bilibili 页面本地提取字幕后，以 `capture.v1` 上传并完成同一入库链路；payload 不含 cookie、签名资源 URL 或内部 ID。
- [ ] 文本详情和引用能生成 Bilibili 原站时间戳/页面链接；旧 YouTube 与 Kaltura 引用回归测试保持通过。
- [ ] 后端、扩展、Web capability/schema、文档和测试均覆盖平台枚举；完整质量门禁（pytest、扩展 TypeScript/lint/build、OpenAPI 一致性）通过。

## Constraints

- 当前环境的 CDP 前置检查未通过（Node 16、Chrome remote debugging 未连接），因此本轮联网证据采用只读的 Bilibili 页面响应与 yt-dlp 官方源码/支持列表；真实登录态字幕捕获必须在实现阶段由人工 Chrome smoke 验证。
- 继续遵守 browser-companion-capture 与 youtube-connector spec 的安全边界，不把 Bilibili 视为可以共享 YouTube signed URL/cookie 的特例。
