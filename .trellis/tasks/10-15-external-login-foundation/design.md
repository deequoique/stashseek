# Shared Foundation Design

The normative shared design is `../10-15-multi-provider-login/design.md`; this child owns its Data Model, Browser/API Contract, Flow/Transactions, Sessions/Caches, and Ownership Operations sections.

Use additive SQLAlchemy/Alembic models for mappings, browser flows, purpose-bound email proof, and immutable session provenance. The mapping references the precise email identity and matching owner; restrictive/deferred constraints enforce safe atomic merges. Retain old email rows/sessions and one migration head.

Extract transaction-local helpers from `WebAuthService`, preserving public email behavior. Provider HTTP stays outside database transactions; code exchange is claimed once. First-use completion validates both proofs and atomically resolves/creates the email owner, inserts/confirms the unique mapping, consumes proofs and issues the provider session.

The same-origin composition owns new routes. Start uses a native top-level POST/303; pending JSON requests use the canonical wrapper, exact Origin and flow CSRF. The clean continuation route is `/login/external`. The verification form displays mailbox/binding purpose and submits `confirm_binding:true` with the code; no separate post-verification confirmation transaction is needed.

Provider sessions validate provenance/current enabled state with one database join and no Redis admission. The versioned email projection explicitly retains null provenance and rejects external-source payloads. Extend supported merge/rebind paths. Preserve frontend session/cache ownership and explicitly map new method labels.

The fake adapter is test injection only. Production capabilities expose only completed adapters with complete enabled server configuration. No frontend credentials enter the build.

Rollback disables providers and revokes provider sessions before reverting to code that cannot understand provenance. Retain additive schema and mappings; no automatic downgrade/deletion.
