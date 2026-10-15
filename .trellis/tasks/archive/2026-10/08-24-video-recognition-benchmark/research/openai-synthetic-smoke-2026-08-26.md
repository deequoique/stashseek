# OpenAI synthetic provider smoke（2026-08-26）

## 范围

本次只验证 OpenAI provider 连通性，不属于正式 `benchmark-v1` 运行。输入为代码生成的 100 ms WAV 和 2×2 PNG；未读取 catalog、公开视频、字幕、冻结媒体或供应商原始响应。Responses 请求使用 `store=false`。

## 前置核验

- 私有 `.env` 中的 Key 可通过 `/v1/models` 认证。
- `whisper-1`、`gpt-5.6-luna`、`gpt-5.6-terra`、`gpt-5.6-sol` 均出现在该 Key 可见模型列表中。
- Key、请求媒体、响应正文、错误正文、完整转写和视觉输出均未写入本文件或 CLI 输出。

## 真实调用结果

| Model | Modality | HTTP status | Safe outcome | Usage reported |
| --- | --- | ---: | --- | --- |
| `whisper-1` | ASR | 429 | `provider_transient` | no |
| `gpt-5.6-luna` | Vision | 429 | `provider_transient` | no |
| `gpt-5.6-terra` | Vision | 429 | `provider_transient` | no |
| `gpt-5.6-sol` | Vision | 429 | `provider_transient` | no |

两次独立执行得到相同的四模型失败分类；第二次在复核后的安全状态码投影下确认全部为 HTTP 429。每次执行均确认 marked 临时目录已删除。CLI 在 provider 失败时以退出码 3 结束。

Billing/额度生效后进行了第三次执行，结果如下：

| Model | Modality | Outcome | Latency | Normalized usage |
| --- | --- | --- | ---: | --- |
| `whisper-1` | ASR | completed | 2232.73 ms | 0.1 audio seconds |
| `gpt-5.6-luna` | Vision | completed | 2838.31 ms | 21 input + 11 output = 32 tokens |
| `gpt-5.6-terra` | Vision | completed | 2265.24 ms | 21 input + 11 output = 32 tokens |
| `gpt-5.6-sol` | Vision | completed | 1863.61 ms | 21 input + 11 output = 32 tokens |

第三次执行退出码为 0，四项协议形状均通过，marked 临时目录确认删除。原始转写、视觉输出、响应正文、请求媒体和 Key 仍未持久化或显示。

## 结论与 blocker

- 已证明本地 TLS、DNS、认证、模型可见性以及最小 ASR/视觉请求均可执行；OpenAI synthetic smoke blocker 已关闭。
- 最初两轮 HTTP 429 与 Billing/额度未生效一致；额度生效后的相同请求全部成功。由于未读取错误正文，本记录不将早期 429 的精确子原因写成已证实事实。
- 下一步仍需人工冻结真实视频的有界音频窗口、代表帧 bundle、内容分类和查询真值，再运行正式候选比较；synthetic smoke 成功不等同于模型质量通过。
- 正式 catalog 的六个 blocker 保持关闭，本次 smoke 不改变 `catalog_status=dry_run_only`。
