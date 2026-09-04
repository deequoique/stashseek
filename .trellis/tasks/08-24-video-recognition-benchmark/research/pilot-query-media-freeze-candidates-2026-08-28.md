# Pilot 查询与媒体冻结候选（2026-08-28）

## 状态与边界

状态：`partial_freeze_candidate`。本文件把 4 个核心样本的 12 个查询槽位整理为可复核候选，但不修改 `pilot_catalog.yaml`，也不授权 provider 调用。只有得到可见字幕 cue 或已人工观察画面支持的槽位标为 `freeze_candidate`；其余保持 `still_pending`，不得根据标题、主题或画面推断语音真值。

时间范围均为视频绝对时间的候选验收区间，不代表已经核实完整场景边界。正式冻结前仍需逐项复核查询措辞、范围、关键词和最小媒体输入。

## 12 个候选槽位

| 样本 / 槽位 | 状态 | 候选查询与时间 | required key terms | 最小输入与证据 |
| --- | --- | --- | --- | --- |
| YT-01 / speech | `freeze_candidate` | “哪一段讲到计算机视觉用于谷歌地图街景和广告？”；`84–94s` | `computer vision`、`Google Maps Street View`、`advertising` | ASR `83–95s`；`88s` 可见字幕 cue 已核验 |
| YT-01 / visual | `freeze_candidate` | “讲师坐在显示机器学习应用幻灯片的屏幕旁边是哪段？”；`14–24s` | 讲师、显示器、`Applications of Machine Learning` | frame bundle `[18, 20, 50]`；`18s` 明确显示讲师与对应幻灯片，`20/50s` 为既有人工观察 |
| YT-01 / OCR | `still_pending` | “哪段字幕提到 AI 和机器学习将额外创造 13 万亿美元？”；候选 `187–194s` | `AI and machine learning`、`13 trillion US` | 仅有 OCR 帧 `[190]`；`190s` 可见字幕 cue 已核验，缺相邻 2 帧 |
| YT-03 / speech | `freeze_candidate` | “哪段讲到把 current object 交给 rest node？”；`347–354s` | `current object`、`rest node` | ASR `347–354s`；`350s` cue 已核验 |
| YT-03 / visual | `still_pending` | “Godot 的 Input Map 设置界面出现在哪段？”；候选 `52–60s` | `Godot`、`Input Map`、UI | 样本级已观察 `[32, 55.9, 330, 350]`，但相关场景只有 `55.9s` 一个锚点，缺同场景 2 帧 |
| YT-03 / combined | `still_pending` | “画面是代码编辑器，同时讲到 current object 和 rest node 的片段”；候选 `326–354s` | code editor、`current object`、`rest node` | frames `[330, 350]`，ASR `[347, 354]`；缺第 3 帧，其余复核被重复 15 秒广告阻塞 |
| BI-01 / speech | `still_pending` | “讲到多层节点和编号输出节点的那一段”；候选 `100–110s` | ASR 术语待定 | 仅有 `105s` 节点/边图视觉锚点；没有实际听取或平台字幕 cue，无音频输入可冻结 |
| BI-01 / OCR | `still_pending` | “多层节点图下方同时出现中英文字幕的片段”；候选 `100–110s` | 精确字幕文字待定 | 仅 OCR 帧 `[105]`；确认存在双语硬字幕，但未核对精确文字且缺相邻帧 |
| BI-01 / combined | `still_pending` | “先后出现像素格数字 3 和多层节点图的讲解”；候选 `25–110s` | 像素网格、数字 `3`、节点、边 | frames `[30, 105, 301]` 均已观察；没有音频证据，不能冻结 combined truth |
| BI-06 / speech | `still_pending` | “讲到加入淡奶油并过筛搅拌的是哪段？”；时间待定 | 淡奶油、过筛、搅拌 | 无音频或字幕 cue；既有视觉观察不能证明说了这些词 |
| BI-06 / visual | `freeze_candidate` | “前半段准备容器、铺油纸并加入配料的步骤”；`5–76s` | 容器、油纸、砂糖、淡奶油 | frames `[5, 21, 46, 76]`；前期阶段已人工观察，单一动作与单帧的精确对应仍需最终复核 |
| BI-06 / combined | `still_pending` | “后半段从搅拌、烘烤成品到切块的步骤”；候选 `106–151s` | 搅拌、烘烤成品、切块 | frames `[106, 136, 151]` 已观察；没有音频证据，不能冻结 combined truth |

## 保守 distractors

- YT-01：`BI-01`。
- YT-03：`BI-02`、`BI-03`。
- BI-01：`YT-01`。
- BI-06：`YT-05`、`BI-05`；仅作为短时、视觉主导的弱干扰项，当前 regression pool 缺少同类烹饪视频。

## 仍需补齐的最小证据

1. YT-01 OCR：在 `190s` 附近补 2 个实际观察帧并核对精确屏幕文字。
2. YT-03 visual：在 `55.9s` 的 Input Map 场景补 2–5 个同场景观察帧。
3. YT-03 combined：在 `330–350s` 代码场景补至少 1 帧；避开广告后重新核对场景边界。
4. BI-01：核对 `105s` 附近精确双语硬字幕，并获得严格有界、人工听取的语音窗口；在此之前 speech/combined 不得冻结。
5. BI-06：获得严格有界、人工听取的语音窗口；在此之前 speech/combined 不得冻结。
6. 所有 `freeze_candidate` 仍需第二人或独立回放复核，之后才能写入 frozen pilot revision。

## 审计

- 本轮公开播放器页面导航：21 次（YT-01 13 次、YT-03 8 次）。
- YT-03 的部分复核被重复 15 秒广告阻塞；未通过下载或登录绕过。
- `provider_calls=0`；`media_downloads=0`；持久化字幕、截图和媒体均为 `0`。
- BI-01、BI-06 没有新增播放器检查，沿用 `media-plan-partial-proposal-2026-08-26.md` 中的有界视觉观察。
