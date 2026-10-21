# Test-branch DDL (2026-10-21)

Target: Neon test branch only. The host was checked against `ep-falling-fog-*`
before every statement (`.env.neon-test`, never the production `.env`).

## Why

Migration `c9d0e1f2a3b4_media_asset_embeddings.py` was removed from the chain
(`cff884c74a1e` now revises `d04fdae36884`). The test branch had been upgraded
through it on 2026-10-20, so it still carried the objects that migration made.
Production is at `d04fdae36884` and never had them. This DDL makes the test
branch match "production after the release".

## Before

- `alembic_version`: `f8a9b0c1d2e3`
- tables: `media_asset` (0 rows), `media_embedding` (0 rows)
- constraint `uq_content_item_id_user_id` on `content_item(id, user_id)`
  (also created by the removed migration; its only dependent was the
  `fk_media_asset_content_item_tenant` foreign key)

## SQL (one transaction)

```sql
DROP TABLE IF EXISTS media_embedding;
DROP TABLE IF EXISTS media_asset;
ALTER TABLE content_item DROP CONSTRAINT IF EXISTS uq_content_item_id_user_id;
```

## After

- `alembic_version`: `f8a9b0c1d2e3` (unchanged)
- `select tablename from pg_tables where tablename like 'media_%'`: empty
- `uq_content_item_id_user_id`: absent
- `alembic check` against the branch: "No new upgrade operations detected."
  (ORM metadata, which no longer declares the media tables or
  `uq_content_item_id_user_id`, matches the database exactly)

## Rollback

Recreate from the removed migration's `upgrade()` (kept in git history at
`3b64d34`). The tables held no rows.
