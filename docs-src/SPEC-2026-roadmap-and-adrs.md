# SPEC-2026: Consolidated Roadmap, Cross-Cutting ADRs & Verification Gates

**Status:** Proposed (Critic-Audited Synthesis) · **Date:** 2026-11-16 · **Owner:** `@critic`  
**Synthesizes:**
- [`SPEC-2026-capture-and-engine.md`](./SPEC-2026-capture-and-engine.md) (`@capture-arch`)
- [`SPEC-2026-study-pipeline-and-local-ai.md`](./SPEC-2026-study-pipeline-and-local-ai.md) (`@local-ai-edtech`)
- [`SPEC-2026-agentic-memory-and-mcp.md`](./SPEC-2026-agentic-memory-and-mcp.md) (`@agentic-memory`)

---

## 1. Swarm Audit Verdict

All three specifications **PASS** audit against `VISION.md`, `AGENTS.md`, `CLAUDE.md`, and citation integrity. No `[rework]` blockers were found.

| Invariant Source | Audit Check | Verdict | Notes |
|---|---|---|---|
| **`VISION.md`** | Record / Rewind / Ask focus, no feature creep, `<20% CPU`, `<3GB RAM`, local-first | **PASS** | Capture spec caps `parakeet-mlx` Metal cache at 256 MB and removes 5s `sysinfo` / `osascript` overhead; study pipeline keeps single-shot RAG and loopback-only (`127.0.0.1`); memory spec gates async embeddings behind idle/budget checks. |
| **`AGENTS.md`** | Hot paths (no per-frame alloc, no blocking audio callbacks, single SQLite writer via `screenpipe-sqlite-coordinator`), minimal env-driven fork delta | **PASS** | `app_tap.rs` preserves zero-alloc `TapCallbackCtx`; `sqlite-vec` indexer reads via read pool, computes embeddings out-of-transaction, and batches `<= 500` rows through `write_queue`. See §1.1 for fork-delta boundary on Tier-0 browser state. |
| **`CLAUDE.md`** | Stdlib-only Python 3, deterministic vs. LLM boundary, citation grounding, `store.bin` safety, `--json` contracts | **PASS** | `lecture_kit.py`, `cite.py`, `study_aids.py`, and `study_graph.sqlite` remain 100% stdlib Python 3; ML/eval deps stay isolated in `.venv-eval/`. `store.bin` is never mutated by experiments. |
| **Citation Integrity** | All external URLs grounded in live `search_web` results | **PASS** | All 3 specs name their sources by title; search-engine redirect links were removed |

### 1.1 Cross-Spec Reconciliation Decisions
1. **Single Concept Alias Source of Truth (`_concepts.json` vs. `study_graph.sqlite`)**: `SPEC-2026-study-pipeline-and-local-ai.md` (§4.3) proposes `<vault>/_concepts.json` while `SPEC-2026-agentic-memory-and-mcp.md` (§2.2) proposes `lectures/study_graph.sqlite`. **Decision:** `study_graph.sqlite` (`concepts` + `edges` + `evidence_links`) is the canonical store; if Obsidian needs a flat file, export `_concepts.json` deterministically from `study_graph.sqlite` rather than maintaining two hand-edited alias maps.
2. **Browser Extension Port & Fork-Delta Boundary (`:47110` vs. `:3030`)**: `@capture-arch` (ADR-B Tier 0) proposes `POST http://127.0.0.1:3030/internal/browser-state`, whereas `extension` currently posts only to `127.0.0.1:47110` (`companion_server.py`). **Decision:** Keep `extension` → `:47110` for study telemetry, and only add `:3030/internal/browser-state` if upstreamed or kept as a clean, env-gated (`SCREENPIPE_DRM_URLS`) route module in `screenpipe-engine` so `feat/lecture-capture` stays reviewable.
3. **Unified-Memory Coexistence on 48 GB M4**: Running `Qwen3.6-35B-A3B` (~23.8 GB on oMLX) + `parakeet-mlx` + a local embedding model for `sqlite-vec` can breach memory/thermal headroom if uncapped. **Decision:** Enforce `SCREENPIPE_MLX_CACHE_LIMIT_MB=256` on `parakeet-mlx` first (P0), and use a compact CPU/ANE embedding model (`<= 300 MB` RSS, paused when active Metal > threshold) for `frames_vec` (P1/P2).

---

## 2. Prioritized P0 / P1 / P2 Roadmap

| Priority | Initiative | Source Spec | Effort | Risk | Upstream vs. Local Boundary |
|---|---|---|---|---|---|
| **P0-1** | **App Tap Active-PID Diffing & Differentiated Backoff** (`app_tap.rs`: track `BTreeSet<i32>` of audio-producing PIDs, `2s` stream-died vs. `5s` idle backoff, optional `kAudioHardwarePropertyProcessObjectList` listener) | Capture §3 ADR-A | Small (1–2d) | Low | **Local fork (`feat/lecture-capture`)** — env-driven (`SCREENPIPE_AUDIO_TAP_APPS`), candidate for upstream PR. |
| **P0-2** | **MLX Metal Cache Cap** (`parakeet-mlx` `mlx_set_cache_limit(256 MB)` + periodic `mlx_clear_cache()` + `/health` MLX MB metric) | Capture §3 ADR-C | Small (1d) | Low | **Upstream-first** — fixes `10+ GB` leak in `screenpipe-audio/src/transcription/engine.rs` for all Apple Silicon users. |
| **P0-3** | **Deterministic Citation Linter & Slide-Bounded Alignment** (`cite.verify_citations()`, bookmark-pinned keyframes, `aligned_segments.json`, salience-weighted selection replacing uniform `max_frames=40`) | Study §4.1, §4.2 | Medium (3d) | Low | **Local `lecture-lens`** — zero Rust changes, stdlib Python 3 only. |
| **P1-1** | **Tiered Hybrid DRM Gate** (Tier-0 extension push signal + Tier-1 bounded `AXDocument` + move `osascript` off hot path) | Capture §3 ADR-B | Medium (2–3d) | Medium | **Hybrid** — Tier-2 `osascript` async cache is upstream-friendly; Tier-0 `:3030/internal/browser-state` should be upstreamed or kept strictly env-gated. |
| **P1-2** | **Stable-GUID FSRS Anki Export & Canonical Concept Graph** (`#guid column:1` in `study_aids.py:anki_export()`, remedial quiz cards, `study_graph.sqlite` sidecar + Obsidian `[[wikilinks]]` & module prev/next) | Study §4.3, Memory §2.2 | Medium (3d) | Low | **Local `lecture-lens`** — sidecar DB keeps `~/.screenpipe/db.sqlite` untouched. |
| **P1-3** | **Cross-Family Judge Decoupling & Eval-Feedback Loop** (non-Qwen `OMLX_JUDGE_MODEL`, `laya_judge.py` calibration $\kappa \ge 0.6$, `promote_feedback.py`, real `eval/cases_real/` set) | Study §4.4 | Medium (2–3d) | Low | **Local `lecture-lens`** — isolated in `tests/evals/` and `eval/`. |
| **P1-4** | **MCP Resources & `Tasks` in `study_mcp.py`** (`study://lectures`, `study://lecture/{id}/notes`, `study://lecture/{id}/concept_map`, `study://companion/state`, `study_query_graph`, MCP `Tasks` replacing `study_job_log` polling) | Memory §2.3 | Medium (2–3d) | Low | **Local `lecture-lens`** — aligns `study_mcp.py` with 2026 MCP spec. |
| **P2-1** | **Asynchronous `sqlite-vec` + FTS5 RRF Hybrid Retrieval** (`frames_vec` / `audio_transcriptions_vec` / `memories_vec`, out-of-transaction worker, `write_queue` `<= 500` batches, `GET /search?search_mode=hybrid`) | Memory §2.1 | Large (5–7d) | Medium | **Upstream-first** — touches `screenpipe-db` migrations, `write_queue`, and `routes/search.rs`; must preserve single-writer & `<3GB` RAM. |
| **P2-2** | **Stateless Streamable HTTP in `packages/screenpipe-mcp` & `/memories` Promotion** (`Mcp-Method`/`Mcp-Name` headers, `ttlMs` `tools/list` caching, `search_mode` arg, opt-in `_study` → `POST /memories`) | Memory §2.2, §2.3 | Medium (3d) | Low | **Upstream-first (`screenpipe-mcp`) + Local opt-in (`lecture-lens`)**. |
| **P2-3** | **CoreML / ANE Parakeet Offload & Per-Display DRM Evaluation** (isolate STT on ANE via `ort` CoreML EP / FluidAudio; test per-monitor SCK invalidation vs. Widevine compositor blanking) | Capture §2.2, §3 ADR-C | Large (5d+) | High | **Upstream-first / Spike** — only adopt per-display DRM if macOS compositor isolates protected layers per display. |

---

## 3. Cross-Cutting ADR Index & Dependency Order

```
[Existing] ADR-001 (Slide Vision 1600px) ──▶ [P0-3] Slide-Bounded Alignment & cite.verify_citations()
[Existing] ADR-002 (SwiftUI --json CLI)  ──▶ [P1-4] study_mcp.py Resources & MCP Tasks
[Existing] ADR-004 (DRM Keep Audio)      ──▶ [P0-1] App Tap PID Diffing ──▶ [P1-1] Tier-0/1/2 DRM Gate
                                         └─▶ [P0-2] MLX 256MB Cache Cap ──▶ [P2-3] CoreML/ANE STT Offload
                                                                       └──▶ [P2-1] sqlite-vec + FTS5 RRF ──▶ [P2-2] Stateless MCP & /memories
[P1-2] study_graph.sqlite & FSRS GUIDs  ──▶ [P1-4] study_query_graph MCP Tool
[P1-3] Cross-Family Judge & cases_real/ ──▶ Gates all future prompt/model/retrieval changes
```

| ADR ID | Title | Status | Depends On | Key Invariant Protected |
|---|---|---|---|---|
| **ADR-001** | Slide Vision & `1600px` Crop | Accepted (existing) | — | VLM accuracy (`21/21`) & 48% token reduction |
| **ADR-002** | Native App `--json` Integration | Accepted (existing) | — | Stable CLI `--json` stdout contracts |
| **ADR-004** | DRM Video Black Screen & `KEEP_AUDIO` | Accepted (existing) | — | Immediate SCK release + continuous audio |
| **ADR-2026-A** | App Tap Active-PID Diffing & Backoff | Proposed (`P0-1`) | ADR-004 | Zero-alloc audio callback; no missed browser tabs |
| **ADR-2026-B** | Tiered Hybrid DRM Gate (Extension + AX) | Proposed (`P1-1`) | ADR-004, ADR-2026-A | No `osascript` on capture hot path; minimal fork delta |
| **ADR-2026-C** | MLX Cache Cap & CoreML/ANE STT | Proposed (`P0-2`, `P2-3`) | — | `<3GB` RAM, `<20%` CPU, oMLX GPU coexistence |
| **ADR-2026-D** | Slide-Bounded Alignment & Citation Linter | Proposed (`P0-3`) | ADR-001 | Deterministic vs. LLM boundary; zero ghost citations |
| **ADR-2026-E** | FSRS Stable-GUID Anki & `study_graph.sqlite` | Proposed (`P1-2`) | ADR-2026-D | Stdlib Python 3; no bloat in `~/.screenpipe/db.sqlite` |
| **ADR-2026-F** | Cross-Family Judge & Feedback Promotion | Proposed (`P1-3`) | — | No self-preference bias; $\kappa \ge 0.6$ calibration |
| **ADR-2026-G** | Out-of-Transaction `sqlite-vec` + FTS5 RRF | Proposed (`P2-1`) | ADR-2026-C | Single SQLite writer via `screenpipe-sqlite-coordinator` |
| **ADR-2026-H** | 2026 MCP Resources, Tasks & Stateless HTTP | Proposed (`P1-4`, `P2-2`) | ADR-2026-E, ADR-2026-G | Declarative context + standard async job progress |

---

## 4. Verification & Eval Gates by Phase

Every phase must pass its concrete gate before merge or promotion:

| Phase | Verification & Eval Gate Commands | Pass Criteria |
|---|---|---|
| **P0 (Capture & Grounding)** | 1. `cargo test -p screenpipe-audio -p screenpipe-engine`<br>2. `build.sh` + 2-hour live/replay soak with `SCREENPIPE_AUDIO_TAP_APPS="Google Chrome"` & `SCREENPIPE_MLX_CACHE_LIMIT_MB=256`<br>3. `check.sh` + `pytest tests/unit` | - Rust unit tests green; zero callback locks/allocs.<br>- `screenpipe-engine` RSS `< 3 GB`, CPU `< 20%`, multi-tab Chrome audio re-attaches in `<= 5s`.<br>- `cite.verify_citations()` catches 100% of synthetic out-of-range/ungrounded citations; `--json` contracts pass `check.sh`. |
| **P1 (DRM, Graph, FSRS & Evals)** | 1. `diag-drm` (restores `store.bin`)<br>2. `check.sh` + unit test for Anki `#guid` stability across rebuilds<br>3. `tests/evals/run.sh --smoke` (with non-Qwen `OMLX_JUDGE_MODEL`)<br>4. `tests/evals/run_judge_bench.sh`<br>5. `eval/run_eval.py` (including `cases_real/`) | - DRM pause releases SCK in `< 100ms` with zero `osascript` spawns on hot path.<br>- Anki re-export preserves identical `guid`s.<br>- Judge calibration reaches Cohen's $\kappa \ge 0.6$ vs. `feedback.jsonl`; Faithfulness & AnswerRelevancy pass `--smoke`; Vision eval maintains `>= 95%` on `cases_stress_1600` + `cases_real/`. |
| **P2 (Hybrid Retrieval & MCP 2026)** | 1. `cargo test --workspace --exclude screenpipe-rfdetr-mlx`<br>2. Read/write contention & `CancellableReadConnection` test under `write_queue` load<br>3. `cd apps/screenpipe-app-tauri && bun run test`<br>4. MCP client smoke test against `packages/screenpipe-mcp` (`:3031`) and `study_mcp.py` (`study://` resources + `Tasks`) | - Full Rust workspace & frontend tests green.<br>- `sqlite-vec` backfill never triggers `SQLITE_BUSY` or widens `SqliteWritePermit` transactions; RRF `GET /search` latency `< 50ms` local.<br>- Stateless HTTP MCP requests succeed across daemon restarts; `study://` resources and `Tasks` progress resolve cleanly. |
