import SwiftUI

// MARK: contracts (`lecture practice ...`, practice.py; `lecture prequestions ...`, prequestions.py)

struct PracticeView: Decodable, Sendable {
    let ok: Bool?; let error: String?; let session_id: String?; let kind: String?; let prompt: String?
    let reveal_enabled: Bool?; let done: Bool?; let index: Int?; let of: Int?; let stem: String?; let options: [String]?
    let removed: Int?; let hint_level: Int?; let answered: Bool?; let closed_book: Bool?; let key: Int?; let correct: Bool?
    let explanation: String?; let at: String?; let hint: String?
    let content: String?; let points: [Point]?; let recalled_share: Double?
    struct Point: Decodable, Sendable, Hashable { let text: String; let missed: Bool }
}

struct PracticeProgress: Decodable, Sendable {
    let ok: Bool; let closed_book_items: Int?; let closed_book_accuracy: Double?; let sure_and_wrong: Int?
    let skip_rate: Double?; let assisted_reveals: Int?
}

enum Practice {
    static let minAttempt = 15
    static func canReveal(_ attempt: String, skip: Bool) -> Bool { skip || attempt.trimmingCharacters(in: .whitespacesAndNewlines).count >= minAttempt }
    static func run(_ args: [String]) async -> PracticeView? {
        try? await ToolRunner.json(PracticeView.self, "lecture", ["practice"] + args + ["--json"], timeout: 60)
    }
    static func progressText(_ p: PracticeProgress) -> String {
        guard let n = p.closed_book_items, n > 0, let a = p.closed_book_accuracy else { return "No closed-book results yet." }
        var s = "Closed-book: \(Int((a * 100).rounded()))% of \(n)"
        if let w = p.sure_and_wrong, w > 0 { s += " · \(w) sure but wrong" }
        if let r = p.skip_rate { s += " · skipped attempts \(Int((r * 100).rounded()))%" }
        return s
    }
}

/// Attempt-first practice (LS-1): recall before the recap, answer and confidence before the key,
/// and closed-book items at the end. Only closed-book results count as progress.
struct PracticeBox: View {
    @Bindable var model: AppModel
    /// Set when the box sits inside one lecture's page; hides the picker.
    var fixedLecture: String? = nil
    /// Start this kind of session as soon as the box appears ("recap" from Recall first).
    var autoStart: String? = nil
    @State private var lecture = ""
    @State private var writing = false
    @State private var view: PracticeView?
    @State private var attempt = ""
    @State private var choice: Int?
    @State private var confidence = ""
    @State private var progress: PracticeProgress?
    @State private var note: String?

    private var lectures: [Session] { model.sessions.filter(\.built) }

    var body: some View {
        GroupBox("Practice (attempt first)") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    if fixedLecture == nil {
                        Picker("Lecture", selection: $lecture) {
                            ForEach(lectures) { s in Text(s.displayTitle).tag(URL(fileURLWithPath: s.folder).lastPathComponent) }
                        }.frame(maxWidth: 380)
                    }
                    Button("Recall, then recap") { start("recap") }
                    Button("Practice quiz") { start("quiz") }
                    Button("Closed-book check") { start("closed") }
                    Button(writing ? "Writing quiz…" : "Write a new quiz") { writeQuiz() }.disabled(writing)
                        .help("Writes linted, cited questions from this lecture with the local model (about a minute).")
                }.disabled(lecture.isEmpty)
                if let p = progress { Text(Practice.progressText(p)).font(.scaled(.caption)).foregroundStyle(.secondary) }
                if let n = note { Text(n).font(.scaled(.caption)).foregroundStyle(.orange) }
                if let v = view, v.kind == "recap" || v.kind == "cheat_sheet" || v.content != nil { recapView(v) }
                else if let v = view, v.stem != nil { quizView(v) }
                else if view?.done == true {
                    Text("Done.").foregroundStyle(.secondary)
                    if view?.kind == "quiz" { Button("Finish with 3 closed-book items") { start("closed") } }
                }
            }
            .padding(6).frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            if let f = fixedLecture { lecture = f }
            if lecture.isEmpty, let s = lectures.first { lecture = URL(fileURLWithPath: s.folder).lastPathComponent }
            await loadProgress()
            if let k = autoStart, view == nil {
                try? await Task.sleep(for: .milliseconds(150))
                if view == nil { start(k) }
            }
        }
    }

    private func start(_ kind: String) {
        Task {
            attempt = ""; choice = nil; confidence = ""; note = nil
            await go(["start", "--lecture", lecture, "--kind", kind])
        }
    }

    /// A refused step (e.g. reveal before an attempt) keeps the current view and shows why.
    private func go(_ args: [String]) async {
        guard let r = await Practice.run(args) else { note = "Practice step failed."; return }
        if r.ok == false { note = r.error ?? "Not allowed yet." } else { view = r; note = nil }
    }

    private func writeQuiz() {
        Task {
            writing = true; defer { writing = false }
            struct Built: Decodable { let shipped: Int?; let items: Int? }
            let r = try? await ToolRunner.json(Built.self, "lecture", ["quiz", lecture, "--json"], timeout: 600)
            note = r.map { "Quiz written: \($0.shipped ?? 0) of \($0.items ?? 0) items passed the checks." } ?? "Could not write a quiz (is the local model running?)."
        }
    }

    private func loadProgress() async {
        progress = try? await ToolRunner.json(PracticeProgress.self, "lecture", ["practice", "progress", "--json"], timeout: 20)
    }

    @ViewBuilder private func recapView(_ v: PracticeView) -> some View {
        if let content = v.content {
            if let r = v.recalled_share { Text("You recalled \(Int((r * 100).rounded()))% of the points; the ones you missed are marked.").font(.scaled(.caption)) }
            ForEach(v.points ?? [], id: \.self) { p in
                Label(p.text, systemImage: p.missed ? "circle" : "checkmark.circle.fill")
                    .foregroundStyle(p.missed ? Color.orange : Color.green).font(.scaled(.callout)).textSelection(.enabled)
            }
            DisclosureGroup("Full text") { MarkdownText(content) }.font(.scaled(.caption))
        } else {
            Text(v.prompt ?? "Write what you remember.").font(.scaled(.callout, weight: .medium))
            TextEditor(text: $attempt).frame(minHeight: 90).font(.scaled(.callout))
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
            HStack {
                Button("Reveal") { reveal(skip: false) }.disabled(!Practice.canReveal(attempt, skip: false))
                Button("Just show me") { reveal(skip: true) }
                Text("Skipping is fine; it is counted.").font(.scaled(.caption2)).foregroundStyle(.tertiary)
            }
        }
    }

    private func reveal(skip: Bool) {
        guard let sid = view?.session_id else { return }
        Task {
            if !attempt.isEmpty { _ = await Practice.run(["attempt", "--session", sid, "--text", attempt]) }
            await go(["reveal", "--session", sid] + (skip ? ["--skip"] : []))
            await loadProgress()
        }
    }

    @ViewBuilder private func quizView(_ v: PracticeView) -> some View {
        let sid = v.session_id ?? ""
        Text("\(v.closed_book == true ? "Closed-book " : "")\((v.index ?? 0) + 1) of \(v.of ?? 0)").font(.scaled(.caption)).foregroundStyle(.secondary)
        Text(v.stem ?? "").font(.scaled(.callout, weight: .medium)).textSelection(.enabled)
        ForEach(Array((v.options ?? []).enumerated()), id: \.offset) { i, o in
            let revealed = v.key != nil
            Button {
                if !revealed { choice = i }
            } label: {
                HStack {
                    Image(systemName: revealed ? (i == v.key ? "checkmark.circle.fill" : (i == choice ? "xmark.circle" : "circle"))
                                               : (choice == i ? "largecircle.fill.circle" : "circle"))
                    Text(o).strikethrough(v.removed == i)
                }
            }
            .buttonStyle(.plain).disabled(v.removed == i)
            .foregroundStyle(revealed && i == v.key ? Color.green : Color.primary)
        }
        if v.key == nil {
            Picker("Confidence", selection: $confidence) {
                Text("–").tag(""); Text("Sure").tag("sure"); Text("Unsure").tag("unsure"); Text("Guess").tag("guess")
            }.pickerStyle(.segmented).frame(width: 320)
            HStack {
                Button("Check answer") {
                    Task { await go(["answer", "--session", sid, "--choice", String(choice ?? -1), "--confidence", confidence]); await loadProgress() }
                }.disabled(choice == nil || confidence.isEmpty)
                if v.closed_book != true {
                    Button("Hint") { Task { await go(["hint", "--session", sid]) } }.disabled((v.hint_level ?? 0) >= 2)
                    Button("Just show me") { Task { await go(["skip", "--session", sid]) } }
                }
            }
            if let h = v.hint { Text(h).font(.scaled(.caption)).foregroundStyle(.secondary) }
        } else {
            Text((v.correct == true ? "Right. " : "Not quite. ") + (v.explanation ?? "") + ((v.at ?? "").isEmpty ? "" : " [\(v.at!)]"))
                .font(.scaled(.callout)).textSelection(.enabled)
            Button("Next") { Task { choice = nil; confidence = ""; await go(["next", "--session", sid]) } }
        }
    }
}

// MARK: prequestions (LS-4)

struct Prequestions: Decodable, Sendable {
    let ok: Bool; let error: String?; let prequestions: [Item]?; let note: String?
    struct Item: Decodable, Sendable, Identifiable {
        let pq_id: String; let agenda_item: String; let text: String; let guess: String?; let late: Bool?
        let resolved: Resolved?; let got_it: Bool?; let became_card: Bool?
        var id: String { pq_id }
    }
    struct Resolved: Decodable, Sendable { let status: String; let quote: String?; let t: String? }
    static func run(_ args: [String]) async -> Prequestions? {
        try? await ToolRunner.json(Prequestions.self, "lecture", ["prequestions"] + args + ["--json"], timeout: 120)
    }
}

/// Before class: guess at three to five prequestions from the agenda. After class: each is
/// resolved against what was said, beside your guess.
struct PrequestionsBox: View {
    let day: String
    var caption: String? = nil
    @State private var state: Prequestions?
    @State private var guesses: [String: String] = [:]
    @State private var busy = false

    var body: some View {
        GroupBox("Prequestions (guess before class)") {
            VStack(alignment: .leading, spacing: 8) {
                let items = state?.prequestions ?? []
                if let caption { Text(caption).font(.scaled(.caption, weight: .medium)).foregroundStyle(.secondary) }
                if items.isEmpty {
                    Text("Write a guess for each before class: guessing first helps the answer stick, even when the guess is wrong.")
                        .font(.scaled(.caption)).foregroundStyle(.secondary)
                    Button(busy ? "Writing…" : "Make prequestions from the agenda") { act(["make", "--day", day]) }.disabled(busy)
                }
                if let err = state?.error {
                    Label(err, systemImage: "exclamationmark.triangle").font(.scaled(.caption)).foregroundStyle(.orange)
                }
                ForEach(items) { p in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(p.text).font(.scaled(.callout, weight: .medium))
                        Text(p.agenda_item).font(.scaled(.caption2)).foregroundStyle(.tertiary)
                        if p.resolved == nil {
                            HStack {
                                TextField("Your guess", text: Binding(get: { guesses[p.pq_id] ?? p.guess ?? "" }, set: { guesses[p.pq_id] = $0 }))
                                Button("Save") { act(["guess", "--day", day, "--id", p.pq_id, "--text", guesses[p.pq_id] ?? ""]) }
                                    .disabled((guesses[p.pq_id] ?? "").isEmpty)
                            }
                            if p.late == true { Text("Saved after class began: not counted as a pretest.").font(.scaled(.caption2)).foregroundStyle(.tertiary) }
                        } else if let r = p.resolved {
                            if let g = p.guess { Text("You guessed: \(g)").font(.scaled(.caption)) }
                            if r.status == "answered" {
                                Text("In class\(r.t.map { " (\(LiveData.clock($0)))" } ?? ""): \(r.quote ?? "")").font(.scaled(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
                                Toggle("My guess was right", isOn: Binding(get: { p.got_it == true },
                                                                             set: { act(["mark", "--day", day, "--id", p.pq_id, "--got-it", $0 ? "y" : "n"]) }))
                                    .font(.scaled(.caption))
                            } else {
                                Text("Not covered in class.").font(.scaled(.caption)).foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
                if !items.isEmpty && items.allSatisfy({ $0.resolved == nil }) && day <= LiveData.todayKey() {   // not before the class day
                    Button("Check against the class now") { act(["resolve", "--day", day]) }.disabled(busy)
                        .font(.scaled(.caption))
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .task(id: day) { state = await Prequestions.run(["show", "--day", day]) }
    }

    private func act(_ args: [String]) {
        Task {
            busy = true; defer { busy = false }
            if let r = await Prequestions.run(args) {
                if r.ok {
                    state = args.first == "make" || args.first == "resolve" ? r : await Prequestions.run(["show", "--day", day])
                } else {   // show why (e.g. no agenda for that day) and keep what was there
                    state = Prequestions(ok: false, error: r.error ?? "Could not \(args.first ?? "run") prequestions",
                                         prequestions: state?.prequestions, note: nil)
                }
            } else {
                state = Prequestions(ok: false, error: "The lecture tool did not answer; try again, or run lecture doctor.",
                                     prequestions: state?.prequestions, note: nil)
            }
        }
    }
}
