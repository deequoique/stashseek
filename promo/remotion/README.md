# Notebook Agent 横屏宣传视频

这是一个独立的 Remotion 工程，用 React/TypeScript 生成约 120 秒、1920×1080、30fps 的中文宣传视频。真实演示主线包括 Telegram/LangBot 接入、Telegram 保存与问答、带时间点的来源回答、浏览器伴侣字幕提交，以及 Telegram/Web 账户绑定。它不会向现有 `web/` 应用引入运行时依赖。

## 安装和预览

```bash
cd promo/remotion
pnpm install
pnpm studio
```

Studio 中选择 `NotebookAgentLandscape` 即可逐帧预览。

## 渲染

先渲染半尺寸预览：

```bash
pnpm render:preview
```

确认节奏和文字后渲染 1920×1080 成片：

```bash
pnpm render
```

最终文件：

```text
out/notebook-agent-demo-promo-zh-v2.mp4
```

## 旁白和音乐

默认版本没有音频，字幕仍会完整显示。加入音频时，把文件放在：

```text
public/assets/audio/narration.mp3
public/assets/audio/music.mp3
```

然后在 `src/Root.tsx` 的 `defaultProps` 中配置相对于 `public/` 的路径：

```tsx
defaultProps={{
  narrationSrc: 'assets/audio/narration.mp3',
  musicSrc: 'assets/audio/music.mp3',
}}
```

背景音乐默认音量为 12%，旁白为 100%。正式发布前应检查音乐授权和中文旁白的字幕同步。

## 修改内容

- 总时长和场景边界：`src/timeline.ts`
- 字幕：`src/data/subtitles.ts`
- 演示视频与证据：`src/data/demo.ts`
- 视觉颜色与字体：`src/theme.ts`
- 各段动画：`src/scenes/`

旁白按普通字幕语速约 1.25 倍编排；如果录音速度仍有差异，应优先调整 `src/data/subtitles.ts` 中的 cue 边界，再修改场景总时长。

当前端到端导入能力以 YouTube 为主。多入口文案表示部署中已经启用的 MCP、Web、Telegram 或微信入口，不应解读为所有部署默认开启全部渠道。
