# Benchmark 视频选择表

你只需要为每个槽位填写 `URL`。其余字段能判断就填，不能判断留空；后续会通过只读 metadata/subtitle probe 和人工观看共同补齐。

## 选择原则

- YouTube 6 个、Bilibili 6 个。
- 只选无需登录即可观看、非 DRM、普通单视频页面；Bilibili 暂不选合集中的非 P1 分集。
- 优先选择明确 Creative Commons、开放课程、官方项目/机构允许研究使用，或你本人拥有的视频。
- 不要全部选择内容差异很大的视频：最好有 3 组“主题相近但具体内容不同”的跨平台视频，方便测试检索能否区分。
- 至少 2 个长于 45 分钟；其余优先 5–30 分钟。
- 如果不确定字幕状态，不用检查，留空即可。
- 暂时不要下载视频、音频或字幕，也不要上传任何私有课程视频。

## YouTube

### YT-01｜英语课程 + 幻灯片

- URL：https://www.youtube.com/watch?v=3oAY1j5-KIg&list=PLULgBZmS3YWRXpqgJTOq9m_nU4oyEVyj4
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### YT-02｜普通话访谈/讲解 + 一般实景

- URL：https://www.youtube.com/watch?v=Fh5HeQN5kI8&list=PL_PjwQXfPuE40qjW10Uj2h-vYN1EqIqVJ&index=5&pp=iAQB
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### YT-03｜中英混合软件录屏 + 代码

- URL：https://www.youtube.com/watch?v=iSpWZzL2i1o&list=PLKudDWXjxzbuADo_tLA9angsEGwTJuqfX
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### YT-04｜英语图表/数据讲解

- URL：https://www.youtube.com/watch?v=pt5-G2KgHi8
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### YT-05｜普通话实景演示，最好有噪声

- URL：https://www.youtube.com/watch?v=2wsXC4HU8WI&list=PLKudDWXjxzbs3l_uZ6HhDfbS0wKKyYSvw
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### YT-06｜中英混合长视频 + 重复幻灯片

- URL：https://www.youtube.com/watch?v=dQw4w9WgXcQ
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

## Bilibili

### BI-01｜普通话课程 + 幻灯片

- URL：https://www.bilibili.com/video/BV1bx411M7Zx/?spm_id_from=333.1007.top_right_bar_window_custom_collection.content.click
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### BI-02｜中英混合访谈/技术分享

- URL：https://www.bilibili.com/video/BV1uw4m1S7Cd/?spm_id_from=333.1007.top_right_bar_window_custom_collection.content.click
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### BI-03｜普通话软件录屏 + UI 小字

- URL：https://www.bilibili.com/video/BV1AV4y1n7Zt?spm_id_from=333.1387.favlist.content.click
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### BI-04｜普通话图表/数据讲解

- URL：https://www.bilibili.com/video/BV13cSoYVEJ1?spm_id_from=333.1387.favlist.content.click
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### BI-05｜普通话实景/动手演示，最好有噪声

- URL：https://www.bilibili.com/video/BV1Nt4y1m7kJ?spm_id_from=333.1387.favlist.content.click
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

### BI-06｜中英混合长录屏/课程 + 重复画面

- URL：https://www.bilibili.com/video/BV1h64y1f7jz?spm_id_from=333.1387.favlist.content.click
- 权利/许可依据：
- 你希望能搜索到的内容（可选）：
- 备注：

## 不必严格匹配槽位

如果找不到某个完全匹配的视频，可以交换槽位。最终只需整体满足：

- 两个平台各 6 个；
- 英语、普通话、中英混合均有覆盖；
- 幻灯片、代码/UI、图表、实景均有覆盖；
- 字幕完整、部分/低质量、无字幕最终各有足够样本；
- 至少 3 组相似主题干扰对。

你也可以不编辑本文件，直接按下面格式在对话里分批发送：

```text
YT-01 https://...
BI-01 https://...
备注：……
```

一次发送 1–12 个都可以。收到 URL 后再做 metadata、字幕状态、公开性和许可核验；核验失败的样本会说明原因并请你替换，不会静默改用别的视频。
