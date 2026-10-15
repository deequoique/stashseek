# 按意图按需分配视频识别能力：父任务集成计划

## 父任务规则

- 父任务不直接实施业务代码，保持 `planning`，直到所有子任务完成。
- 每次只激活当前阶段的一个子任务；下游依赖写入子任务 PRD/设计，不通过目录顺序隐含。
- 每个复杂子任务必须单独完成 `prd.md`、`design.md`、`implement.md` 和上下文清单，并经过用户评审后再 `task.py start`。

## 阶段检查点

- [ ] 1. `08-24-video-recognition-benchmark`：冻结真值、质量门、默认模型和供应商无关 fixtures。
- [ ] 2. `08-24-discovery-index-foundation`：发布兼容的时间段索引、字幕质量和覆盖生命周期。
- [ ] 3. `08-24-public-video-media-sampling`：交付公开 YouTube/Bilibili 的安全 MediaSampleBundle。
- [ ] 4. `08-24-multimodal-recognition-runtime`：交付异步 RecognitionJob、模型适配、时间轴融合和完成事件。
- [ ] 5. `08-24-discovery-retrieval-evidence-gate`：交付候选检索与详细证据隔离。
- [ ] 6. `08-24-async-recognition-agent-continuation`：交付 pending、事件观测、中断和自动续答。
- [ ] 7. `08-24-video-recognition-rollout-backfill`：完成灰度、canary、历史公开条目回填和回滚演练。

## 跨阶段审查

- [ ] 每个上游子任务归档前，输出的 contract/fixture 已加入直接下游的上下文清单。
- [ ] Segment、RecognitionJob、事件、错误码和模态词汇只存在一个版本所有者，没有平行实现。
- [ ] 每阶段关闭 feature flag 后，旧字幕摄取、检索和引用回归通过。
- [ ] 私有/登录态媒体、Web/插件迁移和费用预算没有从子任务中意外扩张进首期范围。

## 最终集成验收

- [ ] 运行父 PRD 的端到端场景矩阵和全量回归。
- [ ] 验证公开 YouTube/Bilibili 的字幕完整、部分和无字幕路径。
- [ ] 验证搜索视频/时间段、按模态 pending、状态汇报、新消息中断和自动续答。
- [ ] 验证 tenant、删除/恢复、重复 delivery、并发 pending、乱序完成和 provider failure。
- [ ] 验证单一 Alembic head、部署 admission、运行依赖、safe diagnostics、灰度开关和回滚。
- [ ] 只有全部子任务归档且集成验收通过后，父任务才可归档。
