# Agent 异步识别与自动续答

## Goal

在现有主 Agent 中实现结构化模态路由、recognition_pending、事件观测、中断和 PendingAnswer 自动续答。

## 依赖

- Recognition runtime 的 Job/event contract。
- Retrieval/evidence gate 的两个读面与模态充分性结果。

## 需求

- 确定性高置信提示 + 现有主 Agent 结构化工具路由 + 服务端证据门；不增加独立分类 LLM。
- 所有新识别立即 pending；缓存命中可同步回答。
- pending turn 不进入普通 completed 历史；保存原问题和服务器验证目标快照。
- 阶段事件驱动汇报，不保持长模型请求、不高频轮询、不伪造百分比。
- 新用户问题中断前台等待但不取消任务；完成后重新校验并续答原问题。
- 多 pending 可乱序完成，不串答、不复用最新消息重建旧目标。

## 验收标准

- [ ] text/audio/vision/ocr/组合/澄清路由通过离线意图集。
- [ ] 新识别请求在固定短路径内返回 pending，断线不取消任务。
- [ ] 新消息即时处理；旧任务完成后答案关联回原消息。
- [ ] 重复事件、两个并发 pending、乱序完成和 continuation claim 崩溃均幂等。
- [ ] 删除/权限失效/provider failure 产生稳定终态，不编造答案。

## 不在范围

- 模型执行、索引 schema、UI/插件迁移和历史回填。

## Planning Gate

Recognition 与 Retrieval 两个上游完成后单独补齐设计与实施计划。
