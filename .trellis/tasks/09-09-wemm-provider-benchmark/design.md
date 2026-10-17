# 技术设计：WeMM 基准测试

## 总体流程与运行位置

M1 只做网络和 API 调用这类轻量工作，所有 GPU 和视频解码都放在 RunPod。

```text
M1（持有 API key，不跑模型）                     RunPod Pod（无 .env、无数据库、无 API key）
1. prepare-text                                   3. setup_pod.sh + smoke
   字幕/ASR → 切片 → 智谱向量                      4. pod_embed.py
2. fetch-media                                        抽帧 → 切片 → WeMM 编码 → 资源指标
   yt-dlp 下载仅视频流（240/360/720p）──上传──▶
5. score + report  ◀──────────────下载向量与指标──
   三路排序 → RRF 融合 → 打分 → 闸门 → 报告
```

### 1. prepare-text（M1）

- 时间戳文本：YouTube 有公开字幕的样本用 `YouTubeConnector.fetch_text`（`app/connectors/youtube.py:282`）；其余样本在 M1 临时下载音频后用 `WhisperClient.transcribe_file`（`app/asr/whisper.py:89`，生产 ASR 的备选路径）。偏差：生产默认是 Fun-ASR，它需要对象存储签名 URL，benchmark 不接触生产存储。
- 切片：`app.ingest.chunker.chunk`（`app/ingest/chunker.py:260`），使用生产参数（en 80–200 / zh 130–330 单位，≤120 s，15% 重叠），semantic_embedder 的调用方式与 `app/ingest/tasks.py:522` 一致。
- 向量：`ZhipuEmbedder`（`app/ingest/embed.py:81`）编码全部 Segment 和 10 条查询。
- 输出：`segments.jsonl`（sample_id、segment_id、start、end、text）、`text_vectors.npy`、`query_text_vectors.npy`。

### 2. fetch-media（M1）

- yt-dlp 按 240p / 360p / 720p 各下载一次**仅视频流**（不足时取最接近的较低档），记录实际高度、编码、字节数和时长，写入 `traffic.json`（每小时 MB）。
- 上传到 Pod 并确认校验和后，删除 M1 上的临时文件。

### 3. setup_pod.sh（Pod）

- 安装 ffmpeg；按官方固定版本安装 `transformers==5.2.0`、`sentence-transformers==5.7.0`、`qwen-vl-utils[decord]==0.0.14`、`accelerate`，以及加速库 `causal-conv1d`、`flash-linear-attention`。
- 按固定 revision 下载 `tencent/WeMM-Embedding-2B@bbd6cd4bf52cfc6716f752a2df80b2706720bd95`（2026-10-07 记录的 HF sha，实施时再核对）。
- 冒烟测试：编码 1 句文本、1 张图、1 段短片段，确认维度和归一化，并确认线性注意力走的是加速路径，没有 transformers 的 "Falling back to torch implementation" 警告。记录模型加载时间。

### 4. pod_embed.py（Pod，独立脚本）

- 不 import `app.*`（避免加载 `.env` 和重依赖），只读取清单文件和视频文件。
- 每个"清晰度 × 切片方式"跑一遍，共 6 遍：
  - 固定窗口：10 s 窗口、5 s 步长，覆盖整段视频。
  - 文本对齐：使用 `segments.jsonl` 中各 Segment 的时间范围。
  - 抽帧：每 2.5 s 一帧，每段至少 2 帧、最多 16 帧，用 ffmpeg 按时间点抽取；以帧列表作为 video 输入交给 WeMM。
- 统一以 2048 维编码并归一化，存 float16；512 / 1024 维在打分阶段截断后重新归一化得到。
- 10 条查询用 WeMM 编码。
- 资源指标：模型加载时间；查询编码延迟（同一查询重复 20 次，去掉第 1 次，报中位数和 p95）；片段编码吞吐；显存峰值（`torch.cuda.max_memory_allocated` 加 nvidia-smi 采样）；GPU 型号；每遍耗时。
- 每遍结束后删除该遍的帧和视频文件。
- 输出：`clips_<res>_<slicing>.jsonl`、`clip_vectors_<res>_<slicing>.npy`、`query_wemm_vectors.npy`、`resources.json`。

### 5. score + report（M1，纯内存）

- 三路排序：
  - ① BM25：离线近似生产行为。CJK 查询用 pg_trgm 风格的 trigram similarity，其他查询用 AND 语义加 ts_rank_cd 近似。这是近似实现，与 Postgres 结果不完全一致，报告中注明。可复用 `data/meeting_gold/runs/2026-10-specific30/00-offline-ablation-20261006/ablation.py` 的思路。
  - ② 智谱余弦，在全部 Segment 上排序。
  - ③ WeMM 余弦，在全部片段上排序。
- 融合：RRF，k=60，等权（与 `evals/meeting_gold/retrieval_study.py::fuse_rrf` 相同公式；该文件未被 git 跟踪，benchmark 内自带实现）。
- 对比设置：
  - WeMM 单路
  - 两路 RRF(①②)
  - 三路 RRF(①②③)
  - 参考项：生产现行的两路原始分数合并
- 不做 diversify，报告中注明。
- 打分：每条结果转为 `RankedHit(sample_id, segment_id, time_range, …)`，交给 `evals.video_recognition.scoring.score_query`，得到正确视频 top1/top3 和时间命中（IoU ≥ 0.30 或中点落入）。另算前 10 名内的时间命中（Recall@10）和 MRR。
- 只对 10 条已冻结查询打分；pending 的 2 条跳过。

## 默认配置与闸门判定

- **预先声明的默认配置**（闸门只按它判定）：360p、固定 10 s / 5 s、1024 维、每 2.5 s 一帧。
- 其他配置只作方向性参考：
  - 要换掉默认配置，变体必须在闸门 ① 或 ③ 的指标上多命中 ≥ 2 题，并且不导致其他闸门失败。
  - 相差 ≤ 1 题视为持平，持平时选更省资源的设置。
- 闸门与指标的对应：

| 闸门 | 设置 | 判定 |
|---|---|---|
| ① WeMM 自己找得到 | WeMM 单路，7 题（visual / combined / ocr） | top3 正确视频 ≥ 5/7，且时间命中 ≥ 4/7 |
| ② 不拖后腿 | 两路 vs 三路，3 道 speech 题 | 两路时间命中的题，三路也必须时间命中 |
| ③ 有增益 | 两路 vs 三路，7 道画面类题 | 三路时间命中数 ≥ 两路 + 1 |
| ④ 工程可行 | RTX 4090 | 查询编码中位数 < 200 ms（不含冷启动），显存峰值 < 20 GB，6 遍全部完成、无崩溃 |

- 结论映射：
  - 全过：可用，进入入库开发。
  - ① 不过：不用 WeMM，或换 4B 版本重试。
  - ① 过、③ 不过：暂缓上线。
  - ② 不过：先调融合权重再重测。

## 代码布局

```text
evals/wemm_benchmark/
  __init__.py
  __main__.py          CLI：prepare-text / fetch-media / score（各自支持 --dry-run）
  text_prep.py         字幕/ASR → chunk → 智谱向量
  media_fetch.py       yt-dlp 仅视频流下载与流量记录
  lexical.py           BM25 离线近似
  fusion.py            RRF 与原始分数合并
  scoring_adapter.py   RankedHit 构建、Recall@10/MRR、闸门判定
  report.py            Markdown 报告
  pod/                 只拷贝这个目录到 Pod
    requirements-pod.txt
    setup_pod.sh
    pod_embed.py
  README.md
  reports/             提交的汇总报告
tests/test_wemm_benchmark_*.py   离线测试，用假向量，不加载模型、不联网、不连数据库
```

- 运行产物：`data/wemm_benchmark/runs/<run_id>/`（`data/` 已在 `.gitignore:17`）。
- 提交的报告：`evals/wemm_benchmark/reports/2026-10-wemm-benchmark.md`，只含配置、查询 ID、指标和闸门结论，不含媒体、字幕全文或向量。

## 安全

- Pod 上没有 `.env`、数据库或 API key，只有模型、视频文件和清单；跑完立即 Terminate。
- 媒体只在临时目录中存在，M1 和 Pod 两端用完即删。处理范围按 2026-10-16 决定（整段处理）。benchmark 使用自己的下载器，不修改 `catalog.yaml` 的 `processing_scope`，也不修改 `annotation_assist.py` 的校验；这两处仍约束旧的 pilot 识别流程。
- 测试一律使用 `DATABASE_URL=postgresql+psycopg://postgres:unused@127.0.0.1:1/unavailable`。

## 已知偏差与风险

- BM25 是离线近似，不是 Postgres 原生实现。
- 无字幕样本用 whisper-1 转写，不是生产默认的 Fun-ASR；中文转写质量可能偏低。
- 不做 diversify。
- 10 条查询只能给方向性结论。
- 模型加载用官方 SentenceTransformer 的最小调用，不经过 `app/ingest/wemm_local.py`（该文件未被跟踪，worktree 中也没有）。入库实现完成后用同一套查询复测，可以发现两者配置不一致的问题。
- 部分样本可能没有目标清晰度，取最接近的较低档并记录实际值。
- 费用估计：GPU 约 $0.34/h × ≤ 4 h，whisper 约 $1，智谱可忽略，总计 < $5。GPU 时间超过 4 h 时暂停，先找原因。
