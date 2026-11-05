import AppKit
import Foundation

/// Lecture Lens Studio: the in-browser model path (STUDY_LLM_PROVIDER=browser). The app
/// never talks to it; both sides read and write files in the lectures folder.
enum Studio {
    static let defaultURL = "https://opnsrcntrbtr.github.io/lecture-lens/studio/"

    static var enabled: Bool { (EnvFile().value("STUDY_LLM_PROVIDER") ?? "omlx").lowercased() == "browser" }

    /// Opens the Studio page in Google Chrome when installed (the Prompt API is Chrome-only).
    /// Returns an error message, or nil.
    @discardableResult
    static func open(_ raw: String? = nil) -> String? {
        let s = (raw ?? EnvFile().value("STUDY_STUDIO_URL") ?? "").trimmingCharacters(in: .whitespaces)
        guard let url = URL(string: s.isEmpty ? defaultURL : s), ["https", "http"].contains(url.scheme ?? "") else {
            return "Studio page must be an https address"
        }
        if let chrome = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.google.Chrome") {
            NSWorkspace.shared.open([url], withApplicationAt: chrome, configuration: NSWorkspace.OpenConfiguration())
        } else {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    enum JobState: Equatable { case none, pending, done(engine: String), finished(engine: String) }

    /// Where a lecture is in the browser path: no job, waiting for the Studio, done in the
    /// Studio but not finished (vault note not written yet), or finished.
    static func state(folder: URL) -> JobState {
        let fm = FileManager.default
        let job = folder.appendingPathComponent("studio_job.json"), res = folder.appendingPathComponent("studio_result.json")
        guard fm.fileExists(atPath: job.path) else { return .none }
        guard let data = try? Data(contentsOf: res),
              let r = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return .pending }
        let engine = r["engine"] as? String ?? "browser"
        func date(_ url: URL, _ key: String) -> Date? {
            guard let d = try? Data(contentsOf: url), let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
                  let s = o[key] as? String else { return nil }
            return ISO8601DateFormatter.flexible.date(from: s)
        }
        let finished = (r["finished_at"] as? String).flatMap { ISO8601DateFormatter.flexible.date(from: $0) }
        if let made = date(job, "created_at"), let f = finished, f < made { return .pending }
        if let fin = date(folder.appendingPathComponent("manifest.json"), "finished_at"), let f = finished, fin >= f {
            return .finished(engine: engine)
        }
        return .done(engine: engine)
    }

    /// The Studio's live heartbeat (studio_live.json): its engine, if it wrote in the last 3 minutes.
    static func liveEngine(folder: URL) -> String? {
        guard let d = try? Data(contentsOf: folder.appendingPathComponent("studio_live.json")),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
              let at = (o["at"] as? String).flatMap({ ISO8601DateFormatter.flexible.date(from: $0) }),
              Date().timeIntervalSince(at) < 180 else { return nil }
        return o["engine"] as? String ?? "browser"
    }
}
