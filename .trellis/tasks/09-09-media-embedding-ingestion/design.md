# 技术设计

摄入任务先校验媒体所属 ContentItem，再规范化对象和哈希；视频只处理受限时长/大小/并发范围，抽帧与片段清单由本地 worker 生成，WeMM adapter 负责编码。每个写入以媒体版本和幂等键保护，provider 失败不影响文本 Segment 发布。
