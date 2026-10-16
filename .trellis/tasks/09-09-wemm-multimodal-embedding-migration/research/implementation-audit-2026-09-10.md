# 实施核对（2026-09-10）

## 已核实的上游接口

来源是 Tencent 官方仓库，非二手教程：
- https://github.com/Tencent/WeMM-Embedding
- https://raw.githubusercontent.com/Tencent/WeMM-Embedding/main/examples/sentence_transformers_inference.py
- https://raw.githubusercontent.com/Tencent/WeMM-Embedding/main/examples/transformers_inference.py

官方示例使用 `SentenceTransformer.encode`，文本传字符串，图片/视频传已解析媒体路径；示例明确指定归一化、可支持的 truncate_dim。WeMM-2B 支持 64/128/256/512/1024/2048 维，不含原智谱的 1536 维。

此前 `WemmEmbedder` 的 `{inputs: [{modality, content: object_id}]}` HTTP 协议仅为项目内部假定协议，没有已部署 backend，不能声称与官方 vLLM/SGLang wire contract 兼容。当前修订增加真实 lazy 本地 adapter；不得自动下载/执行未固定 revision 的 remote code。

## 当前阶段的真实状态

- provider、inspect_media 及 text-to-unified projection 已有代码和 offline tests。
- 尚没有生产媒体查询、持久化媒体索引、真实 WeMM endpoint、完整媒体入库或线上验证。
- 多轮 reviewer/test 引用了不同时间的代码；之后报告必须带所验证文件摘要或 revision，不能用旧测试结果判断新代码。
- 当前 runtime 的闭合社交问候名单及 reference_scope 条件需要按现有 bounded-autonomy spec 独立检查，不能只修单个陈旧断言而改变产品策略。
- `.env` 仅记录检查过 WeMM 配置是否存在的布尔结果，未记录任何凭据；WeMM 参数未配置。

## 发布门槛

不因 fake tests 通过就启用生产 feature flag。需真实模型/服务运行条件、同模型 query/media 向量、媒体权限与证据链、持久化/回填验收，以及独立 migration/备份检查。缺少任何一个条件时继续可离线工作，生产保持原路径。
