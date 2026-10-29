import AppKit
import SwiftUI

/// Live class view: what live_class.py has written for today in lectures/live/<yyyyMMdd>/:
/// Zoom Q&A threads with drafted follow-ups, questions to ask, the running outline and
/// the slide log. Read-only; refreshed every few seconds. Copy a question, then paste it
/// into Zoom yourself.
struct LiveView: View {
    @State private var live = LiveData()
    @State private var copied: String?
    @State private var qaFilter: QAFilter = .all
    @State private var showPruned = false
    private func visible(_ q: String) -> Bool { showPruned || !live.pruned.contains(q) }
    @State private var qaSearch = ""

    enum QAFilter: String, CaseIterable, Identifiable {
        case all = "All", mine = "Mine", unanswered = "Unanswered"
        var id: String { rawValue }
    }

    /// Threads after the filter and search, newest first.
    private var shownThreads: [QAThread] {
        let q = qaSearch.trimmingCharacters(in: .whitespaces).lowercased()
        return live.threads.reversed().filter { t in
            switch qaFilter {
            case .all: true
            case .mine: t.by == "You"
            case .unanswered: t.answers.isEmpty
            }
        }.filter { t in
            q.isEmpty || t.q.lowercased().contains(q) || t.answers.contains { $0.text.lowercased().contains(q) }
        }
    }

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
            .frame(maxWidth: 880, alignment: .leading)      // a readable line length, like a notes app
            .frame(maxWidth: .infinity)
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
                    Text(live.isFresh ? "Following · capture \(s.status ?? "?")"
                         : "Not following · last update \(LiveData.clock(s.updated ?? ""))")
                    Text("· \(s.words ?? 0) words re-transcribed · \(live.slides.count) slides · \(live.threads.count) Q&A threads")
                        .foregroundStyle(.secondary)
                } else {
                    Text("No live class today. Start capture in live mode, then run live_class.py.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !live.pruned.isEmpty {
                    Toggle("Show \(live.pruned.count) pruned", isOn: $showPruned).toggleStyle(.checkbox)
                        .help("Drafts the post-class judge failed on relevance or answerability")
                }
                Button("Open folder") { NSWorkspace.shared.open(live.folder) }.disabled(live.state == nil)
            }
            .font(.scaled(.callout))
        }
    }

    // MARK: ask now
    private var askNowBox: some View {
        GroupBox("Ask now: top 3 for what is being taught") {
            VStack(alignment: .leading, spacing: 10) {
                if live.askNow.isEmpty { Text("Ranked every 5 minutes once questions exist.").foregroundStyle(.secondary) }
                ForEach(Array(live.askNow.filter { visible($0.q) }.enumerated()), id: \.offset) { i, c in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(alignment: .top) {
                            Text("\(i + 1). ").font(.scaled(.callout, weight: .bold)) + Text(c.q).font(.scaled(.callout, weight: .semibold))
                            Spacer()
                            copyButton(c.q)
                        }
                        Text("Draft: \(c.answer)\(live.sourceTitles(c.sources))").font(.scaled(.caption)).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: Q&A
    private var qaBox: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Picker("Show", selection: $qaFilter) {
                        ForEach(QAFilter.allCases) { f in Text(f.rawValue).tag(f) }
                    }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
                    TextField("Search questions and replies", text: $qaSearch)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 280)
                    Spacer()
                    Text("\(shownThreads.count) of \(live.threads.count)").foregroundStyle(.secondary).monospacedDigit()
                }
                if live.threads.isEmpty {
                    Text("No Q&A seen yet. Keep Zoom's Q&A panel open beside the slides.").foregroundStyle(.secondary)
                } else if shownThreads.isEmpty {
                    Text("No threads match.").foregroundStyle(.secondary)
                }
                ForEach(shownThreads) { t in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("\(t.time) · \(t.by)").font(.scaled(.caption, weight: .semibold))
                            .foregroundStyle(t.by == "You" ? Color.accentColor : .secondary)
                        Text(t.q).textSelection(.enabled)
                        ForEach(Array(t.answers.enumerated()), id: \.offset) { _, a in
                            Text("↳ \(a.by): \(a.text)").font(.scaled(.callout)).foregroundStyle(.secondary)
                                .textSelection(.enabled).padding(.leading, 12)
                        }
                        if let p = live.prospective[t.id] { prospectiveRow(p, answered: !t.answers.isEmpty) }
                        ForEach((live.followups[t.id] ?? []).filter { visible($0.q) }) { f in followupRow(f) }
                    }
                    .padding(t.by == "You" ? 10 : 0)
                    .background(t.by == "You" ? Color.accentColor.opacity(0.08) : .clear,
                                in: RoundedRectangle(cornerRadius: 8))
                    Divider()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            Text("Zoom Q&A, newest first")
        }
    }

    /// The answer drafted when the learner posted the question, before staff replied; once
    /// they reply, how much of the reply the draft anticipated (content-word recall).
    private func prospectiveRow(_ p: Prospective, answered: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .top) {
                Text("Prospective answer (\(p.confidence)): ").font(.scaled(.callout, weight: .semibold))
                    + Text(p.answer).font(.scaled(.callout))
                Spacer()
                copyButton(p.answer)
            }
            if let lf = p.listen_for, !lf.isEmpty, !answered {
                Text("Listen for: \(lf)").font(.scaled(.caption)).foregroundStyle(.secondary)
            }
            if let a = p.agreement {
                Text("Anticipated \(Int((a * 100).rounded()))% of the reply's key words")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
            }
        }
        .padding(.leading, 12)
    }

    private func followupRow(_ f: Drafted) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .top) {
                Text("Follow-up (\(f.type)): ").font(.scaled(.callout, weight: .semibold)) + Text(f.q).font(.scaled(.callout))
                Spacer()
                copyButton(f.q)
            }
            Text("Draft: \(f.answer)\(live.sourceTitles(f.sources))").font(.scaled(.caption)).foregroundStyle(.secondary)
                .textSelection(.enabled)
            judged(f.q)
        }
        .padding(.leading, 12)
        .opacity(live.pruned.contains(f.q) ? 0.5 : 1)
    }

    // MARK: questions to ask
    private var questionsBox: some View {
        GroupBox("Questions to ask (every 10 minutes)") {
            VStack(alignment: .leading, spacing: 10) {
                if live.questions.isEmpty { Text("First questions after 10 minutes of class.").foregroundStyle(.secondary) }
                ForEach(live.questions.reversed().filter { visible($0.q) }.prefix(9)) { q in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(alignment: .top) {
                            Text("\(q.type): ").font(.scaled(.callout, weight: .semibold)) + Text(q.q).font(.scaled(.callout))
                            Spacer()
                            copyButton(q.q)
                        }
                        Text("Draft: \(q.answer)\(live.sourceTitles(q.sources))").font(.scaled(.caption)).foregroundStyle(.secondary)
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
                    Text(LiveData.clock(o.from) + "–" + LiveData.clock(o.to)).font(.scaled(.caption, weight: .semibold))
                    Text(o.bullets).font(.scaled(.callout)).textSelection(.enabled)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var slidesBox: some View {
        GroupBox("Slides") {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(live.slideTitles, id: \.self) { s in Text(s).font(.scaled(.callout)) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// Post-class judge marks: pruned (shown only with the toggle) and unsupported drafts.
    @ViewBuilder private func judged(_ q: String) -> some View {
        if live.pruned.contains(q) {
            Label("Pruned: failed relevance or answerability after class", systemImage: "scissors")
                .font(.scaled(.caption)).foregroundStyle(.secondary)
        } else if live.unsupported.contains(q) {
            Label("Draft not supported by the class context", systemImage: "exclamationmark.triangle")
                .font(.scaled(.caption)).foregroundStyle(.orange)
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
struct SlideEntry: Decodable { var t: String; var title: String; var kind: String? }
struct Prospective: Decodable {
    var thread: String; var answer: String; var confidence: String; var listen_for: String?; var agreement: Double?
}
struct Evidence: Decodable { var id: String; var title: String }
struct AskNowFile: Decodable { var top: [Drafted] }
/// Written by live_class.py: non-trivial thread ids and the curated follow-ups (on-topic, no repeats).
struct QAViewFile: Decodable {
    var threads: [String]; var followups: [String: [Drafted]]; var prospective: [String: Prospective]?
    var pruned: [String]?; var unsupported: [String]?
}

struct LiveData {
    var folder = URL(fileURLWithPath: "/")
    var state: LiveState?
    var threads: [QAThread] = []
    var followups: [String: [Drafted]] = [:]
    var prospective: [String: Prospective] = [:]
    var pruned: Set<String> = []
    var unsupported: Set<String> = []
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
        for s in slides where (s.kind ?? "slide") == "slide" && out.last?.hasSuffix(s.title) != true { out.append("\(LiveData.clock(s.t))  \(s.title)") }
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
            d.prospective = v.prospective ?? [:]
            d.pruned = Set(v.pruned ?? [])
            d.unsupported = Set(v.unsupported ?? [])
        } else {
            for f in lines("qa_followups.jsonl", Drafted.self) { d.followups[f.thread ?? "", default: []].append(f) }
        }
        // Drafts written by a replay, or before the follower rendered its view: read them directly.
        if d.prospective.isEmpty {
            for p in lines("qa_prospective.jsonl", Prospective.self) { d.prospective[p.thread] = p }
            struct Row: Decodable { var thread: String; var recall: Double }
            struct Eval: Decodable { var rows: [Row] }
            for r in json("qa_prospective_eval.json", Eval.self)?.rows ?? [] { d.prospective[r.thread]?.agreement = r.recall }
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
