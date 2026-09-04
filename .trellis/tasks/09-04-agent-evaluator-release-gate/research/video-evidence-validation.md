# 视频证据与最终回答校验研究

## 结论

建议把 evaluator 分成四层：冻结的 case/fixture manifest、同环境的基线/候选运行、轨迹与运行指标采集、最终回答证据校验。视频正确性必须先由程序验证身份/链接/时间和字幕归属，再由固定 rubric 的 LLM-as-judge 判断结论是否被字幕支持；LLM judge 不能替代硬校验或发布门禁。

现有代码已经具备 Gold evidence 的雏形和 MCP 结构化输出，但还不能直接满足本门禁：segment/item 是运行时自增 ID，collector 不保留参数合法性所需的安全投影，Bilibili 时间深链不能直接交给普通 URL normalizer，summary 也没有参数合法率、冗余调用率和成本。

## 已确认的代码约束

### 最终答案和字幕证据

- `Citation` 目前只有整数 `item_id`/`segment_id`、`title`、`excerpt`、`url` 和可选 `start_sec`，没有 platform、platform_id 或 end timestamp（`app/agent/types.py:17-28`）。MCP 的 `AskStashSeekOutput` 同时返回 answer、citations、request_id、elapsed_ms 和 error_code，适合作为 evaluator 的主输入（`app/mcp_server.py:223-241`）；浏览器 API 则明确不返回内部 item/segment ID（`app/api/conversation_routes.py:119-140`、`docs/reference/web-api.md:143-145`）。
- 服务端已经约束 grounded response 必须为 `ok` 且有 citations，section 的 citation union 必须与最终 citations 一致，最多 5 个 item/8 个 segment（`app/agent/response.py:221-250`）；最终 `[S<segment_id>]` marker、来源 URL 和 excerpt 由服务端追加（`app/agent/response.py:175-197`、`app/agent/answer_pipeline.py:397-461`）。因此 checker 应检查最终可见文本和结构化 citations 的一致性，不信任模型自行生成的 URL/marker。
- 无证据是 `not_found/no_evidence`、固定文本且无 citations；read/provider failure 仍需保持 phase-accurate 的 `failed`/安全错误码，不能把异常伪装成 no-answer（`app/agent/response.py:251-257`、`docs/explanation/ingestion-and-retrieval.md:136-147`）。
- ready Segment 具有 `start_sec`、`end_sec`、text、vector/fts；原始字幕按租户隔离保存（`docs/explanation/ingestion-and-retrieval.md:91-94`）。字幕 API 会清洗、去重、合并 cue，并用字幕 body hash 绑定分页 cursor（`app/web/transcript.py:66-100`、`app/web/transcript.py:115-174`）。

### 身份、URL 和时间戳

- ContentItem 的稳定业务身份是 `platform + platform_id + canonical url`，而不是数据库 `id`；同时有 duration、content_hash、raw_format、text_source、state（`app/models.py:484-533`）。Segment `id` 是自增主键，只有 `(item_id, seq)` 唯一（`app/models.py:882-912`；初始 migration 也确认了这一点：`migrations/versions/6df2e721d7b2_init_schema.py:115-141`）。
- YouTube ID 是 11 个字符，Bilibili 只接受 HTTPS、指定 host、`/video/(BV...|av...)`，并拒绝不允许的 query/fragment（`app/connectors/youtube.py:23-24,134-136`、`app/connectors/bilibili.py:29-35,136-171`）。
- `timestamp_url` 对 YouTube 保留 query 并设置 `t=floor(seconds)`，对 Bilibili 清空原 query 后设置 `t=floor(seconds)`（`app/browser_capture.py:254-267`）。所以 `https://www.bilibili.com/video/BV...?t=42` 是合法 citation deep-link，但直接调用 `normalize_item_reference` 会因为 `t` 被拒绝；不能复用 `_citation_matches_scope` 的当前实现（`app/agent/runtime_state.py:60-70`）来校验 Bilibili deep-link。已有测试覆盖 canonicalization、恶意 URL 和 Bilibili `?t=42` 规则（`tests/test_bilibili_url.py:27-75,253-273`）。

### 现有 evaluator 的可复用部分与缺口

- Gold schema 已有 `fixture_ref`、稳定 segment key、时间范围、容忍度、evidence groups、reference points 和 answer boundary；no-evidence 明确禁止 gold item/segment/timestamp/evidence groups（`evals/natural_language/quality.py:76-165`）。完整数据集当前要求 20–30 条、六种既有 kind、至少 4 条 multi-segment 和 3 条 no-evidence（`evals/natural_language/quality.py:181-213`）。
- 已有 Recall@1/3、MRR、citation precision/completeness、timestamp hit 和 no-evidence false-positive 评分，并保留 retrieval miss、evidence selection miss、answer contract failure 三个故障层（`evals/natural_language/quality.py:262-341,383-441`）。这三层不能合并，否则无法区分“没检索到”“选错证据”和“证据对但回答契约失败”。
- runner 当前从 citations 只投影 numeric item/segment/start，并依赖 dev-only retrieval detail（`evals/natural_language/runner.py:414-499`）；`DiagnosticCollector` 只保留安全工具名、索引、结果和有限 retrieval item/segment/start/score，不保留 end/excerpt/URL/参数合法性结果（`evals/natural_language/mcp_runtime.py:59-145,157-215`）。
- 现有 summary 已有 task success、policy pass、citation validity、p50/p95、模型/工具调用数和 loop-limit rate，但没有参数合法率、冗余调用率、成功成本；当前 loop 指标主要只识别 `limit`（`evals/natural_language/runner.py:809-934`）。
- 视频 recognition catalog 提供可复用的冻结范式：revision/status/freeze date/probe snapshot、执行前 blockers，以及固定每单元重复 3 次（`evals/video_recognition/schema.py:418-533`、`evals/video_recognition/catalog.yaml:1-20`）。它还证明 dry-run/probe 可以只保留 cue/time/coverage，不落 raw subtitle body/URL/headers（`evals/video_recognition/README.md:113-124`）。

## 推荐的数据集与冻结契约

顶层按用户要求固定为四类：`simple_retrieval`、`multi_hop_retrieval`、`no_answer`、`tool_error`。既有 `direct_keyword`、`paraphrase`、`cross_language`、`context` 等 kind 作为子标签；不要把“多跳”定义为调用次数少或多，而要定义为需要覆盖的证据组/依赖关系。

manifest 建议使用版本化结构（示意）：

```yaml
schema_version: agent-eval-video-v1
dataset_id: video-evidence-regression-v1
freeze:
  revision: 2026-09-04
  manifest_sha256: ...
  transcript_policy: ready_fixture_only
fixtures:
  lecture_a:
    platform: youtube
    platform_id: qz9tKlF431k
    canonical_url: https://www.youtube.com/watch?v=qz9tKlF431k
    duration_sec: 3443
    transcript: {content_hash: ..., raw_format: json3, text_source: official_cc}
    segments:
      - {key: lecture_a.seg.003, seq: 3, start_sec: 312.0, end_sec: 328.0, text_sha256: ...}
cases:
  - id: simple-001
    class: simple_retrieval
    query: ...
    fixture_ref: lecture_a
    gold_segment_keys: [lecture_a.seg.003]
    timestamp_tolerance_sec: 5
  - id: hop-001
    class: multi_hop_retrieval
    evidence_groups: [[lecture_a.seg.003], [lecture_a.seg.011]]
  - id: none-001
    class: no_answer
    no_evidence: true
    must_not_claim: [...]
  - id: error-001
    class: tool_error
    fault: retrieval_timeout
    expected_error_code: retrieval_unavailable
    retry: bounded
```

具体规则：

1. Gold 源文件只写 fixture alias/platform ID 和 `fixture_alias.seg.<seq>` 等稳定 key；运行前 resolver 检查 fixture 的 platform ID、canonical URL、duration、transcript/content hash、`ready` 状态，再把稳定 key 映射到本次 numeric item/segment ID。现有 Gold schema 已表达稳定引用，但 `_identity()` 只是字符串化，runner 尚未做 resolver（`evals/natural_language/quality.py:47-60,369-370`、`evals/natural_language/runner.py:466-482`）。
2. 正样本冻结 `gold_segment_keys`、时间范围、容忍度、必需 evidence groups、reference points 和 `must_not_claim`；多跳按 group 覆盖率判完整，允许合法的额外检索，不以调用次数作为成功条件。
3. no-answer 必须显式 `no_evidence=true`，没有 item/segment/time/evidence group，并声明允许的拒答边界和禁止的常识补充。任何 unrelated retrieval/citation 是 false positive；不能因回答“听起来正确”而通过。
4. tool-error 单独记录注入点、预期 phase/status/error code、是否允许 retry、恢复后的任务结果和 citations 规则。空搜索仍是 no-evidence；timeout/read/provider failure 不得降级为 no-answer。已有错误语义可直接复用 `docs/explanation/ingestion-and-retrieval.md:136-147`。
5. 冻结后只允许新 revision，不原地改 query/gold/transcript hash。执行前必须通过 manifest hash、fixture readiness、transcript probe 和工具 schema 校验；缺 fixture/缺字幕/缺 gold 应为 `skip/unscorable`，不能伪造 0 分。

## 最终回答 checker

以 MCP structured output 为主输入；HTTP/SSE 只能作为补充，因为它不含内部 ID。每个回答按以下顺序处理：

### A. 程序硬校验（失败即不通过）

1. 校验 status、error_code、citations schema 和数量；`ok` grounded 必须有 citations，`not_found/no_evidence` 必须无 citations，tool-error 必须符合该 fault 的 phase-accurate 预期。
2. 对每个 citation 校验 typed item/segment/start：start/end 为 finite、非负、有序且不超过 fixture duration；解析 segment 所属 item，要求 item 是期望 fixture、`state=ready`，segment key/seq、时间和 `text_sha256` 与冻结 manifest 一致。
3. 用平台专用 deep-link parser 校验 URL host/path/video ID：URL 的 YouTube/Bilibili ID 必须等于 fixture `platform_id`；URL timestamp 必须等于 `floor(start_sec)`。Bilibili deep-link 先拆出 `t`，再对去 query 的 base URL 做 canonicalization，不能直接把带 `?t=` 的 URL 交给 `normalize_item_reference`。
4. citation excerpt 必须与可信 Segment 文本做规范化相等（至少折叠空白、保持内容 hash 校验）；标题只作展示 metadata，不作为视频身份依据。没有 timestamp 的视频引用应硬失败，除非该 case 显式允许无定位证据。
5. 检查最终文本的 `[S...]` marker 与结构化 citation union 一致、无重复/越权 marker，并检查来源 URL 是服务端追加的允许值。响应层已有同类约束（`app/agent/response.py:184-197,236-250`），evaluator 要把它变成可审计结果。

### B. 字幕支持判断（LLM judge + 人工抽检）

把 question、最终 answer、每个 citation 的可信字幕 excerpt/时间和冻结的 `reference_points` 输入固定版本 judge。Rubric 固定为：问题是否解决、每个结论是否被所引字幕蕴含、是否覆盖必需 evidence groups、是否引用正确视频/时间、是否有无依据补充、是否清楚地拒答。judge 只输出逐点 `supported/unsupported/unclear`、总体等级和理由；固定 judge model、prompt hash、temperature/seed，不允许联网或自行检索。

程序硬校验和 judge 分数分开报告。对 judge 与硬校验不一致、`unclear`、低置信/边界样本以及 tool-error 恢复样本做人工抽检和仲裁；抽检记录放本地受控 artifact，不写入 sanitized report。因为 `redact_report` 会删除 key 中的 answer/question/token 等字段（`evals/natural_language/runner.py:795-805`），模型答案和字幕只能在显式 review export 或内存中处理。

## 基线/候选运行协议与指标

两方案共享同一 frozen manifest、代码/依赖锁、fixture DB/字幕 hash、工具清单和 schema、system/developer prompt、模型 provider/model alias、temperature/top-p/max tokens、超时/重试/并发、网络/代理、worker 配置、随机 seed 和计费表；run manifest 记录这些值及 baseline/candidate strategy revision。候选唯一变化应是待评估策略/实现，不能同时换模型或工具环境。关键 case 至少重复 3 次，采用现有 benchmark 的 `runs_per_profile_sample=3` 范式（`evals/video_recognition/schema.py:426-430`）；保留每次 attempt 和最坏结果，不能只留平均值。

报告同时按四个 strata 和总体给出 case macro、run micro 以及重复分布：

| 层 | 指标和定义 |
| --- | --- |
| 结果 | `task_success_rate`；另列 Recall@1/3、MRR、citation precision/completeness、timestamp hit、no-answer retrieval/citation false-positive，以及 judge 支持率。无答案和工具异常使用各自分母。 |
| 轨迹 | `tool_selection_accuracy`：每个标注 decision point 命中允许工具/顺序，等价工具须在 gold 中显式声明；`parameter_validity_rate`：调用前通过该工具 schema、类型、范围、tenant/scope 校验；`redundant_call_rate`：未被 gold 允许的重复 tool+参数语义调用 / attempted calls，合法 retry 不计冗余；`loop_rate`：发生状态/工具循环、重复 decision cycle 或 usage limit 的 case / attempted cases。 |
| 运行 | end-to-end p50/p95 和 turn p95；`success_cost` = 成功 task 的所有模型/工具/provider 实际计费之和 ÷ 成功 task 数，包含重试成本并记录未计费本地工具。 |

轨迹指标不是“越少调用越好”：调用次数仅作诊断；额外但必要的调用可保持通过，冗余和 loop 另行计分。现有 runtime 已把同一 model step 的重复 search/neighbor 标为 typed `skipped`（`docs/explanation/ingestion-and-retrieval.md:98-106`），可作为冗余调用的安全信号；但需要增加不落原始参数的 `params_valid`、`scope_valid`、`normalized_call_hash` 或 allow-listed failure code 投影。当前 `DiagnosticCollector` 的过滤边界（`evals/natural_language/mcp_runtime.py:59-145`）不足以计算这些指标。

## 发布门禁

运行前预注册门禁阈值和比较方式，按 paired case macro 比较基线/候选，并同时展示每一 stratum、关键 case 的重复结果和置信区间。默认发布条件：

1. 所有硬正确性指标不低于 baseline（默认非劣化 margin 为 0）：task success、检索/引用/时间戳正确性、no-answer false-positive、tool selection 和 parameter validity；关键 case 不允许出现稳定的身份或字幕越权错误。
2. `loop_rate` 和 end-to-end `p95` 均严格优于 baseline；任一不改善则不发布。成本作为运行层报告和预算上限检查，不用较少调用次数替代正确性或稳定性。
3. 任何 `unscorable`、fixture skip、judge/human disagreement 都单独列出；未完成的分母不能被当作通过。只有硬校验通过、judge 支持且分歧样本完成抽检后，才生成 release decision。

## 主要风险与实现顺序

1. 先实现 fixture/transcript resolver 和稳定 segment key；否则 numeric ID 会让 gold 在重建数据库或不同租户间失效。
2. 增加平台专用 citation deep-link parser，并补 Bilibili `?t=` scope 回归测试；当前 `_citation_matches_scope` 有误拒风险。
3. 扩展 trace 为 privacy-safe trajectory projection（decision point、工具、schema/scope 合法布尔值、重复/loop 分类、模型/provider usage/cost），保留原始参数只在受控本地调试 artifact，sanitized report 不落参数/回答/字幕正文。
4. 在现有 `GoldSample`/`quality.py` 旁增加四类 dataset adapter 和 tool-error fault contract；保留既有六种子 kind 与 retrieval/selection/answer 三层故障分类。
5. 最后实现固定 judge prompt/rubric、人工分歧抽检导入和 paired release gate。先跑 network-free validate/dry-run，再在隔离 ready fixture 上以固定模型和重复次数执行付费评测。
