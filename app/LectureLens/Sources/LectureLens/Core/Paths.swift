import Foundation

/// Where the toolkit lives. Defaults to ~/lecture-lens; overridable in Settings.
enum Paths {
    static var study: URL {
        if let custom = UserDefaults.standard.string(forKey: "studyPath"), !custom.isEmpty {
            return URL(fileURLWithPath: (custom as NSString).expandingTildeInPath)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("lecture-lens")
    }
    /// The selected course's lecture folder (CourseStore); the default course's is lectures/.
    static var lectures: URL {
        if let r = CourseStore.root { return URL(fileURLWithPath: (r as NSString).expandingTildeInPath) }
        return study.appendingPathComponent("lectures")
    }
    static var results: URL { study.appendingPathComponent("eval/results") }
    static var feedback: URL { study.appendingPathComponent("feedback/feedback.jsonl") }
    static var envFile: URL { study.appendingPathComponent(".env") }
    static func tool(_ name: String) -> URL { study.appendingPathComponent(name) }

    /// A GUI app starts with a minimal PATH (/usr/bin:/bin:…), where `python3` is
    /// Apple's 3.9 without Pillow. Put Homebrew and the user's tool dirs first so
    /// the toolkit runs exactly as it does in Terminal.
    static var toolEnvironment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extra = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "\(home)/.cargo/bin",
                     "\(home)/.bun/bin", "\(home)/.local/bin", study.path,
                     study.deletingLastPathComponent().appendingPathComponent("target/release").path]
        env["PATH"] = (extra + [env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"]).joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        // every tool works on the course shown in the app (courses.py reads STUDY_COURSE)
        if let c = CourseStore.id { env["STUDY_COURSE"] = c } else { env.removeValue(forKey: "STUDY_COURSE") }
        return env
    }
}
