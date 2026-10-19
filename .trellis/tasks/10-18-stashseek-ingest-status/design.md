# 整理状态：技术设计

本文对应 `prd.md`（2026-10-18 定稿）。需求、验收标准和已定决策以 PRD 为准，这里只写怎么实现。

调研记录在 `research/` 目录：`backend-ingest.md`、`api-and-retrieval.md`、`extension-ui.md`。下文引用代码位置时，行号以 dev 分支 2026-10-18 的代码为准，实现前要再核对一次。

## 1. 设计原则

1. **正文阶段不另存，读的时候推算出来。** 正文阶段的状态完全由已有的 `content_item.state` 和最新一条 `ingest_dispatch` 推算，和现在的 `project_lifecycle` 一样。原因：整理流程、完成事件和检索都以 `content_item.state` 为准（检索只认 `state == 'ready'`），再存一份就要两边同步写，容易对不上。
2. **只有后续阶段新建存储。** 分类与主题、内容要点（任务 5），以及第二阶段的电影识别，存在一张新表里，一个阶段一行。以后新增阶段不用改表结构。
3. **不改已有的枚举和取值。** `ingest_state` 枚举、`lifecycle`、`stage` 的取值、`ingest_dispatch.state` 的取值、完成事件的 CHECK 约束都不动。原因：旧版插件拿到不认识的 `lifecycle` 会直接崩溃（`lifecycleCopy[...]` 没有兜底）；`ingest_dispatch.state` 的取值写死在唯一索引和大约 8 处代码里；完成事件的 CHECK 要求 completed 事件的 `error_code` 必须为空。
4. **接口只加可选字段。** 新字段都要有默认值，生成的 TypeScript 类型里是可选属性，旧版插件和测试里的假数据都不会受影响。
5. **原因码要过白名单。** 对外只输出原因表里的原因码和类别；不在表里的，一律归到“处理出错”。

## 2. 对外约定（本任务的核心交付物）

任务 2、5、7、8 都按这一节开发。这一节有改动时，要同步通知这几个任务的负责人。

### 2.1 阶段

| key | 名称 | 由谁写入 | 什么时候出现在接口里 |
|---|---|---|---|
| `body` | 正文（读取字幕、必要时语音转写、建立索引） | 推算，不存储 | 一直出现 |
| `classify` | 分类与主题 | 任务 5 | 任务 5 把它登记为启用后 |
| `summary` | 内容要点 | 任务 5 | 同上 |
| `movie` | 电影识别 | 第二阶段任务 15 | 第二阶段登记为启用后 |

- 在接口里，`key` 的类型是普通字符串，不是枚举。所以以后新增阶段时，生成的类型不变，旧版插件也不会因此报错。
- 插件只显示自己认识的 key，不认识的直接忽略。
- 接口返回的阶段列表按上表顺序排列，`body` 始终排第一个。

### 2.2 阶段状态

| status | 含义 | 界面显示 |
|---|---|---|
| `pending` | 还没开始 | ○ |
| `running` | 进行中 | ◌ |
| `done` | 完成 | ✓ |
| `failed` | 失败，带原因 | 正文失败时显示没整理完的画面；后续阶段失败时显示占位文字 |
| `skipped` | 不适用 | 不显示 |

### 2.3 正文阶段怎么推算

正文阶段由 `project_lifecycle` 的结果推算。如果收藏已归档，就先忽略归档，按归档前的实际状态来算：

| lifecycle | body.status | body.detail |
|---|---|---|
| `queued` | `pending` | `queued` |
| `processing` | `running` | `fetching` / `transcribing` / `indexing`（见下） |
| `ready` | `done` | 空 |
| `needs_action` / `failed` | `failed` | 空，原因放在 `reason` 字段 |

`detail` 只在 `running` 时有值：

- **`transcribing`**：`item.state == 'fetching'`，并且最新一条整理任务的 `asr_handoff_at` 不为空（新字段，见 4.3）。另外，为了照顾上线时还在跑的老任务，保留现在的判断条件：最新的 ready 浏览器提交里 `caption_status == 'unavailable'`。两个条件满足任一个即可。
- **`indexing`**：`item.state` 是 `chunking` 或 `embedding`。
- **`fetching`**：其他情况。

现有的 `stage` 字段照旧输出，供旧版插件使用。

### 2.4 后续阶段怎么推算

只处理已启用的阶段（启用清单见 4.6）。按下面的顺序判断，命中就停：

1. 表里有这一行：直接用这一行的 `status`。如果是 `failed`，原因码查原因表，查不到就归为“处理出错”。
2. 没有这一行，但正文已经 `failed`：`skipped`。
3. 没有这一行：`pending`。现有的 ready 收藏就是这种情况，会显示“还没开始”，满足 PRD 里历史数据的要求。

后续阶段的状态不影响 `lifecycle`、`body_searchable` 和检索。

### 2.5 原因表

原因表是一个 Python 常量注册表，放在 `app/ingest/reasons.py`。它是纯数据，不依赖 web 层，任务 2 和 Agent 都可以直接用。每个原因码对应一个类别，以及“重新整理有没有意义”。

| 原因码 | 来源 | 类别 category | 重新整理有意义 |
|---|---|---|---|
| `asr_no_speech` | 转写结果为空 | `no_speech` | 否 |
| `asr_disabled` | **新写入**：needs_asr，因为 ASR 开关关闭 | `no_transcription` | 是（开关打开后重试就会转写） |
| `asr_unsupported_platform` | **新写入**：needs_asr，因为这个平台不能转写 | `no_transcription` | 否 |
| `asr_unavailable` | needs_asr、needs_* 的历史数据（没有原因码），或同步调用 | `no_transcription` | 是 |
| `asr_not_configured` | ASR 开关打开但没配置转写服务 | `no_transcription` | 是 |
| `transient_fetch_failed` | 平台访问失败、限流、代理出错（重试 5 次后） | `fetch_failed` | 是 |
| `asr_audio_unavailable` | 拿不到音频 | `fetch_failed` | 是 |
| `asr_provider_failed` | 转写服务出错 | `asr_failed` | 是 |
| `asr_handoff_failed` | **新写入**：交给转写队列失败 | `asr_failed` | 是 |
| `quota_exceeded` | **预留**，本任务不会写入 | `quota` | 是 |
| `ingest_too_large`、`asr_too_long`、`asr_audio_too_large` | 超出处理上限 | `too_large` | 否 |
| `bilibili_login_required` | **新写入**：B站字幕要登录才能看 | `login_required` | 不提供（见 4.5） |
| `bilibili_capture_required` | **新写入**：B站字幕数据要在浏览器里读取 | `login_required` | 不提供 |
| `queue_unavailable`、`ingestion_failed`、`missing_dispatch`、`item_missing`、`item_deleted`、`capture_upload_failed`，以及其他所有码 | — | `error` | 是 |

八个类别和 PRD 整理-8 一一对应：

| category | PRD 中的说法 |
|---|---|
| `no_speech` | 没有识别出语音 |
| `no_transcription` | 暂无字幕，且无法转写 |
| `fetch_failed` | 获取失败 |
| `asr_failed` | 转写服务出错 |
| `quota` | 配额不足 |
| `too_large` | 超出处理上限 |
| `login_required` | 需要登录 B站 读取字幕 |
| `error` | 处理出错 |

对外的 `reason.code` 只会是表里列出的码。不在表里的码一律输出 `ingestion_failed`，类别为 `error`。

### 2.6 接口字段

`LibraryItemResponse`（列表和详情共用）新增三个字段，都是可选的，都有默认值：

```python
class IngestReasonResponse(BaseModel):
    code: str                                   # 原因表里的码
    category: Literal["no_speech", "no_transcription", "fetch_failed", "asr_failed",
                      "quota", "too_large", "login_required", "error"]
    retry_useful: bool                          # 重新整理有没有意义，只影响提示文案

class IngestStageResponse(BaseModel):
    key: str                                    # "body" | "classify" | "summary" | "movie" | 以后新增的阶段
    status: Literal["pending", "running", "done", "failed", "skipped"]
    detail: Literal["queued", "fetching", "transcribing", "indexing"] | None = None  # 只有 body 用
    reason: IngestReasonResponse | None = None  # 只在 failed 时有值

# LibraryItemResponse 新增
stages: list[IngestStageResponse] = Field(default_factory=list)
reason: IngestReasonResponse | None = Field(default=None)      # 正文没整理完的原因，等于 body 阶段的 reason
body_searchable: bool = Field(default_factory=lambda: False)   # == (item.state == 'ready')，不考虑是否归档
```

列表响应 `LibraryPage` 新增两个计数，统计范围是没有归档、没有删除的收藏：

```python
unsearchable_total: int = Field(default_factory=lambda: 0)  # lifecycle 是 needs_action 或 failed
processing_total: int = Field(default_factory=lambda: 0)    # lifecycle 是 queued 或 processing
```

服务层同时提供 `ContentLibraryService.availability_counts(scope)`，任务 2 的搜索接口直接调用。

`CapabilitiesResponse` 新增一个字段：

```python
# 服务器会对哪些平台做语音转写；ASR 关闭时为空
transcription_platforms: tuple[Literal["youtube", "bilibili"], ...] = Field(default_factory=tuple)
```

它和 `page_qa` 一样，在运行时根据 `settings.asr_enabled` 赋值（`app/api/runtime.py`）：开关打开时，值为 `_ASR_PLATFORMS` 里的平台，按字母排序。

### 2.7 已有字段的变化

- `lifecycle`、`stage`、`error_code` 的取值和含义都不变。`error_code` 仍然只在 failed 时有值。needs_action 的原因只出现在新字段 `reason` 里。
- `available_actions` 里出现 `retry` 的条件放宽了（见 4.5）：needs_action 的收藏也会有 `retry`，原因是 `login_required` 的除外。旧版详情页会因此显示「重新整理」按钮，后端也会接受这个请求，这正是 PRD 想要的效果。

## 3. 数据流

```
收藏 / 重新整理
   │
   ▼
ingest_dispatch（pending → enqueued → running）
   │
   ├─ process_item：读字幕
   │     ├─ 有字幕 ─────────────────────────────→ chunking → ready（正文 done）
   │     ├─ 没字幕，可以转写 ─→ _handoff_dispatch_to_asr（写 asr_handoff_at）
   │     │        └─ ASR worker ─→ 有文字：chunking → ready
   │     │                       └─ 没文字或出错：failed + asr_* 原因码
   │     ├─ 没字幕，不能转写 ─→ needs_asr + fail_reason=asr_disabled / asr_unsupported_platform …
   │     └─ B站字幕要登录 ───→ needs_extension + fail_reason=bilibili_login_required
   │
   └─ ready 之后（任务 5 起）：后续阶段写入 content_item_stage（不改变 item.state）

读的时候：project_lifecycle → project_body_stage → project_enrichment_stages → reason / body_searchable
```

## 4. 后端改动

### 4.1 数据库迁移（一个迁移文件）

接在 dev 分支当前的 head（`c9d0e1f2a3b4`）后面。写迁移之前先运行 `alembic heads` 确认 head 只有一个；如果别的会话已经新增了迁移，就接在最新的 head 后面。

1. 新建表 `content_item_stage`：
   - `item_id BIGINT NOT NULL`，外键 `content_item.id`，`ON DELETE CASCADE`；
   - `stage TEXT NOT NULL`，CHECK `stage <> 'body'`；
   - `status TEXT NOT NULL`，CHECK 取值 `IN ('pending','running','done','failed','skipped')`；
   - `error_code TEXT`，CHECK `(status = 'failed') = (error_code IS NOT NULL)`；
   - `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`；
   - 主键是 `(item_id, stage)`。
   - 不额外建索引：读取时用的就是 `item_id IN (...)`，主键已经覆盖。
2. 在 `ingest_dispatch` 上新增可空列 `asr_handoff_at TIMESTAMPTZ`。
3. downgrade：删掉这张表和这一列。本任务没有任何代码写这张表，所以回滚不会丢数据。

在 `app/models.py` 里新增对应的模型 `ContentItemStage`，并在 `IngestDispatch` 上加 `asr_handoff_at`。生产环境部署时会运行 `alembic check`，所以模型和迁移必须完全一致。

另外，`tests/test_web_auth_migration.py:33` 写死了旧的 head（`d04fdae36884`），在 dev 上已经过时。这次要改成新的 head。

### 4.2 needs_* 时记下原因码（`app/ingest/tasks.py` 等）

复用 `content_item.fail_reason` 这一列来存原因码，含义变成“最近一次没整理成功的原因”。这一列没有 CHECK 约束，现在只在 failed 分支被读取，而且读取前会过白名单（`library.py:157`；Agent 侧根本不读它，见 `management.py:244`）。所以写入新的码是安全的。收藏变成 ready、或者被重新整理时，这一列会被清空（`tasks.py:558`、`library.py:454`、`browser_capture_submission.py:286`），这些都已经实现了。

需要新写入原因码的地方：

| 位置 | 现在的写法 | 新写入的 fail_reason |
|---|---|---|
| `process_item` 走浏览器提交、页面没字幕、又没交给转写（`tasks.py:399-402`） | `needs_asr` | ASR 关闭 → `asr_disabled`；平台不在 `_ASR_CAPTURE_PLATFORMS` 里 → `asr_unsupported_platform`；其他情况（同步调用）→ `asr_unavailable` |
| `process_item` 遇到 `NeedsASR`、又没交给转写（`tasks.py:443-456`） | `needs_asr` | ASR 关闭 → `asr_disabled`；平台不在 `_ASR_PLATFORMS` 里 → `asr_unsupported_platform`；其他情况（浏览器提交过、同步调用）→ `asr_unavailable` |
| `process_item` 遇到 `NeedsExtension`（`tasks.py:439-442`） | `needs_extension` | 取 `NeedsExtension.code` 的值 |
| 交给转写队列失败（`_handoff_dispatch_to_asr` 调用 `_complete_dispatch(..., converge_item_state="needs_asr")`） | `needs_asr` | `asr_handoff_failed`。给 `_complete_dispatch` 加一个参数 `converge_reason`，在同一个事务里写入 |
| `asr_backfill.py:216` 发布失败 | `needs_asr` | `asr_handoff_failed` |

`app/connectors/base.py` 的 `NeedsExtension` 新增字段 `code: str = "browser_capture_required"`。B站连接器（`bilibili.py:356,361`）分别传入 `bilibili_login_required` 和 `bilibili_capture_required`。`browser_capture_required` 同样登记进原因表，类别是 `login_required`。

完成事件不变，completed 事件的 `error_code` 仍然为空。原因码只记在收藏上。

### 4.3 转写标记

在 `_handoff_dispatch_to_asr` 里，把整理任务从 `running` 改成 `enqueued` 的那一步（`tasks.py:1289-1307`，持有行锁），同时写入 `dispatch.asr_handoff_at = now()`。这里没有其他改动。重新整理会新建一条整理任务，新任务这一列是空的，所以不会留下旧的标记。

### 4.4 B站：浏览器提交过“无字幕”记录的收藏，重新整理时也能走转写

**先说清楚现在的流程**：在侧栏收藏一个没有字幕的 B站视频时，插件根本不会提交浏览器读取的结果。只有页面有字幕时插件才提交（`useCollect.ts:176,192`），没有字幕就直接改用链接导入。链接导入的收藏走的是平台连接器，`_ASR_PLATFORMS` 里已经有 bilibili，ASR 打开时本来就会转写。所以**正常收藏流程不需要这一项改动**。

这一项只处理一种遗留情况：有些 B站收藏，最新一条浏览器提交记录写的是“没有字幕”（`caption_status = 'unavailable'`）。这种记录来自旧版弹窗，或者 worker 里现在已经没人调用的 `CAPTURE` 命令。`process_item` 会先读这条记录，而 `_ASR_CAPTURE_PLATFORMS` 里只有 youtube，所以这些收藏即使开着 ASR，重新整理也会一直停在 needs_asr。

**改动**：把 `_ASR_CAPTURE_PLATFORMS` 从 `{"youtube"}` 改成 `{"youtube", "bilibili"}`（`tasks.py:351`），`library.py:60` 的镜像常量也同步改。ASR worker 拿音频走的是平台连接器，不依赖浏览器提交的数据，所以这里可以直接放开。改动只有一行常量，作用是让 PRD 里“没整理完的收藏都能重新整理”对这批遗留收藏也成立。

### 4.5 推算和重新整理（`app/web/library.py`、`app/ingest/submission.py`）

- **新增纯函数**（放在 `library.py`，或者拆到 `app/ingest/status.py`）：
  - `project_reason(item, latest) -> ReasonProjection | None`：failed 时沿用现在的取码逻辑；needs_* 时取 `item.fail_reason`；拿到的码再查原因表。`fail_reason` 为空时按状态给默认值：`needs_asr` 和 `no_text` 给 `asr_unavailable`，`needs_extension` 给 `bilibili_login_required`。
  - `project_body_stage(item, latest, *, asr_capture)`：按 2.3 推算。
  - `project_stages(item, latest, rows, *, enabled, asr_capture)`：按 2.1 和 2.4 推算，返回排好序的列表。
- **`project_lifecycle` 的 actions 规则改为**：lifecycle 是 `failed` 或 `needs_action`，并且没有进行中的整理任务，并且 `reason.category != "login_required"` 时，才加 `retry`。lifecycle 本身的推算逻辑不变。
- **`ContentLibraryService.retry`**（`library.py:441`）：校验条件改成和上面完全一样，不满足时仍然返回 `retry_unavailable`。部分成功的收藏 lifecycle 是 ready，本来就会被拒绝，这正是 PRD 要的效果。额度检查、防重放和发布流程都不变。
- **Agent 和 MCP 的重试**（`submission.py:877` 的 `state != "failed"`）：改成允许 `failed`、`needs_asr`、`no_text`，`needs_extension` 仍然拒绝。这样各个入口的规则一致。
- **`_dto` 和列表**：读取当前这一页所有收藏的 `content_item_stage` 行，只发一条 `WHERE item_id IN (...)` 查询，然后填充 `stages`、`reason`、`body_searchable`。列表已经在 Python 里对每条收藏推算 lifecycle（`library.py:327-352`），计数在同一个循环里顺手统计，不另外查询。
- **`availability_counts(scope)`**：加载这个用户所有没删除、没归档的收藏和它们最新的整理任务，复用 `project_lifecycle` 来统计。列表接口和任务 2 共用这个方法。
- **测试替身**：`tests/test_web_library.py` 里的 `SimpleNamespace` 假对象没有新属性。新代码用 `getattr(..., None)` 读取，或者同步更新这些假对象，二选一，不能让旧测试崩掉。

### 4.6 后续阶段的写入接口（给任务 5 用）

新建 `app/ingest/stages.py`：

```python
BODY_STAGE = "body"
KNOWN_STAGES = ("body", "classify", "summary", "movie")   # 只决定排序和合法性
ENRICHMENT_STAGES: tuple[str, ...] = ()                    # 已启用的后续阶段；任务 5 改成 ("classify", "summary")

def mark_stage(db, item_id: int, stage: str, status: str, *, error_code: str | None = None) -> None:
    """upsert content_item_stage 的一行，并更新 updated_at。stage 必须在 KNOWN_STAGES 里且不是 body；
    error_code 只在 status == 'failed' 时允许，而且必须是原因表里的码（后续阶段的码由任务 5 登记）。"""
```

- `ContentLibraryService` 的构造函数新增参数 `enrichment_stages`，默认值是 `ENRICHMENT_STAGES`，测试里可以传入 `("classify", "summary")`。
- 本任务不在任何整理流程里调用 `mark_stage`，只有测试会调用。

### 4.7 能力接口

- 修改 `CapabilitiesResponse`（`app/api/app.py:117-140`）和对应的路由（`:545-555`），新增 `transcription_platforms`，赋值方式参考 `page_qa`。
- `tests/test_web_api_public.py:31-42` 写死了 capabilities 的完整 JSON，要一起更新。

### 4.8 HTTP 错误

不新增 HTTP 错误码：重新整理被拒绝时，仍然返回 409 `retry_unavailable` 或 429 `quota_exceeded`。如果实现时发现非加不可，必须同时登记到 `app/api/app.py` 的 `_SAFE_HTTP_CODES` 和 `_SAFE_MESSAGES`，否则会被转成 `request_failed`。

## 5. 插件改动

### 5.1 共享模块 `extension/src/ingest/`（新建）

侧栏和工作区（资料库、详情页）共用这个模块。

- **`ingestCopy.ts`**：所有和整理状态有关的文案都集中在这里。
  - `STAGE_LABEL`：`body` →“读取字幕与内容”，`classify` →“分类与主题”，`summary` →“内容要点”，`movie` →“电影识别”。
  - `BODY_DETAIL_LABEL`：`queued` →“排队中”，`fetching` →“读取字幕与内容”，`transcribing` →“语音转写中”，`indexing` →“建立检索索引”。
  - `STAGE_PLACEHOLDER`：`classify` →“未分类”，`summary` →“暂无内容要点”，`movie` →“未识别”。
  - `REASON_COPY[category]`：失败时的状态行文案，见下表。`retry_useful` 为 false 时，再补一句“再试一次大概率还是同样的结果”。
  - `LIFECYCLE_LABEL`：`queued` →“排队中”，`processing` →“整理中”，`ready` →“可检索”，`failed` 和 `needs_action` 都是“整理失败”，`archived` →“已归档”。遇到不认识的值时，显示“整理中”，不能崩溃。

  | category | 状态行文案（后面统一接“正文暂不可检索。”） |
  |---|---|
  | `no_speech` | 没有字幕，也没有识别出语音 |
  | `no_transcription` | 没有字幕，暂时无法语音转写 |
  | `fetch_failed` | 没能获取视频内容，可能是平台暂时限制了访问 |
  | `asr_failed` | 语音转写服务暂时出错 |
  | `quota` | 处理额度不足 |
  | `too_large` | 视频太长或内容太大，超出处理上限 |
  | `login_required` | 这个视频的字幕需要登录 B站 才能读取 |
  | `error` | 整理时出错了 |

  这张表替换掉 `sidepanel/steps.ts` 里的 `FAILURE_REASON` 和 `NEEDS_ACTION_REASON`、`library/lifecycle.ts` 里的 `lifecycleCopy`，以及 `VideoDetailView.tsx:168-169` 里永远用不到的限流文案。

- **`ingestModel.ts`**：
  - `visibleStages(item)`：只保留认识的 key 和非 `skipped` 的阶段，按 `KNOWN_STAGES` 的顺序排列。如果服务器是旧版、没有返回 `stages`，就根据 `lifecycle` 和 `stage` 推出一个只有 `body` 的列表，这样新版插件连旧版服务器也能正常用。
  - `isUnfinished(item)`：lifecycle 是 `failed` 或 `needs_action`。
  - `allStagesSettled(item)`：可见的阶段都是 `done` 或 `failed`。
- **`IngestProgress.tsx`**（对应原型 P01b、D02）：
  - 标题“已经保存，不必等在这里”，说明“现在可以继续看视频，关闭插件也不影响处理。”。
  - 步骤列表：第一行固定是“保存视频链接 ✓”，后面是 `visibleStages`。body 在 `running` 时显示 `BODY_DETAIL_LABEL`。
  - body 变成 `done` 时，显示状态行“已整理，可检索”。
- **`IngestUnfinished.tsx`**（对应原型 P01c）：
  - 标题“收藏还在，整理没有完成”。
  - 卡片：视频标题，以及“{平台名} · 链接已保存”。
  - 错误状态行：`REASON_COPY[category]` 加上“，正文暂不可检索。”。
  - 说明：一般情况是“可以按标题和备注查找，也可以稍后重试。”；`login_required` 时是“请先登录 B站，再在视频页重新收藏。”。
  - 按钮通过回调传入：主按钮可以是「重新整理」、「重新收藏」或「打开视频页」，另外还有「仅查标题 / 备注」。
- **`ingest.css`**：参考原型的结构（勾选行、醒目的错误状态行、卡片），但用插件现有的配色和按钮样式，不照搬原型的颜色。宽度按侧栏 420px 来设计。

### 5.2 侧栏收藏流程（`extension/src/sidepanel/`）

- **画面**：收藏进入跟踪阶段（拿到收藏 ID 以后），`QaThread` 里的收藏块改用 `IngestProgress` 显示；没整理完时改用 `IngestUnfinished`。读取页面字幕和提交这一小段仍然用现有的 `StepTimeline`。退回链接导入时的说明保留在卡片上方，作为一行灰色小字。`FALLBACK_COPY.no_captions` 改成“页面没有字幕，收藏后会自动语音转写”（只在支持转写时这么写，否则保持原文案）。
- **数据**：`collectModel.applyItem` 从服务器返回的收藏里多读这几项：`stages`、`reason`、`body_searchable`、`title`、`platform`。`StoredCollect` 和 `parseStored` 也要同步加上这些字段（`collectModel.ts:195-210`），读取时缺了就给默认值。
- **轮询什么时候停**：
  - 现在是 lifecycle 一到终态就停（`collectModel.ts:133-141`）。
  - 改成：lifecycle 是 `failed`、`needs_action`、`archived` 时停；lifecycle 是 `ready` 时，要等 `allStagesSettled` 为真才停。
  - 原来 30 分钟的截止时间不变。任务 5 上线之前，ready 时可见的阶段只有 body，所以表现和现在一样。
- **在侧栏里重新整理**：
  - `PanelDeps` 新增 `retryItem(itemPublicId, requestKey)`，内部调用 `api/client.ts` 现有的 `retryItem`。
  - 点「重新整理」后，这条记录回到跟踪阶段，重新开始轮询。
  - 遇到 429 时，提示“处理额度不足，请稍后再试”。
- **`login_required` 的情况**：用户此时就在视频页上，所以主按钮是「重新收藏」，直接调用现有的 `collect()`。浏览器会读取登录后才能看的字幕，服务器在原来那条收藏上重新整理（`browser_capture_submission.py:222-329`）。说明文字提醒用户先登录 B站。
- **「仅查标题 / 备注」**：调用 `deps.tabs.openUrl(deps.workspaceUrl("/library?focus=search"))`。
- 原来那句“，可以在工作区重试”和指向工作区的链接都去掉，改用上面这些按钮。

### 5.3 资料库（`extension/src/library/`）

- `LibraryPage` 读取 URL 参数 `search`（预填搜索词，留给任务 9 用）和 `focus=search`（让搜索框获得焦点）。现在的筛选项都存在 `useState` 里，打开页面时需要从 URL 初始化一次。
- `VideoCard`：
  - 状态标签用 `LIFECYCLE_LABEL`；
  - 没整理完的收藏，加一行原因短句（`REASON_COPY`），再加一个“正文不可检索”标记；
  - 点击仍然进入详情页。
- 筛选下拉框只改文字：`failed` 改成“整理失败”，`needs_action` 改成“需要处理”。合并成一个“失败”入口是任务 8 的事。
- `lifecycle.ts` 的 `lifecycleCopy` 换成 `ingestCopy.ts` 里的内容，同时加上兜底，遇到不认识的值也不会崩溃。

### 5.4 详情页（`extension/src/videos/`）

- **没整理完时**：在页面顶部显示 `IngestUnfinished`。
  - 主按钮：`available_actions` 里有 `retry` 时，显示「重新整理」，沿用现有的 `onRetry`；类别是 `login_required` 时，显示「打开视频页」，沿用现有的 `open_source`。
  - 「仅查标题 / 备注」：在工作区内跳转到 `/library?focus=search`。
- **重新整理出错时的提示**：429 显示“处理额度不足，请稍后再试”，其他错误沿用现有文案。
- **“正文不可检索”标记**：在 `body_searchable` 为 false、并且收藏没有在整理中时显示。
- **后续阶段的区域**：
  - 新增 `StageRegion` 组件，对 `visibleStages` 里除了 body 的每个阶段渲染一个区域，标题用 `STAGE_LABEL`。
  - 状态是 `failed` 时，显示 `STAGE_PLACEHOLDER`；`pending` 和 `running` 时，显示“整理中…”；`done` 时，显示由调用方传入的内容。具体内容由任务 5 和任务 10 来填。
  - 任务 5 上线之前，接口里没有这些阶段，所以页面上不会出现这些区域。
- 删掉 `VideoDetailView.tsx:168-169` 里的限流文案。

### 5.5 页面问答提示（补充-1）

- `SidePanel.tsx:55-57` 已经会读取 capabilities，再多读一个 `transcription_platforms`，传给 `PageWorkspace`。
- `PageWorkspace.tsx:37`：页面没有字幕、并且当前平台在 `transcription_platforms` 里时，提示文字改成：
  > 该视频没有字幕，提问将仅基于标题和简介回答。收藏后会自动语音转写，转写完成后可以按内容搜索。
  
  其他情况保持原文案。「收藏视频」按钮本来就在这段提示下面（`:100-107`），不用改。
- 文案里不能出现“回到侧栏就能问”之类的承诺。

### 5.6 接口契约生成

- 后端改完以后，运行 `pnpm generate:api` 重新生成 `openapi.json`，再用 `openapi-typescript` 重新生成 `schema.d.ts`。
- 目前 `schema.d.ts` 没有对应的 package 脚本。这次在 `extension/package.json` 里补一个 `generate:types` 脚本，把用到的 openapi-typescript 版本和命令固定下来。两个文件要放在同一次提交里。
- `contracts.ts` 新增三个别名：`IngestStage`、`IngestReason`、`IngestReasonCategory`。`LibraryItemSummary` 的 `Pick` 里要加上 `stages`、`reason`、`body_searchable`。
- 测试里的类型化假数据（`test/sidepanelEnvironment.ts:86-117`、`LibraryPage.test.tsx:11-31`）不用改，因为新字段都是可选的。新增的测试要专门覆盖这些字段。

## 6. 测试

**运行后端测试时，一律显式指定测试数据库**，例如 `DATABASE_URL=<.env.neon-test 里的测试分支或本地 docker 库> uv run pytest …`。直接跑 `pytest` 会连到 `.env` 里的生产库。需要 PostgreSQL 的测试，在连不上库时会自动跳过。

### 6.1 后端

| 范围 | 内容 | 放在哪里 |
|---|---|---|
| 原因表 | `SAFE_ERROR_CODES` 里的每个码、表里新增的每个码都能查到类别；不认识的码归到 `error` | 新建 `tests/test_ingest_reasons.py` |
| 推算 | 各种 lifecycle 下的 `stages`、`reason`、`body_searchable`、`available_actions`；needs_* 没有原因码时的默认值；归档的收藏 | `tests/test_web_library.py` |
| 后续阶段 | 启用 `("classify","summary")` 以后：没有行时是 pending；某个阶段失败时，lifecycle 仍是 ready、`body_searchable` 仍为真，检索不受影响；正文失败时是 skipped；多启用一个未知阶段，已有阶段的结果不变 | `tests/test_web_library.py`，以及 PostgreSQL 测试 |
| `mark_stage` | 阶段名不合法、状态不合法、failed 却没有码、不是 failed 却带了码，都要拒绝；upsert 正常工作 | 新建 `tests/test_ingest_stages.py` |
| 整理流程 | ASR 开着和关着时，各个 needs_* 分支写入的原因码；交给转写时写入 `asr_handoff_at`；交接失败时写入 `asr_handoff_failed`；B站浏览器提交、页面没字幕、开着 ASR 时进入转写；ASR 开关用 `dataclasses.replace(Settings(), asr_enabled=...)` 注入 | `tests/test_asr_ingest_flow.py` 及同类文件 |
| 重新整理 | needs_asr 可以重试；needs_extension、ready（部分成功）、已归档、有任务在跑、已删除、别人的收藏都被拒绝；额度不足返回 429；防重放；Agent 和 MCP 的重试遵守同样的规则 | `tests/test_web_library.py`、submission 的相关测试 |
| 计数 | `unsearchable_total` 和 `processing_total` 的统计范围，以及跨账号隔离 | `tests/test_web_library.py`，以及 PostgreSQL 测试 |
| 接口 | 新字段的序列化；capabilities 的完整 JSON | `tests/test_library_extension_transport.py`、`tests/test_web_api_public.py` |
| 迁移 | 只有一个 head；在测试库上 upgrade 和 downgrade 都成功；更新 `test_web_auth_migration.py` 里写死的 head | 迁移相关测试 |

### 6.2 插件

`pnpm test`、`pnpm typecheck`、`pnpm lint`、`pnpm build`、`pnpm check:api` 全部要通过。新增或更新的测试：

- `ingest/` 模块：
  - 每个 category 都有文案；
  - 不认识的 lifecycle 和不认识的阶段 key 不会崩溃；
  - `skipped` 的阶段不显示；
  - 服务器没返回 `stages` 时，能从 lifecycle 推出 body。
- `IngestProgress` 和 `IngestUnfinished` 的渲染：
  - 各种状态；
  - 转写中；
  - `login_required` 时的变体；
  - 每个按钮的回调。
- 侧栏收藏（`SidePanelCollect.test.tsx`、`collectModel.test.ts`、`steps.test.ts`）：
  - ready 以后，要等后续阶段都结束才停止轮询；
  - 在侧栏里重新整理；
  - `login_required` 时，「重新收藏」会走 `collect()`；
  - 新字段能存进 storage 并读出来；
  - 退回链接导入时的文案。
- 资料库：状态标签、“正文不可检索”标记、原因短句、URL 参数 `focus` 和 `search`。
- 详情页：没整理完的画面、后续阶段的占位文字、不再出现限流文案。
- `PageWorkspace`：有转写能力和没有转写能力时的两种提示。

## 7. 和其他任务的关系

- **`10-16-extension-asr-copy`（规划中）**：它的三条需求（区分“正在转写”和“不会转写”、显示“语音转写中”、`asr_*` 错误码的中文提示）都被本任务的 4.3、5.1 和 5.2 覆盖了。**建议**：本任务合并后，把它归档，并注明“已被 `10-18-stashseek-ingest-status` 覆盖”。由技术负责人决定。
- **`10-17-bilibili-side-panel`**：代码已经在 dev 上（`c6d2044`、`966dfd4`），并且已经通过 PR #32 在 2026-10-17 发布到 main（插件 0.2.0）。只是 Trellis 里的任务记录还没收尾。本任务直接在它的基础上改 `useCollect.ts`、`collectModel.ts`，没有先后顺序的限制。
- **任务 5**：在 `ENRICHMENT_STAGES` 里登记 `classify` 和 `summary`，整理流程里调用 `mark_stage`，把真实内容接进 `StageRegion`，并在原因表里登记自己的失败码。
- **任务 2**：调用 `availability_counts(scope)`，读取 `body_searchable`。
- **任务 7、8**：直接复用 `IngestProgress`、`IngestUnfinished`、`ingestCopy.ts`，不要另写一套状态文案。

## 8. 发布与回滚

**发布顺序**：

1. 迁移（只新增表和列）随后端一起上：dev → main → 生产环境审批 → `alembic upgrade head` 和 `alembic check`。
2. 后端上线以后，旧版插件照常可用：没有新增 lifecycle 取值，新字段旧版插件不会读。它唯一会看到的变化，是 needs_action 的收藏多了「重新整理」按钮，后端也接受这个请求。
3. 再发布插件，走 Chrome 应用商店。新版插件遇到旧版服务器，也会退回到根据 lifecycle 推算阶段，不会出错。
4. 生产环境的 ASR 开关和本任务无关，由技术负责人单独打开。

**回滚**：

- 后端直接回到上一个版本即可。新加的表和列旧代码不会用。needs_* 收藏上写入的 `fail_reason`，旧代码不会读，重新整理时也会被清空。
- 如果需要，可以执行 downgrade 删掉这张表和这一列。本任务期间这张表没有数据。
- 插件回滚不受后端影响。

## 9. 取舍与风险

- **正文阶段推算，还是单独存一份**：选择推算。这样状态只有一个来源，代价是正文阶段的含义和 `item.state` 绑在一起。以后如果要做“只重跑某个阶段”（整理-11），再考虑要不要单独存。
- **复用 `fail_reason`，还是新加一列**：选择复用，少一次改表，也少一处要同步清空的地方。风险是这一列的含义变杂了，靠原因表的白名单兜底，并在 spec 里写清楚。
- **后续阶段用新表，还是 JSONB 或多加几列**：选择新表。一行一个阶段，可以单独更新；新增阶段时不用改表结构；CHECK 约束能保证状态取值合法。
- **轮询时间变长**：任务 5 上线以后，侧栏要一直等到分类和要点都完成才停止轮询，请求会变多，但仍然受 30 分钟截止时间限制，后台标签页也会暂停轮询。
- **B站遗留收藏重新整理时走转写**：只影响少量遗留数据，转写费用的增加可以忽略。
- **列表要加载用户的全部收藏**：这是现有的做法，本任务的计数沿用它。等视频库-8 改成数据库分页时，`availability_counts` 要改成用 SQL 统计。

## 10. 收尾时要更新的 spec（第 3.3 步）

- 新建 `.trellis/spec/backend/ingest-status-model.md`：写阶段、状态取值、原因表、接口字段、兼容规则，以及新增阶段的步骤。
- `.trellis/spec/backend/knowledge-item-management.md`：把“只有 failed 能重试”改成新的规则。
- `.trellis/spec/backend/asr-transcription.md`：补充 `asr_handoff_at`、浏览器提交时的转写平台、`asr_handoff_failed`。
- `.trellis/spec/frontend/type-safety.md`：写明 `schema.d.ts` 用哪个脚本生成。
- `.trellis/spec/frontend/directory-structure.md`：补充 `src/ingest/` 目录。
