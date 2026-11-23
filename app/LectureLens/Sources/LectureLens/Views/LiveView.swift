import AppKit
import SwiftUI

/// Live class view: what live_class.py has written for today in lectures/live/<yyyyMMdd>/:
/// Zoom Q&A threads with drafted follow-ups, questions to ask, the running outline and
/// the slide log. Read-only; refreshed every few seconds. Copy a question, then paste it
/// into Zoom yourself.
struct LiveView: View {
    @Bindable var model: AppModel
    @State private var live = LiveData()
    @State private var copied: String?
    @State private var qaFilter: QAFilter = .all
    @State private var showPruned = false
    private func visible(_ q: String) -> Bool { showPruned || !live.pruned.contains(q) }
    @State private var qaSearch = ""
    @State private var syncing: String?
    @State private var syncNote: [String: String] = [:]
    /// A past class to review (yyyyMMdd); nil follows today's live folder.
    @State private var pastDay: String?

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

    /// The class the prequestions are for: a day picked in the toolbar, else today when today
    /// already has prequestions, else the next scheduled class within a week (so they can be
    /// made before class), else today.
    private var prequestionDay: String {
        if let pastDay, !pastDay.isEmpty { return pastDay }
        return LiveData.prequestionDay(nextClassDate: model.nextClass?.date)
    }

    private var prequestionCaption: String {
        let title = model.nextClass.flatMap { c in c.date.replacingOccurrences(of: "-", with: "") == prequestionDay ? c.title : nil }
        return "For \(LiveData.dayLabel(prequestionDay))" + (title.map { " · \($0)" } ?? "")
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let c = model.course, c.selfPaced {
                    Label("Live is for live classes (Zoom or Meet). \(c.label) is self-paced: watch a lesson with capture on, then build it in Lectures.",
                          systemImage: "info.circle")
                        .font(.scaled(.callout)).foregroundStyle(.secondary)
                        .padding(10)
                        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                }
                statusBox
                PrequestionsBox(day: prequestionDay, caption: prequestionCaption)
                askNowBox
                curatedBox
                qaBox
                questionsBox
                outlineBox
                slidesBox
            }
            .padding(24)
            .frame(maxWidth: 880, alignment: .leading)      // a readable line length, like a notes app
            .frame(maxWidth: .infinity)
        }
        .navigationTitle(pastDay.map { "Live class · \(LiveData.dayLabel($0))" } ?? "Live class")
        .toolbar {
            Picker("Day", selection: $pastDay) {
                Text("Today").tag(String?.none)
                ForEach(LiveData.pastDays(), id: \.self) { d in Text(LiveData.dayLabel(d)).tag(Optional(d)) }
            }
            .help("Review a past class: its curated questions, Q&A and citations")
        }
        .task(id: pastDay) {
            while !Task.isCancelled {
                live = await LiveData.load(day: pastDay.flatMap(LiveData.date) ?? Date())
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
                if let engine = live.studioEngine {
                    Label("Studio drafting with \(engine)", systemImage: "globe").foregroundStyle(.secondary)
                        .help("Lecture Lens Studio in Chrome writes the outline, questions and Q&A drafts (browser mode)")
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
                            CitationButton(title: c.q, t: c.t, text: c.q + " " + c.answer, sources: c.sources, day: live.day, answer: c.answer)
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

    // MARK: curated top questions
    private var curatedBox: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                let rounds = CuratedQuestions.rounds(live.questions)
                if rounds.isEmpty {
                    Text("Curate a round to get the top questions for what was taught since the last one, grounded in the evidence bank.")
                        .foregroundStyle(.secondary)
                }
                ForEach(rounds, id: \.round) { r in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Round \(r.round) · \(LiveData.clock(r.rows.first?.t ?? ""))").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
                        ForEach(r.rows) { c in curatedRow(c) }
                    }
                    Divider()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            HStack(spacing: 8) {
                Text("Top questions (curated rounds)")
                Spacer()
                if let n = model.curateNote { Text(n).font(.scaled(.caption)).foregroundStyle(.secondary) }
                if model.curateRunning { ProgressView().controlSize(.small) }
                Toggle("Every 25 min", isOn: $model.autoCurate).toggleStyle(.checkbox).controlSize(.small)
                    .help("While the follower is running, curate a round every 25 minutes (uses the local model)")
                Button("Curate now", systemImage: "sparkles") { model.curateNow() }.disabled(pastDay != nil)
                    .controlSize(.small).disabled(model.curateRunning || live.state == nil)
            }
        }
    }

    private func curatedRow(_ c: Drafted) -> some View {
        let asked = CuratedQuestions.askedIn(c.q, threads: live.threads)
        return VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .top) {
                if let a = asked {
                    Label("Asked \(a.time)", systemImage: "checkmark.circle.fill").labelStyle(.titleAndIcon)
                        .font(.scaled(.caption2, weight: .semibold)).foregroundStyle(.green)
                        .help(a.answers.isEmpty ? "Posted in the Q&A; no reply yet" : "Posted in the Q&A; \(a.answers.count) repl\(a.answers.count == 1 ? "y" : "ies")")
                }
                Text(CuratedQuestions.prefix(c)).font(.scaled(.callout, weight: .semibold))
                    + Text(c.q).font(.scaled(.callout))
                Spacer()
                CitationButton(title: c.q, t: c.t, text: c.q + " " + c.answer, sources: c.sources, day: live.day, answer: c.answer)
                copyButton(c.q)
            }
            if !c.answer.isEmpty {
                Text((c.pile == "self_check" ? "In class: " : "Draft: ") + c.answer + live.sourceTitles(c.sources))
                    .font(.scaled(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
        .opacity(c.part == "later" || c.pile == "self_check" ? 0.75 : 1)
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
                        HStack {
                            Text("\(t.time) · \(t.by)").font(.scaled(.caption, weight: .semibold))
                                .foregroundStyle(t.by == "You" ? Color.accentColor : .secondary)
                            Spacer()
                            CitationButton(title: t.q, t: t.time,
                                           text: ([t.q] + t.answers.map(\.text) + [live.prospective[t.id]?.answer ?? ""]).joined(separator: " "),
                                           day: live.day)
                        }
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
                CitationButton(title: f.q, t: f.t, text: f.q + " " + f.answer, sources: f.sources, day: live.day, answer: f.answer)
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
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                if live.questions.isEmpty { Text("First questions after 10 minutes of class.").foregroundStyle(.secondary) }
                ForEach(live.questions.reversed().filter { visible($0.q) && $0.curated != true }) { q in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(alignment: .top) {
                            Text(LiveData.clock(q.t) + "  ").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
                            + Text("\(q.type): ").font(.scaled(.callout, weight: .semibold)) + Text(q.q).font(.scaled(.callout))
                            Spacer()
                            CitationButton(title: q.q, t: q.t, text: q.q + " " + q.answer, sources: q.sources, day: live.day, answer: q.answer)
                            copyButton(q.q)
                        }
                        Text("Draft: \(q.answer)\(live.sourceTitles(q.sources))").font(.scaled(.caption)).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { syncHeader("Questions to ask (every 10 minutes · \(live.questions.count))", what: "questions") }
    }

    // MARK: on-demand sync (live_class.py picks up sync_request.json within ~2 s)
    private func syncHeader(_ title: String, what: String) -> some View {
        HStack(spacing: 8) {
            Text(title)
            Spacer()
            if let n = syncNote[what] { Text(n).font(.scaled(.caption)).foregroundStyle(.secondary) }
            if syncing == what { ProgressView().controlSize(.small) }
            Button("Sync now", systemImage: "arrow.triangle.2.circlepath") { requestSync(what) }.disabled(pastDay != nil)
                .controlSize(.small)
                .disabled(syncing != nil || !live.isFresh)
                .help(live.isFresh ? "Ask the live follower to update this now" : "The live follower isn't running")
        }
    }

    private func requestSync(_ what: String) {
        let req = live.folder.appendingPathComponent("sync_request.json")
        let done = live.folder.appendingPathComponent("sync_done.json")
        let asked = Date()
        guard (try? JSONSerialization.data(withJSONObject: ["what": [what]]))
                .map({ (try? $0.write(to: req, options: .atomic)) != nil }) == true else {
            syncNote[what] = "couldn't write the request"; return
        }
        syncing = what; syncNote[what] = nil
        Task {
            defer { syncing = nil }
            for _ in 0..<180 {
                try? await Task.sleep(for: .seconds(1))
                guard let data = try? Data(contentsOf: done),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let at = (obj["at"] as? String).flatMap({ ISO8601DateFormatter.flexible.date(from: $0) }),
                      at > asked else { continue }
                live = await LiveData.load()
                if let note = obj["note"] as? String { syncNote[what] = note }
                else if what == "questions" { syncNote[what] = "\((obj["questions"] as? Int) ?? 0) new" }
                else { syncNote[what] = "updated \(LiveData.clock(obj["at"] as? String ?? ""))" }
                return
            }
            syncNote[what] = "no answer yet (follower busy)"
        }
    }

    // MARK: outline and slides
    private var outlineBox: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(live.outline.reversed()) { o in
                    Text(LiveData.clock(o.from) + "–" + LiveData.clock(o.to) + (o.partial == true ? " (so far)" : ""))
                        .font(.scaled(.caption, weight: .semibold))
                    Text(o.bullets).font(.scaled(.callout)).textSelection(.enabled)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { syncHeader("Running outline", what: "outline") }
    }

    private var slidesBox: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(live.slideTitles, id: \.self) { s in Text(s).font(.scaled(.callout)) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { syncHeader("Slides", what: "slides") }
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

struct LiveState: Decodable { var status: String?; var words: Int?; var updated: String?; var ended_at: String? }
struct QAAnswer: Decodable { var by: String; var time: String; var text: String }
struct QAThread: Decodable, Identifiable { var id: String; var by: String; var time: String; var q: String; var answers: [QAAnswer] }
struct Drafted: Decodable, Identifiable {
    var thread: String?; var t: String; var type: String; var q: String; var answer: String; var sources: [String]
    /// Curated top questions (curate.py): round number and part ("now" or "later").
    var curated: Bool?; var round: Int?; var part: String?
    /// Questions v2 (NG-5): "ask" (not answered in class) or "self_check" (answered; quote of the class lines).
    var pile: String?; var requested_level: String?
    var id: String { t + q }
}
struct OutlinePart: Decodable, Identifiable { var from: String; var to: String; var bullets: String; var partial: Bool?; var id: String { from + to } }
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
    var studioEngine: String?

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

    /// The live folder's day (yyyymmdd), for `lecture cite --day`.
    var day: String { folder.lastPathComponent }

    static func todayKey(_ d: Date = Date()) -> String { let f = DateFormatter(); f.dateFormat = "yyyyMMdd"; return f.string(from: d) }

    /// The day prequestions are made for (yyyyMMdd): today when today already has them,
    /// else the next class from the calendar ("yyyy-MM-dd") when it is within `withinDays`,
    /// else today.
    static func prequestionDay(nextClassDate: String?, today: Date = Date(), withinDays: Int = 7,
                               root: URL = Paths.lectures.appendingPathComponent("live")) -> String {
        let t = todayKey(today)
        if FileManager.default.fileExists(atPath: root.appendingPathComponent("\(t)/prequestions.json").path) { return t }
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX")
        guard let s = nextClassDate, let d = f.date(from: s) else { return t }
        let cal = Calendar.current
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: today), to: cal.startOfDay(for: d)).day ?? Int.max
        return (0...withinDays).contains(days) ? todayKey(d) : t
    }

    /// Past live-class folders (yyyyMMdd), newest first, today excluded.
    static func pastDays(root: URL = Paths.lectures.appendingPathComponent("live"), today: Date = Date()) -> [String] {
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd"
        let t = f.string(from: today)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: root.path)) ?? []
        return names.filter { $0.count == 8 && $0.allSatisfy(\.isNumber) && $0 != t }.sorted(by: >)
    }

    static func date(_ day: String) -> Date? {
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd"; f.locale = Locale(identifier: "en_US_POSIX")
        return f.date(from: day)
    }

    static func dayLabel(_ day: String) -> String {
        guard let d = date(day) else { return day }
        let f = DateFormatter(); f.dateFormat = "EEE d MMM"
        return f.string(from: d)
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
    /// Only today's state.json: cheap enough for a timer (auto-curate, class-end notice).
    static func state(day: Date = Date()) -> LiveState? {
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd"
        let url = Paths.lectures.appendingPathComponent("live/\(f.string(from: day))/state.json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(LiveState.self, from: data)
    }

    static func fresh(_ s: LiveState?) -> Bool {
        guard let u = s?.updated, let d = ISO8601DateFormatter.flexible.date(from: u) else { return false }
        return Date().timeIntervalSince(d) < 180 && s?.status != "ended"
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
        d.studioEngine = Studio.liveEngine(folder: d.folder)
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
