# Measured and inspected bottlenecks

- app/web_auth.py resolve_session rolls back the transaction before projecting
  ORM rows in a read-only branch. Synthetic SQLite reproduction counted four
  SELECTs: the initial joined authorization plus reloaded session/user/identity.
- app/api/conversation_routes.py get_conversation_turns selects whole turn rows,
  including internal model_messages JSONB which no public response needs.
- tests/test_conversation_list_queries.py confirms sidebar list is already
  constant-query and tenant-page scoped; do not redo its Aug 22 optimization.
- web/src/app/App.tsx imports seven routes eagerly. Baseline Vite output main
  JS is 501.32 kB / 154.63 kB gzip.
- ChatPage history waits for capabilities, then first transcript waits for a
  React selection effect. Login seeds only the session on a rotated client.
- Public capabilities are safe to reuse, while tenant history must remain in
  the active private QueryClient and never survive account/cache rotation.
- Historical remote SQL timing was fast compared with roundtrip duration;
  extra serialized queries can magnify latency. No live timing was collected here.

Frontend and backend research were read-only. Existing auth/cache and frontend
state specs govern privacy, invalidation and honest loading/error states.
