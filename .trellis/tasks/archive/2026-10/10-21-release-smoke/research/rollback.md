# 回滚方案：dev 整体发布（release/2026-10-dev-sync → main）

发布前请用户过目。所有命令在生产主机 `ubuntu@51.79.159.110` 上以 sudo 执行。

## 发布会改变什么

- 代码：`/opt/notebook-agent/releases/<新 SHA>`，`current` 符号链接指向它；部署脚本会在该目录构建 `extension/dist` 并建 `web/dist -> ../extension/dist`。
- 数据库（Alembic `d04fdae36884` → `f8a9b0c1d2e3`，三个迁移）：
  - `cff884c74a1e`：新表 `content_item_stage`，`ingest_dispatch.asr_handoff_at` 可空列。
  - `e2f3a4b5c6d7`：新表 `external_login_identity`、`external_login_flow`，`web_auth_challenge.external_login_flow_id`、`web_session.external_login_identity_id` 可空列与检查约束、索引，`channel_identity` 唯一约束 `uq_channel_identity_owner`。
  - `f8a9b0c1d2e3`：`content_item.bilibili_bvid/bilibili_cid/bilibili_page` 可空列、检查约束、索引。
- 数据：`python -m app.cli reindex` 给中文片段写 `fts`、给每个 ready 条目插入标题行（`segment.seq = -1`）。

## 回滚步骤

### A. 只回代码（首选，30 秒内）
1. `ls -la /opt/notebook-agent/releases/` 找到上一个 release 目录（当前线上 `185b73c` 对应的目录）。
2. `ln -sfn /opt/notebook-agent/releases/<旧 SHA> /opt/notebook-agent/current`
3. 按部署脚本的顺序重启：`systemctl restart notebook-agent-worker notebook-agent-beat notebook-agent.service notebook-agent-asr-worker notebook-agent-gateway notebook-agent-langbot`
4. 旧代码对新表/新列视而不见；标题行是普通 `segment` 行，旧代码的 `get_neighbors` 会把 `seq -1` 当成一段正文返回（仅影响邻居扩展的首段，不报错）；中文 `fts` 旧代码不读。
5. 旧的 `web/dist`：旧 release 目录里仍有自己的 `web/dist`，网站照常。

### B. 连数据库一起回（仅在新表/新列造成故障时）
1. 先做 A。
2. 停写：`systemctl stop notebook-agent-worker notebook-agent-beat notebook-agent-asr-worker notebook-agent.service notebook-agent-gateway`。
3. 在新 release 目录里用 migration 专用连接：`MIGRATION_DATABASE_URL=... .venv/bin/alembic downgrade d04fdae36884`（顺序自动为 `f8a9b0c1d2e3 → e2f3a4b5c6d7 → cff884c74a1e → d04fdae36884`）。
4. 会丢的数据：`content_item_stage` 全部、外部登录两张表、`web_session.external_login_identity_id`、Bilibili 三列（发布后短时间内都是空或可重建）。
5. 标题行与中文 `fts` 不在迁移里：如需清理 `DELETE FROM segment WHERE boundary_kind = 'title'`，`fts` 可保留。
6. 重启服务（A 的第 3 步）。

### C. 单独回滚某一项功能（不回代码）
- 检索融合：`RETRIEVAL_FUSION=raw` 写进 `/etc/notebook-agent/notebook-agent.env`，重启 `notebook-agent.service`。
- 标题行：`DELETE FROM segment WHERE boundary_kind = 'title'`。
- 网站静态目录：主机单元 `WEB_STATIC_DIR` 在 `web/dist`（有兼容链接）或 `extension/dist` 都可用。

## 验证回滚成功
`curl -sI https://stashseek.deequoique.tech/ | head -1`、`/privacy`、`/api/v1/health`、登录一次、提问一次。
