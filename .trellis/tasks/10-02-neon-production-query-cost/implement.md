# Implementation Plan

1. Load backend database, error-handling, deployment-lifecycle, and quality guidelines before editing.
2. Add an event-scoped notification claim/delivery method and Celery task; wire terminal completion paths to enqueue it after commit with privacy-safe best-effort handling.
3. Change the repair sweep default to 600 seconds and update configuration/reference/deployment documentation.
4. Change the launcher fallback profile from `full` to `read`, retaining explicit full/langbot behavior and updating affected tests/docs.
5. Add focused tests for targeted delivery, duplicate/idempotent claims, enqueue failure fallback, schedule defaults, and launcher defaults.
6. Run focused notification/deployment tests, then the backend full-scope quality checks and `git diff --check`.
7. Review production rollout and rollback instructions; do not apply remote changes without a separate deployment authorization.
