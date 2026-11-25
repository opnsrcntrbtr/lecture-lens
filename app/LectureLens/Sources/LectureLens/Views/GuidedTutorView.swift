import SwiftUI

// Guided tutor v2 (`lecture tutor-session …`, tutor_session.py). The answer is committed in the toolkit and
// never sent to the app until a reveal is allowed (two tries, or "Just tell me", which is logged). Hints climb
// a ladder that the toolkit remembers per question, so a new chat carries on. Every episode ends with one
// closed-book question from the same lecture: the "next item without help" check.

struct GuidedSeed: Identifiable, Equatable {
    let id = UUID()
    var question: String = ""
    var lecture: String? = nil
    var itemRef: String? = nil
}

struct GuidedSession: Decodable, Sendable {
    struct Hint: Decodable, Sendable, Hashable { let rung: Int; let kind: String; let text: String; let how: String? }
    struct Attempt: Decodable, Sendable { let text: String; let pct: Int?; let verdict: String?; let correct: Bool }
    struct Cite: Decodable, Sendable, Hashable { let lecture_id: String?; let source: String?; let snippet: String? }
    struct Exit: Decodable, Sendable {
        let question: String
        let choices: [String]
        let answer: Int?
        let correct: Bool?
        let correct_index: Int?
    }
    let ok: Bool
    let id: String?
    let question: String?
    let lecture_id: String?
    let rung: Int?
    let max_rung: Int?
    let next_hint: String?
    let attempts: Int?
    let solved: Bool?
    let can_reveal: Bool?
    let revealed: String?
    let hints: [Hint]?
    let last: Attempt?
    let note: String?
    let brief: String?
    let feedback: String?
    let answer: String?
    let explanation: String?
    let cites: [Cite]?
    let exit: Exit?
    let error: String?
}

enum GuidedText {
    static func status(_ s: GuidedSession) -> String {
        if s.revealed == "asked" { return "You asked for the answer. One closed-book question next, to see what stuck." }
        if s.solved == true { return "Solved. One closed-book question next, without help." }
        if s.revealed != nil { return "Here's the answer. One closed-book question next, without help." }
        let left = max(0, 2 - (s.attempts ?? 0))
        return left > 0 ? "Try it in your own words first: the answer opens after \(left) more \(left == 1 ? "try" : "tries")."
                        : "You can open the answer now, or take a hint and try once more."
    }

    static func hintButton(_ s: GuidedSession) -> String {
        s.next_hint.map { "Hint: \($0)" } ?? "No more hints"
    }

    static func args(_ action: String, id: String, extra: [String] = []) -> [String] {
        ["tutor-session", action, "--id", id] + extra + ["--json"]
    }

    static func startArgs(_ seed: GuidedSeed) -> [String] {
        if let ref = seed.itemRef { return ["tutor-session", "start", "--item", ref, "--json"] }
        var a = ["tutor-session", "start"]
        if let l = seed.lecture, !l.isEmpty { a += ["--lecture", l] }
        return a + ["--json", "--question=\(seed.question)"]   // one token: a question starting with "-" stays a question
    }
}

struct GuidedTutorPanel: View {
    let seed: GuidedSeed
    var onLecture: ((String) -> Void)? = nil
    @State private var s: GuidedSession?
    @State private var draft = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "graduationcap").foregroundStyle(Phosphor.accent).accessibilityHidden(true)
                Text(s?.question ?? seed.question).font(.scaled(.body, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                Spacer()
                if busy { ProgressView().controlSize(.small) }
            }
            if let e = error ?? (s?.ok == false ? s?.error : nil) {
                Label(e, systemImage: "exclamationmark.triangle").font(.scaled(.callout)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let s, s.ok || s.id != nil {
                if let n = s.note { Text(n).font(.scaled(.caption)).foregroundStyle(.secondary) }
                if let b = s.brief, !b.isEmpty {
                    DisclosureGroup("What the tutor knows about this lecture") {
                        Text(b).font(.scaled(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 2)
                    }
                    .font(.scaled(.caption))
                    .help("Worked out from your mastery checks, practice and earlier tutor sessions, not by the model")
                }
                Text(GuidedText.status(s)).font(.scaled(.callout)).foregroundStyle(.secondary)
                ForEach(s.hints ?? [], id: \.self) { h in
                    Label { Text(h.text).font(.scaled(.callout)).fixedSize(horizontal: false, vertical: true)
                    } icon: { Text("\(h.rung)").font(.scaled(.caption, weight: .bold)).foregroundStyle(Phosphor.accent) }
                        .accessibilityLabel("Hint \(h.rung), \(h.kind): \(h.text)")
                }
                if let last = s.last {
                    Text("Your last try: \(last.text)").font(.scaled(.caption)).foregroundStyle(.secondary).lineLimit(3)
                }
                if let f = s.feedback { Text(f).font(.scaled(.callout, weight: .medium)) }
                if s.revealed == nil { tryArea(s) } else { answerArea(s) }
            }
        }
        .padding(14)
        .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Phosphor.accent.opacity(0.35)))
        .task { if s == nil { await run(GuidedText.startArgs(seed)) } }
    }

    @ViewBuilder
    private func tryArea(_ s: GuidedSession) -> some View {
        TextField("Your answer, in your own words", text: $draft, axis: .vertical)
            .textFieldStyle(.roundedBorder).lineLimit(2...6)
            .accessibilityLabel("Your answer")
        HStack(spacing: 8) {
            Button("Check my answer", systemImage: "checkmark.circle") {
                let t = draft; draft = ""
                Task { await run(GuidedText.args("attempt", id: s.id ?? "", extra: ["--text", t])) }
            }
            .buttonStyle(.borderedProminent).tint(Phosphor.fill)
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(busy || draft.trimmingCharacters(in: .whitespaces).isEmpty)
            Button(GuidedText.hintButton(s), systemImage: "lightbulb") {
                Task { await run(GuidedText.args("hint", id: s.id ?? "")) }
            }
            .disabled(busy || s.next_hint == nil)
            .keyboardShortcut("h", modifiers: .command)
            Spacer()
            Button("Show answer") { Task { await run(GuidedText.args("reveal", id: s.id ?? "")) } }
                .disabled(busy || s.can_reveal != true)
                .help(s.can_reveal == true ? "Open the answer and its explanation" : "Opens after two tries")
            Button("Just tell me") { Task { await run(GuidedText.args("reveal", id: s.id ?? "", extra: ["--just-tell-me"])) } }
                .buttonStyle(.borderless).foregroundStyle(.secondary)
                .disabled(busy || s.can_reveal == true)
                .help("Shows the answer now. It's noted, so the closed-book check afterwards tells you what stuck.")
        }
    }

    @ViewBuilder
    private func answerArea(_ s: GuidedSession) -> some View {
        if let a = s.answer {
            VStack(alignment: .leading, spacing: 4) {
                Text("Answer").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
                Text(a).font(.scaled(.callout)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                if let e = s.explanation, !e.isEmpty {
                    Text(e).font(.scaled(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                ForEach(s.cites ?? [], id: \.self) { c in
                    Button {
                        if let l = c.lecture_id { onLecture?(l) }
                    } label: {
                        Label("\(c.source ?? "lecture"): \((c.snippet ?? "").prefix(120))", systemImage: "text.quote")
                            .font(.scaled(.caption)).lineLimit(2)
                    }
                    .buttonStyle(.link)
                    .accessibilityLabel("Open the lecture at this source")
                }
            }
            .padding(10).background(.background, in: RoundedRectangle(cornerRadius: 8))
        }
        if let x = s.exit { exitArea(s, x) } else {
            Button("Closed-book question", systemImage: "rectangle.and.pencil.and.ellipsis") {
                Task { await run(GuidedText.args("exit", id: s.id ?? "")) }
            }
            .buttonStyle(.borderedProminent).tint(Phosphor.fill).disabled(busy)
            .help("One question from the same lecture, without help: did it stick?")
        }
    }

    @ViewBuilder
    private func exitArea(_ s: GuidedSession, _ x: GuidedSession.Exit) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Without help").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
            Text(x.question).font(.scaled(.callout, weight: .medium)).fixedSize(horizontal: false, vertical: true)
            ForEach(Array(x.choices.enumerated()), id: \.offset) { n, c in
                Button {
                    Task { await run(GuidedText.args("answer", id: s.id ?? "", extra: ["--choice", "\(n)"])) }
                } label: {
                    HStack(alignment: .top, spacing: 8) {
                        Text("\(n + 1)").font(.scaled(.caption).monospacedDigit()).foregroundStyle(.secondary)
                        Text(c).font(.scaled(.callout)).multilineTextAlignment(.leading)
                        Spacer()
                        if x.answer != nil, n == x.correct_index {
                            Image(systemName: "checkmark.circle.fill").foregroundStyle(Phosphor.accent).accessibilityLabel("Right answer")
                        } else if x.answer == n {
                            Image(systemName: "xmark.circle").foregroundStyle(.orange).accessibilityLabel("Your answer")
                        }
                    }
                    .padding(8).contentShape(Rectangle())
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.25)))
                }
                .buttonStyle(.plain)
                .disabled(busy || x.answer != nil)
                .keyboardShortcut(KeyEquivalent(Character("\(min(n + 1, 9))")), modifiers: [])
                .accessibilityLabel("Choice \(n + 1): \(c)")
            }
        }
    }

    private func run(_ args: [String]) async {
        busy = true; defer { busy = false }
        do {
            let r = try await ToolRunner.json(GuidedSession.self, "lecture", args, timeout: 180)
            if r.ok || s == nil { s = r; error = nil } else { error = r.error }
        } catch { self.error = ErrorText.short(error) }
    }
}
