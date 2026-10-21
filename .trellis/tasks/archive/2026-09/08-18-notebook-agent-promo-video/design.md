# 技术设计：Notebook Agent 横屏 Remotion 宣传视频

## 边界

视频工程放在独立的 `promo/remotion/` 目录，使用自己的 `package.json`、TypeScript 配置和 Remotion 入口；不把 Remotion 依赖引入现有 `web/` 运行时，也不修改 Web API 或产品页面逻辑。

## 组成

- `src/Root.tsx`：注册唯一横屏 Composition。
- `src/NotebookAgentPromo.tsx`：按绝对帧区间组合场景和全局音频/字幕。
- `src/scenes/`：每个宣传场景一个纯展示组件。
- `src/components/`：Logo、视频卡片、流程节点、聊天气泡、证据卡片、字幕等复用视觉组件。
- `src/data/`：场景时长、旁白文案、证据时间戳和演示数据；不包含真实用户数据。
- `public/assets/`：从现有 Web 资产复制或引用的 Logo，以及可选截图、录屏、旁白和背景音乐。

## 时间轴

统一 30fps、1920×1080。以帧数表达场景边界，避免组件内部依赖真实时间。场景顺序为 Opening → Telegram Intro → Telegram Save → Processing → Browser Companion → Telegram Question → Telegram Evidence → Link & Privacy → Ending，总时长目标 120 秒左右。旁白以原字幕语速约 1.25 倍为基准，字幕切成更短的 7–12 秒句段。

## 动画策略

- `interpolate`：透明度、位移、进度线和卡片位置等确定性动画。
- `spring`：Logo、按钮、证据卡片和节点的自然入场。
- `Sequence`：场景级时间编排。
- 场景组件只接收浏览器安全的展示数据，不读取 API、数据库或租户信息。
- 颜色复用 Web `styles.css` 中的米白、墨黑、砖红、绿色和黄色；字体使用系统无衬线与 Georgia 类衬线字体的可用替代。

## 音频与字幕

旁白和音乐通过可选 `staticFile()` 资源注入。字幕数据独立于音频，以秒或帧为单位维护；音频缺失时不阻塞视频渲染，字幕仍然显示。字幕和重要 UI 文字均限制在安全边距内。

## 可信表达

演示数据使用 Showcase 页面已有的公开示例问题、证据时间点或明确标注为模拟数据的内容。Telegram 保存结果、来源区块和 `/link web` 文案遵循真实 Agent/渠道契约；浏览器伴侣沿用真实弹窗文案及 YouTube/NTULearn/Kaltura 支持边界。不得暗示当前已支持尚未实现的 Bilibili/微信公众号导入。

## 验证与产物

先用 Remotion Studio 逐场景检查，再运行类型检查和 lint，最后渲染 H.264 MP4。最终产物目标路径为 `promo/remotion/out/notebook-agent-demo-promo-zh-v1.mp4`。
