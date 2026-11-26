import SwiftUI

// Mastery check (`lecture mastery-check …`, mastery_check.py): an untimed, cumulative review of every lecture so far.
// Pass at 90%, retry as often as you like; a retry starts with what you missed (options reshuffled).
// Every answer comes back with an explanation and the lecture evidence; a calibration line compares how
// sure you were with how you did. Questions come from your own notes and cards, never a graded quiz.

struct MasteryStatus: Decodable, Sendable, Equatable {
    let ok: Bool
    let state: String
    let message: String
    let bar: Int
    let lecture_count: Int
    let attempts: Int
    let last_score: Int?
    let new_lectures: [String]
    let due_at: String?
    let error: String?

    enum CodingKeys: String, CodingKey { case ok, state, message, bar, lecture_count, attempts, last_score, new_lectures, due_at, error }

    /// A retry waits for its spacing (successive relearning); Today stays quiet until then.
    func waiting(now: Date = .now) -> Bool {
        guard state == "retry", let d = due_at else { return false }
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd'T'HH:mmXXXXX"
        guard let t = f.date(from: d) else { return false }
        return t > now
    }

    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? ""
        message = try c.decodeIfPresent(String.self, forKey: .message) ?? ""
        bar = try c.decodeIfPresent(Int.self, forKey: .bar) ?? 90
        lecture_count = try c.decodeIfPresent(Int.self, forKey: .lecture_count) ?? 0
        attempts = try c.decodeIfPresent(Int.self, forKey: .attempts) ?? 0
        last_score = try c.decodeIfPresent(Int.self, forKey: .last_score)
        new_lectures = try c.decodeIfPresent([String].self, forKey: .new_lectures) ?? []
        due_at = try c.decodeIfPresent(String.self, forKey: .due_at)
        error = try c.decodeIfPresent(String.self, forKey: .error)
    }
}

struct MasteryStart: Decodable, Sendable {
    let ok: Bool
    let check_id: String
    let attempt: Int
    let bar: Int
    let retry: Bool
    let retry_count: Int
    let lecture_count: Int
    let items: [MockExamItem]
    let retried: Set<String>
    let error: String?

    private struct Flag: Decodable { let item_id: String?; let retry: Bool? }
    enum CodingKeys: String, CodingKey { case ok, check_id, attempt, bar, retry, retry_count, lecture_count, items, error }

    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        check_id = try c.decodeIfPresent(String.self, forKey: .check_id) ?? ""
        attempt = try c.decodeIfPresent(Int.self, forKey: .attempt) ?? 1
        bar = try c.decodeIfPresent(Int.self, forKey: .bar) ?? 90
        retry = try c.decodeIfPresent(Bool.self, forKey: .retry) ?? false
        retry_count = try c.decodeIfPresent(Int.self, forKey: .retry_count) ?? 0
        lecture_count = try c.decodeIfPresent(Int.self, forKey: .lecture_count) ?? 0
        items = try c.decodeIfPresent([MockExamItem].self, forKey: .items) ?? []
        let flags = try c.decodeIfPresent([Flag].self, forKey: .items) ?? []
        retried = Set(flags.compactMap { $0.retry == true ? $0.item_id : nil })
        error = try c.decodeIfPresent(String.self, forKey: .error)
    }
}

struct MasteryResult: Decodable, Sendable {
    struct Calibration: Decodable, Sendable { let line: String?; let verdict: String?; let overconfident: Int? }
    struct LectureRow: Decodable, Sendable, Identifiable {
        let lecture_id: String; let title: String; let asked: Int; let right: Int; let pct: Int
        var id: String { lecture_id }
    }
    let ok: Bool
    let score_pct: Int
    let correct_count: Int
    let item_count: Int
    let bar: Int
    let passed: Bool
    let next: String
    let calibration: Calibration?
    let by_lecture: [LectureRow]
    let graded_items: [MockExamGradedItem]
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok, score_pct, correct_count, item_count, bar, passed, next, calibration, by_lecture, graded_items, error
    }

    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        score_pct = try c.decodeIfPresent(Int.self, forKey: .score_pct) ?? 0
        correct_count = try c.decodeIfPresent(Int.self, forKey: .correct_count) ?? 0
        item_count = try c.decodeIfPresent(Int.self, forKey: .item_count) ?? 0
        bar = try c.decodeIfPresent(Int.self, forKey: .bar) ?? 90
        passed = try c.decodeIfPresent(Bool.self, forKey: .passed) ?? false
        next = try c.decodeIfPresent(String.self, forKey: .next) ?? ""
        calibration = try c.decodeIfPresent(Calibration.self, forKey: .calibration)
        by_lecture = try c.decodeIfPresent([LectureRow].self, forKey: .by_lecture) ?? []
        graded_items = try c.decodeIfPresent([MockExamGradedItem].self, forKey: .graded_items) ?? []
        error = try c.decodeIfPresent(String.self, forKey: .error)
    }
}

/// One answer as `lecture mastery-check submit` reads it (with the optional one-line why).
struct MasteryAnswer: Codable, Sendable, Equatable {
    let item_id: String
    let answer: String
    let choice_index: Int?
    let confidence: String
    let why: String

    static func encode(_ rows: [MasteryAnswer]) -> String {
        let e = JSONEncoder(); e.outputFormatting = [.sortedKeys]
        return (try? e.encode(rows)).flatMap { String(data: $0, encoding: .utf8) } ?? "[]"
    }
}

enum MasteryText {
    static func whyPrompt(_ c: MockExamConfidence) -> String {
        c == .sure ? "You're sure: in one line, why? (optional, it helps it stick)"
                   : "Why this answer? (optional, one line)"
    }

    static func startTitle(_ state: String?) -> String {
        switch state {
        case "retry": return "Try again"
        case "passed", "due_new": return "Take it again"
        default: return "Start mastery check"
        }
    }

    static func resultTitle(score: Int, bar: Int, passed: Bool) -> String {
        passed ? "Passed: \(score)% (pass mark \(bar)%)" : "Not yet: \(score)%. Pass mark \(bar)%"
    }

    static func progress(answered: Int, total: Int) -> String {
        answered == total ? "All \(total) answered" : "\(answered) of \(total) answered"
    }

    static func submitArgs(checkId: String, answers: String, elapsed: Int) -> [String] {
        ["mastery-check", "submit", "--check-id", checkId, "--answers-json", answers, "--elapsed-seconds", "\(max(0, elapsed))", "--json"]
    }

    /// Short label for the Today card.
    static func todayBadge(_ s: MasteryStatus) -> String {
        switch s.state {
        case "passed": return "Passed"
        case "retry": return s.last_score.map { "Last \($0)%" } ?? "Try again"
        case "due_new": return "\(s.new_lectures.count) new lecture(s)"
        case "not_taken": return "Not taken"
        default: return ""
        }
    }
}

struct MasteryCheckView: View {
    @Bindable var model: AppModel
    @State private var status: MasteryStatus?
    @State private var check: MasteryStart?
    @State private var index = 0
    @State private var choices: [String: Int] = [:]
    @State private var texts: [String: String] = [:]
    @State private var sure: [String: MockExamConfidence] = [:]
    @State private var whys: [String: String] = [:]
    @State private var lastWhys: [String: String] = [:]
    @FocusState private var whyFocused: Bool
    @State private var started: Date?
    @State private var result: MasteryResult?
    @State private var lastCheck: MasteryStart?
    @State private var relearning: RelearnQueue?
    @State private var relearnFromPlan = false
    @State private var plan: RelearnPlan?
    @State private var relearnNote: String?
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("An untimed review of every lecture so far, mixed together. Pass at \(status?.bar ?? 90)% and retry as often as you like: each try starts with what you missed, and every answer comes with an explanation. Keys: 1–4 choose, ⌘] next, ⌘[ previous, ⌘↩ check.")
                .font(.scaled(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let e = error {
                Text(e).font(.scaled(.caption)).foregroundStyle(.red)
            }
            if let c = check, result == nil, !c.items.isEmpty {
                runner(c)
            } else if relearning != nil {
                RelearnPanel(queue: Binding(get: { relearning ?? RelearnQueue(items: []) }, set: { relearning = $0 }),
                             explanations: relearnFromPlan
                                ? Dictionary((plan?.items ?? []).map { ($0.item_id, "Expected answer: \($0.expected_answer)") }, uniquingKeysWith: { a, _ in a })
                                : Dictionary((result?.graded_items ?? []).map { ($0.item_id, $0.explanation) }, uniquingKeysWith: { a, _ in a }),
                             checkId: relearnFromPlan ? nil : lastCheck?.check_id) { note in
                    relearning = nil; relearnFromPlan = false; relearnNote = note; Task { await loadStatus() }
                }
            } else {
                statusBar
                if let n = relearnNote {
                    Label(n, systemImage: "calendar.badge.clock").font(.scaled(.callout)).fixedSize(horizontal: false, vertical: true)
                }
                if let r = result { resultPanel(r) }
            }
        }
        .padding(.top, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: model.course?.id) { await loadStatus() }
    }

    private var statusBar: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(status?.message ?? "Checking your mastery status…").font(.scaled(.callout))
                    .fixedSize(horizontal: false, vertical: true)
                if let s = status, !s.new_lectures.isEmpty, s.state == "due_new" {
                    Text("New: " + s.new_lectures.joined(separator: ", ")).font(.scaled(.caption)).foregroundStyle(.secondary)
                }
                if let p = plan, p.ok, (p.counts?.items ?? 0) > 0 {
                    HStack(spacing: 8) {
                        Label(p.message, systemImage: "arrow.triangle.2.circlepath").font(.scaled(.caption)).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        if !p.items.isEmpty {
                            Button("Relearn \(p.items.count) now") { relearnFromPlan = true; relearning = RelearnQueue(items: p.items) }
                                .controlSize(.small)
                                .help("Each item until it is right once; spaced sessions make it stick. Fits in what is left of today's review time.")
                        }
                    }
                }
            }
            Spacer()
            Button { Task { await start() } } label: {
                if busy { ProgressView().controlSize(.small) } else { Label(MasteryText.startTitle(status?.state), systemImage: "checkmark.seal") }
            }
            .buttonStyle(.borderedProminent).tint(Phosphor.fill)
            .disabled(busy || status?.state == "no_lectures")
            .accessibilityLabel(MasteryText.startTitle(status?.state))
        }
        .padding(10)
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary, lineWidth: 1))
    }

    private var answered: Int {
        guard let c = check else { return 0 }
        return c.items.filter { choices[$0.id] != nil || !(texts[$0.id] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }.count
    }

    @ViewBuilder
    private func runner(_ c: MasteryStart) -> some View {
        let i = min(max(index, 0), c.items.count - 1)
        let item = c.items[i]
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Text("Try \(c.attempt) · question \(i + 1) of \(c.items.count)")
                    .font(.scaled(.caption, weight: .semibold)).foregroundStyle(Phosphor.accent)
                if c.retried.contains(item.item_id) {
                    Text("You missed this last time").font(.scaled(.caption2, weight: .semibold))
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Color.orange.opacity(0.6)))
                        .foregroundStyle(.orange)
                }
                Spacer()
                Text(MasteryText.progress(answered: answered, total: c.items.count))
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
            }
            ProgressView(value: Double(i + 1), total: Double(c.items.count))
                .accessibilityLabel("Question \(i + 1) of \(c.items.count)")
            Text([item.lecture_title, item.concept].filter { !$0.isEmpty }.joined(separator: " · "))
                .font(.scaled(.caption2)).foregroundStyle(.secondary)
            Text(item.question).font(.scaled(.body, weight: .medium)).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if item.kind.lowercased() == "mcq", !item.choices.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(item.choices.enumerated()), id: \.offset) { n, choice in
                        let on = choices[item.id] == n
                        Button { choices[item.id] = n } label: {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: on ? "largecircle.fill.circle" : "circle")
                                    .foregroundStyle(on ? Phosphor.accent : .secondary)
                                Text("\(n + 1)").font(.scaled(.caption).monospacedDigit()).foregroundStyle(.secondary)
                                    .accessibilityHidden(true)
                                Text(choice).font(.scaled(.callout)).multilineTextAlignment(.leading)
                                Spacer()
                            }
                            .padding(8).contentShape(Rectangle())
                            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(on ? Phosphor.accent : Color.secondary.opacity(0.25)))
                        }
                        .buttonStyle(.plain)
                        .keyboardShortcut(whyFocused ? nil : KeyboardShortcut(KeyEquivalent(Character("\(min(n + 1, 9))")), modifiers: []))
                        .help("Press \(n + 1)")
                        .accessibilityLabel("Choice \(n + 1): \(choice)")
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
            } else {
                TextField("Your answer, in your own words", text: Binding(get: { texts[item.id] ?? "" }, set: { texts[item.id] = $0 }), axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...6)
            }
            HStack(spacing: 10) {
                Text("How sure are you?").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
                Picker("How sure are you?", selection: Binding(get: { sure[item.id] ?? .unsure }, set: { sure[item.id] = $0 })) {
                    ForEach(MockExamConfidence.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 240)
                Spacer()
            }
            // open self-explanation helps transfer, but costs time: optional, one line
            TextField(MasteryText.whyPrompt(sure[item.id] ?? .unsure),
                      text: Binding(get: { whys[item.id] ?? "" }, set: { whys[item.id] = $0 }))
                .textFieldStyle(.roundedBorder)
                .focused($whyFocused)
                .accessibilityLabel("Why this answer (optional)")
            Divider()
            HStack(spacing: 10) {
                Button("Previous", systemImage: "chevron.left") { index = max(0, i - 1) }
                    .disabled(i == 0).keyboardShortcut("[", modifiers: .command)
                Button("Next", systemImage: "chevron.right") { index = min(c.items.count - 1, i + 1) }
                    .disabled(i >= c.items.count - 1).keyboardShortcut("]", modifiers: .command)
                Spacer()
                Button("Stop", role: .cancel) { check = nil; Task { await loadStatus() } }
                    .help("Leave this try without grading it")
                Button { Task { await submit(c) } } label: {
                    if busy { ProgressView().controlSize(.small) } else { Label("Check my answers", systemImage: "checkmark.circle") }
                }
                .buttonStyle(.borderedProminent).tint(Phosphor.fill)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(busy || answered == 0)
                .help(answered < c.items.count ? "Unanswered questions count as missed" : "Grade this try")
            }
        }
        .padding(12)
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.35)))
    }

    @ViewBuilder
    private func resultPanel(_ r: MasteryResult) -> some View {
        let accent = r.passed ? Phosphor.accent : Color.orange
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: r.passed ? "checkmark.seal.fill" : "arrow.clockwise.circle").foregroundStyle(accent)
                    .accessibilityHidden(true)
                Text(MasteryText.resultTitle(score: r.score_pct, bar: r.bar, passed: r.passed))
                    .font(.scaled(.headline)).foregroundStyle(accent)
                Text("\(r.correct_count) of \(r.item_count) right").font(.scaled(.caption)).foregroundStyle(.secondary)
            }
            Text(r.next).font(.scaled(.callout)).fixedSize(horizontal: false, vertical: true)
            if !r.passed, let c = lastCheck, relearnNote == nil {
                let missed = Set(r.graded_items.filter { !$0.correct }.map(\.item_id))
                let items = c.items.filter { missed.contains($0.item_id) && !$0.choices.isEmpty }
                if !items.isEmpty {
                    Button("Relearn the \(items.count) missed now", systemImage: "arrow.triangle.2.circlepath") {
                        relearning = RelearnQueue(items: items)
                    }
                    .buttonStyle(.borderedProminent).tint(Phosphor.fill)
                    .help("Answer each missed question until you get it right once; then the next try is due in two days")
                }
            }
            if let cal = r.calibration, let line = cal.line, !line.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    Text("How sure you were: \(line)").font(.scaled(.caption, weight: .semibold))
                    if let v = cal.verdict, !v.isEmpty { Text(v).font(.scaled(.caption)).foregroundStyle(.secondary) }
                }
                .accessibilityElement(children: .combine)
            }
            if r.by_lecture.count > 1 {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(r.by_lecture) { row in
                        HStack {
                            Text(row.title).font(.scaled(.caption)).lineLimit(1).truncationMode(.middle)
                            Spacer()
                            Text("\(row.right)/\(row.asked)").font(.scaled(.caption).monospacedDigit())
                                .foregroundStyle(row.pct >= r.bar ? Phosphor.accent : Color.orange)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
                .padding(8).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
            }
            Divider()
            ForEach(r.graded_items.sorted { !$0.correct && $1.correct }) { g in explained(g, checkId: lastCheck?.check_id) }
        }
        .padding(12)
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(accent.opacity(0.5)))
    }

    private func explained(_ g: MockExamGradedItem, checkId: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: g.correct ? "checkmark.circle" : "xmark.circle")
                    .foregroundStyle(g.correct ? Phosphor.accent : Color.orange).accessibilityHidden(true)
                Text(g.correct ? "Right" : "Missed").font(.scaled(.caption2, weight: .semibold))
                Text("· you said \(g.confidence)").font(.scaled(.caption2)).foregroundStyle(.secondary)
            }
            Text(g.question).font(.scaled(.callout, weight: .medium)).fixedSize(horizontal: false, vertical: true)
            if !g.user_answer.isEmpty {
                Text("Your answer: \(g.user_answer)").font(.scaled(.caption)).foregroundStyle(.secondary)
            }
            if let w = lastWhys[g.item_id], !w.isEmpty {
                Text("Your reasoning: \(w)").font(.scaled(.caption)).italic().foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if g.correct || checkId == nil {
                Text(g.explanation).font(.scaled(.caption)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            } else {
                // generate first, then feedback: work it out with the tutor, or open the explanation
                HStack(spacing: 8) {
                    Button("Work it out with the tutor", systemImage: "graduationcap") {
                        model.tutorOnItem("mastery:\(checkId ?? ""):\(g.item_id)")
                    }
                    .controlSize(.small)
                    .help("The tutor keeps the answer back while you reason it out, then asks one question without help")
                    DisclosureGroup("Explanation") {
                        Text(g.explanation).font(.scaled(.caption)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                    .font(.scaled(.caption))
                }
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder((g.correct ? Phosphor.accent : Color.orange).opacity(0.35)))
        .accessibilityElement(children: .contain)
    }

    private func loadStatus() async {
        do {
            let s = try await ToolRunner.json(MasteryStatus.self, "lecture", ["mastery-check", "status", "--json"], timeout: 30)
            status = s
            plan = try? await ToolRunner.json(RelearnPlan.self, "lecture", ["mastery-check", "relearn-plan", "--json"], timeout: 60)
            error = s.ok ? nil : (s.error ?? "Couldn't read the mastery status.")
        } catch { self.error = error.localizedDescription }
    }

    private func start() async {
        busy = true; defer { busy = false }
        error = nil
        do {
            let c = try await ToolRunner.json(MasteryStart.self, "lecture", ["mastery-check", "start", "--json"], timeout: 120)
            guard c.ok, !c.items.isEmpty else { error = c.error ?? "No questions could be built."; return }
            check = c; result = nil; index = 0; choices = [:]; texts = [:]; sure = [:]; whys = [:]; started = Date()
        } catch { self.error = error.localizedDescription }
    }

    private func submit(_ c: MasteryStart) async {
        busy = true; defer { busy = false }
        let payload = c.items.map { it in
            MasteryAnswer(item_id: it.item_id,
                          answer: choices[it.id].map { it.choices.indices.contains($0) ? it.choices[$0] : "" } ?? (texts[it.id] ?? ""),
                          choice_index: choices[it.id],
                          confidence: (sure[it.id] ?? .unsure).rawValue,
                          why: (whys[it.id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines))
        }
        let elapsed = Int(Date().timeIntervalSince(started ?? Date()))
        do {
            let r = try await ToolRunner.json(MasteryResult.self, "lecture",
                                              MasteryText.submitArgs(checkId: c.check_id, answers: MasteryAnswer.encode(payload), elapsed: elapsed),
                                              timeout: 180)
            guard r.ok else { error = r.error ?? "The answers could not be checked."; return }
            result = r; lastCheck = c; relearnNote = nil; check = nil; lastWhys = whys
            await loadStatus()
        } catch { self.error = error.localizedDescription }
    }
}

/// Retrieval to criterion: a missed question goes to the back of the line until it is answered
/// right once (successive relearning, Rawson and Dunlosky 2022).
struct RelearnQueue: Equatable {
    private(set) var queue: [MockExamItem]
    private(set) var all: [MockExamItem]
    private(set) var firstTry: [String: Bool] = [:]
    private(set) var total: Int
    private(set) var answers = 0
    private(set) var wrong = 0
    init(items: [MockExamItem]) { queue = items; all = items; total = items.count }
    var current: MockExamItem? { queue.first }
    var done: Bool { queue.isEmpty }
    var learned: Int { total - queue.count }
    /// Rounds through the list: 1 when every question was right the first time.
    var rounds: Int { total == 0 ? 1 : 1 + (wrong + total - 1) / total }
    @discardableResult mutating func answer(_ choice: Int) -> Bool {
        guard let it = queue.first else { return false }
        answers += 1
        let right = it.correct_index == choice
        if firstTry[it.item_id] == nil { firstTry[it.item_id] = right }
        queue.removeFirst()
        if !right { wrong += 1; queue.append(it) }
        return right
    }
}

struct RelearnPanel: View {
    @Binding var queue: RelearnQueue
    let explanations: [String: String]
    /// The mastery try whose misses these are; nil for a relearning session from the plan.
    let checkId: String?
    let onDone: (String) -> Void
    @State private var startedAt = Date()
    @State private var picked: Int?
    @State private var lastRight: Bool?
    @State private var shown: MockExamItem?
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Relearning: \(queue.learned) of \(queue.total) right once")
                    .font(.scaled(.caption, weight: .semibold))
                Spacer()
                Text("A miss comes back at the end").font(.scaled(.caption)).foregroundStyle(.secondary)
            }
            ProgressView(value: Double(queue.learned), total: Double(max(1, queue.total)))
                .accessibilityLabel("\(queue.learned) of \(queue.total) relearned")
            if let it = shown ?? queue.current {
                Text(it.question).font(.scaled(.body, weight: .medium)).fixedSize(horizontal: false, vertical: true)
                ForEach(Array(it.choices.enumerated()), id: \.offset) { n, choice in
                    Button { choose(n, on: it) } label: {
                        HStack(alignment: .top, spacing: 8) {
                            Text("\(n + 1)").font(.scaled(.caption).monospacedDigit()).foregroundStyle(.secondary)
                            Text(choice).font(.scaled(.callout)).multilineTextAlignment(.leading)
                            Spacer()
                            if shown != nil, n == it.correct_index {
                                Image(systemName: "checkmark.circle.fill").foregroundStyle(Phosphor.accent).accessibilityLabel("Right answer")
                            } else if shown != nil, n == picked {
                                Image(systemName: "xmark.circle").foregroundStyle(.orange).accessibilityLabel("Your answer")
                            }
                        }
                        .padding(8).contentShape(Rectangle())
                        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.25)))
                    }
                    .buttonStyle(.plain)
                    .disabled(shown != nil)
                    .keyboardShortcut(KeyEquivalent(Character("\(min(n + 1, 9))")), modifiers: [])
                    .accessibilityLabel("Choice \(n + 1): \(choice)")
                }
                if let right = lastRight, shown != nil {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(right ? "Right." : "Not yet: it comes back at the end.").font(.scaled(.callout, weight: .semibold))
                        if !right, let e = explanations[it.item_id], !e.isEmpty {
                            Text(e).font(.scaled(.caption)).fixedSize(horizontal: false, vertical: true)
                        }
                        Button(queue.done ? "Finish" : "Next", systemImage: "arrow.right") { next() }
                            .keyboardShortcut(.return, modifiers: [])
                            .disabled(saving)
                    }
                    .accessibilityElement(children: .contain)
                }
            }
        }
        .padding(12)
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Phosphor.accent.opacity(0.5)))
    }

    private func choose(_ n: Int, on it: MockExamItem) {
        picked = n
        shown = it
        lastRight = queue.answer(n)
    }

    private func next() {
        shown = nil; picked = nil; lastRight = nil
        guard queue.done else { return }
        saving = true
        Task {
            let args: [String]
            if let checkId {
                args = ["mastery-check", "relearned", "--check-id", checkId, "--items", "\(queue.total)", "--rounds", "\(queue.rounds)", "--json"]
            } else {
                args = ["mastery-check", "relearn-record", "--results-json", RelearnText.results(queue),
                        "--elapsed-seconds", "\(Int(Date().timeIntervalSince(startedAt)))", "--json"]
            }
            let r = try? await ToolRunner.json(RelearnResult.self, "lecture", args, timeout: 30)
            saving = false
            onDone(r?.message ?? "Relearned all \(queue.total). Try the mastery check again in two days.")
        }
    }
}

enum RelearnText {
    /// `relearn-record` input: each item with whether it was right the first time.
    static func results(_ q: RelearnQueue) -> String {
        let rows: [[String: String]] = q.all.map {
            ["lecture_id": $0.lecture_id, "question": $0.question, "first_try_ok": (q.firstTry[$0.item_id] ?? false) ? "1" : ""]
        }
        let data = (try? JSONSerialization.data(withJSONObject: rows.map { r -> [String: Any] in
            ["lecture_id": r["lecture_id"] ?? "", "question": r["question"] ?? "", "first_try_ok": !(r["first_try_ok"] ?? "").isEmpty]
        }, options: [.sortedKeys])) ?? Data("[]".utf8)
        return String(decoding: data, as: UTF8.self)
    }
}

/// `lecture mastery-check relearn-plan --json` (relearning.py).
struct RelearnPlan: Decodable, Sendable {
    struct Counts: Decodable, Sendable { let items: Int; let secure: Int; let due: Int; let in_progress: Int }
    let ok: Bool
    let message: String
    let counts: Counts?
    let budget_minutes: Double?
    let items: [MockExamItem]
}

struct RelearnResult: Decodable, Sendable {
    let ok: Bool
    let message: String?
    let next_try: String?
}
