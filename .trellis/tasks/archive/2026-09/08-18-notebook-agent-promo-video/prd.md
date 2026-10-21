# 制作 Notebook Agent 横屏 Remotion 宣传视频

## Goal

为 Notebook Agent 制作一支约 2 分钟、仅面向 16:9 横屏发布的 Remotion 宣传演示视频。视频需要清楚展示产品从保存 YouTube 来源、后台解析和索引，到自然语言提问、返回带时间戳证据回答的完整价值链。

## Requirements

- 在仓库内建立独立的 Remotion/TypeScript 视频工程，不破坏现有 `web/` Vite 应用。
- 使用项目现有 Logo、色彩和中文产品文案，保持与 Notebook Agent Web 视觉一致。
- 真实主流程必须以 Telegram/LangBot 为主要交互入口：介绍接入关系，在 Telegram 中保存 YouTube 链接、自然语言提问，并在 Telegram 回答中展示服务器附加的来源链接和时间点。
- 演示可选的浏览器伴侣：从用户当前打开的 YouTube 或 NTULearn/Kaltura 页面读取字幕，点击“保存当前视频”后只提交字幕和公开元数据。
- Web 只展示资料库管理、插件批准或跨渠道绑定等真实能力，不模拟当前产品中不存在的主要问答页面。
- 实现约 9 个高密度场景：开场痛点、Telegram 接入、Telegram 保存、后台处理、浏览器伴侣、Telegram 提问、证据回答、绑定与隐私、片尾品牌收束。
- 支持 Logo、产品截图/录屏、旁白音频、字幕和演示数据作为可替换素材；首版即使没有旁白音频也能渲染有字幕的无声版本。
- 所有动画通过代码时间轴控制，包括淡入、卡片堆叠、进度节点、打字机文本、证据卡片和场景转场。
- 只实现横屏 Composition：1920×1080、30fps、约 120 秒；不要求竖屏适配。
- 旁白按普通字幕语速的约 1.25 倍设计；缩短静态停留、增加场景内状态变化，保持约 120 秒总时长但提高信息密度。
- 宣传内容必须符合当前能力边界：端到端导入以 YouTube 为主；回答基于检索证据并带原视频时间点；数据按账户隔离。
- 提供本地预览、静态检查和 MP4 渲染命令，并补充使用说明。

## Acceptance Criteria

- [x] 可在独立视频工程中启动 Remotion Studio 并预览横屏成片。
- [x] 9 个主要场景均有可见内容和明确的中文屏幕文字，完整时长约 120 秒。
- [x] 项目 Logo 使用仓库已有资源，颜色和字体层级与现有 Web 视觉一致。
- [x] 场景间无重叠、无黑帧或未处理的资源加载错误；字幕不会溢出 1920×1080 安全区。
- [x] 旁白音频为可选资源：存在时能按时间轴播放，不存在时仍可正常渲染字幕版。
- [x] `pnpm typecheck`、`pnpm lint`（若工程提供）和 Remotion render 命令通过。
- [x] 生成一个可播放的 `notebook-agent-demo-promo-zh-v1.mp4` 横屏文件或明确记录渲染产物路径。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
