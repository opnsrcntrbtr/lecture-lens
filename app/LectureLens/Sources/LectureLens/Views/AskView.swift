import SwiftUI

/// Ask your captures (brief J3): single-shot retrieval + answer via
/// `study ask --json`, sources shown underneath, ↑/↓ feedback on every answer.
/// No agent loop — one question, one retrieval, one answer (AI-first review).
struct AskView: View {
    struct Turn: Identifiable {
        let id = UUID()
        let question: String
        let window: String
        var result: AskResult? = nil
        var error: String? = nil
    }

    var model: AppModel
    @State private var question = ""
    @State private var window = "30d"
    @State private var think = false
    @State private var turns: [Turn] = []
    @State private var busy = false
    @FocusState private var focused: Bool

    private let windows = [("Last 3 hours", "3h"), ("Today", "24h"), ("7 days", "7d"), ("30 days", "30d"), ("90 days", "90d")]

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        if turns.isEmpty {
                            ContentUnavailableView {
                                Label("Ask about what you studied", systemImage: "questionmark.bubble")
                            } description: {
                                Text("Answers come only from this Mac's captured lectures and pages, with numbered sources. e.g. “What are the three levels of AI product maturity?”")
                            }
                            .padding(.top, 60)
                        }
                        ForEach(turns) { t in TurnView(turn: t).id(t.id) }
                    }
                    .frame(maxWidth: 820, alignment: .leading)
                    .padding(20)
                    .frame(maxWidth: .infinity)
                }
                .onChange(of: turns.count) { _, _ in
                    if let last = turns.last { withAnimation { proxy.scrollTo(last.id, anchor: .top) } }
                }
            }
            Divider()
            if model.judgeLoaded {
                Label("An eval has the judge model loaded — asking now would force a model swap. Ask waits until it finishes.",
                      systemImage: "hourglass").font(.caption).foregroundStyle(.orange).padding(.top, 8)
            }
            HStack(alignment: .bottom, spacing: 10) {
                TextField("Ask a question about your lectures…", text: $question, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(1...5)
                    .focused($focused)
                    .onSubmit(ask)
                Picker("", selection: $window) {
                    ForEach(windows, id: \.1) { Text($0.0).tag($0.1) }
                }
                .labelsHidden().frame(width: 120)
                .help("How far back to search")
                Toggle("Think", isOn: $think).toggleStyle(.checkbox)
                    .help("Let the model reason first — slower, sometimes better on multi-part questions")
                Button(action: ask) {
                    if busy { ProgressView().controlSize(.small) } else { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                }
                .buttonStyle(.borderless)
                .keyboardShortcut(.return, modifiers: [.command])
                .disabled(busy || model.judgeLoaded || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .padding(14)
        }
        .navigationTitle("Ask")
        .toolbar {
            if !turns.isEmpty { Button("Clear", systemImage: "trash") { turns = [] } }
        }
        .onAppear { focused = true }
    }

    private func ask() {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !busy, !model.judgeLoaded else { return }
        question = ""
        busy = true
        turns.append(Turn(question: q, window: window))
        let idx = turns.count - 1
        // `--` ends option parsing, so a question starting with "-" stays a question.
        var args = ["ask", "--json", "--since", window]
        if think { args.append("--think") }
        args += ["--", q]
        Task {
            defer { busy = false }
            do {
                let r = try await ToolRunner.json(AskResult.self, "study", args, timeout: 300)
                turns[idx].result = r
                if !r.ok { turns[idx].error = r.error ?? "The toolkit reported a failure." }
            } catch {
                turns[idx].error = error.localizedDescription
            }
        }
    }
}

private struct TurnView: View {
    let turn: AskView.Turn
    @State private var showSources = true

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                Image(systemName: "person.crop.circle").foregroundStyle(.secondary)
                Text(turn.question).font(.headline).textSelection(.enabled)
            }
            if let err = turn.error {
                Label(err, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
            } else if let r = turn.result, let answer = r.answer {
                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        MarkdownText(answer)
                        HStack(spacing: 10) {
                            if let m = r.model { Text(m).font(.caption2.monospaced()) }
                            if let l = r.latency_s { Text(String(format: "%.1fs", l)).font(.caption2) }
                            Text("last \(turn.window)").font(.caption2)
                        }.foregroundStyle(.tertiary)
                        FeedbackBar(target: "answer", ref: ["question": turn.question, "window": turn.window],
                                    input: turn.question, output: answer,
                                    context: (r.sources ?? []).map { "[\($0.n)] \($0.text)" },
                                    model: r.model, promptVersion: r.prompt_version)
                    }
                    .padding(6).frame(maxWidth: .infinity, alignment: .leading)
                }
                if let sources = r.sources, !sources.isEmpty {
                    DisclosureGroup("\(sources.count) sources", isExpanded: $showSources) {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(sources) { s in
                                HStack(alignment: .top, spacing: 8) {
                                    Text("[\(s.n)]").font(.caption.monospaced()).foregroundStyle(.secondary)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("\(s.src) · \(s.where) · \(Self.time(s.ts))")
                                            .font(.caption).foregroundStyle(.secondary)
                                        Text(s.text).font(.callout).lineLimit(4).textSelection(.enabled)
                                    }
                                }
                            }
                        }.padding(.top, 6)
                    }
                    .font(.callout)
                }
            } else {
                HStack { ProgressView().controlSize(.small); Text("Searching captures and answering…").foregroundStyle(.secondary) }
            }
        }
    }

    static func time(_ iso: String) -> String {
        guard let d = parseISO(iso) else { return iso }
        return d.formatted(.dateTime.day().month(.abbreviated).hour().minute())
    }
}
