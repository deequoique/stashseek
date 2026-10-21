# Type Safety

> How Python API schemas become browser TypeScript types.

---

## Overview

TypeScript runs in strict mode. FastAPI Pydantic response and request models are the canonical browser contract. `openapi-typescript` generates the tracked `schema.d.ts`; frontend code imports small aliases from `contracts.ts`.

---

## Type Organization

- `app/api/*_schemas.py`: canonical request and response DTOs.
- `scripts/export_web_openapi.py`: deterministic, side-effect-free OpenAPI export.
- `web/src/api/openapi.json`: tracked generated schema.
- `web/src/api/schema.d.ts`: tracked generated TypeScript declarations.
- `web/src/api/contracts.ts`: semantic aliases such as `LibraryItem`, `TranscriptPage`, and `LoginChannel`.
- Component-only prop interfaces remain next to the component.

Never duplicate lifecycle, login-channel, batch-status, or transcript response unions by hand.

Conversation SSE events use the generated `ConversationStreamEvent` contract.
The server omits event-specific null fields (`exclude_none`), so nullable
fields such as `section_id`, `status`, and `reason` are optional in generated
TypeScript as well as nullable. Use runtime lifecycle narrowing for their
event-specific requirements; do not make fixtures satisfy a broader global
required shape or bypass the contract with a cast.

The exporter must instantiate the same email-enabled canonical route
composition used in production, with inert injected services where external
providers would otherwise be constructed. A schema generated from a legacy or
partial app is stale even when its generated files are internally consistent.

---

## Validation

The server is the runtime validation boundary. Pydantic uses strict extra-field rejection for browser requests and emits fixed safe validation errors. The client does not maintain a second handwritten Zod response schema.

Client-side checks may improve form feedback, such as the 1–10 URL limit, but they do not replace server validation.

---

## Common Patterns

```ts
import type { components } from "./schema";

type Schemas = components["schemas"];
export type LibraryItem = Schemas["LibraryItemResponse"];
export type LibraryLifecycle = LibraryItem["lifecycle"];
```

- Use `import type` for erased imports.
- Narrow unknown errors by behavior rather than using `any`.
- Use generated nullable fields exactly as returned by the API.
- Treat transcript cursors and public IDs as opaque strings.
- Regenerate `openapi.json` and `schema.d.ts` together and require
  `pnpm check:api` in CI: `pnpm generate:api` (exports `openapi.json`), then
  `pnpm generate:types` (`openapi-typescript` 7.13.0 → `schema.d.ts`). Commit
  both files in the same change. `check:api` only diffs `openapi.json`, so a
  skipped `generate:types` leaves `schema.d.ts` silently stale (it had drifted
  by ~500 lines before 2026-10-19).
- New response fields use `Field(default=...)`/`default_factory` so the
  generated property is optional; old extension builds and typed test
  fixtures keep compiling.

---

## Forbidden Patterns

- Editing `schema.d.ts` directly.
- Casting API responses to unrelated interfaces.
- Adding `any` to bypass a contract mismatch.
- Declaring a second handwritten `LibraryItem` or lifecycle union.
- Using ORM/model types in React.
- Assuming optional summary data exists when the schema says null is valid.

## Scenario: Reproducible validation descriptions

### 1. Scope / Trigger

Use when exporting browser contracts on supported Python versions. CI uses
Python 3.11 while local development may use 3.13; their standard HTTP 413 and
422 phrases can differ even with the same FastAPI and Starlette versions (3.13
renamed both). Any route that declares `responses={413: {"model": ...}}` without
a `description` inherits that default.

### 2. Signatures

`scripts.export_web_openapi.rendered_schema() -> str` is the deterministic,
inert exporter consumed by `generate:api` and `check:api`.

### 3. Contracts

Normalize only a 422 response description equal to `Unprocessable Entity` or
`Unprocessable Content` to the existing contract's `Unprocessable Entity`, and a
413 description equal to `Request Entity Too Large` or `Content Too Large` to
`Request Entity Too Large` (`STANDARD_PHRASES` in the exporter).
Retain custom descriptions, schema contents and every other status. Always
regenerate both tracked files using the exporter, never edit generated text.
This is documentation normalization, not a runtime response/error change.

### 4. Validation & Error Matrix

| Input | Export result |
| --- | --- |
| Either standard 413 or 422 phrase | Same canonical description and identical contract |
| Custom 413/422 description | Preserve verbatim |
| Other response/status/schema | Preserve |

### 5. Good / Base / Bad Cases

- Good: Python 3.11 and 3.13 export the same interface.
- Base: custom safe-error documentation is unchanged.
- Bad: blame every phrase change on Starlette or hand-edit generated types.

### 6. Tests Required

`tests/test_web_openapi_export.py` changes the standard-library phrase and
compares real exports, then checks an explicitly documented custom response.
Run `check:api` after generation; package compatibility alone does not establish
an exact lock-file match.

### 7. Wrong vs Correct

Wrong: patch 19 descriptions in tracked JSON to match one machine.

Correct: normalize the two standard defaults in the exporter, preserve custom
text, test both standard phrases, and regenerate from the canonical app.
