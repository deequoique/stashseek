# 视频识别公开基准选型与产品验收：设计

## 状态

本任务已进入 `in_progress`。2026-08-26 经用户确认，从“全候选内部 benchmark”收缩为“公开证据选型 + 小规模产品验收”。原正式 catalog gate 继续关闭，直到新的 pilot manifest 冻结。

## 数据流

```text
公开 benchmark / 官方模型卡
  -> 按语言、领域、协议要求筛选 default + fallback
  -> synthetic/API compatibility smoke
  -> 4 个核心样本（必要时 +2 风险扩展）
  -> 有界音频/代表帧/OCR 帧
  -> default 模型产品验收
       -> 通过：停止该模态比较
       -> 失败：按预先原因触发一个 fallback
  -> Discovery Segments + 隔离检索
  -> 查询→视频→时间段产品门槛
  -> 默认模型/blocked 结论

12 视频 regression pool
  -> 后续版本回归或线上失败补样
```

## 公开证据层

每条外部证据统一记录：模态、benchmark/模型卡、版本/查询日期、数据语言和场景、指标定义、候选成绩、来源性质（独立/供应商）、局限和本产品结论。不得把不同数据集的 WER、准确率、OCR 分数或综合多模态分数直接平均。

公开证据负责减少候选数量，不负责替代协议和搜索验收：

- ASR 重点看普通话、英语、中英混合、噪声、专有词和时间戳能力。
- Vision 重点看多图理解、图表/UI/代码、场景文字和结构化输出。
- OCR 重点看中英文、小字、场景文字、坐标和置信度。

每种模态冻结一个 default 和一个 fallback。fallback 不自动运行，只有 default 在兼容性或产品门槛失败时触发。

## Pilot manifest

- `regression_pool`：保留原 12 个公开 URL 及 probe/权利记录，不要求首轮媒体/查询真值完整。
- `core_acceptance_set`：`YT-01`、`YT-03`、`BI-01`、`BI-06`。
- `risk_extension_set`：`YT-04`、`BI-04`；只有核心集没有覆盖长视频/partial 字幕或密集 UI 风险时纳入。
- 每个入选样本 3 条查询；查询类型跨样本总体覆盖 speech、visual/OCR、combined，每片至少一条需要时间定位。
- 每个入选样本只冻结实际查询所需的音频窗口和 frame/OCR bundle；不再机械要求所有样本各两段音频和两组帧。

## 候选适配边界

- `ASRBenchmarkAdapter` 只接受本地有界音频，输出片段/词级时间戳、文本、语言和模型版本。
- `VisionBenchmarkAdapter` 接受带时间的代表帧，输出 schema 约束的场景类型、短描述和搜索词。
- `OCRBenchmarkAdapter` 接受本地代表帧，统一输出文字、坐标、置信度、帧时间、供应商/模型版本和用量。
- adapter 返回标准化结果或稳定错误；原始 SDK 响应只允许存在于 marked 临时目录并在 run 后清理。

这些协议是后续生产 fixtures 来源，但 harness 不写生产数据库、不发布 Celery 任务。

## 产品验收与 fallback

默认模型先运行一次。只有以下情况允许补跑：

- 网络/供应商临时失败：按稳定错误策略最多补 2 次，不计为质量比较样本。
- 同一输入出现协议或语义不稳定：最多补 2 次确认稳定性，结果全部保留终态。
- default 未通过 hard gate：运行预先冻结的单一 fallback；不得临时挑选第三个候选追求更好分数。

先比较标准化结果对搜索的实际贡献：正确视频 Top-3、时间范围命中/重叠、关键术语召回、schema、证据隔离。通过后才报告端到端耗时、供应商失败、音频分钟/图片/token 用量和估算费用。

## Provider smoke 边界

- 只使用代码生成的极小合成 WAV 和极小合成测试图，不读取 catalog、公开视频、字幕或冻结媒体目录。
- OpenAI smoke 已验证 `whisper-1` 与 `gpt-5.6-luna/terra/sol`；结果只证明接口连通性。
- 默认关闭外部调用，必须同时通过专用 CLI 命令和显式确认开关开启；该开关不能复用或打开 pilot provider gate。
- Responses 设置 `store=false`；不持久化原始响应、请求媒体或完整识别文本。

## 媒体、安全与失败

- 只对 pilot 实际入选样本提取查询所需的最小音频窗口和帧；其余 regression pool 样本保持 metadata/probe 状态。
- 不处理私有、登录态或 DRM 内容，不把完整视频、完整音频、完整字幕或帧集写入仓库。
- provider key 只来自环境，不写 fixture、报告或日志。
- 托管服务接收媒体 bytes，不提交视频 URL、签名 URL、页面身份信息、Cookie 或账户信息。
- 日志只记录脱敏 ID、阶段、数值、稳定错误和经过校验的 HTTP 状态。
- 网络/供应商失败与产品质量失败分开统计；marked 临时媒体和原始响应在每次 run 后验证清理。

## 兼容与迁移

现有 12-sample catalog/probe 保留为 regression-pool provenance。实现将新增 pilot selection/query/profile 状态，而不是删除原 12 个 identity。旧 dry-run 固定规模只用于迁移前回归；迁移后 dry-run 依据冻结 pilot 计算计划单元。

回滚时可恢复旧 catalog/schema 版本；不会影响生产路径，因为本任务仍与生产 ingestion/queue/database 隔离。

## 下游交付

选择报告输出：公开证据矩阵、default/fallback、产品验收指标、标准化 schema、成功/失败 fixtures、采样/字幕建议、模型配置和官方来源。父任务将这些文件加入阶段 2–4 的 Trellis 上下文。
