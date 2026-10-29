import Foundation

/// After class: notice Zoom's files (transcript, Q&A export, deck) beside today's class and
/// run `lecture ingest` once per new file. A file counts once it has not changed for 10 s.
/// Files the toolkit writes itself are skipped; the toolkit decides by content what a file is.
enum AutoImport {
    static let toolkitFiles: Set<String> = ["questions_to_ask.md", "brief.md", "zoom_summary.md", "transcript.md",
                                            "transcript_merged.md", "notes.md", "qa.md", "digest.md", "slides.md"]
    static let kinds: Set<String> = ["md", "txt", "vtt", "pptx"]
    private static let seenKey = "autoImportSeen"

    static var enabled: Bool {
        ["1", "true", "on", "yes"].contains((EnvFile().value("STUDY_AUTO_IMPORT") ?? "0").lowercased())
    }

    /// Candidate files with their modification time, from today's live folder and the class folder.
    static func candidates(live: URL, lecture: URL?, now: Date = Date()) -> [(URL, Date)] {
        let fm = FileManager.default
        return [live, lecture].compactMap { $0 }.flatMap { dir -> [(URL, Date)] in
            let items = (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey])) ?? []
            return items.compactMap { u in
                guard kinds.contains(u.pathExtension.lowercased()), !toolkitFiles.contains(u.lastPathComponent),
                      let v = try? u.resourceValues(forKeys: [.contentModificationDateKey, .isRegularFileKey]),
                      v.isRegularFile == true, let m = v.contentModificationDate,
                      now.timeIntervalSince(m) >= 10 else { return nil }
                return (u, m)
            }
        }
    }

    /// The built lecture folder for a day (yyyyMMdd-HHmm_…), newest first.
    static func lectureFolder(day: String) -> URL? {
        let dirs = (try? FileManager.default.contentsOfDirectory(at: Paths.lectures, includingPropertiesForKeys: nil)) ?? []
        return dirs.filter { $0.lastPathComponent.hasPrefix(day + "-") &&
                             FileManager.default.fileExists(atPath: $0.appendingPathComponent("manifest.json").path) }
                   .sorted { $0.lastPathComponent > $1.lastPathComponent }.first
    }

    /// New, settled files not imported before; marks them seen when `commit` is true.
    static func pending(day: String, commit: Bool) -> (lecture: URL, files: [URL])? {
        guard let lecture = lectureFolder(day: day) else { return nil }
        let live = Paths.lectures.appendingPathComponent("live/\(day)")
        var seen = Set(UserDefaults.standard.stringArray(forKey: seenKey) ?? [])
        // Files older than the last ingest report were already imported (by hand or a previous run).
        let reported = (try? lecture.appendingPathComponent("ingest_report.json")
            .resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
        let fresh = candidates(live: live, lecture: lecture)
            .filter { $0.1 > reported && !seen.contains(signature($0.0, $0.1)) }
        guard !fresh.isEmpty else { return nil }
        if commit {
            fresh.forEach { seen.insert(signature($0.0, $0.1)) }
            UserDefaults.standard.set(Array(seen), forKey: seenKey)
        }
        return (lecture, fresh.map(\.0))
    }

    static func signature(_ u: URL, _ m: Date) -> String { "\(u.path)|\(Int(m.timeIntervalSince1970))" }
}
