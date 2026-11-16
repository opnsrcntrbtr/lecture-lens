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

/// `lecture next --json`: the next class from the programme calendar (lectures/calendar.json).
struct NextClass: Decodable, Sendable {
    let week: Int
    let title: String
    let date: String
    let start: String
    let end: String
    let module: String?
    let module_title: String?
    let tz: String?

    var day: Date? {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.timeZone = TimeZone(identifier: "Asia/Kolkata")
        return f.date(from: date)
    }
    var daysAway: Int? {
        guard let d = day else { return nil }
        var cal = Calendar.current; cal.timeZone = TimeZone(identifier: "Asia/Kolkata")!
        return cal.dateComponents([.day], from: cal.startOfDay(for: .now), to: cal.startOfDay(for: d)).day
    }
    var whenText: String {
        let f = DateFormatter(); f.dateFormat = "EEE d MMM"; f.timeZone = TimeZone(identifier: "Asia/Kolkata")
        let d = day.map { f.string(from: $0) } ?? date
        let rel: String
        switch daysAway { case 0?: rel = "today"; case 1?: rel = "tomorrow"; case let n?: rel = "in \(n) days"; default: rel = "" }
        return "\(d) · \(start)–\(end) IST" + (rel.isEmpty ? "" : " · \(rel)")
    }
    var moduleText: String {
        guard let m = module, !m.isEmpty else { return "Week \(week) · orientation" }
        return "Week \(week) · \(m)" + ((module_title ?? "").isEmpty ? "" : ": \(module_title!)")
    }
}

/// `lecture after --json` (also saved as after_class.json in the lecture folder).
struct AfterClassReport: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let at: String?
    let steps: [Step]
    /// EP-2 regression gates (model-free); any failure marks the lecture "needs review".
    let gates: [Gate]?
    let needs_review: Bool?
    struct Step: Decodable, Sendable, Identifiable { let name: String; let ok: Bool; let note: String; var id: String { name } }
    struct Gate: Decodable, Sendable, Identifiable {
        let name: String; let value: GateValue?; let threshold: GateValue?; let ok: Bool
        var id: String { name }
    }
    /// A gate's value or threshold: a number, a string, or null.
    enum GateValue: Decodable, Sendable, CustomStringConvertible {
        case number(Double), text(String)
        init(from d: Decoder) throws {
            let c = try d.singleValueContainer()
            if let n = try? c.decode(Double.self) { self = .number(n) } else { self = .text((try? c.decode(String.self)) ?? "") }
        }
        var description: String {
            switch self {
            case .number(let n): n == n.rounded() ? String(Int(n)) : String(n)
            case .text(let t): t
            }
        }
    }
}

/// `lecture health --json` (CT-1): one probe of the engine's outputs and any alert.
struct CaptureHealth: Decodable, Sendable {
    let ok: Bool
    let record: Record
    let alert: Alert?
    struct Record: Decodable, Sendable {
        let t: String; let capture_running: Bool; let audio_chunk_age_s: Double?; let audio_level_db: Double?
        let frame_age_s: Double?; let frames_expected: Bool?; let status: String
    }
    struct Alert: Decodable, Sendable { let status: String; let since: String; let lasted_s: Int; let message: String }

    static func label(_ status: String) -> String {
        switch status {
        case "silent_audio": "silent audio"
        case "audio_stalled": "audio stopped"
        case "no_frames": "no screen frames"
        case "stopped": "not capturing"
        default: "ok"
        }
    }

    static func detail(_ r: Record) -> String {
        var parts: [String] = []
        if let a = r.audio_chunk_age_s { parts.append("audio \(Int(a)) s ago") }
        if let l = r.audio_level_db { parts.append(String(format: "%.0f dB", l)) }
        if r.frames_expected == true, let f = r.frame_age_s { parts.append("frame \(Int(f)) s ago") }
        return parts.isEmpty ? label(r.status) : label(r.status) + " · " + parts.joined(separator: " · ")
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
    /// Deterministic citation checks (study.check_citations); absent in older outputs.
    let citation_warnings: [String]?
    let citation_coverage: Double?
    let model: String?
    let prompt_version: String?
    let latency_s: Double?
    /// Where the sources came from ("screenpipe" or "lecture index"), and why when not screenpipe.
    let retrieval: String?
    let note: String?
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
        /// 95% Wilson interval [low, high] for pass/n; absent in older summaries.
        let ci95: [Double]?
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
