import AppKit
import SwiftUI

/// Live class view: what live_class.py has written for today in lectures/live/<yyyyMMdd>/:
/// Zoom Q&A threads with drafted follow-ups, questions to ask, the running outline and
/// the slide log. Read-only; refreshed every few seconds. Copy a question, then paste it
/// into Zoom yourself.
struct LiveView: View {
    @State private var live = LiveData()
    @State private var copied: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                statusBox
                askNowBox
                qaBox
                questionsBox
                outlineBox
                slidesBox
            }
            .padding(24)
        }
        .navigationTitle("Live class")
        .task {
            while !Task.isCancelled {
                live = await LiveData.load()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    // MARK: status
    private var statusBox: some View {
        GroupBox("Follower") {
            HStack(spacing: 10) {
                Circle().fill(live.isFresh ? Color.green : Color.orange).frame(width: 9, height: 9)
                if let s = live.state {
                    Text(live.isFresh ? "Following · capture \(s.status ?? "?")" : "Not updated for a while")
                    Text("· \(s.words ?? 0) words re-transcribed · \(live.slides.count) slides · \(live.threads.count) Q&A threads")
                        .foregroundStyle(.secondary)
                } else {
                    Text("No live class today. Start capture in live mode, then run live_class.py.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Open folder") { NSWorkspace.shared.open(live.folder) }.disabled(live.state == nil)
            }
            .font(.callout)
        }
    }

    // MARK: ask now
    private var askNowBox: some View {
        GroupBox("Ask now: top 3 for what is being taught") {
            VStack(alignment: .leading, spacing: 10) {
                if live.askNow.isEmpty { Text("Ranked every 5 minutes once questions exist.").foregroundStyle(.secondary) }
                ForEach(Array(live.askNow.enumerated()), id: \.offset) { i, c in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(alignment: .top) {
                            Text("\(i + 1). ").font(.callout.weight(.bold)) + Text(c.q).font(.callout.weight(.semibold))
                            Spacer()
                            copyButton(c.q)
                        }
                        Text("Draft: \(c.answer)\(live.sourceTitles(c.sources))").font(.caption).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: Q&A
    private var qaBox: some View {
        GroupBox("Zoom Q&A, newest first") {
            VStack(alignment: .leading, spacing: 12) {
                if live.threads.isEmpty {
                    Text("No Q&A seen yet. Keep Zoom's Q&A panel open beside the slides.").foregroundStyle(.secondary)
                }
                ForEach(live.threads.reversed()) { t in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("\(t.time) · \(t.by)").font(.caption.weight(.semibold))
                            .foregroundStyle(t.by == "You" ? Color.accentColor : .secondary)
                        Text(t.q).textSelection(.enabled)
                        ForEach(Array(t.answers.enumerated()), id: \.offset) { _, a in
                            Text("↳ \(a.by): \(a.text)").font(.callout).foregroundStyle(.secondary)
                                .textSelection(.enabled).padding(.leading, 12)
                        }
                        ForEach(live.followups[t.id] ?? []) { f in followupRow(f) }
                    }
                    Divider()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func followupRow(_ f: Drafted) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .top) {
                Text("Follow-up (\(f.type)): ").font(.callout.weight(.semibold)) + Text(f.q).font(.callout)
                Spacer()
                copyButton(f.q)
            }
            Text("Draft: \(f.answer)\(live.sourceTitles(f.sources))").font(.caption).foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
        .padding(.leading, 12)
    }

    // MARK: questions to ask
    private var questionsBox: some View {
        GroupBox("Questions to ask (every 10 minutes)") {
            VStack(alignment: .leading, spacing: 10) {
                if live.questions.isEmpty { Text("First questions after 10 minutes of class.").foregroundStyle(.secondary) }
                ForEach(live.questions.reversed().prefix(9)) { q in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(alignment: .top) {
                            Text("\(q.type): ").font(.callout.weight(.semibold)) + Text(q.q).font(.callout)
                            Spacer()
                            copyButton(q.q)
                        }
                        Text("Draft: \(q.answer)\(live.sourceTitles(q.sources))").font(.caption).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: outline and slides
    private var outlineBox: some View {
        GroupBox("Running outline") {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(live.outline.reversed()) { o in
                    Text(LiveData.clock(o.from) + "–" + LiveData.clock(o.to)).font(.caption.weight(.semibold))
                    Text(o.bullets).font(.callout).textSelection(.enabled)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var slidesBox: some View {
        GroupBox("Slides") {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(live.slideTitles, id: \.self) { s in Text(s).font(.callout) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func copyButton(_ text: String) -> some View {
        Button(copied == text ? "Copied" : "Copy") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
            copied = text
        }
        .controlSize(.small)
    }
}

// MARK: - data written by live_class.py

struct LiveState: Decodable { var status: String?; var words: Int?; var updated: String? }
struct QAAnswer: Decodable { var by: String; var time: String; var text: String }
struct QAThread: Decodable, Identifiable { var id: String; var by: String; var time: String; var q: String; var answers: [QAAnswer] }
struct Drafted: Decodable, Identifiable {
    var thread: String?; var t: String; var type: String; var q: String; var answer: String; var sources: [String]
    var id: String { t + q }
}
struct OutlinePart: Decodable, Identifiable { var from: String; var to: String; var bullets: String; var id: String { from } }
struct SlideEntry: Decodable { var t: String; var title: String }
struct Evidence: Decodable { var id: String; var title: String }
struct AskNowFile: Decodable { var top: [Drafted] }
/// Written by live_class.py: non-trivial thread ids and the curated follow-ups (on-topic, no repeats).
struct QAViewFile: Decodable { var threads: [String]; var followups: [String: [Drafted]] }

struct LiveData {
    var folder = URL(fileURLWithPath: "/")
    var state: LiveState?
    var threads: [QAThread] = []
    var followups: [String: [Drafted]] = [:]
    var questions: [Drafted] = []
    var outline: [OutlinePart] = []
    var slides: [SlideEntry] = []
    var evidence: [String: String] = [:]
    var askNow: [Drafted] = []

    var isFresh: Bool {
        guard let u = state?.updated, let d = ISO8601DateFormatter.flexible.date(from: u) else { return false }
        return Date().timeIntervalSince(d) < 180
    }

    /// Slide titles in order, a title repeated back to back shown once.
    var slideTitles: [String] {
        var out: [String] = []
        for s in slides where out.last?.hasSuffix(s.title) != true { out.append("\(LiveData.clock(s.t))  \(s.title)") }
        return out
    }

    func sourceTitles(_ ids: [String]) -> String {
        let t = ids.compactMap { evidence[$0] }
        return t.isEmpty ? "" : " (" + t.joined(separator: "; ") + ")"
    }

    static func clock(_ iso: String) -> String {
        guard let d = ISO8601DateFormatter.flexible.date(from: iso) else { return "" }
        let f = DateFormatter(); f.dateFormat = "HH:mm"
        return f.string(from: d)
    }

    static func load(day: Date = Date()) async -> LiveData {
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd"
        var d = LiveData()
        d.folder = Paths.lectures.appendingPathComponent("live/\(f.string(from: day))")
        let dec = JSONDecoder()
        func json<T: Decodable>(_ name: String, _ type: T.Type) -> T? {
            guard let data = try? Data(contentsOf: d.folder.appendingPathComponent(name)) else { return nil }
            return try? dec.decode(T.self, from: data)
        }
        func lines<T: Decodable>(_ name: String, _ type: T.Type) -> [T] {
            guard let s = try? String(contentsOf: d.folder.appendingPathComponent(name), encoding: .utf8) else { return [] }
            return s.split(separator: "\n").compactMap { try? dec.decode(T.self, from: Data($0.utf8)) }
        }
        d.state = json("state.json", LiveState.self)
        d.threads = json("qa_threads.json", [QAThread].self) ?? []
        if let v = json("qa_view.json", QAViewFile.self) {
            let keep = Set(v.threads)
            d.threads = d.threads.filter { keep.contains($0.id) }
            d.followups = v.followups
        } else {
            for f in lines("qa_followups.jsonl", Drafted.self) { d.followups[f.thread ?? "", default: []].append(f) }
        }
        d.questions = lines("questions.jsonl", Drafted.self)
        d.outline = lines("outline.jsonl", OutlinePart.self)
        d.slides = lines("slides.jsonl", SlideEntry.self)
        for e in json("evidence.json", [Evidence].self) ?? [] { d.evidence[e.id] = e.title }
        d.askNow = json("ask_now.json", AskNowFile.self)?.top ?? []
        return d
    }
}

extension ISO8601DateFormatter {
    /// live_class.py writes Python isoformat: with or without fractional seconds.
    static let flexible: ISO8601DateFormatterBox = ISO8601DateFormatterBox()
}

/// Tries both ISO 8601 shapes Python produces.
final class ISO8601DateFormatterBox: @unchecked Sendable {
    private let plain = ISO8601DateFormatter()
    private let frac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f
    }()
    func date(from s: String) -> Date? { plain.date(from: s) ?? frac.date(from: s) }
}
