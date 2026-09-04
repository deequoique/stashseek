# 按意图按需分配视频识别能力：父任务设计

## 状态与所有权

这是跨子任务架构与集成契约。具体 schema、组件和执行清单由对应子任务设计拥有。父任务不直接修改业务代码，也不在子任务通过各自 planning gate 前启动实现。

## 已确认的产品模型

```text
视频入库
  -> 有效带时间戳字幕？
       -> 是：生成时间段级 Discovery Segments
       -> 否：异步采样音频与代表帧
             -> 不收敛时自动升级完整类型相关处理
  -> partial / complete / failed 发现覆盖

用户搜索
  -> 只读取已发布 Discovery Segments
  -> 返回候选视频与相关时间段

用户追问细节
  -> 主 Agent 结构化意图路由
  -> 检索已有 answer-eligible 证据
  -> 证据足够：直接回答
  -> 需要新识别：立即返回 recognition_pending
       -> 后台任务阶段事件
       -> 新问题可中断前台等待，但不取消任务
       -> 完成后自动续答原问题
```

## 不变的跨阶段决策

- 元数据只能辅助排序，不能单独完成预处理；索引必须带 `start_sec/end_sec`。
- 完整有效字幕直接建立索引；部分字幕只补缺口；无有效字幕必须采样音频和画面。
- 采样结合时间覆盖、语音活动、场景变化、感知去重和局部加密；不能收敛时自动升级。
- 视觉理解以搜索召回和时间定位为目标，不预先生成逐帧详解。
- 多模态视觉负责语义，独立 OCR 只在文字密集画面或精确文字问题中触发。
- 新识别一律异步；缓存命中可以同步回答。
- 意图识别复用现有主 Agent，以确定性提示和服务端证据门约束，不增加独立分类 LLM 调用。
- 首期只支持后端可直接访问的公开 YouTube/Bilibili；登录态、私有和 DRM 媒体不在范围。
- 首期托管 ASR/视觉/OCR，本地抽帧、场景检测、文字密度判断和去重；本地 OCR 只作 benchmark/降级对照，供应商由 benchmark 决定。
- 首期不做费用预算或额度准入，只记录用量、延迟和估算费用。
- Web/插件功能迁移属于其他任务。

## 子任务契约

### 1. Benchmark → 所有下游

输出版本化 fixtures 和选择报告：

- ASR：带时间戳片段、语言和模型版本；
- Vision：场景类型、短搜索描述、搜索词和帧时间；
- OCR：文字、坐标、置信度和帧时间；
- Discovery Segment、RecognitionJob 和错误词汇；
- 字幕质量、采样工作量和模型质量门建议。

### 2. Index foundation → Media/Runtime/Retrieval

输出：

- 字幕质量与覆盖区间协议；
- `none | partial | complete | failed` 发现生命周期；
- active revision 与 Segment provenance/answer eligibility；
- 旧 ready/needs_asr/failed 条目的兼容行为。

### 3. Media sampling → Recognition runtime

输出平台无关、临时且有清理意图的 `MediaSampleBundle`：目标区间、音频对象、代表帧、场景/文字密度信号、媒体版本和安全错误。不得输出 Cookie、令牌、签名 URL 或原始 stderr。

### 4. Recognition runtime → Retrieval/Agent

输出：

- 幂等 RecognitionJob 和真实阶段事件；
- ASR/OCR/视觉时间轴证据；
- 原子发布的 Segment revision；
- at-least-once completion event，仅携带内部 ID 和稳定状态。

同时提供服务器本地 Recognition CLI：operator 可用 `probe/status/events/watch/result/cancel` 创建异步测试 Job、观察事件和读取结果。`probe` 允许直接测试公开 YouTube/Bilibili 与显式模态但不发布索引；CLI 调用同一 runtime service，不新增公网接口或同步创建路径。

### 5. Retrieval → Agent

提供两个逻辑读面：

- `search_video_candidates`：发现候选，可以返回 partial 覆盖和非 answer-eligible 摘要；
- `search_segments`：只返回当前 revision、answer-eligible 且满足所需模态的证据。

候选摘要不能进入详细 Citation cache；字幕不能满足显式视觉/OCR/声音问题。

### 6. Agent continuation → Rollout

输出 pending turn、可信目标快照、阶段观测、中断语义和自动续答终态。多个 pending 可以乱序完成，但必须关联回各自原消息。

## 阶段顺序

```text
Benchmark
  -> Index foundation
  -> Public media sampling
  -> Recognition runtime
  -> Retrieval/evidence gate
  -> Agent continuation
  -> Rollout/backfill
```

上游 contract 未冻结时，下游只能规划和使用 fixtures，不得绑定临时供应商响应或未迁移表结构。

## 集成与回滚边界

- PostgreSQL 是索引、任务、事件和 pending answer 的真相源；Celery 消息只携带内部 ID。
- 新 Segment revision 全部写完后再原子切换 active revision。
- pending 普通历史只读取 completed turn；续答使用原问题和服务器验证快照，不使用后来消息重建目标。
- 每个子任务必须在关闭 feature flag 时保持旧字幕摄取/检索可用。
- schema 采用前向兼容迁移；代码回滚保留新表、任务、证据和清理意图，不做破坏性 downgrade。
- 最终 rollout 子任务才允许历史公开 `needs_asr` 回填；私有/登录态条目始终排除。

## 父任务最终集成验收

1. 公开 YouTube 和 Bilibili 各覆盖完整字幕、部分字幕和无字幕样本。
2. 内容搜索只能引用已发布时间段，partial 结果明确覆盖不完整。
3. 搜索返回正确视频和可接受时间范围，质量门优先于费用。
4. 显式视觉/OCR/声音问题不会被错误文本证据直接回答。
5. 新识别立即 pending；阶段事件真实；新消息不被阻塞；完成后续答原问题。
6. 重复任务、at-least-once 事件、删除/恢复、失败和乱序完成均不重复计费、不串答、不越租户。
7. 关闭新能力后，旧字幕条目、检索、引用和完成通知仍可运行。
