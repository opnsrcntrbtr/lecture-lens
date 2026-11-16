# SPEC-2026: Ambient Agentic Memory, Hybrid Retrieval & MCP Context Infrastructure

> **Status**: Proposed Architecture & Research Spec (Nov 2026)  
> **Scope**: `screenpipe-db`, `screenpipe-sqlite-coordinator`, `screenpipe-engine` HTTP API (`:3030`), `packages/screenpipe-mcp` (`:3031`), and the study layer (`study_mcp.py`, `lecture_kit.py`, Obsidian vault integration).  
> **Non-Negotiable Invariants**:
> - `VISION.md`: Record / Rewind / Ask focus, local-first always, `<20%` CPU, `<3GB` RAM on release builds.
> - `AGENTS.md`: Single SQLite writer per DB path via `screenpipe-sqlite-coordinator` + `write_queue`; never widen write transactions to cover model/network work; no per-frame heap allocation or blocking in capture/audio hot paths.
> - `CLAUDE.md`: Stdlib-only Python 3 for core toolkit scripts, `~/.screenpipe/db.sqlite` accessed strictly read-only, deterministic segmentation/dedup vs. LLM judgement boundary, strict citation grounding.

---

## 1. Current State Analysis

### 1.1 Storage & Single-Writer Coordinator (`screenpipe-db` + `screenpipe-sqlite-coordinator`)

Screenpipe's persistence layer is built around strict corruption-prevention invariants learned from real-world SQLite WAL/mmap incidents:

1. **Process-Wide Single-Writer Lane (`crates/screenpipe-sqlite-coordinator/src/lib.rs`)**:
   - `SqliteWritePool` wraps a `sqlx::SqlitePool` behind an `Arc<Semaphore>` (10-second acquisition timeout) and optional `admission_gate`. Callers must obtain a `SqliteWritePermit` via `.lock().await` before mutating state.
   - `SqliteManagerLease` enforces one live manager generation per canonical physical DB path (`lock_key`), preventing multiple independent pools from unlocking macOS POSIX inode locks.
   - Hard faults (`IOERR`, `CORRUPT`, `FULL`, `NOTADB`) latch a process-lifetime tombstone (`SQLITE_HARD_FAULTS`) so a faulted DB is never reopened in-process; recovery requires a verified fresh generation swap (`recovery.rs`).

2. **Coalesced Write Queue (`crates/screenpipe-db/src/write_queue.rs`)**:
   - All mutations funnel through an `mpsc` channel (`CHANNEL_CAPACITY = 4096`) into a single drain loop that batches up to `MAX_BATCH_SIZE = 500` writes into one `BEGIN IMMEDIATE ... COMMIT` transaction.
   - Supports `request_write_pause()` / `request_write_resume()` for macOS sleep/wake transitions and escalates consecutive fatal batches (`DEGRADED_AFTER = 3`, `WRITE_POOL_REOPEN_EVERY = 5`).

3. **macOS `unix-excl` VFS & Read Safety (`crates/screenpipe-db/src/db/setup.rs`, `cancellable_query.rs`)**:
   - On macOS, `db.sqlite` opens with `.vfs("unix-excl")`, `PRAGMA journal_mode=WAL`, `busy_timeout = 5s`, and tier-configured `mmap_size`. `unix-excl` holds a process-exclusive lock on the database inode, keeping external processes out while the daemon runs.
   - Read queries use `CancellableReadConnection` (`SEARCH_QUERY_TIMEOUT`) so disconnected HTTP/MCP callers cannot pin SQLite read connections indefinitely.

4. **Current Indexing: Pure Lexical FTS5 + Unused `sqlite-vec` Capability**:
   - **FTS5 Virtual Tables** (external-content mode `content='...'`, `tokenize='unicode61'`): `frames_fts` (migrated to external-content in `20260415000000_frames_fts_external_content.sql` to avoid ~175 MB duplicate text and 420× slower retention deletes), `ocr_text_fts`, `audio_transcriptions_fts`, `memories_fts`, `semantic_items_fts`, `output_search_fts`, plus `elements_fts`, `accessibility_fts`, and `ui_events_fts`.
   - **`sqlite-vec` Status**: `register_sqlite_extensions()` (`crates/screenpipe-db/src/db/setup.rs`) *already* compiles and registers `sqlite3_vec_init` as a SQLite auto-extension! Currently, `vec_f32` / `vec_length` are only used for 512-dim voice profiles in `speaker_embeddings` (`crates/screenpipe-db/src/db/speakers.rs`). A prior `ocr_text_embeddings` table (`20250117134500`) was dropped in `20260311000000_drop_unused_tables.sql` because an embedding pipeline was never implemented.
   - **Semantic & Memory Tables**:
     - `memories` + `memories_fts` (`20260310000000`): stores persistent facts, preferences, and insights (`content`, `source`, `source_context`, `tags`, `importance`, `frame_id`).
     - `semantic_runs`, `semantic_items`, `semantic_run_items`, `semantic_items_fts` (`20260724000000`): stores normalized semantic projections (`conversation`, `message`, `document`, `task`, `calendar_event`, `page`) extracted from accessibility trees without duplicating raw JSON per frame.

### 1.2 HTTP API (`localhost:3030`, `crates/screenpipe-engine/src/server.rs`)

The Axum server exposes the local context surface over `127.0.0.1:3030` (CORS-restricted to localhost origins):
- **Search & Content**: `GET /search`, `GET /search/keyword`, `POST /raw_sql` (strictly read-only `SELECT`/`WITH`/`EXPLAIN`, `LIMIT` capped at `10,000` in `routes/content.rs`), `GET /activity-summary`, `GET /activity-ledger`, `GET /elements`, `GET /frames/:frame_id/*`.
- **Meetings & Audio**: `GET /meetings`, `GET /meetings/:id`, `GET /meetings/:id/transcript`, `POST /meetings/start|stop`, `POST /export`.
- **Memories & Semantic Actors**: `GET|POST /memories`, `GET|PUT|DELETE /memories/:id`, `GET /memories/tags`, `POST /memories/sync-external`, `GET /semantic/actors/search`, `POST /semantic/actors/*`.
- **Pipes & Workflows**: `/pipes/*`, `/workflows/*`.

### 1.3 MCP Servers (`packages/screenpipe-mcp` & `study_mcp.py`)

- **`packages/screenpipe-mcp`**:
  - Runs over **stdio** (`src/index.ts`) or **Streamable HTTP** (`src/http-server.ts`, default `127.0.0.1:3031/mcp` + `/health`, optional `--listen-on-lan --api-key`). Proxies all calls to `localhost:3030`; never opens `db.sqlite` directly.
  - Exposes ~27 local tools (`search-content`, `keyword-search`, `activity-summary`, `search-elements`, `get-frame-elements`, `frame-context`, `list-meetings`, `get-meeting`, `update-memory`, `list-pipes`, `create-pipe`, `run-pipe`, `pipe-logs`, `search-speakers`, etc., plus 4 `team-*` enterprise tools) and 3 dynamic resources (`screenpipe://context`, `screenpipe://guide`, `screenpipe://guide/pipes`).
  - `screenpipe://guide` codifies progressive disclosure (`activity-summary` → `search-content` → `search-elements` → `frame-context`) and notes that screen text is primarily **accessibility-derived** (`[Screen·a11y]`) with OCR as fallback (`[Screen·ocr]`).
- **`study_mcp.py`**:
  - A standalone Python `FastMCP` stdio server exposing 10 study tools (`study_health`, `study_list_lectures`, `study_get_lecture`, `study_ask`, `study_build_lecture`, `study_make_study_aid`, `study_save_references`, `study_companion_state`, `study_player_control`, `study_job_log`). Currently exposes **0 MCP resources** and uses ad-hoc background log polling (`study_job_log`) for long-running builds.

### 1.4 How `lecture-lens` Consumes Screenpipe Read-Only

`lecture_kit.py::db()` implements a clean read-only pattern compatible with macOS `unix-excl`:
1. Attempts `sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)` for direct read-only access when the daemon is stopped.
2. When `screenpipe` is running, `unix-excl` returns a `"locked"` `OperationalError`; `db()` catches this and returns `HttpDB`, which transparently pages `POST http://localhost:3030/raw_sql` in `10,000`-row chunks (`HttpDB.PAGE = 10000`).
3. `study.py` and `live_class.py` query `:3030/search` and `:3030/health` over `127.0.0.1` (bypassing system HTTP proxies via `urllib.request.ProxyHandler({})`).

---

## 2. Proposed Design Specifications

### 2.1 Spec A: Asynchronous Hybrid Retrieval (FTS5 + `sqlite-vec` + RRF)

**Problem**: Pure FTS5 BM25 fails on conceptual/paraphrased queries (e.g., *"what did the professor say about model drift?"* when the transcript says *"distribution shift in production"*), while pure vector search fails on exact identifiers (error codes, API names, proper nouns).

**Design (Zero Hot-Path Impact, Single-Writer Compliant)**:
1. **Reuse Bundled `sqlite-vec`**: `sqlite3_vec_init` is already registered in `screenpipe-db`. Add `vec0` virtual tables (e.g., `frames_vec`, `audio_transcriptions_vec`, `memories_vec`) storing quantized/float32 embeddings (e.g., 384-dim or 768-dim) keyed by `rowid` matching the source table.
2. **Deferred Out-of-Transaction Embedding Worker**:
   - **Never** compute embeddings in capture/audio callbacks or inside a `SqliteWritePermit` transaction.
   - Run a low-priority background worker (configurable via env/feature flag, paused during high CPU or battery saver) that:
     1. Polls a lightweight cursor (`embedding_index_progress`) via the **read pool** for settled, deduplicated `frames` (`full_text` non-empty), `audio_transcriptions` / `meeting_transcript_segments`, and `memories`.
     2. Computes embeddings out-of-transaction against a local endpoint (e.g., local oMLX/Ollama/llama.cpp embedding server on `127.0.0.1`, or an embedded small-footprint model).
     3. Submits `(rowid, vec_f32(?))` batches to `write_queue` (`<= 500` rows/batch), respecting `request_write_pause()` and retention deletes.
3. **Hybrid Search & Reciprocal Rank Fusion (RRF) in `GET /search`**:
   - Add `search_mode=lexical|semantic|hybrid` (default `lexical` for `keyword-search`, `hybrid` opt-in or default when `frames_vec` is populated).
   - Execute `vec0` KNN (`k = 20..50`) and `FTS5` BM25 (`LIMIT 20..50`) in a single `CancellableReadConnection` CTE using **Reciprocal Rank Fusion (RRF)**:
     ```sql
     rrf_score = COALESCE(1.0 / (60 + fts.rank_number), 0.0) * 0.4
               + COALESCE(1.0 / (60 + vec.rank_number), 0.0) * 0.6
     ```
   - Retain `start_time` / `end_time` / `app_name` pre-filtering or post-join filtering so scans stay bounded within `<3GB` RAM and `<20%` CPU.

### 2.2 Spec B: Lecture / Session Entity & Concept Graph Linking into Obsidian

**Problem**: the study layer generates rich per-lecture Markdown notes, `concept_map.mmd`, `cards.tsv`, and `refs.md` into `<vault>/`, while `screenpipe` maintains `memories` and `semantic_items`. Currently there is no cross-lecture concept graph or bidirectional link between Obsidian study notes and ambient desktop memory.

**Design (Sidecar Graph Index + Canonical Deep Links)**:
1. **Sidecar Study Graph (`lectures/study_graph.sqlite`)**:
   - Keep edtech-specific graph state **out of `~/.screenpipe/db.sqlite`** to avoid schema bloat and write-queue contention on the 24/7 capture DB.
   - Maintain a stdlib-only Python SQLite sidecar (`study_graph.sqlite` with WAL + FTS5) indexing:
     - `concepts` (canonical concept name, aliases, first-seen lecture, mastery/FSRS state).
     - `edges` (`concept_a` → `[:PREREQUISITE_FOR | :EXTENDS | :CONTRASTS_WITH | :APPEARS_IN]` → `concept_b` / `lecture_id`).
     - `evidence_links` (mapping each concept/claim to `lecture_id`, slide number, timecode, and `screenpipe://frame/{frame_id}` or `screenpipe://timeline?timestamp=...`).
2. **Obsidian Vault Graph Hygiene**:
   - Emit deterministic `[[wikilinks]]` and YAML frontmatter (`screenpipe_session`, `time_range`, `concepts`, `prereqs`) in generated Obsidian notes so Obsidian's native graph view and local GraphRAG MCPs (e.g., `obsidian-memvector-knowledge-engine` / PKMRAG patterns) can traverse multi-hop concept paths across lectures.
3. **Opt-In Memory Promotion to Screenpipe `/memories`**:
   - After a lecture build or `study_save_references` completes, `lecture-lens` can optionally `POST http://localhost:3030/memories` with concise, user-approved summaries (`source: "study_toolkit"`, `tags: ["course", "<lecture_id>"]`, `frame_id` anchor). This lets general coding/assistant agents (Claude Code, Cursor) recall *what you learned* via `search-content(content_type="memory")` without needing to know about `lecture-lens`.

### 2.3 Spec C: MCP Tools & Resources Upgrade (2026 MCP Spec Alignment)

1. **Expose First-Class MCP Resources in `study_mcp.py`**:
   - Add read-only URI resources alongside tools so MCP hosts can attach context declaratively:
     - `study://lectures` — JSON index of captured/built lectures.
     - `study://lecture/{lecture_id}/notes` — Markdown notes with `screenpipe://` citations.
     - `study://lecture/{lecture_id}/concept_map` — Mermaid concept graph.
     - `study://companion/state` — Current browser companion state (course, module, video timestamp).
   - Emit `notifications/resources/list_changed` when `study_build_lecture` or `study_make_study_aid` finishes.
2. **Adopt 2026 MCP Protocol Patterns (`screenpipe-mcp` & `study_mcp`)**:
   - **Stateless Streamable HTTP & Header Routing**: In `packages/screenpipe-mcp/src/http-server.ts`, align with the 2026 MCP stateless core (`Mcp-Method`, `Mcp-Name` HTTP headers, cacheable `tools/list` with `ttlMs`) so local/LAN agents can reconnect across daemon restarts without sticky-session failures.
   - **MCP `Tasks` for Long-Running Builds**: Replace `study_mcp.py`'s ad-hoc `_job()` + `study_job_log` polling with MCP's first-class **Tasks** extension for `study_build_lecture` and `study_make_study_aid`, giving clients standard progress and deferred result retrieval.
   - **Hybrid Search & Graph Tools**:
     - Upgrade `screenpipe-mcp`'s `search-content` tool schema with `search_mode?: "lexical" | "semantic" | "hybrid"` (defaulting safely to `lexical` until embeddings are backfilled).
     - Add `study_query_graph` (read-only) to `study_mcp.py` for multi-hop concept traversal (`prerequisites`, `related_lectures`, `grounded_frames`) with strict `readOnlyHint: true`.

### 2.4 Spec D: Accessibility-Tree-First Context & Ambient Peer Alignment

- Screenpipe's `screenpipe-a11y` + `semantic_items` architecture (`AXUIElement` on macOS, UIA on Windows, AT-SPI on Linux) already reflects the 2026 industry shift away from pixel-only OCR toward structured OS accessibility trees.
- **OpenClaw-style Transparent Memory**: Like OpenClaw's plain-Markdown + local SQLite hybrid memory (`AGENTS.md`/`USER.md` + `sqlite-vec`/FTS5), pairing human-readable Obsidian Markdown (`<vault>/`) as the source of truth with SQLite FTS5 + `sqlite-vec` as the fast retrieval index gives both human inspectability and sub-30ms agent recall.

---

## 3. Correlated Nov 2026 References

References (titles only; looked up on 2026-11-16 — search the title to find the source):

1. **SQLite Hybrid Search (`sqlite-vec` + FTS5 + RRF)**:
   - Hybrid vector (`vec0`) + FTS5 BM25 with Reciprocal Rank Fusion (RRF) inside a single local SQLite file (pushing Recall@5 past 92% on LongMemEval vs. low-80s for vectors alone, with sub-30ms local queries):

2. **Model Context Protocol (MCP) 2026 Specifications (Resources, Tools, Memory, Stateless Core & Tasks)**:
   - MCP governance under the Agentic AI Foundation (Linux Foundation) and the 2026-07-28 specification revisions (stateless core, `Mcp-Method`/`Mcp-Name` header routing, `ttlMs` caching on `tools/list`, Resources with `listChanged` subscriptions, JSON Schema 2020-12 tools, Memory Server, and first-class async `Tasks`):

3. **Local GraphRAG, Obsidian & PKM Memory**:
   - Local-first GraphRAG architectures combining SQLite/embedded graph stores with Obsidian `[[wikilinks]]`, typed relations (`[:EXTENDS]`, `[:PREREQUISITE_FOR]`), and MCP servers (MemVector Knowledge Engine, PKMRAG, InfraNodus):

4. **Ambient Desktop AI Peers & Accessibility-Tree-First Capture**:
   - OpenClaw (local Markdown + SQLite/vector hybrid memory), `rem` (macOS local Vision OCR + SQLite timeline), Rewind/Limitless evolution, and OS accessibility trees (`AXUIElement` / UIA / AT-SPI):
