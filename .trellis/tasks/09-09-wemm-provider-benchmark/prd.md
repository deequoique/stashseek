# WeMM 基准测试（benchmark）

> 2026-10-16 重写。旧版"本阶段不执行 benchmark"的决定已于 2026-10-07 被用户推翻。

## 目标

用一个可复现的小规模基准回答：**WeMM-Embedding-2B 能不能用在本产品里**。如果能用，同时给出三路合并检索的默认参数（维度、视频切片方式、清晰度）、生产流量估算和 GPU 资源数据。技术方案见 `design.md`，执行步骤见 `implement.md`。

## 已确认的决定

| 日期 | 决定 |
|---|---|
| 2026-10-07 | WeMM 必须跑 benchmark 后才能使用。 |
| 2026-10-07 | 在租用的海外 GPU 上跑（RunPod 按需 Pod，RTX 4090 级别）。用户的 M1 只做下载和 API 调用这类轻量工作。租用机器上不放项目 `.env`，不连接任何数据库。 |
| 2026-10-15 | 视频方案为"多模态 embedding + ASR"，独立 Vision/OCR 识别已移除；ASR 默认 Fun-ASR，备选 `whisper-1`。 |
| 2026-10-15/16 | 检索路由为三路合并：① BM25（文本 Segment，含字幕/ASR 文本）、② 智谱 embedding-3 向量、③ WeMM 向量（媒体片段），按名次融合。见 `09-09-multimodal-retrieval-rollout`。 |
| 2026-10-16 | WeMM 和普通文本 embedding 采用同样的处理方式：整段内容全部分段、全部入库，不再适用回归池登记的"每视频 ≤60 秒、3–6 帧"限制。做法与现有 ASR 流程一致（`app/asr/transcriber.py:197-255`）：媒体临时下载，处理完删除，只保留派生数据（片段向量和时间戳）。 |
| 2026-10-16 | benchmark 阶段的向量和时间戳存成本地文件，检索和打分在内存里完成，不依赖还没建的媒体表和数据库版入库代码。入库实现完成后，用同一套查询复测。 |
| 2026-10-16 | 查询只用 pilot 现有的 10 条冻结查询（speech 3、visual 3、combined 3、ocr 1），目标是"看个大概效果"。12 个视频全部入库，没有查询的 8 个视频当干扰项。结论只有方向性：一题约占 10 个百分点；两种设置相差 ≤ 1 题算持平，持平时选更省资源的设置。 |
| 2026-10-16 | 及格线采用下文"闸门"一节的四条。 |

## 背景事实

- **模型**：`tencent/WeMM-Embedding-2B`。
  - 2.72B 参数，BF16 权重 5.44 GB。
  - Qwen3.5 骨干，24 层中有 18 层线性注意力，GPU 加速依赖 `causal-conv1d` 和 `flash-linear-attention`。
  - 官方固定 `transformers==5.2.0`、`sentence-transformers==5.7.0`、`qwen-vl-utils[decord]==0.0.14`。
  - Matryoshka 维度 64–2048。
  - Hugging Face 上没有托管推理 API。
  - 应用里的 adapter `app/ingest/wemm_local.py` 尚未被 git 跟踪，也从未用真实模型跑过。
- **视频资产**：
  - 回归池 12 个公开视频，约 4.3 小时（`evals/video_recognition/catalog.yaml:56-275`）。pilot 选用 YT-01、YT-03、BI-01、BI-06（`pilot_catalog.yaml:13`）。
  - pilot 的 12 条查询中有 10 条已冻结，2 条 pending，无法打分（`scoring.py:129-130`）。部分查询是用中文问英文视频。
  - `scoring.py::score_query` 接受通用的排序结果 `RankedHit`（`scoring.py:70-77, 116-180`），可以直接给检索结果打分：正确视频 top1/top3、时间命中（IoU ≥ 0.30 或中点落入，`scoring.py:27-34`）。现有的执行闸门（`pilot_catalog.yaml:9`、`runner.py:461-468`）只拦识别 provider 的运行，不影响纯打分函数。
  - 旧的下载工具 `annotation_assist.py` 写死了 4 个 URL、≤60 秒、3–6 帧（`:94-196, 271-299`）。本任务用自己的下载器，不改这个工具，也不改 `catalog.yaml` 的 `processing_scope`。
  - 时间戳文本有两个来源：YouTube 公开字幕（`app/connectors/youtube.py:282-310`）；Bilibili 样本没有平台字幕，Fun-ASR 又需要对象存储签名 URL，所以这部分用 `WhisperClient.transcribe_file`（`app/asr/whisper.py:89`）。
- **流量实测**（2026-10-16，yt-dlp 只读了格式列表，没有下载）：
  - YT-01：音频 21 MB/h，视频流 360p 68 MB/h，720p 178 MB/h。
  - BI-01：音频 30 MB/h，360p 88 MB/h，720p 128 MB/h。
  - 这两个视频画面都偏静态；实拍类视频的码率没测。
- **文本检索现状**：meeting_gold RQ1（test n=270，`.trellis/tasks/10-08-meeting-qa-test-full-e2e/research/report-rq1-test.md`）已经测过，raw→RRF 对文本检索几乎没有影响（R@5 +0.006，不显著）。所以本任务不再重测文本回归。

## 需求

- **R1 WeMM 单路画面检索**：12 个视频整段切片，用 WeMM 编码；冻结查询在**全部**片段上检索；报告正确视频 top1/top3、时间命中、Recall@10、MRR，按查询类型分组。
- **R2 三路 vs 两路**：同一批视频的字幕/ASR 文本跑 BM25 和智谱向量，再与 WeMM 片段按 RRF 融合；在 10 条冻结查询上比较两路和三路，按查询类型分组；附上生产现行的原始分数合并作参考。
- **R3 参数**：
  - 维度：512 / 1024 / 2048。
  - 切片方式：固定 10 s 窗口，或与文本 Segment 相同的时间边界。
  - 清晰度：240p / 360p / 720p，只下视频流。
  - 默认配置事先声明（360p、固定窗口、1024 维），闸门只按默认配置判定，其余配置只作方向参考。替换默认配置的规则见 `design.md`。
- **R4 资源与流量**：模型加载时间、查询编码延迟（中位数 / p95）、片段编码吞吐、显存峰值、GPU 型号、总费用；按清晰度分别记录每小时视频的下载流量，作为生产流量的估算依据。
- **R5 安全**：
  - 租用机器上不放 `.env`、不连数据库、不放 API key。
  - 媒体只在临时目录里存在，两端用完即删，Pod 跑完立即终止。
  - 仓库只提交脚本、测试和汇总报告，不提交媒体、字幕全文或向量。
  - 测试不加载模型、不联网、不连数据库。

## 闸门（用户 2026-10-16 批准）

| 闸门 | 范围 | 标准 |
|---|---|---|
| ① WeMM 自己找得到 | WeMM 单路，7 道画面类题（visual / combined / ocr） | 正确视频进前 3 ≥ 5/7，且时间命中 ≥ 4/7 |
| ② 不拖后腿 | 3 道 speech 题 | 两路时间命中的题，三路也必须时间命中 |
| ③ 有增益 | 7 道画面类题 | 三路的时间命中数 ≥ 两路 + 1 |
| ④ 工程可行 | RTX 4090 | 查询编码中位数 < 200 ms（不含冷启动），显存峰值 < 20 GB，全部运行无崩溃 |

结论映射：
- 全部通过：可用，进入入库开发。
- ① 不过：不用 WeMM，或换 4B 版本再试。
- ① 通过、③ 不过：暂缓上线。
- ② 不过：先调融合权重再重测。

## 验收标准

- [ ] `evals/wemm_benchmark/` 的离线测试通过（假向量、不联网、不连数据库），覆盖 RRF 公式、四个闸门的判定和边界、持平规则、pending 查询跳过、报告不含媒体路径和字幕全文。
- [ ] `pod/` 脚本不读取 `.env`、不连数据库、不引用 API key；`--plan-only` 能正确生成切片和帧计划。
- [ ] 实际运行完成 6 遍（3 档清晰度 × 2 种切片方式），冒烟测试确认走的是加速路径。
- [ ] 提交 `evals/wemm_benchmark/reports/2026-10-wemm-benchmark.md`，包含 R1–R4 的全部指标、四个闸门的通过/不通过和结论映射、已知偏差说明。
- [ ] 每档清晰度的每小时视频流量都写进报告。
- [ ] Pod 已终止；M1 和 Pod 上没有残留的媒体文件。
- [ ] 结论已回写到 `09-09-wemm-multimodal-embedding-migration` 和 `09-09-multimodal-retrieval-rollout`。

## 不在范围内

- `inspect_media` 核验（依赖视觉模型，独立 Vision 已于 2026-10-15 移除）。
- 生产 WeMM 服务、数据库 schema、真实入库代码、回填、灰度。
- 用图片搜图片（产品目前没有入口）。
- 扩充查询集；在 meeting_gold 上重测 raw→RRF；WeMM 和智谱的纯文本检索对比（以后可以基于 `library.json` 离线做）。
- 修改 `catalog.yaml` 和 `annotation_assist.py` 现有的 pilot 识别限制。
