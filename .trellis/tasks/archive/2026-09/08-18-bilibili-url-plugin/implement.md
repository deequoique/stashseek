# Bilibili URL/插件执行计划

## Phase A — 契约与预检（先行）

- [x] 建立第一阶段的平台 URL helper，抽离 Bilibili 本地 URL 识别与 canonicalization；完整 connector registry/worker 构造留给下一步。
- [x] 实现 BV/av 的 host、path、ID、凭据、片段、查询清理和 canonicalization 测试；playlist/season/short-link 暂按安全拒绝处理。
- [x] 扩展服务器 capabilities/OpenAPI 类型和前端 URL 添加/平台展示；browser capture schema 留给 companion adapter 阶段。
- [x] 检查 Alembic/model parity，确认初始 migration 和当前 model 均已有 `platform=bilibili`，无需新 migration。

## Phase B — 服务器直连 MVP

- [x] 实现 `BilibiliConnector`：yt-dlp metadata、官方/自动 SRT 选择、SRT cue、429/超时/风控/登录提示稳定映射、大小边界。
- [x] 接入 `_connector()`、`process_item()`、SRT object format/read path 和批量提交；保留既有租户、幂等、completion/retry 语义。
- [x] 增加单元测试：URL 矩阵、metadata 映射、字幕 track、弹幕排除、空字幕/登录提示、429/风控、无 URL 直取、恶意 URL、SRT 存读与日志不泄密。
- [ ] 已用公开真实 BV canary 验证 metadata 与 `needs_asr`；仍缺一个无需登录且带官方字幕的 `ready` 样例，以及可稳定区分登录字幕的上游响应。完成这两个门槛前不宣称 Bilibili 字幕全覆盖。

## Phase C — Browser companion 登录态兜底

- [ ] 实现 `captureBilibiliPage`，覆盖公开视频页的 metadata 与官方字幕/原生 TextTrack；在浏览器内消费同源或页面授权资源。
- [ ] 更新严格平台/host/cover/URL 验证、capture hash、服务端 capture submission、extension popup 错误映射和 fixture adapter 测试。
- [ ] 保证无字幕返回 `caption.status=unavailable`，不上传 cookie、SESSDATA、WBI、KS、signed URL、response body 或内部数据库 ID。
- [ ] 在真实 Chrome（不是服务端模拟）验证普通视频、登录字幕、无字幕、过期页面/资源重定向、重复捕获和断线重试。

## Phase D — 质量门禁与发布

- [ ] 运行 `pytest -q`，扩展 `npm test`/TypeScript/lint/build/audit，导出并校验 OpenAPI 与 `web/src/api` 生成类型一致。
- [ ] 更新 README、接口文档、插件安装/能力说明和 Trellis backend/browser-capture spec；记录新的 Bilibili site pattern（只写已验证事实）。
- [ ] 做一次回滚演练：关闭 Bilibili admission 后旧平台、历史条目、已捕获条目仍可读。
- [ ] 通过 review gate 后再执行 `task.py start`；实现完成后按 Trellis 流程 check、spec update、commit、`task.py archive`。

## 验证命令（实现阶段）

```bash
pytest -q
cd extension && npm test && npm run build && npm run audit
python3 scripts/export_web_openapi.py
python3 ./.trellis/scripts/task.py validate 08-18-bilibili-url-plugin
```

## 停止/回滚点

- yt-dlp 无法在无登录样例稳定取得 metadata/字幕：停止 Phase B，保留 URL 预检和 `needs_extension`，不要引入不受控的第三方 API。
- 扩展无法在页面本地安全提取字幕：停止 Phase C，服务器 connector 与无字幕 ASR 状态仍可发布。
- 任何测试发现签名 URL/cookie/内部 ID 跨边界：立即回滚该 adapter，不扩大 allowlist。
