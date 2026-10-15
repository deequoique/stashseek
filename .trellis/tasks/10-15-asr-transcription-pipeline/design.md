# ASR 转写链路：设计

## 数据流

```text
fetch_text_task（队列 ingest）
  -> process_dispatch -> process_item（连接器路径）
       -> connector.fetch_text() 返回 NeedsASR
       -> 满足 ASR 条件：条目保持 fetching，返回哨兵 "asr_pending"
  -> process_dispatch 收到 "asr_pending"
       -> _handoff_dispatch_to_asr：dispatch running -> enqueued，task_id = NULL，commit
       -> 发布 transcribe_dispatch_task(dispatch_id) 到队列 asr
          （发布失败：把 dispatch 收敛为 completed/needs_asr，与现有行为一致）

transcribe_dispatch_task（队列 asr，并发 1）
  -> process_dispatch(dispatch_id, processor=_asr_processor)
       -> _claim_dispatch：enqueued -> running
       -> process_item(item_id, connector=AsrFallbackConnector(inner, transcriber))
            -> fetch_meta：委托给原连接器
            -> fetch_text：原连接器仍返回 NeedsASR 时，执行 transcriber.transcribe()
                 1. 准入：duration_sec > ASR_MAX_DURATION_SECONDS -> AsrError(asr_too_long)
                 2. 用 yt-dlp 只下载音频到私有临时目录
                 3. object_store.put_file("asr-tmp/<uuid>.<ext>")
                 4. 生成签名 URL：ASR_PUBLIC_BASE_URL/api/v1/asr-audio/<uuid>.<ext>?expires&sig
                 5. FunAsrClient.transcribe_url(url)
                    出现 AsrProviderUnavailable 或 AsrProtocolError 时，改用 WhisperClient.transcribe_file(path)
                 6. finally：删除临时对象（best-effort），删除临时目录
                 7. 归一化片段；为空时 AsrError(asr_no_speech)
                 8. 输出一条结构化用量日志
               -> 返回 TextResult(format="capture_v1", source="asr_whisper")
            -> 现有守卫、切分、向量化、写入 Segment，状态变为 ready
       -> _complete_dispatch：生成一个 completed/ready 完成事件，然后发送通知
```

Fun-ASR 的服务器按签名 URL 回源下载音频，请求链路为：Caddy → 应用 `GET /api/v1/asr-audio/{name}` → 从 MinIO 流式读取。

## 关键决策

| 决策 | 选择 | 原因 |
| --- | --- | --- |
| ASR 在哪里执行 | 同一个 dispatch 转入独立的 `asr` 队列 | 一个 dispatch 只产生一个完成事件和一条通知；长时间的转写不阻塞字幕入库 |
| 转写期间的条目状态 | 复用 `fetching` | Web 端已经把它显示为处理中；新增状态要改动 8 处以上的列表，还需要迁移 |
| 如何接入流水线 | 通过 `process_item` 已有的 `connector=` 参数注入包装连接器 | `process_item` 只需新增 `capture_v1` 后缀和 `asr_pending` 分支，其余流程完全复用 |
| 数据库迁移 | 不做；`text_source` 复用 `asr_whisper` | 当前 Alembic 链的头部是未提交的 WeMM 迁移，新迁移会把两件事缠在一起 |
| 转写为空 | 以终态错误 `asr_no_speech` 失败 | `guard_transcript` 的 `EmptySuccess` 属于临时错误，会重试 5 次并重复计费；`no_text` 又受完成事件 CHECK 约束限制 |
| Fun-ASR 的音频地址 | 应用域名下的 HMAC 签名路由 | 用户已确认；生产 MinIO 只在本机可访问，新加坡区域又没有百炼临时存储 |
| 是否调用 Celery 时长限制 | `time_limit=3600`、`soft_time_limit=3480` | Redis 不启用 `acks_late`；限制总时长，避免 worker 被长期占用 |

## 配置（`app/config.py`）

所有字段沿用现有的 `_env*` 和 `__post_init__` 校验模式。只有 `ASR_ENABLED=true` 时，才校验必需项。

| 环境变量 | 字段 | 默认值 | 开启时的校验 |
| --- | --- | --- | --- |
| `ASR_ENABLED` | `asr_enabled: bool` | `False` | — |
| `DASHSCOPE_API_KEY` | `dashscope_api_key: str \| None` | `None` | 必需，非空 |
| `DASHSCOPE_BASE_URL` | `dashscope_base_url: str` | `https://dashscope-intl.aliyuncs.com` | 必须是 https，不带末尾 `/` |
| `FUN_ASR_MODEL` | `fun_asr_model: str` | `fun-asr` | 非空 |
| `OPENAI_API_KEY` | `openai_api_key: str \| None` | `None` | 可选；缺少时不启用备选 |
| `ASR_WHISPER_FALLBACK_ENABLED` | `asr_whisper_fallback_enabled: bool` | `True` | — |
| `ASR_PUBLIC_BASE_URL` | `asr_public_base_url: str \| None` | `None` | 必需，https，不带路径或末尾 `/` |
| `ASR_AUDIO_URL_SECRET` | `asr_audio_url_secret: str \| None` | `None` | 必需，长度至少 32 |
| `ASR_AUDIO_URL_TTL_SECONDS` | `asr_audio_url_ttl_seconds: int` | `7200` | 300–86400 |
| `ASR_MAX_DURATION_SECONDS` | `asr_max_duration_seconds: int` | `14400` | 60–43200 |
| `ASR_MAX_AUDIO_BYTES` | `asr_max_audio_bytes: int` | `524288000` | 1MB–2GB |
| `ASR_DOWNLOAD_TIMEOUT_SECONDS` | `asr_download_timeout_seconds: int` | `900` | 30–3000 |
| `ASR_PROVIDER_TIMEOUT_SECONDS` | `asr_provider_timeout_seconds: int` | `1800` | 60–3000 |
| `ASR_POLL_INTERVAL_SECONDS` | `asr_poll_interval_seconds: float` | `5.0` | 1–60 |

## 模块契约

### A. 存储、签名与公开路由（子 agent A）

`app/object_store.py` 在 `RawObjectStore` 上新增两个方法：

```python
def put_file(self, key: str, path: str | os.PathLike, content_type: str) -> int
    # 用 boto3 upload_file 分片流式上传，不把整个文件读进内存；返回字节数

def open_stream(self, key: str, *, max_bytes: int) -> ObjectStream
    # 对象不存在时抛 ObjectNotFoundError（新异常）；对象大于 max_bytes 时抛 ValueError

class ObjectStream:
    content_length: int
    content_type: str
    def iter_chunks(self, chunk_size: int = 65536) -> Iterator[bytes]: ...
    def close(self) -> None: ...
```

`app/asr/signing.py`：

```python
ASR_AUDIO_PREFIX = "asr-tmp/"
ALLOWED_AUDIO_EXTS = frozenset({"m4a", "webm", "mp3", "mp4", "opus", "ogg", "wav", "aac", "flac"})

def new_audio_object_name(ext: str) -> str          # f"{uuid4().hex}.{ext}"；ext 必须在白名单里
def audio_object_key(name: str) -> str              # 先用正则 ^[0-9a-f]{32}\.(ext)$ 校验，再加前缀
def sign_audio_url(*, base_url: str, name: str, secret: str, ttl_seconds: int, now: float | None = None) -> str
    # f"{base_url}/api/v1/asr-audio/{name}?expires={unix}&sig={hex}"
def verify_audio_signature(*, name: str, expires: str, sig: str, secret: str, max_ttl_seconds: int, now: float | None = None) -> bool
    # HMAC-SHA256(secret, f"asr-audio.v1\n{name}\n{expires}")，用 hmac.compare_digest 比较
    # 拒绝：已过期、expires 比 now + max_ttl_seconds + 60 还晚、格式错误、name 不合法
```

公开路由（在 `app/api/app.py` 中注册，必须早于 SPA 兜底路由）：

- `GET` 和 `HEAD` `/api/v1/asr-audio/{name}?expires=&sig=`：
  - 只在 `asr_enabled` 且签名密钥存在时注册；
  - 不需要会话或来源校验，也不能被 `secure_web_boundary` 拦截；
  - 不进入 OpenAPI 文档。
- 成功时返回 `StreamingResponse`，带上对象自身的 Content-Type、`Content-Length`、`Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`。
- 任何失败（签名错误、过期、名称非法、对象不存在、超过大小上限）都统一返回 404 `not_found`，响应中不出现 key 或原因。

### B. ASR 包（子 agent B，只新增文件）

`app/asr/types.py`：

```python
@dataclass(frozen=True)
class AsrCue:
    start_sec: float
    end_sec: float
    text: str

@dataclass(frozen=True)
class AsrTranscript:
    cues: tuple[AsrCue, ...]
    language: str | None
    provider: str              # "fun_asr" | "whisper"
    model: str
    audio_seconds: float | None
    billed_seconds: float | None

class AsrError(Exception):          # 终态错误，带 .error_code
ASR_ERROR_CODES = {"asr_too_long", "asr_audio_unavailable", "asr_audio_too_large",
                   "asr_provider_failed", "asr_no_speech", "asr_not_configured"}

class AsrProviderUnavailable(Exception)   # 内部使用，会触发备选
class AsrProtocolError(Exception)         # 内部使用，会触发备选
```

下载过程中的临时错误直接抛 `app.connectors.base.TransientFetchError`，沿用现有的 YouTube/Bilibili 错误码。

`app/asr/audio.py`：

```python
@dataclass(frozen=True)
class DownloadedAudio:
    path: Path
    ext: str
    size_bytes: int
    content_type: str

def download_audio(*, platform: str, url: str, dest_dir: Path, settings: Settings, runner=None) -> DownloadedAudio
```

- 用子进程运行 `python -m yt_dlp`，参数为：
  - `--ignore-config --no-cache-dir --no-playlist --socket-timeout N`
  - `-f "bestaudio[ext=m4a]/bestaudio"`
  - `--max-filesize <ASR_MAX_AUDIO_BYTES>`
  - `-o <dest_dir>/audio.%(ext)s`
  - 总时长不超过 `ASR_DOWNLOAD_TIMEOUT_SECONDS`
- YouTube 额外加 `--extractor-args youtube:player_client=android_vr`，并复用 `app/connectors/youtube.py` 的子进程代理和 CA 环境构造（不改 worker 自己的环境）。Bilibili 不使用代理。
- 错误分类：
  - 复用连接器现有的分类，限流和代理问题抛 `TransientFetchError`；
  - 其余失败抛 `AsrError("asr_audio_unavailable")`；
  - 超过大小上限抛 `AsrError("asr_audio_too_large")`。
- 原始 stderr 不能进入异常或日志。

`app/asr/funasr.py`：

```python
class FunAsrClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_seconds: int,
                 poll_interval_seconds: float, client: httpx.Client | None = None,
                 sleep=time.sleep, monotonic=time.monotonic): ...
    def transcribe_url(self, audio_url: str) -> AsrTranscript
```

- **提交**：`POST {base}/api/v1/services/audio/asr/transcription`
  - 请求头：`Authorization: Bearer`、`Content-Type: application/json`、`X-DashScope-Async: enable`
  - 请求体：`{"model": m, "input": {"file_urls": [url]}, "parameters": {}}`，**不发送** `language_hints`
- **轮询**：`GET {base}/api/v1/tasks/{task_id}`
  - `PENDING` 或 `RUNNING`：继续轮询；
  - `SUCCEEDED`：检查 `results[0].subtask_status == "SUCCEEDED"`，再下载 `transcription_url`（只允许 https，大小上限 50MB）；
  - 其他状态、子任务失败、超过超时：抛 `AsrProviderUnavailable`。
- **HTTP 错误**：
  - 401/403：抛 `AsrProviderUnavailable`；
  - 429、5xx 和网络错误：提交和查询各自最多重试 3 次（指数退避），仍失败则抛 `AsrProviderUnavailable`。
- **结果解析**：
  - 读取 `transcripts[0].sentences[]`，`begin_time` 和 `end_time` 的单位是毫秒，转换为秒；跳过空白句子，按开始时间排序。
  - 时间不是有限数或 `end < start` 时，抛 `AsrProtocolError`。
  - 缺少 `transcripts` 时抛 `AsrProtocolError`。
  - `transcripts` 存在但没有非空句子时，返回空的 `cues`。
- **用量**：
  - `audio_seconds` 取 `properties.original_duration_in_milliseconds / 1000`；
  - `billed_seconds` 取 `output.usage.duration`（如果有）。
- **TLS**：`httpx.Client` 使用 `app/tls.py` 构造的可信 CA 上下文。

`app/asr/whisper.py`：

```python
class WhisperClient:
    def __init__(self, *, api_key: str, client: httpx.Client | None = None,
                 ffmpeg_path: str | None = None, runner=None): ...
    def transcribe_file(self, path: Path, *, work_dir: Path) -> AsrTranscript
```

- **请求**：`POST https://api.openai.com/v1/audio/transcriptions`，multipart，字段为 `model=whisper-1`、`response_format=verbose_json`、`timestamp_granularities[]=segment`。
- **切分**：
  - 文件不超过 24MB 时直接发送；
  - 否则用 ffmpeg 转码切分：`-vn -ac 1 -ar 16000 -b:a 48k -f segment -segment_time 600 -reset_timestamps 1 -segment_list <csv>`，按 CSV 里的起始时间给每片的 segment 加偏移；
  - 需要切分但没有 ffmpeg 时，抛 `AsrProviderUnavailable`。
- **语言映射**：`chinese` 映射为 `zh`，`english` 映射为 `en`，其他为 `None`。
- **错误**：429 和 5xx 有限重试；401/403 和其他错误抛 `AsrProviderUnavailable`。
- **响应**：大小上限 20MB。

`app/asr/transcriber.py`：

```python
class AsrTranscriber:
    def __init__(self, *, settings: Settings, object_store, funasr: FunAsrClient | None = None,
                 whisper: WhisperClient | None = None, downloader=download_audio): ...
    def transcribe(self, *, platform: str, url: str, duration_sec: int | None) -> AsrTranscript

def transcript_to_text_result(transcript: AsrTranscript) -> TextResult
    # 用 app.browser_capture 的规范化函数生成 capture-transcript.v1 字节
    # TextResult(raw_body, cues=[Cue...], source="asr_whisper", lang, format="capture_v1")
    # lang 优先取 transcript.language；否则 CJK 字符占比 >= 30% 取 "zh"，再否则取 "en"
```

`transcribe` 按以下规则执行：

- 按 R2–R5 的顺序执行；
- 临时对象和临时目录在 `finally` 中清理；
- 归一化做 NFKC 和空白折叠，并丢弃空白片段；
- 没有片段时抛 `AsrError("asr_no_speech")`；
- Fun-ASR 和备选都失败时，抛 `AsrError("asr_provider_failed")`；
- 结构化日志事件为 `asr_transcription`，只允许以下字段：provider、model、audio_seconds、billed_seconds、estimated_cost、currency、duration_ms、fallback_used、cue_count、error_code。估算单价：Fun-ASR 新加坡区为 0.00026 CNY/秒，whisper 为 0.0001 USD/秒。

`app/asr/connector.py`：

```python
class AsrFallbackConnector:
    def __init__(self, inner, transcriber: AsrTranscriber, *, platform: str): ...
    def fetch_meta(self, platform_id)   # 委托给原连接器，并缓存返回的 ItemMeta
    def fetch_text(self, platform_id)   # 原连接器返回 NeedsASR 时转写，其余结果原样返回
```

### C. 集成（子 agent C，在 A 和 B 完成之后）

`app/ingest/tasks.py`：

- **队列与路由**：新增 `Queue("asr")`；`transcribe_dispatch_task` 路由到 `asr`。
- **`process_item`**：
  - 在非预存路径中支持 `capture_v1`：后缀为 `capture.json`，类型为 `application/json`。
  - 在连接器路径的 `NeedsASR` 分支中，满足以下条件时保持 `fetching` 并返回 `"asr_pending"`：
    - `settings.asr_enabled` 为真；
    - `item.platform` 为 youtube 或 bilibili；
    - 当前不是浏览器采集路径。
- **`process_dispatch`**：处理 `"asr_pending"`，调用 `_handoff_dispatch_to_asr`。
  - 先在加锁事务中把 dispatch 从 running 改为 enqueued，并把 task_id 清空；
  - 然后用与 `publish_ingest_dispatch` 相同的有界发布方式发布任务；
  - 发布失败时，把条目设为 `needs_asr`，并把 dispatch 收敛为 completed，事件为 needs_asr。
- **`transcribe_dispatch_task`**：
  - 参数：`bind=True, base=IngestTask, autoretry_for=(TransientFetchError,), max_retries=5, retry_backoff=8, retry_backoff_max=600, retry_jitter=True, time_limit=3600, soft_time_limit=3480`；
  - 处理函数：读取条目的 url 和 platform，构造 `AsrFallbackConnector(_connector(url), AsrTranscriber(...))`，再调用 `process_item`。
- **错误码**：`_mark_dispatch_failed` 遇到 `AsrError` 时使用 `exc.error_code`；诊断允许列表加入 `ASR_ERROR_CODES`。

其他文件：

- `app/web/library.py`：`SAFE_ERROR_CODES` 加入 `ASR_ERROR_CODES`。确认通知渲染遇到未知的失败码时，回退到安全的通用文案。
- 部署：
  - 新增 `deploy/systemd/notebook-agent-asr-worker.service`，参数为 `--queues=asr --concurrency=1 --hostname=notebook-agent-asr@%h`，沙箱配置与现有 worker 一致；
  - `deploy/scripts/notebook-agent-deploy` 把它纳入停止、重启和回滚；
  - `app/deployment.py` 的本地 worker 改为消费 `ingest,maintenance,asr`；
  - 更新对应测试。
- `app/cli.py`：新增 `asr-backfill --limit N（默认 20，最大 500）[--user-id ID] [--dry-run]`。
  - 选取 `state='needs_asr'`、平台为 youtube 或 bilibili、未删除、并且不是浏览器采集来源的条目；
  - 为每个条目新建一个 dispatch，走现有的发布路径；
  - ASR 未启用时拒绝执行。
- 文档：
  - `.env.example` 和 `docs/reference/configuration.md` 加入新变量；
  - 运维文档写明：安装 ffmpeg、安装 asr worker unit，并为 MinIO `asr-tmp/` 前缀设置 1 天过期的生命周期规则（`mc ilm rule add --prefix asr-tmp/ --expire-days 1`）。

## 兼容、回滚与风险

- ASR 默认关闭；关闭后行为与现在完全一致。回滚只需设置 `ASR_ENABLED=false` 并停止 asr worker。
- 风险：
  - YouTube 音频依赖按需开启的家用代理隧道，隧道断开时会按临时错误重试；
  - 从 OVH 访问 Bilibili 的风控情况还没有验证；
  - 公开路由会让音频经过应用进程转发，占用服务器出口带宽，大约每音频小时 60MB。
- 关闭 ASR 后再开启：已经处于 `needs_asr` 的条目，用 `asr-backfill` 命令重新处理。
