# OpenAI 预标注结果摘要（仅供人工审核）

完整候选结果见 `openai-annotation-assist-2026-09-05.md`。本摘要不替代完整报告，也不构成 benchmark truth。

## 运行状态

- 运行日期：2026-09-05
- 结果：11/11 records completed；6 个 ASR 窗口、5 组视觉请求
- 模型：ASR `whisper-1`；Vision `gpt-5.6-luna`
- 临时媒体：已删除
- 正式 pilot：仍 `execution_gate=closed`，`external_calls=0`，`planned_default_units=0`

## 可供人工复核的候选

- YT-01 ASR：`83.0–88.42s` 命中 computer vision、Google Maps Street View、advertising。
- YT-03 ASR：`347.0–349.08s` 命中 current object、rest node；后续还提到 `distanceTo`。
- BI-01 ASR：`100.0–106.04s` 命中搭建能识别手写数字的 neural network。
- BI-06 ASR：`7.06–8.74s` 识别到“吃起来和冰淇淋一样”；分段窗口覆盖 `5–76s`。
- YT-01 Vision/OCR-assist：三帧支持讲师、显示器和 `Applications of Machine Learning` 标题；文字为视觉辅助读取，不是正式 OCR 结果。
- YT-03 Input Map Vision：三帧均显示 Godot Input Map；中间帧出现确认对话框，后一帧显示 `Device 0, Left Button`。
- YT-03 code-editor Vision：三帧显示 Godot/GDScript 编辑器，包含 `shortest_dist`、`rest_nodes`、`distance_to` 等可搜索文字；末行可能尚未完整输入。
- BI-01 Vision/OCR-assist：三帧显示神经网络动画/数字识别结构和中英文硬字幕；细小文字、节点数量和部分字符存在不确定性。
- BI-06 Vision：三帧确认勺子接触/按压蛋糕，但不能单凭静帧确认“已挖出一勺”。

## 约束

这些候选不得自动写回 `pilot_catalog.yaml`、不得计分、不得冻结 query truth/media，也不得作为正式 OCR provider 结果。人工审核时只确认可接受时间范围、关键词、屏幕文字和画面动作。
