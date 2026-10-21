# 技术设计

`inspect_media` 不执行搜索，只验证候选。Agent 提供问题和候选 ID，服务端解析真实媒体并调用供应商无关的 vision adapter。结果进入可信证据结构，不能直接替代服务器 citation 校验。缓存键包含媒体内容哈希、问题规范化结果、模型和 prompt 版本。
