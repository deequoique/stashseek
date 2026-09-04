# 渠道保存与链接能力路由技术设计

## 1. 父任务依赖与输出所有权

父任务 `08-19-trusted-response-boundary` 定义 ResponseEnvelope 及 grounded、canonical、action section。channel-save 只提供领域数据和服务器执行结果：

```text
supported-link question -> CanonicalSection(supported_video_links)
save intent, no target  -> CanonicalSection(save_target_missing)
offer_video_save success -> ActionSection(save_offer_created)
save/confirm result      -> ActionSection(save_*)
```

模型不能构造 canonical/action section。adapter 负责最终可见文本；channel 不自行绕过 validator 拼接可信 URL 或成功承诺。

## 2. SaveTargetSet 与 quote 边界

LangBot 从结构化 reply/quote element 提取可选 `quoted_text`，与正文 `text` 分字段签名提交。`ChannelEnvelope` 做类型、长度和批量限制。

服务器分别扫描正文和 quote，只提取 HTTP(S) token，再经过 YouTube/Bilibili 规范化和短链解析，形成：

```text
SaveTargetSetEntry {
  source: current_text | quoted_text
  original_index
  canonical_url
  platform
}
```

目标集保留原始顺序和重复项以支持精确批次匹配。用户手工模仿引用框只属于正文；`str(message_chain)` 不作为 quote 边界。模型历史 URL 不进入目标集。

## 3. 保存提议与确认

```text
explicit save + SaveTargetSet -> terminal save Action
bare supported URLs           -> durable confirmation Action
Agent offer + SaveTargetSet   -> offer_video_save
short confirmation + pending  -> confirm/cancel/clarify Action
save intent + no target       -> canonical missing-target section
```

`offer_video_save` 只能接受服务器本轮目标集中的精确批次。成功路径在返回前提交 `PendingChannelAction(kind=save_videos)`；成功 Action section 从已提交结果生成。失败、超时或 input mismatch 不得渲染“要我保存吗”。

所有 mutation 继续重复 tenant、thread、request key 和 pending anchor 校验。

## 4. 可信链接能力说明

服务器注册 `supported_video_links` 模板，由当前 connector 接受的规范格式生成，不让模型复制 URL。模板 key 和参数是受信任标量，由父任务 canonical renderer 投影。

这允许合法示例 URL，同时保留普通模型 text 的 URL/source-block 禁令。模板不能依据“URL parser 认识平台”就宣称 worker 已能达到 `ready`；Bilibili 只有通过真实 canary 后才显示可保存能力。

## 5. Bilibili worker 与短链

扩展 `BilibiliConnector` 实现现有 connector protocol，并让 worker factory 按规范化 URL 选择 YouTube/Bilibili。

公开普通 BV/av 优先使用有界 yt-dlp 子进程获取 metadata 和官方字幕，输出统一 `ItemMeta`、`Cue`、`TextResult/NeedsASR/NeedsExtension`，复用 dispatch、raw object、chunk、embed 和 completion outbox。多 P、番剧、合集等一对多形态返回明确结果，不静默抓整个列表。

`b23.tv` resolver 仅接受 HTTPS、无 credential/fragment 输入；限制逐跳 SSRF/IP、重定向、总超时和响应大小；最终必须通过官方 host/path parser。请求不携带用户 cookie、Authorization 或 channel header。

## 6. Error catalog

唯一 `app/errors/` package 定义：

```text
codes.py      ErrorCode(StrEnum)
catalog.py    visibility/category/retryable/default message/HTTP metadata
render.py     safe fallback projection
legacy.py     persisted/public legacy mapping
```

领域异常可以保留，但跨边界 code 必须是注册值。`invalid_url` 拆分 URL 解析失败、目标缺失、目标越权、短链解析失败和 connector 失败。FSM state、tool outcome、notification disposition 不属于 error catalog。

父任务的 canonical/action template key 与 error code 关联但不混为同一个枚举：模板描述如何显示可信 section，error code 描述稳定失败身份。

## 7. 诊断与兼容

- 诊断记录 route、section kind、reason code、connector stage 和 correlation id，不记录正文、URL、quote、外部用户或模型草稿。
- 新 `quoted_text` 字段可选，旧 bridge payload 保持有效。
- YouTube connector factory 行为保持等价。
- save/pending Action 继续不把 URL 和确认状态写进模型历史。
- 回滚时可关闭 Bilibili connector/短链并恢复旧 factory，但不删除 pending、ContentItem 或历史 turn。
