import Foundation

/// Append-only feedback log (feedback/feedback.jsonl). Each line is one
/// judgement the learner made about one AI output. Never rewritten — only appended — so
/// the eval pipeline can trust its history (02-system-design.md §3.2).
struct FeedbackRecord: Codable, Sendable {
    var id = UUID().uuidString
    var ts = ISO8601DateFormatter().string(from: Date())
    let target: String          // answer | note | slide | card
    let ref: [String: String]   // lecture, slide, question …
    let input: String
    let output: String
    let context: [String]
    let rating: Int             // +1 / -1
    let correction: String?
    let model: String?
    let prompt_version: String?
}

enum FeedbackStore {
    static func append(_ r: FeedbackRecord) throws {
        let url = Paths.feedback
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        var line = try enc.encode(r)
        line.append(0x0A)
        if !FileManager.default.fileExists(atPath: url.path) {
            try line.write(to: url)
            return
        }
        let h = try FileHandle(forWritingTo: url)
        defer { try? h.close() }
        try h.seekToEnd()
        try h.write(contentsOf: line)
    }

    static func load() -> [FeedbackRecord] {
        guard let s = try? String(contentsOf: Paths.feedback, encoding: .utf8) else { return [] }
        let dec = JSONDecoder()
        return s.split(separator: "\n").compactMap { try? dec.decode(FeedbackRecord.self, from: Data($0.utf8)) }
    }
}

/// Line-preserving editor for .env: comments and unknown keys survive,
/// writes are atomic, and the previous file is kept as .env.bak.
struct EnvFile {
    private(set) var lines: [String]
    let url: URL

    init(url: URL = Paths.envFile) {
        self.url = url
        let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        lines = text.components(separatedBy: "\n")
    }

    func value(_ key: String) -> String? {
        for l in lines {
            let t = l.trimmingCharacters(in: .whitespaces)
            guard !t.hasPrefix("#"), let eq = t.firstIndex(of: "=") else { continue }
            if t[..<eq] == key {
                return String(t[t.index(after: eq)...]).trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            }
        }
        return nil
    }

    mutating func set(_ key: String, _ value: String) {
        let needsQuotes = value.contains(" ") || value.contains(",")
        let rendered = "\(key)=\(needsQuotes ? "\"\(value)\"" : value)"
        if let i = lines.firstIndex(where: { $0.trimmingCharacters(in: .whitespaces).hasPrefix("\(key)=") }) {
            lines[i] = rendered
        } else {
            if lines.last == "" { lines.insert(rendered, at: lines.count - 1) } else { lines.append(rendered) }
        }
    }

    func save() throws {
        let fm = FileManager.default
        let bak = url.deletingLastPathComponent().appendingPathComponent(".env.bak")
        if fm.fileExists(atPath: url.path) {
            try? fm.removeItem(at: bak)
            try fm.copyItem(at: url, to: bak)
        }
        try lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
    }
}

/// One heavy job at a time (one GPU): lecture builds, evals, benchmarks.
@MainActor
@Observable
final class Job: Identifiable {
    let id = UUID()
    let title: String
    let started = Date()
    var log: [String] = []
    var status: Int32? = nil
    var running: Bool { status == nil }
    var succeeded: Bool { status == 0 }
    init(title: String) { self.title = title }
}

@MainActor
@Observable
final class JobQueue {
    var jobs: [Job] = []
    private var tail: Task<Void, Never>? = nil

    var busy: Bool { jobs.contains { $0.running } }

    /// Queue a command; it starts when the previous job finishes.
    @discardableResult
    func enqueue(_ title: String, _ executable: URL, _ args: [String], onDone: (@MainActor (Job) -> Void)? = nil) -> Job {
        let job = Job(title: title)
        jobs.insert(job, at: 0)
        let previous = tail
        tail = Task { @MainActor in
            await previous?.value
            job.log.append("$ \(executable.lastPathComponent) \(args.joined(separator: " "))")
            let status = await ToolRunner.stream(executable, args) { line in
                Task { @MainActor in
                    job.log.append(line)
                    if job.log.count > 4000 { job.log.removeFirst(job.log.count - 4000) }
                }
            }
            job.status = status
            onDone?(job)
        }
        return job
    }
}
