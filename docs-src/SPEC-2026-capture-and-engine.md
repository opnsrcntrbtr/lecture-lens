# SPEC-2026: Capture & Engine Architecture (`feat/lecture-capture`)

**Status:** Proposed · **Date:** 2026-11-16 · **Owner:** `@capture-arch`  
**Scope:** `crates/screenpipe-screen`, `crates/screenpipe-a11y`, `crates/screenpipe-audio`, `crates/screenpipe-engine`, and the 8-file `feat/lecture-capture` delta over upstream `main` (`c88ffa4b2`).

---

## 1. Current State & Bottlenecks

### 1.1 `feat/lecture-capture` Fork Delta (8 files, `+526/-7` over `c88ffa4b2`)

The fork intentionally avoids new CLI flags, settings schema migrations, or `store.bin` mutations, keeping all lecture-capture behavior environment-driven:

1. **Per-App Audio Tap (`SCREENPIPE_AUDIO_TAP_APPS`)**:
   - `crates/screenpipe-audio/src/audio_manager/app_tap.rs` (new, 245 lines) polls `sysinfo::System::refresh_processes()` every `POLL = 5s` for case-insensitive substring matches (e.g. `google chrome`), excluding `self_pid`.
   - Reuses `spawn_process_tap_capture_for_pids` (`crates/screenpipe-audio/src/core/process_tap/macos.rs`) bound to session-scoped virtual device `APP_TAP_DEVICE_NAME = "App Tap"` (`crates/screenpipe-audio/src/core/device.rs`), started via `AudioManager::start_app_tap_device` (`crates/screenpipe-audio/src/audio_manager/manager.rs`).
   - Uses `REATTACH_BACKOFF = 30s` and `INSTANT_DEATH = 10s` (3 consecutive instant deaths warn about missing macOS *Screen & System Audio Recording* permission).
   - Spawned at startup in `crates/screenpipe-engine/src/bin/screenpipe-engine.rs` when `!config.disable_audio`.
2. **Configurable DRM Pause & Audio Retention (`SCREENPIPE_DRM_URLS`, `SCREENPIPE_DRM_KEEP_AUDIO`)**:
   - `crates/screenpipe-engine/src/drm_detector.rs` parses `SCREENPIPE_DRM_URLS` (`host` or `host/path-prefix`, scheme/`www.` stripped) into `EXTRA_DRM_URLS`.
   - `extra_drm_browser_window_on_screen()` enumerates on-screen layer-0 windows via `CGWindowListCopyWindowInfo` (no SCK calls) and reads each browser window's `ax::attr::document()` over macOS Accessibility with bounded timeouts (`0.3s` app, `0.2s` window).
   - `DRM_TRANSITION` (`tokio::sync::Notify`) wakes `crates/screenpipe-engine/src/vision_manager/monitor_watcher.rs` immediately on `set_drm_paused` transitions, releasing persistent `SCStream` handles in milliseconds instead of waiting up to the 60s watcher backstop.
   - `keep_audio_during_drm()` (`SCREENPIPE_DRM_KEEP_AUDIO=1|true|yes`) skips `stop_output_devices()` / `start_output_devices()` during DRM pause.

### 1.2 Core Capture Crates & Identified Bottlenecks

| Layer / Crate | Current Implementation | Observed Bottlenecks & Edge Cases |
|---|---|---|
| **`screenpipe-screen`** | Persistent ~2fps `SCStream` (`sck_rs`), `frame_comparison` histogram/hash skip, `stream_invalidation` on wake/cold monitor, `screencapturekit_process_exhausted()` containment, Apple Vision OCR fallback. | 1. **SCK content-protection blanking**: Any active `SCStream` (even `disableScreenshots` / text-only mode) causes Chrome/Widevine L1 to blank protected video frames at the compositor layer.<br>2. **Cold/Wedged SCK streams**: `sck_rs` can wedge silently without returning errors; `monitor_watcher` relies on `SILENT_DB_STALE_SECS_ATTENDED = 65s` / `WEDGE_DB_STALE_SECS = 120s` watchdogs to recover. |
| **`screenpipe-a11y`** | `UiRecorder` (`CGEventTap` + AX tree walk) feeds `ActivityFeed` for event-driven capture (`event_driven_capture.rs`, 200ms debounce, `CAPTURE_OPERATION_TIMEOUT = 20s` on macOS). | 1. **Thin/Custom Canvas Apps**: Browsers/Electron apps with custom canvases or DRM players expose sparse `AXDocument` or generic window titles, triggering OCR fallback or missing URL context.<br>2. **TCC `cdhash` fragility**: Ad-hoc rebuilds invalidate `kTCCServiceAccessibility`, silently breaking `get_focused_app_info()` and `extra_drm_browser_window_on_screen()` while capture keeps running and blanks DRM video (see `ADR-004`). |
| **`screenpipe-audio`** | `AudioManager` + `process_tap/macos.rs` (`AudioHardwareCreateProcessTap` + private aggregate device) + `parakeet-mlx` / `parakeet` / `whisper-rs` / `qwen3-asr` / `deepgram`. | 1. **Multi-tab / new-PID blindness in `app_tap.rs`**: Once `started && is_device_actively_streaming(&device)` is true, the 5s loop `continue`s without checking if *new* matching renderer PIDs appeared (e.g., user opens a second Chrome tab/profile playing a lecture while tab 1 is silent).<br>2. **`sysinfo::refresh_processes()` overhead**: Full process table scan every 5s.<br>3. **MLX Metal cache growth**: `engine.rs` (`mlx_memory`) notes MLX's caching allocator can grow to `10+ GB` over hours on a `0.6B` model unless `mlx_set_cache_limit` is tuned and `mlx_clear_cache()` is called periodically. |
| **`screenpipe-engine`** | `event_driven_capture.rs` (AX-first + conditional `OcrGate`), `drm_detector.rs`, `monitor_watcher.rs`, `store.bin` CLI flag persistence. | 1. **AppleScript subprocesses in `get_browser_url_ax`**: `get_safari_url_for_drm()` and `get_arc_url_for_drm()` spawn `osascript` synchronously on the hot/poll path.<br>2. **All-or-nothing DRM pause**: A DRM tab visible on any monitor pauses SCK across *all* monitors, losing slide/notes capture on a secondary display.<br>3. **`store.bin` boolean trap**: Passing `--pause-on-drm-content` or `--disable-*` persists permanently into `~/.screenpipe/store.bin`. |

---

## 2. Proposed Enhancements & Upstream-Alignment Strategy

### 2.1 Upstream-Alignment Rules
1. **Env-driven fork delta**: Keep all lecture-capture extensions behind `SCREENPIPE_*` environment variables (no new `clap` flags, no `store.bin` schema changes, no `enabled_devices` persistence for virtual devices like `APP_TAP_DEVICE_NAME`).
2. **Hot-path invariants (`AGENTS.md` & `VISION.md`)**:
   - **Target budgets**: `<20% CPU`, `<3GB RAM` on release builds.
   - **Audio callbacks**: Zero heap allocations, zero locks, zero blocking I/O in `TapCallbackCtx` / CoreAudio `AudioDeviceIOProc`.
   - **Recording durability**: Never drop or reject an authorized recording due to transcription or DB backpressure; defer background STT/projection instead.
3. **AX-first, OCR-fallback**: Preserve `event_driven_capture.rs` + `OcrGate` so idle/text-rich apps (IDE, Obsidian, docs) never invoke Vision OCR, reserving Vision/GPU for sparse trees and lecture slides.

### 2.2 Prioritized Enhancement Roadmap
- **P0 (Resilience)**: Fix `app_tap.rs` new-PID / multi-tab attachment drift and replace `sysinfo` full-table polling with CoreAudio process-object property listeners (`kAudioHardwarePropertyProcessObjectList`) where available.
- **P0 (DRM Accuracy)**: Add a lock-free/localhost push signal from the browser companion extension as Tier-0 URL truth before falling back to AX/`osascript`, eliminating `osascript` spawns and supporting Arc/Comet/Brave/Chrome tabs uniformly.
- **P1 (Memory & Thermal)**: Enforce a strict MLX Metal cache ceiling (`mlx_set_cache_limit`) + idle cache flush in `parakeet-mlx`, and evaluate CoreML/ANE-backed Parakeet (via Swift/FluidAudio or ONNX/CoreML) to keep total RSS `<3GB` during 2+ hour lectures.
- **P2 (Multi-Monitor DRM)**: Investigate per-display SCK stream invalidation (`stream_invalidation::invalidate_monitor_stream(monitor_id)`) only if macOS compositor isolates Widevine surfaces per display; otherwise keep global SCK release as the safe default.

---

## 3. Concrete ADRs & Design Specs

### ADR-A: App Tap Resilience & Multi-App / Multi-Tab Edge Cases

- **Context**: In `app_tap.rs`, `matching_pids()` collects all PIDs matching `SCREENPIPE_AUDIO_TAP_APPS` (e.g., Chrome main + GPU + all Renderer helpers). `spawn_process_tap_capture_for_pids` translates those PIDs via `resolve_pids_to_audio_object_ids` into a `CATapDescription` stereo mixdown. Currently, `app_tap.rs` short-circuits with `if started && is_device_actively_streaming(&device) { continue; }`.
- **Failure Modes**:
  1. **New Renderer PID mid-session**: If Chrome is already streaming audio from Renderer PID `A` (or even silent zero-filled buffers from `A`) and the user opens a lecture in a new tab (Renderer PID `B`), `B` is never added to the `CATapDescription` until `A` dies.
  2. **Multi-app pattern (`"Google Chrome,zoom.us"`)**: Launching Zoom while Chrome is already tapped suffers the same blind spot—Zoom's PIDs are ignored until Chrome's tap tears down.
  3. **30s Reattach Gap**: When a browser recycles its audio-decoding renderer on tab navigation, `REATTACH_BACKOFF = 30s` can drop the first ~30s of a new lecture video.
- **Design Spec**:
  1. **Active Audio-Object Diffing**: Track the `BTreeSet<i32>` of *audio-producing* PIDs (or `AudioObjectID`s resolved via `kAudioHardwarePropertyTranslatePIDToProcessObject`) currently bound to the tap. Only rebuild the tap when `active_audio_pids != bound_pids` *and* a new PID actually has a live CoreAudio process object (`resolve_pids_to_audio_object_ids(&new_pids)` is non-empty).
  2. **Differentiated Backoff**: Separate *stream-died* backoff (`2s` initial, exponential to `30s` on `INSTANT_DEATH`) from *no-audio-object-yet* idle polling (`5s`), so tab-navigation renderer swaps re-attach within `2–5s` instead of `30s`.
  3. **Event-driven PID discovery (optional optimization)**: Subscribe to `kAudioHardwarePropertyProcessObjectList` in `process_tap/macos.rs` to wake `app_tap` via `Notify` instead of scanning all OS processes with `sysinfo::System::refresh_processes()` every 5s.
  4. **Never tap `self_pid` or adopt physical output**: Preserve `tap_only_aggregate_desc` (`is_private = true`, no `main_sub_device`) and `self_pid` exclusion so hardware output switches (headphones ↔ speakers) remain seamless.

### ADR-B: DRM URL Detection — AX Polling vs. Browser Extension Push Signal

- **Context**: `drm_detector.rs` currently relies on `AXDocument` (`get_browser_url_ax`), `extra_drm_browser_window_on_screen()`, and synchronous `osascript` fallbacks for Safari/Arc.
- **Failure Modes**:
  1. Several Chromium/WebKit browsers (Arc, Comet, multi-window Chrome profiles) do not reliably expose `AXDocument` for background/split tabs, and `osascript` spawns a subprocess on the poll path.
  2. Window-title fallback (`get_drm_url_from_window_title`) fails on generic titles (e.g., the course portal's `"Learning Experience Platform"`).
  3. If `kTCCServiceAccessibility` is stale after an ad-hoc rebuild, `AXDocument` silently returns `None` and Widevine blanks the video.
- **Design Spec (Tiered Hybrid Gate)**:
  - **Tier 0 — Localhost Browser Extension Signal (Zero-AX, Sub-millisecond)**:
    - Extend the existing companion browser extension (`packages/browser-extension`) to POST active/visible tab URLs + `audible`/`eme_drm` state to a lightweight loopback endpoint or shared memory/Unix socket (e.g., `POST http://127.0.0.1:3030/internal/browser-state` gated behind `SCREENPIPE_DRM_URLS` or an in-memory `RwLock<Vec<BrowserTabState>>` with a 10s TTL).
    - `pre_capture_drm_check` and `poll_drm_clear` check this in-memory table first (O(1), no IPC, no `osascript`, works even if `AXDocument` is unexposed).
  - **Tier 1 — Bounded AX (`AXDocument` + `CGWindowList`)**:
    - Keep `extra_drm_browser_window_on_screen()` as the authoritative fallback when the extension is not installed or its TTL expires.
  - **Tier 2 — Eliminate `osascript` on Hot Path**:
    - Move `get_safari_url_for_drm` / `get_arc_url_for_drm` behind a rate-limited (≥2s) async background cache or remove if Tier 0 covers Arc/Safari, ensuring `pre_capture_drm_check` never blocks a capture turn on subprocess spawn.
  - **Audio Invariant**: Keep `SCREENPIPE_DRM_KEEP_AUDIO=1` paired with `--experimental-coreaudio-system-audio` (or `SCREENPIPE_AUDIO_TAP_APPS`) so SCK is *completely* released during DRM pause while CoreAudio process taps continue capturing lecture audio.

### ADR-C: Parakeet-MLX & CoreML/ANE STT Integration (`<3GB RAM`, `<20% CPU`)

- **Context**: `crates/screenpipe-audio/src/transcription/engine.rs` already supports `parakeet-mlx` (via `mlx-rs`), `parakeet` (via `audiopipe`/`ort`), `qwen3-asr`, and `whisper-rs`. For English/European lectures on Apple Silicon, Parakeet-TDT (`0.6B` v2/v3) delivers ~30–35x real-time throughput and lower WER than Whisper Large-v3, while WhisperKit/CoreML remains superior for 99+ multilingual audio.
- **Bottleneck**: As documented in `engine.rs` (`mlx_memory`), MLX's Metal buffer cache grows unbounded (`10+ GB`) over hours of continuous transcription if left uncapped, violating `VISION.md`'s `<3GB RAM` target and starving oMLX (`127.0.0.1:8001`) of unified memory during live lectures.
- **Design Spec**:
  1. **Strict MLX Cache Cap**: In `parakeet-mlx` initialization, enforce `mlx_set_cache_limit(256 * 1024 * 1024)` (256 MB cap, configurable via `SCREENPIPE_MLX_CACHE_LIMIT_MB`) and invoke `mlx_clear_cache()` between lecture/meeting chunks or when `active + cache > 1.0 GB`.
  2. **Unified-Memory Coexistence with oMLX**: Expose `/health` or `/audio/metrics` MLX active/cache MB so `Lecture Lens.app` (`JobQueue`) can see GPU memory pressure before launching heavy VLM slide-description jobs on oMLX.
  3. **CoreML / ANE Offload Path**: Evaluate `parakeet` via CoreML Execution Provider (`ort` CoreML EP) or a Swift/FluidAudio ANE sidecar for users running local VLMs on the Metal GPU simultaneously. Offloading STT to the Apple Neural Engine (ANE) isolates GPU memory and bandwidth for oMLX while preserving `<20% CPU`.
  4. **Default Routing**: Keep `parakeet-mlx` (capped) or `parakeet` (CoreML/ONNX) as the recommended English lecture-capture engine, and `whisper-rs` / WhisperKit CoreML for multilingual sessions.

---

## 4. Correlated Nov 2026 References

References (titles only; looked up on 2026-11-16 — search the title to find the source):

1. **Apple CoreAudio Process Taps (`CATapDescription`, `AudioHardwareCreateProcessTap`, Private Aggregate Devices)**
   - Apple Developer Documentation — *Capturing System Audio with Core Audio Taps* (`CATapDescription`, `AudioHardwareCreateProcessTap`, `AudioHardwareCreateAggregateDevice`):
   - Apple Developer Documentation — `AudioHardwareCreateProcessTap(_:_:)`:
   - Recall.ai Engineering — *CoreAudio Process Tap & Aggregate Device Routing*:
   - CoreAudio Process Tap (`kAudioHardwarePropertyProcessObjectList` / `kAudioHardwarePropertyTranslatePIDToProcessObject` reference):

2. **macOS ScreenCaptureKit, Widevine L1 / FairPlay DRM & Content Protection**
   - Rekort — *Why ScreenCaptureKit Blanks Widevine / FairPlay Protected Layers*:
   - Screenify Studio — *macOS Protected Surface Layers & Audio Passthrough Behavior*:
   - Screenkite — *macOS TCC Screen & System Audio Recording Desync / Permission Recovery*:

3. **Parakeet-MLX, CoreML/ANE (FluidAudio) & WhisperKit Benchmarks on Apple Silicon**
   - LocalAIMaster — *Apple Silicon STT Latency & Throughput (Parakeet CoreML/FluidAudio vs `parakeet-mlx` vs WhisperKit)*:
   - MacParakeet — *Parakeet MLX vs CoreML Memory & Metal Overhead*:
   - Whispaste — *Parakeet v2/v3 Long-Form Real-Time Factor (~30–35x RT)*:
   - Sotto — *WER & Multilingual Trade-offs (Parakeet-TDT vs WhisperKit Large-v3)*:

4. **Screenpipe Accessibility-Tree-First Capture & Conditional OCR Fallback**
   - Screenpipe Docs — *Accessibility-First Text Extraction & Conditional OCR Fallback*:
   - Screenpipe Blog — *Event-Driven Capture & CPU Reduction*:
