# Pilot 查询与媒体冻结候选（2026-08-28）

## 状态与边界

状态：`partial_freeze_applied`。本文件记录 4 个核心样本的 12 个查询槽位候选；2026-09-05 只将有直接证据的 query truth 和 media selections 应用到 `pilot_catalog.yaml`，未确认项保持 pending，也不授权 provider 调用。不得根据标题、主题或画面推断语音真值。

标为 frozen 的时间范围是视频绝对时间验收区间；候选范围不能当作执行真值。public-evidence profile selection、样本内容分类和正式 probe readiness 仍不在本次冻结范围内。

## 12 个候选槽位

| 样本 / 槽位 | 状态 | 候选查询与时间 | required key terms | 最小输入与证据 |
| --- | --- | --- | --- | --- |
| YT-01 / speech | `freeze_candidate` | “哪一段讲到计算机视觉用于谷歌地图街景和广告？”；`84–94s` | `computer vision`、`Google Maps Street View`、`advertising` | ASR `83–95s`；`88s` 可见字幕 cue 已核验 |
| YT-01 / visual | `freeze_candidate` | “讲师坐在显示机器学习应用幻灯片的屏幕旁边是哪段？”；`14–24s` | 讲师、显示器、`Applications of Machine Learning` | frame bundle `[18, 20, 50]`；`18s` 明确显示讲师与对应幻灯片，`20/50s` 为既有人工观察 |
| YT-01 / OCR | `still_pending` | “哪段幻灯片标题显示 Applications of Machine Learning？”；候选 `14–24s` | `Applications of Machine Learning` | 原“13 万亿美元”OCR 候选经用户人工回放否决；替换查询仍需确认 exact title 在 OCR frames `[18, 20]` 可见 |
| YT-03 / speech | `freeze_candidate` | “哪段讲到把 current object 交给 rest node？”；`347–354s` | `current object`、`rest node` | ASR `347–354s`；`350s` cue 已核验 |
| YT-03 / visual | `manual_confirmed_candidate` | “Godot 的 Input Map 设置界面出现在哪段？”；`52–60s` | `Godot`、`Input Map`、UI | 用户人工确认 frames `[54, 55, 56]` 均为 Input Map 同场景，且无广告、弹窗或切镜 |
| YT-03 / combined | `manual_confirmed_candidate` | “画面是代码编辑器，同时讲到 current object 和 rest node 的片段”；`326–354s` | code editor、`current object`、`rest node` | query truth 已由用户确认；frames `[328, 340, 352]` 是待逐帧确认的 media 候选 |
| BI-01 / speech | `manual_confirmed_candidate` | “哪一段讲到搭建一个能够识别手写数字的神经网络？”；`100–110s` | `neural network`、`recognize handwritten digits` | 用户人工听写 `1:40–1:50` 的英文旁白；原“多层节点 / 编号输出节点”措辞弃用 |
| BI-01 / OCR | `manual_confirmed_candidate` | “哪一段的中英文字幕同时提到能够识别手写数字的神经网络？”；`100–110s` | `识别手写数字`、`neural network`、`recognize handwritten digits` | 用户人工抄录同段中英文硬字幕；frames `[100, 102, 103]` |
| BI-01 / combined | `manual_confirmed_candidate` | “画面出现神经网络结构，同时旁白讲搭建能够识别手写数字的神经网络，是哪一段？”；`100–110s` | neural network visual、`recognize handwritten digits` | 用户人工确认神经网络结构画面、英文旁白及双语硬字幕在同一段出现；frames `[100, 102, 103]` |
| BI-06 / speech | `still_pending` | “哪段形容蛋糕外皮焦焦的、吃起来像冰淇淋？”；候选位于 `5–76s` | `外皮`、`焦焦的`、`冰淇淋` | 用户已人工听写短句，但未给出句级时间；不能由被测 ASR 生成 gold truth |
| BI-06 / visual | `manual_confirmed_candidate` | “用勺子按压并挖取蛋糕的是哪一段？”；`5–8s` | 勺子、按压、挖取、蛋糕 | 用户人工记录动作范围 `0:05–0:08`；候选 frames `[5, 6.5, 8]` |
| BI-06 / combined | `manual_confirmed_candidate` | “持续旁白时，画面用勺子按压并挖取蛋糕的是哪一段？”；`5–8s` | 勺子、按压、挖取、蛋糕 | 用户确认视频持续有旁白且 `0:05–0:08` 有对应动作；不主张特定短句与动作逐字同步 |

## 保守 distractors

- YT-01：`BI-01`。
- YT-03：`BI-02`、`BI-03`。
- BI-01：`YT-01`。
- BI-06：`YT-05`、`BI-05`；仅作为短时、视觉主导的弱干扰项，当前 regression pool 缺少同类烹饪视频。

## 仍需补齐的最小证据

1. YT-01：确认 `18/20s` 的 exact OCR title，并在 `14–24s` 内确认第三个视觉帧。
2. YT-03 visual 已完成；combined query truth 已完成，但 `328/340/352s` 三个媒体帧仍需确认。
3. BI-01 的人工听写、双语硬字幕和同场景代表帧已经补齐并冻结。
4. BI-06 无需完整转写；只需给已听写短句一个约 5–15 秒宽的实际时间范围。visual/combined 使用 `5–8s` 动作段。
5. provider execution 仍需完成上述 truth/media、public-evidence profile selection、样本内容分类和正式 probe readiness。

## 审计

- 本轮公开播放器页面导航：21 次（YT-01 13 次、YT-03 8 次）。
- YT-03 的部分复核被重复 15 秒广告阻塞；未通过下载或登录绕过。
- `provider_calls=0`；`media_downloads=0`；持久化字幕、截图和媒体均为 `0`。
- BI-01、BI-06 没有新增播放器检查，沿用 `media-plan-partial-proposal-2026-08-26.md` 中的有界视觉观察。
- 2026-09-05 用户人工确认记录见 `manual-pilot-review-input.md`；catalog 保持 `planning_only`，当前为部分 query/media freeze。
