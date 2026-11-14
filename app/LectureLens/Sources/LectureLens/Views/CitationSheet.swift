import AppKit
import SwiftUI

/// `lecture cite --json`: the public sources an answer names and the lecture moments near its
/// time that share its words, with the slide on screen then (cite.py).
struct Citation: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let t: String?
    let sources: [Source]?
    let moments: [Moment]?
    let slide: Slide?
    struct Source: Decodable, Sendable, Identifiable { let id: String; let title: String; let fact: String?; let url: String?; let date: String? }
    struct Moment: Decodable, Sendable, Identifiable { let t: String; let text: String; let shared: [String]; var id: String { t + text } }
    struct Slide: Decodable, Sendable { let t: String; let title: String }

    /// Arguments for `lecture cite`, as an argv array (never a shell string).
    static func args(t: String, text: String, sources: [String], day: String?) -> [String] {
        var a = ["cite", "--json", "--t", t, "--text", text]
        if !sources.isEmpty { a += ["--sources", sources.joined(separator: ",")] }
        if let d = day, !d.isEmpty { a += ["--day", d] }
        return a
    }
}

/// The call to action on a question or answer: opens its citations.
struct CitationButton: View {
    let title: String
    let t: String
    let text: String
    var sources: [String] = []
    var day: String? = nil
    @State private var open = false

    var body: some View {
        Button { open = true } label: {
            Label(sources.isEmpty ? "Citations" : "Citations (\(sources.count))", systemImage: "quote.bubble")
        }
        .buttonStyle(.borderless).controlSize(.small).font(.scaled(.caption))
        .help("Sources this answer names and the lecture moments that support it")
        .sheet(isPresented: $open) { CitationSheet(title: title, t: t, text: text, sources: sources, day: day) }
    }
}

struct CitationSheet: View {
    let title: String
    let t: String
    let text: String
    let sources: [String]
    let day: String?
    @Environment(\.dismiss) private var dismiss
    @State private var result: Citation?
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                Text(title).font(.scaled(.headline)).textSelection(.enabled)
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let e = error {
                        Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    } else if let r = result {
                        CitationBody(result: r)
                    } else {
                        HStack { ProgressView().controlSize(.small); Text("Finding sources and lecture moments…").foregroundStyle(.secondary) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(18)
        .frame(minWidth: 560, idealWidth: 640, minHeight: 360, idealHeight: 480)
        .task {
            do {
                let r = try await ToolRunner.json(Citation.self, "lecture", Citation.args(t: t, text: text, sources: sources, day: day), timeout: 60)
                if r.ok { result = r } else { error = r.error ?? "No citations" }
            } catch { self.error = "\(error)" }
        }
    }
}

/// Sources first, then the lecture: slide on screen and the matching transcript lines.
struct CitationBody: View {
    let result: Citation

    var body: some View {
        let srcs = result.sources ?? [], ms = result.moments ?? []
        VStack(alignment: .leading, spacing: 14) {
            GroupBox("Public sources") {
                VStack(alignment: .leading, spacing: 10) {
                    if srcs.isEmpty { Text("This answer names no source from the evidence bank: it rests on the lecture alone.").foregroundStyle(.secondary) }
                    ForEach(srcs) { s in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(s.id).font(.scaled(.caption, design: .monospaced)).foregroundStyle(.secondary)
                                Text(s.title).font(.scaled(.callout, weight: .semibold)).textSelection(.enabled)
                                Spacer()
                                if let u = s.url, let url = URL(string: u), url.scheme == "https" {
                                    Button("Open", systemImage: "arrow.up.right.square") { NSWorkspace.shared.open(url) }
                                        .buttonStyle(.borderless).controlSize(.small)
                                }
                            }
                            if let f = s.fact { Text(f).font(.scaled(.caption)).foregroundStyle(.secondary).textSelection(.enabled) }
                            if let d = s.date { Text(d).font(.scaled(.caption2)).foregroundStyle(.tertiary) }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            GroupBox("In the lecture") {
                VStack(alignment: .leading, spacing: 8) {
                    if let sl = result.slide {
                        Label("\(LiveData.clock(sl.t))  \(sl.title)", systemImage: "rectangle.on.rectangle")
                            .font(.scaled(.callout)).textSelection(.enabled)
                    }
                    if ms.isEmpty { Text("No transcript line near this time shares enough words with the answer.").foregroundStyle(.secondary) }
                    ForEach(ms) { m in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(LiveData.clock(m.t) + "  " + m.text).font(.scaled(.callout)).textSelection(.enabled)
                            Text("shares: " + m.shared.joined(separator: ", ")).font(.scaled(.caption2)).foregroundStyle(.tertiary)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Text("Matched by shared words within 10 minutes of the question; check the lecture before relying on a draft.")
                .font(.scaled(.caption2)).foregroundStyle(.tertiary)
        }
    }
}

/// Plain text helpers, outside any view (views are main-actor isolated).
enum CitationText {
    /// The answer's sentences that cite source n: "[n]", "[n, m]" or "[m, n]".
    static func sentences(_ answer: String, citing n: Int) -> [String] {
        let parts = answer.replacingOccurrences(of: "\n", with: " ")
            .split(whereSeparator: { ".!?".contains($0) }).map { $0.trimmingCharacters(in: .whitespaces) }
        return parts.filter { p in
            p.ranges(of: /\[[\d,\s]+\]/).contains { r in
                p[r].dropFirst().dropLast().split(separator: ",").contains { Int($0.trimmingCharacters(in: .whitespaces)) == n }
            }
        }
    }
}

/// Ask screen: each numbered source with the answer sentences that cite it, and the lecture
/// moments around that source's time.
struct AskCitationSheet: View {
    let question: String
    let answer: String
    let sources: [AskResult.Source]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                Text(question).font(.scaled(.headline)).textSelection(.enabled)
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(sources) { s in
                        GroupBox {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("[\(s.n)] \(s.src) · \(s.where)").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
                                let cited = CitationText.sentences(answer, citing: s.n)
                                if cited.isEmpty { Text("Not cited in the answer text.").font(.scaled(.caption)).foregroundStyle(.tertiary) }
                                ForEach(cited, id: \.self) { c in Text("“\(c)”").font(.scaled(.callout)).italic().textSelection(.enabled) }
                                Text(s.text).font(.scaled(.caption)).foregroundStyle(.secondary).lineLimit(6).textSelection(.enabled)
                                DisclosureGroup("Lecture moments near this source") {
                                    AskSourceMoments(source: s).padding(.top, 4)
                                }
                                .font(.scaled(.caption))
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }
        }
        .padding(18)
        .frame(minWidth: 560, idealWidth: 680, minHeight: 360, idealHeight: 520)
    }
}

private struct AskSourceMoments: View {
    let source: AskResult.Source
    @State private var result: Citation?
    @State private var failed = false

    var body: some View {
        Group {
            if let r = result { CitationBody(result: r) }
            else if failed { Text("No lecture moments found for this source.").foregroundStyle(.secondary) }
            else { ProgressView().controlSize(.small) }
        }
        .task {
            let r = try? await ToolRunner.json(Citation.self, "lecture", Citation.args(t: source.ts, text: source.text, sources: [], day: nil), timeout: 60)
            if let r, r.ok { result = r } else { failed = true }
        }
    }
}
