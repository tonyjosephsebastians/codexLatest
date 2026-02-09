# Codex Workspace IDE Architecture

## UI Layout Structure
- Left rail: workspace picker, open-folder action, thread search, and thread lifecycle actions (rename/delete/pin).
- Main stage: chat-first timeline plus wiki document mode; docked input at bottom with slash command and mention support.
- Right inspector: collapsible/resizable panel with tabs for Files, Diffs, Context, Activity, and Settings.

## Repo Memory Files
- Storage root: `.codex_memory/<workspace_id>/`
- `repo_index.json`: normalized file metadata (path, size, mtime, language).
- `symbol_index.json`: extracted symbols/imports (Python AST + JS/TS regex best effort).
- `chunk_index/chunks.json`: semantic chunks with line ranges and hashes.
- `cache/context_packs/`: cached context packs keyed by workspace+query+mentions fingerprint.
- `cache/context_responses/`: cached chat responses keyed by context-pack fingerprint.
- `wiki.md` + `wiki.meta.json`: persisted wiki output and source fingerprint.

## Context Pack Algorithm
- Input: user query + UI context (`current_file`, `selection`, `open_files`, mentions).
- Mention handling:
  - `@file` -> include file chunk + symbol snapshot.
  - `@folder:path` -> include top files/chunks from folder.
  - `@repo` -> include representative global chunks.
- Retrieval: lexical scoring over chunk previews + dependency neighbor enrichment from symbol index.
- Hard cap: prompt-safe character cap with truncation notes.
- Output: files included, snippets (with line ranges), symbols, truncation notes, fingerprint.

## API Endpoints
- Thread/workspace:
  - `GET/POST /api/workspaces`, `POST /api/workspaces/open`
  - `GET/POST/PATCH/DELETE /api/threads`
- Chat/editing:
  - `POST /api/chat` (single-call chat path with context pack + cache)
  - `POST /api/editor/chat`
  - `GET /api/repo/tree`, `GET /api/repo/file`
  - `POST /api/patch/apply`
- Wiki/analysis:
  - `POST /api/wiki/explain`
  - `POST /api/repo/file-summary`
  - `POST /api/analyze/explain-repo`
  - `POST /api/analyze/architecture-diagram`

## LLM Call Discipline
- Target per chat turn: one provider call after context-pack construction.
- Cache-first behavior for repeated context/query fingerprints.
- Rate limit behavior:
  - provider throttling + circuit breaker.
  - on 429: serve cached response if available, otherwise deterministic context-pack fallback.
