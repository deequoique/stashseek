# WeMM-Embedding 多模态检索架构迁移

## 2026-10-15 方案变更：与视频 ASR 的关系

用户确认视频方案改为“多模态 embedding + ASR”，独立的 Vision/OCR 识别已移除。依据见已归档的 `archive/2026-10/08-24-intent-aware-video-recognition-routing/prd.md` 的“2026-10-15 方案变更”；ASR 实现由 `10-15-asr-transcription-pipeline` 负责。分工如下：

- **画面：** 本任务的多模态 embedding 负责画面检索；`inspect_media` 负责少量候选的按需核验。
- **语音：** 由 `08-24-intent-aware-video-recognition-routing` 任务树的 ASR 链路转成带时间戳文本，再进入现有的文本切分、向量化和引用链路。默认 Fun-ASR，备选 `whisper-1`。
- **检索路由（2026-10-15 用户决定）：** 不由 Agent 判断何时走 WeMM；每次检索固定 BM25 + 智谱向量 + WeMM 向量三路召回后合并。细节见 `09-09-multimodal-retrieval-rollout` 的 PRD 和 design。

## 目标

将 SearchSeek 当前“文本内容 -> 文本 embedding -> pgvector 检索”的架构扩展为文本、图片和视频片段可进入统一语义空间的多模态检索架构，并保留现有文本检索、租户隔离、引用和回滚能力。

## 用户价值

- 用自然语言检索图片和视频画面，而不依赖人工标题或字幕。
- 支持图片搜相似图片/视频、文字搜媒体、文本与媒体混合检索。
- 对复杂视觉问题只分析少量已召回候选，控制延迟和多模态模型成本。
- 视觉模型分析结果可缓存、可追溯、可与原始媒体和消息/内容片段关联。

## 范围

### 包含

- 引入 WeMM-Embedding provider 抽象和本地/服务化推理适配。
- 图片、视频片段的媒体资产模型、对象存储引用、哈希去重和向量索引。
- 文本、图片、视频的混合召回与结果归一化。
- 受限的 `inspect_media` Agent tool：只分析当前检索结果中的候选媒体。
- 多模态识别缓存、预算/数量上限、租户和权限边界。
- 文本向量兼容策略、离线回填、契约/功能测试、灰度开关和回滚；按用户决定不运行模型 benchmark。

### 不包含

- 直接读取微信数据库、绕过平台权限或处理私有媒体获取。
- 人脸识别、身份推断、敏感属性识别。
- 全量视频逐帧理解或自动生成完整视频解说。
- 用多模态大模型替代向量召回；大模型只做候选验证/精排和按需理解。

## 约束

- 当前 `Segment.embedding` 为 `vector(1536)`，现有文本 provider 只接受文本批量输入。
- PostgreSQL/pgvector 是现有检索真相源；迁移必须保持单 Alembic head。
- 关闭新能力后，现有文本摄取、检索、引用和 Agent 行为必须可用。
- 新媒体索引必须遵守现有用户隔离、删除、归档和内容状态条件。

## 验收标准

- [ ] 文本查询可召回文本、图片和视频媒体，媒体结果携带稳定的 `media_id`、来源和定位信息。
- [ ] 图片查询可召回相似图片和视频片段；以可控 fake 驱动端到端功能测试，真实推理 smoke 与模型 benchmark 分开记录。
- [ ] 现有文本向量路径继续工作，迁移期间不要求一次性破坏性重嵌入。
- [ ] `inspect_media` 只能读取当前检索 run 暴露的候选 ID，服务端强制数量、权限、超时和调用预算。
- [ ] Agent 对复杂视觉问题可以调用 `inspect_media`，简单语义检索不会无条件触发多模态模型。
- [ ] 相同媒体、问题和模型/prompt 版本命中缓存时不重复调用。
- [ ] provider 失败、媒体不可访问、重复任务、删除竞态和跨租户访问都有稳定测试覆盖。
- [ ] 迁移、回填、灰度和回滚均有 operator 可执行的验证命令和记录。

## 子任务地图

1. `09-09-media-embedding-ingestion`：先实现图片/视频抽样、WeMM 摄入、去重、异步任务和重试，暂不依赖新数据库表。
2. `09-09-inspect-media-agent-tool`：实现受限多模态识别 tool、缓存、证据结构和 Agent 路由，先使用内存/文件 fixture 验证契约。
3. `09-09-multimodal-retrieval-rollout`：统一候选、媒体证据及 feature flag 的离线集成；数据库落地后再次执行集成验收。
4. `09-09-media-asset-vector-index`：最后实现 additive schema、索引和回填；无本地数据库时只在生产迁移检查及备份确认后执行，不对生产跑破坏性测试。
5. `09-09-wemm-provider-benchmark`：按用户决定跳过，不计作已执行/通过；官方接口核对和必要功能测试由摄入子任务承担。

## 依赖与顺序

`media-embedding-ingestion` -> `inspect-media-agent-tool` -> `multimodal-retrieval-rollout` -> `media-asset-vector-index`。

`wemm-provider-benchmark` 不作为阻塞依赖；本阶段不执行基准测试。

父任务只维护跨子任务契约和最终验收；实现、测试、独立审查分别由子 agent 负责。此前检查只确认默认 PATH 中没有 PostgreSQL/Docker 工具，项目配置指向远程 Neon，不能据此断言整台机器绝无安装。无可用本地环境时可采用生产 additive migration，但必须保留旧表、字段、Segment 向量及历史数据；禁止 destructive downgrade、旧向量覆盖或对生产执行集成测试的数据清理。

## 上线前外部条件

- 2026-09-10 安全检查确认 `.env` 未配置 WEMM 运行参数；不输出、提交或传播现有数据库凭据。
- 官方本地推理接口必须有真实可用的模型运行环境/权重，或提供已验证契约的服务。内部 opaque ID 不能直接当作图片/视频发送给官方模型。
- 跳过 benchmark 不等于跳过真实接口正确性验证；fixture 通过不能报告成真实 WeMM 检索通过。
- 执行生产迁移前必须确认备份/恢复点、目标 revision 和迁移 SQL；未确认时只准备迁移文件，不写生产数据库。
