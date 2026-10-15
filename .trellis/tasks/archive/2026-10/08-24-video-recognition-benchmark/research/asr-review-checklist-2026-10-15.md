# ASR 待确认项审核清单（2026-10-15）

> **2026-10-15 同日更新：本清单已基本作废，不需要再填写。**
>
> 清单写完后，用户确认了三件事：
>
> - 移除 Vision/OCR；
> - 视频方案改为“多模态 embedding + ASR”；
> - 不再测试 ASR 模型，直接选定 Fun-ASR 默认、`whisper-1` 备选。
>
> 决定记录在父任务 `08-24-intent-aware-video-recognition-routing/prd.md` 的“2026-10-15 方案变更”一节。
>
> | 部分 | 状态 | 原因 |
> | --- | --- | --- |
> | 开头关于“三个模态都要选定”的说明 | 作废 | Vision/OCR 已移除 |
> | 1a OCR 查询 | 作废 | OCR 已移除 |
> | 1b BI-06 语音查询、1c 媒体窗口 | 作废 | 不再运行 pilot，无需冻结 truth |
> | 2 whisper 预标注审核 | 作废（结果仍可作参考） | 不再运行 pilot |
> | 3 默认 ASR 选择 | 已决定 | 选了 Fun-ASR，它不在本节原来的选项里 |
> | 3a Deepgram 核对、3b 对比表 | 仍可参考 | 作为公开资料记录保留 |
> | 3c 混说样本 | 作废 | 不再测试 |
> | 4a 视频来源 | 无需确认 | 父任务 R2 和验收标准早已确定：公开 YouTube + Bilibili，后端获取 |
> | 4b 触发规则与费用保险丝 | 写错了 | 父任务验收标准早已确认：采样不收敛时自动升级，不需逐条确认；首期不做预算、额度或付费准入 |

本清单收集 ASR 推进前需要人工决定的 4 组事项。勾选 `[x]` 或在 `____` 处填写即可。

- 模型输出只用来告诉你“去哪里听、去哪里看”，不能直接成为 truth。凡是冻结 truth 的项，都必须由人回放确认。
- 填完后会据此更新 `evals/video_recognition/pilot_catalog.yaml`、ASR profile selection，以及父任务 PRD 中的产品规则。
- ~~即使 ASR 各项全部确认，正式 pilot 仍不能运行：~~（作废）`pilot_schema.py` 要求 ASR、Vision、OCR 三个模态都完成 profile selection；Vision 和 OCR 的提案同样还在待批状态。

资料来源：

- `pilot_catalog.yaml`
- `research/openai-annotation-assist-2026-09-05.md`
- `research/modality-default-fallback-freeze-proposal-2026-08-28.md`
- `research/model-shortlist-2026-08-24.md`
- `research/provider-preflight-reverification-2026-08-25.md`
- `archive/2026-09/08-20-asr-workload-cost-research/`
- 父任务 `08-24-intent-aware-video-recognition-routing/prd.md`
- 2026-10-15 重新查询的 Deepgram 与阿里云官方文档（链接见文末）

---

## 1. 两条未冻结查询　〔作废：不再运行 pilot；OCR 已移除〕

### 1a. `pilot-ocr-01`（YT-01）

| 字段 | 当前值 |
| --- | --- |
| 查询 | 哪段幻灯片标题显示 Applications of Machine Learning？ |
| 关键词 | `Applications of Machine Learning` |
| 候选范围 | 14–24s（与已冻结的 `pilot-visual-01` 一致） |
| OCR 帧 | `[18, 20]` |
| 状态 | `pending_manual`，`time_origin: pending` |

已有证据：

- 人工已确认 14–24s 是“讲师 + 机器学习应用幻灯片”（来自 `pilot-visual-01`）。
- 9/5 视觉辅助（`gpt-5.6-luna`）在 18s、20s、22s 三帧上读到同样的文字：`Stanford Online` / `DeepLearning.AI` / `Machine Learning` / `Applications of Machine Learning`，并判断三帧为同一场景。
- 需要注意：从视觉辅助的描述看，`Applications of Machine Learning` 是**副标题**，主标题是 `Machine Learning`。

请确认：

- [ ] 回放 0:18 和 0:20，屏幕上能看清 `Applications of Machine Learning` 原文：是 / 否
- [ ] 查询措辞：
  - [ ] 保留“幻灯片标题”
  - [ ] 改为“幻灯片副标题”
  - [ ] 改为“屏幕文字”
- [ ] 可接受范围定为 14–24s；如需修改，填写：____
- [ ] OCR 帧：
  - [ ] `[18, 20]`
  - [ ] `[18, 20, 22]`
- [ ] 顺带确认 YT-01 的视觉帧组：当前是 `[18, 20, 50]`，其中 50s 不在 14–24s 内。
  - [ ] 改为 `[18, 20, 22]`
  - [ ] 保留 `[18, 20, 50]`

### 1b. `pilot-speech-04`（BI-06）

| 字段 | 当前值 |
| --- | --- |
| 查询 | 哪段形容蛋糕外皮焦焦的、吃起来像冰淇淋？ |
| 关键词 | `外皮`、`焦焦的`、`冰淇淋` |
| 你当时的听写 | “外皮有点焦焦的 吃起来像冰淇淋一样”，只确认它位于 0:05–1:16 |
| 音频窗口 | 5–20s（`pending_manual_review`） |

9/5 whisper 在 5–76s 的输出中**没有出现“外皮”或“焦焦的”**。最接近的两句：

- `5.00–6.84`：中间有点像布丁
- `7.06–8.74`：吃起来和冰淇淋一样

可能的原因：

1. 原话在 0:00–0:05 之间，而 9/5 的音频窗口从 5s 才开始；
2. 实际原话是“中间有点像布丁，吃起来和冰淇淋一样”，当时听写有出入；
3. whisper 漏识别了。

请回放 0:00–0:12 后填写：

- 实际听到的原话：____
- 大致时间范围（约 5–15 秒宽）：____

然后选择处理方式：

- [ ] A：原话在 0:05 之前。时间范围按实际回放结果填写，音频窗口改为 `0–15` 或 `0–20`，关键词保持 `外皮`、`焦焦的`、`冰淇淋`。
- [ ] B：原话就是“中间像布丁 / 吃起来和冰淇淋一样”。查询改为“哪段说蛋糕中间像布丁、吃起来像冰淇淋？”，关键词改为 `布丁`、`冰淇淋`，范围按回放结果填写（预计在 5–10s 附近）。
- [ ] C：其他处理，说明：____

### 1c. 顺带：还没冻结的媒体窗口

目前只有 BI-01 的媒体选择已冻结。上面两条确认后，还剩：

- [ ] YT-03 的帧组 `[328, 340, 352]`：这是从区间推出来的，没有逐帧确认过，需要确认这三个时刻都是代码编辑器画面。
- [ ] YT-03 的其余媒体（音频窗口 `347–354`、帧组 `[54, 55, 56]`）：已在查询标注里确认，可以直接冻结。
- [ ] BI-06 的音频窗口：按 1b 的结论确定。

---

## 2. 六条 whisper 预标注候选　〔作废：不再运行 pilot；下表结果仍可作参考〕

9/5 annotation assist 的运行情况：

- 模型：`whisper-1`
- 窗口：6 个，共约 100 秒音频
- 结果：全部 `completed`，单次延迟 0.98–8.72s
- 用量：API 没有返回用量（`reported: false`），只能按时长估算费用：100s × $0.006/min ≈ $0.01

这些结果**没有写回 catalog，也没有计分**。用途有两个：

- 帮助冻结 1b；
- 提前粗看 fallback 候选 whisper 在这几个样本上的表现。

| # | 记录 | 窗口 | whisper 关键片段 | 关键词命中 | 已冻结 truth |
| --- | --- | --- | --- | --- | --- |
| 1 | YT-01 computer-vision-speech | 83–95s | `83.00–87.20` problems like speech recognition, computer vision for Google Maps Street View images, / `87.68–88.42` and advertising. | 3/3 | 84–94s，命中区间一致 |
| 2 | YT-03 current-object-rest-node | 347–354s | `347.00–350.86` from our current object to the rest node we're looking at… / `350.86–353.98` the distanceTo method. So we're going to do globalPosition.DistanceTo, | 2/2 | 347–354s，一致 |
| 3 | BI-01 handwritten-digits-speech | 100–110s | `100.00–105.08` …put together a neural network that can learn to recognize / `105.14–106.04` handwritten digits. | 2/2 | 100–110s，一致 |
| 4 | BI-06 cake-description-part-1 | 5–35s | 12 段；`5.00–6.84` 中间有点像布丁 / `7.06–8.74` 吃起来和冰淇淋一样 / 之后是做法：不需要打发蛋清、铺油纸…… | 1/3（只有冰淇淋） | 未冻结，见 1b |
| 5 | BI-06 cake-description-part-2 | 35–65s | 15 段，讲奶油奶酪隔水搅软、加糖、加蛋；**原始输出是繁体**，part 1 和 part 3 都是简体 | 0/3 | 无 |
| 6 | BI-06 cake-description-part-3 | 65–76s | 3 段；`68.24–74.46` 之间约 6s 没有输出 | 0/3 | 无 |

逐条确认（每条选一项；有问题的写备注）：

- [ ] #1　□ 文本基本正确　□ 时间可接受　□ 有问题：____
  - 备注：片段从 83.0s 开始，比 truth 早约 1s；`computer` 这个词落在 85.1s。
- [ ] #2　□ 文本基本正确　□ 时间可接受　□ 有问题：____
  - 是否把 `distanceTo` 加为可选关键词：□ 是　□ 否
- [ ] #3　□ 文本基本正确　□ 时间可接受　□ 有问题：____
- [ ] #4　□ 文本基本正确　□ 有问题：____
  - 结论并入 1b。
- [ ] #5　□ 文本基本正确　□ 有问题：____
  - 繁简问题不需要重跑：报告生成于 9/5 18:02，`text_normalization.py`（OpenCC `t2s`，缺依赖时直接报错）是 19:05 才加的。现在的代码会在写出候选前统一转成简体。
- [ ] #6　68–74s 的空白：□ 确实没有旁白　□ whisper 漏句：____

---

## 3. 默认 ASR 选择：Qwen / Whisper / Deepgram　〔已决定：Fun-ASR 默认 + `whisper-1` 备选；3a、3b 仍可作参考，3c 作废〕

### 3a. 两份旧资料互相矛盾，已用今天的官方文档核对

| 资料 | 对 Deepgram 的说法 | 2026-10-15 核对结果 |
| --- | --- | --- |
| 8/20 成本调研 | Nova-3 多语“从纸面能力看更适合英语/普通话/中英混说”，推荐它做首选 | **不对**。`language=multi` 的混说模式只有 10 种语言（英、西、法、德、印地、俄、葡、日、意、荷），**不含中文** |
| 8/24 候选名单 | “官方语言表未列中文”，因此排除 | **已过时**。Nova-3 单语模式现在支持 `zh` / `zh-CN` / `zh-Hans` / `zh-TW` / `zh-Hant` / `zh-HK` |

正确的说法：**Deepgram 能做纯中文（需要事先指定语言），但做不了中英混说。**

### 3b. 对比

| 维度 | Qwen filetrans（提案默认） | `whisper-1`（提案 fallback） | Deepgram Nova-3 |
| --- | --- | --- | --- |
| 普通话 | 支持，含多种方言 | 支持；中文专有名词和噪声场景可能偏弱 | 单语 `zh` 支持 |
| 中英混说 | 官方页**没有明确写**；候选名单是根据“多语”推断的 | 没有明确保证 | **不支持**（`multi` 不含中文） |
| 时间戳 | 句级 + 词级（8/25 preflight 核验） | `verbose_json` 片段级 + 词级 | 词级 start/end + confidence |
| 输入方式 | 需要**公网可访问的 URL**，最长 12h、最大 2GB | 本地上传，**单文件 ≤25MB**，需要切分 | 最大 2GB；同步请求超过 10 分钟可能超时，需要回调或分片 |
| 价格 | **未核验**：CNY 计价且与区域有关，今天两个官方页面都没查到 | $0.006/min（$0.36/h） | 多语 $0.0052/min（$0.312/h），单语 $0.0043/min（8/20 数据） |
| 区域 / 数据条款 | 北京或新加坡 | 研究中没有记录 | 公开价格对应加入 Model Improvement Program |
| 已有实测 | 无：0 次调用；8/25 预检时环境中没有 `DASHSCOPE_API_KEY` | synthetic smoke 通过；9/5 跑过 6 个真实窗口 | 无；也没有做过 preflight |
| 公开 benchmark | 只有开源权重 `Qwen3-ASR-1.7B` 的英文短音频 WER 5.76，**不是这个 API** | Open ASR 当前快照没有这一行 | 不在证据地图中 |
| 版本 | 官方页已出现 **`qwen-audio-3.1-asr-flash-filetrans`**，提案钉的是 3.0 | — | — |

补充：候选名单里还有 AssemblyAI `universal-2`。它官方列出 code switching 且包含中文，价格 $0.15/h，是候选中最便宜的。8/28 提案没有选它，原因是公开榜单上看到的只有 `universal-3-pro`（而 `universal-3-pro` 不支持中文）。

### 3c. pilot 样本里没有中英混说

4 个核心样本中：

- YT-01、YT-03、BI-01 的音频是英文；
- BI-06 是纯中文。

所以选 Qwen 做默认的主要理由（中英混合）在这一轮**根本测不到**。12 个回归池样本里，只有 BI-02（《使用 Llama Factory 实现中文 llama3 微调》）带 `code-switch` 标签；而计划中可选的风险扩展样本是 YT-04 和 BI-04，都不是混说。

请决定：

- [ ] ASR 默认 / fallback：
  - [ ] A：按 8/28 提案，Qwen **3.0** 默认 + whisper-1 fallback
  - [ ] A′：Qwen **3.1** 默认 + whisper-1 fallback（运行前先补 3.1 的 preflight：时间戳、价格、区域）
  - [ ] B：whisper-1 默认（已有实测，协议最熟）+ Qwen fallback
  - [ ] C：把 Deepgram 加进来（需要先补 preflight；中文视频只能按单语跑，混说视频不适用）
  - [ ] 其他：____
- [ ] 混说覆盖：
  - [ ] 把 BI-02 加为风险扩展样本，并补 1 条中英混说的 speech 查询
  - [ ] 本轮不测混说，写进报告的局限说明
- [ ] Qwen 运行前提（选 A、A′ 或 B 时）：
  - [ ] 提供 `DASHSCOPE_API_KEY`
  - [ ] 选定区域：□ 北京　□ 新加坡
  - [ ] 接受“由 adapter 生成短期受控的公网 URL”这种输入方式

~~**建议：选 A′，并把 BI-02 加进来。**~~（作废：后续改为推荐 Fun-ASR，用户已采纳） 理由如下：

- 产品的硬约束是中文 + 英文 + 中英混说，而 Deepgram 不支持中文混说，不适合做默认。
- whisper 作为 fallback，已经有协议和真实窗口的实测结果。
- 如果不加入混说样本，Qwen 当默认的理由就没有被验证过。

---

## 4. 第一版范围　〔无需填写：两项都已在父任务中确定，见下〕

### 4a. 视频来源　〔无需确认：父任务已确定公开 YouTube + Bilibili，后端获取〕

8/20 归档 PRD 里还有一条没勾：“第一版是否必须覆盖私有 NTULearn/Kaltura”。不过父任务 PRD（R2）**实际上已经写定**：

- 首期只处理后端能直接、合规获取的公开视频，同时覆盖 YouTube 和 Bilibili；
- 登录态、私有、受访问控制或 DRM 媒体都不进入首期。

请确认是否沿用这个决定：

| 选项 | 内容 | 工作量（8/20 估算） | 主要风险 |
| --- | --- | --- | --- |
| [ ] A（父任务现行） | 公开 YouTube + Bilibili，后端获取 | 仅 YouTube 估算为 11–17 个工程日；**Bilibili 适配器没有单独估算** | 生产环境的 YouTube IP 限流；无法处理 NTULearn |
| [ ] B | A + 私有 NTULearn/Kaltura，经浏览器上传 | YouTube + Kaltura 估算为 23–36 个工程日，再加 Bilibili | 第三方处理私有课程音频的授权；断点续传；Kaltura 页面变化 |
| [ ] C | 只做公开 YouTube | 11–17 个工程日 | 同 A，且不覆盖 Bilibili |
| [ ] D | 先不上线，只把 pilot 跑完 | 剩余 pilot 工作量 | 产品侧没有 ASR |

- [ ] 确认后，把 8/20 归档 PRD 中那条未勾选的验收项标记为“已由父任务 R2 决定”。

### 4b. 触发规则　〔写错了：父任务验收标准早已确认，采样不收敛时自动升级、不需逐条确认；首期不做预算、额度或付费准入。下面的选项和费用保险丝不适用〕

已经定下的部分：入库时没有可用字幕，就必须做确定性的稀疏音频/画面采样（父任务 R2），这一步自动执行。

还没定的是：**详细 ASR（整片或大段）什么时候运行**。

- [ ] 入库时自动运行
- [ ] 用户确认后运行（8/20 调研的建议：可能产生费用的转写应由用户明确操作）
- [ ] 只处理用户提问相关的时间范围
- [ ] 组合方案：____

费用保险丝（8/20 调研建议必须设置，数值待定）：

- 单条视频时长上限：____ 分钟
- 每用户每月 ASR 分钟上限：____
- ASR 队列并发：____（调研建议 1–2）

---

## 文末：官方来源（查询于 2026-10-15）

- [Deepgram models and languages](https://developers.deepgram.com/docs/models-languages-overview/)：Nova-3 单语支持中文；`multi` 的 10 种语言不含中文。
- [阿里云百炼：语音识别模型](https://help.aliyun.com/zh/model-studio/asr-model)：列出 `qwen-audio-3.1-asr-flash-filetrans`、`qwen-audio-3.0-asr-flash-filetrans`、`qwen3-asr-flash-filetrans`，均为 12h / 2GB；页面上没有价格，也没有写中英混说或时间戳。
- [阿里云百炼：模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)：页面过长被截断，可见部分没有 filetrans 的价格。
