# Project conventions

- TypeScript: never `any`. Prefer precise interfaces.
- Reuse shared helpers from `src/format.ts` rather than redefining them.
- DB: prefer `text` over `varchar`, enums over free strings, soft delete, no `ON DELETE CASCADE`.
