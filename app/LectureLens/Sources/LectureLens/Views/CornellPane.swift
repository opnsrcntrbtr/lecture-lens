import SwiftUI

// MARK: contracts (`lecture cornell|agenda|teachback`, cornell.py, agenda.py, teachback.py)

struct CornellView: Decodable, Sendable {
    let ok: Bool; let error: String?; let sections: [Section]?; let summary: String?; let summary_prompt: String?; let built: Bool?
    struct Section: Decodable, Sendable, Identifiable { let section_id: String; let heading: String; let markdown: String; let cues: [Cue]; var id: String { section_id } }
    struct Cue: Decodable, Sendable, Identifiable, Hashable { let cue_id: String; let kind: String; let question: String; let times: [String]; var id: String { cue_id } }
}

struct AgendaMap: Decodable, Sendable {
    let ok: Bool; let error: String?; let parts: [Part]?; let unplanned: [String]?; let window_min: Int?
    struct Part: Decodable, Sendable, Identifiable {
        let agenda_item: String; let item_id: String; let status: String; let minutes: Int; let runs: [[String]]?; let uncertain: Bool?
        var id: String { item_id }
    }
}

struct TeachBack: Decodable, Sendable {
    let ok: Bool; let error: String?; let id: String?; let topic: String?; let statements: [Statement]?; let scored: Bool?
    let caught: Int?; let missed: Int?; let false_alarms: Int?
    struct Statement: Decodable, Sendable, Identifiable {
        let s_id: String; let text: String; let planted: Bool?; let marked: Bool?; let correct: String?; let why: String?; let t0: String?
        var id: String { s_id }
    }
}

enum Cornell {
    static func run<T: Decodable & Sendable>(_ t: T.Type, _ args: [String], timeout: TimeInterval = 300) async -> T? {
        try? await ToolRunner.json(T.self, "lecture", args, timeout: timeout)
    }
    static func agendaLine(_ p: AgendaMap.Part) -> String {
        let span = (p.runs ?? []).map { "\($0[0].prefix(5))–\($0[1].prefix(5))" }.joined(separator: ", ")
        return "\(p.status)\(p.uncertain == true ? " (uncertain)" : "") · \(p.agenda_item)\(span.isEmpty ? "" : " · \(span)")"
    }
    static func score(_ t: TeachBack) -> String {
        "Found \(t.caught ?? 0) of \((t.caught ?? 0) + (t.missed ?? 0)) planted errors · \(t.false_alarms ?? 0) correct statement(s) marked wrong"
    }
}

/// LS-5 Cornell view (cue column, notes, your summary), the NG-4 agenda map, and LS-6 teach-back.
struct CornellPane: View {
    let lecture: String
    @State private var v: CornellView?
    @State private var map: AgendaMap?
    @State private var summary = ""
    @State private var busy = ""
    @State private var showCues = true

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                agendaBox
                HStack {
                    Toggle("Show cue questions", isOn: $showCues).toggleStyle(.switch).font(.scaled(.caption))
                    Spacer()
                    Button(busy == "cues" ? "Writing cues…" : (v?.built == true ? "Rewrite cues" : "Write cue questions")) { build() }
                        .disabled(!busy.isEmpty)
                }
                ForEach(v?.sections ?? []) { s in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(s.cues) { c in
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(c.question).font(.scaled(.callout, weight: c.kind == "cue" ? .medium : .regular))
                                    Text("\(c.kind == "cue" ? "recall" : c.kind)\(c.times.first.map { " · \($0.prefix(5))" } ?? "")")
                                        .font(.scaled(.caption2)).foregroundStyle(.tertiary)
                                }
                            }
                        }
                        .frame(width: 260, alignment: .leading).opacity(showCues ? 1 : 0)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(s.heading).font(.scaled(.headline))
                            MarkdownText(s.markdown).textSelection(.enabled)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Divider()
                }
                GroupBox("Your summary") {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(v?.summary_prompt ?? "").font(.scaled(.caption)).foregroundStyle(.secondary)
                        TextEditor(text: $summary).frame(minHeight: 90).overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
                        Button("Save summary") {
                            Task { struct R: Decodable, Sendable { let ok: Bool }; _ = await Cornell.run(R.self, ["cornell", "summary", lecture, "--text", summary]) }
                        }.disabled(summary.trimmingCharacters(in: .whitespaces).isEmpty)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                TeachBackBox(lecture: lecture)
            }.padding(16)
        }
        .task(id: lecture) { await load() }
    }

    private var agendaBox: some View {
        GroupBox("How the class ran against the agenda") {
            VStack(alignment: .leading, spacing: 4) {
                if let m = map, m.ok {
                    ForEach(m.parts ?? []) { p in
                        Text(Cornell.agendaLine(p)).font(.scaled(.caption))
                            .foregroundStyle(p.status == "skipped" ? Color.orange : p.status == "moved" ? Color.blue : Color.primary)
                    }
                    if let u = m.unplanned, !u.isEmpty { Text("Not on the agenda: \(u.count * (m.window_min ?? 5)) min").font(.scaled(.caption2)).foregroundStyle(.secondary) }
                } else if let e = map?.error {
                    Text(e).font(.scaled(.caption)).foregroundStyle(.secondary)
                }
                Button(busy == "agenda" ? "Mapping…" : "Map the agenda") {
                    Task {
                        busy = "agenda"; defer { busy = "" }
                        map = await Cornell.run(AgendaMap.self, ["agenda", lecture, "--json"], timeout: 600)
                        await load()
                    }
                }.disabled(!busy.isEmpty).font(.scaled(.caption))
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func load() async {
        v = await Cornell.run(CornellView.self, ["cornell", "show", lecture])
        summary = v?.summary ?? ""
        if map == nil { map = await Cornell.run(AgendaMap.self, ["agenda", lecture, "--json", "--cached"], timeout: 30) }
    }

    private func build() {
        Task {
            busy = "cues"; defer { busy = "" }
            struct R: Decodable, Sendable { let ok: Bool }
            _ = await Cornell.run(R.self, ["cornell", "build", lecture], timeout: 600)
            await load()
        }
    }
}

struct TeachBackBox: View {
    let lecture: String
    @State private var t: TeachBack?
    @State private var marks: Set<String> = []
    @State private var busy = false

    var body: some View {
        GroupBox("Teach-back: find the planted errors") {
            VStack(alignment: .leading, spacing: 6) {
                Text("A colleague explains part of this lecture. Two or three statements are wrong; mark the ones you think are. Each was checked against the lecture before you see it.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                if let e = t?.error { Text(e).font(.scaled(.caption)).foregroundStyle(.orange) }
                ForEach(t?.statements ?? []) { s in
                    VStack(alignment: .leading, spacing: 1) {
                        Toggle(isOn: Binding(get: { marks.contains(s.s_id) || s.marked == true },
                                             set: { if $0 { marks.insert(s.s_id) } else { marks.remove(s.s_id) } })) {
                            Text(s.text).font(.scaled(.callout))
                        }.disabled(t?.scored == true)
                        if t?.scored == true, s.planted == true {
                            Text("Planted: \(s.correct ?? "")\(s.t0.map { " [\($0.prefix(5))]" } ?? "")").font(.scaled(.caption)).foregroundStyle(.orange)
                        }
                    }
                }
                HStack {
                    Button(busy ? "Writing…" : "New teach-back") {
                        Task { busy = true; defer { busy = false }; marks = []
                            t = await Cornell.run(TeachBack.self, ["teachback", "start", "--lecture", lecture], timeout: 600) }
                    }.disabled(busy)
                    if let x = t, x.ok, x.scored != true, let id = x.id {
                        Button("Check my marks") {
                            Task { t = await Cornell.run(TeachBack.self, ["teachback", "score", "--id", id, "--marks", marks.sorted().joined(separator: ",")]) }
                        }
                    }
                }
                if let x = t, x.scored == true { Text(Cornell.score(x)).font(.scaled(.callout, weight: .medium)) }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
