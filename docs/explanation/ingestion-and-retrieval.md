# 导入、分块、检索与回答

StashSeek Chat 的核心区别不是“把 URL 存下来”，而是把可获取的字幕变成
带时间边界、可按租户检索、可以回到原视频的 evidence。URL collection 只知道
用户曾经保存过什么；knowledge library 还需要有可验证的原文、segment 和
来源投影。

## 支持的输入路径

| 输入 | 当前路径 | 没有可用字幕时 |
| --- | --- | --- |
| YouTube 普通视频 URL | server-side `yt-dlp` metadata + JSON3 subtitle | `needs_asr`；不会自动上传音视频 |
| Bilibili 普通视频 URL | server-side `yt-dlp` metadata + inline official/automatic SRT | `needs_asr` 或明确登录字幕时 `needs_extension`；不保存 cookies |
| 当前 YouTube 页面 | 可选 browser companion；在浏览器本地读取 JSON3/WebVTT/XML/官方 transcript fallback | `unavailable` → worker `needs_asr` |
| NTULearn/Kaltura 页面 | 可选 browser companion；读取用户当前页/授权 player frame 的 captions | `unavailable` → worker `needs_asr` |

browser companion 当前只接受 `youtube` 和 `ntu_kaltura` capture。Bilibili
connector 返回 `needs_extension` 并不表示当前扩展已经支持任意 Bilibili 登录
页面；它只是避免服务端扩大 cookie 或字幕 URL 获取范围。ASR 不是通用的已交付
导入路径。

## 导入状态机

普通 server submission 的 durable item 通常经过：

```text
pending → fetching → chunking → embedding → ready
                    ├→ needs_extension
                    ├→ needs_asr
                    └→ failed
```

提交阶段先 canonicalize URL、检查每批最多 10 个、租户 quota 和 idempotency，
创建 `ContentItem` 与 `IngestDispatch`，再由 Celery `ingest` queue 处理。worker
只从 dispatch id 重新取得租户拥有的 item；它不会从 broker 消息读取字幕或
第三方凭据。`maintenance` queue 负责 completion notification、回收站 purge
和其他维护任务。

浏览器 capture 的路径略有不同：

```text
page-local authorized captions
  → capture.v1 validation + cue hash
  → tenant-prefixed raw object
  → dispatch id
  → same chunk/embed/completion path
```

capture 使用 `raw_format=capture_v1`；既有 server JSON3 对象仍可读取。字幕
不可用时，capture payload 必须是 `status=unavailable`、空 cues、无 source/
language，worker 只将 item 标为 `needs_asr`，不上传音频或视频。

## 原文获取和安全限制

YouTube server connector 使用声明的 yt-dlp runtime、bounded socket/process
timeout，并优先与视频原始语言匹配的 track，再考虑英文、中文等 fallback。它
只保存归一化字幕对象和公开 metadata；如果配置 `YOUTUBE_PROXY_URL`，代理只
注入 metadata/subtitle 子进程，失败后不回退直连。

Bilibili connector 只接收 credential-free HTTPS ordinary-video URL（BV 或 av
ID）；去掉 tracking query，固定第一 part，不展开 playlist。它优先官方 SRT，
再考虑 `ai-*` automatic SRT，忽略 `danmaku`。只有 yt-dlp 投影的 inline `data`
会被消费；URL-only track 进入 browser/action 边界，不会添加广泛服务端抓取
allowlist。cover 只接受允许的 `hdslb.com` HTTPS host。

browser companion 在页面内消费授权媒体：

- YouTube caption endpoint 的 signed query、Cookie、player key 和 transcript
  params 只在当前页临时使用；上传前只保留 cue、视频 ID、公开 canonical URL。
- Kaltura VOD WebVTT playlist 只允许 bounded、可信 relative timed-text segment，
  在浏览器本地应用 timestamp map 并合并 cues。
- `page_url` 仅保留允许 host 的 HTTPS origin/path；capture 不携带 signed caption
  URL、KS、Authorization 或 browser credential。

所有 connector/capture 输入都会通过 raw bytes、cue 数、文本字符、segment 数
和 embedding 字符预算。超限在 object storage、embedding 或 queue 之前失败，
避免一个长视频消耗不受控的内存、存储或 provider 费用。

## 分块和 embedding

`chunk()` 采用 semantic-first、带上下限的窗口切分：chapter 信号仍然最先生效
（chapter 不超过约 180 秒时整段作为一个 chunk，超过则继续递归切分）。在
chapter 之外，每个 chunk 从当前起点贪婪累积 cue，直到达到语言相关的下限
（英文 80 词 / 中文 130 字，约 30 秒），随后只在 `[下限, min(上限, 120 秒)]`
这个窗口内选择切点：

- 有 cue embedding 时，优先选窗口内 TextTiling 式相似度最深的下跌点；但如果
  某个句末或静默间隔的深度已经达到最深下跌深度的 50% 以上，优先选它，避免
  在句子中间切断证据。
- 没有 embedding 时，优先选窗口内最大的静默间隔（≥2 秒），其次选最靠近窗口
  中点的句末标点，否则取窗口能达到的最大位置。
- 如果窗口内连下限都无法达到就会撞到上限或 120 秒上限（例如异常长的单条
  cue），退回纯时长驱动的 hard cut，取能保持在 120 秒内的最后一条 cue。

相邻 chunk 之间会重叠若干条完整 cue：下一个 chunk 的起点回退
`max(1, round(0.15 × 本次 chunk 的 cue 数))` 条 cue，且每次都保证至少前进
一条 cue。切分完成后，如果末尾剩余片段的词/字数仍低于下限，会尝试并入前一
个 chunk（前提是合并结果不超过上限且不超过 120 秒），否则保留为独立的短
尾部 chunk。只要不超过 180 秒，极短的整段文本也会直接作为单个 chunk，不受
下限限制。

对于 browser capture 的超长字幕（超过 512 cues），系统仍然跳过逐 cue
semantic boundary embedding，改用 gap/punctuation 信号驱动切分，再只对最终
chunks 做 embedding。普通 ingestion 的语义优先策略意味着每条 cue 都会被
embedding；当预估的逐 cue embedding 加上重叠后的 chunk embedding 字符数会
超出该 item 的 embedding 预算时，系统会优雅降级为 gap/punctuation 切分，
而不是让整个 ingest 失败。这是 provider cost 和时间边界之间的取舍，不会把
字幕当作无限长的单段文本。

每个 ready item 的 Segment 同时保留 `start_sec`、`end_sec`、文本、vector 和
英文全文检索字段（中文走相似度/substring 路径）。原始字幕对象放在 tenant-
prefixed object key；segment hydration 仍重复租户、active/deleted、ready 和
exact-reference predicates。

## 检索如何形成 evidence

普通问题使用 vector 与 lexical/BM25 两条 bounded 路径；二者都只查询当前
tenant 的 active、未归档、ready items。Agent runtime 会在每个 model step
锁定 retrieval reservation：同一 step 的重复 search/neighbor call 返回 typed
`skipped`，不再次执行 SQL、embedding 或 storage。

`search_segments` 的公开 limit 最大 10。它先从后端取得 bounded candidate pool，
按 segment id 去重，再按 item 的最佳 hit 选择最多五个 item，最后从这些 item
中补足分数较高且 distinct 的 segment。这样一条视频不会因为 raw score 太密
而完全挤掉其他来源，同时保留同一视频相隔较远的时间点。

当前消息带 supported video URL 时，URL 解析由服务器完成：

- 纯 1–10 个 supported URL 直接进入 save-confirmation action，保持原顺序和
  duplicates，零模型、零检索。
- URL 加自然语言问题时，URL 是可信上下文，不自动成为 exact authorization
  scope。Agent 可以 tenant-wide search 或请求可选 `item_id`，但 service 会
  重新验证该 item 属于当前租户、active、未归档、ready，并在 citation 进入
  cache 前再次过滤。
- 旧对话历史不能扩大当前 URL 的访问边界；其他租户、删除条目和无证据条目
  永远不会作为 fallback。

## 从 evidence 到回答

一旦检索成功且有 candidates，系统使用无 retrieval/action tool 的结构化
Composer。Composer 只收到当前 run 的 bounded Citation：title、excerpt、
timestamp 和 segment id。服务端验证：

- 每个 selected/cited id 都来自当前 allow-list；
- grounded section 必须有 evidence，unsupported section 不能含模型自写文本；
- 最多 5 个 item、8 个 distinct segment；
- 模型不能写 URL、source/reference section、HTML 或自定义 `[S...]` marker；
- action 成功时 canonical action result 优先，不再运行 Composer。

验证通过后，服务器追加精确的 `[S<segment_id>]` 标记，并以 item 为单位分组
真实来源和时间戳链接。答案持久化只包含规范化问题、最终可见文本和已经验证
的 citation selection；tool payload、intermediate Agent text、invalid draft
和 provider exception 不持久化。

## 空结果、超时和失败

空检索是“成功但无 evidence”，不是 provider read failure；最终公开结果是
server-owned `no_evidence`/`not_found`。如果 primary retrieval 在已有可信
citations 后超时或达到 usage limit，会在同一 evidence 上进入 bounded Composer
repair；Composer 最多三次尝试，不能重新搜索来修 citation。

如果三次 draft 都 invalid、超时或 provider failure，公开结果是
`failed/answer_unavailable`，清空 citations，且不保存失败草稿。没有 evidence
的 primary timeout/read failure 会保持 phase-accurate `read_unavailable` 等
安全错误。流式回答在 section 完整且最终成功前都是临时数据；客户端断开或
section abort 不会写半条历史。
