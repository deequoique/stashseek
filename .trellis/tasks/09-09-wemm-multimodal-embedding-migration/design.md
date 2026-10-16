# WeMM-Embedding 多模态检索架构迁移：技术设计

## 总体数据流

```text
文本/图片/视频摄入
  -> 内容规范化、哈希去重、媒体切片
  -> WeMM provider
  -> media_asset.embedding / 版本化文本向量
  -> pgvector + BM25/OCR/ASR

用户查询
  -> query embedding + lexical retrieval
  -> 统一候选集与元数据过滤
  -> 简单问题直接回答
  -> 复杂视觉问题调用 inspect_media
  -> 结构化视觉证据 + 最终回答
```

## Provider 边界

保留现有文本 provider 的兼容接口，同时新增多模态能力，不让业务代码依赖 WeMM 原始响应格式。输入包括文本、图片引用、视频片段引用；输出必须包含向量、维度、模型版本和协议版本。provider 不负责租户权限、业务状态或回答生成。

## 向量存储策略

第一阶段采用并行字段：保留 `Segment.embedding vector(1536)`，新增媒体资产向量字段或独立媒体向量表。WeMM 模型、维度和 provider 配置按当前决策直接冻结，不执行 benchmark。禁止直接改写现有文本字段。每个向量必须记录 `embedding_model`、`embedding_dimension`、`embedding_revision` 和内容哈希。

2026-09-10 上游接口核对后的默认配置建议为 `tencent/WeMM-Embedding-2B`、1024 维、固定模型 revision；旧智谱保持 1536 维。此选择不声称 benchmark 优于旧模型。WeMM 文本 query 与媒体向量使用相同模型/版本/归一化；两个模型空间的原始 cosine 分数不可直接比较，应分别排序后融合。

## 媒体资产

`MediaAsset` 代表原始图片、视频或视频片段；`Segment` 继续代表文本/字幕证据。媒体资产通过 `content_item_id`、可选 `segment_id`、对象存储 key、时间范围和版本关联。视频采用片段级向量，整体视频向量只作为补充召回，不作为精确时间证据。

## Agent Tool

`inspect_media` 只接收当前 retrieval run 产生的候选 `media_id`，由服务端解析真实对象并再次做租户/删除/归档校验。tool 返回结构化相关性、置信度、证据摘要、OCR/对象/动作字段和时间范围。Agent 可以选择是否调用，但服务端强制最大候选数、超时、重复调用去重和 prompt/model 版本化缓存。

## 回答证据边界

- 文本保留真实 `segment_id` 和 `[S...]`，媒体采用独立 `media_id`/媒体来源类型；不得伪造 Segment ID。
- 媒体-only 与混合结果需要真正进入 tool-free Composer 的证据选择、最终 allow-list 验证及服务端来源渲染。仅将对象存入 `ComposerDeps` 不算完成。
- feature off 时不出现媒体工具、provider 调用或新增空字段；旧文本回答和持久化结构保持兼容。
- raw embeddings 和发现候选不等于可回答证据；只有通过状态复查及 provenance 校验的媒体识别结果可被选为证据。
- frame sampling、声音/动作理解及时间定位必须区分：抽到的静态帧不能自动证明完整动作发生。

## 缓存与识别运行时

- 当前 turn-local cache 是阶段性保护，不满足跨 turn/worker 复用验收。
- durable cache 的身份应包含 tenant、媒体 hash/revision、标准化问题、模型及 prompt 版本，不包含一次性 run_id；读取后重新鉴权并绑定到新 run，绝不直接重用旧 run 的授权。
- 与已有 `08-24-intent-aware-video-recognition-routing` 任务共用未来 RecognitionJob/事件/自动续答所有权，不另造一套持久化识别系统。现阶段注入式 fake 同步服务不意味着已实现生产异步识别。
  - 2026-10-15：`08-24` 任务树已归档（`archive/2026-10/`），共用的 RecognitionJob、事件和自动续答架构不会再建。`inspect_media` 如需异步执行，要在本工作流内重新决定。
- 线程等待超时不等于模型请求被取消。生产适配必须提供 provider 原生超时/取消及全局并发上限，不能仅依靠 Future.cancel 保证成本上界。

## 回滚与兼容

数据库 schema 和迁移放在最后一个子任务实现。前置子任务必须能用 fixture、内存 fake 或现有文本表验证 provider、tool 和检索协议。当前没有本地数据库环境，生产迁移前必须完成 SQL 静态审查、Alembic 离线生成/校验、数据库备份或快照确认、锁与执行时长评估；生产只执行新增表、字段和索引等可前向兼容变更。旧的 Segment、embedding、内容元数据和历史记录必须原样保留。回填是可暂停、可重试、可按模型版本重建的后台任务，不覆盖旧向量。删除和归档优先于媒体识别结果发布，避免已删除内容重新进入检索。
