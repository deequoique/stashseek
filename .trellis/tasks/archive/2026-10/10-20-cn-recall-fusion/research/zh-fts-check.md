# 中文词元切分验证（测试分支，2026-10-20）

目的：确认 `to_tsvector('simple', ...)` 对「空格分隔的 CJK 双字词元」得到独立词元，且 `websearch_to_tsquery('simple', 'a OR b')` 生成 OR 查询，这样 `zh_tsvector` / `zh_tsquery` 可以按 `design.md` 3.2 直接使用 `websearch_to_tsquery`，不需要退化为手拼 `to_tsquery`。

环境：Neon 测试分支（主机 `ep-falling-fog-azrht8ir-pooler…`，与 `.env` 的生产主机 `ep-royal-cherry-…` 不同），PostgreSQL 18.6，`default_text_search_config = pg_catalog.simple`，数据库 `neondb`。只运行了不访问任何表的 `SELECT` 表达式。

注意：`.env.neon-test` 的主机名里没有字面的 `test`，用的是仓库记忆里记录的测试分支主机（`ep-falling-fog-azrht8ir`）；`NEON_BRANCH=production` 这一行是该文件里的遗留标签，不代表指向生产库（主机与 `.env` 不同、区域也不同：`ap-southeast-1` 对 `us-east-1`）。

## 结果

| SQL | 结果 |
|---|---|
| `SELECT to_tsvector('simple', '电脑 脑价 ddr5')` | `'ddr5':3 '电脑':1 '脑价':2`（3 个独立词元） |
| `SELECT websearch_to_tsquery('simple', '电脑 OR 脑价 OR ddr5')` | `'电脑' \| '脑价' \| 'ddr5'`（三者的 OR） |
| `SELECT to_tsvector('simple', '电脑 脑价 价格 DDR5 5070')` | `'5070':5 'ddr5':4 '价格':3 '电脑':1 '脑价':2`（ASCII 自动小写） |
| `to_tsvector('simple', '电脑 脑价 ddr5') @@ websearch_to_tsquery('simple', '电脑 OR 显卡')` | `true`（任一词元命中即可） |
| `to_tsvector('simple', '电脑 脑价 ddr5') @@ websearch_to_tsquery('simple', '显卡 OR 内存')` | `false` |
| `ts_rank_cd(to_tsvector('simple','电脑 脑价 价格'), websearch_to_tsquery('simple','电脑 OR 价格 OR 显卡'))` | `0.2`（命中 2 个词元）；只含 `电脑 脑价` 的向量为 `0.1`（命中 1 个）：命中词元越多排名越高 |

边界检查：

| SQL | 结果 |
|---|---|
| `websearch_to_tsquery('simple', 'ab OR or OR cd')` | `'ab' \| 'or' \| 'cd'`（小写的 ASCII 词 `or` 夹在 `OR` 之间仍按普通词元处理，不会被吃掉） |
| `websearch_to_tsquery('simple', 'ab OR and OR not')` | `'ab' \| 'and' \| 'not'`（`simple` 配置没有停用词；`not` 不是 websearch 语法） |
| `to_tsvector('simple', '电脑') @@ websearch_to_tsquery('simple', '电')` | `false`：入库只生成双字词元，单字查询只能命中被切成单字段的文本（如「电 脑」），见下方已知限制 |
| `to_tsvector('simple', '电 脑') @@ websearch_to_tsquery('simple', '电')` | `true` |

## 结论

CJK 词元被按空格独立切分，`websearch_to_tsquery('simple', ...)` 的 `OR` 语法可用，不需要改用 `to_tsquery`。`zh_tsquery` 按 `design.md` 3.2 实现。

## 已知限制（设计内）

- 单个汉字的查询词元（如查询「电」）不会匹配由双字词元索引的文本（`电脑` → 只有 `电脑` 词元）。`design.md` 规定"单个汉字的查询退化为单字词元"，这与入库侧"长度 1 的 CJK 段取单字"保持一致，所以只有被标点或空白隔成单字的文本才命中。这属于设计取舍，单字查询靠向量路径兜底。
