import Foundation

// Types for the toolkit's --json contract (02-system-design.md §3.1).
// Every field the tools might omit is optional: decoding must never fail on a
// missing key, only on malformed JSON.

struct DoctorResult: Decodable, Sendable {
    let ok: Bool
    let checks: [Check]
    struct Check: Decodable, Sendable, Identifiable {
        let id: String
        let ok: Bool
        let level: String
        let message: String
        let fix: String?
    }
}

struct ModeState: Decodable, Sendable {
    let running: Bool
    let mode: String
    let pinned: String?
    let autoswitch: Bool
}

struct SessionList: Decodable, Sendable {
    let sessions: [Session]
}

struct Session: Decodable, Sendable, Identifiable, Hashable {
    let id: String?
    let title: String?
    let start: String?
    let end: String?
    let minutes: Double?
    let frames: Int?
    let built: Bool
    let folder: String
    var key: String { id ?? folder }
    var displayTitle: String { title ?? "Untitled lecture" }
}

struct AskResult: Decodable, Sendable {
    let ok: Bool
    let question: String?
    let answer: String?
    let error: String?
    let sources: [Source]?
    let model: String?
    let prompt_version: String?
    let latency_s: Double?
    struct Source: Decodable, Sendable, Identifiable, Hashable {
        let n: Int
        let ts: String
        let src: String
        let `where`: String
        let text: String
        var id: Int { n }
    }
}

struct BuildResult: Decodable, Sendable {
    let ok: Bool
    let folder: String?
}

/// eval/results/<run>/summary.json — the vision eval and the DeepEval suite
/// write different shapes, so both are optional here.
struct EvalSummary: Decodable, Sendable {
    let suite: String?
    let model: String?
    let generator: String?
    let judge: String?
    let metrics: [String: Metric]?
    let by_config: [String: [String: FlexValue]]?
    struct Metric: Decodable, Sendable {
        let mean: Double?
        let pass: Int?
        let n: Int?
        let threshold: Double?
    }
}

/// A JSON value that may be a number or an object ({pass, n, mean, …}).
enum FlexValue: Decodable, Sendable {
    case number(Double)
    case agg(pass: Int, n: Int, mean: Double?)
    case other
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let d = try? c.decode(Double.self) { self = .number(d); return }
        struct Agg: Decodable { let pass: Int; let n: Int; let mean: Double? }
        if let a = try? c.decode(Agg.self) { self = .agg(pass: a.pass, n: a.n, mean: a.mean); return }
        self = .other
    }
}

/// Health of screenpipe itself (GET 127.0.0.1:3030/health, no auth needed).
struct ScreenpipeHealth: Decodable, Sendable {
    let status: String?
    let frame_status: String?
    /// e.g. "ok", "permission_denied" — why vision isn't producing frames.
    let vision_reason: String?
    let audio_status: String?
    let last_frame_timestamp: String?
    let last_audio_timestamp: String?
    /// True while a DRM video page is focused and screen capture is released
    /// so the video isn't blanked (ADR-004). Audio keeps recording.
    let drm_content_paused: Bool?
}

struct OmlxStatus: Decodable, Sendable {
    let status: String?
    let loaded_models: [String]?
    let active_requests: Int?
}
