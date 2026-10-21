# 执行计划：WeMM 基准测试

代码由 Sonnet 子 agent（`trellis-implement`，`model: sonnet`）编写；主会话负责评审、验证、实际运行和提交（`.trellis/spec/guides/branch-and-delegation-workflow.md`）。

## 0. 准备

- [ ] 确认当前 checkout 在 `dev`。
- [ ] 把本任务目录（`.trellis/tasks/09-09-wemm-provider-benchmark/`）按路径提交到 `dev`：`git commit -m ... -- <paths>`。dev 的 git index 有其他会话在用，不要做裸 commit。
- [ ] 从 `dev` 建实现用的 worktree。benchmark 不依赖未跟踪的 WeMM 应用代码。
- [ ] `python3 ./.trellis/scripts/task.py start 09-09-wemm-provider-benchmark`（需用户批准规划后）。

## 1. 离线核心（implement agent）

- [ ] `fusion.py`：RRF（k=60）与原始分数合并。
- [ ] `lexical.py`：BM25 离线近似（CJK 用 trigram similarity，其他用 AND + ts_rank_cd 近似）。
- [ ] `scoring_adapter.py`：把三类命中转成 `RankedHit` 并调用 `evals.video_recognition.scoring.score_query`；补 Recall@10 和 MRR；按 design 的规则实现四个闸门和默认配置替换规则。
- [ ] `report.py`：Markdown 报告（配置、各设置指标、按查询类型分组、闸门结论、流量、资源、偏差说明）。
- [ ] 测试：用假向量验证 RRF 公式、闸门判定（包括边界：5/7、4/7、持平规则）、pending 查询被跳过、报告不含媒体路径和字幕全文。

## 2. M1 侧数据准备（implement agent）

- [ ] `text_prep.py`：调用现有 YouTube 字幕、whisper、chunker、智谱 embedder；支持 `--dry-run`（不联网，只打印计划）。
- [ ] `media_fetch.py`：yt-dlp 仅视频流下载，覆盖三档清晰度；记录 `traffic.json`；临时目录在退出时清理。
- [ ] 测试：用 fake connector、fake whisper、fake embedder 验证输出格式；不联网、不调用付费 API。

## 3. Pod 侧脚本（implement agent）

- [ ] `pod/requirements-pod.txt`、`pod/setup_pod.sh`：固定版本，固定模型 revision，含冒烟测试和"加速路径已启用"检查。
- [ ] `pod/pod_embed.py`：独立脚本，不 import `app.*`；实现切片清单、抽帧计划、编码、资源指标和每遍清理；提供 `--plan-only` 模式，不加载模型，只输出切片和帧计划，供本地测试。
- [ ] 测试：在 `--plan-only` 模式下验证固定窗口覆盖整段视频、文本对齐切片使用 Segment 边界、每段帧数在 2–16 之间。

## 4. 检查（trellis-check）

- [ ] 对照 spec 和本 design 评审；运行：
  - `DATABASE_URL='postgresql+psycopg://postgres:unused@127.0.0.1:1/unavailable' .venv/bin/python -m pytest -q tests/test_wemm_benchmark_*.py`
  - `python -m evals.wemm_benchmark prepare-text --dry-run`
  - `python -m evals.wemm_benchmark fetch-media --dry-run`
  - `python -m evals.wemm_benchmark score --dry-run`
  - `git diff --check`
- [ ] 确认 `pod/` 目录里没有 `.env` 读取、数据库连接或 API key 引用。

## 5. 实际运行（主会话；需用户确认）

- [ ] M1：`prepare-text`（会调用 whisper，约 $1，并调用智谱 API），开始前向用户确认。
- [ ] M1：`fetch-media`，并记录流量。
- [ ] 用户开 Pod（RTX 4090、Community、On-Demand、PyTorch 模板、≥ 50 GB）。运行方式二选一：用户把 SSH 连接命令给主会话由它操作，或用户自己按 README 运行。
- [ ] Pod：先跑 `setup_pod.sh` 和冒烟测试，再跑 6 遍 `pod_embed.py`；把结果下载回 M1；确认文件完整后 **Terminate Pod**。
- [ ] M1：`score`，生成报告。

## 6. 收尾

- [ ] 向用户报告闸门结论（四个闸门各自通过还是不通过，加上结论映射）。
- [ ] 把结论回写到父任务 `09-09-wemm-multimodal-embedding-migration` 和 `09-09-multimodal-retrieval-rollout`（是否继续、默认参数、生产流量与资源估算）。
- [ ] 如有可复用的经验，更新 spec（trellis-update-spec）。
- [ ] 按路径提交代码、报告和任务文档到 `dev`；提交后用 `git show --stat HEAD` 核对。

## 回滚点

- 第 1–3 步只新增 `evals/wemm_benchmark/` 和测试，不改任何 app 代码；回滚就是删除这些文件。
- 运行中任何一步失败都可以单独重跑；Pod 被终止只会丢失 Pod 上的临时数据，M1 上保留清单和向量文件。
- GPU 时间超过 4 h 时暂停，先查原因再决定是否继续。
