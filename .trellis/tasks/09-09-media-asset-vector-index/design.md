# 技术设计

媒体表保存原始对象引用、sha256、类型、时间范围、embedding、模型/维度/版本、状态和 provenance。媒体向量与文本向量分列或分表，禁止破坏性改写 `Segment.embedding`。所有检索 SQL 必须先应用 ContentItem 的用户、删除、归档和 ready 条件。
