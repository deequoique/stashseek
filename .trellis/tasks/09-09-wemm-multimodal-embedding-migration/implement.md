# WeMM-Embedding 多模态检索架构迁移：执行计划

## 阶段 1：协议冻结

- [ ] 按当前决策冻结 WeMM 的文本/图像/视频输入协议、输出维度、批处理和错误映射。
- [ ] 不执行 benchmark；只保留可重复的 contract fixture 和后续回归入口。

## 阶段 2：无 schema 的 provider 与摄入逻辑

- [ ] 在不依赖新数据库表的前提下实现 provider、媒体规范化、抽样和任务契约。
- [ ] 使用 fake provider、fixture 和对象存储测试摄入幂等、重试、失败恢复。

## 阶段 3：Agent tool 与检索协议

- [ ] 实现 `inspect_media` 的候选范围校验、缓存、预算和结构化证据。
- [ ] 实现统一候选协议和 feature flag，不改变旧文本检索默认路径。

## 阶段 4：生产数据库迁移

- [ ] 生成并审查新增 schema、migration 和 pgvector 索引 SQL；不删除或改写旧表、字段或向量。
- [ ] 使用 Alembic offline 模式校验 revision、单 head、SQL 顺序、锁风险和可重复执行语义。
- [ ] 确认生产数据库备份/快照和恢复责任人，再执行 additive migration。
- [ ] 迁移后检查旧表行数、旧向量非空率、关键索引、租户隔离和应用读路径。
- [ ] 迁移成功后才启动极小批量媒体回填；旧文本数据保持只读兼容。

## 阶段 5：生产发布

- [ ] 生产迁移前完成数据库备份、迁移 SQL 审核、版本锁定和回滚预案。
- [ ] 先准备关闭 feature flag 的候选代码；按部署规范执行一次性迁移 admission，成功后启动依赖新 schema 的候选服务。无本地数据库时不虚报本地 migration 测试通过。
- [ ] 以小租户/小批量回填启动，观察错误率、延迟、向量写入和隔离指标。
- [ ] 任何 migration 或回填异常都停止后续批次，不执行未经验证的现场修复。

## Review gates

- WeMM 配置直接冻结，不以 benchmark 结果作为启动条件。
- 数据模型和 migration 是最后一个实现子任务；没有本地数据库时，改用 SQL 静态审查、备份确认和生产 additive migration 的分步校验。
- `inspect_media` 未完成候选范围校验前，不开放 Agent 调用。
- 新能力未通过本地集成和 feature flag off 回归前，不开启生产流量。

## 验证命令

- `python3 ./.trellis/scripts/task.py validate 09-09-wemm-multimodal-embedding-migration`
- 相关子任务的 focused pytest、Alembic offline SQL 校验、provider contract tests。
- 混合检索功能/契约测试与 feature flag off 回归；不运行 benchmark。

- 生产迁移后的旧数据保留核对和小批量回填 dry-run。
