# 识别运行时测试 CLI 草案

## 目标

识别运行时提供服务器本地、可直接用于开发与验收的命令行入口。CLI 调用与生产任务相同的 application service、PostgreSQL 状态机和 Celery recognition queue，不直接绕过运行时调用供应商，因此能做真实端到端测试。

首期不新增公网 Recognition HTTP API、独立 Bearer、CORS、callback 或 Web UI。operator 通过现有服务器 shell/SSH 和应用配置运行 `.venv/bin/python -m app.cli`，沿用仓库现有 CLI 安全边界。

## 命令

```text
.venv/bin/python -m app.cli recognition probe URL \
  --user-id USER_ID \
  [--modalities auto|asr,vision,ocr] \
  [--start-sec SECONDS] [--end-sec SECONDS] \
  [--profile PROFILE_ID] \
  [--idempotency-key KEY] [--json]

.venv/bin/python -m app.cli recognition status JOB_ID \
  --user-id USER_ID [--json]

.venv/bin/python -m app.cli recognition events JOB_ID \
  --user-id USER_ID [--after CURSOR] [--limit N] [--json]

.venv/bin/python -m app.cli recognition watch JOB_ID \
  --user-id USER_ID [--after CURSOR] [--jsonl]

.venv/bin/python -m app.cli recognition result JOB_ID \
  --user-id USER_ID [--json]

.venv/bin/python -m app.cli recognition cancel JOB_ID \
  --user-id USER_ID [--json]
```

## `probe`

- 首期只接受公开 YouTube/Bilibili URL，并复用生产 MediaAdapter 的规范化、redirect、host/IP admission 和安全错误。
- `--modalities` 默认 `auto`；显式列表用于独立验证 ASR、Vision、OCR 或组合路径。
- `--start-sec/--end-sec` 必须成对合法并受最大跨度限制；不提供时仍受视频时长、下载字节和采样工作量上限。
- `--profile` 默认 `default`，只允许服务器配置的 benchmark profile ID；不能输入 provider key、base URL 或任意模型字符串。
- `--idempotency-key` 可选；未提供时 CLI 生成并打印随机键。相同 user、key 和规范化 payload 复用同一 Job，同键不同 payload 返回稳定冲突。
- `probe` 只保存带 TTL 的测试 Job、事件、用量和标准化结果，不创建 Library item、不发布 Discovery Segment 或 active revision。
- 命令只完成 admission、幂等建 Job 和 dispatch，随后立即打印 `pending` 与 `job_id` 并退出；不等待模型。

人类可读输出：

```text
job=rec_01... status=pending stage=accepted idempotency_key=...
```

`--json` 输出单个稳定 JSON object：

```json
{
  "schema_version": "recognition-job.v1",
  "job_id": "rec_01...",
  "status": "pending",
  "stage": "accepted",
  "idempotency_key": "..."
}
```

## 状态、事件和等待

- 状态固定为 `pending | running | completed | failed | cancelled`。
- 阶段只表达真实边界，例如 `accepted`、`fetching_media`、`sampling`、`transcribing`、`analyzing_frames`、`reading_text`、`merging_timeline`、`publishing`、`completed`；不伪造百分比。
- `events` 做一次有界读取，使用 opaque cursor 与单调 sequence。
- `watch` 是独立的 operator 观察命令，可持续读取事件直到终态；Ctrl-C 只停止本地观察，不取消 RecognitionJob。
- `result` 在未完成时打印当前状态并以约定非零 exit code 退出；完成后输出 `recognition-result.v1`。
- `cancel` 调用与生产取消相同的幂等 service，并确保临时媒体最终清理。

## 结果

完成结果只投影供应商无关协议：

```json
{
  "schema_version": "recognition-result.v1",
  "job_id": "rec_01...",
  "status": "completed",
  "evidence": [
    {
      "modality": "ocr",
      "start_sec": 124.5,
      "end_sec": 124.5,
      "text": "example",
      "polygon": [[10, 20], [100, 20], [100, 50], [10, 50]],
      "confidence": 0.97
    }
  ],
  "usage": {"audio_seconds": 0, "image_count": 3},
  "model_profiles": ["ocr-default-v1"]
}
```

- 不输出 provider 原始响应、credential、内部 endpoint、SDK 对象、临时路径、媒体 URL、Cookie、token 或 stderr。
- 普通输出适合人工读取；`--json` 只向 stdout 写一个 JSON object，`--jsonl` 每行一个事件。运行日志写 stderr/private log，便于 shell 脚本稳定解析。
- 输出包含 profile/version、调用量、估算费用、阶段耗时和稳定错误，足以比较候选，但不暴露秘密配置。

## 用户和权限边界

- 所有命令要求 `--user-id`，验证 AppUser 存在且未禁用，并把 Job/结果绑定到该 tenant。
- CLI 是 operator 工具；服务器 shell/SSH、Unix 用户和环境文件是调用边界，不新增远程 token。
- `status/events/watch/result/cancel` 必须同时按 `job_public_id + user_id` 查询；错误 user 只能得到稳定 not-found，不能确认其他 tenant 的 Job 是否存在。
- CLI 不接受客户端提供的 provider credential 或任意 URL fetch 参数。

## 退出码

```text
0  命令成功；probe 已接受，或 watch/result 得到 completed
1  参数、配置、认证用户、readiness 或未找到错误
2  watch/result 观察到 failed 或 cancelled 终态
3  result 尚未完成
130 operator 使用 Ctrl-C 中止 watch；后台 Job 继续
```

精确退出码可在运行时设计阶段冻结，但机器可判定、stderr/stdout 分离和 Ctrl-C 不取消 Job 是硬约束。

## 可直接测试的验收流程

1. 在已加载生产/测试环境的服务器 shell 中运行 `recognition probe`，对公开 YouTube/Bilibili URL 创建真实 Job。
2. probe 快速打印 `pending` 与 `job_id` 后退出，不等待媒体或模型。
3. `status`、`events` 和 `watch` 能观察真实阶段；中断 watch 后 Job 继续。
4. `result --json` 返回供应商无关 ASR/Vision/OCR 时间轴证据；probe 不污染资料库或搜索索引。
5. 相同幂等请求复用 Job；错误 user、任意 URL/profile、超限范围和删除/取消竞态均返回稳定结果。
6. CLI 路径与自动预处理/Agent 触发路径调用同一 runtime service；测试命令不能形成第二套供应商调用逻辑。

## 暂不包含

- 公网 Recognition HTTP API、Recognition Bearer、OpenAPI/Swagger、匿名调用、callback/webhook。
- 任意网页/内网 URL、登录态/私有视频、DRM、原始媒体直传、客户端 provider 配置。
- `probe --wait` 或在创建命令内同步等待；观察由单独的 `watch` 命令完成。
- 通过 CLI 构造或续答 Agent conversation。
