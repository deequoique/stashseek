# OpenAI 视频预标注候选（待人工审核）

> 这些结果只是模型候选，不是 benchmark truth。它们不会修改 `pilot_catalog.yaml`、不会计分，也不会打开正式 provider execution gate。
> OCR 字段由多模态视觉模型辅助读取，不代表正式 OCR provider 的结果。

- ASR 模型：`whisper-1`
- 视觉模型：`gpt-5.6-luna`
- 记录数：11

## YT-01 / computer-vision-speech

- 模态：`asr`
- 状态：`completed`
- 延迟：8721.39 ms
- 用量：`{   "reported": false }`

````json
{
  "segments": [
    {
      "end_sec": 87.2,
      "start_sec": 83.0,
      "text": "problems like speech recognition, computer vision for Google Maps Street View images,"
    },
    {
      "end_sec": 88.42,
      "start_sec": 87.68,
      "text": "and advertising."
    },
    {
      "end_sec": 94.98,
      "start_sec": 89.08,
      "text": "Or, leading AI at Baidu, I worked on everything from AI for augmented reality to compacting"
    }
  ],
  "text": "problems like speech recognition, computer vision for Google Maps Street View images, and advertising. Or, leading AI at Baidu, I worked on everything from AI for augmented reality to compacting",
  "words": [
    {
      "end_sec": 83.3,
      "start_sec": 83.0,
      "word": "problems"
    },
    {
      "end_sec": 83.58,
      "start_sec": 83.3,
      "word": "like"
    },
    {
      "end_sec": 83.78,
      "start_sec": 83.58,
      "word": "speech"
    },
    {
      "end_sec": 84.42,
      "start_sec": 83.78,
      "word": "recognition"
    },
    {
      "end_sec": 85.18,
      "start_sec": 85.1,
      "word": "computer"
    },
    {
      "end_sec": 85.62,
      "start_sec": 85.18,
      "word": "vision"
    },
    {
      "end_sec": 85.84,
      "start_sec": 85.62,
      "word": "for"
    },
    {
      "end_sec": 86.06,
      "start_sec": 85.84,
      "word": "Google"
    },
    {
      "end_sec": 86.34,
      "start_sec": 86.06,
      "word": "Maps"
    },
    {
      "end_sec": 86.68,
      "start_sec": 86.34,
      "word": "Street"
    },
    {
      "end_sec": 86.74,
      "start_sec": 86.68,
      "word": "View"
    },
    {
      "end_sec": 87.2,
      "start_sec": 86.74,
      "word": "images"
    },
    {
      "end_sec": 87.96,
      "start_sec": 87.68,
      "word": "and"
    },
    {
      "end_sec": 88.42,
      "start_sec": 87.96,
      "word": "advertising"
    },
    {
      "end_sec": 89.7,
      "start_sec": 89.08,
      "word": "Or"
    },
    {
      "end_sec": 89.92,
      "start_sec": 89.72,
      "word": "leading"
    },
    {
      "end_sec": 90.22,
      "start_sec": 89.92,
      "word": "AI"
    },
    {
      "end_sec": 90.72,
      "start_sec": 90.22,
      "word": "at"
    },
    {
      "end_sec": 90.82,
      "start_sec": 90.72,
      "word": "Baidu"
    },
    {
      "end_sec": 91.14,
      "start_sec": 91.0,
      "word": "I"
    },
    {
      "end_sec": 91.32,
      "start_sec": 91.14,
      "word": "worked"
    },
    {
      "end_sec": 91.6,
      "start_sec": 91.32,
      "word": "on"
    },
    {
      "end_sec": 91.94,
      "start_sec": 91.6,
      "word": "everything"
    },
    {
      "end_sec": 92.54,
      "start_sec": 91.94,
      "word": "from"
    },
    {
      "end_sec": 92.86,
      "start_sec": 92.54,
      "word": "AI"
    },
    {
      "end_sec": 93.52,
      "start_sec": 92.86,
      "word": "for"
    },
    {
      "end_sec": 93.52,
      "start_sec": 93.52,
      "word": "augmented"
    },
    {
      "end_sec": 94.12,
      "start_sec": 93.52,
      "word": "reality"
    },
    {
      "end_sec": 94.54,
      "start_sec": 94.12,
      "word": "to"
    },
    {
      "end_sec": 94.98,
      "start_sec": 94.54,
      "word": "compacting"
    }
  ]
}
````

## YT-03 / current-object-rest-node

- 模态：`asr`
- 状态：`completed`
- 延迟：8332.84 ms
- 用量：`{   "reported": false }`

````json
{
  "segments": [
    {
      "end_sec": 350.86,
      "start_sec": 347.0,
      "text": "from our current object to the rest node we're looking at, and we're going to find that using"
    },
    {
      "end_sec": 353.98,
      "start_sec": 350.86,
      "text": "the distanceTo method. So we're going to do globalPosition.DistanceTo,"
    }
  ],
  "text": "from our current object to the rest node we're looking at, and we're going to find that using the distanceTo method. So we're going to do globalPosition.DistanceTo,",
  "words": [
    {
      "end_sec": 347.24,
      "start_sec": 347.0,
      "word": "from"
    },
    {
      "end_sec": 347.46,
      "start_sec": 347.24,
      "word": "our"
    },
    {
      "end_sec": 347.78,
      "start_sec": 347.46,
      "word": "current"
    },
    {
      "end_sec": 348.2,
      "start_sec": 347.78,
      "word": "object"
    },
    {
      "end_sec": 348.56,
      "start_sec": 348.2,
      "word": "to"
    },
    {
      "end_sec": 348.66,
      "start_sec": 348.56,
      "word": "the"
    },
    {
      "end_sec": 348.92,
      "start_sec": 348.66,
      "word": "rest"
    },
    {
      "end_sec": 349.08,
      "start_sec": 348.92,
      "word": "node"
    },
    {
      "end_sec": 349.22,
      "start_sec": 349.08,
      "word": "we're"
    },
    {
      "end_sec": 349.44,
      "start_sec": 349.22,
      "word": "looking"
    },
    {
      "end_sec": 349.76,
      "start_sec": 349.44,
      "word": "at"
    },
    {
      "end_sec": 349.9,
      "start_sec": 349.86,
      "word": "and"
    },
    {
      "end_sec": 350.1,
      "start_sec": 349.9,
      "word": "we're"
    },
    {
      "end_sec": 350.12,
      "start_sec": 350.1,
      "word": "going"
    },
    {
      "end_sec": 350.22,
      "start_sec": 350.12,
      "word": "to"
    },
    {
      "end_sec": 350.42,
      "start_sec": 350.22,
      "word": "find"
    },
    {
      "end_sec": 350.62,
      "start_sec": 350.42,
      "word": "that"
    },
    {
      "end_sec": 350.86,
      "start_sec": 350.62,
      "word": "using"
    },
    {
      "end_sec": 351.1,
      "start_sec": 350.86,
      "word": "the"
    },
    {
      "end_sec": 351.68,
      "start_sec": 351.1,
      "word": "distanceTo"
    },
    {
      "end_sec": 352.06,
      "start_sec": 351.68,
      "word": "method"
    },
    {
      "end_sec": 352.54,
      "start_sec": 352.28,
      "word": "So"
    },
    {
      "end_sec": 352.68,
      "start_sec": 352.54,
      "word": "we're"
    },
    {
      "end_sec": 352.68,
      "start_sec": 352.68,
      "word": "going"
    },
    {
      "end_sec": 352.78,
      "start_sec": 352.68,
      "word": "to"
    },
    {
      "end_sec": 352.92,
      "start_sec": 352.78,
      "word": "do"
    },
    {
      "end_sec": 353.54,
      "start_sec": 352.92,
      "word": "globalPosition"
    },
    {
      "end_sec": 353.98,
      "start_sec": 353.54,
      "word": "DistanceTo"
    }
  ]
}
````

## BI-01 / handwritten-digits-speech

- 模态：`asr`
- 状态：`completed`
- 延迟：2534.76 ms
- 用量：`{   "reported": false }`

````json
{
  "segments": [
    {
      "end_sec": 105.08,
      "start_sec": 100.0,
      "text": "learning. What we're going to do is put together a neural network that can learn to recognize"
    },
    {
      "end_sec": 106.04,
      "start_sec": 105.14,
      "text": "handwritten digits."
    }
  ],
  "text": "learning. What we're going to do is put together a neural network that can learn to recognize handwritten digits.",
  "words": [
    {
      "end_sec": 100.46,
      "start_sec": 100.0,
      "word": "learning"
    },
    {
      "end_sec": 101.2,
      "start_sec": 100.76,
      "word": "What"
    },
    {
      "end_sec": 101.28,
      "start_sec": 101.2,
      "word": "we're"
    },
    {
      "end_sec": 101.44,
      "start_sec": 101.28,
      "word": "going"
    },
    {
      "end_sec": 101.54,
      "start_sec": 101.44,
      "word": "to"
    },
    {
      "end_sec": 101.62,
      "start_sec": 101.54,
      "word": "do"
    },
    {
      "end_sec": 101.9,
      "start_sec": 101.62,
      "word": "is"
    },
    {
      "end_sec": 102.04,
      "start_sec": 101.9,
      "word": "put"
    },
    {
      "end_sec": 102.44,
      "start_sec": 102.04,
      "word": "together"
    },
    {
      "end_sec": 102.72,
      "start_sec": 102.44,
      "word": "a"
    },
    {
      "end_sec": 102.98,
      "start_sec": 102.72,
      "word": "neural"
    },
    {
      "end_sec": 103.32,
      "start_sec": 102.98,
      "word": "network"
    },
    {
      "end_sec": 103.66,
      "start_sec": 103.32,
      "word": "that"
    },
    {
      "end_sec": 103.96,
      "start_sec": 103.66,
      "word": "can"
    },
    {
      "end_sec": 104.18,
      "start_sec": 103.96,
      "word": "learn"
    },
    {
      "end_sec": 105.08,
      "start_sec": 104.18,
      "word": "to"
    },
    {
      "end_sec": 105.08,
      "start_sec": 105.08,
      "word": "recognize"
    },
    {
      "end_sec": 105.72,
      "start_sec": 105.14,
      "word": "handwritten"
    },
    {
      "end_sec": 106.04,
      "start_sec": 105.72,
      "word": "digits"
    }
  ]
}
````

## BI-06 / cake-description-part-1

- 模态：`asr`
- 状态：`completed`
- 延迟：2930.29 ms
- 用量：`{   "reported": false }`

````json
{
  "segments": [
    {
      "end_sec": 6.84,
      "start_sec": 5.0,
      "text": "中间有点像布丁"
    },
    {
      "end_sec": 8.74,
      "start_sec": 7.06,
      "text": "吃起来和冰淇淋一样"
    },
    {
      "end_sec": 12.28,
      "start_sec": 8.76,
      "text": "最最关键是它的做法比一般的芝士蛋糕要简单"
    },
    {
      "end_sec": 13.72,
      "start_sec": 12.28,
      "text": "不需要打发蛋清"
    },
    {
      "end_sec": 16.0,
      "start_sec": 13.72,
      "text": "新手做起来真的一点难度都没有"
    },
    {
      "end_sec": 21.6,
      "start_sec": 19.46,
      "text": "做之前先在模具里铺一层油纸"
    },
    {
      "end_sec": 23.98,
      "start_sec": 21.94,
      "text": "直接暴力点塞进去就好了"
    },
    {
      "end_sec": 26.36,
      "start_sec": 23.98,
      "text": "这样烤出来的蛋糕看起来就比较狂野"
    },
    {
      "end_sec": 29.48,
      "start_sec": 27.5,
      "text": "多出来的边边要修剪掉"
    },
    {
      "end_sec": 31.68,
      "start_sec": 30.14,
      "text": "现在再准备一些奶油奶酪"
    },
    {
      "end_sec": 33.5,
      "start_sec": 32.0,
      "text": "因为现在天气比较冷"
    },
    {
      "end_sec": 34.56,
      "start_sec": 33.5,
      "text": "不太容易软化"
    }
  ],
  "text": "中间有点像布丁 吃起来和冰淇淋一样 最最关键是它的做法比一般的芝士蛋糕要简单 不需要打发蛋清 新手做起来真的一点难度都没有 做之前先在模具里铺一层油纸 直接暴力点塞进去就好了 这样烤出来的蛋糕看起来就比较狂野 多出来的边边要修剪掉 现在再准备一些奶油奶酪 因为现在天气比较冷 不太容易软化",
  "words": [
    {
      "end_sec": 5.16,
      "start_sec": 5.0,
      "word": "中"
    },
    {
      "end_sec": 6.02,
      "start_sec": 5.16,
      "word": "间"
    },
    {
      "end_sec": 6.24,
      "start_sec": 6.02,
      "word": "有"
    },
    {
      "end_sec": 6.42,
      "start_sec": 6.24,
      "word": "点"
    },
    {
      "end_sec": 6.66,
      "start_sec": 6.42,
      "word": "像"
    },
    {
      "end_sec": 6.74,
      "start_sec": 6.66,
      "word": "布"
    },
    {
      "end_sec": 6.84,
      "start_sec": 6.74,
      "word": "丁"
    },
    {
      "end_sec": 7.46,
      "start_sec": 7.06,
      "word": "吃"
    },
    {
      "end_sec": 7.62,
      "start_sec": 7.46,
      "word": "起"
    },
    {
      "end_sec": 7.82,
      "start_sec": 7.62,
      "word": "来"
    },
    {
      "end_sec": 8.04,
      "start_sec": 7.82,
      "word": "和"
    },
    {
      "end_sec": 8.22,
      "start_sec": 8.04,
      "word": "冰"
    },
    {
      "end_sec": 8.32,
      "start_sec": 8.22,
      "word": "淇"
    },
    {
      "end_sec": 8.38,
      "start_sec": 8.32,
      "word": "淋"
    },
    {
      "end_sec": 8.58,
      "start_sec": 8.38,
      "word": "一"
    },
    {
      "end_sec": 8.74,
      "start_sec": 8.58,
      "word": "样"
    },
    {
      "end_sec": 9.16,
      "start_sec": 8.76,
      "word": "最"
    },
    {
      "end_sec": 9.4,
      "start_sec": 9.16,
      "word": "最"
    },
    {
      "end_sec": 9.54,
      "start_sec": 9.4,
      "word": "关"
    },
    {
      "end_sec": 9.62,
      "start_sec": 9.54,
      "word": "键"
    },
    {
      "end_sec": 9.94,
      "start_sec": 9.62,
      "word": "是"
    },
    {
      "end_sec": 10.16,
      "start_sec": 9.94,
      "word": "它的"
    },
    {
      "end_sec": 10.38,
      "start_sec": 10.16,
      "word": "做"
    },
    {
      "end_sec": 10.54,
      "start_sec": 10.38,
      "word": "法"
    },
    {
      "end_sec": 10.74,
      "start_sec": 10.54,
      "word": "比"
    },
    {
      "end_sec": 10.94,
      "start_sec": 10.74,
      "word": "一"
    },
    {
      "end_sec": 11.02,
      "start_sec": 10.94,
      "word": "般"
    },
    {
      "end_sec": 11.24,
      "start_sec": 11.02,
      "word": "的"
    },
    {
      "end_sec": 11.38,
      "start_sec": 11.24,
      "word": "芝"
    },
    {
      "end_sec": 11.38,
      "start_sec": 11.38,
      "word": "士"
    },
    {
      "end_sec": 11.54,
      "start_sec": 11.38,
      "word": "蛋"
    },
    {
      "end_sec": 11.6,
      "start_sec": 11.54,
      "word": "糕"
    },
    {
      "end_sec": 11.96,
      "start_sec": 11.6,
      "word": "要"
    },
    {
      "end_sec": 12.0,
      "start_sec": 11.96,
      "word": "简"
    },
    {
      "end_sec": 12.28,
      "start_sec": 12.0,
      "word": "单"
    },
    {
      "end_sec": 12.62,
      "start_sec": 12.28,
      "word": "不"
    },
    {
      "end_sec": 12.8,
      "start_sec": 12.62,
      "word": "需要"
    },
    {
      "end_sec": 13.0,
      "start_sec": 12.8,
      "word": "打"
    },
    {
      "end_sec": 13.12,
      "start_sec": 13.0,
      "word": "发"
    },
    {
      "end_sec": 13.38,
      "start_sec": 13.12,
      "word": "蛋"
    },
    {
      "end_sec": 13.72,
      "start_sec": 13.38,
      "word": "清"
    },
    {
      "end_sec": 14.04,
      "start_sec": 13.72,
      "word": "新"
    },
    {
      "end_sec": 14.18,
      "start_sec": 14.04,
      "word": "手"
    },
    {
      "end_sec": 14.4,
      "start_sec": 14.18,
      "word": "做"
    },
    {
      "end_sec": 14.5,
      "start_sec": 14.4,
      "word": "起"
    },
    {
      "end_sec": 14.7,
      "start_sec": 14.5,
      "word": "来"
    },
    {
      "end_sec": 14.98,
      "start_sec": 14.7,
      "word": "真的"
    },
    {
      "end_sec": 15.26,
      "start_sec": 14.98,
      "word": "一点"
    },
    {
      "end_sec": 15.44,
      "start_sec": 15.26,
      "word": "难"
    },
    {
      "end_sec": 15.54,
      "start_sec": 15.44,
      "word": "度"
    },
    {
      "end_sec": 15.76,
      "start_sec": 15.54,
      "word": "都"
    },
    {
      "end_sec": 16.0,
      "start_sec": 15.76,
      "word": "没有"
    },
    {
      "end_sec": 19.86,
      "start_sec": 19.46,
      "word": "做"
    },
    {
      "end_sec": 20.06,
      "start_sec": 19.86,
      "word": "之前"
    },
    {
      "end_sec": 20.28,
      "start_sec": 20.06,
      "word": "先"
    },
    {
      "end_sec": 20.48,
      "start_sec": 20.28,
      "word": "在"
    },
    {
      "end_sec": 20.58,
      "start_sec": 20.48,
      "word": "模"
    },
    {
      "end_sec": 20.66,
      "start_sec": 20.58,
      "word": "具"
    },
    {
      "end_sec": 20.94,
      "start_sec": 20.66,
      "word": "里"
    },
    {
      "end_sec": 20.94,
      "start_sec": 20.94,
      "word": "铺"
    },
    {
      "end_sec": 21.22,
      "start_sec": 20.94,
      "word": "一"
    },
    {
      "end_sec": 21.22,
      "start_sec": 21.22,
      "word": "层"
    },
    {
      "end_sec": 21.46,
      "start_sec": 21.22,
      "word": "油"
    },
    {
      "end_sec": 21.6,
      "start_sec": 21.46,
      "word": "纸"
    },
    {
      "end_sec": 22.34,
      "start_sec": 21.94,
      "word": "直接"
    },
    {
      "end_sec": 22.54,
      "start_sec": 22.34,
      "word": "暴"
    },
    {
      "end_sec": 22.58,
      "start_sec": 22.54,
      "word": "力"
    },
    {
      "end_sec": 22.86,
      "start_sec": 22.58,
      "word": "点"
    },
    {
      "end_sec": 22.92,
      "start_sec": 22.86,
      "word": "塞"
    },
    {
      "end_sec": 23.06,
      "start_sec": 22.92,
      "word": "进"
    },
    {
      "end_sec": 23.22,
      "start_sec": 23.06,
      "word": "去"
    },
    {
      "end_sec": 23.32,
      "start_sec": 23.22,
      "word": "就"
    },
    {
      "end_sec": 23.98,
      "start_sec": 23.32,
      "word": "好了"
    },
    {
      "end_sec": 24.3,
      "start_sec": 23.98,
      "word": "这样"
    },
    {
      "end_sec": 24.42,
      "start_sec": 24.3,
      "word": "烤"
    },
    {
      "end_sec": 24.66,
      "start_sec": 24.42,
      "word": "出来"
    },
    {
      "end_sec": 24.9,
      "start_sec": 24.66,
      "word": "的"
    },
    {
      "end_sec": 24.92,
      "start_sec": 24.9,
      "word": "蛋"
    },
    {
      "end_sec": 25.0,
      "start_sec": 24.92,
      "word": "糕"
    },
    {
      "end_sec": 25.22,
      "start_sec": 25.0,
      "word": "看"
    },
    {
      "end_sec": 25.32,
      "start_sec": 25.22,
      "word": "起"
    },
    {
      "end_sec": 25.38,
      "start_sec": 25.32,
      "word": "来"
    },
    {
      "end_sec": 25.58,
      "start_sec": 25.38,
      "word": "就"
    },
    {
      "end_sec": 25.64,
      "start_sec": 25.58,
      "word": "比"
    },
    {
      "end_sec": 25.76,
      "start_sec": 25.64,
      "word": "较"
    },
    {
      "end_sec": 26.18,
      "start_sec": 25.76,
      "word": "狂"
    },
    {
      "end_sec": 26.36,
      "start_sec": 26.18,
      "word": "野"
    },
    {
      "end_sec": 27.9,
      "start_sec": 27.5,
      "word": "多"
    },
    {
      "end_sec": 28.08,
      "start_sec": 27.9,
      "word": "出来"
    },
    {
      "end_sec": 28.36,
      "start_sec": 28.08,
      "word": "的"
    },
    {
      "end_sec": 28.36,
      "start_sec": 28.36,
      "word": "边"
    },
    {
      "end_sec": 28.48,
      "start_sec": 28.36,
      "word": "边"
    },
    {
      "end_sec": 28.72,
      "start_sec": 28.48,
      "word": "要"
    },
    {
      "end_sec": 28.88,
      "start_sec": 28.72,
      "word": "修"
    },
    {
      "end_sec": 28.92,
      "start_sec": 28.88,
      "word": "剪"
    },
    {
      "end_sec": 29.48,
      "start_sec": 28.92,
      "word": "掉"
    },
    {
      "end_sec": 30.54,
      "start_sec": 30.14,
      "word": "现在"
    },
    {
      "end_sec": 30.8,
      "start_sec": 30.54,
      "word": "再"
    },
    {
      "end_sec": 30.86,
      "start_sec": 30.8,
      "word": "准"
    },
    {
      "end_sec": 30.96,
      "start_sec": 30.86,
      "word": "备"
    },
    {
      "end_sec": 31.22,
      "start_sec": 30.96,
      "word": "一些"
    },
    {
      "end_sec": 31.34,
      "start_sec": 31.22,
      "word": "奶"
    },
    {
      "end_sec": 31.4,
      "start_sec": 31.34,
      "word": "油"
    },
    {
      "end_sec": 31.58,
      "start_sec": 31.4,
      "word": "奶"
    },
    {
      "end_sec": 31.68,
      "start_sec": 31.58,
      "word": "酪"
    },
    {
      "end_sec": 32.4,
      "start_sec": 32.0,
      "word": "因为"
    },
    {
      "end_sec": 32.68,
      "start_sec": 32.4,
      "word": "现在"
    },
    {
      "end_sec": 32.84,
      "start_sec": 32.68,
      "word": "天"
    },
    {
      "end_sec": 32.92,
      "start_sec": 32.84,
      "word": "气"
    },
    {
      "end_sec": 33.1,
      "start_sec": 32.92,
      "word": "比"
    },
    {
      "end_sec": 33.2,
      "start_sec": 33.1,
      "word": "较"
    },
    {
      "end_sec": 33.5,
      "start_sec": 33.2,
      "word": "冷"
    },
    {
      "end_sec": 33.72,
      "start_sec": 33.5,
      "word": "不"
    },
    {
      "end_sec": 33.86,
      "start_sec": 33.72,
      "word": "太"
    },
    {
      "end_sec": 34.08,
      "start_sec": 33.86,
      "word": "容易"
    },
    {
      "end_sec": 34.36,
      "start_sec": 34.08,
      "word": "软"
    },
    {
      "end_sec": 34.56,
      "start_sec": 34.36,
      "word": "化"
    }
  ]
}
````

## BI-06 / cake-description-part-2

- 模态：`asr`
- 状态：`completed`
- 延迟：4659.26 ms
- 用量：`{   "reported": false }`

````json
{
  "segments": [
    {
      "end_sec": 36.1,
      "start_sec": 35.0,
      "text": "隔熱水把它攪軟"
    },
    {
      "end_sec": 37.24,
      "start_sec": 36.1,
      "text": "水不需要很熱"
    },
    {
      "end_sec": 39.12,
      "start_sec": 37.24,
      "text": "剛好開始冒泡的狀態就可以了"
    },
    {
      "end_sec": 41.28,
      "start_sec": 39.12,
      "text": "太燙的話很容易把奶油奶酪熱融化"
    },
    {
      "end_sec": 44.44,
      "start_sec": 42.36,
      "text": "把它攪到這種軟趴趴的樣子就可以了"
    },
    {
      "end_sec": 46.94,
      "start_sec": 45.1,
      "text": "之後加入砂糖攪拌均勻"
    },
    {
      "end_sec": 48.22,
      "start_sec": 46.94,
      "text": "糖的量可以調整"
    },
    {
      "end_sec": 49.74,
      "start_sec": 48.22,
      "text": "不喜歡甜可以適當減少"
    },
    {
      "end_sec": 51.74,
      "start_sec": 49.74,
      "text": "不過我覺得這個甜味真的剛剛好"
    },
    {
      "end_sec": 53.88,
      "start_sec": 51.74,
      "text": "再減少的話可能就不太好吃了"
    },
    {
      "end_sec": 56.02,
      "start_sec": 54.88,
      "text": "接著再準備三個蛋"
    },
    {
      "end_sec": 57.68,
      "start_sec": 56.02,
      "text": "加入一個蛋黃把它們攪散"
    },
    {
      "end_sec": 63.12,
      "start_sec": 61.48,
      "text": "分次加入奶油奶酪中"
    },
    {
      "end_sec": 63.8,
      "start_sec": 63.12,
      "text": "把它們攪勻"
    },
    {
      "end_sec": 64.98,
      "start_sec": 63.8,
      "text": "每加一次都要把"
    }
  ],
  "text": "隔熱水把它攪軟 水不需要很熱 剛好開始冒泡的狀態就可以了 太燙的話很容易把奶油奶酪熱融化 把它攪到這種軟趴趴的樣子就可以了 之後加入砂糖攪拌均勻 糖的量可以調整 不喜歡甜可以適當減少 不過我覺得這個甜味真的剛剛好 再減少的話可能就不太好吃了 接著再準備三個蛋 加入一個蛋黃把它們攪散 分次加入奶油奶酪中 把它們攪勻 每加一次都要把",
  "words": [
    {
      "end_sec": 35.14,
      "start_sec": 35.0,
      "word": "隔"
    },
    {
      "end_sec": 35.38,
      "start_sec": 35.14,
      "word": "熱"
    },
    {
      "end_sec": 35.5,
      "start_sec": 35.38,
      "word": "水"
    },
    {
      "end_sec": 35.92,
      "start_sec": 35.5,
      "word": "把它"
    },
    {
      "end_sec": 35.92,
      "start_sec": 35.92,
      "word": "攪"
    },
    {
      "end_sec": 36.1,
      "start_sec": 35.92,
      "word": "軟"
    },
    {
      "end_sec": 36.38,
      "start_sec": 36.1,
      "word": "水"
    },
    {
      "end_sec": 36.6,
      "start_sec": 36.38,
      "word": "不"
    },
    {
      "end_sec": 36.8,
      "start_sec": 36.6,
      "word": "需要"
    },
    {
      "end_sec": 37.06,
      "start_sec": 36.8,
      "word": "很"
    },
    {
      "end_sec": 37.24,
      "start_sec": 37.06,
      "word": "熱"
    },
    {
      "end_sec": 37.5,
      "start_sec": 37.24,
      "word": "剛"
    },
    {
      "end_sec": 37.66,
      "start_sec": 37.5,
      "word": "好"
    },
    {
      "end_sec": 37.96,
      "start_sec": 37.66,
      "word": "開始"
    },
    {
      "end_sec": 38.08,
      "start_sec": 37.96,
      "word": "冒"
    },
    {
      "end_sec": 38.14,
      "start_sec": 38.08,
      "word": "泡"
    },
    {
      "end_sec": 38.44,
      "start_sec": 38.14,
      "word": "的"
    },
    {
      "end_sec": 38.44,
      "start_sec": 38.44,
      "word": "狀"
    },
    {
      "end_sec": 38.5,
      "start_sec": 38.44,
      "word": "態"
    },
    {
      "end_sec": 38.82,
      "start_sec": 38.5,
      "word": "就可以"
    },
    {
      "end_sec": 39.12,
      "start_sec": 38.82,
      "word": "了"
    },
    {
      "end_sec": 39.28,
      "start_sec": 39.12,
      "word": "太"
    },
    {
      "end_sec": 39.42,
      "start_sec": 39.28,
      "word": "燙"
    },
    {
      "end_sec": 39.68,
      "start_sec": 39.42,
      "word": "的話"
    },
    {
      "end_sec": 39.82,
      "start_sec": 39.68,
      "word": "很"
    },
    {
      "end_sec": 39.98,
      "start_sec": 39.82,
      "word": "容易"
    },
    {
      "end_sec": 40.18,
      "start_sec": 39.98,
      "word": "把"
    },
    {
      "end_sec": 40.32,
      "start_sec": 40.18,
      "word": "奶"
    },
    {
      "end_sec": 40.4,
      "start_sec": 40.32,
      "word": "油"
    },
    {
      "end_sec": 40.58,
      "start_sec": 40.4,
      "word": "奶"
    },
    {
      "end_sec": 40.58,
      "start_sec": 40.58,
      "word": "酪"
    },
    {
      "end_sec": 40.84,
      "start_sec": 40.58,
      "word": "熱"
    },
    {
      "end_sec": 41.0,
      "start_sec": 40.84,
      "word": "融"
    },
    {
      "end_sec": 41.28,
      "start_sec": 41.0,
      "word": "化"
    },
    {
      "end_sec": 42.72,
      "start_sec": 42.36,
      "word": "把它"
    },
    {
      "end_sec": 42.78,
      "start_sec": 42.72,
      "word": "攪"
    },
    {
      "end_sec": 42.94,
      "start_sec": 42.78,
      "word": "到"
    },
    {
      "end_sec": 43.18,
      "start_sec": 42.94,
      "word": "這種"
    },
    {
      "end_sec": 43.46,
      "start_sec": 43.18,
      "word": "軟"
    },
    {
      "end_sec": 43.5,
      "start_sec": 43.46,
      "word": "趴"
    },
    {
      "end_sec": 43.56,
      "start_sec": 43.5,
      "word": "趴"
    },
    {
      "end_sec": 43.76,
      "start_sec": 43.56,
      "word": "的"
    },
    {
      "end_sec": 43.84,
      "start_sec": 43.76,
      "word": "樣"
    },
    {
      "end_sec": 43.94,
      "start_sec": 43.84,
      "word": "子"
    },
    {
      "end_sec": 44.24,
      "start_sec": 43.94,
      "word": "就可以"
    },
    {
      "end_sec": 44.44,
      "start_sec": 44.24,
      "word": "了"
    },
    {
      "end_sec": 45.24,
      "start_sec": 45.1,
      "word": "之後"
    },
    {
      "end_sec": 45.52,
      "start_sec": 45.24,
      "word": "加入"
    },
    {
      "end_sec": 45.72,
      "start_sec": 45.52,
      "word": "砂"
    },
    {
      "end_sec": 45.8,
      "start_sec": 45.72,
      "word": "糖"
    },
    {
      "end_sec": 46.0,
      "start_sec": 45.8,
      "word": "攪"
    },
    {
      "end_sec": 46.14,
      "start_sec": 46.0,
      "word": "拌"
    },
    {
      "end_sec": 46.4,
      "start_sec": 46.14,
      "word": "均"
    },
    {
      "end_sec": 46.94,
      "start_sec": 46.4,
      "word": "勻"
    },
    {
      "end_sec": 47.24,
      "start_sec": 46.94,
      "word": "糖"
    },
    {
      "end_sec": 47.48,
      "start_sec": 47.24,
      "word": "的"
    },
    {
      "end_sec": 47.54,
      "start_sec": 47.48,
      "word": "量"
    },
    {
      "end_sec": 47.78,
      "start_sec": 47.54,
      "word": "可以"
    },
    {
      "end_sec": 48.0,
      "start_sec": 47.78,
      "word": "調"
    },
    {
      "end_sec": 48.22,
      "start_sec": 48.0,
      "word": "整"
    },
    {
      "end_sec": 48.42,
      "start_sec": 48.22,
      "word": "不"
    },
    {
      "end_sec": 48.62,
      "start_sec": 48.42,
      "word": "喜歡"
    },
    {
      "end_sec": 48.84,
      "start_sec": 48.62,
      "word": "甜"
    },
    {
      "end_sec": 49.1,
      "start_sec": 48.84,
      "word": "可以"
    },
    {
      "end_sec": 49.18,
      "start_sec": 49.1,
      "word": "適"
    },
    {
      "end_sec": 49.26,
      "start_sec": 49.18,
      "word": "當"
    },
    {
      "end_sec": 49.5,
      "start_sec": 49.26,
      "word": "減"
    },
    {
      "end_sec": 49.74,
      "start_sec": 49.5,
      "word": "少"
    },
    {
      "end_sec": 50.06,
      "start_sec": 49.74,
      "word": "不過"
    },
    {
      "end_sec": 50.32,
      "start_sec": 50.06,
      "word": "我覺得"
    },
    {
      "end_sec": 50.56,
      "start_sec": 50.32,
      "word": "這個"
    },
    {
      "end_sec": 50.66,
      "start_sec": 50.56,
      "word": "甜"
    },
    {
      "end_sec": 50.82,
      "start_sec": 50.66,
      "word": "味"
    },
    {
      "end_sec": 51.12,
      "start_sec": 50.82,
      "word": "真的"
    },
    {
      "end_sec": 51.36,
      "start_sec": 51.12,
      "word": "剛剛"
    },
    {
      "end_sec": 51.74,
      "start_sec": 51.36,
      "word": "好"
    },
    {
      "end_sec": 52.08,
      "start_sec": 51.74,
      "word": "再"
    },
    {
      "end_sec": 52.26,
      "start_sec": 52.08,
      "word": "減"
    },
    {
      "end_sec": 52.44,
      "start_sec": 52.26,
      "word": "少"
    },
    {
      "end_sec": 52.74,
      "start_sec": 52.44,
      "word": "的話"
    },
    {
      "end_sec": 52.9,
      "start_sec": 52.74,
      "word": "可能"
    },
    {
      "end_sec": 53.18,
      "start_sec": 52.9,
      "word": "就"
    },
    {
      "end_sec": 53.24,
      "start_sec": 53.18,
      "word": "不"
    },
    {
      "end_sec": 53.38,
      "start_sec": 53.24,
      "word": "太"
    },
    {
      "end_sec": 53.68,
      "start_sec": 53.38,
      "word": "好吃"
    },
    {
      "end_sec": 53.88,
      "start_sec": 53.68,
      "word": "了"
    },
    {
      "end_sec": 54.92,
      "start_sec": 54.88,
      "word": "接"
    },
    {
      "end_sec": 54.94,
      "start_sec": 54.92,
      "word": "著"
    },
    {
      "end_sec": 55.24,
      "start_sec": 54.94,
      "word": "再"
    },
    {
      "end_sec": 55.3,
      "start_sec": 55.24,
      "word": "準備"
    },
    {
      "end_sec": 55.52,
      "start_sec": 55.3,
      "word": "三"
    },
    {
      "end_sec": 55.68,
      "start_sec": 55.52,
      "word": "個"
    },
    {
      "end_sec": 56.02,
      "start_sec": 55.68,
      "word": "蛋"
    },
    {
      "end_sec": 56.34,
      "start_sec": 56.02,
      "word": "加入"
    },
    {
      "end_sec": 56.6,
      "start_sec": 56.34,
      "word": "一個"
    },
    {
      "end_sec": 56.76,
      "start_sec": 56.6,
      "word": "蛋"
    },
    {
      "end_sec": 56.94,
      "start_sec": 56.76,
      "word": "黃"
    },
    {
      "end_sec": 57.2,
      "start_sec": 56.94,
      "word": "把它"
    },
    {
      "end_sec": 57.38,
      "start_sec": 57.2,
      "word": "們"
    },
    {
      "end_sec": 57.48,
      "start_sec": 57.38,
      "word": "攪"
    },
    {
      "end_sec": 57.68,
      "start_sec": 57.48,
      "word": "散"
    },
    {
      "end_sec": 61.88,
      "start_sec": 61.48,
      "word": "分"
    },
    {
      "end_sec": 61.94,
      "start_sec": 61.88,
      "word": "次"
    },
    {
      "end_sec": 62.2,
      "start_sec": 61.94,
      "word": "加入"
    },
    {
      "end_sec": 62.38,
      "start_sec": 62.2,
      "word": "奶"
    },
    {
      "end_sec": 62.5,
      "start_sec": 62.38,
      "word": "油"
    },
    {
      "end_sec": 62.64,
      "start_sec": 62.5,
      "word": "奶"
    },
    {
      "end_sec": 62.72,
      "start_sec": 62.64,
      "word": "酪"
    },
    {
      "end_sec": 63.12,
      "start_sec": 62.72,
      "word": "中"
    },
    {
      "end_sec": 63.32,
      "start_sec": 63.12,
      "word": "把它"
    },
    {
      "end_sec": 63.48,
      "start_sec": 63.32,
      "word": "們"
    },
    {
      "end_sec": 63.6,
      "start_sec": 63.48,
      "word": "攪"
    },
    {
      "end_sec": 63.8,
      "start_sec": 63.6,
      "word": "勻"
    },
    {
      "end_sec": 64.34,
      "start_sec": 63.8,
      "word": "每"
    },
    {
      "end_sec": 64.54,
      "start_sec": 64.34,
      "word": "加"
    },
    {
      "end_sec": 64.74,
      "start_sec": 64.54,
      "word": "一次"
    },
    {
      "end_sec": 64.86,
      "start_sec": 64.74,
      "word": "都"
    },
    {
      "end_sec": 64.98,
      "start_sec": 64.86,
      "word": "要"
    },
    {
      "end_sec": 64.98,
      "start_sec": 64.98,
      "word": "把"
    }
  ]
}
````

## BI-06 / cake-description-part-3

- 模态：`asr`
- 状态：`completed`
- 延迟：981.44 ms
- 用量：`{   "reported": false }`

````json
{
  "segments": [
    {
      "end_sec": 66.52,
      "start_sec": 65.0,
      "text": "把它搅到吸收,再加下一次"
    },
    {
      "end_sec": 68.24,
      "start_sec": 66.76,
      "text": "一定要把它们充分搅匀"
    },
    {
      "end_sec": 75.98,
      "start_sec": 74.46,
      "text": "然后再分次加入奶油"
    }
  ],
  "text": "把它搅到吸收,再加下一次 一定要把它们充分搅匀 然后再分次加入奶油",
  "words": [
    {
      "end_sec": 65.04,
      "start_sec": 65.0,
      "word": "把它"
    },
    {
      "end_sec": 65.24,
      "start_sec": 65.04,
      "word": "搅"
    },
    {
      "end_sec": 65.48,
      "start_sec": 65.24,
      "word": "到"
    },
    {
      "end_sec": 65.64,
      "start_sec": 65.48,
      "word": "吸"
    },
    {
      "end_sec": 65.8,
      "start_sec": 65.64,
      "word": "收"
    },
    {
      "end_sec": 65.98,
      "start_sec": 65.94,
      "word": "再"
    },
    {
      "end_sec": 66.08,
      "start_sec": 65.98,
      "word": "加"
    },
    {
      "end_sec": 66.26,
      "start_sec": 66.08,
      "word": "下"
    },
    {
      "end_sec": 66.52,
      "start_sec": 66.26,
      "word": "一次"
    },
    {
      "end_sec": 67.12,
      "start_sec": 66.76,
      "word": "一定要"
    },
    {
      "end_sec": 67.34,
      "start_sec": 67.12,
      "word": "把它"
    },
    {
      "end_sec": 67.68,
      "start_sec": 67.34,
      "word": "们"
    },
    {
      "end_sec": 67.68,
      "start_sec": 67.68,
      "word": "充"
    },
    {
      "end_sec": 67.84,
      "start_sec": 67.68,
      "word": "分"
    },
    {
      "end_sec": 68.08,
      "start_sec": 67.84,
      "word": "搅"
    },
    {
      "end_sec": 68.24,
      "start_sec": 68.08,
      "word": "匀"
    },
    {
      "end_sec": 74.82,
      "start_sec": 74.46,
      "word": "然后"
    },
    {
      "end_sec": 75.1,
      "start_sec": 74.82,
      "word": "再"
    },
    {
      "end_sec": 75.26,
      "start_sec": 75.1,
      "word": "分"
    },
    {
      "end_sec": 75.38,
      "start_sec": 75.26,
      "word": "次"
    },
    {
      "end_sec": 75.6,
      "start_sec": 75.38,
      "word": "加入"
    },
    {
      "end_sec": 75.82,
      "start_sec": 75.6,
      "word": "奶"
    },
    {
      "end_sec": 75.98,
      "start_sec": 75.82,
      "word": "油"
    }
  ]
}
````

## YT-01 / applications-slide

- 模态：`vision+ocr-assist`
- 状态：`completed`
- 延迟：5744.55 ms
- 用量：`{   "input_tokens": 1101,   "output_tokens": 369,   "reported": true,   "total_tokens": 1470 }`

````json
{
  "actions": [
    "The lecturer speaks directly to the camera and gestures with his hands."
  ],
  "frame_observations": [
    {
      "frame_index": 1,
      "observation": "A male lecturer wearing a light blue button-up shirt stands centered and speaks toward the camera. A monitor at left shows a slide with Stanford Online and DeepLearning.AI branding, the title “Machine Learning,” and the subtitle “Applications of Machine Learning.”",
      "screen_text": [
        "Stanford Online",
        "DeepLearning.AI",
        "Machine Learning",
        "Applications of Machine Learning"
      ],
      "timestamp_sec": 18
    },
    {
      "frame_index": 2,
      "observation": "The same lecturer remains in the same studio setup, gesturing while speaking. The monitor shows the same slide.",
      "screen_text": [
        "Stanford Online",
        "DeepLearning.AI",
        "Machine Learning",
        "Applications of Machine Learning"
      ],
      "timestamp_sec": 20
    },
    {
      "frame_index": 3,
      "observation": "The same lecturer continues speaking and gesturing. The same machine-learning slide remains visible on the monitor.",
      "screen_text": [
        "Stanford Online",
        "DeepLearning.AI",
        "Machine Learning",
        "Applications of Machine Learning"
      ],
      "timestamp_sec": 22
    }
  ],
  "same_scene": true,
  "screen_text": [
    "Stanford Online",
    "DeepLearning.AI",
    "Machine Learning",
    "Applications of Machine Learning"
  ],
  "screen_text_present": true,
  "search_terms": [
    "Stanford Online Machine Learning Applications of Machine Learning",
    "DeepLearning.AI machine learning lecture",
    "machine learning applications lecture"
  ],
  "summary": "A male lecturer in a light blue shirt speaks to camera in front of a monitor displaying a machine-learning lecture slide. The visible slide appears to be about applications of machine learning and includes Stanford Online branding.",
  "uncertainties": [
    "The lecturer's name is not visible, so identity cannot be determined from these frames.",
    "The small text and details on the secondary device below the monitor are not legible."
  ]
}
````

## YT-03 / input-map-ui

- 模态：`vision+ocr-assist`
- 状态：`completed`
- 延迟：4687.05 ms
- 用量：`{   "input_tokens": 1100,   "output_tokens": 527,   "reported": true,   "total_tokens": 1627 }`

````json
{
  "actions": [
    "Frame 1: The Godot Input Map list is visible, including built-in UI actions and a 'click' action; a mouse-input selection menu is open near the lower right.",
    "Frame 2: A 'Please Confirm...' dialog is visible for adding a mouse button input, showing Device 0 and Left Button, with Add and Cancel buttons.",
    "Frame 3: The dialog is gone, and the 'click' action visibly contains 'Device 0, Left Button.'"
  ],
  "frame_observations": [
    {
      "frame_index": 1,
      "observation": "Godot Input Map-style action list with the same visible UI actions and a 'click' row. A popup menu for input types is open at the lower right, with entries including Key, Joy Button, Joy Axis, and Mouse Button.",
      "screen_text": [
        "ui_left",
        "ui_right",
        "ui_up",
        "ui_down",
        "ui_page_up",
        "ui_page_down",
        "ui_home",
        "ui_end",
        "click",
        "Key",
        "Joy Button",
        "Joy Axis",
        "Mouse Button",
        "Close"
      ],
      "timestamp_sec": 54
    },
    {
      "frame_index": 2,
      "observation": "The same action list remains visible behind a centered confirmation dialog for a mouse button input. The dialog appears to show Device 0 and Left Button.",
      "screen_text": [
        "Please Confirm...",
        "Device:",
        "Mouse Button Index:",
        "Device 0",
        "Left Button",
        "Add",
        "Cancel"
      ],
      "timestamp_sec": 55
    },
    {
      "frame_index": 3,
      "observation": "The same Input Map list is visible without the dialog. The 'click' action now shows a child entry for Device 0, Left Button.",
      "screen_text": [
        "click",
        "Device 0, Left Button",
        "Close"
      ],
      "timestamp_sec": 56
    }
  ],
  "same_scene": true,
  "screen_text": [
    "Action:",
    "Action",
    "Deadzone",
    "ui_left",
    "ui_right",
    "ui_up",
    "ui_down",
    "ui_page_up",
    "ui_page_down",
    "ui_home",
    "ui_end",
    "click",
    "Close",
    "Please Confirm...",
    "Device 0",
    "Left Button",
    "Add",
    "Cancel"
  ],
  "screen_text_present": true,
  "search_terms": [
    "Godot Input Map",
    "Please Confirm mouse button",
    "Device 0 Left Button",
    "ui_left ui_right ui_up ui_down"
  ],
  "summary": "All three frames visibly show the same Godot Input Map action-list scene, with a mouse-button assignment dialog appearing in frame 2 and the resulting assignment visible in frame 3.",
  "uncertainties": [
    "Some small labels and icon/tool-tip text are low resolution and may not be fully legible.",
    "The exact Godot version and some existing device-button entries cannot be determined from the frames alone."
  ]
}
````

## YT-03 / code-editor-current-object

- 模态：`vision+ocr-assist`
- 状态：`completed`
- 延迟：5600.03 ms
- 用量：`{   "input_tokens": 1101,   "output_tokens": 534,   "reported": true,   "total_tokens": 1635 }`

````json
{
  "actions": [
    "The editor types `var shortest_dist = 75` on line 27.",
    "The editor types `var child in rest_nodes:` on line 28.",
    "The editor begins typing `var distance = global_position.distance_to(child` on line 29."
  ],
  "frame_observations": [
    {
      "frame_index": 1,
      "observation": "Dark code editor showing lines 12–27. The caret is at the end of a partially typed declaration on line 27, visibly beginning `var shorte` (the remainder is not yet present).",
      "screen_text": [
        "var shorte"
      ],
      "timestamp_sec": 328
    },
    {
      "frame_index": 2,
      "observation": "Same editor and code position. Line 27 now reads `var shortest_dist = 75`; line 28 reads `var child in rest_nodes:` with the caret at the end.",
      "screen_text": [
        "var shortest_dist = 75",
        "var child in rest_nodes:"
      ],
      "timestamp_sec": 340
    },
    {
      "frame_index": 3,
      "observation": "Same editor and code position. Line 29 contains a partially visible distance expression beginning `var distance = global_position.distance_to(child`; the line appears unfinished.",
      "screen_text": [
        "var distance = global_position.distance_to(child"
      ],
      "timestamp_sec": 352
    }
  ],
  "same_scene": true,
  "screen_text": [
    "func _on_Area2D_input_event(viewport, event, shape_idx):",
    "if Input.is_action_just_pressed(\"click\"):",
    "selected = true",
    "func _physics_process(delta):",
    "if selected:",
    "global_position = lerp(global_position, get_global_mouse_position(), 25 * delta)",
    "else:",
    "global_position = lerp(global_position, rest_point, 10 * delta)",
    "func _input(event):",
    "if event is InputEventMouseButton:",
    "if event.button_index == BUTTON_LEFT and not event.pressed:",
    "selected = false",
    "var shortest_dist = 75",
    "var child in rest_nodes:",
    "var distance = global_position.distance_to(child"
  ],
  "screen_text_present": true,
  "search_terms": [
    "Godot script editor",
    "GDScript _input event",
    "InputEventMouseButton BUTTON_LEFT",
    "shortest_dist",
    "rest_nodes",
    "distance_to"
  ],
  "summary": "All three frames show the same dark-themed code editor with a Godot/GDScript-like script. The editor is progressively typing code inside an input handler, adding variables and a distance calculation.",
  "uncertainties": [
    "The application name and exact editor version are not visible.",
    "The final line is cut off or still being typed, so its complete expression cannot be determined.",
    "Some indentation and punctuation are difficult to verify at this resolution."
  ]
}
````

## BI-01 / network-and-hard-subtitles

- 模态：`vision+ocr-assist`
- 状态：`completed`
- 延迟：8326.53 ms
- 用量：`{   "input_tokens": 3548,   "output_tokens": 687,   "reported": true,   "total_tokens": 4235 }`

````json
{
  "actions": [
    "A neural-network thumbnail is shown inside a red rounded rectangle with a play symbol.",
    "A second red rounded rectangle containing a play symbol appears under the learning label.",
    "The animation cuts to a neural-network schematic connecting multiple layers of nodes, with output labels 0–9 and a blurred handwritten digit 7 at left."
  ],
  "frame_observations": [
    {
      "frame_index": 1,
      "observation": "Black background with labels 'Structure 结构' and 'learning 学习' near the top-left/center, and 'Machine learning', 'Neural network', plus Chinese equivalents at upper right. Three blue cartoon figures and one brown figure occupy the lower area. A red rounded thumbnail with a neural-network image and play triangle is visible.",
      "screen_text": [
        "Structure 结构",
        "learning 学习",
        "Machine learning",
        "Neural network",
        "机器 学习",
        "神经 网络",
        "下一期将会讨论它的学习原理",
        "and the following one is going to tackle learning"
      ],
      "timestamp_sec": 100
    },
    {
      "frame_index": 2,
      "observation": "Same black-background cartoon scene and top labels. A second red rounded play-button box is now visible beneath 'learning 学习'; the first box remains beneath 'Structure 结构'.",
      "screen_text": [
        "Structure 结构",
        "learning 学习",
        "Machine learning",
        "Neural network",
        "机器 学习",
        "神经 网络",
        "接下来我们将会手把手搭建一个",
        "What we're going to do is put together a neural network"
      ],
      "timestamp_sec": 102
    },
    {
      "frame_index": 3,
      "observation": "The scene cuts to a large neural-network visualization on black. A blurred image of the digit '7' appears in a square at left. Several columns of cyan-outlined nodes are densely connected by thin lines; a brace labels the input size as '784'. The rightmost column is labeled 0 through 9.",
      "screen_text": [
        "784",
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "接下来我们将会手把手搭建一个",
        "What we're going to do is put together a neural network"
      ],
      "timestamp_sec": 103
    }
  ],
  "same_scene": false,
  "screen_text": [
    "Structure 结构",
    "learning 学习",
    "Machine learning",
    "Neural network",
    "机器 学习",
    "神经 网络",
    "下一期将会讨论它的学习原理",
    "and the following one is going to tackle learning",
    "接下来我们将会手把手搭建一个",
    "What we're going to do is put together a neural network",
    "784",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9"
  ],
  "screen_text_present": true,
  "search_terms": [
    "neural network diagram 784 0 1 2 3 4 5 6 7 8 9",
    "machine learning neural network animation",
    "Structure 结构 learning 学习"
  ],
  "summary": "Across all three frames, a black-background educational animation shows cartoon characters beneath labels for machine learning and neural networks. Frames 1–2 show topic icons; frame 3 cuts to a detailed digit-recognition neural-network diagram.",
  "uncertainties": [
    "The Chinese subtitles are read from visible text but may contain minor character or spacing uncertainty.",
    "The digit in the left input image appears to be 7, but it is blurred.",
    "The exact number of hidden-layer nodes and connections is not counted."
  ]
}
````

## BI-06 / spoon-cake-action

- 模态：`vision`
- 状态：`completed`
- 延迟：4884.77 ms
- 用量：`{   "input_tokens": 3551,   "output_tokens": 430,   "reported": true,   "total_tokens": 3981 }`

````json
{
  "actions": [
    "At 5 seconds, a spoon lies against the browned top surface of the cake.",
    "At 6.5 seconds, the spoon remains in contact with the cake top, with no clearly visible displacement or removed piece.",
    "At 8 seconds, the spoon is positioned more vertically and appears to press into the cake's top surface; no lifted scoop is visible."
  ],
  "frame_observations": [
    {
      "frame_index": 1,
      "observation": "Close-up of a cheesecake slice. The bowl of a spoon rests on the browned top crust, visibly contacting it; no cut-out piece or lifted cake is visible.",
      "screen_text": [
        "外皮有点焦焦的"
      ],
      "timestamp_sec": 5
    },
    {
      "frame_index": 2,
      "observation": "The spoon is still laid across the cake's browned top and appears to maintain contact. The cake surface and slice outline look essentially unchanged; scooping is not visible.",
      "screen_text": [
        "中间有点像布丁"
      ],
      "timestamp_sec": 6.5
    },
    {
      "frame_index": 3,
      "observation": "The view is wider. The spoon is oriented nearly vertically with its lower edge inserted or pressing into the top crust near the center-left of the slice. No spoonful is lifted away.",
      "screen_text": [
        "吃起来和冰淇淋一样"
      ],
      "timestamp_sec": 8
    }
  ],
  "same_scene": true,
  "screen_text": [
    "一人厨 bilibili",
    "外皮有点焦焦的",
    "中间有点像布丁",
    "吃起来和冰淇淋一样"
  ],
  "screen_text_present": true,
  "search_terms": [
    "spoon presses cheesecake",
    "spoon scoops cake",
    "cheesecake texture"
  ],
  "summary": "A spoon is visibly placed on and then oriented into the top of a cheesecake slice. The frames show contact/pressing, but do not clearly show a scoop being lifted or removed.",
  "uncertainties": [
    "The still frames do not establish the exact motion between timestamps.",
    "It is uncertain whether the spoon penetrates deeply into the cake in frame 3 or only presses against the surface.",
    "A completed scooping action is not visibly confirmed."
  ]
}
````

## 人工审核后续

只把人工确认过的时间范围、屏幕文字和画面判断写回 pilot truth；模型候选本身不能作为冻结依据。
